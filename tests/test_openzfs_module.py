"""Test suite for OpenZFS out-of-tree module compilation and CI/CD workflow."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-openzfs-module.sh"
WORKFLOW_FILE = REPO_ROOT / ".github" / "workflows" / "openzfs-module.yml"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-release.yml"
OPENZFS = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))["modules"][
    "openzfs"
]
RELEASE_BASE = "https://github.com/openzfs/zfs/releases/download"

needs_dpkg = pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not installed")


def _make_fake_headers_deb(out: Path, kr: str, version: str = "1.0.0") -> Path:
    """Build a minimal valid linux-headers deb package with dpkg-deb."""
    pkg_dir = out / f"pkg-linux-headers-{kr}"
    debian = pkg_dir / "DEBIAN"
    debian.mkdir(parents=True, exist_ok=True)
    control = (
        f"Package: linux-headers-{kr}\n"
        f"Version: {version}\n"
        "Architecture: amd64\n"
        "Maintainer: test <test@example.com>\n"
        "Description: test kernel headers package\n"
    )
    (debian / "control").write_text(control, encoding="utf-8")
    deb_path = out / f"linux-headers-{kr}_{version}_amd64.deb"
    subprocess.run(
        [
            "dpkg-deb",
            "-Zgzip",
            "--root-owner-group",
            "--build",
            str(pkg_dir),
            str(deb_path),
        ],
        check=True,
        capture_output=True,
    )
    return deb_path


def test_manifest_openzfs_entry_matches_schema():
    """Ensure versions.json modules.openzfs matches versions.schema.json."""
    manifest = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
    schema = json.loads((REPO_ROOT / "versions.schema.json").read_text(encoding="utf-8"))

    jsonschema.validate(instance=manifest, schema=schema)

    assert "modules" in manifest, "versions.json must declare 'modules'"
    assert "openzfs" in manifest["modules"], "modules must declare 'openzfs'"

    zfs = manifest["modules"]["openzfs"]
    version = zfs["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"OpenZFS version {version!r} is not x.y.z"
    for bound in ("linux_minimum", "linux_maximum"):
        assert re.fullmatch(r"\d+\.\d+", zfs[bound]), f"{bound} {zfs[bound]!r} is not x.y"

    source = zfs["source"]
    assert source["kind"] == "tarball"
    assert source["url"] == f"{RELEASE_BASE}/zfs-{version}/zfs-{version}.tar.gz"
    assert source["signature_url"] == f"{source['url']}.asc"
    assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"])

    signers = source["signers"]
    expected_signer = "4F3BA9AB6D1F8D683DC2DFB56AD860EED4598027"
    assert expected_signer in signers, f"OpenZFS signer {expected_signer} missing"
    assert (REPO_ROOT / "keys" / f"{expected_signer}.asc").is_file(), (
        f"Public key for {expected_signer} missing in keys/"
    )


def test_openzfs_workflow_shape():
    """Ensure openzfs-module.yml conforms to repository contracts and shapes."""
    assert WORKFLOW_FILE.is_file(), f"{WORKFLOW_FILE.name} must exist"
    parsed = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))

    # Permissions
    assert parsed.get("permissions") == {"contents": "read"}

    # Triggers: pull_request runs live in build-matrix.yml against same-SHA headers
    on_triggers = parsed.get("on", parsed.get(True, {}))
    assert "workflow_dispatch" in on_triggers
    assert "schedule" in on_triggers
    assert "pull_request" not in on_triggers
    # A merged stream bump has no release until publish-release.yml publishes it, so the
    # release path runs after that workflow succeeds, never on the push that merged the bump.
    assert "push" not in on_triggers

    plan_step = next(s for s in parsed["jobs"]["plan"]["steps"] if s.get("id") == "plan")
    assert "python3 scripts/module_stream_matrix.py --module=openzfs" in plan_step["run"]
    assert '--release-tag="${PUBLISHED_TAG}"' in plan_step["run"]
    assert plan_step["env"]["PUBLISHED_TAG"] == "${{ github.event.workflow_run.head_branch || '' }}"

    # Container image digest equals release-build.yml
    release_build_workflow = REPO_ROOT / ".github" / "workflows" / "release-build.yml"
    build_parsed = yaml.safe_load(release_build_workflow.read_text(encoding="utf-8"))
    build_step = next(
        s
        for s in build_parsed["jobs"]["build"]["steps"]
        if s.get("name") == "Build and Boot the Tagged Stream"
    )
    expected_image = build_step["env"]["BUILD_IMAGE"]

    build_job = parsed["jobs"]["build"]
    actual_image = build_job["container"]["image"]
    assert actual_image == expected_image, (
        f"Image digest mismatch: {actual_image} != {expected_image}"
    )

    # Matrix is dynamically computed from versions.json, not hardcoded
    assert build_job["strategy"]["matrix"] == "${{ fromJSON(needs.plan.outputs.matrix) }}"
    build_job_text = yaml.safe_dump(build_job)
    for stream in ("lts", "mainstream", "realtime", "bleeding"):
        assert f"stream: {stream}" not in build_job_text
        assert f"- {stream}" not in build_job_text


def test_openzfs_workflow_runs_after_a_successful_publish_release():
    """The release path runs on the published tag's commit and stream, only after success."""
    parsed = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))
    on_triggers = parsed.get("on", parsed.get(True, {}))
    published = "${{ github.event.workflow_run.head_branch || '' }}"
    assert on_triggers["workflow_run"] == {
        "workflows": ["publish-release"],
        "types": ["completed"],
    }
    plan_job = parsed["jobs"]["plan"]
    assert plan_job["if"] == (
        "github.event_name != 'workflow_run' || (github.event.workflow_run.conclusion == 'success'"
        " && github.event.workflow_run.head_repository.full_name == github.repository)"
    )
    assert parsed["jobs"]["build"]["if"] == (
        "needs.plan.result == 'success' && fromJSON(needs.plan.outputs.matrix).stream[0] != null"
        " && (github.event_name != 'workflow_run'"
        " || github.event.workflow_run.head_repository.full_name == github.repository)"
    )
    for job in ("plan", "build"):
        checkout = next(
            s for s in parsed["jobs"][job]["steps"] if s.get("name") == "Checkout Repository"
        )
        assert checkout["with"]["ref"] == "${{ github.event.workflow_run.head_sha || github.sha }}"
    build_step = next(
        s
        for s in parsed["jobs"]["build"]["steps"]
        if s.get("name") == "Download Release Assets and Build Module"
    )
    assert build_step["env"]["PUBLISHED_TAG"] == published
    assert "matching = [m for m in matching if m[1] == published]" in build_step["run"]


def test_openzfs_workflow_verifies_the_release_with_cosign():
    """The push and schedule path proves the published release's SHA256SUMS with cosign."""
    parsed = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))
    steps = parsed["jobs"]["build"]["steps"]
    build_mod_step = next(
        s for s in steps if s.get("name") == "Download Release Assets and Build Module"
    )
    run_script = build_mod_step["run"]
    assert "cosign verify-blob" in run_script
    assert "scripts/release_signer_identity.py" in run_script
    assert '--certificate-oidc-issuer "https://token.actions.githubusercontent.com"' in run_script


@needs_dpkg
def test_check_openzfs_module_print_plan(tmp_path: Path):
    """Ensure --print-plan states url, sha, KR, range and exits 0."""
    kr = "7.2.8-lusoris1-mainstream"
    deb = _make_fake_headers_deb(tmp_path, kr)

    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            "--print-plan",
            f"--versions={REPO_ROOT / 'versions.json'}",
            f"--keys={REPO_ROOT / 'keys'}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"--print-plan failed:\n{res.stdout}\n{res.stderr}"
    assert f"url: {OPENZFS['source']['url']}" in res.stdout
    assert f"sha256: {OPENZFS['source']['sha256']}" in res.stdout
    assert f"kernelrelease: {kr}" in res.stdout
    assert f"range: [{OPENZFS['linux_minimum']}, {OPENZFS['linux_maximum']}]" in res.stdout


@needs_dpkg
def test_check_openzfs_module_refuses_out_of_range(tmp_path: Path):
    """Ensure a kernel release above linux_maximum is refused, naming both bounds."""
    major, minor = OPENZFS["linux_maximum"].split(".")
    above = f"{major}.{int(minor) + 1}"
    kr_out = f"{above}.0-rc5-lusoris1-bleeding"
    deb = _make_fake_headers_deb(tmp_path, kr_out)

    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            "--print-plan",
            f"--versions={REPO_ROOT / 'versions.json'}",
            f"--keys={REPO_ROOT / 'keys'}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0, "Expected out-of-range kernel to be refused"
    assert above in res.stderr
    assert OPENZFS["linux_minimum"] in res.stderr, "Error message must name linux_minimum"
    assert OPENZFS["linux_maximum"] in res.stderr, "Error message must name linux_maximum"


@needs_dpkg
def test_check_openzfs_module_refuses_tampered_sha256(tmp_path: Path):
    """Ensure tampered tarball download fails sha256 check and exits non-zero."""
    kr = "7.2.8-lusoris1-mainstream"
    deb = _make_fake_headers_deb(tmp_path, kr)
    work = tmp_path / "work"
    work.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_stub = bin_dir / "curl"
    # A stub curl that writes corrupt data instead of authentic upstream release
    curl_script = """#!/usr/bin/env bash
set -euo pipefail
out=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) out="$2"; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [[ -n "${out}" ]]; then
  echo "tampered-content" > "${out}"
fi
exit 0
"""
    curl_stub.write_text(curl_script, encoding="utf-8")
    curl_stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            f"--work={work}",
            f"--versions={REPO_ROOT / 'versions.json'}",
            f"--keys={REPO_ROOT / 'keys'}",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0, "Tampered sha256 must exit non-zero"
    assert "sha256" in res.stderr.lower(), f"Expected sha256 refusal in stderr:\n{res.stderr}"


@needs_dpkg
def test_check_openzfs_module_resolves_a_relative_work_directory(tmp_path: Path):
    """A relative --work, as the workflow passes it, is made absolute before ./configure runs.

    ./configure runs inside WORK/src, so a relative headers path given to --with-linux would
    name a directory that does not exist there.
    """
    deb = _make_fake_headers_deb(tmp_path, "7.2.8-lusoris1-mainstream")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_stub = bin_dir / "curl"
    curl_stub.write_text("#!/usr/bin/env bash\nexit 22\n", encoding="utf-8")
    curl_stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            "--work=zfs-work-mainstream",
            f"--versions={REPO_ROOT / 'versions.json'}",
            f"--keys={REPO_ROOT / 'keys'}",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert f"==> Work directory {tmp_path / 'zfs-work-mainstream'}" in res.stdout, (
        res.stdout + res.stderr
    )


def test_openzfs_workflow_names_no_repository_and_bounds_every_job():
    """Ensure a fork runs the workflow against its own releases, and no job or download can hang."""
    text = WORKFLOW_FILE.read_text(encoding="utf-8")
    assert "cordanaLLM/nucleus" not in text, "the release lookup must use GITHUB_REPOSITORY"
    assert 'os.environ["GITHUB_REPOSITORY"]' in text
    parsed = yaml.safe_load(text)
    for name, job in parsed["jobs"].items():
        assert isinstance(job.get("timeout-minutes"), int), f"job {name} has no timeout-minutes"
    assert "urlopen(req, timeout=" in text
    assert "urlopen(down_req, timeout=" in text


def test_build_matrix_runs_openzfs_module_against_same_sha_headers():
    """Ensure build-matrix.yml runs OpenZFS module check against the build job's headers artifact."""
    bm_path = REPO_ROOT / ".github" / "workflows" / "build-matrix.yml"
    bm = yaml.safe_load(bm_path.read_text(encoding="utf-8"))
    plan_job = bm["jobs"]["plan-modules"]
    assert plan_job["permissions"] == {"contents": "read"}
    assert plan_job["steps"][0]["name"] == "Stop on a draft pull request"
    zfs_plan = next(s for s in plan_job["steps"] if s.get("id") == "openzfs")
    assert "python3 scripts/module_stream_matrix.py --module=openzfs" in zfs_plan["run"]

    job = bm["jobs"]["openzfs-module"]
    assert job["name"] == "Build OpenZFS Module (${{ matrix.stream }})"
    assert sorted(job["needs"]) == ["build", "plan-modules"]
    assert job["permissions"] == {"contents": "read"}
    assert job["container"]["image"] == bm["jobs"]["build"]["container"]["image"]
    assert job["strategy"]["matrix"] == "${{ fromJSON(needs.plan-modules.outputs.openzfs) }}"
    assert job["env"]["DRY_RUN"] == "${{ github.event.inputs.dry_run == 'true' }}"
    assert job["steps"][0]["name"] == "Stop on a draft pull request"

    steps = {s.get("name", ""): s for s in job["steps"]}
    for step_name in (
        "Install Toolchain",
        "Download Kernel Build Artifact",
        "Build OpenZFS Module",
    ):
        assert steps[step_name]["if"] == "env.DRY_RUN != 'true'"
    dl = steps["Download Kernel Build Artifact"]
    assert re.fullmatch(r"actions/download-artifact@[0-9a-f]{40}", dl["uses"])
    other_dls = [
        s["uses"]
        for j_name, j in bm.get("jobs", {}).items()
        if j_name != "openzfs-module" and isinstance(j, dict)
        for s in j.get("steps", [])
        if isinstance(s, dict) and s.get("uses", "").startswith("actions/download-artifact@")
    ]
    assert other_dls and all(u == dl["uses"] for u in other_dls)
    assert dl["with"]["name"] == "kernel-${{ matrix.stream }}-x86_64"
    build_run = steps["Build OpenZFS Module"]["run"]
    assert "api.github.com" not in build_run
    assert "./scripts/check-openzfs-module.sh" in build_run
    assert '--headers-deb="${HEADERS_DEB}"' in build_run
    assert '--work="zfs-work-${STREAM}"' in build_run
    assert "--jobs=4" in build_run


def test_module_stream_matrix_positive(tmp_path: Path):
    """Positive: module_stream_matrix.py selects in-range streams and writes summary + output."""
    script = REPO_ROOT / "scripts" / "module_stream_matrix.py"
    gh_out = tmp_path / "github_output"
    summary = tmp_path / "step_summary.md"
    res = subprocess.run(
        [
            "python3",
            str(script),
            "--module=openzfs",
            f"--versions={REPO_ROOT / 'versions.json'}",
            f"--github-output={gh_out}",
            f"--step-summary={summary}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    out_text = gh_out.read_text(encoding="utf-8").strip()
    assert out_text.startswith("matrix=")
    matrix = json.loads(out_text.split("=", 1)[1])
    assert matrix == {"stream": ["mainstream", "lts", "realtime"]}
    summary_text = summary.read_text(encoding="utf-8")
    assert "### OpenZFS" in summary_text
    assert "#### Supported Streams (Matrix)" in summary_text
    assert "- `mainstream`" in summary_text
    assert "#### Out of Range Streams (Skipped)" in summary_text
    assert "- `bleeding`" in summary_text


def test_module_stream_matrix_negative_missing_module_refused(tmp_path: Path):
    """Negative: missing module key is refused with non-zero exit status."""
    script = REPO_ROOT / "scripts" / "module_stream_matrix.py"
    res = subprocess.run(
        [
            "python3",
            str(script),
            "--module=nonexistent_module",
            f"--versions={REPO_ROOT / 'versions.json'}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "modules.nonexistent_module missing" in res.stderr


def test_module_stream_matrix_boundary_min_max_and_outside(tmp_path: Path):
    """Boundary: streams at linux_minimum and linux_maximum are included; just outside are skipped."""
    script = REPO_ROOT / "scripts" / "module_stream_matrix.py"
    manifest = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
    manifest["modules"]["openzfs"]["linux_minimum"] = "4.18"
    manifest["modules"]["openzfs"]["linux_maximum"] = "7.2"
    manifest["streams"] = {
        "at_min": {"version": "4.18.0"},
        "at_max": {"version": "7.2.99"},
        "below_min": {"version": "4.17.19"},
        "above_max": {"version": "7.3-rc1"},
    }
    manifest_file = tmp_path / "versions.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    gh_out = tmp_path / "github_output"
    summary = tmp_path / "step_summary.md"

    res = subprocess.run(
        [
            "python3",
            str(script),
            "--module=openzfs",
            f"--versions={manifest_file}",
            f"--github-output={gh_out}",
            f"--step-summary={summary}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    matrix = json.loads(gh_out.read_text(encoding="utf-8").strip().split("=", 1)[1])
    assert matrix == {"stream": ["at_min", "at_max"]}
    summary_text = summary.read_text(encoding="utf-8")
    assert "`below_min` (`4.17.19` is outside `[4.18, 7.2]`)" in summary_text
    assert "`above_max` (`7.3-rc1` is outside `[4.18, 7.2]`)" in summary_text


def test_module_stream_matrix_refuses_more_streams_than_its_bound(tmp_path: Path):
    """Boundary: a manifest above the stream bound is refused, never truncated."""
    script = REPO_ROOT / "scripts" / "module_stream_matrix.py"
    streams = {f"s{i}": {"version": "7.2.10"} for i in range(33)}
    manifest = {
        "streams": streams,
        "modules": {
            "openzfs": {"version": "2.4.2", "linux_minimum": "4.18", "linux_maximum": "7.2"}
        },
    }
    versions = tmp_path / "versions.json"
    versions.write_text(json.dumps(manifest), encoding="utf-8")
    res = subprocess.run(
        ["python3", str(script), "--module=openzfs", f"--versions={versions}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "more than 32" in res.stderr


def _matrix_for_release_tag(tmp_path: Path, module: str, tag: str) -> subprocess.CompletedProcess:
    """Run module_stream_matrix.py with --release-tag against the repository's versions.json."""
    return subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "scripts" / "module_stream_matrix.py"),
            f"--module={module}",
            f"--versions={REPO_ROOT / 'versions.json'}",
            f"--release-tag={tag}",
            f"--github-output={tmp_path / 'github_output'}",
            f"--step-summary={tmp_path / 'step_summary.md'}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _pinned_tag(stream: str) -> str:
    version = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))["streams"][
        stream
    ]["version"]
    return f"v{version.replace('~', '-')}-{stream}-lusoris1"


def test_module_stream_matrix_release_tag_keeps_only_its_stream(tmp_path: Path):
    """Positive: a published release tag of an in-range stream leaves that stream alone."""
    res = _matrix_for_release_tag(tmp_path, "openzfs", _pinned_tag("mainstream"))
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == 'matrix={"stream": ["mainstream"]}'
    assert "only `mainstream` is checked" in (tmp_path / "step_summary.md").read_text("utf-8")


def test_module_stream_matrix_release_tag_outside_the_module_builds_nothing(tmp_path: Path):
    """Boundary: a release of a stream outside the module's range yields an empty matrix."""
    res = _matrix_for_release_tag(tmp_path, "openzfs", _pinned_tag("bleeding"))
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == 'matrix={"stream": []}'
    summary = (tmp_path / "step_summary.md").read_text("utf-8")
    assert "`bleeding` is not a target of this module; nothing to build" in summary


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("v0.0.1-mainstream-lusoris1", "is not the mainstream version"),
        ("v7.2.11-nosuch-lusoris1", "names unknown stream 'nosuch'"),
        ("v7.2.11-mainstream-lusoris0", "is not v<version>-<stream>-lusoris<N>"),
        ("v7.2.11-mainstream-lusoris10000", "is not v<version>-<stream>-lusoris<N>"),
        ("refs/heads/main", "is not v<version>-<stream>-lusoris<N>"),
    ],
)
def test_module_stream_matrix_refuses_a_tag_that_is_not_a_pinned_release(
    tmp_path: Path, tag: str, message: str
):
    """Negative: a malformed tag, an unknown stream or another version is refused, not widened."""
    res = _matrix_for_release_tag(tmp_path, "openzfs", tag)
    assert res.returncode != 0
    assert message in res.stderr
    assert not (tmp_path / "github_output").exists()
