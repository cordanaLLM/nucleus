"""The local gates fail on what they check for, and on a missing tool.

`make fmt-check` once ended in `|| true` and ran each formatter only when installed, and the
repository audit ignored yamllint's exit status, skipped missing tools and repaired executable
bits instead of reporting them. Each test plants one defect in a copy of the tracked tree and
requires the gate to refuse it.
"""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
MAKE = shutil.which("make")

BASICS = ("bash", "env", "make", "git", "python3", "find", "grep", "wc", "dirname", "sed")

pytestmark = pytest.mark.skipif(BASH is None or MAKE is None, reason="needs bash and make")


@pytest.fixture
def tree(tmp_path):
    """A git repository holding a copy of every tracked file, modes included."""
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.split(b"\0")
    root = tmp_path / "nucleus"
    for name in filter(None, listed):
        source = REPO_ROOT / name.decode()
        if not source.is_file():
            continue
        target = root / name.decode()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def _make(tree: Path, target: str, path: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": path or os.environ["PATH"]}
    return subprocess.run(
        [MAKE, "-s", "-C", str(tree), target], capture_output=True, text=True, env=env
    )


def _path_without(tmp_path: Path, missing: str, tools: tuple[str, ...]) -> str:
    """A PATH holding only `tools` and the basics, so `missing` cannot resolve.

    Each entry is a wrapper script, not a symlink, so `python3` stays the interpreter running
    these tests, with its virtual environment.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    found = {tool: shutil.which(tool) for tool in BASICS + tools}
    found["python3"] = sys.executable
    for tool, target in found.items():
        if tool != missing and target:
            wrapper = bin_dir / tool
            wrapper.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8")
            wrapper.chmod(0o755)
    return str(bin_dir)


def _require(*tools: str) -> None:
    absent = [tool for tool in tools if shutil.which(tool) is None]
    if absent:
        pytest.fail(
            f"{', '.join(absent)} missing: pip install --require-hashes -r requirements-test.txt"
        )


def test_fmt_check_passes_the_tracked_tree(tree):
    _require("ruff", "shfmt")
    result = _make(tree, "fmt-check")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("path", "text", "tool"),
    [
        pytest.param(
            "scripts/planted.py", "x = {  'a':1 }\n", "ruff format", id="unformatted-python"
        ),
        pytest.param("tests/planted.py", "import os\n", "ruff check", id="unused-import"),
        pytest.param(
            "scripts/planted.sh",
            "#!/usr/bin/env bash\nf()   {\n    echo  x\n}\n",
            "shfmt",
            id="unformatted-shell",
        ),
    ],
)
def test_fmt_check_refuses_a_planted_defect(tree, path, text, tool):
    _require("ruff", "shfmt")
    (tree / path).write_text(text, encoding="utf-8")
    result = _make(tree, "fmt-check")
    assert result.returncode != 0, f"{tool} finding passed:\n{result.stdout}"
    assert "planted" in result.stdout + result.stderr


@pytest.mark.parametrize("missing", ["ruff", "shfmt"])
def test_fmt_check_fails_when_a_tool_is_missing(tree, tmp_path, missing):
    path = _path_without(tmp_path, missing, ("ruff", "shfmt"))
    result = _make(tree, "fmt-check", path)
    assert result.returncode != 0
    assert f"{missing} is not installed" in result.stderr


def _audit(tree: Path, path: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": path or os.environ["PATH"]}
    script = tree / "scripts" / "audit-repository-health.sh"
    return subprocess.run([BASH, str(script)], capture_output=True, text=True, env=env)


AUDIT_TOOLS = ("shellcheck", "yamllint", "actionlint")


def test_audit_passes_the_tracked_tree(tree):
    _require(*AUDIT_TOOLS)
    result = _audit(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL QUALITY GATES PASSED" in result.stdout


def test_audit_refuses_a_yamllint_error(tree):
    _require(*AUDIT_TOOLS)
    (tree / "planted.yml").write_text("---\nkey: 1\nkey: 2\n", encoding="utf-8")
    result = _audit(tree)
    assert result.returncode != 0, result.stdout
    assert "planted.yml" in result.stdout
    assert "ALL QUALITY GATES PASSED" not in result.stdout


def test_audit_reports_a_non_executable_script_and_leaves_it(tree):
    _require(*AUDIT_TOOLS)
    script = tree / "scripts" / "merge-config.sh"
    script.chmod(script.stat().st_mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    result = _audit(tree)
    assert result.returncode != 0, result.stdout
    assert "merge-config.sh is not executable" in result.stdout
    assert not script.stat().st_mode & stat.S_IXUSR, "the audit must report, not repair"


@pytest.mark.parametrize("missing", AUDIT_TOOLS)
def test_audit_fails_when_a_tool_is_missing(tree, tmp_path, missing):
    path = _path_without(tmp_path, missing, AUDIT_TOOLS)
    result = _audit(tree, path)
    assert result.returncode != 0, result.stdout
    assert f"{missing} is not installed" in result.stdout
