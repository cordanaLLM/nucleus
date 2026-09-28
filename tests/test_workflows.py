"""Test suite for GitHub Actions workflows validation and least-privilege permissions."""

import os
import re
import shutil
import subprocess
from pathlib import Path

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


def test_no_run_block_interpolates_event_data():
    """Event data reaches a script through env:, never through ${{ }} inside a run: block."""
    event_data = re.compile(r"\$\{\{[^}]*github\.(event|head_ref)[^}]*\}\}")
    for wf in WORKFLOWS_DIR.glob("*.yml"):
        parsed = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for job in parsed["jobs"].values():
            for step in job.get("steps", []):
                assert not event_data.search(step.get("run", "")), f"{wf.name}: {step.get('name')}"


def test_verify_requirements_fetches_pinned_documents_and_keeps_the_report():
    """The dispatch payload goes to the fetch script through env; the report is kept."""
    parsed = yaml.safe_load((WORKFLOWS_DIR / "verify-requirements.yml").read_text(encoding="utf-8"))
    steps = {step.get("name", ""): step for step in parsed["jobs"]["verify"]["steps"]}
    fetch = steps["Fetch Requirement Documents"]
    assert "scripts/fetch-kernel-requirements.sh" in fetch["run"]
    for key in ("PAYLOAD_SOURCE", "PAYLOAD_REF", "PAYLOAD_SHA256", "PAYLOAD_CORRELATION_ID"):
        assert key in fetch["env"], key
    verify_step = steps["Verify Requirements Against the Declared KConfig"]
    verify = verify_step["run"]
    assert "--report-json=" in verify and "--nucleus-revision=" in verify
    assert steps["Upload Verification Report"]["if"] == "always()"
    # The verifier is piped through tee: without pipefail its exit status is lost and the gate
    # reports green. An explicit bash runs with -eo pipefail; the step also sets it itself.
    assert parsed["jobs"]["verify"]["defaults"]["run"]["shell"] == "bash"
    assert verify_step.get("shell", "bash") == "bash"
    assert "| tee" in verify and "set -euo pipefail" in verify


def test_verify_requirements_never_cancels_a_pending_dispatch():
    """A group holds one pending run; each dispatch gets its own, so none replaces another."""
    parsed = yaml.safe_load((WORKFLOWS_DIR / "verify-requirements.yml").read_text(encoding="utf-8"))
    concurrency = parsed["concurrency"]
    group = " ".join(concurrency["group"].split())
    assert "github.event_name == 'repository_dispatch'" in group
    assert "github.run_id" in group
    assert concurrency["cancel-in-progress"] is False


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


def test_publish_release_tag_and_ref_cannot_diverge():
    """A dispatched run must start from the tag it releases, and the release names that tag."""
    steps, names = _publish_release_steps()
    resolve = steps[names.index("Resolve Stream and Version")]
    assert '--ref "${GITHUB_REF}"' in resolve["run"], "the resolver must compare the run's ref with the tag"
    release = steps[names.index("Publish to GitHub Release")]
    assert release["with"]["tag_name"] == "${{ steps.meta.outputs.release_tag }}"


def _guard_step():
    steps, names = _publish_release_steps()
    return steps, names, steps[names.index("Require KERNEL_FORGE_TOKEN for the Downstream Dispatch")]


def _run_guard(token_set: str) -> subprocess.CompletedProcess:
    """Execute the guard's run block the way the runner does, with only the boolean in its env."""
    _, _, guard = _guard_step()
    env = {"PATH": os.environ["PATH"], "KERNEL_FORGE_TOKEN_SET": token_set}
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-c", guard["run"]],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_publish_release_dispatch_requires_kernel_forge_token():
    """GITHUB_TOKEN cannot dispatch to another repository, so nothing falls back to it."""
    steps, names, guard = _guard_step()
    dispatch = steps[names.index("Dispatch Downstream Notification to imago")]
    assert dispatch["with"]["token"] == "${{ secrets.KERNEL_FORGE_TOKEN }}"
    assert guard["env"] == {"KERNEL_FORGE_TOKEN_SET": "${{ secrets.KERNEL_FORGE_TOKEN != '' }}"}
    assert "${{" not in guard["run"], "the guard reads its env, never an inline expression"
    payload = dispatch["with"]["client-payload"]
    assert "steps.meta.outputs.release_version" in payload
    assert "steps.meta.outputs.release_tag" in payload


def test_publish_release_stops_before_building_or_publishing_without_the_token():
    """A missing credential must publish nothing: the guard runs before every other step."""
    _, names, _ = _guard_step()
    guard = names.index("Require KERNEL_FORGE_TOKEN for the Downstream Dispatch")
    for later in ("Package Signed Deb and UKI Binaries", "Publish to GitHub Release"):
        assert guard < names.index(later), f"the guard must run before {later!r}"


def test_publish_release_secret_is_read_only_by_the_dispatch_step():
    """ADR-0005: the token value reaches the dispatch step alone; the guard sees a boolean."""
    steps, names, _ = _guard_step()
    dispatch = names.index("Dispatch Downstream Notification to imago")
    text = (WORKFLOWS_DIR / "publish-release.yml").read_text(encoding="utf-8")
    plain = re.findall(r"secrets\.KERNEL_FORGE_TOKEN(?! != '')", text)
    assert len(plain) == 1 and "secrets.KERNEL_FORGE_TOKEN" in str(steps[dispatch])
    assert "secrets.GITHUB_TOKEN" not in text, "there is no fallback to GITHUB_TOKEN"


def test_publish_release_guard_refuses_without_the_token():
    """The guard's run block exits 1 with an annotation naming the secret when it is not set."""
    for unset in ("false", ""):
        result = _run_guard(unset)
        assert result.returncode == 1, (unset, result.stdout, result.stderr)
        assert result.stderr.startswith("::error title=Missing secret KERNEL_FORGE_TOKEN::")
        assert "GITHUB_TOKEN cannot reach it" in result.stderr
        assert result.stdout == ""


def test_publish_release_guard_passes_with_the_token():
    """With the secret set the guard is silent and exits 0."""
    result = _run_guard("true")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


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
