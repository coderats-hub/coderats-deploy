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
        assert isinstance(on, dict) and set(on) == {"workflow_dispatch"}
        assert "coderats-public-deploy" in text
        assert "self-hosted" in text and "ubuntu-latest" not in text
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

print("PASS: public dispatcher has protected main/DEV-only entrypoint, private App checkout, "
      "DEV guards and own-runner-only builds; all Bash scripts parse.")
