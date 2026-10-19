#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Print the versions.json fields a shell script needs, one KEY=VALUE per line.

The shell scripts that fetch and configure a kernel read versions.json through this module,
so every value they place on a command line has been checked against its shape first:

    versions_query.py source <stream>   the stream's source, the kernelversion it carries and
                                        the Debian upstream version its packages carry
    versions_query.py arch <arch>       the architecture's ARCH, base defconfig, toolchain and
                                        Debian architecture
    versions_query.py list <collection> the declared names under 'streams' or 'architectures',
                                        one per line
    versions_query.py openzfs           the OpenZFS out-of-tree module source, pins, and
                                        supported kernel version range
    versions_query.py nvidia-open       the NVIDIA Open GPU kernel module source, pins, and
                                        supported streams
    versions_query.py series-streams <series>
                                        the streams a patch series is bound to, one per line
    versions_query.py scheduler-streams <scheduler>
                                        the streams whose x86_64 boot loads a sched_ext
                                        scheduler, one per line

A value outside its shape, an unknown stream, an unknown architecture or an unknown collection
is refused with exit status 2 and nothing on standard output. versions.schema.json states the
same shapes for the committed file; this module holds them for any file it is pointed at,
including the throwaway manifests the tests build, which may name a file:// URL.
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
_ZFS_VERSION = re.compile(r"[0-9]+[.][0-9]+(?:[.][0-9]+)?")
_BOUND = re.compile(r"[0-9]+[.][0-9]+")
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


def openzfs_fields(versions: Mapping[str, object]) -> list[tuple[str, str]]:
    """The checked build fields of the OpenZFS module, in a fixed order."""
    modules = versions.get("modules")
    if not isinstance(modules, dict) or "openzfs" not in modules:
        raise QueryError("modules.openzfs missing from manifest")
    zfs = modules["openzfs"]
    if not isinstance(zfs, dict):
        raise QueryError("modules.openzfs: must be an object")
    for k in ("version", "linux_minimum", "linux_maximum"):
        if k not in zfs:
            raise QueryError(f"modules.openzfs.{k} missing")
    source = zfs.get("source")
    if not isinstance(source, dict):
        raise QueryError("modules.openzfs.source missing")
    for k in ("url", "signature_url", "sha256", "signers"):
        if k not in source:
            raise QueryError(f"modules.openzfs.source.{k} missing")
    return [
        ("version", _shaped(zfs.get("version"), _ZFS_VERSION, "modules.openzfs.version")),
        ("url", _shaped(source.get("url"), _SHAPES["url"], "modules.openzfs.source.url")),
        (
            "signature_url",
            _shaped(
                source.get("signature_url"),
                _SHAPES["signature_url"],
                "modules.openzfs.source.signature_url",
            ),
        ),
        (
            "sha256",
            _shaped(source.get("sha256"), _SHAPES["sha256"], "modules.openzfs.source.sha256"),
        ),
        ("signers", _signers(source, "modules.openzfs.source")),
        (
            "linux_minimum",
            _shaped(zfs.get("linux_minimum"), _BOUND, "modules.openzfs.linux_minimum"),
        ),
        (
            "linux_maximum",
            _shaped(zfs.get("linux_maximum"), _BOUND, "modules.openzfs.linux_maximum"),
        ),
    ]


def nvidia_open_fields(versions: Mapping[str, object]) -> list[tuple[str, str]]:
    """The checked build fields of the NVIDIA Open GPU kernel module, in a fixed order."""
    modules = versions.get("modules")
    if not isinstance(modules, dict) or "nvidia_open" not in modules:
        raise QueryError("modules.nvidia_open missing from manifest")
    nv = modules["nvidia_open"]
    if not isinstance(nv, dict):
        raise QueryError("modules.nvidia_open: must be an object")
    for k in ("version", "streams", "architectures"):
        if k not in nv:
            raise QueryError(f"modules.nvidia_open.{k} missing")
    source = nv.get("source")
    if not isinstance(source, dict):
        raise QueryError("modules.nvidia_open.source missing")
    for k in ("url", "sha256", "commit"):
        if k not in source:
            raise QueryError(f"modules.nvidia_open.source.{k} missing")
    streams = nv.get("streams")
    if not isinstance(streams, list) or not streams:
        raise QueryError("modules.nvidia_open.streams: must be a non-empty list")
    for s in streams:
        _shaped(s, _NAME, "modules.nvidia_open.streams")
    archs = nv.get("architectures")
    if not isinstance(archs, list) or not archs:
        raise QueryError("modules.nvidia_open.architectures: must be a non-empty list")
    for a in archs:
        _shaped(a, _NAME, "modules.nvidia_open.architectures")
    return [
        (
            "version",
            _shaped(nv.get("version"), _ZFS_VERSION, "modules.nvidia_open.version"),
        ),
        (
            "url",
            _shaped(source.get("url"), _SHAPES["url"], "modules.nvidia_open.source.url"),
        ),
        (
            "sha256",
            _shaped(source.get("sha256"), _SHAPES["sha256"], "modules.nvidia_open.source.sha256"),
        ),
        (
            "commit",
            _shaped(source.get("commit"), _SHAPES["commit"], "modules.nvidia_open.source.commit"),
        ),
        ("streams", " ".join(streams)),
        ("architectures", " ".join(archs)),
    ]


def list_names(versions: Mapping[str, object], collection: str) -> list[str]:
    """The names declared under streams or architectures, in order."""
    if collection not in ("streams", "architectures"):
        raise QueryError(
            f"unknown list collection {collection!r}: must be 'streams' or 'architectures'"
        )
    items = versions.get(collection)
    if not isinstance(items, dict) or not items:
        raise QueryError(f"{collection}: missing or not an object in versions.json")
    return list(items.keys())


def series_streams(
    versions: Mapping[str, object], series: str, collection: str = "patches"
) -> list[str]:
    """The streams an entry under 'patches' (or 'schedulers') names, in order."""
    entries = versions.get(collection)
    entry = entries.get(series) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        raise QueryError(f"{collection}.{series}: missing or not an object in versions.json")
    streams = entry.get("streams")
    if not isinstance(streams, list) or not all(isinstance(name, str) for name in streams):
        raise QueryError(f"{collection}.{series}.streams: missing or not a list of names")
    return list(streams)


_NAMED = ("source", "arch", "list", "series-streams", "scheduler-streams")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--versions", type=Path, default=Path("versions.json"))
    parser.add_argument(
        "query",
        choices=(
            "source",
            "arch",
            "list",
            "openzfs",
            "nvidia-open",
            "series-streams",
            "scheduler-streams",
        ),
    )
    parser.add_argument(
        "name",
        nargs="?",
        default="",
        help=(
            "the stream (source), architecture (arch), collection (list), "
            "patch series (series-streams), or scheduler (scheduler-streams)"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.query in _NAMED and not args.name:
            raise QueryError(f"query {args.query!r} requires a name argument")
        versions = json.loads(args.versions.read_text(encoding="utf-8"))
        if not isinstance(versions, dict):
            raise QueryError("versions.json is not a JSON object")
        if args.query == "source":
            fields = source_fields(versions, args.name)
            print("\n".join(f"{key}={value}" for key, value in fields))
        elif args.query == "arch":
            fields = arch_fields(versions, args.name)
            print("\n".join(f"{key}={value}" for key, value in fields))
        elif args.query == "openzfs":
            fields = openzfs_fields(versions)
            print("\n".join(f"{key}={value}" for key, value in fields))
        elif args.query == "nvidia-open":
            fields = nvidia_open_fields(versions)
            print("\n".join(f"{key}={value}" for key, value in fields))
        elif args.query == "list":
            names = list_names(versions, args.name)
            print("\n".join(names))
        elif args.query == "series-streams":
            print("\n".join(series_streams(versions, args.name)))
        elif args.query == "scheduler-streams":
            print("\n".join(series_streams(versions, args.name, "schedulers")))
    except (OSError, ValueError) as exc:
        print(f"versions_query: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
