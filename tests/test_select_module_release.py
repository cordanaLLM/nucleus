# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for published release selection in out-of-tree module builds."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from scripts.select_module_release import select_module_release


def test_positive_current_version_present_highest_rev_no_lines() -> None:
    """Select the highest revision of current version without summary lines."""
    stream = "mainstream"
    version_tag_form = "7.2.11"
    releases = [
        {"tag_name": "v7.2.11-mainstream-lusoris1", "published_at": "2026-10-22T02:00:00Z"},
        {"tag_name": "v7.2.11-mainstream-lusoris2", "published_at": "2026-10-22T04:00:00Z"},
        {"tag_name": "v7.2.10-mainstream-lusoris3", "published_at": "2026-10-10T02:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)

    tag, lines = select_module_release(
        stream=stream,
        version_tag_form=version_tag_form,
        releases=releases,
        published_tag=None,
        bump_time=bump_time,
        now=now,
    )

    assert tag == "v7.2.11-mainstream-lusoris2"
    assert lines == ()


def test_planted_missing_tag_within_seven_days_substituted() -> None:
    """Substitute newest older version within 7 days of bump commit with Substitution line."""
    stream = "mainstream"
    version_tag_form = "7.2.11"
    releases = [
        {"tag_name": "v7.2.10-mainstream-lusoris1", "published_at": "2026-10-01T10:00:00Z"},
        {"tag_name": "v7.2.10-mainstream-lusoris2", "published_at": "2026-10-15T10:00:00Z"},
        {"tag_name": "v7.2.8-mainstream-lusoris3", "published_at": "2026-09-01T10:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    tag, lines = select_module_release(
        stream=stream,
        version_tag_form=version_tag_form,
        releases=releases,
        published_tag="",
        bump_time=bump_time,
        now=now,
    )

    assert tag == "v7.2.10-mainstream-lusoris2"
    assert len(lines) == 1
    assert lines[0].startswith("Substitution:")
    assert "mainstream" in lines[0]
    assert "7.2.11" in lines[0]
    assert "v7.2.10" in lines[0]


def test_planted_negative_missing_beyond_seven_days_refused() -> None:
    """Refuse substitution when bump time exceeds 7-day bound without a published release."""
    stream = "mainstream"
    version_tag_form = "7.2.11"
    releases = [
        {"tag_name": "v7.2.10-mainstream-lusoris1", "published_at": "2026-10-01T10:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="7-day bound"):
        select_module_release(
            stream=stream,
            version_tag_form=version_tag_form,
            releases=releases,
            published_tag=None,
            bump_time=bump_time,
            now=now,
        )


def test_planted_negative_no_releases_of_stream_at_all_refused() -> None:
    """Refuse when no releases exist for the stream at all."""
    stream = "mainstream"
    version_tag_form = "7.2.11"
    releases = [
        {"tag_name": "v7.2.11-realtime-lusoris1", "published_at": "2026-10-22T10:00:00Z"},
        {"tag_name": "v7.3-rc7-bleeding-lusoris1", "published_at": "2026-10-22T10:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="no published releases found"):
        select_module_release(
            stream=stream,
            version_tag_form=version_tag_form,
            releases=releases,
            published_tag=None,
            bump_time=bump_time,
            now=now,
        )


def test_published_tag_present() -> None:
    """Return exact published tag without substitution when present and valid for stream."""
    stream = "mainstream"
    version_tag_form = "7.2.11"
    published_tag = "v7.2.11-mainstream-lusoris1"
    releases = [
        {"tag_name": "v7.2.11-mainstream-lusoris1", "published_at": "2026-10-22T02:00:00Z"},
        {"tag_name": "v7.2.11-mainstream-lusoris2", "published_at": "2026-10-22T04:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)

    tag, lines = select_module_release(
        stream=stream,
        version_tag_form=version_tag_form,
        releases=releases,
        published_tag=published_tag,
        bump_time=bump_time,
        now=now,
    )

    assert tag == published_tag
    assert lines == ()


def test_planted_negative_published_tag_absent_refused() -> None:
    """Refuse when published_tag is not present in releases list."""
    stream = "mainstream"
    version_tag_form = "7.2.11"
    published_tag = "v7.2.11-mainstream-lusoris99"
    releases = [
        {"tag_name": "v7.2.11-mainstream-lusoris1", "published_at": "2026-10-22T02:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match=published_tag):
        select_module_release(
            stream=stream,
            version_tag_form=version_tag_form,
            releases=releases,
            published_tag=published_tag,
            bump_time=bump_time,
            now=now,
        )


def test_planted_negative_published_tag_of_other_stream_refused() -> None:
    """Refuse when published_tag belongs to a different stream."""
    stream = "mainstream"
    version_tag_form = "7.2.11"
    published_tag = "v7.2.11-realtime-lusoris1"
    releases = [
        {"tag_name": "v7.2.11-realtime-lusoris1", "published_at": "2026-10-22T02:00:00Z"},
        {"tag_name": "v7.2.11-mainstream-lusoris1", "published_at": "2026-10-22T02:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match=published_tag):
        select_module_release(
            stream=stream,
            version_tag_form=version_tag_form,
            releases=releases,
            published_tag=published_tag,
            bump_time=bump_time,
            now=now,
        )


def test_other_streams_tags_ignored() -> None:
    """Releases belonging to other streams are ignored during release selection and substitution."""
    stream = "mainstream"
    version_tag_form = "7.2.11"
    releases = [
        {"tag_name": "v7.2.10-mainstream-lusoris1", "published_at": "2026-10-01T10:00:00Z"},
        {"tag_name": "v7.2.11-realtime-lusoris2", "published_at": "2026-10-22T10:00:00Z"},
        {"tag_name": "v7.3-rc7-bleeding-lusoris3", "published_at": "2026-10-22T12:00:00Z"},
    ]
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    bump_time = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    tag, lines = select_module_release(
        stream=stream,
        version_tag_form=version_tag_form,
        releases=releases,
        published_tag=None,
        bump_time=bump_time,
        now=now,
    )

    assert tag == "v7.2.10-mainstream-lusoris1"
    assert len(lines) == 1
    assert lines[0].startswith("Substitution:")
    assert "v7.2.10" in lines[0]


@pytest.mark.parametrize(
    "workflow_rel_path",
    [
        ".github/workflows/nvidia-open-module.yml",
        ".github/workflows/openzfs-module.yml",
    ],
)
def test_workflow_module_release_selection_contract(workflow_rel_path: str) -> None:
    """Ensure module workflows import select_module_release, fetch full depth, and avoid old prefix check."""
    repo_root = Path(__file__).resolve().parent.parent
    path = repo_root / workflow_rel_path
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    build_job = parsed["jobs"]["build"]
    build_checkout = next(s for s in build_job["steps"] if s.get("name") == "Checkout Repository")
    assert build_checkout["with"]["fetch-depth"] == 0

    build_step = next(
        s for s in build_job["steps"] if s.get("name") == "Download Release Assets and Build Module"
    )
    run_script = build_step["run"]
    assert "from select_module_release import select_module_release" in run_script
    assert "No release tag found matching prefix" not in run_script
    assert "GIT_CONFIG_KEY_0=safe.directory" in run_script
    assert "except (ValueError, RuntimeError) as exc:" in run_script
