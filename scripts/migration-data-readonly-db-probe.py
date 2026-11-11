#!/usr/bin/env python3
"""Ephemeral read-only database connectivity check; never logs credentials or data."""
import json
import os
import subprocess
import sys
import urllib.parse

def quiet_az(*args):
    run = subprocess.run(
        ["az", *args, "--only-show-errors", "-o", "json"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=35,
        check=False
    )
    if run.returncode != 0:
        raise RuntimeError("metadata-unavailable")
    return json.loads(run.stdout)

def app_credentials(name):
    sid, rg = os.environ["AZURE_SUBSCRIPTION_ID"], os.environ["MIGRATION_RESOURCE_GROUP"]
    config=quiet_az("containerapp", "show", "--subscription",sid,"-g",rg,"-n",name)
    allsecrets=quiet_az("containerapp", "secret", "list","--show-values","--subscription",sid,"-g",rg,"-n",name)
    secrets = {v.get("name"):v.get("value") for v in allsecrets if isinstance(v,dict)}
    containers = ((config.get("properties") or {}).get("template") or {}).get("containers") or []
    if len(containers)!=1:
        raise RuntimeError("unexpected-container-count")
    env = {e.get("name"):e for e in (containers[0].get("env") or []) if isinstance(e,dict)}
    def value(key):
        obj=env.get(key) or {}
        ref=obj.get("secretRef")
        answer=secrets.get(ref) if ref else obj.get("value")
        return answer if isinstance(answer,str) and answer.strip() else None
    raw=value("DATABASE_URL")
    if raw is None:
        raise RuntimeError("database-url-not-configured")
    if raw.startswith("jdbc:"):
        raw=raw[5:]
    raw=raw.replace("postgresql+asyncpg://","postgresql://",1)
    url=urllib.parse.urlsplit(raw)
    if url.scheme not in ("postgresql","postgres") or not url.hostname:
        raise RuntimeError("unexpected-connection-url")
    dbname=urllib.parse.unquote(url.path.lstrip("/"))
    username=value("DATABASE_USERNAME") or urllib.parse.unquote(url.username or "")
    password=value("DATABASE_PASSWORD") or urllib.parse.unquote(url.password or "")
    if not dbname or not username or not password:
        raise RuntimeError("database-credentials-not-configured")
    return dict(host=url.hostname,port=url.port or 5432,dbname=dbname,user=username,password=password)

def query(creds, sql):
    # psql never prints failure output; SQL is read-only, session is read-only.
    params=("host="+creds["host"]+" port="+str(creds["port"])+
            " dbname="+creds["dbname"]+" user="+creds["user"]+
            " sslmode=require connect_timeout=10")
    env=os.environ.copy()
    env["PGPASSWORD"]=creds["password"]
    env["PGOPTIONS"]="-c default_transaction_read_only=on -c statement_timeout=6000"
    res=subprocess.run(["psql",params,"-X","-qAt","-v","ON_ERROR_STOP=1","-c",sql],
            env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            text=True,timeout=25,check=False)
    if res.returncode:
        raise RuntimeError("db-connection-or-query-failed")
    return res.stdout.strip()

def main():
    role=os.environ.get("MIGRATION_ROLE")
    if role not in ("source","target"):
        raise RuntimeError("invalid-role")
    names = [os.environ["MIGRATION_API"],os.environ["MIGRATION_GRADER"]]
    results=[]
    for kind,name in zip(("api","grader"),names):
        credentials=app_credentials(name)
        raw=query(credentials,"SELECT (current_setting('server_version_num')::integer / 10000)::text || '|' || (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE')::text")
        fields=raw.split("|")
        if len(fields)!=2 or not fields[0].isdigit() or not fields[1].isdigit():
            raise RuntimeError("unrecognized-db-response")
        major=int(fields[0])
        table_count=int(fields[1])
        if kind=="api":
            exists=query(credentials,"SELECT to_regclass('public.members') IS NOT NULL")
            if exists.lower() != 't':
                raise RuntimeError("member-table-missing")
            has_members=query(credentials,"SELECT EXISTS(SELECT 1 FROM public.members)")
            if has_members not in ("t","f"):
                raise RuntimeError("unrecognized-member-check")
            print("MIGRATION_PROBE_"+role.upper()+"_MEMBER_ROWS_PRESENT="+str(has_members=="t"))
        else:
            print("MIGRATION_PROBE_"+role.upper()+"_GRADER_TABLES_PRESENT="+str(table_count>0))
        expected=15 if role=="source" else 16
        print("MIGRATION_PROBE_"+role.upper()+"_"+kind.upper()+"_DB_READ_ONLY_CONNECTED=True")
        print("MIGRATION_PROBE_"+role.upper()+"_"+kind.upper()+"_PG_MAJOR_EXPECTED="+str(major==expected))
        print("MIGRATION_PROBE_"+role.upper()+"_"+kind.upper()+"_PG_MAJOR="+str(major))
        print("MIGRATION_PROBE_"+role.upper()+"_"+kind.upper()+"_TABLES_PRESENT="+str(table_count>0))
        results.append((kind,major,table_count))
    with open(os.environ["GITHUB_STEP_SUMMARY"],"a") as out:
        out.write("### "+("Source" if role=="source" else "Destination")+" PostgreSQL read-only connectivity\n")
        for kind,major,count in results:
            out.write("- "+kind+": authenticated read-only SQL successful; major version expected="+str(major==(15 if role=="source" else 16))+"; schema present="+str(count>0)+"\n")
        out.write("- No row data, credentials or DB errors displayed or uploaded\n")

if __name__=="__main__":
    try:
        main()
    except Exception:
        print("::error::Database connectivity probe blocked. Cause class: credentials/network/query; diagnostic details suppressed.")
        sys.exit(1)
