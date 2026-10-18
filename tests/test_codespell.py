"""make lint-spelling: codespell over the tracked tree with .codespellrc refuses a misspelling.

The configuration skips only the files .config/lint-exceptions.yaml names, by their full path;
every line of every other file is read, however long, and a file that only shares a skipped
file's name is read too.
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


@pytest.mark.parametrize("width", [0, 499, 520, 70000])
def test_a_planted_misspelling_is_refused_at_any_line_length(tmp_path, width):
    planted = tmp_path / "planted.md"
    planted.write_text("x" * width + " " + MISSPELLED + "\n", encoding="utf-8")
    assert spelling_exit(planted) != 0


def test_a_long_prose_line_in_another_file_is_read(tmp_path):
    words = " ".join(["the build record names the kernel release"] * 15)
    planted = tmp_path / "notes.md"
    planted.write_text(f"{words} {MISSPELLED} {words}\n", encoding="utf-8")
    assert len(words) > 500
    assert spelling_exit(planted) != 0


def test_a_file_that_only_shares_a_skipped_name_is_read(tmp_path):
    planted = tmp_path / "player.js"
    planted.write_text(f"// {MISSPELLED}\n", encoding="utf-8")
    assert spelling_exit(planted) != 0
