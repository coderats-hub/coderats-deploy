#!/usr/bin/env python3
"""Back up the two existing isolated destination DBs to private Azure Blob.

No source subscription access, PostgreSQL writes, public artifacts or secret logs.
The environment and names are deliberately pinned to migration-target.
"""
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

TARGET_SID = "da3650a9-e291-43f1-ae4b-9dcc8ff84239"
TARGET_TID = "1cf74c7e-fcd9-46f7-8a9d-e699d3529003"
RG = "rg-coderats-migration-c26m0911"
STORAGE = "stcoderatsmigc26m0911"
CONTAINER = "coderats-migration-staging"
APPS = {
    "api": "ca-cdmstd-api-c26m0911",
    "grader": "ca-cdmstd-grader-c26m0911",
}
EXPECTED_DBS = {"api": "coderats_base", "grader": "coderats_grader"}


def az_json(*args, env=None):
    run = subprocess.run(
        ["az", *args, "--only-show-errors", "-o", "json"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=75, check=False
    )
    if run.returncode:
        raise RuntimeError("Azure destination metadata unavailable")
    return json.loads(run.stdout)


def private_command(args, *, env=None, timeout=600):
    run = subprocess.run(args, env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    if run.returncode:
        raise RuntimeError("Private target backup command failed")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_scope():
    if os.environ.get("AZURE_SUBSCRIPTION_ID") != TARGET_SID:
        raise RuntimeError("Destination subscription mismatch")
    if os.environ.get("AZURE_TENANT_ID") != TARGET_TID:
        raise RuntimeError("Destination tenant mismatch")
    if os.environ.get("MIGRATION_STAGE") != "TARGET_BACKUP_ONLY":
        raise RuntimeError("Unexpected backup stage")
    account = az_json("account", "show")
    if account.get("id") != TARGET_SID or account.get("tenantId") != TARGET_TID:
        raise RuntimeError("Azure identity mismatch")
    metadata = az_json("storage", "account", "show",
                       "--subscription", TARGET_SID, "-g", RG, "-n", STORAGE)
    if metadata.get("name") != STORAGE or metadata.get("allowBlobPublicAccess") is not False:
        raise RuntimeError("Private destination storage prerequisite missing")


def storage_env():
    credentials = az_json("storage", "account", "keys", "list",
                          "--subscription", TARGET_SID, "-g", RG, "-n", STORAGE)
    key = credentials[0].get("value") if isinstance(credentials, list) and credentials else None
    if not isinstance(key, str) or not key:
        raise RuntimeError("Target storage permission unavailable")
    environ = os.environ.copy()
    environ["AZURE_STORAGE_KEY"] = key
    environ["AZURE_STORAGE_ACCOUNT"] = STORAGE
    container = az_json("storage", "container", "show",
                        "--account-name", STORAGE, "--name", CONTAINER, env=environ)
    if (container.get("properties") or {}).get("publicAccess") not in (None, "None", "none"):
        raise RuntimeError("Destination container is not private")
    return environ


def dump_db(creds, kind, directory):
    if creds["dbname"] != EXPECTED_DBS[kind]:
        raise RuntimeError("Unexpected database identity")
    filename = "target-" + kind + ".dump"
    environ = os.environ.copy()
    environ["PGPASSWORD"] = creds["password"]
    command = [
        "docker", "run", "--rm", "--network", "host",
        "-e", "PGPASSWORD", "-e", "PGSSLMODE=require",
        "-v", str(directory) + ":/backup",
        "postgres:16-alpine", "pg_dump",
        "--format=custom", "--no-owner", "--no-acl",
        "--host", creds["host"], "--port", str(creds["port"]),
        "--username", creds["user"], "--dbname", creds["dbname"],
        "--file", "/backup/" + filename,
    ]
    private_command(command, env=environ, timeout=5400)
    file = directory / filename
    if not file.is_file() or not 100 < file.stat().st_size < 4_000_000_000:
        raise RuntimeError("Target backup size outside safety bounds")
    private_command(["docker", "run", "--rm",
                     "-v", str(directory) + ":/backup:ro",
                     "postgres:16-alpine", "pg_restore", "--list",
                     "/backup/" + filename], timeout=300)
    return file


def upload_and_verify(file, kind, run_id, environ, directory):
    blob = "target-before-restore-" + kind + "-" + run_id + ".dump"
    private_command(["az", "storage", "blob", "upload",
                     "--account-name", STORAGE, "--container-name", CONTAINER,
                     "--name", blob, "--file", str(file),
                     "--overwrite", "false", "--only-show-errors", "-o", "none"],
                    env=environ, timeout=1800)
    copy = directory / ("verified-" + kind + ".dump")
    private_command(["az", "storage", "blob", "download",
                     "--account-name", STORAGE, "--container-name", CONTAINER,
                     "--name", blob, "--file", str(copy),
                     "--overwrite", "true", "--only-show-errors", "-o", "none"],
                    env=environ, timeout=1800)
    if digest(copy) != digest(file) or copy.stat().st_size != file.stat().st_size:
        raise RuntimeError("Private target backup verification failed")
    return blob


def main():
    validate_scope()
    run_id = os.environ["GITHUB_RUN_ID"]
    if not run_id.isascii() or not run_id.isdigit() or len(run_id) > 20:
        raise RuntimeError("Invalid Actions execution identifier")
    storage = storage_env()
    probe = runpy.run_path(str(Path(__file__).with_name("migration-data-readonly-db-probe.py")))
    credentials = probe["app_credentials"]
    root = Path(os.environ["RUNNER_TEMP"]) / "private-target-backups"
    root.mkdir(mode=0o700, exist_ok=False)
    try:
        verified = {}
        for kind, app in APPS.items():
            creds = credentials(app)
            file = dump_db(creds, kind, root)
            verified[kind] = upload_and_verify(file, kind, run_id, storage, root)
            print("TARGET_PRE_RESTORE_BACKUP_" + kind.upper() + "_VERIFIED=True")
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("### Isolated migration-target: rollback backups verified\\n")
            summary.write("- API and Grader existing target databases: separately dumped and verified in private destination Blob\\n")
            summary.write("- Zero source subscription access, DB writes, DNS changes or public data artifacts\\n")
            summary.write("- No restoration was attempted; keep the blobs for rollback until after cutover acceptance\\n")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("::error::Isolated target backup/verification blocked; private diagnostics suppressed.")
        sys.exit(1)
