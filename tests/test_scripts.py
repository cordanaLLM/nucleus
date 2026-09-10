"""Test shell script standards and Power of 10 compliance for lusoris-kernel-forge."""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

def test_scripts_executable():
    """Ensure all scripts have executable bits set."""
    scripts = list((REPO_ROOT / "scripts").glob("*.sh"))
    assert len(scripts) >= 3, "At least 3 core scripts must exist"
    for script in scripts:
        assert script.stat().st_mode & 0o111, f"{script.name} must be executable"

def test_scripts_shebang_and_strict_mode():
    """Ensure all scripts have shebang and set -euo pipefail."""
    scripts = list((REPO_ROOT / "scripts").glob("*.sh"))
    for script in scripts:
        content = script.read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env bash"), f"{script.name} must use bash shebang"
        assert "set -euo pipefail" in content, f"{script.name} must enable strict mode"
        assert "Copyright 2026 The Lusoris Authors" in content, f"{script.name} must have copyright header"

def test_power_of_ten_function_length():
    """Ensure no shell function exceeds 60 lines (NASA/JPL Power of 10)."""
    scripts = list((REPO_ROOT / "scripts").glob("*.sh"))
    for script in scripts:
        lines = script.read_text(encoding="utf-8").splitlines()
        in_func = False
        func_name = ""
        count = 0
        for line in lines:
            stripped = line.strip()
            if stripped.endswith("() {") or (stripped.endswith("()") and not stripped.startswith("#")):
                in_func = True
                func_name = stripped.split()[0]
                count = 0
            elif in_func:
                if stripped == "}":
                    assert count <= 60, f"Function {func_name} in {script.name} exceeds 60 lines ({count})"
                    in_func = False
                elif stripped and not stripped.startswith("#"):
                    count += 1

def test_shellcheck_clean():
    """Ensure scripts pass ShellCheck with 0 warnings."""
    scripts = list((REPO_ROOT / "scripts").glob("*.sh"))
    cmd = ["shellcheck", "-s", "bash"] + [str(s) for s in scripts]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"ShellCheck failed:\n{result.stdout}\n{result.stderr}"
