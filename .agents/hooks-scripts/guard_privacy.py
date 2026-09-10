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


def inspect_text(text: str) -> None:
    """Inspect text payload for prohibited IP addresses or workstation paths."""
    for pattern in RFC1918_PATTERNS:
        match = pattern.search(text)
        if match:
            sys.stderr.write(
                f"SECURITY VIOLATION: Private RFC 1918 IP detected: {match.group(0)}\n"
            )
            sys.exit(1)

    for pattern in WORKSTATION_PATH_PATTERNS:
        match = pattern.search(text)
        if match:
            sys.stderr.write(
                f"SECURITY VIOLATION: Local workstation path detected: {match.group(0)}\n"
            )
            sys.exit(1)


def scan_payload_values(val: Any) -> None:
    """Recursively scan data structures for text violations."""
    if isinstance(val, str):
        inspect_text(val)
    elif isinstance(val, dict):
        for v in val.values():
            scan_payload_values(v)
    elif isinstance(val, list):
        for item in val:
            scan_payload_values(item)


def main() -> None:
    """Process incoming JSON payload from standard input."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        data = json.loads(raw)
    except Exception:
        # If payload is empty or not JSON, allow through
        sys.exit(0)

    args = data.get("arguments", {})
    scan_payload_values(args)
    sys.exit(0)


if __name__ == "__main__":
    main()
