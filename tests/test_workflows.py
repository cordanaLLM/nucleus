"""Test suite for GitHub Actions workflows validation and least-privilege permissions."""

from pathlib import Path
import shutil
import subprocess
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


def test_workflows_count():
    """Ensure all 12 production CI/CD workflows exist."""
    workflows = list(WORKFLOWS_DIR.glob("*.yml"))
    assert len(workflows) == 12, f"Expected exactly 12 workflows, found {len(workflows)}"


def test_workflows_least_privilege_permissions():
    """Ensure all workflows declare explicit top-level least-privilege permissions."""
    for wf in WORKFLOWS_DIR.glob("*.yml"):
        content = wf.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        assert "permissions" in parsed, f"Workflow {wf.name} missing top-level permissions block"
        perms = parsed["permissions"]
        assert isinstance(perms, dict) or perms == "read-all", (
            f"Workflow {wf.name} permissions must be restricted"
        )
        if isinstance(perms, dict) and "contents" in perms:
            assert perms["contents"] in ("read", "write"), (
                f"Workflow {wf.name} invalid contents permission"
            )


def test_required_aggregator_contract():
    """Ensure required-aggregator.yml defines the required-checks job."""
    aggregator = WORKFLOWS_DIR / "required-aggregator.yml"
    assert aggregator.exists(), "required-aggregator.yml must exist"
    content = aggregator.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    assert "jobs" in parsed
    assert "required-checks" in parsed["jobs"], "required-aggregator must define job 'required-checks'"


def test_actionlint_passes():
    """Ensure all workflows pass actionlint with zero errors."""
    actionlint_bin = shutil.which("actionlint")
    if not actionlint_bin:
        for candidate in [
            Path("/usr/local/bin/actionlint"),
            Path("/usr/bin/actionlint"),
            Path.home() / "go" / "bin" / "actionlint",
        ]:
            if candidate.exists() and candidate.is_file():
                actionlint_bin = str(candidate)
                break
    if not actionlint_bin:
        pytest.skip("actionlint binary not found in PATH or standard locations")

    workflows = list(WORKFLOWS_DIR.glob("*.yml"))
    cmd = [actionlint_bin] + [str(w) for w in workflows]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"actionlint failed on workflows:\n{res.stdout}\n{res.stderr}"

