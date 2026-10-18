#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Check that the release directory's SHA256SUMS lists the release UKI and that its digest matches.

Usage:
    python3 scripts/check_release_uki.py --dir=<dir> --stream=<stream> --version=<version>

Complies with NASA/JPL Power of 10: functions <= 60 lines, explicit error handling, type hints.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Sequence
from pathlib import Path

CHECKSUMS_FILE = "SHA256SUMS"
MAX_CHECKSUM_LINES = 256
LINE_PATTERN = re.compile(r"^(?P<digest>[0-9a-f]{64})\s+[ *]?(?P<name>\S+)$")


class CheckReleaseUkiError(Exception):
    """The release UKI check failed."""


def calculate_sha256(path: Path) -> str:
    """Calculate the hex sha256 digest of a file."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def read_checksums(checksums_file: Path) -> list[tuple[str, str]]:
    """Read entries from a SHA256SUMS file."""
    if not checksums_file.is_file() or checksums_file.is_symlink():
        raise CheckReleaseUkiError(f"{checksums_file} is missing or not a regular file")
    text = checksums_file.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise CheckReleaseUkiError(f"{checksums_file} is empty: lists no artifacts")
    if len(lines) > MAX_CHECKSUM_LINES:
        raise CheckReleaseUkiError(
            f"{checksums_file} lists {len(lines)} lines; limit is {MAX_CHECKSUM_LINES}"
        )

    entries: list[tuple[str, str]] = []
    for line in lines:
        match = LINE_PATTERN.match(line)
        if match is None:
            raise CheckReleaseUkiError(f"invalid checksum line format: {line!r}")
        entries.append((match.group("name"), match.group("digest")))
    return entries


def verify_release_uki(release_dir: Path, stream: str, version: str) -> str:
    """Verify that exactly one matching UKI is listed and valid. Returns '<sha256>  <name>'."""
    version_clean = version.replace("~", ".")
    expected_name = f"linux-{stream}-{version_clean}-uki.efi"

    checksums_file = release_dir / CHECKSUMS_FILE
    entries = read_checksums(checksums_file)

    matching_entries = [digest for name, digest in entries if name == expected_name]
    uki_entries = [name for name, _ in entries if name.endswith("-uki.efi")]

    if len(matching_entries) == 0:
        raise CheckReleaseUkiError(f"{expected_name} is not listed in {checksums_file.name}")
    if len(matching_entries) > 1:
        raise CheckReleaseUkiError(
            f"{expected_name} is listed {len(matching_entries)} times in {checksums_file.name}"
        )
    if len(uki_entries) > 1:
        raise CheckReleaseUkiError(
            f"expected exactly one UKI in {checksums_file.name}, found {len(uki_entries)}: {', '.join(uki_entries)}"
        )

    expected_digest = matching_entries[0]
    uki_file = release_dir / expected_name
    if not uki_file.exists():
        raise CheckReleaseUkiError(f"{expected_name} is missing from {release_dir}")
    if not uki_file.is_file() or uki_file.is_symlink():
        raise CheckReleaseUkiError(f"{expected_name} is not a regular file")

    actual_digest = calculate_sha256(uki_file)
    if actual_digest != expected_digest:
        raise CheckReleaseUkiError(
            f"digest mismatch for {expected_name}: listed {expected_digest}, computed {actual_digest}"
        )

    return f"{expected_digest}  {expected_name}"


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify that the release directory's SHA256SUMS lists the release UKI and digest matches."
    )
    parser.add_argument("--dir", type=Path, required=True, help="Release directory")
    parser.add_argument("--stream", type=str, required=True, help="Kernel stream name")
    parser.add_argument("--version", type=str, required=True, help="Package release version")
    args = parser.parse_args(argv)

    if not str(args.stream).strip():
        parser.error("--stream must not be empty")
    if not str(args.version).strip():
        parser.error("--version must not be empty")
    if not args.dir.is_dir():
        parser.error(f"--dir is not an existing directory: {args.dir}")

    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_arguments(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    try:
        result_line = verify_release_uki(args.dir, args.stream, args.version)
        print(result_line)
        return 0
    except (CheckReleaseUkiError, OSError) as exc:
        print(f"check_release_uki: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
