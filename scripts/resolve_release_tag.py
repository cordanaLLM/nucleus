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
"""Resolve a kernel release tag to exactly one stream of versions.json.

A kernel release tag has the form ``v<version>-<stream>-lusoris<N>``:

- ``<stream>`` is a key of ``streams`` in versions.json;
- ``<version>`` equals that stream's ``version`` exactly;
- ``<N>`` is the forge revision, an integer from 1 to 9999 written without
  leading zeros: exactly what ``scripts/build_kernel.sh --revision`` accepts.
  The release workflow passes it to the build, which writes ``-lusoris<N>``
  into the kernel release and the package version, so a second revision
  releases the same upstream version again under a new kernel release
  (``7.2.8-lusoris2-realtime``) and package version (``7.2.8-lusoris2``).

The stream is spelled out because two streams may carry the same upstream
version. There is no fallback: a tag that does not name exactly one stream at
exactly its version is refused, and the release workflow stops there.

With ``--ref`` the resolver also refuses a run whose git ref is not
``refs/tags/<tag>``. A manually dispatched workflow checks out, signs and
publishes for the ref it was started from, so a tag typed into the dispatch
form must be the ref the run started from, or the release would be attached to
another tag than the manifest names.

On success the resolver writes ``stream``, ``version``, ``rev``,
``release_tag`` and ``release_version`` (``<version>-lusoris<N>``, the version
string the artifact manifest and the downstream payload carry) as ``key=value``
lines, appended to the file named by ``--github-output`` or printed to stdout.

Complies with NASA/JPL Power of 10: short functions (<= 60 lines), no
recursion, no unbounded loops, and every failure is reported, never guessed
around.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VERSIONS = REPO_ROOT / "versions.json"
GRAMMAR = "v<version>-<stream>-lusoris<N>"
TAG_SHAPE = re.compile(r"v(?P<body>.+)-lusoris(?P<rev>[0-9]+)")
# The range scripts/build_kernel.sh --revision accepts (^[1-9][0-9]{0,3}$); the two must agree.
REVISION_TOKEN = re.compile(r"[1-9][0-9]{0,3}")
STREAM_TOKEN = re.compile(r"[a-z][a-z0-9-]*")
VERSION_TOKEN = re.compile(r"[0-9][A-Za-z0-9._+-]*")
MAX_TAG_LENGTH = 128


class TagError(ValueError):
    """A tag, or the versions.json it is checked against, cannot be resolved."""


@dataclass(frozen=True)
class Resolution:
    """The coordinates a kernel release tag resolves to."""

    stream: str
    version: str
    rev: int
    release_tag: str

    @property
    def release_version(self) -> str:
        return f"{self.version}-lusoris{self.rev}"

    def outputs(self) -> str:
        pairs = (
            ("stream", self.stream),
            ("version", self.version),
            ("rev", str(self.rev)),
            ("release_tag", self.release_tag),
            ("release_version", self.release_version),
        )
        return "".join(f"{key}={value}\n" for key, value in pairs)


def load_streams(path: Path) -> dict[str, str]:
    """Return {stream: version} from versions.json, refusing malformed entries."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TagError(f"cannot read {path}: {exc}") from exc
    streams = data.get("streams") if isinstance(data, dict) else None
    if not isinstance(streams, dict) or not streams:
        raise TagError(f"{path} declares no streams")
    resolved: dict[str, str] = {}
    for name, entry in streams.items():
        version = entry.get("version") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not STREAM_TOKEN.fullmatch(name):
            raise TagError(f"{path}: stream name {name!r} is not a lowercase token")
        if not isinstance(version, str) or not VERSION_TOKEN.fullmatch(version):
            raise TagError(f"{path}: stream {name} has no usable version ({version!r})")
        resolved[name] = version
    return resolved


def split_revision(tag: str) -> tuple[str, int]:
    """Split ``v<body>-lusoris<N>`` into (body, N); refuse any other shape."""
    if not tag:
        raise TagError(f"no tag given; a kernel release tag is {GRAMMAR}")
    if len(tag) > MAX_TAG_LENGTH:
        raise TagError(f"tag is longer than {MAX_TAG_LENGTH} characters")
    if not tag.startswith("v"):
        raise TagError(f"tag {tag!r} does not start with 'v'; a kernel release tag is {GRAMMAR}")
    shape = TAG_SHAPE.fullmatch(tag)
    if shape is None:
        raise TagError(
            f"tag {tag!r} does not end in -lusoris<N>; a kernel release tag is {GRAMMAR}"
        )
    rev = shape.group("rev")
    if not REVISION_TOKEN.fullmatch(rev):
        raise TagError(
            f"tag {tag!r} carries revision {rev!r}; a revision is an integer from 1 to 9999 "
            "without leading zeros"
        )
    return shape.group("body"), int(rev)


def match_stream(body: str, streams: dict[str, str], tag: str) -> tuple[str, str]:
    """Return the one (stream, version) whose ``<version>-<stream>`` equals body."""
    exact = [(name, version) for name, version in streams.items() if body == f"{version}-{name}"]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        names = ", ".join(name for name, _ in exact)
        raise TagError(f"tag {tag!r} matches more than one stream ({names})")
    named = [name for name in streams if body.endswith(f"-{name}")]
    if not named:
        known = ", ".join(sorted(streams))
        raise TagError(f"tag {tag!r} names no stream of versions.json (streams: {known})")
    details = "; ".join(
        f"stream {name} is at version {streams[name]}, "
        f"the tag names version {body[: -len(name) - 1]!r}"
        for name in named
    )
    raise TagError(f"tag {tag!r} does not match versions.json: {details}")


def resolve(tag: str, streams: dict[str, str]) -> Resolution:
    """Resolve a kernel release tag against {stream: version}, or raise TagError."""
    body, rev = split_revision(tag)
    stream, version = match_stream(body, streams, tag)
    return Resolution(stream=stream, version=version, rev=rev, release_tag=tag)


def check_ref(tag: str, ref: str | None) -> None:
    """Refuse a run whose git ref is not the tag it releases; None skips the check."""
    if ref is None:
        return
    if ref != f"refs/tags/{tag}":
        raise TagError(
            f"the run started from ref {ref!r}, not from refs/tags/{tag}; checkout, signer "
            f"identity and release all follow the ref, so start it from the tag "
            f"(gh workflow run --ref {tag})"
        )


def emit(text: str, github_output: str | None) -> None:
    """Append outputs to the GitHub output file, or print them to stdout."""
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


def report(message: str) -> None:
    """Print a refusal; inside GitHub Actions it becomes an error annotation."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=Kernel release tag refused::{escaped}", file=sys.stderr)
    else:
        print(f"error: {message}", file=sys.stderr)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Resolve a kernel release tag ({GRAMMAR}) against versions.json."
    )
    parser.add_argument("--tag", required=True, help=f"kernel release tag, {GRAMMAR}")
    parser.add_argument(
        "--versions",
        type=Path,
        default=DEFAULT_VERSIONS,
        help="path to versions.json (default: the repository's versions.json)",
    )
    parser.add_argument(
        "--ref",
        default=None,
        help="git ref of the run (in Actions: $GITHUB_REF); refuse unless it is refs/tags/<tag>",
    )
    parser.add_argument(
        "--github-output",
        default=None,
        help="append key=value outputs to this file (in Actions: $GITHUB_OUTPUT)",
    )
    return parser.parse_args(argv)


def run(argv: list[str]) -> int:
    """Resolve the tag named on the command line; 0 on success, 1 on refusal."""
    args = parse_args(argv)
    try:
        resolution = resolve(args.tag, load_streams(args.versions))
        check_ref(args.tag, args.ref)
    except TagError as exc:
        report(str(exc))
        return 1
    try:
        emit(resolution.outputs(), args.github_output)
    except OSError as exc:
        report(f"cannot write the resolved outputs: {exc}")
        return 1
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
