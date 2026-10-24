#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Select published releases for kernel package lifecycle testing.

Selects the two newest published releases (or one if only a single release
exists) for a kernel stream declared in versions.json. When a stream version
has been bumped in versions.json but no release of that version is published
yet, the script temporarily substitutes the most recently published version
of that stream for up to 7 days after the bump commit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MAX_GIT_LOG_COMMITS = 500
SEVEN_DAYS = timedelta(days=7)


@dataclass(frozen=True)
class SelectionResult:
    """Result of published release selection."""

    stream: str
    version: str
    release_tag: str
    previous_tag: str
    is_substituted: bool
    summary_lines: tuple[str, ...]
    substituted_version: str | None = None
    release_rev: int | None = None
    previous_rev: int | None = None


def _normalize_tz(dt: datetime) -> datetime:
    """Ensure datetime has timezone information set to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_published_at(value: str) -> datetime:
    """Parse ISO 8601 publishedAt string into timezone-aware datetime."""
    try:
        dt = datetime.fromisoformat(value)
        return _normalize_tz(dt)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=UTC)


def _format_current_selection(
    stream: str,
    items: list[dict[str, Any]],
) -> SelectionResult:
    """Format SelectionResult when releases of the current version exist."""
    if len(items) == 1:
        rel_rev = items[0]["rev"]
        rel_tag = items[0]["tag"]
        return SelectionResult(
            stream=stream,
            version=items[0]["ver"],
            release_tag=rel_tag,
            previous_tag="",
            is_substituted=False,
            summary_lines=(
                f"Upgrade test was not exercised (only 1 release published: `{rel_tag}`).",
            ),
            release_rev=rel_rev,
            previous_rev=None,
        )

    prev_rev = items[-2]["rev"]
    prev_tag = items[-2]["tag"]
    rel_rev = items[-1]["rev"]
    rel_tag = items[-1]["tag"]
    return SelectionResult(
        stream=stream,
        version=items[-1]["ver"],
        release_tag=rel_tag,
        previous_tag=prev_tag,
        is_substituted=False,
        summary_lines=(),
        release_rev=rel_rev,
        previous_rev=prev_rev,
    )


def _format_substituted_selection(
    stream: str,
    target_version: str,
    substituted_version: str,
    items: list[dict[str, Any]],
    bump_time: datetime,
) -> SelectionResult:
    """Format SelectionResult when substituting an older version."""
    bump_str = bump_time.strftime("%Y-%m-%d")
    refused_str = (bump_time + SEVEN_DAYS).strftime("%Y-%m-%d")
    v_old = f"v{substituted_version.lstrip('v')}"

    if len(items) == 1:
        rel_rev = items[0]["rev"]
        rel_tag = items[0]["tag"]
        prev_rev = None
        prev_tag = ""
        tags_str = rel_tag
        extra_summary = (
            f"Upgrade test was not exercised (only 1 release published: `{rel_tag}`).",
        )
    else:
        prev_rev = items[-2]["rev"]
        prev_tag = items[-2]["tag"]
        rel_rev = items[-1]["rev"]
        rel_tag = items[-1]["tag"]
        tags_str = f"{prev_tag}, {rel_tag}"
        extra_summary = ()

    subst_line = (
        f"Substitution: versions.json names {stream} {target_version}, which has no published release; "
        f"selected {v_old} releases {tags_str} instead (bump {bump_str}, refused after {refused_str})."
    )
    return SelectionResult(
        stream=stream,
        version=target_version,
        release_tag=rel_tag,
        previous_tag=prev_tag,
        is_substituted=True,
        summary_lines=(subst_line, *extra_summary),
        substituted_version=substituted_version,
        release_rev=rel_rev,
        previous_rev=prev_rev,
    )


def select_lifecycle_releases(
    stream: str,
    version: str,
    releases: Sequence[Mapping[str, Any]],
    bump_time: datetime,
    now: datetime,
) -> SelectionResult:
    """Select target releases for stream package lifecycle testing."""
    norm_bump = _normalize_tz(bump_time)
    norm_now = _normalize_tz(now)

    pattern = re.compile(rf"^v(?P<ver>.+)-{re.escape(stream)}-lusoris(?P<rev>[0-9]+)$")
    matching: list[dict[str, Any]] = []
    for item in releases:
        tag = str(item.get("tagName", ""))
        match = pattern.match(tag)
        if match:
            matching.append(
                {
                    "ver": match.group("ver"),
                    "rev": int(match.group("rev")),
                    "tag": tag,
                    "publishedAt": str(item.get("publishedAt", "")),
                }
            )

    current_items = [it for it in matching if it["ver"] == version]
    if current_items:
        current_items.sort(key=lambda x: x["rev"])
        return _format_current_selection(stream, current_items)

    if norm_now - norm_bump > SEVEN_DAYS:
        bump_str = norm_bump.strftime("%Y-%m-%d")
        refused_str = (norm_bump + SEVEN_DAYS).strftime("%Y-%m-%d")
        raise ValueError(
            f"versions.json stream {stream!r} version {version} bumped on {bump_str} "
            f"exceeds 7-day bound without a published release (refused after {refused_str})"
        )

    other_items = [it for it in matching if it["ver"] != version]
    if not other_items:
        raise ValueError(f"no published releases found matching v{version}-{stream}-lusoris<N>")

    newest_other = max(other_items, key=lambda x: _parse_published_at(x["publishedAt"]))
    old_version = newest_other["ver"]
    subst_items = [it for it in matching if it["ver"] == old_version]
    subst_items.sort(key=lambda x: x["rev"])
    return _format_substituted_selection(stream, version, old_version, subst_items, norm_bump)


def find_bump_time(
    stream: str,
    current_version: str,
    repo_dir: Path | None = None,
    now: datetime | None = None,
) -> datetime:
    """Find datetime when versions.json first introduced current_version for stream."""
    fallback_now = _normalize_tz(now or datetime.now(UTC))
    base_dir = repo_dir if repo_dir is not None else Path.cwd()
    cmd = ["git", "log", "--format=%H %cI", "-n", str(MAX_GIT_LOG_COMMITS), "--", "versions.json"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        # A missing history would silently disable the 7-day bound: refuse instead.
        raise RuntimeError(f"cannot read versions.json history with git log: {exc}") from exc

    lines = proc.stdout.strip().splitlines()
    if not lines:
        return fallback_now

    oldest_date: datetime | None = None
    for idx, line in enumerate(lines):
        if idx >= MAX_GIT_LOG_COMMITS:
            break
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        sha, date_str = parts
        try:
            show_proc = subprocess.run(
                ["git", "show", f"{sha}:versions.json"],
                cwd=base_dir,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            data = json.loads(show_proc.stdout)
            ver = data.get("streams", {}).get(stream, {}).get("version")
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read versions.json at {sha}: {exc}") from exc

        if ver == current_version:
            try:
                oldest_date = _normalize_tz(datetime.fromisoformat(date_str))
            except ValueError:
                break
        else:
            break

    return oldest_date if oldest_date is not None else fallback_now


def fetch_releases(
    repo: str | None = None,
    timeout_seconds: int = 30,
) -> list[dict[str, Any]]:
    """Fetch published releases from GitHub CLI."""
    cmd = ["gh", "release", "list", "--limit", "100", "--json", "tagName,publishedAt"]
    if repo:
        cmd.extend(["-R", repo])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=True,
    )
    data = json.loads(proc.stdout)
    if not isinstance(data, list):
        raise ValueError("unexpected output from gh release list; expected JSON array")
    return data


def _emit_outputs(result: SelectionResult, stream: str) -> None:
    """Emit selection results to stdout and GitHub Actions files."""
    if result.is_substituted and result.summary_lines:
        print(result.summary_lines[0])

    if result.previous_tag and result.previous_rev is not None and result.release_rev is not None:
        print(
            f"Selected releases for stream {stream}: "
            f"previous={result.previous_tag} (rev {result.previous_rev}), "
            f"release={result.release_tag} (rev {result.release_rev})"
        )
    elif result.previous_tag:
        print(
            f"Selected releases for stream {stream}: "
            f"previous={result.previous_tag}, release={result.release_tag}"
        )
    else:
        print(f"Found single release for stream {stream}: {result.release_tag}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"release_tag={result.release_tag}\n")
            f.write(f"previous_tag={result.previous_tag}\n")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary and result.summary_lines:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(f"### Stream `{stream}`\n")
            for line in result.summary_lines:
                f.write(f"{line}\n")


def _parser() -> argparse.ArgumentParser:
    """Construct argument parser for release selection CLI."""
    parser = argparse.ArgumentParser(
        description="Select published releases for package lifecycle verification."
    )
    parser.add_argument(
        "--stream",
        required=True,
        help="Kernel stream name declared in versions.json (e.g. mainstream, realtime)",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="GitHub repository (owner/repo) to query releases from",
    )
    parser.add_argument(
        "--versions-file",
        type=Path,
        default=Path("versions.json"),
        help="Path to versions.json file",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for select_lifecycle_releases CLI."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        versions_path = args.versions_file
        if not versions_path.exists():
            raise FileNotFoundError(f"versions file not found: {versions_path}")
        versions_data = json.loads(versions_path.read_text(encoding="utf-8"))
        stream_info = versions_data.get("streams", {}).get(args.stream)
        if not stream_info:
            raise ValueError(f"unknown stream {args.stream!r} in versions.json")
        current_version = stream_info.get("version")
        if not current_version:
            raise ValueError(f"no version declared for stream {args.stream!r}")

        now = datetime.now(UTC)
        bump_time = find_bump_time(
            stream=args.stream,
            current_version=current_version,
            repo_dir=versions_path.parent,
            now=now,
        )
        releases = fetch_releases(repo=args.repo)
        result = select_lifecycle_releases(
            stream=args.stream,
            version=current_version,
            releases=releases,
            bump_time=bump_time,
            now=now,
        )

        _emit_outputs(result, args.stream)
    except Exception as exc:
        print(f"select_lifecycle_releases: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
