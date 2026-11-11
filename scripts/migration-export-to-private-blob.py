#!/usr/bin/env python3
"""Read-only database snapshots directly to private Azure Blob SAS URLs.

Runs only in a pinned GitHub Actions source DEV job. Secrets and user data never
reach stdout, GitHub artifacts, or the public repository.
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import urllib.parse

AZURE_SOURCE_APP_NAMES = (
    "ca-coderats-base-api-dev",
    "ca-coderats-grader-dev",
)
TARGET_ACCOUNT = "stcoderatsmigc26m0911"
TARGET_CONTAINER = "coderats-migration-staging"
PG_CLIENT_IMAGE = "postgres:16-alpine"

def az_private(*parts):
    result = subprocess.run(["az",*parts,"--only-show-errors","-o","json"],
                            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                            text=True,timeout=50)
    if result.returncode: raise RuntimeError("Azure origin read unavailable")
    return json.loads(result.stdout)

def load_connection(app):
    sid=os.environ["SOURCE_SID"]
    rg="rg-coderats-base-dev"
    d=az_private("containerapp","show","--subscription",sid,"-g",rg,"-n",app)
    s=az_private("containerapp","secret","list","--show-values","--subscription",sid,"-g",rg,"-n",app)
    secrets={v.get("name"):v.get("value") for v in s if isinstance(v,dict)}
    c=((d.get("properties") or {}).get("template") or {}).get("containers") or []
    if len(c)!=1: raise RuntimeError("Unexpected origin container layout")
    env={v.get("name"):v for v in (c[0].get("env") or []) if isinstance(v,dict)}
    def value(k):
        obj=env.get(k) or {}
        val=secrets.get(obj["secretRef"]) if obj.get("secretRef") else obj.get("value")
        return val if isinstance(val,str) and val else None
    url=value("DATABASE_URL")
    if not url: raise RuntimeError("Source DB URL missing")
    if url.startswith("jdbc:"):url=url[5:]
    url=url.replace("postgresql+asyncpg://","postgresql://",1)
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ("postgresql","postgres") or not p.hostname:raise RuntimeError("Source DB URL invalid")
    db=urllib.parse.unquote(p.path.lstrip("/"))
    user=value("DATABASE_USERNAME") or urllib.parse.unquote(p.username or "")
    password=value("DATABASE_PASSWORD") or urllib.parse.unquote(p.password or "")
    if not db or not user or not password:raise RuntimeError("Source DB login unavailable")
    return dict(host=p.hostname,port=p.port or 5432,db=db,user=user,password=password)

def verify_sas(raw,stage,runid):
    url=urllib.parse.urlsplit(raw)
    expected= f"/{TARGET_CONTAINER}/coderats-{stage}-{runid}.dump"
    if (url.scheme!="https" or url.hostname!=TARGET_ACCOUNT+".blob.core.windows.net"
        or url.path != expected or not url.query):
        raise RuntimeError("Untrusted backup upload destination")
    q=urllib.parse.parse_qs(url.query)
    if "sig" not in q or not set(q.get("sp",[""])[0]).issubset(set("cw")):
        raise RuntimeError("Unsafe SAS scope")
    if not set("cw").issubset(set(q.get("sp",[""])[0])):
        raise RuntimeError("SAS missing create/write permission")
    return raw

def dump_to_file(cfg,filename,tmpdir):
    environ=os.environ.copy()
    environ["PGPASSWORD"]=cfg["password"]
    args=[
        "docker","run","--rm","--network","host",
        "-e","PGPASSWORD","-e","PGSSLMODE=require",
        "-v",str(tmpdir)+":/backup",PG_CLIENT_IMAGE,
        "pg_dump","--format=custom","--no-owner","--no-acl",
        "--host",cfg["host"],"--port",str(cfg["port"]),
        "--username",cfg["user"],"--dbname",cfg["db"],
        "--file","/backup/"+filename,
    ]
    run=subprocess.run(args,env=environ,stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,timeout=5400)
    if run.returncode:raise RuntimeError("Source pg_dump failed (details suppressed)")
    result=tmpdir/filename
    if not result.is_file() or result.stat().st_size<100:
        raise RuntimeError("Backup missing or too small")
    if result.stat().st_size>4_000_000_000:
        raise RuntimeError("Backup larger than single-blob safety limit")
    listed=subprocess.run(
       ["docker","run","--rm","-v",str(tmpdir)+":/backup:ro",
        PG_CLIENT_IMAGE,"pg_restore","--list","/backup/"+filename],
       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=300)
    if listed.returncode:raise RuntimeError("Backup archive validation failed")
    return result

def upload_file(file,url):
    result=subprocess.run(["curl","--silent","--fail","--show-error",
                           "--retry","2","--max-time","5400",
                           "--request","PUT","-H","x-ms-blob-type: BlockBlob",
                           "--upload-file",str(file),url],
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          timeout=5500)
    if result.returncode:raise RuntimeError("Private Blob upload failed")

def sha256_file(file):
    h=hashlib.sha256()
    with open(file,"rb") as f:
        for block in iter(lambda:f.read(4*1024*1024),b""): h.update(block)
    return h.hexdigest()

def main():
    if os.environ.get("SOURCE_SID")=="da3650a9-e291-43f1-ae4b-9dcc8ff84239":
        raise RuntimeError("Origin identity scope mismatch")
    if os.environ.get("MIGRATION_STAGE")!="SNAPSHOT_ONLY":
        raise RuntimeError("Unexpected migration stage")
    runid=os.environ["GITHUB_RUN_ID"]
    manifest=json.load(open(os.environ["MIGRATION_PRIVATE_MANIFEST"]))
    if not isinstance(manifest,dict) or sorted(manifest)!=["api","grader"]:
        raise RuntimeError("Unexpected target upload manifest")
    urls={stage:verify_sas(manifest[stage],stage,runid) for stage in ("api","grader")}
    root=pathlib.Path(os.environ["RUNNER_TEMP"])/"private-migration-dumps"
    root.mkdir(mode=0o700,exist_ok=False)
    records={}
    for stage,name in (("api",AZURE_SOURCE_APP_NAMES[0]),("grader",AZURE_SOURCE_APP_NAMES[1])):
        cfg=load_connection(name)
        target_file=dump_to_file(cfg,"coderats-"+stage+".dump",root)
        upload_file(target_file,urls[stage])
        records[stage]=(sha256_file(target_file),target_file.stat().st_size)
        print("MIGRATION_SOURCE_"+stage.upper()+"_PRIVATE_SNAPSHOT_UPLOADED=True")
    with open(os.environ["GITHUB_OUTPUT"],"a") as output:
        for stage,(digest,size) in records.items():
            output.write(stage+"_sha256="+digest+"\n")
            output.write(stage+"_bytes="+str(size)+"\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"],"a") as summary:
        summary.write("### Source consistent-per-database PostgreSQL snapshots\n")
        summary.write("- Both databases: snapshot uploaded to destination private Blob container\n")
        summary.write("- No imports, row updates, source network changes, public artifacts or secret values\n")
        summary.write("- Online snapshots are for mirror validation, NOT final cutover consistency\n")

if __name__=="__main__":
    try:
        main()
    except Exception:
        print("::error::Source private snapshot stage failed; data and diagnostic details suppressed.")
        sys.exit(1)
