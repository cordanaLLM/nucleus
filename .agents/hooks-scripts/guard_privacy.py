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
"""Pre-tool lifecycle hook for Antigravity Agent sandbox.

Asserts zero private RFC 1918 IP addresses or developer workstation paths
before any file write operation executes.

The scanning functions return the violation message instead of ending the
process; only main() decides the exit status (HISS-07). The payload walk is
iterative and bounded (HISS-01, HISS-02).
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

# RFC 1918 private IPv4 patterns
RFC1918_PATTERNS = [
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}\b"),
]

# Workstation private home paths
WORKSTATION_PATH_PATTERNS = [
    re.compile(r"/(?:home|Users)/" + r"[a-zA-Z0-9_-]+/"),
    re.compile(r"[a-zA-Z]:\\Users\\[a-zA-Z0-9_-]+\\"),
]

# Bounds of the payload walk. A tool-call payload nests a few levels deep and holds a
# few dozen values, far below both. A payload beyond either bound is refused, never
# passed with values left unscanned. So is a payload nested so deeply that the JSON
# decoder gives up (RecursionError); main() refuses that one.
MAX_SCAN_DEPTH = 64
MAX_SCAN_VALUES = 100_000


def inspect_text(text: str) -> str | None:
    """Return the violation message for the first prohibited match in text, or None."""
    for pattern in RFC1918_PATTERNS:
        match = pattern.search(text)
        if match:
            return f"SECURITY VIOLATION: Private RFC 1918 IP detected: {match.group(0)}"

    for pattern in WORKSTATION_PATH_PATTERNS:
        match = pattern.search(text)
        if match:
            return f"SECURITY VIOLATION: Local workstation path detected: {match.group(0)}"
    return None


def _children(val: Any) -> list[Any]:
    """Return the values a decoded JSON container holds; dictionary keys are not scanned."""
    if isinstance(val, dict):
        return list(val.values())
    if isinstance(val, list):
        return val
    return []


def scan_payload_values(val: Any) -> str | None:
    """Scan every string in a decoded JSON value and return the first violation, or None.

    The walk visits values depth first, dictionary values in insertion order and list
    items in order, and stops at the first violation. A value nested more than
    MAX_SCAN_DEPTH containers deep, or a payload holding more than MAX_SCAN_VALUES
    values, is refused with a violation message.
    """
    pending: list[tuple[Any, int]] = [(val, 0)]
    for _ in range(MAX_SCAN_VALUES):
        if not pending:
            return None
        node, depth = pending.pop()
        if isinstance(node, str):
            violation = inspect_text(node)
            if violation is not None:
                return violation
            continue
        children = _children(node)
        if children and depth >= MAX_SCAN_DEPTH:
            return (
                f"SECURITY VIOLATION: Payload nests deeper than {MAX_SCAN_DEPTH} levels; "
                "refusing to pass values that were not scanned"
            )
        pending.extend((child, depth + 1) for child in reversed(children))
    if pending:
        return (
            f"SECURITY VIOLATION: Payload holds more than {MAX_SCAN_VALUES} values; "
            "refusing to pass values that were not scanned"
        )
    return None


def main() -> int:
    """Process incoming JSON payload from standard input and return the exit status."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        data = json.loads(raw)
    except RecursionError:
        # The decoder gave up: the payload is JSON, and nothing in it was scanned.
        sys.stderr.write(
            "SECURITY VIOLATION: Payload nests too deeply to decode; "
            "refusing to pass values that were not scanned\n"
        )
        return 1
    except (ValueError, UnicodeDecodeError):
        # If payload is empty or not JSON (JSONDecodeError is a ValueError), allow through
        return 0

    args = data.get("arguments", {})
    violation = scan_payload_values(args)
    if violation is None:
        return 0
    sys.stderr.write(f"{violation}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
