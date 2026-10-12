"""Test zero-leak privacy and security invariants for nucleus.

The scan reads every file git tracks, the set a commit publishes, and fails on any file it cannot
read. It used to walk the checkout instead, so untracked local state failed it, and it skipped
any file whose read raised, so a leak in an unreadable file passed. The planted cases below build
a throwaway repository and require the scan to report each leak.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

RFC1918_PATTERNS = [
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(?:1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}\b"),
]

WORKSTATION_PATH_PATTERNS = [
    re.compile(r"/home/[a-zA-Z0-9_-]+"),
    re.compile(r"/Users/[a-zA-Z0-9_-]+"),
    re.compile(r"[a-zA-Z]:\\Users\\[a-zA-Z0-9_-]+"),
]


def tracked_files(root: Path) -> list[Path]:
    """Every file in the git index of ``root``; fails when git does."""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [root / name for name in out.decode("utf-8").split("\0") if name]


def scan(root: Path, patterns: list[re.Pattern]) -> list[str]:
    """One line per tracked file that matches a pattern. A read error propagates."""
    violations = []
    for file_path in tracked_files(root):
        content = file_path.read_bytes().decode("utf-8", errors="ignore")
        matches = [m.group(0) for pattern in patterns for m in pattern.finditer(content)]
        if matches:
            violations.append(f"{file_path.relative_to(root)}: {matches}")
    return violations


def test_no_private_rfc1918_ips():
    """Ensure no RFC 1918 private IP addresses exist in any tracked file."""
    violations = scan(REPO_ROOT, RFC1918_PATTERNS)
    assert not violations, "RFC 1918 private IP leaks detected:\n" + "\n".join(violations)


def test_no_developer_workstation_paths():
    """Ensure no developer workstation home paths exist in any tracked file."""
    violations = scan(REPO_ROOT, WORKSTATION_PATH_PATTERNS)
    assert not violations, "Workstation path leaks detected:\n" + "\n".join(violations)


# Planted cases --------------------------------------------------------------------------------

# Built from parts, so that this file does not match the patterns it tests.
PLANTED_IPS = ["10." + "4.0.9", "192." + "168.7.20", "172." + "20.1.5"]
PLANTED_PATHS = ["/ho" + "me/someone", "/Us" + "ers/someone", "C:" + "\\Users\\someone"]


@pytest.fixture
def planted(tmp_path):
    """A git repository with one tracked leak file, one untracked one and one clean file."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "leak.conf").write_text("\n".join(PLANTED_IPS + PLANTED_PATHS) + "\n", encoding="utf-8")
    (tmp_path / "clean.txt").write_text("192.0.2.10 kernel.example.com\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "leak.conf", "clean.txt"], check=True)
    (tmp_path / "untracked.log").write_text(PLANTED_PATHS[0] + "\n", encoding="utf-8")
    return tmp_path


def test_the_scan_reports_every_planted_leak(planted):
    assert scan(planted, RFC1918_PATTERNS) == [f"leak.conf: {PLANTED_IPS}"]
    assert scan(planted, WORKSTATION_PATH_PATTERNS) == [f"leak.conf: {PLANTED_PATHS}"]


def test_the_scan_reads_only_tracked_files(planted):
    assert sorted(p.name for p in tracked_files(planted)) == ["clean.txt", "leak.conf"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file without permissions")
def test_an_unreadable_tracked_file_fails_the_scan(planted):
    leak = planted / "leak.conf"
    leak.chmod(0)
    try:
        with pytest.raises(PermissionError):
            scan(planted, RFC1918_PATTERNS)
    finally:
        leak.chmod(0o644)


def test_a_tracked_file_missing_from_the_checkout_fails_the_scan(planted):
    (planted / "leak.conf").unlink()
    with pytest.raises(FileNotFoundError):
        scan(planted, WORKSTATION_PATH_PATTERNS)
