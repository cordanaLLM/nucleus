#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Check that the carried patches for a kernel stream apply as a series, or apply them.

Layout rules:
- patches/common/*.patch apply to every kernel stream.
- patches/<series>/*.patch apply to the streams a series under 'patches' in versions.json names.
  The files are the series' pinned commits in order: file N starts with 'From <commit N> '.
- patches/<stream>/*.patch apply only to the named stream.
- Every subdirectory under patches/ must be 'common', a declared series or a declared stream.
- Every file in a patch subdirectory must match '*.patch'.

The patches of a stream are common, then each series naming the stream in name order, then the
stream's own, each in sorted order. They apply as one series: patch N on top of patches 1 to N-1,
with no fuzz (-F0) and no backup files.

Without --apply the tree is not touched: the files the patches name are copied to a scratch
directory and the series is applied there. With --apply each patch is dry-run on the tree, then
applied, so a refusal leaves no half-applied patch. The files the series changed get the mtime of
the tree's Makefile, the release time scripts/build_kernel.sh uses as SOURCE_DATE_EPOCH, and
.nucleus-patches.json records each patch and its sha256. A tree that already holds that record is
refused: the series is never applied twice.

Exit status:
- 0: All applicable patches apply cleanly, or no patches are carried for the stream.
- 1: Structural error, unknown stream, missing Makefile, a series that differs from its pin,
  an already patched tree, or a patch that does not apply cleanly.
- 2: CLI syntax error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATCHES = REPO_ROOT / "patches"
DEFAULT_VERSIONS = REPO_ROOT / "versions.json"
MAX_ENTRIES = 1024
MAX_LINES = 1_000_000
RECORD = ".nucleus-patches.json"
PATCH_TIMEOUT = 300
PATH_LINE = re.compile(r"^(?:---|\+\+\+) (?:a|b)/(\S+)")


@dataclass(frozen=True)
class Series:
    name: str
    streams: tuple[str, ...]
    commits: tuple[str, ...]


def load_manifest(versions_path: Path) -> tuple[set[str], dict[str, Series]]:
    """Return the declared streams and patch series of versions.json."""
    if not versions_path.is_file():
        raise ValueError(f"{versions_path}: file does not exist")
    data = json.loads(versions_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{versions_path}: versions.json is not a JSON object")
    streams = data.get("streams")
    if not isinstance(streams, dict) or not streams:
        raise ValueError(f"{versions_path}: 'streams' missing or not an object")
    raw = data.get("patches", {})
    if not isinstance(raw, dict):
        raise ValueError(f"{versions_path}: 'patches' is not an object")
    series: dict[str, Series] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{versions_path}: patches.{name} is not an object")
        names = entry.get("streams")
        commits = entry.get("commits")
        if not isinstance(names, list) or not all(isinstance(s, str) for s in names):
            raise ValueError(f"{versions_path}: patches.{name}.streams is not a list of names")
        if not isinstance(commits, list) or not all(isinstance(c, str) for c in commits):
            raise ValueError(f"{versions_path}: patches.{name}.commits is not a list of commits")
        unknown = sorted(set(names) - set(streams))
        if unknown or name == "common" or name in streams:
            raise ValueError(f"{versions_path}: patches.{name}: bad name or streams {unknown}")
        series[name] = Series(name, tuple(names), tuple(commits))
    return set(streams), series


def load_declared_streams(versions_path: Path) -> set[str]:
    """Return stream names declared under 'streams' in versions.json."""
    return load_manifest(versions_path)[0]


def _patch_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.name.endswith(".patch"))


def validate_patches_layout(
    patches_dir: Path, declared_streams: set[str], series: dict[str, Series] | None = None
) -> list[str]:
    """Validate directory structure and file naming within patches_dir."""
    problems: list[str] = []
    if not patches_dir.is_dir():
        return problems
    allowed = {"common"} | declared_streams | set(series or {})

    for idx, entry in enumerate(sorted(patches_dir.iterdir())):
        if idx >= MAX_ENTRIES:
            problems.append(f"{patches_dir}: directory holds more than {MAX_ENTRIES} entries")
            break
        if not entry.is_dir():
            problems.append(
                f"{entry}: expected directory, found file (patches must reside in 'common', '<series>' or '<stream>')"
            )
            continue
        if entry.name not in allowed:
            problems.append(
                f"{entry}: subdirectory is neither 'common', a declared series nor a declared stream"
            )
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


def validate_series(patches_dir: Path, series: dict[str, Series]) -> list[str]:
    """Refuse a series whose files are not its pinned commits, in order."""
    problems: list[str] = []
    for item in series.values():
        files = _patch_files(patches_dir / item.name)
        if len(files) != len(item.commits):
            problems.append(
                f"{patches_dir / item.name}: holds {len(files)} patches, versions.json pins {len(item.commits)} commits"
            )
            continue
        for patch_file, commit in zip(files, item.commits, strict=True):
            with open(patch_file, encoding="utf-8", errors="replace") as handle:
                first = handle.readline()
            if not first.startswith(f"From {commit} "):
                problems.append(
                    f"{patch_file}: does not start with 'From {commit} ', the commit pinned at its place"
                )
    return problems


def collect_patches_for_stream(
    patches_dir: Path, stream: str, series: dict[str, Series] | None = None
) -> list[Path]:
    """Collect the ordered list of patches applicable to the given stream."""
    if not patches_dir.is_dir():
        return []
    ordered = _patch_files(patches_dir / "common")
    for name in sorted(series or {}):
        if stream in (series or {})[name].streams:
            ordered += _patch_files(patches_dir / name)
    return ordered + _patch_files(patches_dir / stream)


def touched_paths(patch_file: Path) -> list[str]:
    """Return the tree-relative paths a patch reads or writes."""
    found: set[str] = set()
    with open(patch_file, encoding="utf-8", errors="replace") as handle:
        for idx, line in enumerate(handle):
            if idx >= MAX_LINES:
                raise ValueError(f"{patch_file}: more than {MAX_LINES} lines")
            match = PATH_LINE.match(line)
            if match:
                found.add(match.group(1))
    return sorted(found)


def run_patch(patch_file: Path, source_tree: Path, dry_run: bool) -> str | None:
    """Run patch -p1 -F0 against source_tree. Return error string on non-zero exit."""
    cmd = [
        "patch",
        "-p1",
        "-F0",
        "--no-backup-if-mismatch",
        "--reject-file=-",
        "--batch",
        "--forward",
        "--silent",
    ]
    if dry_run:
        cmd.append("--dry-run")
    cmd += ["-d", str(source_tree)]
    try:
        with open(patch_file, "rb") as patch_in:
            proc = subprocess.run(cmd, stdin=patch_in, capture_output=True, timeout=PATCH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{patch_file}: failed to execute patch command: {exc}"
    if proc.returncode != 0:
        detail = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()
        detail = " | ".join(detail.splitlines()[-3:])
        return f"{patch_file}: does not apply to {source_tree}: {detail}"
    return None


def check_patch_applies(patch_file: Path, source_tree: Path) -> str | None:
    """Dry-run one patch against source_tree. Return error string on non-zero exit."""
    return run_patch(patch_file, source_tree, dry_run=True)


def check_series(patches: Sequence[Path], source_tree: Path) -> list[str]:
    """Apply the series to a scratch copy of the files it names; the tree is not touched."""
    with tempfile.TemporaryDirectory(prefix="nucleus-patches-") as scratch:
        root = Path(scratch)
        for patch_file in patches:
            for rel in touched_paths(patch_file):
                source = source_tree / rel
                if source.is_file() and not (root / rel).exists():
                    (root / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, root / rel)
        for patch_file in patches:
            err = run_patch(patch_file, root, dry_run=False)
            if err:
                return [err.replace(str(root), str(source_tree))]
    return []


def apply_series(patches: Sequence[Path], source_tree: Path) -> list[str]:
    """Apply the series to source_tree, pin the changed files' mtime and write the record."""
    record = source_tree / RECORD
    if record.exists():
        return [f"{record}: the tree is already patched; fetch a fresh tree"]
    epoch = (source_tree / "Makefile").stat().st_mtime
    applied: list[dict[str, str]] = []
    changed: set[str] = set()
    for patch_file in patches:
        err = run_patch(patch_file, source_tree, dry_run=True) or run_patch(
            patch_file, source_tree, dry_run=False
        )
        if err:
            done = ", ".join(entry["patch"] for entry in applied) or "none"
            return [f"{err} (applied before it: {done})"]
        digest = hashlib.sha256(patch_file.read_bytes()).hexdigest()
        applied.append({"patch": f"{patch_file.parent.name}/{patch_file.name}", "sha256": digest})
        changed.update(touched_paths(patch_file))
    parents = {str(parent) for path in changed for parent in Path(path).parents}
    for rel in sorted(changed | parents):
        if rel != "." and (source_tree / rel).exists():
            os.utime(source_tree / rel, (epoch, epoch))
    record.write_text(json.dumps(applied, indent=2) + "\n", encoding="utf-8")
    os.utime(record, (epoch, epoch))
    return []


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
    parser.add_argument(
        "--apply", action="store_true", help="apply the series to the tree instead of checking it"
    )
    return parser


def _structure(args: argparse.Namespace) -> tuple[list[str], dict[str, Series]]:
    try:
        declared_streams, series = load_manifest(args.versions)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)], {}
    problems: list[str] = []
    if args.stream not in declared_streams:
        problems.append(f"{args.versions}: unknown stream '{args.stream}'")
    makefile = args.source_tree / "Makefile"
    if not args.source_tree.is_dir() or not makefile.is_file():
        problems.append(f"{makefile}: source tree missing top-level Makefile")
    if args.patches_dir.exists():
        problems.extend(validate_patches_layout(args.patches_dir, declared_streams, series))
        problems.extend(validate_series(args.patches_dir, series))
    return problems, series


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    problems, series = _structure(args)
    if problems:
        for prob in problems:
            print(f"check_patches_apply: {prob}", file=sys.stderr)
        return 1

    patches = collect_patches_for_stream(args.patches_dir, args.stream, series)
    if not patches:
        print(f"no carried patches for {args.stream}")
        return 0

    if shutil.which("patch") is None:
        print("check_patches_apply: patch binary not found in PATH", file=sys.stderr)
        return 1

    try:
        problems = (
            apply_series(patches, args.source_tree)
            if args.apply
            else check_series(patches, args.source_tree)
        )
    except (OSError, ValueError) as exc:
        problems = [str(exc)]
    if problems:
        for prob in problems:
            print(f"check_patches_apply: {prob}", file=sys.stderr)
        return 1

    verb = "applied" if args.apply else "apply cleanly"
    print(f"all {len(patches)} carried patch(es) {verb} for {args.stream}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
