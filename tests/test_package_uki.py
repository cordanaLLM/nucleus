"""Behaviour of scripts/package-uki.sh: simulate only on request, otherwise refuse to fabricate.

A UKI that is not a UKI is checksummed and covered by the release cosign signature, so it
reaches a consumer as a signed, verified artifact. These tests pin the refusal paths from
issue #21, including the one where a failing ukify used to be checksummed anyway.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "package-uki.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to run package-uki.sh")


def _path_without_ukify() -> str:
    """PATH with every directory that provides a ukify removed, so absence is deterministic."""
    entries = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(e for e in entries if e and not (Path(e) / "ukify").exists())


def _fake_ukify(bin_dir: Path, body: str) -> None:
    """Install a stand-in ukify whose behaviour is the given shell body."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "ukify"
    stub.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n", encoding="utf-8", newline="\n")
    stub.chmod(0o755)


def _run(tmp_path: Path, *args: str, path: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = path if path is not None else _path_without_ukify()
    env["TMPDIR"] = str(tmp_path / "tmp")
    (tmp_path / "tmp").mkdir(exist_ok=True)
    cmd = [BASH, str(SCRIPT), "--stream=mainstream", "--arch=x86_64", f"--output-dir={tmp_path / 'out'}", *args]
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False)


def _target(tmp_path: Path) -> Path:
    return tmp_path / "out" / "mainstream-x86_64"


def test_dry_run_simulates_and_says_so(tmp_path):
    result = _run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    target = _target(tmp_path)
    assert (target / "BOOTX64.EFI").is_file()
    measurement = json.loads((target / "pcr11-measurements.json").read_text(encoding="utf-8"))
    assert measurement["simulated"] is True, "a simulated measurement must be marked as one"


def test_production_without_a_kernel_refuses_and_writes_nothing(tmp_path):
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "no kernel image to package" in result.stderr
    target = _target(tmp_path)
    assert not (target / "BOOTX64.EFI").exists(), "a missing kernel must not fall through to simulation"
    assert not (target / "pcr11-measurements.json").exists()


def test_production_without_ukify_refuses_and_writes_nothing(tmp_path):
    vmlinuz = tmp_path / "vmlinuz"
    vmlinuz.write_bytes(b"MZ\x00not-really-a-kernel")
    result = _run(tmp_path, f"--vmlinuz={vmlinuz}")
    assert result.returncode != 0
    assert "ukify is not installed" in result.stderr
    target = _target(tmp_path)
    assert not (target / "BOOTX64.EFI").exists(), "the kernel must not be copied to a .efi name"
    assert not (target / "BOOTX64.EFI.sha256").exists()


def test_failing_ukify_leaves_no_partial_image_and_no_checksum(tmp_path):
    vmlinuz = tmp_path / "vmlinuz"
    vmlinuz.write_bytes(b"kernel")
    bin_dir = tmp_path / "bin"
    # Writes partial output, then fails: the case errexit used to miss inside `fn || status=$?`.
    _fake_ukify(bin_dir, 'for a in "$@"; do case "$a" in --output=*) printf partial > "${a#*=}";; esac; done; exit 1')
    result = _run(tmp_path, f"--vmlinuz={vmlinuz}", path=str(bin_dir) + os.pathsep + _path_without_ukify())
    assert result.returncode != 0
    target = _target(tmp_path)
    assert not (target / "BOOTX64.EFI").exists(), "partial ukify output must be removed"
    assert not (target / "BOOTX64.EFI.sha256").exists(), "a failed build must never be checksummed"


def test_ukify_reporting_success_with_empty_output_is_refused(tmp_path):
    vmlinuz = tmp_path / "vmlinuz"
    vmlinuz.write_bytes(b"kernel")
    bin_dir = tmp_path / "bin"
    _fake_ukify(bin_dir, 'for a in "$@"; do case "$a" in --output=*) : > "${a#*=}";; esac; done; exit 0')
    result = _run(tmp_path, f"--vmlinuz={vmlinuz}", path=str(bin_dir) + os.pathsep + _path_without_ukify())
    assert result.returncode != 0
    assert "produced no output" in result.stderr
    assert not (_target(tmp_path) / "BOOTX64.EFI.sha256").exists()


def test_successful_ukify_output_is_checksummed(tmp_path):
    vmlinuz = tmp_path / "vmlinuz"
    vmlinuz.write_bytes(b"kernel")
    bin_dir = tmp_path / "bin"
    _fake_ukify(bin_dir, 'for a in "$@"; do case "$a" in --output=*) printf "MZ-uki" > "${a#*=}";; esac; done; exit 0')
    result = _run(tmp_path, f"--vmlinuz={vmlinuz}", path=str(bin_dir) + os.pathsep + _path_without_ukify())
    assert result.returncode == 0, result.stderr
    target = _target(tmp_path)
    assert (target / "BOOTX64.EFI").read_bytes() == b"MZ-uki"
    assert (target / "BOOTX64.EFI.sha256").is_file()
