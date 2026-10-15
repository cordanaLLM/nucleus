"""supply-chain.yml verifies a published release and refuses every way it can be wrong.

The workflow used to re-sign packages it expected in ``output/``. It ran on the ``release``
event, which never fires for a release that publish-release.yml creates with GITHUB_TOKEN, and
on dispatch, where ``output/`` is always empty, so it never ran green. It now downloads the
release and runs ``scripts/verify-release.sh``. These tests run that script against fixture
assets and a stub ``cosign`` that records the identity it is asked to verify.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "supply-chain.yml"
SCRIPT = REPO_ROOT / "scripts" / "verify-release.sh"
REPO = "cordanaLLM/nucleus"
TAG = "v7.2.10-mainstream-lusoris1"
IDENTITY = f"https://github.com/{REPO}/.github/workflows/publish-release.yml@refs/tags/{TAG}"

COSIGN_STUB = """#!/usr/bin/env bash
set -euo pipefail
echo "cosign $*" >> "${STUB_LOG}"
[[ "${COSIGN_FAIL:-0}" == "0" ]]
"""


def _write_release(directory: Path) -> None:
    """A release as publish-release.yml writes it: assets, SHA256SUMS, bundle, manifest."""
    directory.mkdir()
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
        "provenance": {"tag": TAG, "signer_identity": IDENTITY, "bundle": "SHA256SUMS.bundle"},
    }
    (directory / "kernel-mainstream.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _run(tmp_path: Path, tag: str = TAG, cosign_fail: bool = False, mutate=None):
    release = tmp_path / "release"
    _write_release(release)
    if mutate is not None:
        mutate(release)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "cosign").write_text(COSIGN_STUB, encoding="utf-8")
    (bin_dir / "cosign").chmod(0o755)
    log = tmp_path / "stub.log"
    log.touch()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "COSIGN_FAIL": "1" if cosign_fail else "0",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), str(release), tag, REPO],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result, log.read_text(encoding="utf-8")


def test_a_correct_release_verifies_against_the_release_workflow_identity(tmp_path):
    result, log = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"--certificate-identity {IDENTITY} " in log
    assert "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in log


def _tamper(release: Path) -> None:
    (release / "vmlinuz-7.2.10-lusoris1-mainstream").write_bytes(b"other kernel")


def _extra(release: Path) -> None:
    (release / "unlisted.deb").write_bytes(b"stranger")


def _no_bundle(release: Path) -> None:
    (release / "SHA256SUMS.bundle").unlink()


def _no_manifest(release: Path) -> None:
    (release / "kernel-mainstream.manifest.json").unlink()


def _manifest_field(path: tuple[str, str], value: str):
    def mutate(release: Path) -> None:
        target = release / "kernel-mainstream.manifest.json"
        manifest = json.loads(target.read_text(encoding="utf-8"))
        manifest[path[0]][path[1]] = value
        target.write_text(json.dumps(manifest), encoding="utf-8")

    return mutate


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_tamper, "does not match SHA256SUMS"),
        (_extra, "not listed in SHA256SUMS: unlisted.deb"),
        (_no_bundle, "missing or empty asset: SHA256SUMS.bundle"),
        (_no_manifest, "missing or empty asset: kernel-mainstream.manifest.json"),
        (_manifest_field(("checksums", "sha256"), "0" * 64), "checksums.sha256"),
        (_manifest_field(("provenance", "tag"), "v7.2.8-mainstream-lusoris1"), "provenance.tag"),
        (
            _manifest_field(("provenance", "signer_identity"), "https://example.com/x"),
            "signer_identity",
        ),
    ],
)
def test_a_wrong_release_is_refused(tmp_path, mutate, message):
    result, _ = _run(tmp_path, mutate=mutate)
    assert result.returncode == 1
    assert message in result.stderr


def test_a_signature_cosign_refuses_is_refused(tmp_path):
    result, _ = _run(tmp_path, cosign_fail=True)
    assert result.returncode == 1
    assert "is not a signature of" in result.stderr


@pytest.mark.parametrize(
    "tag", ["nucleus-v0.3.0", "v7.2.10-mainstream", "v7.2.10-mainstream-lusoris0"]
)
def test_a_tag_that_is_not_a_kernel_release_is_refused(tmp_path, tag):
    result, log = _run(tmp_path, tag=tag)
    assert result.returncode == 1
    assert log == ""


def test_the_workflow_only_reads_and_runs_after_each_release():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow[True]  # PyYAML reads the key "on" as a boolean.
    assert triggers["workflow_run"] == {"workflows": ["publish-release"], "types": ["completed"]}
    assert triggers["workflow_dispatch"]["inputs"]["tag"]["required"] is True
    assert "release" not in triggers
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["verify"]
    assert "permissions" not in job
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "scripts/verify-release.sh" in runs
    assert "sign-blob" not in WORKFLOW.read_text(encoding="utf-8")
