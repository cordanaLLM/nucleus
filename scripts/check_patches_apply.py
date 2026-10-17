#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Check that all carried patches for a kernel stream apply cleanly.

Layout rules:
- patches/common/*.patch apply to every kernel stream.
- patches/<stream>/*.patch apply only to the named stream.
- Every subdirectory under patches/ must be either 'common' or a declared stream in versions.json.
- Every file in a patch subdirectory must match '*.patch'.

Limitation:
Patches are checked each against the pristine tree in sorted order. Because each check runs
with --dry-run against the clean source tree, patches are tested individually against
the pristine tree rather than cumulatively on top of preceding patches in the series.

Exit status:
- 0: All applicable patches apply cleanly, or no patches are carried for the stream.
- 1: Structural error, unknown stream, missing Makefile, or a patch does not apply cleanly.
- 2: CLI syntax error.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATCHES = REPO_ROOT / "patches"
DEFAULT_VERSIONS = REPO_ROOT / "versions.json"
MAX_ENTRIES = 1024


def load_declared_streams(versions_path: Path) -> set[str]:
    """Return stream names declared under 'streams' in versions.json."""
    if not versions_path.is_file():
        raise ValueError(f"{versions_path}: file does not exist")
    data = json.loads(versions_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{versions_path}: versions.json is not a JSON object")
    streams = data.get("streams")
    if not isinstance(streams, dict) or not streams:
        raise ValueError(f"{versions_path}: 'streams' missing or not an object")
    return set(streams.keys())


def validate_patches_layout(patches_dir: Path, declared_streams: set[str]) -> list[str]:
    """Validate directory structure and file naming within patches_dir."""
    problems: list[str] = []
    if not patches_dir.is_dir():
        return problems

    for idx, entry in enumerate(sorted(patches_dir.iterdir())):
        if idx >= MAX_ENTRIES:
            problems.append(f"{patches_dir}: directory holds more than {MAX_ENTRIES} entries")
            break
        if not entry.is_dir():
            problems.append(
                f"{entry}: expected directory, found file (patches must reside in 'common' or '<stream>')"
            )
            continue
        if entry.name != "common" and entry.name not in declared_streams:
            problems.append(f"{entry}: subdirectory is neither 'common' nor a declared stream")
            continue

        for child_idx, child in enumerate(sorted(entry.iterdir())):
            if child_idx >= MAX_ENTRIES:
                problems.append(f"{entry}: subdirectory holds more than {MAX_ENTRIES} entries")
                break
            if child.is_dir():
                problems.append(f"{child}: nested directory not permitted in {entry.name}")
                continue
            if not child.name.endswith(".patch"):
                problems.append(f"{child}: file does not match '*.patch'")

    return problems


def collect_patches_for_stream(patches_dir: Path, stream: str) -> list[Path]:
    """Collect sorted list of patches applicable to the given stream."""
    if not patches_dir.is_dir():
        return []

    common_dir = patches_dir / "common"
    stream_dir = patches_dir / stream

    common_patches = (
        sorted(p for p in common_dir.iterdir() if p.is_file() and p.name.endswith(".patch"))
        if common_dir.is_dir()
        else []
    )
    stream_patches = (
        sorted(p for p in stream_dir.iterdir() if p.is_file() and p.name.endswith(".patch"))
        if stream_dir.is_dir()
        else []
    )
    return common_patches + stream_patches


def check_patch_applies(patch_file: Path, source_tree: Path) -> str | None:
    """Run patch --dry-run against source_tree. Return error string on non-zero exit."""
    cmd = [
        "patch",
        "-p1",
        "--dry-run",
        "--batch",
        "--forward",
        "--silent",
        "-d",
        str(source_tree),
    ]
    try:
        with open(patch_file, "rb") as patch_in:
            proc = subprocess.run(cmd, stdin=patch_in, capture_output=True)
        if proc.returncode != 0:
            detail = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()
            detail = " | ".join(detail.splitlines()[-3:])
            return f"{patch_file}: does not apply to {source_tree}: {detail}"
    except OSError as exc:
        return f"{patch_file}: failed to execute patch command: {exc}"
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--stream", required=True, help="the kernel stream name")
    parser.add_argument("--source-tree", type=Path, required=True, help="path to source tree")
    parser.add_argument(
        "--patches-dir", type=Path, default=DEFAULT_PATCHES, help="patches directory"
    )
    parser.add_argument(
        "--versions", type=Path, default=DEFAULT_VERSIONS, help="versions.json path"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    problems: list[str] = []

    try:
        declared_streams = load_declared_streams(args.versions)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"check_patches_apply: {exc}", file=sys.stderr)
        return 1

    if args.stream not in declared_streams:
        problems.append(f"{args.versions}: unknown stream '{args.stream}'")

    makefile = args.source_tree / "Makefile"
    if not args.source_tree.is_dir() or not makefile.is_file():
        problems.append(f"{makefile}: source tree missing top-level Makefile")

    if args.patches_dir.exists():
        problems.extend(validate_patches_layout(args.patches_dir, declared_streams))

    if problems:
        for prob in problems:
            print(f"check_patches_apply: {prob}", file=sys.stderr)
        return 1

    patches_to_check = collect_patches_for_stream(args.patches_dir, args.stream)
    if not patches_to_check:
        print(f"no carried patches for {args.stream}")
        return 0

    if shutil.which("patch") is None:
        print("check_patches_apply: patch binary not found in PATH", file=sys.stderr)
        return 1

    for patch_file in patches_to_check:
        err = check_patch_applies(patch_file, args.source_tree)
        if err:
            problems.append(err)

    if problems:
        for prob in problems:
            print(f"check_patches_apply: {prob}", file=sys.stderr)
        return 1

    print(f"all {len(patches_to_check)} carried patch(es) apply cleanly for {args.stream}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
