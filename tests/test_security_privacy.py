"""Test zero-leak privacy and security invariants for nucleus."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

RFC1918_PATTERNS = [
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}\b"),
]

WORKSTATION_PATH_PATTERNS = [
    re.compile(r"/home/[a-zA-Z0-9_-]+"),
    re.compile(r"/Users/[a-zA-Z0-9_-]+"),
    re.compile(r"[a-zA-Z]:\\Users\\[a-zA-Z0-9_-]+"),
]

EXCLUDED_DIRS = {".git", ".pytest_cache", "__pycache__", "output", "build", "dist"}

def get_scannable_files():
    for p in REPO_ROOT.rglob("*"):
        if p.is_file() and not any(part in EXCLUDED_DIRS for part in p.parts):
            yield p

def test_no_private_rfc1918_ips():
    """Ensure no RFC 1918 private IP addresses exist in any tracked file."""
    violations = []
    for file_path in get_scannable_files():
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in RFC1918_PATTERNS:
            matches = pattern.findall(content)
            if matches:
                violations.append(f"{file_path.relative_to(REPO_ROOT)}: {matches}")
    assert not violations, f"RFC 1918 private IP leaks detected:\n" + "\n".join(violations)

def test_no_developer_workstation_paths():
    """Ensure no developer workstation home paths exist in any tracked file."""
    violations = []
    for file_path in get_scannable_files():
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in WORKSTATION_PATH_PATTERNS:
            matches = pattern.findall(content)
            if matches:
                violations.append(f"{file_path.relative_to(REPO_ROOT)}: {matches}")
    assert not violations, f"Workstation path leaks detected:\n" + "\n".join(violations)
