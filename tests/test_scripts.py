"""Test shell script standards and Power of 10 compliance for nucleus."""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

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
        assert "Copyright 2026 The Lusoris Authors" in content, (
            f"{script.name} must have copyright header"
        )


FUNCTION_START = re.compile(r"^(?:function\s+)?([A-Za-z_][A-Za-z0-9_-]*)\s*\(\)\s*\{?\s*$")
HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
MAX_FUNCTION_LINES = 60


def function_lengths(text: str) -> list[tuple[str, int]]:
    """Each shell function's name and its count of non-blank, non-comment body lines.

    A function opens on a line of its own (``name() {``) and closes at the next ``}`` at the
    indentation of its opening line, so a ``|| {`` block or a nested brace inside it does not
    end it; heredoc bodies count as lines and their contents never open or close anything.
    """
    found: list[tuple[str, int]] = []
    name, indent, count = "", "", 0
    heredoc: tuple[str, bool] | None = None
    for line in text.splitlines():
        if heredoc is not None:
            word, dash = heredoc
            if (line.lstrip("\t") if dash else line) == word:
                heredoc = None
            if name:
                count += 1
            continue
        stripped = line.strip()
        match = HEREDOC.search(line) if not stripped.startswith("#") else None
        if match:
            heredoc = (match.group(3), match.group(1) == "-")
        if not name:
            start = FUNCTION_START.match(stripped)
            if start:
                name, indent, count = start.group(1), line[: len(line) - len(line.lstrip())], 0
            continue
        if line.rstrip() == indent + "}" or line.startswith(indent + "} "):
            found.append((name, count))
            name = ""
        elif stripped and not stripped.startswith("#") and stripped != "{":
            count += 1
    assert not name, f"function {name} is never closed"
    return found


def _workflow_run_blocks() -> list[tuple[str, str]]:
    blocks = []
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        parsed = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job_id, job in (parsed.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                if "run" in step:
                    blocks.append((f"{workflow.name}:{job_id}:{index}", step["run"]))
    return blocks


def test_power_of_ten_function_length():
    """No shell function, in scripts/ or in a workflow step, exceeds 60 lines (Power of 10)."""
    sources = [
        (s.name, s.read_text(encoding="utf-8")) for s in (REPO_ROOT / "scripts").glob("*.sh")
    ]
    sources += _workflow_run_blocks()
    measured = 0
    for origin, text in sources:
        for name, count in function_lengths(text):
            measured += 1
            assert count <= MAX_FUNCTION_LINES, (
                f"Function {name} in {origin} exceeds 60 lines ({count})"
            )
    assert measured >= 20, f"only {measured} functions measured; the parser lost track"


def _body(lines: int, indent: str = "  ") -> str:
    return "\n".join(f"{indent}echo {i}" for i in range(lines))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("f() {\n" + _body(61) + "\n}\n", [("f", 61)], id="plain"),
        pytest.param(
            "f() {\n  run || {\n    echo failed\n  }\n" + _body(59) + "\n}\n",
            [("f", 62)],
            id="an-or-block-does-not-end-the-function",
        ),
        pytest.param(
            "f() {\n  cat <<EOF\n{\n}\nEOF\n" + _body(58) + "\n}\n",
            [("f", 62)],
            id="a-heredoc-brace-does-not-end-the-function",
        ),
        pytest.param(
            "f() {\n\tcat <<-'EOF'\n\t}\n\tEOF\n" + _body(2) + "\n}\ng() {\n  :\n}\n",
            [("f", 5), ("g", 1)],
            id="dash-heredoc-and-a-second-function",
        ),
        pytest.param("f()\n{\n" + _body(3) + "\n}\n", [("f", 3)], id="brace-on-its-own-line"),
    ],
)
def test_function_lengths_count_what_a_reader_counts(text, expected):
    """The measure counts across inner braces and heredocs, which the old one stopped at."""
    assert function_lengths(text) == expected


def test_an_unclosed_function_is_an_error():
    with pytest.raises(AssertionError, match="never closed"):
        function_lengths("f() {\n  echo\n")


def test_shellcheck_clean():
    """Ensure scripts pass ShellCheck with 0 warnings."""
    scripts = list((REPO_ROOT / "scripts").glob("*.sh"))
    cmd = ["shellcheck", "-s", "bash"] + [str(s) for s in scripts]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"ShellCheck failed:\n{result.stdout}\n{result.stderr}"
