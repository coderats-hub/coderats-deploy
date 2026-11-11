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
        # This workflow runs HERE, not in the private caller repository.
        on = data.get("on", data.get(True, {}))
        assert isinstance(on, dict) and set(on) == {"workflow_dispatch", "push", "schedule"}
        assert on["schedule"] == [{"cron": "*/15 * * * *"}]
        assert "github.event_name == 'schedule'" in text
        assert on["push"] == {"branches": ["main"], "paths": [".github/workflows/deploy-private-dev.yml"]}
        assert "github.event.head_commit.message == 'ci(dev): publish beta4 api'" in text
        assert "runs-on: ubuntu-latest" in text
        assert "self-hosted" not in text
        assert "Check required protected DEV credentials" in text
        assert "actions/create-github-app-token@" in text
        assert "persist-credentials: false" in text
        assert "github.repository == 'coderats-hub/coderats-deploy'" in text
        assert "github.ref == 'refs/heads/main'" in text
        assert "environment: dev" in text
        assert "APP_PRIVATE_KEY" in text
        assert "rg-coderats-base-dev" in text
        assert "workflow_call" not in text
        assert "pull_request" not in text and "repository_dispatch" not in text
        assert "GET_BACKFILL_ENABLED=false" in text
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

# Keep the legacy mirror retirement workflow explicitly opt-in and syntax-checked.
legacy_retirement = workflows / "retire-legacy-migration-mirror.yml"
legacy_text = legacy_retirement.read_text()
legacy_data = yaml.safe_load(legacy_text)
legacy_events = legacy_data.get("on", legacy_data.get(True, {}))
assert isinstance(legacy_events, dict) and set(legacy_events) == {"workflow_dispatch"}, (
    "Legacy retirement must never be triggered by push or schedule"
)
assert "environment: migration-target" in legacy_text
assert "github.ref == 'refs/heads/main'" in legacy_text
assert "default: audit" in legacy_text
assert "DELETE_LEGACY_MIRROR_C26M0911" in legacy_text
assert "az containerapp delete" in legacy_text
assert "az containerapp env delete" in legacy_text
assert "az group delete" not in legacy_text
assert "terraform" not in legacy_text.lower()
for job in legacy_data["jobs"].values():
    for step in job.get("steps", []):
        script = step.get("run")
        if isinstance(script, str):
            syntax = subprocess.run(["bash", "-n"], input=script, text=True,
                                    capture_output=True, check=False)
            assert syntax.returncode == 0, (
                f"Legacy retirement bash syntax invalid at {step.get('name')}: "
                f"{syntax.stderr}"
            )

print("PASS: public dispatcher has protected main/DEV-only entrypoint, private App checkout, "
      "DEV guards and public hosted-runner builds; all Bash scripts parse.")
