#!/usr/bin/env python3
"""Prepare a ONE-EXECUTION Azure Container Apps Job override, without changing job configuration.

Input: the PRIVATE existing job's properties.template saved by the OIDC workflow.
Never print the job template: it may contain secret references and private config.
"""
import argparse
import json
import pathlib
import re

EXPECTED = "marialmeida1"
VERSION = "recover-marialmeida1-20261029-v1"


def override(template: dict, username: str, version: str, image: str) -> dict:
    if username != EXPECTED or version != VERSION:
        raise ValueError("Unexpected recovery target or replay version")
    if not re.fullmatch(r"crcoderatsbasedev9e0045[.]azurecr[.]io/api-coderats-base@sha256:[a-f0-9]{64}", image):
        raise ValueError("Image must be the verified immutable DEV API digest")
    if not isinstance(template, dict) or not isinstance(template.get("containers"), list):
        raise ValueError("Invalid Azure job template")
    containers = [c for c in template["containers"] if isinstance(c, dict) and c.get("name") == "get-worker"]
    if len(containers) != 1 or len(template["containers"]) != 1:
        raise ValueError("Unexpected job container configuration")
    container = containers[0]
    container["image"] = image
    env = container.get("env")
    if not isinstance(env, list):
        raise ValueError("Missing job environment; refusing to drop secrets")

    updates = {
        "APP_RUNTIME_MODE": "worker",
        "SPRING_PROFILES_ACTIVE": "get-worker",
        "GET_WORKER_ENABLED": "true",
        "GET_WORKER_RUN_ONCE": "false",
        "GET_BACKFILL_ENABLED": "false",
        "GET_RECOVERY_ENABLED": "true",
        "GET_RECOVERY_USERNAME": username,
        "GET_RECOVERY_VERSION": version,
        "GET_RECOVERY_MAX_SECONDS": "3000",
        "GET_BETA_ENABLED": "true",
        "GET_BETA_ENVIRONMENT": "dev",
        "GET_BETA_ALLOWED_REPOSITORY_OWNER": "coderats-hub",
        "GET_BETA_ALLOW_LEGACY_OAUTH": "true",
        "GET_BETA_POLICY_RELEASE": "beta4-proficiency-quality",
    }
    existing = {e.get("name"): e for e in env if isinstance(e, dict)}
    for name, value in updates.items():
        if name in existing:
            existing[name].pop("secretRef", None)
            existing[name]["value"] = value
        else:
            env.append({"name": name, "value": value})
    return template


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--username", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--image", required=True)
    opts = p.parse_args()
    source = pathlib.Path(opts.source)
    output = pathlib.Path(opts.output)
    obj = json.loads(source.read_text(encoding="utf-8"))
    output.write_text(json.dumps(override(obj, opts.username, opts.version, opts.image)), encoding="utf-8")
    output.chmod(0o600)
    print("Prepared a restricted recovery execution override; original Azure job is unchanged.")


if __name__ == "__main__":
    main()
