# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for scripts/stage_release_uki.sh."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "stage_release_uki.sh"
CHECKER = REPO_ROOT / "scripts" / "check_uki.py"
REAL_KERNEL = os.environ.get("NUCLEUS_UKI_TEST_VMLINUZ", "")


def _run(*args: str) -> subprocess.CompletedProcess:
    cmd = ["bash", str(SCRIPT), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_refuse_missing_vmlinuz(tmp_path):
    out = tmp_path / "out"
    result = _run(
        "--stream=mainstream",
        "--version=7.2.10-lusoris1",
        f"--vmlinuz={tmp_path / 'nonexistent-vmlinuz'}",
        f"--output-dir={out}",
    )
    assert result.returncode == 1
    assert "not a regular file" in result.stderr
    assert not list(out.glob("*.efi"))


def test_refuse_existing_target(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    target = out / "linux-mainstream-7.2.10-lusoris1-uki.efi"
    target.write_bytes(b"pre-existing UKI")

    fake_vmlinuz = tmp_path / "vmlinuz"
    fake_vmlinuz.write_bytes(b"kernel dummy")

    result = _run(
        "--stream=mainstream",
        "--version=7.2.10-lusoris1",
        f"--vmlinuz={fake_vmlinuz}",
        f"--output-dir={out}",
    )
    assert result.returncode == 1
    assert "target UKI already exists" in result.stderr
    assert target.read_bytes() == b"pre-existing UKI"


def test_refuse_missing_arguments(tmp_path):
    result = _run("--stream=mainstream")
    assert result.returncode == 1


def test_real_ukify_stage_release_uki(tmp_path):
    if shutil.which("ukify") is None or not REAL_KERNEL or not Path(REAL_KERNEL).is_file():
        pytest.skip("ukify or NUCLEUS_UKI_TEST_VMLINUZ not available")

    out = tmp_path / "out"
    stream = "mainstream"
    version = "7.2.10-lusoris1"
    target = out / f"linux-{stream}-{version}-uki.efi"

    result = _run(
        f"--stream={stream}",
        f"--version={version}",
        f"--vmlinuz={REAL_KERNEL}",
        f"--output-dir={out}",
    )
    assert result.returncode == 0, result.stderr
    assert target.is_file() and target.stat().st_size > 0

    # Ensure unwanted metadata was not copied
    assert not (out / f"{target.name}.sha256").exists()
    assert not (out / "BOOTX64.EFI.sha256").exists()
    assert not (out / "pcr11-measurements.json").exists()

    # Verify check_uki accepts the staged UKI
    check_result = subprocess.run(
        ["python3", str(CHECKER), "--arch=x86_64", "--expect-initrd", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_result.returncode == 0, check_result.stderr
