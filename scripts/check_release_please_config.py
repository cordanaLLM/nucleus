#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Refuse a release-please configuration that releases a feature under a patch version.

Below 1.0.0, release-please bumps the patch version for a `feat` commit when
`bump-patch-for-minor-pre-major` is true. The milestones name the next minor
version (0.4.0, 0.5.0), so a release with features must bump the minor version.

Usage:
    python3 scripts/check_release_please_config.py [--config=release-please-config.json]

Exit 0 when no package sets the option, 1 when one does or the file is not a
release-please configuration, 2 on a usage error.

Complies with NASA/JPL Power of 10: functions <= 60 lines, explicit error handling, type hints.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

OPTION = "bump-patch-for-minor-pre-major"
MAX_PACKAGES = 64


def findings(config: object) -> list[str]:
    """Return one message per place the configuration releases a feature as a patch."""
    if not isinstance(config, dict) or not isinstance(config.get("packages"), dict):
        return ["not a release-please configuration: no packages object"]
    packages = config["packages"]
    if len(packages) > MAX_PACKAGES:
        return [f"more than {MAX_PACKAGES} packages"]
    found = []
    if OPTION in config and config[OPTION] is not False:
        found.append(f"top level sets {OPTION}={json.dumps(config[OPTION])}")
    for path, package in packages.items():
        if not isinstance(package, dict):
            found.append(f"package {path!r} is not an object")
        elif OPTION in package and package[OPTION] is not False:
            found.append(f"package {path!r} sets {OPTION}={json.dumps(package[OPTION])}")
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("release-please-config.json"))
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"check_release_please_config: cannot read {args.config}: {exc}", file=sys.stderr)
        return 1
    found = findings(config)
    for message in found:
        print(
            f"check_release_please_config: {args.config}: {message}; a feat would be released as a patch",
            file=sys.stderr,
        )
    if found:
        return 1
    print(f"check_release_please_config: {args.config}: a feat bumps the minor version below 1.0.0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
