"""Tests for scripts/check-action-pins.sh against stand-in gh and git commands.

The stand-ins answer `gh api <endpoint>` and `git ls-remote` from files, so these
tests run offline. They cover the outcomes the script must tell apart: a tag that
points to the pinned commit, a pinned commit that does not exist upstream, a commit
that exists but belongs to another tag than the comment claims, and an API refusal
(HTTP 403 from an organization IP allow list) answered over anonymous git.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-action-pins.sh"

PINNED = "a" * 40
OTHER = "b" * 40
TAG_OBJECT = "c" * 40

FAKE_GH = """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" != "api" ]]; then
  exit 4
fi
key="${2//\\//__}"
printf '%s\\n' "$2" >>"${FAKE_GH_DIR}/calls.log"
if [[ -f "${FAKE_GH_DIR}/${key}" ]]; then
  cat "${FAKE_GH_DIR}/${key}"
  exit 0
fi
code=404
if [[ -f "${FAKE_GH_DIR}/${key}.status" ]]; then
  code="$(cat "${FAKE_GH_DIR}/${key}.status")"
fi
printf '{"message":"stand-in","status":"%s"}\\n' "${code}"
echo "gh: stand-in failure (HTTP ${code})" >&2
exit 1
"""

FAKE_GIT = """#!/usr/bin/env bash
set -euo pipefail
printf 'git prompt=%s %s\\n' "${GIT_TERMINAL_PROMPT:-unset}" "$*" >>"${FAKE_GH_DIR}/calls.log"
url=""
after_ls_remote=0
for arg in "$@"; do
  if [[ ${after_ls_remote} -eq 1 ]]; then
    url="${arg}"
    break
  fi
  if [[ "${arg}" == "ls-remote" ]]; then
    after_ls_remote=1
  fi
done
repo="${url#https://github.com/}"
repo="${repo%.git}"
key="ls-remote__${repo//\\//__}"
if [[ -n "${repo}" && -f "${FAKE_GH_DIR}/${key}" ]]; then
  cat "${FAKE_GH_DIR}/${key}"
  exit 0
fi
echo "fatal: could not read Username for 'https://github.com': terminal prompts disabled" >&2
exit 128
"""


class FakeGitHub:
    """Files under a directory stand in for GitHub API answers."""

    def __init__(self, root: Path) -> None:
        self.table = root / "gh-table"
        self.table.mkdir()
        self.bindir = root / "bin"
        self.bindir.mkdir()
        for name, script in (("gh", FAKE_GH), ("git", FAKE_GIT)):
            stand_in = self.bindir / name
            stand_in.write_text(script, encoding="utf-8")
            stand_in.chmod(0o755)

    def answer(self, endpoint: str, text: str) -> None:
        (self.table / endpoint.replace("/", "__")).write_text(text + "\n", encoding="utf-8")

    def tags(self, repo: str, listing: str) -> None:
        """What anonymous `git ls-remote https://github.com/<repo>.git` lists."""
        (self.table / ("ls-remote__" + repo.replace("/", "__"))).write_text(
            listing + "\n", encoding="utf-8"
        )

    def fail(self, endpoint: str, code: int) -> None:
        (self.table / (endpoint.replace("/", "__") + ".status")).write_text(
            str(code), encoding="utf-8"
        )

    def calls(self) -> list[str]:
        log = self.table / "calls.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@pytest.fixture
def github(tmp_path):
    return FakeGitHub(tmp_path)


def _workflows(tmp_path: Path, **files: str) -> Path:
    directory = tmp_path / "workflows"
    directory.mkdir()
    for name, uses in files.items():
        lines = "\n".join(f"      - uses: {use}" for use in uses.splitlines())
        body = f"---\nname: {name}\non: push\njobs:\n  job:\n    runs-on: ubuntu-latest\n    steps:\n{lines}\n"
        (directory / f"{name}.yml").write_text(body, encoding="utf-8")
    return directory


def _run(github: FakeGitHub, workflows: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{github.bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_GH_DIR": str(github.table),
    }
    return subprocess.run(
        ["bash", str(SCRIPT), f"--workflows-dir={workflows}"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_pin_matching_its_lightweight_tag_passes(tmp_path, github):
    github.answer("repos/o/r/git/ref/tags/v1.0.0", f"commit {PINNED}")
    result = _run(github, _workflows(tmp_path, ci=f"o/r@{PINNED} # v1.0.0"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"OK    o/r@{PINNED} # v1.0.0  [ci.yml:8]" in result.stdout
    assert github.calls() == ["repos/o/r/git/ref/tags/v1.0.0"]


def test_workflow_without_uses_lines_is_not_an_error(tmp_path, github):
    github.answer("repos/o/r/git/ref/tags/v1.0.0", f"commit {PINNED}")
    workflows = _workflows(tmp_path, ci=f"o/r@{PINNED} # v1.0.0")
    (workflows / "scripted.yml").write_text(
        "---\nname: scripted\non: push\njobs:\n  job:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: echo nothing to pin\n",
        encoding="utf-8",
    )
    result = _run(github, workflows)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 pin(s) checked" in result.stdout


def test_unreadable_workflow_is_an_error_not_a_skipped_file(tmp_path, github):
    """grep exits 2 for a file it cannot read; that must not pass as 'no pins'."""
    workflows = _workflows(tmp_path, ci=f"o/r@{PINNED} # v1.0.0")
    locked = workflows / "ci.yml"
    locked.chmod(0o000)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("file permissions are not enforced for this user")
        result = _run(github, workflows)
    finally:
        locked.chmod(0o644)
    assert result.returncode == 1
    assert "cannot read" in result.stderr and "ci.yml" in result.stderr
    assert "0 failure(s)" not in result.stdout


def test_annotated_tag_is_dereferenced(tmp_path, github):
    github.answer("repos/o/r/git/ref/tags/v1.0.0", f"tag {TAG_OBJECT}")
    github.answer(f"repos/o/r/git/tags/{TAG_OBJECT}", f"commit {PINNED}")
    result = _run(github, _workflows(tmp_path, ci=f"o/r@{PINNED} # v1.0.0"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"OK    o/r@{PINNED} # v1.0.0" in result.stdout


def test_pinned_commit_missing_upstream_is_reported(tmp_path, github):
    github.answer("repos/o/r/git/ref/tags/v3.0.0", f"commit {OTHER}")
    github.fail(f"repos/o/r/commits/{PINNED}", 422)
    result = _run(github, _workflows(tmp_path, publish=f"o/r@{PINNED} # v3.0.0"))
    assert result.returncode == 1
    line = next(line for line in result.stdout.splitlines() if line.startswith("FAIL"))
    assert f"tag v3.0.0 points to {OTHER}" in line
    assert "does not exist upstream (HTTP 422" in line


def test_commit_of_another_release_is_reported_not_accepted(tmp_path, github):
    """The commit exists, but the tag in the comment is a different release."""
    github.answer("repos/o/r/git/ref/tags/v1.4.0", f"tag {TAG_OBJECT}")
    github.answer(f"repos/o/r/git/tags/{TAG_OBJECT}", f"commit {OTHER}")
    github.answer(f"repos/o/r/commits/{PINNED}", PINNED)
    result = _run(github, _workflows(tmp_path, gate=f"o/r@{PINNED} # v1.4.0"))
    assert result.returncode == 1
    assert (
        f"tag v1.4.0 points to {OTHER}, not to the pin; the pinned commit exists upstream"
        in result.stdout
    )
    assert "OK " not in result.stdout


def test_tag_missing_upstream_is_reported(tmp_path, github):
    github.answer(f"repos/o/r/commits/{PINNED}", PINNED)
    result = _run(github, _workflows(tmp_path, ci=f"o/r@{PINNED} # v9.9.9"))
    assert result.returncode == 1
    assert "tag v9.9.9 not resolved (HTTP 404" in result.stdout
    assert not any(call.startswith("git ") for call in github.calls()), "404 is an answer"


def test_failed_queries_fail_closed(tmp_path, github):
    github.fail("repos/o/r/git/ref/tags/v1.0.0", 502)
    github.fail(f"repos/o/r/commits/{PINNED}", 502)
    result = _run(github, _workflows(tmp_path, ci=f"o/r@{PINNED} # v1.0.0"))
    assert result.returncode == 1
    assert "not resolved (HTTP 502 from repos/o/r/git/ref/tags/v1.0.0)" in result.stdout
    assert "the pinned commit could not be checked (HTTP 502" in result.stdout
    assert "OK " not in result.stdout


def test_ip_allow_list_refusal_falls_back_to_anonymous_git(tmp_path, github):
    """An organization IP allow list answers 403 to the Actions token, even for public repos."""
    github.fail("repos/o/r/git/ref/tags/v0.36.0", 403)
    github.tags("o/r", f"{TAG_OBJECT}\trefs/tags/v0.36.0\n{PINNED}\trefs/tags/v0.36.0^{{}}")
    result = _run(github, _workflows(tmp_path, scans=f"o/r@{PINNED} # v0.36.0"))
    assert result.returncode == 0, result.stdout + result.stderr
    note = "(via anonymous git, the API answered HTTP 403)"
    assert f"OK    o/r@{PINNED} # v0.36.0  {note}  [scans.yml:8]" in result.stdout
    git_calls = [call for call in github.calls() if call.startswith("git ")]
    assert len(git_calls) == 1
    assert git_calls[0].startswith("git prompt=0 -C / ")
    listing = "ls-remote https://github.com/o/r.git refs/tags/v0.36.0 refs/tags/v0.36.0^{}"
    assert listing in git_calls[0]


def test_anonymous_git_answer_is_held_to_the_pin(tmp_path, github):
    github.fail("repos/o/r/git/ref/tags/v0.36.0", 403)
    github.fail(f"repos/o/r/commits/{PINNED}", 403)
    github.tags("o/r", f"{OTHER}\trefs/tags/v0.36.0")
    result = _run(github, _workflows(tmp_path, scans=f"o/r@{PINNED} # v0.36.0"))
    assert result.returncode == 1
    assert f"tag v0.36.0 points to {OTHER} (via anonymous git" in result.stdout
    assert "the pinned commit could not be checked (HTTP 403" in result.stdout


def test_anonymous_git_failure_fails_closed(tmp_path, github):
    github.fail("repos/o/r/git/ref/tags/v1.0.0", 403)
    github.fail(f"repos/o/r/commits/{PINNED}", 403)
    result = _run(github, _workflows(tmp_path, ci=f"o/r@{PINNED} # v1.0.0"))
    assert result.returncode == 1
    refusal = "not resolved (HTTP 403 from repos/o/r/git/ref/tags/v1.0.0; anonymous git: fatal"
    assert refusal in result.stdout
    assert "OK " not in result.stdout


def test_unpinned_or_uncommented_uses_fail(tmp_path, github):
    uses = f"o/r@v1\no/r@{PINNED}\no/r@{PINNED[:7]} # v1.0.0"
    result = _run(github, _workflows(tmp_path, ci=uses))
    assert result.returncode == 1
    failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
    assert len(failures) == 3
    assert all("not pinned as owner/repo@<40-hex commit> # <tag>" in line for line in failures)
    assert github.calls() == []


def test_local_actions_are_skipped(tmp_path, github):
    github.answer("repos/o/r/git/ref/tags/v1.0.0", f"commit {PINNED}")
    uses = f"./.github/actions/setup\no/r@{PINNED} # v1.0.0"
    result = _run(github, _workflows(tmp_path, ci=uses))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SKIP  ./.github/actions/setup  [ci.yml:8]: local action" in result.stdout


def test_one_line_per_pin_across_files(tmp_path, github):
    github.answer("repos/o/r/git/ref/tags/v1.0.0", f"commit {PINNED}")
    pin = f'"o/r@{PINNED}" # v1.0.0'
    result = _run(github, _workflows(tmp_path, a=pin, b=pin))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"OK    o/r@{PINNED} # v1.0.0  [a.yml:8 b.yml:8]" in result.stdout
    assert github.calls() == ["repos/o/r/git/ref/tags/v1.0.0"]


def test_actions_in_a_subdirectory_resolve_against_their_repository(tmp_path, github):
    github.answer("repos/o/r/git/ref/tags/v2.0.0", f"commit {PINNED}")
    result = _run(github, _workflows(tmp_path, ci=f"o/r/sub/init@{PINNED} # v2.0.0"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"OK    o/r/sub/init@{PINNED} # v2.0.0" in result.stdout


def test_lint_pins_runs_in_ci_and_stays_out_of_offline_lint():
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^lint-pins: .*## ", makefile, re.MULTILINE)
    assert "./scripts/check-action-pins.sh" in makefile
    lint_prerequisites = re.search(r"^lint:([^#\n]*)", makefile, re.MULTILINE).group(1)
    assert "lint-pins" not in lint_prerequisites, "make lint must stay offline"

    ci = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    steps = [step for job in ci["jobs"].values() for step in job["steps"]]
    pins = [step for step in steps if step.get("run", "").strip() == "make lint-pins"]
    assert len(pins) == 1, "ci.yml must run make lint-pins once"
    assert pins[0]["env"]["GH_TOKEN"] == "${{ github.token }}"
