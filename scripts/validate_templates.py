"""Validate CodeRats public CI/CD workflows without accessing private code."""
from pathlib import Path
import subprocess

import yaml

root = Path(__file__).resolve().parents[1]
workflows = root / ".github" / "workflows"

for name in ("dev-container.yml", "dev-get-worker.yml", "validate-templates.yml", "deploy-private-dev.yml"):
    file = workflows / name
    assert file.is_file(), f"Missing workflow {name}"
    text = file.read_text()
    data = yaml.safe_load(text)
    assert isinstance(data, dict) and data.get("jobs"), f"Invalid jobs in {name}"
    if name == "validate-templates.yml":
        assert "ubuntu-latest" in text, "Only public-source template lint may use hosted runners"
    elif name == "deploy-private-dev.yml":
        # Official Brazil South Azure only, via existing protected destination OIDC.
        on = data.get("on", data.get(True, {}))
        assert isinstance(on, dict) and set(on) == {"workflow_dispatch", "push"}
        assert on["push"] == {"branches": ["main"], "paths": [".github/workflows/deploy-private-dev.yml"]}
        assert set(on["workflow_dispatch"]["inputs"]["service"]["options"]) == {"web", "api"}
        assert "github.event.head_commit.message == 'ci(migration): release target web'" in text
        assert "github.event.head_commit.message == 'ci(migration): release target api'" in text
        assert "runs-on: ubuntu-latest" in text and "self-hosted" not in text
        assert "Check required protected DEV credentials" in text
        assert "actions/create-github-app-token@" in text
        assert "persist-credentials: false" in text
        assert "github.repository == 'coderats-hub/coderats-deploy'" in text
        assert "github.ref == 'refs/heads/main'" in text
        assert "environment: migration-target" in text
        assert "APP_PRIVATE_KEY" in text
        assert "rg-coderats-migration-c26m0911" in text
        assert "ca-cdmstd-web-c26m0911" in text
        assert "ca-cdmstd-api-c26m0911" in text
        assert "ca-cdmstd-grader-c26m0911" in text
        assert "grader.internal.$domain" in text
        assert "TARGET_GRADER_ORIGIN=https://$grader_fqdn" in text
        assert '"GRADER_API_URL=$TARGET_GRADER_ORIGIN"' in text
        assert '"GRADER_POLLING_ENABLED=true"' in text
        assert "API Grader origin or polling configuration did not take effect" in text
        assert "NEXT_PUBLIC_API_URL=https://coderats.club" in text
        assert "GITHUB_OAUTH_REDIRECT_URI=https://coderats.club/login/oauth2/code/github" in text
        assert "rg-coderats-base-dev" not in text
        assert "crcoderatsbasedev9e0045" not in text
        assert "environment: dev" not in text
        assert "schedule:" not in text
        assert "containerapp job start" not in text
        assert "workflow_call" not in text
        assert "pull_request" not in text and "repository_dispatch" not in text
    else:
        # Deprecated templates remain present for audit; they must not build
        # private applications on public GitHub-hosted runners.
        assert "self-hosted" in text and "ubuntu-latest" not in text

    for job in data["jobs"].values():
        for step in job.get("steps", []):
            script = step.get("run")
            if isinstance(script, str):
                result = subprocess.run(["bash", "-n"], input=script,
                                        text=True, capture_output=True, check=False)
                assert result.returncode == 0, (
                    f"{name}: bad shell syntax in {step.get('name')} "
                    f"{result.stderr}"
                )

# Keep isolated pre-restore backup manual-only and syntax-checked in public CI.
target_backup = workflows / "backup-migration-target-before-restore.yml"
target_text = target_backup.read_text()
target_data = yaml.safe_load(target_text)
target_events = target_data.get("on", target_data.get(True, {}))
assert isinstance(target_events, dict) and set(target_events) == {"workflow_dispatch"}, (
    "Target rollback backup must be manual-only after data migration"
)
assert "environment: migration-target" in target_text
assert "github.ref == 'refs/heads/main'" in target_text
assert "BACKUP_TARGET_BEFORE_RESTORE" in target_text
assert "source" not in target_data["jobs"], "Target backup cannot have source environment jobs"
for job in target_data["jobs"].values():
    for step in job.get("steps", []):
        script = step.get("run")
        if isinstance(script, str):
            syntax = subprocess.run(["bash", "-n"], input=script, text=True,
                                    capture_output=True, check=False)
            assert syntax.returncode == 0, f"Target backup bash invalid: {syntax.stderr}"
target_python = root / "scripts" / "migration-backup-target-db.py"
compile_check = subprocess.run(["python3", "-m", "py_compile", str(target_python)],
                               capture_output=True, text=True, check=False)
assert compile_check.returncode == 0, "Target backup Python syntax invalid"

# One-time source-read-only => isolated Azure Files non-destructive media recovery.
media = workflows / "recover-migration-target-media.yml"
media_text = media.read_text()
media_workflow = yaml.safe_load(media_text)
events = media_workflow.get("on", media_workflow.get(True, {}))
assert events == {
    "workflow_dispatch": {"inputs": {"confirmation": {
        "description": "Type COPY_LEGACY_MEDIA (origin read only, target Files create only)",
        "required": True, "type": "string"
    }}}
}, "Media recovery must remain manual-only"
assert "github.ref == 'refs/heads/main'" in media_text
assert "github.event_name == 'workflow_dispatch' && inputs.confirmation == 'COPY_LEGACY_MEDIA'" in media_text
assert 'environment: migration-target' in media_text and 'environment: dev' in media_text
for job in media_workflow["jobs"].values():
    for step in job.get("steps", []):
        script = step.get("run")
        if isinstance(script, str):
            check = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
            assert check.returncode == 0, "Media recovery shell syntax invalid"
media_python = root / "scripts" / "migration-recover-azure-files.py"
assert media_python.exists()
compile(media_python.read_text(), str(media_python), "exec")


# The canonical Grader release must also be isolated from the exhausted Azure.
grader = workflows / "release-grader-languages-dev.yml"
grader_text = grader.read_text()
grader_data = yaml.safe_load(grader_text)
assert "environment: migration-target" in grader_text
assert "rg-coderats-migration-c26m0911" in grader_text
assert "ca-cdmstd-grader-c26m0911" in grader_text
assert "rg-coderats-base-dev" not in grader_text
assert grader_data.get("jobs", {}).get("release")
for job in grader_data["jobs"].values():
    for step in job.get("steps", []):
        script = step.get("run")
        if isinstance(script, str):
            result = subprocess.run(["bash", "-n"], input=script,
                                    text=True, capture_output=True)
            assert result.returncode == 0, (
                f"Official Grader bad shell syntax: {result.stderr}"
            )
for name in ("deploy-optimizations-release.yml", "deploy-profile-release-preview.yml", "restore-grader-ai-dev.yml"):
    legacy = yaml.safe_load((workflows / name).read_text())
    events = legacy.get("on", legacy.get(True, {}))
    assert set(events) == {"workflow_dispatch"}
    assert "id-token: write" not in (workflows / name).read_text()
    assert set(legacy["jobs"]) == {"retired"}

print("PASS: official Azure release is protected by exact destination scope, private checkout, "
      "Bash syntax checks and legacy workflow retirement.")
