"""supply-chain.yml attests only packages it is given, and fails when it is given none.

The job builds nothing. It used to ``touch`` two empty ``.deb`` files when ``output/`` was
empty, generate SBOMs for them, and sign the checksums with ``|| true`` on both the
checksum and the signing command, so a run with nothing to attest reported success.
These tests run the step's own ``run`` block against stub ``syft`` and ``cosign``.
"""

import os
import shutil
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "supply-chain.yml"
STEP_NAME = "Generate SBOM and Sign Release Artifacts"

SYFT_STUB = """#!/usr/bin/env bash
set -euo pipefail
echo "syft $*" >> "${STUB_LOG}"
for arg in "$@"; do
  case "${arg}" in
    *=*) printf '{"stub": "%s"}\\n' "${arg%%=*}" > "${arg#*=}" ;;
  esac
done
"""

COSIGN_STUB = """#!/usr/bin/env bash
set -euo pipefail
echo "cosign $*" >> "${STUB_LOG}"
[[ "${COSIGN_FAIL:-0}" == "0" ]] || exit 1
bundle=""
while [[ $# -gt 0 ]]; do
  case "$1" in --bundle) bundle="$2"; shift 2 ;; *) shift ;; esac
done
echo '{}' > "${bundle}"
"""


def _step() -> dict:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["provenance"]["steps"]
    return next(step for step in steps if step.get("name") == STEP_NAME)


def _run(tmp_path: Path, debs: dict[str, bytes] | None, cosign_fail: bool = False):
    """Run the step in a fresh workspace; ``debs=None`` means no output directory at all."""
    workspace = tmp_path / "workspace"
    (workspace / "scripts").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "publish_release.sh", workspace / "scripts")
    if debs is not None:
        (workspace / "output").mkdir()
        for name, data in debs.items():
            (workspace / "output" / name).write_bytes(data)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("syft", SYFT_STUB), ("cosign", COSIGN_STUB)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "stub.log"
    log.touch()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "COSIGN_FAIL": "1" if cosign_fail else "0",
        "GITHUB_REF_NAME": "v7.2.8-mainstream-lusoris1",
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", _step()["run"]],
        cwd=workspace,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result, workspace / "output", log.read_text(encoding="utf-8")


def test_the_step_neither_fabricates_nor_swallows_errors():
    run = _step()["run"]
    assert "touch" not in run, "the job must never create a package it did not build"
    assert "|| true" not in run, "a failed checksum or signature must fail the job"


def test_an_empty_output_directory_fails_before_anything_is_attested(tmp_path):
    result, output, calls = _run(tmp_path, debs={})
    assert result.returncode != 0, result.stdout
    assert "no Debian package" in result.stderr
    assert calls == "", "nothing is SBOMed or signed"
    assert sorted(p.name for p in output.iterdir()) == []


def test_a_missing_output_directory_fails(tmp_path):
    result, output, calls = _run(tmp_path, debs=None)
    assert result.returncode != 0, result.stdout
    assert calls == ""
    assert not output.exists()


def test_a_zero_byte_package_is_never_signed(tmp_path):
    result, output, calls = _run(tmp_path, debs={"linux-image-7.2.8.deb": b""})
    assert result.returncode != 0, result.stdout
    assert "cosign" not in calls
    assert not (output / "SHA256SUMS").exists()


def test_a_failed_signature_fails_the_job(tmp_path):
    result, _, calls = _run(
        tmp_path, debs={"linux-image-7.2.8.deb": b"!<arch>\n"}, cosign_fail=True
    )
    assert result.returncode != 0, result.stdout
    assert "cosign sign-blob" in calls


def test_given_packages_are_sbomed_checksummed_and_signed(tmp_path):
    result, output, calls = _run(tmp_path, debs={"linux-image-7.2.8.deb": b"!<arch>\n"})
    assert result.returncode == 0, result.stderr
    sums = (output / "SHA256SUMS").read_text(encoding="utf-8").split()
    assert {"linux-image-7.2.8.deb", "kernel-sbom.cdx.json", "kernel-sbom.spdx.json"} <= set(sums)
    assert "cosign sign-blob --yes --bundle output/SHA256SUMS.bundle output/SHA256SUMS" in calls
    assert (output / "SHA256SUMS.bundle").exists()
