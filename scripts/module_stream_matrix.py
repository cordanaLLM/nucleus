#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Compute the target kernel stream matrix for an out-of-tree module in versions.json.

Reads ``versions.json``, evaluates the compatibility rule for the requested module
(``linux_minimum``..``linux_maximum`` major.minor range or explicit ``streams`` list),
writes the markdown compatibility report to ``GITHUB_STEP_SUMMARY`` when set, and
writes ``matrix={"stream": [...]}`` to ``GITHUB_OUTPUT`` when set (and stdout).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_MAJ_MIN = re.compile(r"^(\d+)\.(\d+)")
_MAX_STREAMS = 32


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Parse JSON object rejecting any duplicate key."""
    result: dict[str, Any] = {}
    for key, val in pairs:
        if key in result:
            raise ValueError(f"duplicate key in JSON: {key!r}")
        result[key] = val
    return result


def parse_maj_min(version_str: str) -> tuple[int, int]:
    """Extract (major, minor) integers from a kernel version or bound string."""
    if not isinstance(version_str, str):
        raise ValueError(f"cannot parse version {version_str!r}")
    match = _MAJ_MIN.match(version_str)
    if not match:
        raise ValueError(f"cannot parse version {version_str}")
    return int(match.group(1)), int(match.group(2))


def _range_plan(
    module_name: str,
    mod: Mapping[str, Any],
    streams_map: Mapping[str, Any],
) -> tuple[list[str], str]:
    """Compute in-range streams and step summary for a linux_minimum..linux_maximum module."""
    if "linux_minimum" not in mod or "linux_maximum" not in mod:
        raise ValueError(f"modules.{module_name} must declare both linux_minimum and linux_maximum")
    min_str = str(mod["linux_minimum"])
    max_str = str(mod["linux_maximum"])
    min_ver = parse_maj_min(min_str)
    max_ver = parse_maj_min(max_str)
    if min_ver > max_ver:
        raise ValueError(
            f"modules.{module_name}: linux_minimum {min_str} > linux_maximum {max_str}"
        )

    in_range: list[str] = []
    out_of_range: list[tuple[str, str]] = []
    for stream, info in streams_map.items():
        if not isinstance(info, dict) or "version" not in info:
            raise ValueError(f"streams.{stream}.version missing")
        ver_str = str(info["version"])
        maj_min = parse_maj_min(ver_str)
        if min_ver <= maj_min <= max_ver:
            in_range.append(stream)
        else:
            out_of_range.append((stream, ver_str))

    label = "OpenZFS" if module_name == "openzfs" else module_name
    version = str(mod.get("version", "unknown"))
    summary_lines = [
        f"### {label} {version} Module Stream Compatibility",
        f"- {label} version: `{version}`",
        f"- Supported Linux range: `[{min_str}, {max_str}]`",
        "",
        "#### Supported Streams (Matrix)",
    ]
    for stream in in_range:
        summary_lines.append(f"- `{stream}` (`{streams_map[stream]['version']}`)")
    if out_of_range:
        summary_lines.append("")
        summary_lines.append("#### Out of Range Streams (Skipped)")
        for stream, ver in out_of_range:
            summary_lines.append(f"- `{stream}` (`{ver}` is outside `[{min_str}, {max_str}]`)")
    return in_range, "\n".join(summary_lines) + "\n"


def _streams_list_plan(
    module_name: str,
    mod: Mapping[str, Any],
    streams_map: Mapping[str, Any],
) -> tuple[list[str], str]:
    """Compute target streams and step summary for an explicit-streams module."""
    raw_streams = mod.get("streams")
    if not isinstance(raw_streams, list) or not raw_streams:
        raise ValueError(f"modules.{module_name}.streams missing or empty")
    if len(raw_streams) > _MAX_STREAMS:
        raise ValueError(f"modules.{module_name}.streams lists more than {_MAX_STREAMS} streams")

    target_streams: list[str] = []
    for stream in raw_streams:
        if not isinstance(stream, str) or stream not in streams_map:
            raise ValueError(f"modules.{module_name}.streams names unknown stream {stream!r}")
        target_streams.append(stream)

    version = str(mod.get("version", "unknown"))
    source = mod.get("source")
    commit = str(source.get("commit", "unknown")) if isinstance(source, dict) else "unknown"
    summary_lines = [
        f"### NVIDIA Open GPU Kernel Module {version} Stream Compatibility",
        f"- NVIDIA Open version: `{version}`",
        f"- Pinned commit: `{commit}`",
        "",
        "#### Target Streams (Matrix)",
    ]
    for stream in target_streams:
        stream_info = streams_map.get(stream, {})
        stream_ver = (
            str(stream_info.get("version", "unknown"))
            if isinstance(stream_info, dict)
            else "unknown"
        )
        summary_lines.append(f"- `{stream}` (`{stream_ver}`)")
    return target_streams, "\n".join(summary_lines) + "\n"


def compute_module_matrix(
    versions: Mapping[str, Any],
    module_name: str,
) -> tuple[dict[str, list[str]], str]:
    """Return (matrix_dict, summary_markdown) for ``modules.<module_name>``."""
    modules = versions.get("modules")
    if not isinstance(modules, dict) or module_name not in modules:
        raise ValueError(f"modules.{module_name} missing from versions.json")
    mod = modules[module_name]
    if not isinstance(mod, dict):
        raise ValueError(f"modules.{module_name} must be an object")

    streams_map = versions.get("streams")
    if not isinstance(streams_map, dict) or not streams_map:
        raise ValueError("streams missing or empty in versions.json")
    if len(streams_map) > _MAX_STREAMS:
        raise ValueError(
            f"versions.json declares {len(streams_map)} streams, more than {_MAX_STREAMS}"
        )

    if "linux_minimum" in mod or "linux_maximum" in mod:
        streams, summary = _range_plan(module_name, mod, streams_map)
    elif "streams" in mod:
        streams, summary = _streams_list_plan(module_name, mod, streams_map)
    else:
        raise ValueError(
            f"modules.{module_name} must declare linux_minimum/linux_maximum or streams"
        )
    return {"stream": streams}, summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--module",
        required=True,
        help="Module key under versions.json 'modules' (e.g. openzfs, nvidia_open)",
    )
    parser.add_argument(
        "--versions",
        type=Path,
        default=Path("versions.json"),
        help="Path to versions.json manifest (default: versions.json)",
    )
    parser.add_argument(
        "--github-output",
        default=None,
        help="File path to append matrix=<json> (defaults to GITHUB_OUTPUT env var)",
    )
    parser.add_argument(
        "--step-summary",
        default=None,
        help="File path to append Markdown summary (defaults to GITHUB_STEP_SUMMARY env var)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    args = _parser().parse_args(argv)
    try:
        raw = args.versions.read_text(encoding="utf-8")
        versions = json.loads(raw, object_pairs_hook=_unique_keys)
        if not isinstance(versions, dict):
            raise ValueError("versions.json is not a JSON object")
        matrix, summary = compute_module_matrix(versions, args.module)
    except (OSError, ValueError) as exc:
        print(f"module_stream_matrix: {exc}", file=sys.stderr)
        return 1

    summary_path = (
        args.step_summary
        if args.step_summary is not None
        else os.environ.get("GITHUB_STEP_SUMMARY")
    )
    if summary_path:
        Path(summary_path).parent.mkdir(parents=True, exist_ok=True)
        with Path(summary_path).open("a", encoding="utf-8") as sf:
            sf.write(summary)

    line = f"matrix={json.dumps(matrix)}"
    output_path = (
        args.github_output if args.github_output is not None else os.environ.get("GITHUB_OUTPUT")
    )
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with Path(output_path).open("a", encoding="utf-8") as of:
            of.write(f"{line}\n")

    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
