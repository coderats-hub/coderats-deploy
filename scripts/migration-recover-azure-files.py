#!/usr/bin/env python3
"""Recover source Azure Files by copying, never overwriting, onto private target share.

Source-side job only. No writes to source, no access to original user DB, and no
credential / user filenames printed. Target destination SAS is short-lived and
received encrypted by a public certificate created in source DEV context.
"""
from __future__ import annotations
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import urllib.parse

TARGET_ACCOUNT = "stcoderatsmigc26m0911"
TARGET_SHARE = "coderats-media"
TARGET_SID = "da3650a9-e291-43f1-ae4b-9dcc8ff84239"
SOURCE_RG = "rg-coderats-base-dev"
SOURCE_API = "ca-coderats-base-api-dev"
EXPECTED_CATEGORIES = {"posts", "colonies", "member-banners", "organizations", "organization-banners"}

def run(arguments: list[str], *, env=None, timeout=90, capture=False):
    p = subprocess.run(arguments, env=env, timeout=timeout, check=False,
                       stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    if p.returncode:
        raise RuntimeError("Azure read or media transport failed")
    return p.stdout.decode("utf-8") if capture else ""

def az(*args, env=None):
    return json.loads(run(["az", *args, "--only-show-errors", "-o", "json"],
                          env=env, capture=True))

def safe_name(name: str) -> bool:
    return isinstance(name, str) and 3 <= len(name) <= 63 and all(
        c.islower() or c.isdigit() or c == "-" for c in name
    )

def get_source_storage():
    sid = os.environ["SOURCE_SID"]
    if not sid or sid == TARGET_SID:
        raise RuntimeError("Unexpected source subscription identity")
    account = az("account", "show")
    if account.get("id") != sid:
        raise RuntimeError("Original OIDC scope mismatch")
    app = az("containerapp", "show", "--subscription", sid, "-g", SOURCE_RG, "-n", SOURCE_API)
    if app.get("name") != SOURCE_API:
        raise RuntimeError("Unexpected original API identity")
    print("MEDIA_STAGE_SOURCE_API_RESOLVED=true")
    props = app.get("properties") or {}
    template = props.get("template") or {}
    container = (template.get("containers") or [])
    volumes = {v.get("name"): v for v in template.get("volumes") or [] if isinstance(v, dict)}
    if not container or not all(isinstance(c, dict) for c in container):
        raise RuntimeError("Ambiguous source API mount layout")
    mounts = [m for c in container for m in (c.get("volumeMounts") or []) if isinstance(m, dict)]
    print("MEDIA_STAGE_SOURCE_API_AZUREFILE_DECLARED=" + str(any(v.get("storageType") == "AzureFile" for v in volumes.values())).lower())
    print("MEDIA_STAGE_SOURCE_API_VOLUME_MOUNTS_PRESENT=" + str(bool(mounts)).lower())
    azure_mounts = [
        volumes[m.get("volumeName")].get("storageName")
        for m in mounts
        if isinstance(m, dict) and m.get("volumeName") in volumes
        and volumes[m.get("volumeName")].get("storageType") == "AzureFile"
    ]
    azure_mounts = list(dict.fromkeys(x for x in azure_mounts if isinstance(x, str)))
    if len(azure_mounts) != 1:
        raise RuntimeError("Original API media share mount not unique")
    env_id = props.get("managedEnvironmentId") or ""
    if not env_id.lower().startswith("/subscriptions/" + sid.lower() + "/") or not env_id:
        raise RuntimeError("Source environment identifier inconsistent")
    env_name = env_id.rstrip("/").rsplit("/", 1)[-1]
    print("MEDIA_STAGE_SOURCE_ENV_RESOLVED=true")
    bindings = az("containerapp", "env", "storage", "list", "--subscription", sid,
                  "-g", SOURCE_RG, "-n", env_name)
    match = [
        entry for entry in bindings if isinstance(entry, dict) and
        entry.get("name") == azure_mounts[0]
    ]
    if len(match) != 1:
        raise RuntimeError("Original Azure Files mount binding missing")
    print("MEDIA_STAGE_SOURCE_STORAGE_BINDING_MATCHED=true")
    f = (match[0].get("properties") or {}).get("azureFile") or {}
    name = f.get("accountName")
    share = f.get("shareName")
    if not safe_name(name) or not safe_name(share):
        raise RuntimeError("Unexpected source media storage identity")
    try:
        accounts = az("storage", "account", "list", "--subscription", sid, "-g", SOURCE_RG)
    except Exception:
        accounts = []
    if not any(isinstance(x, dict) and x.get("name") == name for x in accounts):
        accounts = az("storage", "account", "list", "--subscription", sid)
    found = [entry for entry in accounts if isinstance(entry, dict) and entry.get("name") == name]
    if len(found) != 1:
        raise RuntimeError("Original source storage account not found uniquely")
    print("MEDIA_STAGE_SOURCE_ACCOUNT_RESOLVED=true")
    resource_id = found[0].get("id") or ""
    rg = resource_id.split("/resourceGroups/", 1)[1].split("/", 1)[0] if "/resourceGroups/" in resource_id else ""
    if not rg:
        raise RuntimeError("Original source storage group unresolved")
    print("MEDIA_STAGE_SOURCE_ACCOUNT_RESOURCEGROUP_RESOLVED=true")
    key = None
    try:
        response = az("storage", "account", "keys", "list", "--subscription", sid,
                      "-g", rg, "-n", name)
        if isinstance(response, list) and response:
            key = response[0].get("value")
    except Exception:
        print("MEDIA_STAGE_SOURCE_ARM_LISTKEYS_DENIED=true")
        # Disabled subscriptions can block ARM listKeys. Existing Container Apps
        # environment storage binding may still expose its key.
        key = f.get("accountKey")
    if not isinstance(key, str) or len(key) < 40 or "*" in key:
        raise RuntimeError("Source storage key unavailable on disabled subscription")
    print("MEDIA_SOURCE_KEY_RECOVERED=true")
    return name, share, key

def validate_target_sas(raw: str):
    url = urllib.parse.urlsplit(raw.strip())
    if (url.scheme != "https" or url.hostname != TARGET_ACCOUNT + ".file.core.windows.net"
        or url.path != "/" + TARGET_SHARE or not url.query or url.fragment):
        raise RuntimeError("Untrusted target Azure Files transfer URL")
    params = urllib.parse.parse_qs(url.query)
    permissions = set(params.get("sp", [""])[0])
    if not {"c","w","l"}.issubset(permissions) or "d" in permissions:
        raise RuntimeError("Target copy capability must not permit delete")
    if params.get("spr") != ["https"] or not params.get("sig"):
        raise RuntimeError("Target scoped SAS is not HTTPS-only")
    return raw.strip()

def azcopy_binary(root: pathlib.Path) -> str:
    existing = shutil.which("azcopy")
    if existing:
        return existing
    archive = root / "azcopy.tgz"
    run(["curl", "--fail", "--silent", "--show-error", "--location",
         "--max-time", "120", "https://aka.ms/downloadazcopy-v10-linux",
         "--output", str(archive)], timeout=130)
    # Keep tar extraction inside temporary directory and reject path escapes.
    with tarfile.open(archive, "r:gz") as tar:
        members = [m for m in tar.getmembers() if pathlib.PurePosixPath(m.name).name == "azcopy" and m.isfile()]
        if len(members) != 1:
            raise RuntimeError("Unexpected AzCopy release archive")
        member = members[0]
        stream = tar.extractfile(member)
        if stream is None:
            raise RuntimeError("AzCopy binary missing")
        path = root / "azcopy"
        with open(path, "wb") as destination:
            shutil.copyfileobj(stream, destination)
        path.chmod(0o700)
        return str(path)

def main():
    if os.environ.get("MIGRATION_MEDIA_STAGE") != "COPY_SOURCE_TO_TARGET_ONLY":
        raise RuntimeError("Unexpected migration stage")
    source_account, source_share, source_key = get_source_storage()
    root = pathlib.Path(os.environ["RUNNER_TEMP"]) / "private-media-recovery"
    root.mkdir(mode=0o700, exist_ok=False)
    try:
        transfer = pathlib.Path(os.environ["RUNNER_TEMP"]) / "private-media-target-uri.txt"
        destination_uri = validate_target_sas(transfer.read_text(encoding="utf-8"))
        expiry = run(["date", "-u", "-d", "+2 hours", "+%Y-%m-%dT%H:%MZ"], capture=True).strip()
        env = os.environ.copy()
        env["AZURE_STORAGE_ACCOUNT"] = source_account
        env["AZURE_STORAGE_KEY"] = source_key
        # Prove old data plane is readable before making any destination writes.
        entries = az("storage", "file", "list", "--account-name", source_account,
                     "--share-name", source_share, env=env)
        if not isinstance(entries, list) or not entries:
            raise RuntimeError("Source file share is unavailable or empty")
        dirs = {str(e.get("name")) for e in entries if isinstance(e, dict) and
                (e.get("type") == "dir" or e.get("isDirectory") is True)}
        # Some Azure CLI versions omit the 'type'; root contents still need
        # some recognized CodeRats category to prevent accidental wrong-share copy.
        all_names = {str(e.get("name")) for e in entries if isinstance(e, dict)}
        if not (all_names | dirs) & EXPECTED_CATEGORIES:
            raise RuntimeError("Original media share does not contain expected category directories")
        print("MEDIA_SOURCE_DATA_PLANE_READABLE=true")
        print("MEDIA_SOURCE_ROOT_ENTRIES=" + str(len(entries)))
        source_sas = run(["az","storage","share","generate-sas","--account-name",source_account,
                          "--name",source_share,"--permissions","rl","--https-only",
                          "--expiry",expiry,"--only-show-errors","-o","tsv"],
                         env=env, capture=True).strip()
        if not source_sas or "sig=" not in source_sas:
            raise RuntimeError("Source read-only share SAS creation failed")
        src = "https://" + source_account + ".file.core.windows.net/" + source_share + "?" + source_sas.lstrip("?")
        binary = azcopy_binary(root)
        temp_logs = root / "azcopy-logs"
        temp_logs.mkdir(mode=0o700)
        env2 = os.environ.copy()
        env2["AZCOPY_LOG_LOCATION"] = str(temp_logs)
        env2["AZCOPY_JOB_PLAN_LOCATION"] = str(temp_logs)
        # No stdout/stderr or AzCopy logs are published; never expose SAS URLs.
        run([binary, "copy", src, destination_uri, "--recursive=true",
             "--overwrite=false", "--log-level=NONE", "--output-type=json"],
            env=env2, timeout=7200)
        print("MEDIA_RECOVERY_COPY_NON_DESTRUCTIVE_COMPLETED=true")
        with open(os.environ["GITHUB_STEP_SUMMARY"],"a",encoding="utf-8") as f:
            f.write("### Source Azure Files recovery\n")
            f.write("- Original source share had expected CodeRats media directories and readable data plane\n")
            f.write("- Copy to pinned private destination share completed with no overwrites or deletions\n")
            f.write("- No DNS, database rows, app revisions or source objects modified\n")
            f.write("- Reconcile historic source-domain URLs separately before cutover\n")
    finally:
        shutil.rmtree(root, ignore_errors=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc)
        if message in {
            "Unexpected source subscription identity",
            "Original OIDC scope mismatch",
            "Unexpected original API identity",
            "Ambiguous source API mount layout",
            "Original API media share mount not unique",
            "Source environment identifier inconsistent",
            "Original Azure Files mount binding missing",
            "Unexpected source media storage identity",
            "Original source storage account not found uniquely",
            "Original source storage group unresolved",
            "Source storage key unavailable on disabled subscription",
            "Source file share is unavailable or empty",
            "Original media share does not contain expected category directories",
        }:
            print("::error::MEDIA_RECOVERY_BLOCKED: " + message)
        else:
            print("::error::MEDIA_RECOVERY_BLOCKED: Azure media access or secure transport check failed.")
        sys.exit(1)
