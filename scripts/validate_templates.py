"""Validate the public templates without pulling private CodeRats sources."""
from pathlib import Path
import yaml

root = Path(__file__).resolve().parents[1]
workflow_dir = root / ".github" / "workflows"

for name in ("dev-container.yml", "dev-get-worker.yml", "validate-templates.yml"):
    file = workflow_dir / name
    assert file.is_file(), f"missing {name}"
    data = yaml.safe_load(file.read_text())
    assert "jobs" in data, f"no jobs in {name}"
    if name != "validate-templates.yml":
        text = file.read_text()
        assert "self-hosted" in text
        assert "coderats-deploy" in text
        assert "ubuntu-latest" not in text, "private builds cannot run on hosted public workers"
        assert "workflow_call" in text
    else:
        assert "ubuntu-latest" in file.read_text()

for file in (root / "examples").glob("*.yml"):
    text = file.read_text()
    assert "REPLACE_WITH_FULL_40_CHARACTER_PUBLIC_REPO_SHA" in text
    assert "secrets: inherit" in text

print("PASS: only public template validation uses GitHub-hosted runners; private application builds require private self-hosted callers.")
