#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Select published kernel release for out-of-tree module builds.

Selects the target release for module compilation. If a published tag is
specified, it validates the tag against the stream and available releases
without substitution. Otherwise, it delegates to select_lifecycle_releases to
select the newest release of the declared version or temporarily substitute
an older release within the 7-day bump window.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from scripts.select_lifecycle_releases import select_lifecycle_releases
except ImportError:
    from select_lifecycle_releases import select_lifecycle_releases  # type: ignore[no-redef]

MAX_RELEASES = 500


def _validate_published_tag(
    stream: str,
    published_tag: str,
    releases: Sequence[Mapping[str, Any]],
) -> tuple[str, tuple[str, ...]]:
    """Validate published tag against stream pattern and available releases."""
    pattern = re.compile(rf"^v(?P<ver>.+)-{re.escape(stream)}-lusoris\d+$")
    if not pattern.match(published_tag):
        raise ValueError(f"published tag {published_tag!r} does not match stream {stream!r} format")

    found = False
    for idx, item in enumerate(releases):
        if idx >= MAX_RELEASES:
            break
        tag = item.get("tag_name") or item.get("tagName")
        if tag == published_tag:
            found = True
            break

    if not found:
        raise ValueError(f"published tag {published_tag!r} not found among releases")

    return published_tag, ()


def select_module_release(
    stream: str,
    version_tag_form: str,
    releases: Sequence[Mapping[str, Any]],
    published_tag: str | None,
    bump_time: datetime,
    now: datetime,
) -> tuple[str, tuple[str, ...]]:
    """Select the target release tag and summary lines for a module build."""
    if published_tag and published_tag.strip():
        return _validate_published_tag(stream, published_tag.strip(), releases)

    mapped_releases: list[dict[str, Any]] = []
    for idx, r in enumerate(releases):
        if idx >= MAX_RELEASES:
            break
        tag = str(r.get("tag_name") or r.get("tagName") or "")
        pub = str(r.get("published_at") or r.get("publishedAt") or "")
        mapped_releases.append({"tagName": tag, "publishedAt": pub})

    result = select_lifecycle_releases(
        stream=stream,
        version=version_tag_form,
        releases=mapped_releases,
        bump_time=bump_time,
        now=now,
    )

    summary_lines: tuple[str, ...] = ()
    if result.is_substituted:
        summary_lines = tuple(
            line for line in result.summary_lines if line.startswith("Substitution:")
        )
        if not summary_lines and result.summary_lines:
            summary_lines = (result.summary_lines[0],)

    return result.release_tag, summary_lines
