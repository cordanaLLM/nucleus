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
"""Open the kernel artifacts of one build and refuse them unless they are what they claim.

A checksum and a signature attest to bytes, not to what the bytes are: an empty file or a text
file named ``.deb`` checksums and signs as well as a kernel does. This gate runs before any
checksum is computed (docs/adr/0009-kernel-compilation-and-artifact-gate.md) and refuses the
directory unless all of these hold:

- it holds only non-empty regular files: Debian packages, ``vmlinuz-<kernelrelease>`` and
  ``kernel-<stream>-<arch>.config``, and nothing else;
- every package names itself, in ``dpkg-deb -f``, as ``linux-image-<kernelrelease>``,
  ``linux-headers-<kernelrelease>`` or ``linux-libc-dev``, at version ``<version>-lusoris<N>``
  and the architecture's Debian architecture from versions.json, under the file name
  ``<Package>_<Version>_<Architecture>.deb``; the image package is present, and no package twice;
- the kernelrelease starts with the stream's kernel version and ends with ``-lusoris<N>-<stream>``;
- the image package carries ``./boot/vmlinuz-<kernelrelease>``, byte-identical to the
  ``vmlinuz-<kernelrelease>`` beside it, and ``./boot/config-<kernelrelease>``, byte-identical
  to the resolved ``kernel-<stream>-<arch>.config``.

Exit status 0 when the directory passes, 1 when it is refused (every finding is listed), and 2
when the request itself cannot be evaluated (unknown stream or architecture, no dpkg-deb).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import versions_query  # noqa: E402

PACKAGE_FIELDS = ("Package", "Version", "Architecture")
_KERNELRELEASE = re.compile(r"[0-9][0-9A-Za-z.+_-]{0,63}")


class GateError(RuntimeError):
    """The gate cannot evaluate the request at all."""


@dataclass(frozen=True)
class Expectation:
    """What versions.json and the build request say the artifacts must be."""

    stream: str
    arch: str
    kernelversion: str
    package_version: str
    debian_arch: str
    localversion: str
    kernelrelease: str

    @property
    def config_name(self) -> str:
        return f"kernel-{self.stream}-{self.arch}.config"

    @property
    def vmlinuz_name(self) -> str:
        return f"vmlinuz-{self.kernelrelease}"

    @property
    def packages(self) -> tuple[str, ...]:
        kr = self.kernelrelease
        return (f"linux-image-{kr}", f"linux-headers-{kr}", "linux-libc-dev")


def expectation(versions_path: Path, args: argparse.Namespace) -> Expectation:
    """Derive every expected value from versions.json, the request and the measured release."""
    try:
        versions = json.loads(versions_path.read_text(encoding="utf-8"))
        source = dict(versions_query.source_fields(versions, args.stream))
        arch = dict(versions_query.arch_fields(versions, args.arch))
    except (OSError, ValueError) as exc:
        raise GateError(str(exc)) from exc
    if args.revision < 1:
        raise GateError(f"revision {args.revision} is not a positive integer")
    return Expectation(
        stream=args.stream,
        arch=args.arch,
        kernelversion=source["kernelversion"],
        package_version=f"{source['version']}-lusoris{args.revision}",
        debian_arch=arch["debian_arch"],
        localversion=f"-lusoris{args.revision}-{args.stream}",
        kernelrelease=args.kernelrelease,
    )


def check_release(exp: Expectation) -> list[str]:
    """The kernelrelease must be this stream's kernel version carrying this build's suffix."""
    kr = exp.kernelrelease
    if _KERNELRELEASE.fullmatch(kr) is None:
        return [f"kernelrelease {kr!r} is not a kernel release string"]
    problems = []
    base = exp.kernelversion
    if not (kr == base or kr.startswith(f"{base}-") or kr.startswith(f"{base}+")):
        problems.append(f"kernelrelease {kr} does not start with the {exp.stream} kernel version {base}")
    if not kr.endswith(exp.localversion):
        problems.append(f"kernelrelease {kr} does not end with the build's {exp.localversion}")
    return problems


def check_listing(directory: Path, exp: Expectation) -> tuple[list[str], list[Path]]:
    """Only non-empty regular files, of the three expected kinds, may be in the directory."""
    if not directory.is_dir():
        return [f"{directory} is not a directory"], []
    problems: list[str] = []
    debs: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if entry.is_symlink() or not entry.is_file():
            problems.append(f"{entry.name}: not a regular file")
            continue
        if entry.stat().st_size == 0:
            problems.append(f"{entry.name}: zero bytes")
            continue
        if entry.suffix == ".deb":
            debs.append(entry)
        elif entry.name not in (exp.config_name, exp.vmlinuz_name):
            problems.append(f"{entry.name}: not an artifact of this build")
    for name in (exp.config_name, exp.vmlinuz_name):
        if not (directory / name).is_file():
            problems.append(f"{name}: missing")
    if not debs:
        problems.append("no Debian package")
    return problems, debs


def deb_fields(path: Path) -> dict[str, str]:
    """The control fields dpkg-deb reads from a package, or an empty mapping if it cannot."""
    result = subprocess.run(
        ["dpkg-deb", "-f", str(path), *PACKAGE_FIELDS],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {}
    fields = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def check_package(path: Path, exp: Expectation) -> tuple[list[str], str | None]:
    """Check one package's identity; return the findings and its package name."""
    fields = deb_fields(path)
    if not all(fields.get(key) for key in PACKAGE_FIELDS):
        return [f"{path.name}: dpkg-deb cannot read Package, Version and Architecture"], None
    package, version, arch = (fields[key] for key in PACKAGE_FIELDS)
    problems = []
    if package not in exp.packages:
        problems.append(f"{path.name}: package {package} is none of {', '.join(exp.packages)}")
    if version != exp.package_version:
        problems.append(f"{path.name}: version {version}, expected {exp.package_version}")
    if arch != exp.debian_arch:
        problems.append(f"{path.name}: architecture {arch}, expected {exp.debian_arch}")
    if path.name != f"{package}_{version}_{arch}.deb":
        problems.append(f"{path.name}: file name does not match {package}_{version}_{arch}.deb")
    return problems, package


def check_packages(debs: Sequence[Path], exp: Expectation) -> tuple[list[str], Path | None]:
    """Check every package; return the findings and the image package, if there is one."""
    problems: list[str] = []
    seen: dict[str, Path] = {}
    for path in debs:
        found, package = check_package(path, exp)
        problems.extend(found)
        if package is None:
            continue
        if package in seen:
            problems.append(f"{path.name}: package {package} also in {seen[package].name}")
        seen.setdefault(package, path)
    image = seen.get(exp.packages[0])
    if image is None:
        problems.append(f"no {exp.packages[0]} package")
    return problems, image


def image_members(image: Path, wanted: Sequence[str]) -> dict[str, bytes]:
    """Read the wanted regular files out of a package's data archive, streaming it once."""
    found: dict[str, bytes] = {}
    with subprocess.Popen(
        ["dpkg-deb", "--fsys-tarfile", str(image)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as proc:
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|*") as archive:
                for member in archive:
                    if member.name in wanted and member.isfile():
                        handle = archive.extractfile(member)
                        found[member.name] = handle.read() if handle else b""
        except tarfile.TarError:
            found.clear()
        finally:
            if proc.stdout is not None:
                proc.stdout.read()
    return found


def check_image(image: Path, directory: Path, exp: Expectation) -> list[str]:
    """The image package must carry the kernel beside it and the configuration it was built from."""
    vmlinuz = f"./boot/vmlinuz-{exp.kernelrelease}"
    config = f"./boot/config-{exp.kernelrelease}"
    members = image_members(image, (vmlinuz, config))
    problems = []
    for member, beside in ((vmlinuz, exp.vmlinuz_name), (config, exp.config_name)):
        data = members.get(member)
        if not data:
            problems.append(f"{image.name}: carries no non-empty {member}")
            continue
        path = directory / beside
        if path.is_file() and path.read_bytes() != data:
            problems.append(f"{beside}: differs from {member} in {image.name}")
    return problems


def evaluate(directory: Path, exp: Expectation) -> list[str]:
    """Every finding against the directory; an empty list means it passes."""
    problems = check_release(exp)
    listed, debs = check_listing(directory, exp)
    problems.extend(listed)
    found, image = check_packages(debs, exp)
    problems.extend(found)
    if image is not None:
        problems.extend(check_image(image, directory, exp))
    return problems


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True, help="the build's artifact directory")
    parser.add_argument("--stream", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--kernelrelease", required=True, help="make -s kernelrelease of the build")
    parser.add_argument("--revision", type=int, default=1, help="the forge revision N")
    parser.add_argument("--versions", type=Path, default=Path("versions.json"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if shutil.which("dpkg-deb") is None:
            raise GateError("dpkg-deb is required to open Debian packages and is not installed")
        exp = expectation(args.versions, args)
    except GateError as exc:
        print(f"check_kernel_artifacts: {exc}", file=sys.stderr)
        return 2
    problems = evaluate(args.dir, exp)
    if problems:
        print(f"Refused: {args.dir} ({exp.stream}/{exp.arch}, {exp.kernelrelease}):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"==> Artifact gate passed: {args.dir} holds {exp.kernelrelease} for {exp.stream}/{exp.arch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
