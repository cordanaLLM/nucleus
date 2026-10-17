#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Atheris fuzz harness for the downstream kernel requirement and kconfig parser.

Exercises scripts/verify_kernel_requirement.py:
- parse_requirement: must only raise RequirementError on malformed or invalid documents.
- parse_config_text: must only raise KconfigError on invalid configuration text.
- release_components and at_least: must never raise on arbitrary inputs, with reflexive
  release comparison always returning True.

Any unexpected exception or assertion failure propagates as a crash signal.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import verify_kernel_requirement as vkr  # noqa: E402


def test_one_input(data: bytes) -> None:
    """Exercise requirement parsing, config parsing, and release version comparison."""
    # (a) parse_requirement(data), catch RequirementError only
    try:
        vkr.parse_requirement(data)
    except vkr.RequirementError:
        pass

    # (b) decode data utf-8 errors="surrogateescape" -> text; parse_config_text(text, "fuzz"), catch KconfigError only
    text = data.decode("utf-8", errors="surrogateescape")
    try:
        vkr.parse_config_text(text, "fuzz")
    except vkr.KconfigError:
        pass

    # (c) release_components(text) must return tuple of ints, len <= vkr.MAX_RELEASE_COMPONENTS, every int >= 0;
    # at_least(text, text) must be True (reflexive); assert these with explicit if ...: raise AssertionError(...)
    components = vkr.release_components(text)
    if not isinstance(components, tuple):
        raise AssertionError(f"release_components returned {type(components)}, expected tuple")
    if len(components) > vkr.MAX_RELEASE_COMPONENTS:
        raise AssertionError(
            f"release_components len {len(components)} exceeds MAX_RELEASE_COMPONENTS {vkr.MAX_RELEASE_COMPONENTS}"
        )
    for comp in components:
        if type(comp) is not int or comp < 0:
            raise AssertionError(f"release_components element is not a non-negative int: {comp!r}")

    if vkr.at_least(text, text) is not True:
        raise AssertionError(f"at_least({text!r}, {text!r}) is not True")


def main(argv: list[str]) -> int:
    """Run the Atheris fuzzer with command-line arguments."""
    import atheris

    # Instrument every loaded function, the parser's private helpers included: instrumenting
    # only the entry points leaves the fuzzer blind to the branches it is meant to explore.
    atheris.instrument_all()
    atheris.Setup(argv, test_one_input)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
