# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for scripts/refuse_existing_release.sh.

Verifies that publish-release refuses execution when a GitHub release already exists
for the target tag (non-reproducible packages), proceeds cleanly when no release exists,
surfaces gh errors (fail closed), and checks arguments passed to the gh CLI.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "refuse_existing_release.sh"
TEST_REPO = "cordanaLLM/nucleus"
TEST_TAG = "v7.2.8-mainstream-lusoris1"


def _create_gh_stub(
    tmp_path: Path,
    *,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
) -> tuple[Path, Path]:
    """Create a mock gh CLI executable that records invocations and returns configured output."""
    log_file = tmp_path / "gh_args.log"
    stdout_file = tmp_path / "gh_stdout.txt"
    stderr_file = tmp_path / "gh_stderr.txt"
    stdout_file.write_text(stdout, encoding="utf-8")
    stderr_file.write_text(stderr, encoding="utf-8")

    gh_script = tmp_path / "gh"
    content = f"""#!/usr/bin/env bash
printf "%s\\n" "$*" >> "{log_file}"
cat "{stdout_file}"
cat "{stderr_file}" >&2
exit {exit_code}
"""
    gh_script.write_text(content, encoding="utf-8")
    gh_script.chmod(0o755)
    return gh_script, log_file


def _run_script(
    tmp_path: Path,
    args: list[str],
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Execute refuse_existing_release.sh with tmp_path prepended to PATH."""
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"}
    log_file = tmp_path / "gh_args.log"
    result = subprocess.run(
        [str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log_file


def test_refuse_existing_release_not_found(tmp_path: Path):
    """When gh reports 'release not found', the script exits 0 and allows the release run."""
    _create_gh_stub(
        tmp_path,
        exit_code=1,
        stdout="",
        stderr="release not found\n",
    )
    result, log_file = _run_script(tmp_path, ["--repo", TEST_REPO, "--tag", TEST_TAG])

    assert result.returncode == 0, f"Expected 0, got {result.returncode}: {result.stderr}"
    assert f"No release exists for tag '{TEST_TAG}'" in result.stdout
    assert "::error::" not in result.stderr

    logged_args = log_file.read_text(encoding="utf-8").strip()
    assert (
        logged_args
        == f"release view {TEST_TAG} --repo {TEST_REPO} --json assets --jq .assets | length"
    )


def test_refuse_existing_release_exists_with_assets(tmp_path: Path):
    """When a release already exists with assets, the script exits 1 and emits an error annotation."""
    release_json = "2\n"
    _create_gh_stub(
        tmp_path,
        exit_code=0,
        stdout=release_json,
        stderr="",
    )
    result, log_file = _run_script(tmp_path, ["--repo", TEST_REPO, "--tag", TEST_TAG])

    assert result.returncode == 1
    assert "::error::" in result.stderr
    assert TEST_TAG in result.stderr
    assert "2 asset(s)" in result.stderr
    assert "lusoris<N+1>" in result.stderr
    assert "operator" in result.stderr

    logged_args = log_file.read_text(encoding="utf-8").strip()
    assert (
        logged_args
        == f"release view {TEST_TAG} --repo {TEST_REPO} --json assets --jq .assets | length"
    )


def test_refuse_existing_release_exists_with_zero_assets(tmp_path: Path):
    """When a release exists with zero assets, the script exits 1 and emits an error annotation."""
    release_json = "0\n"
    _create_gh_stub(
        tmp_path,
        exit_code=0,
        stdout=release_json,
        stderr="",
    )
    result, log_file = _run_script(tmp_path, ["--repo", TEST_REPO, "--tag", TEST_TAG])

    assert result.returncode == 1
    assert "::error::" in result.stderr
    assert TEST_TAG in result.stderr
    assert "0 asset(s)" in result.stderr
    assert "lusoris<N+1>" in result.stderr
    assert "operator" in result.stderr

    logged_args = log_file.read_text(encoding="utf-8").strip()
    assert (
        logged_args
        == f"release view {TEST_TAG} --repo {TEST_REPO} --json assets --jq .assets | length"
    )


def test_refuse_existing_release_auth_or_network_failure(tmp_path: Path):
    """Any gh failure other than 'release not found' exits 1 and surfaces the error (fail closed)."""
    error_msg = "HTTP 401: Bad credentials (token has expired)"
    _create_gh_stub(
        tmp_path,
        exit_code=1,
        stdout="",
        stderr=f"{error_msg}\n",
    )
    result, log_file = _run_script(tmp_path, ["--repo", TEST_REPO, "--tag", TEST_TAG])

    assert result.returncode == 1
    assert error_msg in result.stderr
    assert "fail closed" in result.stderr
    assert "::error::" not in result.stderr

    logged_args = log_file.read_text(encoding="utf-8").strip()
    assert (
        logged_args
        == f"release view {TEST_TAG} --repo {TEST_REPO} --json assets --jq .assets | length"
    )


def test_refuse_existing_release_argument_validation(tmp_path: Path):
    """Missing or invalid arguments are refused."""
    _create_gh_stub(tmp_path, exit_code=0)

    # Missing --repo
    res1, _ = _run_script(tmp_path, ["--tag", TEST_TAG])
    assert res1.returncode == 1
    assert "--repo OWNER/NAME is required" in res1.stderr

    # Missing --tag
    res2, _ = _run_script(tmp_path, ["--repo", TEST_REPO])
    assert res2.returncode == 1
    assert "--tag TAG is required" in res2.stderr

    # Unknown option
    res3, _ = _run_script(tmp_path, ["--invalid"])
    assert res3.returncode == 1
    assert "unknown option" in res3.stderr


def test_a_failure_that_only_mentions_release_not_found_is_refused(tmp_path: Path):
    """Only gh's exact not-found message proves absence; a longer error refuses (fail closed)."""
    _create_gh_stub(
        tmp_path,
        exit_code=1,
        stderr="HTTP 502: upstream said release not found, retry later\n",
    )
    result, _ = _run_script(tmp_path, ["--repo", TEST_REPO, "--tag", TEST_TAG])

    assert result.returncode == 1
    assert "fail closed" in result.stderr
