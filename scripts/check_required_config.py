#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Refuse a kernel configuration in which an option this forge claims to build is off.

Usage: check_required_config.py --config <.config> --stream <stream> --arch <arch>
                                [--required kconfig/required-config.json]

kconfig/required-config.json lists what the documentation claims a stream builds, each claim
with the streams and architectures it covers and the symbols that back it. A symbol required
``built`` must be ``=y`` or ``=m``; one required ``y`` must be ``=y``. The survival check
(scripts/kconfig_survival.py) proves that the fragments hold; this check proves that the
fragments still carry what is claimed, so removing a line from a fragment fails the build
instead of silently dropping a claimed driver.

Exit status: 0 when every claim holds, 1 when one does not, 2 when an input is unreadable or
the list is malformed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from verify_kernel_requirement import KconfigError, read_config

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIST = REPO_ROOT / "kconfig" / "required-config.json"
VERSIONS = REPO_ROOT / "versions.json"
SCHEMA = "nucleus.required-config.v1"
ACCEPTED = {"built": ("y", "m"), "y": ("y",)}
CLAIM_KEYS = {"id", "claim", "streams", "architectures", "symbols"}


class ListError(ValueError):
    """The required-config list does not have the shape this checker reads."""


@dataclass(frozen=True)
class Claim:
    """One documented claim and the symbols that back it."""

    ident: str
    text: str
    streams: tuple[str, ...]
    architectures: tuple[str, ...]
    symbols: Mapping[str, str]


@dataclass(frozen=True)
class Miss:
    """A required symbol the configuration does not hold."""

    claim: Claim
    symbol: str
    observed: str | None


def _scope(value: object, known: Sequence[str], field: str, ident: str) -> tuple[str, ...]:
    if value == "all":
        return tuple(known)
    if not isinstance(value, list) or not value:
        raise ListError(f'claim {ident}: {field} must be "all" or a non-empty list')
    unknown = [name for name in value if name not in known]
    if unknown:
        raise ListError(f"claim {ident}: {field} names {unknown}, not in versions.json")
    return tuple(value)


def _claim(raw: object, streams: Sequence[str], arches: Sequence[str]) -> Claim:
    if not isinstance(raw, dict) or set(raw) != CLAIM_KEYS:
        raise ListError(f"a claim must carry exactly the keys {sorted(CLAIM_KEYS)}")
    ident = raw["id"]
    if not isinstance(ident, str) or not ident or not isinstance(raw["claim"], str):
        raise ListError("a claim id and its text must be non-empty strings")
    symbols = raw["symbols"]
    if not isinstance(symbols, dict) or not symbols:
        raise ListError(f"claim {ident}: symbols must be a non-empty object")
    for symbol, need in symbols.items():
        if not symbol.startswith("CONFIG_") or need not in ACCEPTED:
            raise ListError(f"claim {ident}: {symbol} must be a CONFIG_ symbol, built or y")
    return Claim(
        ident,
        raw["claim"],
        _scope(raw["streams"], streams, "streams", ident),
        _scope(raw["architectures"], arches, "architectures", ident),
        dict(symbols),
    )


def load_claims(path: Path, versions: Path = VERSIONS) -> list[Claim]:
    """The claims of the list, each scope checked against the streams and architectures."""
    manifest = json.loads(versions.read_text(encoding="utf-8"))
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise ListError(f"{path} is not a {SCHEMA} document")
    claims = document.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ListError(f"{path} lists no claim")
    parsed = [
        _claim(raw, list(manifest["streams"]), list(manifest["architectures"])) for raw in claims
    ]
    idents = [claim.ident for claim in parsed]
    if len(set(idents)) != len(idents):
        raise ListError(f"{path} repeats a claim id")
    return parsed


def misses(
    claims: Sequence[Claim], config: Mapping[str, str], stream: str, arch: str
) -> list[Miss]:
    """The required symbols of the claims covering stream and arch that config does not hold."""
    found = []
    for claim in claims:
        if stream not in claim.streams or arch not in claim.architectures:
            continue
        for symbol, need in sorted(claim.symbols.items()):
            observed = config.get(symbol)
            if observed not in ACCEPTED[need]:
                found.append(Miss(claim, symbol, observed))
    return found


def _describe(value: str | None) -> str:
    if value is None:
        return " unrecorded"
    return " is not set" if value == "n" else f"={value}"


def report_lines(label: str, applicable: int, found: Sequence[Miss]) -> list[str]:
    if not found:
        return [f"required-config: all {applicable} claims hold for {label}"]
    lines = [f"required-config: {len(found)} claimed options are off for {label}:"]
    for miss in found:
        need = "=y or =m" if miss.claim.symbols[miss.symbol] == "built" else "=y"
        lines.append(
            f"  {miss.symbol}{_describe(miss.observed)}, needs {need}: "
            f"claim {miss.claim.ident} ({miss.claim.text})"
        )
    return lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--config", type=Path, required=True, help="the kernel .config")
    parser.add_argument("--stream", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--required", type=Path, default=DEFAULT_LIST)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        claims = load_claims(args.required)
        config = read_config(args.config)
    except (ListError, KconfigError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"required-config: {exc}", file=sys.stderr)
        return 2
    if not any(args.stream in c.streams and args.arch in c.architectures for c in claims):
        print(f"required-config: no claim covers {args.stream}/{args.arch}", file=sys.stderr)
        return 2
    found = misses(claims, config, args.stream, args.arch)
    applicable = sum(args.stream in c.streams and args.arch in c.architectures for c in claims)
    label = f"{args.stream}/{args.arch} in {args.config}"
    print(
        "\n".join(report_lines(label, applicable, found)), file=sys.stderr if found else sys.stdout
    )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
