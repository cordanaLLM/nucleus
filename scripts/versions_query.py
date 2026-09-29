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
"""Print the versions.json fields a shell script needs, one KEY=VALUE per line.

The shell scripts that fetch and configure a kernel read versions.json through this module,
so every value they place on a command line has been checked against its shape first:

    versions_query.py source <stream>   the stream's source, the kernelversion it carries and
                                        the Debian upstream version its packages carry
    versions_query.py arch <arch>       the architecture's ARCH, base defconfig, toolchain and
                                        Debian architecture

A value outside its shape, an unknown stream or an unknown architecture is refused with
exit status 2 and nothing on standard output. versions.schema.json states the same shapes
for the committed file; this module holds them for any file it is pointed at, including the
throwaway manifests the tests build, which may name a file:// URL.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

_URL = re.compile(r"(?:https|file)://[!-~]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_FINGERPRINT = re.compile(r"[0-9A-F]{40}")
_TAG = re.compile(r"v[0-9][0-9A-Za-z.-]*")
_VERSION = re.compile(r"([0-9]+)[.]([0-9]+)(?:[.]([0-9]+))?(-rc[0-9]+)?")
_RC = re.compile(r"-(rc[1-9])")
_NAME = re.compile(r"[a-z0-9_]+")
_DEFCONFIG = re.compile(r"[a-z0-9_]*defconfig")
_PREFIX = re.compile(r"[a-z0-9_]+(?:-[a-z0-9_]+)*-")
_DEBIAN_ARCH = re.compile(r"[a-z0-9]+")

_TARBALL_FIELDS = ("url", "signature_url", "sha256")
_GIT_TAG_FIELDS = ("repository", "tag", "commit")
_SHAPES = {
    "url": _URL,
    "signature_url": _URL,
    "sha256": _SHA256,
    "repository": _URL,
    "tag": _TAG,
    "commit": _COMMIT,
}


class QueryError(ValueError):
    """versions.json does not declare what was asked, or declares it in the wrong shape."""


def _shaped(value: object, pattern: re.Pattern[str], where: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise QueryError(f"{where}: {value!r} is not in the expected form")
    return value


def kernelversion(version: str) -> str:
    """What ``make kernelversion`` prints for a stream version: 7.3-rc5 reads 7.3.0-rc5."""
    match = _VERSION.fullmatch(version)
    if match is None:
        raise QueryError(f"version: {version!r} is not X.Y, X.Y.Z or X.Y-rcN")
    major, minor, sublevel, rc = match.groups()
    return f"{major}.{minor}.{sublevel or 0}{rc or ''}"


def debian_version(version: str) -> str:
    """The Debian upstream version of a stream version: 7.3-rc5 reads 7.3~rc5.

    A tilde sorts before anything, so dpkg orders 7.3~rc5 before 7.3; 7.3-rc5 would sort after
    it. The kernel's scripts/package/mkdebian applies the same mapping, -rc<N> to ~rc<N>, when it
    derives a package version itself.
    """
    kernelversion(version)
    return _RC.sub(r"~\1", version, count=1)


def _signers(source: Mapping[str, object], where: str) -> str:
    signers = source.get("signers")
    if not isinstance(signers, list) or not signers:
        raise QueryError(f"{where}.signers: at least one fingerprint is required")
    shaped = [_shaped(fpr, _FINGERPRINT, f"{where}.signers") for fpr in signers]
    return " ".join(shaped)


def source_fields(versions: Mapping[str, object], stream: str) -> list[tuple[str, str]]:
    """The checked source fields of one stream, in a fixed order."""
    streams = versions.get("streams")
    if not isinstance(streams, dict) or stream not in streams:
        raise QueryError(f"unknown stream {stream!r}: versions.json does not publish it")
    entry = streams[stream]
    where = f"streams.{stream}.source"
    source = entry.get("source") if isinstance(entry, dict) else None
    if not isinstance(source, dict):
        raise QueryError(f"{where}: missing")
    kind = source.get("kind")
    if kind == "tarball":
        names = _TARBALL_FIELDS
    elif kind == "git-tag":
        names = _GIT_TAG_FIELDS
    else:
        raise QueryError(f"{where}.kind: {kind!r} is neither tarball nor git-tag")
    fields = [("kind", kind), ("version", str(entry.get("version")))]
    fields.append(("kernelversion", kernelversion(str(entry.get("version")))))
    fields.append(("debian_version", debian_version(str(entry.get("version")))))
    for name in names:
        fields.append((name, _shaped(source.get(name), _SHAPES[name], f"{where}.{name}")))
    fields.append(("signers", _signers(source, where)))
    return fields


def arch_fields(versions: Mapping[str, object], arch: str) -> list[tuple[str, str]]:
    """The checked build fields of one architecture, in a fixed order."""
    architectures = versions.get("architectures")
    if not isinstance(architectures, dict) or arch not in architectures:
        raise QueryError(f"unknown architecture {arch!r}: versions.json does not build it")
    entry = architectures[arch]
    if not isinstance(entry, dict):
        raise QueryError(f"architectures.{arch}: must be an object")
    where = f"architectures.{arch}"
    return [
        ("kernel_arch", _shaped(entry.get("kernel_arch"), _NAME, f"{where}.kernel_arch")),
        ("base_config", _shaped(entry.get("base_config"), _DEFCONFIG, f"{where}.base_config")),
        ("cross_compile", _shaped(entry.get("cross_compile"), _PREFIX, f"{where}.cross_compile")),
        ("debian_arch", _shaped(entry.get("debian_arch"), _DEBIAN_ARCH, f"{where}.debian_arch")),
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--versions", type=Path, default=Path("versions.json"))
    parser.add_argument("query", choices=("source", "arch"))
    parser.add_argument("name", help="the stream (source) or the architecture (arch)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        versions = json.loads(args.versions.read_text(encoding="utf-8"))
        if not isinstance(versions, dict):
            raise QueryError("versions.json is not a JSON object")
        if args.query == "source":
            fields = source_fields(versions, args.name)
        else:
            fields = arch_fields(versions, args.name)
    except (OSError, ValueError) as exc:
        print(f"versions_query: {exc}", file=sys.stderr)
        return 2
    print("\n".join(f"{key}={value}" for key, value in fields))
    return 0


if __name__ == "__main__":
    sys.exit(main())
