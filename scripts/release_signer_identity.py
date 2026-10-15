#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Determine the expected cosign certificate signer identity for a release tag.

Kernel releases tagged up to the transition to SLSA Build Level 3 were built and
signed directly by ``publish-release.yml``. Releases following the transition are
built, attested, and signed inside the reusable ``release-build.yml`` workflow.

This script reads the authoritative ``release-signers.json`` mapping and prints
the exact OIDC certificate identity expected for a given repository and release
tag, ensuring legacy tags and new tags are strictly partitioned without pattern
wildcarding.

Complies with NASA/JPL Power of 10 rules: functions <= 60 lines, no recursion,
no unbounded loops, explicit error handling.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO_ROOT / "release-signers.json"
EXPECTED_SCHEMA = "nucleus.release-signers.v1"
KNOWN_KEYS = {"schema", "legacy_workflow", "workflow", "legacy_tags"}

TAG_PATTERN = re.compile(r"^v[0-9][0-9A-Za-z.~-]*-[a-z]+-lusoris[1-9][0-9]{0,3}$")
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Parse JSON object rejecting any duplicate key."""
    result: dict[str, Any] = {}
    for key, val in pairs:
        if key in result:
            raise ValueError(f"duplicate key in JSON: {key!r}")
        result[key] = val
    return result


def load_signers_config(path: Path) -> dict[str, Any]:
    """Load and validate release-signers.json."""
    if not path.is_file():
        raise ValueError(f"release signers config file not found: {path}")
    raw_text = path.read_text(encoding="utf-8")
    data = json.loads(raw_text, object_pairs_hook=_unique_keys)
    if not isinstance(data, dict):
        raise ValueError("release signers configuration must be a JSON object")

    unknown_keys = set(data.keys()) - KNOWN_KEYS
    if unknown_keys:
        raise ValueError(f"unknown keys in release signers config: {sorted(unknown_keys)}")

    if data.get("schema") != EXPECTED_SCHEMA:
        raise ValueError(
            f"invalid schema: expected {EXPECTED_SCHEMA!r}, got {data.get('schema')!r}"
        )

    for field in ("legacy_workflow", "workflow"):
        val = data.get(field)
        if not isinstance(val, str) or not val.endswith(".yml"):
            raise ValueError(f"field {field!r} must be a valid workflow filename ending in .yml")

    legacy_tags = data.get("legacy_tags")
    if not isinstance(legacy_tags, list) or not all(isinstance(t, str) for t in legacy_tags):
        raise ValueError("field 'legacy_tags' must be a list of strings")

    return data


def resolve_signer_identity(
    repo: str,
    tag: str,
    server: str = "https://github.com",
    data_path: Path = DEFAULT_DATA,
) -> str:
    """Resolve the certificate identity string for a given repo and tag."""
    if not REPO_PATTERN.fullmatch(repo):
        raise ValueError(f"malformed repository identifier: {repo!r}")
    if not TAG_PATTERN.fullmatch(tag):
        raise ValueError(f"malformed kernel release tag: {tag!r}")

    config = load_signers_config(data_path)
    clean_server = server.rstrip("/")
    legacy_tags = set(config["legacy_tags"])

    workflow = config["legacy_workflow"] if tag in legacy_tags else config["workflow"]
    return f"{clean_server}/{repo}/.github/workflows/{workflow}@refs/tags/{tag}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Print expected cosign certificate signer identity for a release tag."
    )
    parser.add_argument(
        "--repo", required=True, help="Repository owner/name (e.g. cordanaLLM/nucleus)"
    )
    parser.add_argument(
        "--tag", required=True, help="Release tag (e.g. v7.2.10-mainstream-lusoris2)"
    )
    parser.add_argument(
        "--server",
        default="https://github.com",
        help="GitHub server URL (default: https://github.com)",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA,
        help="Path to release-signers.json data file",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        identity = resolve_signer_identity(
            repo=args.repo,
            tag=args.tag,
            server=args.server,
            data_path=args.data,
        )
        print(identity)
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"release_signer_identity error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
