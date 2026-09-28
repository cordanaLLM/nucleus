#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Check that every symbol the kconfig fragments request survives into the resolved .config.

``make olddefconfig`` drops or changes a requested value, silently, when the symbol's
dependencies are unmet, when a ``select`` or a range overrides it, or when the symbol does
not exist in the tree. A fragment line that did not survive configures nothing, so the
resolved configuration is refused rather than published.

Usage: kconfig_survival.py --config <resolved .config> <fragment> [<fragment> ...]

The fragments are read in merge order with the grammar scripts/merge-config.sh accepts, the
last line for a symbol winning. Every ``CONFIG_X=value`` and every ``# CONFIG_X is not set``
that remains must appear with the same value in the resolved .config; an unset request is met
only by the resolved ``# CONFIG_X is not set`` line, never by the symbol being unrecorded.
Exit status: 0 when every request holds, 1 when one does not, 2 when an input is unreadable.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from verify_kernel_requirement import KconfigError, read_config


@dataclass(frozen=True)
class Request:
    """One value a fragment asks for: the last line for the symbol across the fragments."""

    symbol: str
    value: str
    fragment: str


@dataclass(frozen=True)
class Casualty:
    """A request the resolved configuration does not hold, and what it holds instead."""

    request: Request
    resolved: str | None


def requested(fragments: Sequence[Path]) -> dict[str, Request]:
    """Every symbol the fragments request, in merge order, the last fragment winning."""
    requests: dict[str, Request] = {}
    for fragment in fragments:
        for symbol, value in read_config(fragment).items():
            requests[symbol] = Request(symbol, value, str(fragment))
    return requests


def casualties(requests: Mapping[str, Request], resolved: Mapping[str, str]) -> list[Casualty]:
    """The requests whose symbol the resolved configuration records differently or not at all."""
    lost = []
    for symbol in sorted(requests):
        request = requests[symbol]
        observed = resolved.get(symbol)
        if observed != request.value:
            lost.append(Casualty(request, observed))
    return lost


def describe(value: str | None) -> str:
    """A value as the report shows it: ``=y``, ``is not set``, or unrecorded."""
    if value is None:
        return "unrecorded (unmet dependencies, or no such symbol in this tree)"
    return "is not set" if value == "n" else f"={value}"


def report_lines(
    config: str, requests: Mapping[str, Request], lost: Sequence[Casualty]
) -> list[str]:
    if not lost:
        return [f"survival: all {len(requests)} requested symbols hold in {config}"]
    lines = [f"survival: {len(lost)} of {len(requests)} requested symbols do not hold in {config}:"]
    for casualty in lost:
        request = casualty.request
        lines.append(
            f"  {request.symbol}: requested {describe(request.value)} by {request.fragment}, "
            f"resolved {describe(casualty.resolved)}"
        )
    return lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--config", type=Path, required=True, help="the resolved .config")
    parser.add_argument("fragments", type=Path, nargs="+", help="the fragments, in merge order")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        requests = requested(args.fragments)
        resolved = read_config(args.config)
    except (KconfigError, OSError, UnicodeDecodeError) as exc:
        print(f"survival: {exc}", file=sys.stderr)
        return 2
    lost = casualties(requests, resolved)
    stream = sys.stderr if lost else sys.stdout
    print("\n".join(report_lines(str(args.config), requests, lost)), file=stream)
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main())
