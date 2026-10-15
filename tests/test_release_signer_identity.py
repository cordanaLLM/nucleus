"""Test suite for release signer identity resolution, dual verification, and workflow isolation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "release_signer_identity.py"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify-release.sh"
RELEASE_SIGNERS_JSON = REPO_ROOT / "release-signers.json"
RELEASE_BUILD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-build.yml"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-release.yml"

REPO = "cordanaLLM/nucleus"
LEGACY_TAG = "v7.2.10-mainstream-lusoris1"
NEW_TAG = "v7.2.10-mainstream-lusoris4"

COSIGN_STUB = """#!/usr/bin/env bash
set -euo pipefail
echo "cosign $*" >> "${STUB_LOG}"
if [[ -n "${EXPECTED_IDENTITY:-}" ]]; then
  for arg in "$@"; do
    if [[ "${arg}" == *".github/workflows/"* ]]; then
      if [[ "${arg}" != "${EXPECTED_IDENTITY}" ]]; then
        echo "identity mismatch: expected ${EXPECTED_IDENTITY}, got ${arg}" >&2
        exit 1
      fi
    fi
  done
fi
[[ "${COSIGN_FAIL:-0}" == "0" ]]
"""


def test_legacy_tag_resolves_to_legacy_workflow():
    """All 20 legacy tags must resolve to publish-release.yml."""
    data = json.loads(RELEASE_SIGNERS_JSON.read_text(encoding="utf-8"))
    assert len(data["legacy_tags"]) == 20, (
        f"Expected exactly 20 legacy tags, found {len(data['legacy_tags'])}"
    )
    for tag in data["legacy_tags"]:
        res = subprocess.run(
            ["python3", str(SCRIPT), "--repo", REPO, "--tag", tag],
            capture_output=True,
            text=True,
            check=True,
        )
        expected = (
            f"https://github.com/{REPO}/.github/workflows/publish-release.yml@refs/tags/{tag}"
        )
        assert res.stdout.strip() == expected


def test_new_tag_resolves_to_new_workflow():
    """Tags not in legacy_tags resolve to release-build.yml."""
    res = subprocess.run(
        ["python3", str(SCRIPT), "--repo", REPO, "--tag", NEW_TAG],
        capture_output=True,
        text=True,
        check=True,
    )
    expected = f"https://github.com/{REPO}/.github/workflows/release-build.yml@refs/tags/{NEW_TAG}"
    assert res.stdout.strip() == expected


@pytest.mark.parametrize(
    "invalid_tag",
    [
        "invalid-tag",
        "v1.0.0",
        "v7.2.10-mainstream",
        "v7.2.10-mainstream-lusoris0",
        "7.2.10-mainstream-lusoris1",
        "v7.2.10-mainstream-lusoris10000",
    ],
)
def test_malformed_tag_refused(invalid_tag: str):
    """Tags not conforming to kernel release grammar are refused with exit != 0."""
    res = subprocess.run(
        ["python3", str(SCRIPT), "--repo", REPO, "--tag", invalid_tag],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "malformed kernel release tag" in res.stderr


@pytest.mark.parametrize(
    "invalid_repo",
    [
        "invalid",
        "owner/repo/extra",
        "/repo",
        "owner/",
    ],
)
def test_malformed_repo_refused(invalid_repo: str):
    """Repositories not matching owner/repo pattern are refused with exit != 0."""
    res = subprocess.run(
        ["python3", str(SCRIPT), "--repo", invalid_repo, "--tag", NEW_TAG],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "malformed repository identifier" in res.stderr


def test_duplicate_json_key_refused(tmp_path: Path):
    """Duplicate JSON keys in release-signers.json must cause immediate refusal."""
    bad_json = tmp_path / "bad-signers.json"
    bad_json.write_text(
        """{
  "schema": "nucleus.release-signers.v1",
  "legacy_workflow": "publish-release.yml",
  "legacy_workflow": "duplicate.yml",
  "workflow": "release-build.yml",
  "legacy_tags": []
}""",
        encoding="utf-8",
    )
    res = subprocess.run(
        ["python3", str(SCRIPT), "--repo", REPO, "--tag", NEW_TAG, "--data", str(bad_json)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "duplicate key in JSON" in res.stderr


def test_unknown_json_key_refused(tmp_path: Path):
    """Unknown JSON keys in release-signers.json must be refused."""
    bad_json = tmp_path / "bad-signers.json"
    bad_json.write_text(
        """{
  "schema": "nucleus.release-signers.v1",
  "legacy_workflow": "publish-release.yml",
  "workflow": "release-build.yml",
  "legacy_tags": [],
  "unknown_field": true
}""",
        encoding="utf-8",
    )
    res = subprocess.run(
        ["python3", str(SCRIPT), "--repo", REPO, "--tag", NEW_TAG, "--data", str(bad_json)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "unknown keys in release signers config" in res.stderr


def _write_fixture_release(directory: Path, tag: str, signer_identity: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    assets = {
        "linux-image-7.2.10-lusoris1-mainstream_7.2.10-lusoris1_amd64.deb": b"image",
        "vmlinuz-7.2.10-lusoris1-mainstream": b"kernel",
    }
    lines = []
    for name, data in assets.items():
        (directory / name).write_bytes(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
    sums = "".join(lines).encode()
    (directory / "SHA256SUMS").write_bytes(sums)
    (directory / "SHA256SUMS.bundle").write_text("{}\n", encoding="utf-8")
    manifest = {
        "checksums": {"file": "SHA256SUMS", "sha256": hashlib.sha256(sums).hexdigest()},
        "provenance": {
            "tag": tag,
            "signer_identity": signer_identity,
            "bundle": "SHA256SUMS.bundle",
        },
    }
    (directory / "kernel-mainstream.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def test_verify_release_rejects_legacy_tag_with_new_identity(tmp_path: Path):
    """verify-release.sh must reject a legacy tag whose manifest carries the new identity."""
    release_dir = tmp_path / "release"
    new_identity = (
        f"https://github.com/{REPO}/.github/workflows/release-build.yml@refs/tags/{LEGACY_TAG}"
    )
    _write_fixture_release(release_dir, tag=LEGACY_TAG, signer_identity=new_identity)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "cosign").write_text(COSIGN_STUB, encoding="utf-8")
    (bin_dir / "cosign").chmod(0o755)
    log = tmp_path / "stub.log"
    log.touch()
    expected_legacy_identity = (
        f"https://github.com/{REPO}/.github/workflows/publish-release.yml@refs/tags/{LEGACY_TAG}"
    )
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "EXPECTED_IDENTITY": expected_legacy_identity,
    }

    res = subprocess.run(
        ["bash", str(VERIFY_SCRIPT), str(release_dir), LEGACY_TAG, REPO],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 1
    assert "manifest provenance.signer_identity is not" in res.stderr


def test_verify_release_rejects_new_tag_with_legacy_identity(tmp_path: Path):
    """verify-release.sh must reject a new tag whose manifest carries the legacy identity."""
    release_dir = tmp_path / "release"
    legacy_identity = (
        f"https://github.com/{REPO}/.github/workflows/publish-release.yml@refs/tags/{NEW_TAG}"
    )
    _write_fixture_release(release_dir, tag=NEW_TAG, signer_identity=legacy_identity)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "cosign").write_text(COSIGN_STUB, encoding="utf-8")
    (bin_dir / "cosign").chmod(0o755)
    log = tmp_path / "stub.log"
    log.touch()
    expected_new_identity = (
        f"https://github.com/{REPO}/.github/workflows/release-build.yml@refs/tags/{NEW_TAG}"
    )
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "EXPECTED_IDENTITY": expected_new_identity,
    }

    res = subprocess.run(
        ["bash", str(VERIFY_SCRIPT), str(release_dir), NEW_TAG, REPO],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 1
    assert "manifest provenance.signer_identity is not" in res.stderr


def test_release_build_workflow_attestation_and_caller_permissions():
    """Ensure release-build.yml has attest-build-provenance in the build job, and publish job has no id-token."""
    release_build = yaml.safe_load(RELEASE_BUILD_WORKFLOW.read_text(encoding="utf-8"))
    publish_parsed = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))

    # release-build.yml build job
    build_job = release_build["jobs"]["build"]
    assert build_job["permissions"]["attestations"] == "write"
    assert build_job["permissions"]["id-token"] == "write"

    steps = build_job["steps"]
    step_names = [s.get("name") for s in steps]
    assert "Build and Boot the Tagged Stream" in step_names
    assert "Attest Build Provenance" in step_names

    attest_step = next(s for s in steps if s.get("name") == "Attest Build Provenance")
    assert "actions/attest-build-provenance" in attest_step["uses"]
    assert "output/*.deb" in attest_step["with"]["subject-path"]

    # publish-release.yml publish job
    publish_job = publish_parsed["jobs"]["publish"]
    assert publish_job["permissions"] == {"contents": "write"}
    assert "id-token" not in publish_job["permissions"]
    assert "attestations" not in publish_job["permissions"]
