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

    # Triggers
    on_triggers = parsed.get("on", parsed.get(True, {}))
    assert "workflow_dispatch" in on_triggers
    assert "schedule" in on_triggers
    assert "pull_request" in on_triggers
    assert "push" in on_triggers

    # Filtered paths
    expected_paths = [
        "versions.json",
        "versions.schema.json",
        "keys/**",
        "scripts/check-openzfs-module.sh",
        ".github/workflows/openzfs-module.yml",
    ]
    assert sorted(on_triggers["pull_request"]["paths"]) == sorted(expected_paths)
    assert sorted(on_triggers["push"]["paths"]) == sorted(expected_paths)
    assert on_triggers["push"]["branches"] == ["main"]

    # Container image digest equals publish-release.yml
    publish_parsed = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
    build_step = next(
        s
        for s in publish_parsed["jobs"]["publish"]["steps"]
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

    # Cosign identity and issuer
    steps = build_job["steps"]
    build_mod_step = next(
        s for s in steps if s.get("name") == "Download Release Assets and Build Module"
    )
    run_script = build_mod_step["run"]
    assert "cosign verify-blob" in run_script
    assert ".github/workflows/publish-release.yml@refs/tags/" in run_script
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
