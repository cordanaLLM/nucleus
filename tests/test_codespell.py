"""make lint-spelling: codespell over the tracked tree with .codespellrc refuses a misspelling.

The configuration ignores a line of 500 characters or more as minified code; these cases prove
that a misspelling on a shorter line is still refused, at the length boundary too.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / ".codespellrc"
# Built at run time, so this file itself stays clean under make lint-spelling.
MISSPELLED = "recie" + "ved"


def spelling_exit(path):
    """Exit status of codespell with the repository configuration over one file."""
    return subprocess.run(
        ["codespell", "--config", str(CONFIG), str(path)],
        capture_output=True,
        check=False,
        timeout=60,
    ).returncode


def test_the_tracked_tree_passes():
    result = subprocess.run(
        ["make", "-s", "lint-spelling"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("width", [0, 400, 498])
def test_a_planted_misspelling_on_a_short_line_is_refused(tmp_path, width):
    line = "x" * width + " " + MISSPELLED
    planted = tmp_path / "planted.md"
    planted.write_text(line[-499:] + "\n", encoding="utf-8")
    assert spelling_exit(planted) != 0


def test_a_minified_line_is_not_read(tmp_path):
    planted = tmp_path / "bundle.js"
    planted.write_text("x" * 520 + " " + MISSPELLED + "\n", encoding="utf-8")
    assert spelling_exit(planted) == 0
