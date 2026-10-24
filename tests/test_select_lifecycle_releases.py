# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for published release selection in package lifecycle verification."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.select_lifecycle_releases import find_bump_time, select_lifecycle_releases


def test_positive_current_version_two_releases() -> None:
    """Select the two newest releases when releases of current version exist."""
    stream = "mainstream"
    version = "7.2.11"
    releases = [
        {"tagName": "v7.2.11-mainstream-lusoris1", "publishedAt": "2026-10-22T02:00:00Z"},
        {"tagName": "v7.2.11-mainstream-lusoris2", "publishedAt": "2026-10-22T04:00:00Z"},
        {"tagName": "v7.2.10-mainstream-lusoris3", "publishedAt": "2026-10-10T02:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)

    result = select_lifecycle_releases(stream, version, releases, bump_time, now)

    assert result.is_substituted is False
    assert result.release_tag == "v7.2.11-mainstream-lusoris2"
    assert result.previous_tag == "v7.2.11-mainstream-lusoris1"
    assert result.summary_lines == ()


def test_positive_current_version_single_release() -> None:
    """Select single release and set empty previous tag with summary note."""
    stream = "mainstream"
    version = "7.2.11"
    releases = [
        {"tagName": "v7.2.11-mainstream-lusoris1", "publishedAt": "2026-10-22T02:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)

    result = select_lifecycle_releases(stream, version, releases, bump_time, now)

    assert result.is_substituted is False
    assert result.release_tag == "v7.2.11-mainstream-lusoris1"
    assert result.previous_tag == ""
    assert len(result.summary_lines) == 1
    assert "Upgrade test was not exercised" in result.summary_lines[0]
    assert "v7.2.11-mainstream-lusoris1" in result.summary_lines[0]


def test_substitution_picks_newest_published_other_version_and_ignores_other_streams() -> None:
    """Substitute most recently published version when current version has no releases."""
    stream = "mainstream"
    version = "7.2.11"
    releases = [
        {"tagName": "v7.2.8-mainstream-lusoris1", "publishedAt": "2026-09-01T10:00:00Z"},
        {"tagName": "v7.2.8-mainstream-lusoris2", "publishedAt": "2026-09-05T10:00:00Z"},
        {"tagName": "v7.2.10-mainstream-lusoris1", "publishedAt": "2026-10-01T10:00:00Z"},
        {"tagName": "v7.2.10-mainstream-lusoris2", "publishedAt": "2026-10-15T10:00:00Z"},
        {"tagName": "v7.2.10-mainstream-lusoris3", "publishedAt": "2026-10-10T10:00:00Z"},
        {"tagName": "v7.2.11-realtime-lusoris1", "publishedAt": "2026-10-22T10:00:00Z"},
        {"tagName": "v7.3-rc7-bleeding-lusoris3", "publishedAt": "2026-10-22T12:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    result = select_lifecycle_releases(stream, version, releases, bump_time, now)

    assert result.is_substituted is True
    assert result.substituted_version == "7.2.10"
    assert result.release_tag == "v7.2.10-mainstream-lusoris3"
    assert result.previous_tag == "v7.2.10-mainstream-lusoris2"
    assert len(result.summary_lines) == 1
    summary = result.summary_lines[0]
    assert "Substitution:" in summary
    assert "mainstream" in summary
    assert "7.2.11" in summary
    assert "v7.2.10" in summary
    assert "v7.2.10-mainstream-lusoris2, v7.2.10-mainstream-lusoris3" in summary
    assert "bump 2026-10-20" in summary
    assert "refused after 2026-10-27" in summary


def test_substitution_single_older_release() -> None:
    """Substitute older version when only one release of that version exists."""
    stream = "mainstream"
    version = "7.2.11"
    releases = [
        {"tagName": "v7.2.10-mainstream-lusoris1", "publishedAt": "2026-10-01T10:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    result = select_lifecycle_releases(stream, version, releases, bump_time, now)

    assert result.is_substituted is True
    assert result.release_tag == "v7.2.10-mainstream-lusoris1"
    assert result.previous_tag == ""
    assert len(result.summary_lines) == 2
    assert "Substitution:" in result.summary_lines[0]
    assert "Upgrade test was not exercised" in result.summary_lines[1]


def test_planted_negative_no_releases_at_all() -> None:
    """Planted negative (a): refuse when no release exists for current or any older version."""
    stream = "mainstream"
    version = "7.2.11"
    releases = [
        {"tagName": "v7.2.11-realtime-lusoris1", "publishedAt": "2026-10-22T10:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)

    with pytest.raises(
        ValueError, match=r"^no published releases found matching v7\.2\.11-mainstream-lusoris<N>$"
    ):
        select_lifecycle_releases(stream, version, releases, bump_time, now)


def test_planted_negative_bump_older_than_seven_days_refused() -> None:
    """Planted negative (b): refuse when bump time exceeds 7 days without a published release."""
    stream = "mainstream"
    version = "7.2.11"
    releases = [
        {"tagName": "v7.2.10-mainstream-lusoris1", "publishedAt": "2026-10-01T10:00:00Z"},
    ]
    bump_time = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError) as exc_info:
        select_lifecycle_releases(stream, version, releases, bump_time, now)

    err_msg = str(exc_info.value)
    assert "7.2.11" in err_msg
    assert "2026-10-10" in err_msg
    assert "7-day bound" in err_msg


def test_boundary_seven_days_allowed_vs_plus_one_second_refused() -> None:
    """Boundary test: exactly 7 days difference is allowed, 7 days + 1 second is refused."""
    stream = "mainstream"
    version = "7.2.11"
    releases = [
        {"tagName": "v7.2.10-mainstream-lusoris1", "publishedAt": "2026-10-01T10:00:00Z"},
    ]
    bump_time = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)

    # Exactly 7 days: allowed
    exact_7d = bump_time + timedelta(days=7)
    res = select_lifecycle_releases(stream, version, releases, bump_time, exact_7d)
    assert res.is_substituted is True
    assert res.release_tag == "v7.2.10-mainstream-lusoris1"

    # 7 days + 1 second: refused
    plus_one_sec = bump_time + timedelta(days=7, seconds=1)
    with pytest.raises(ValueError) as exc_info:
        select_lifecycle_releases(stream, version, releases, bump_time, plus_one_sec)

    assert "7.2.11" in str(exc_info.value)
    assert "7-day bound" in str(exc_info.value)


def _git(repo: Path, args: list[str], date_str: str | None = None) -> None:
    """Run git in a throwaway repository with no user or system configuration."""
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    if date_str:
        env["GIT_COMMITTER_DATE"] = date_str
        env["GIT_AUTHOR_DATE"] = date_str
    subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, timeout=10, check=True
    )


def _commit_versions(repo: Path, stream_info: dict[str, str], date_str: str) -> None:
    """Commit a versions.json carrying one mainstream entry at a fixed date."""
    (repo / "versions.json").write_text(
        json.dumps({"streams": {"mainstream": stream_info}}), encoding="utf-8"
    )
    _git(repo, ["add", "versions.json"])
    _git(repo, ["commit", "-m", f"chore: {stream_info['version']}"], date_str=date_str)


def test_git_bump_walk_tmp_repo(tmp_path: Path) -> None:
    """Verify git log walk finds oldest commit of contiguous run and handles uncommitted edits."""
    _git(tmp_path, ["init", "-b", "main"])
    _git(tmp_path, ["config", "user.name", "Test Committer"])
    _git(tmp_path, ["config", "user.email", "test@example.com"])
    _commit_versions(tmp_path, {"version": "7.2.9"}, "2026-10-01T10:00:00Z")
    # The bump commit.
    _commit_versions(tmp_path, {"version": "7.2.10"}, "2026-10-10T10:00:00Z")
    # A later edit that keeps the version.
    _commit_versions(
        tmp_path, {"version": "7.2.10", "description": "updated"}, "2026-10-15T10:00:00Z"
    )

    # Oldest commit of the newest contiguous run for 7.2.10 is the bump commit.
    bump_time = find_bump_time("mainstream", "7.2.10", repo_dir=tmp_path)
    assert bump_time == datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)

    # An uncommitted bump counts as made now.
    (tmp_path / "versions.json").write_text(
        json.dumps({"streams": {"mainstream": {"version": "7.2.11"}}}), encoding="utf-8"
    )
    fixed_now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)
    uncommitted_bump = find_bump_time("mainstream", "7.2.11", repo_dir=tmp_path, now=fixed_now)
    assert uncommitted_bump == fixed_now


def test_planted_negative_missing_history_refused(tmp_path: Path) -> None:
    """A directory without git history refuses instead of treating the bump as made now."""
    (tmp_path / "versions.json").write_text(
        json.dumps({"streams": {"mainstream": {"version": "7.2.11"}}}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="cannot read versions.json history"):
        find_bump_time("mainstream", "7.2.11", repo_dir=tmp_path)
