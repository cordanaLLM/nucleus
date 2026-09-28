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


def test_publish_release_ships_kernel_artifact_manifest():
    """Ensure publish-release.yml generates, uploads, and announces the imago kernel artifact manifest."""
    workflow = WORKFLOWS_DIR / "publish-release.yml"
    parsed = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    steps = parsed["jobs"]["publish"]["steps"]
    names = [step.get("name", "") for step in steps]

    package = names.index("Package Signed Deb and UKI Binaries")
    manifest = names.index("Generate Kernel Artifact Manifest")
    publish = names.index("Publish to GitHub Release")
    assert package < manifest < publish, "manifest must be generated after signing and before upload"
    assert "imago.nucleus.kernel-artifact.v1" in steps[manifest]["run"]
    assert "kernel-${STREAM}.config" in steps[package]["run"], "merged kconfig must be shipped for config_digest"

    files = steps[publish]["with"]["files"]
    for pattern in ("output/*.manifest.json", "output/*.config", "output/SHA256SUMS*"):
        assert pattern in files, f"release upload must include {pattern}"

    dispatch = steps[names.index("Dispatch Downstream Notification to imago")]
    payload = dispatch["with"]["client-payload"]
    for key in ("stream", "version", "tag"):
        assert f'"{key}"' in payload, f"downstream payload must carry {key}"


def _publish_release_steps():
    parsed = yaml.safe_load((WORKFLOWS_DIR / "publish-release.yml").read_text(encoding="utf-8"))
    steps = parsed["jobs"]["publish"]["steps"]
    return steps, [step.get("name", "") for step in steps]


def test_publish_release_resolves_tags_without_fallback():
    """A kernel tag must resolve to exactly one versions.json stream; there is no default."""
    steps, names = _publish_release_steps()
    resolve = steps[names.index("Resolve Stream and Version")]
    assert resolve["id"] == "meta"
    assert "scripts/resolve_release_tag.py" in resolve["run"]
    assert '--github-output "${GITHUB_OUTPUT}"' in resolve["run"]
    assert "python3 -c" not in resolve["run"], "the tag grammar lives in scripts/resolve_release_tag.py"
    assert "${{" not in resolve["run"], "the tag reaches the resolver through env, never inline"


def test_publish_release_dispatch_requires_kernel_forge_token():
    """GITHUB_TOKEN cannot dispatch to another repository, so nothing falls back to it."""
    steps, names = _publish_release_steps()
    index = names.index("Dispatch Downstream Notification to imago")
    dispatch, guard = steps[index], steps[index - 1]
    assert dispatch["with"]["token"] == "${{ secrets.KERNEL_FORGE_TOKEN }}"
    assert guard["env"]["KERNEL_FORGE_TOKEN"] == "${{ secrets.KERNEL_FORGE_TOKEN }}"
    assert "::error" in guard["run"] and "KERNEL_FORGE_TOKEN" in guard["run"]
    assert "exit 1" in guard["run"]
    assert "${{" not in guard["run"], "the secret reaches the guard through env, never inline"
    payload = dispatch["with"]["client-payload"]
    assert "steps.meta.outputs.release_version" in payload
    assert "steps.meta.outputs.release_tag" in payload


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

