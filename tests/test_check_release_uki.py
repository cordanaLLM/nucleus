# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for scripts/check_release_uki.py."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_release_uki.py"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(release_dir: Path, stream: str, version: str, *extra: str) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        str(SCRIPT),
        f"--dir={release_dir}",
        f"--stream={stream}",
        f"--version={version}",
        *extra,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_positive_check_release_uki(tmp_path):
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    stream = "mainstream"
    version = "7.2.10-lusoris1"
    uki_name = f"linux-{stream}-{version}-uki.efi"
    uki_content = b"valid uki binary content"
    (release_dir / uki_name).write_bytes(uki_content)
    digest = _sha(uki_content)

    sums = f"{digest}  {uki_name}\n"
    (release_dir / "SHA256SUMS").write_text(sums, encoding="utf-8")

    result = _run(release_dir, stream, version)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{digest}  {uki_name}"


def test_tilde_spelled_dot_version(tmp_path):
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    stream = "bleeding"
    version = "7.3~rc7-lusoris1"
    expected_name = "linux-bleeding-7.3.rc7-lusoris1-uki.efi"
    uki_content = b"rc uki content"
    (release_dir / expected_name).write_bytes(uki_content)
    digest = _sha(uki_content)

    sums = f"{digest}  {expected_name}\n"
    (release_dir / "SHA256SUMS").write_text(sums, encoding="utf-8")

    result = _run(release_dir, stream, version)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{digest}  {expected_name}"


def test_missing_uki_in_sums_or_on_disk(tmp_path):
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    stream = "mainstream"
    version = "7.2.10-lusoris1"
    uki_name = f"linux-{stream}-{version}-uki.efi"

    # Case 1: missing from SHA256SUMS
    (release_dir / "SHA256SUMS").write_text(
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef  other.deb\n",
        encoding="utf-8",
    )
    result = _run(release_dir, stream, version)
    assert result.returncode == 1
    assert "not listed" in result.stderr

    # Case 2: listed in SHA256SUMS but missing on disk
    digest = "a" * 64
    (release_dir / "SHA256SUMS").write_text(f"{digest}  {uki_name}\n", encoding="utf-8")
    result = _run(release_dir, stream, version)
    assert result.returncode == 1
    assert "missing" in result.stderr


def test_digest_mismatch(tmp_path):
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    stream = "mainstream"
    version = "7.2.10-lusoris1"
    uki_name = f"linux-{stream}-{version}-uki.efi"
    (release_dir / uki_name).write_bytes(b"content A")
    fake_digest = "b" * 64

    (release_dir / "SHA256SUMS").write_text(f"{fake_digest}  {uki_name}\n", encoding="utf-8")
    result = _run(release_dir, stream, version)
    assert result.returncode == 1
    assert "digest mismatch" in result.stderr


def test_two_ukis_refused(tmp_path):
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    stream = "mainstream"
    version = "7.2.10-lusoris1"
    uki_name = f"linux-{stream}-{version}-uki.efi"
    (release_dir / uki_name).write_bytes(b"content")
    digest = _sha(b"content")

    # Case 1: same UKI listed twice
    (release_dir / "SHA256SUMS").write_text(
        f"{digest}  {uki_name}\n{digest}  {uki_name}\n", encoding="utf-8"
    )
    result = _run(release_dir, stream, version)
    assert result.returncode == 1

    # Case 2: two different UKIs listed
    other_uki = "linux-bleeding-7.3.0-uki.efi"
    (release_dir / other_uki).write_bytes(b"other content")
    (release_dir / "SHA256SUMS").write_text(
        f"{digest}  {uki_name}\n{_sha(b'other content')}  {other_uki}\n", encoding="utf-8"
    )
    result = _run(release_dir, stream, version)
    assert result.returncode == 1
    assert "expected exactly one UKI" in result.stderr or "times" in result.stderr


@pytest.mark.parametrize(
    "cmd_args",
    [
        ["--stream=mainstream", "--version=7.2.10-lusoris1"],
        ["--dir=/nonexistent/path/xyz", "--stream=mainstream", "--version=7.2.10-lusoris1"],
        ["--dir=.", "--stream=", "--version=7.2.10-lusoris1"],
        ["--dir=.", "--stream=mainstream", "--version="],
    ],
)
def test_usage_error(cmd_args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *cmd_args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
