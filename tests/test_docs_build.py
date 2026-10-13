"""Pull requests build the documentation strictly, and the strict build refuses a broken link.

`mkdocs build --strict` used to run only in `pages.yml`, on a push to main, so a broken link
reached main before anything built it. The lint job of `ci.yml` now runs `make docs-build`.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MKDOCS = shutil.which("mkdocs")


def _makefile_recipe(target: str) -> list[str]:
    lines = (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{target}:"))
    recipe = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line.strip().lstrip("@"))
    return recipe


def test_docs_build_is_strict_and_not_quiet():
    """A quiet flag hides the warnings strict mode counts."""
    builds = [line for line in _makefile_recipe("docs-build") if line.startswith("mkdocs build")]
    assert builds, "docs-build runs no mkdocs build"
    for line in builds:
        words = line.split()
        assert "--strict" in words, line
        assert not {"-q", "--quiet"} & set(words), line


def test_the_pull_request_lint_job_builds_the_docs():
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert "pull_request" in workflow[True], "ci.yml must run on pull requests"
    job = workflow["jobs"]["lint-and-test"]
    assert "if" not in job
    runs = [step for step in job["steps"] if "make docs-build" in step.get("run", "")]
    assert runs, "the lint job does not run make docs-build"
    assert all("if" not in step and not step.get("continue-on-error") for step in runs)


@pytest.fixture
def docs_tree(tmp_path):
    if MKDOCS is None:
        pytest.fail("mkdocs missing: pip install --require-hashes -r requirements-test.txt")
    root = tmp_path / "nucleus"
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.split(b"\0")
    for name in filter(None, listed):
        source = REPO_ROOT / name.decode()
        if source.is_file():
            target = root / name.decode()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return root


def _build(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [MKDOCS, "build", "--strict", "-d", str(root.parent / "site")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_tracked_docs_build_strictly(docs_tree):
    result = _build(docs_tree)
    assert result.returncode == 0, result.stderr


def test_the_strict_build_refuses_a_planted_broken_link(docs_tree):
    index = docs_tree / "docs" / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "\nSee [the planted page](planted-missing.md).\n",
        encoding="utf-8",
    )
    result = _build(docs_tree)
    assert result.returncode != 0, "a broken link passed the strict build"
    assert "planted-missing.md" in result.stderr
