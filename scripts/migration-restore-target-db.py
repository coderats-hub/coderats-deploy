#!/usr/bin/env python3
"""Guarded target-only restore of verified snapshots with a reversible DB rename.

Creates shadow databases first, keeps old DBs under unique names, and never
changes source DEV, Azure resource configuration, application secrets or DNS.
All data remains in private ephemeral runner files and private target Blob.
"""
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

SID = "da3650a9-e291-43f1-ae4b-9dcc8ff84239"
TID = "1cf74c7e-fcd9-46f7-8a9d-e699d3529003"
SERVER = "psql-coderatsmig-c26m0911.postgres.database.azure.com"
STORAGE = "stcoderatsmigc26m0911"
CONTAINER = "coderats-migration-staging"
SOURCE_RUN = "42932502989"
TARGET_BACKUP_RUN = "42941945757"
ARCHIVES = {
    "api": ("coderats_base", "ca-cdmstd-api-c26m0911",
            "412c53a4f09e6c3d177b226592d78ca43718af97a17553fc90abf6271086df14", 2852594),
    "grader": ("coderats_grader", "ca-cdmstd-grader-c26m0911",
               "e939f7a45144546e69bf33a4bd15a66745accff55171c02be308876e2be1be58", 42143630)
}


def quiet(cmd, env=None, timeout=120, output=False):
    run = subprocess.run(cmd, env=env, check=False, timeout=timeout,
                         stdout=subprocess.PIPE if output else subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    if run.returncode:
        raise RuntimeError("Database migration operation failed (details suppressed)")
    return run.stdout.decode("utf-8", errors="strict").strip() if output else ""


def sha256(file):
    h = hashlib.sha256()
    with file.open("rb") as f:
        for chunk in iter(lambda: f.read(4194304), b""):
            h.update(chunk)
    return h.hexdigest()


def db_cmd(creds, db, args, *, output=False, timeout=150):
    if creds["host"] != SERVER:
        raise RuntimeError("Target DB host mismatch")
    if creds["user"] != "coderats_admin":
        raise RuntimeError("Target DB administrator identity mismatch")
    environ = os.environ.copy()
    environ["PGPASSWORD"] = creds["password"]
    argv = ["docker", "run", "--rm", "--network", "host",
            "-e", "PGPASSWORD", "-e", "PGSSLMODE=require",
            "postgres:16-alpine", *args,
            "--host", creds["host"], "--port", str(creds["port"]),
            "--username", creds["user"], "--dbname", db]
    return quiet(argv, environ, timeout=timeout, output=output)


def sql(creds, db, statement, *, output=False):
    return db_cmd(creds, db, ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
                               "-c", statement], output=output)


def download_blob(storage, name, target):
    quiet(["az", "storage", "blob", "download", "--account-name", STORAGE,
           "--container-name", CONTAINER, "--name", name,
           "--file", str(target), "--overwrite", "true", "--only-show-errors", "-o", "none"],
          env=storage, timeout=1800)
    if not target.exists() or target.stat().st_size < 100:
        raise RuntimeError("Required private archive missing")
    quiet(["docker", "run", "--rm", "-v", str(target.parent) + ":/backup:ro",
           "postgres:16-alpine", "pg_restore", "--list", "/backup/" + target.name],
          timeout=300)


def dbnames(run_id, live):
    if not run_id.isascii() or not run_id.isdigit() or not (6 <= len(run_id) <= 20):
        raise RuntimeError("Invalid migration run identifier")
    shadow = live + "_import_" + run_id
    prior = live + "_prior_" + run_id
    if len(shadow) >= 63 or len(prior) >= 63:
        raise RuntimeError("PostgreSQL identifier too long")
    return shadow, prior


def database_exists(creds, name):
    result = sql(creds, "postgres",
                 "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = '" + name + "')",
                 output=True)
    if result not in ("t", "f"):
        raise RuntimeError("Unrecognized DB existence response")
    return result == "t"


def verify_import(creds, kind, dbname):
    tables = sql(creds, dbname,
                 "SELECT count(*) FROM information_schema.tables "
                 "WHERE table_schema='public' AND table_type='BASE TABLE'",
                 output=True)
    if not tables.isdigit() or int(tables) < 1:
        raise RuntimeError("Imported schema has no public tables")
    if kind == "api":
        present = sql(creds, dbname, "SELECT to_regclass('public.members') IS NOT NULL",
                      output=True)
        rows = sql(creds, dbname, "SELECT EXISTS(SELECT 1 FROM public.members)",
                   output=True) if present == "t" else "f"
        if present != "t" or rows != "t":
            raise RuntimeError("Source API member rows are missing in imported snapshot")
    return int(tables)


def backup_exists_and_archive(storage, root, kind):
    name = "target-before-restore-" + kind + "-" + TARGET_BACKUP_RUN + ".dump"
    file = root / ("prior-" + kind + ".dump")
    download_blob(storage, name, file)
    return file.stat().st_size


def import_shadow(creds, name, shadow, filename, root):
    if database_exists(creds, shadow):
        raise RuntimeError("Shadow name already exists; no existing target database can be overwritten")
    sql(creds, "postgres", 'CREATE DATABASE "' + shadow + '" WITH TEMPLATE template0')
    environ = os.environ.copy()
    environ["PGPASSWORD"] = creds["password"]
    quiet(["docker", "run", "--rm", "--network", "host",
           "-e", "PGPASSWORD", "-e", "PGSSLMODE=require",
           "-v", str(root) + ":/backup:ro", "postgres:16-alpine",
           "pg_restore", "--exit-on-error", "--no-owner", "--no-acl",
           "--host", creds["host"], "--port", str(creds["port"]),
           "--username", creds["user"], "--dbname", shadow,
           "/backup/" + filename], env=environ, timeout=5400)


def terminate_sessions(creds, database):
    # Only connections to the isolated destination DB; not the source.
    results = sql(creds, "postgres",
                  "SELECT count(*) FROM pg_stat_activity WHERE datname='" + database + "'",
                  output=True)
    if not results.isdigit():
        raise RuntimeError("Could not inspect destination connections")
    sql(creds, "postgres",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname='" + database + "' AND pid <> pg_backend_pid()")
    remaining = sql(creds, "postgres",
                    "SELECT count(*) FROM pg_stat_activity WHERE datname='" + database + "'",
                    output=True)
    if remaining != "0":
        raise RuntimeError("Destination connection termination incomplete")


def promote(creds, live, shadow, prior):
    if database_exists(creds, prior):
        raise RuntimeError("Rollback database name already exists")
    # Deny reconnections before terminating, guaranteeing no split active sessions.
    sql(creds, "postgres", 'ALTER DATABASE "' + live + '" WITH ALLOW_CONNECTIONS false')
    try:
        terminate_sessions(creds, live)
        sql(creds, "postgres", 'ALTER DATABASE "' + live + '" RENAME TO "' + prior + '"')
        sql(creds, "postgres", 'ALTER DATABASE "' + shadow + '" RENAME TO "' + live + '"')
    except Exception:
        # Reopen the original DB under whichever name it currently has.
        if database_exists(creds, prior) and not database_exists(creds, live):
            sql(creds, "postgres", 'ALTER DATABASE "' + prior + '" RENAME TO "' + live + '"')
        if database_exists(creds, live):
            sql(creds, "postgres", 'ALTER DATABASE "' + live + '" WITH ALLOW_CONNECTIONS true')
        raise


def revert_promotion(creds, live, shadow, prior):
    if not database_exists(creds, prior):
        return
    sql(creds, "postgres", 'ALTER DATABASE "' + live + '" WITH ALLOW_CONNECTIONS false')
    terminate_sessions(creds, live)
    sql(creds, "postgres", 'ALTER DATABASE "' + live + '" RENAME TO "' + shadow + '"')
    sql(creds, "postgres", 'ALTER DATABASE "' + prior + '" RENAME TO "' + live + '"')
    sql(creds, "postgres", 'ALTER DATABASE "' + live + '" WITH ALLOW_CONNECTIONS true')


def main():
    backup = runpy.run_path(str(Path(__file__).with_name("migration-backup-target-db.py")))
    backup["validate_scope"]()
    if os.environ.get("MIGRATION_STAGE") != "TARGET_BACKUP_ONLY":
        raise RuntimeError("Migration stage mismatch")
    if os.environ.get("IMPORT_TARGET_SNAPSHOTS") != "YES":
        raise RuntimeError("Target import not explicitly enabled")
    storage = backup["storage_env"]()
    probes = runpy.run_path(str(Path(__file__).with_name("migration-data-readonly-db-probe.py")))
    app_credentials = probes["app_credentials"]
    run_id = os.environ["GITHUB_RUN_ID"]
    root = Path(os.environ["RUNNER_TEMP"]) / "private-source-restoration"
    root.mkdir(mode=0o700, exist_ok=False)
    try:
        candidates = {}
        for kind, (live, app, expected_sha, expected_size) in ARCHIVES.items():
            creds = app_credentials(app)
            if creds["dbname"] != live or creds["host"] != SERVER:
                raise RuntimeError("Unexpected target database binding")
            shadow, prior = dbnames(run_id, live)
            if database_exists(creds, shadow) or database_exists(creds, prior):
                raise RuntimeError("Nonempty reserved migration database name")
            backup_bytes = backup_exists_and_archive(storage, root, kind)
            if backup_bytes < 100:
                raise RuntimeError("Target recovery snapshot not verified")
            file = root / ("source-" + kind + ".dump")
            download_blob(storage, "coderats-" + kind + "-" + SOURCE_RUN + ".dump", file)
            if file.stat().st_size != expected_size or sha256(file) != expected_sha:
                raise RuntimeError("Source snapshot digest mismatch")
            candidates[kind] = (creds, live, shadow, prior, file.name)
        # No existing destination DBs are modified during extraction/import.
        for kind, (creds, live, shadow, prior, name) in candidates.items():
            import_shadow(creds, live, shadow, name, root)
            count = verify_import(creds, kind, shadow)
            print("MIGRATION_IMPORTED_" + kind.upper() + "_SCHEMA_VALID=True")
            print("MIGRATION_IMPORTED_" + kind.upper() + "_TABLE_COUNT=" + str(count))
        completed = []
        try:
            for kind, (creds, live, shadow, prior, name) in candidates.items():
                promote(creds, live, shadow, prior)
                completed.append(kind)
                verify_import(creds, kind, live)
                print("MIGRATION_" + kind.upper() + "_PROMOTED=True")
        except Exception:
            # Best effort rollback of any completely switched databases.
            for kind in reversed(completed):
                creds, live, shadow, prior, name = candidates[kind]
                try:
                    revert_promotion(creds, live, shadow, prior)
                    print("MIGRATION_" + kind.upper() + "_ROLLBACK_APPLIED=True")
                except Exception:
                    print("::error::Manual destination recovery required; target previous DB is retained.")
            raise
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("### Isolated PostgreSQL restore complete\n")
            summary.write("- Both source archives verified by pinned SHA-256 and restored into new isolated databases\n")
            summary.write("- API and Grader database names switched after validation; original databases retained under _prior_ names\n")
            summary.write("- Web/API/Grader configuration, Azure network, GitHub Apps, source DEV and DNS unchanged\n")
            summary.write("- This is a snapshot mirror only; no final live-write cutover, media migration or OAuth browser validation\n")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("::error::Target-only database import/promotion did not finish; private diagnostics suppressed.")
        sys.exit(1)
