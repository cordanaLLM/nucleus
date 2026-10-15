#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Write the imago.nucleus.kernel-artifact.v1 manifest for a signed release directory.

The release workflow runs this after ``SHA256SUMS`` is written and signed. The manifest lists
every artifact ``SHA256SUMS`` names with its digest and size, the digest of ``SHA256SUMS``
itself, the digest of the resolved kernel configuration, and the provenance imago checks before
it pins a stream. The shape is owned by cordanaLLM/imago (``pkg/kernel``); see
docs/packaging.md section 5.3.

Nothing is taken on trust from ``SHA256SUMS``. The manifest is refused, and nothing is written,
when:

- ``SHA256SUMS`` or ``SHA256SUMS.bundle`` is missing, or ``SHA256SUMS`` lists nothing;
- a line is not ``<64 hex> <name>``, names a file twice, or names a path rather than a plain
  file name;
- a listed file is missing, is not a regular file, or no longer has the listed digest;
- a file in the directory is not listed, so it would be uploaded without a manifest entry;
- ``kernel-<stream>-x86_64.config`` is not listed: there is no configuration to digest, and a
  digest of anything else would describe a build that did not happen.

Complies with NASA/JPL Power of 10: short functions (<= 60 lines), no recursion, bounded loops,
and every failure is reported, never guessed around.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

SCHEMA = "imago.nucleus.kernel-artifact.v1"
CHECKSUMS = "SHA256SUMS"
BUNDLE = "SHA256SUMS.bundle"
ARCH = "x86_64"
# sha256sum writes "<digest>  <name>" in text mode and "<digest> *<name>" in binary mode.
LINE = re.compile(r"(?P<digest>[0-9a-f]{64}) [ *](?P<name>.+)")
# An allow-list: the names publish_release.sh checksums, and no path, no leading dot.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
REVISION = re.compile(r"[0-9a-f]{40}")
STREAM = re.compile(r"[a-z][a-z0-9-]*")
MAX_LINES = 256


class ManifestError(ValueError):
    """The release directory cannot be described by a manifest."""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def read_checksums(out: Path) -> list[tuple[str, str]]:
    """The (name, digest) pairs SHA256SUMS lists, in its order, each proven against its file."""
    checksums = out / CHECKSUMS
    if not regular_file(checksums):
        raise ManifestError(f"{checksums} is missing or not a regular file")
    lines = [line for line in checksums.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ManifestError(f"{checksums} lists no artifact")
    if len(lines) > MAX_LINES:
        raise ManifestError(f"{checksums} lists {len(lines)} files, more than {MAX_LINES}")
    entries: list[tuple[str, str]] = []
    for line in lines:
        match = LINE.fullmatch(line)
        if match is None or NAME.fullmatch(match["name"]) is None:
            raise ManifestError(f"{CHECKSUMS} line is not '<64 hex> <file name>': {line!r}")
        name, listed = match["name"], match["digest"]
        if name in dict(entries):
            raise ManifestError(f"{CHECKSUMS} lists {name} twice")
        if not regular_file(out / name):
            raise ManifestError(f"{name} is listed in {CHECKSUMS} but is not a regular file")
        if digest(out / name) != listed:
            raise ManifestError(f"{name} does not have the digest {CHECKSUMS} lists for it")
        entries.append((name, listed))
    return entries


def check_complete(out: Path, entries: list[tuple[str, str]], target: str, provenance: str) -> None:
    """Every file in the directory is listed, apart from the checksums, bundle, manifest and
    provenance bundle, which actions/attest-build-provenance writes after SHA256SUMS."""
    if not regular_file(out / BUNDLE):
        raise ManifestError(f"{out / BUNDLE} is missing: SHA256SUMS has not been signed")
    listed = {name for name, _ in entries}
    unlisted = sorted(
        path.name
        for path in out.iterdir()
        if path.name not in listed and path.name not in (CHECKSUMS, BUNDLE, target, provenance)
    )
    if unlisted:
        raise ManifestError(f"not listed in {CHECKSUMS}: {', '.join(unlisted)}")


def build_manifest(args: argparse.Namespace) -> dict:
    out: Path = args.dir
    config = f"kernel-{args.stream}-{ARCH}.config"
    entries = read_checksums(out)
    check_complete(
        out,
        entries,
        f"kernel-{args.stream}.manifest.json",
        f"kernel-{args.stream}.provenance.sigstore.json",
    )
    digests = dict(entries)
    if config not in digests:
        raise ManifestError(f"{config} is not in {CHECKSUMS}: there is no configuration to digest")
    return {
        "schema": SCHEMA,
        "provider": args.repository,
        "stream": args.stream,
        "version": args.version,
        "kernel": {"release": args.kernelrelease, "config_digest": "sha256:" + digests[config]},
        "artifacts": [
            {"name": name, "sha256": listed, "size": (out / name).stat().st_size}
            for name, listed in entries
        ],
        "checksums": {"file": CHECKSUMS, "sha256": digest(out / CHECKSUMS)},
        "provenance": {
            "repository": args.repository,
            "tag": args.tag,
            "revision": args.revision,
            "bundle": BUNDLE,
            "signer_identity": args.signer_identity,
        },
    }


def check_arguments(args: argparse.Namespace) -> None:
    if STREAM.fullmatch(args.stream) is None:
        raise ManifestError(f"--stream {args.stream!r} is not a stream name")
    if REVISION.fullmatch(args.revision) is None:
        raise ManifestError(f"--revision {args.revision!r} is not a 40-hex commit")
    for option in ("version", "kernelrelease", "tag", "repository", "signer_identity"):
        if not getattr(args, option).strip():
            raise ManifestError(f"--{option.replace('_', '-')} is empty")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True, help="the signed release directory")
    parser.add_argument("--stream", required=True)
    parser.add_argument("--version", required=True, help="<version>-lusoris<N>")
    parser.add_argument("--kernelrelease", required=True, help="from the build record")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--revision", required=True, help="the commit the tag points at")
    parser.add_argument("--repository", required=True, help="owner/name")
    parser.add_argument("--signer-identity", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        check_arguments(args)
        manifest = build_manifest(args)
    except (ManifestError, OSError, UnicodeDecodeError) as exc:
        print(f"kernel_artifact_manifest: refused: {exc}", file=sys.stderr)
        return 1
    target = args.dir / f"kernel-{args.stream}.manifest.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(target.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
