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
  ``linux-headers-<kernelrelease>`` or ``linux-libc-dev``, at version
  ``<debian_version>-lusoris<N>`` (``7.3~rc5`` for ``7.3-rc5``, so dpkg orders a release
  candidate before its release) and the architecture's Debian architecture from versions.json,
  under the file name ``<Package>_<Version>_<Architecture>.deb`` with ``~`` spelled ``.``
  (GitHub renames ``~`` in a release asset, and imago refuses it in an artifact name); no
  package appears twice, and the image and libc-dev packages are present, and the headers
  package too unless ``--cross`` says the build was a cross build, which makes none;
- the kernelrelease starts with the stream's kernel version and ends with ``-lusoris<N>-<stream>``;
- the image package carries ``./boot/vmlinuz-<kernelrelease>``, byte-identical to the
  ``vmlinuz-<kernelrelease>`` beside it, and ``./boot/config-<kernelrelease>``, byte-identical
  to the resolved ``kernel-<stream>-<arch>.config``;
- that kernel image is a kernel of this release for this architecture: on x86 a bzImage whose
  setup header (``HdrS`` at 0x202) points at a version string that starts with
  ``<kernelrelease> ``; on arm64 and riscv a gzip-compressed ``Image`` with the architecture's
  header magic at 0x38 and the banner ``Linux version <kernelrelease> `` inside. Any other
  kernel architecture is refused, since no check is known for it.

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
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import versions_query  # noqa: E402

PACKAGE_FIELDS = ("Package", "Version", "Architecture")
_KERNELRELEASE = re.compile(r"[0-9][0-9A-Za-z.+_-]{0,63}")
# The header magic of an arm64 and a riscv Image, at offset 0x38 (Documentation/arch/arm64/
# booting.rst, Documentation/arch/riscv/boot-image-header.rst).
IMAGE_MAGIC = {"arm64": b"ARM\x64", "riscv": b"RSC\x05"}
IMAGE_MAGIC_OFFSET = 0x38
# A decompressed arm64 Image of this forge is about 50 MB; anything past this bound is refused.
MAX_IMAGE_BYTES = 256 * 1024 * 1024


class GateError(RuntimeError):
    """The gate cannot evaluate the request at all."""


@dataclass(frozen=True)
class Expectation:
    """What versions.json and the build request say the artifacts must be."""

    stream: str
    arch: str
    kernel_arch: str
    kernelversion: str
    package_version: str
    debian_arch: str
    localversion: str
    kernelrelease: str
    cross: bool = False

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

    @property
    def required(self) -> tuple[str, ...]:
        """The packages the build must have produced: a cross build makes no headers package."""
        image, headers, libc = self.packages
        return (image, libc) if self.cross else (image, headers, libc)


def package_file_name(package: str, version: str, arch: str) -> str:
    """The file name of a package: dpkg's, with ``~`` spelled ``.``."""
    return f"{package}_{version}_{arch}.deb".replace("~", ".")


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
        kernel_arch=arch["kernel_arch"],
        kernelversion=source["kernelversion"],
        package_version=f"{source['debian_version']}-lusoris{args.revision}",
        debian_arch=arch["debian_arch"],
        localversion=f"-lusoris{args.revision}-{args.stream}",
        kernelrelease=args.kernelrelease,
        cross=bool(getattr(args, "cross", False)),
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
    expected_name = package_file_name(package, version, arch)
    if path.name != expected_name:
        problems.append(f"{path.name}: file name does not match {expected_name}")
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
    for package in exp.required:
        if package not in seen:
            problems.append(f"no {package} package")
    return problems, seen.get(exp.packages[0])


def image_members(image: Path, wanted: Sequence[str]) -> dict[str, bytes] | None:
    """Read the wanted regular files out of a package's data archive, streaming it once.

    None when dpkg-deb or the archive fails, even after the wanted members streamed through.
    """
    found: dict[str, bytes] = {}
    with subprocess.Popen(
        ["dpkg-deb", "--fsys-tarfile", str(image)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as proc:
        readable = True
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|*") as archive:
                for member in archive:
                    if member.name in wanted and member.isfile():
                        handle = archive.extractfile(member)
                        found[member.name] = handle.read() if handle else b""
        except tarfile.TarError:
            readable = False
        finally:
            if proc.stdout is not None:
                proc.stdout.read()
        if proc.wait() != 0:
            readable = False
    return found if readable else None


def _bzimage_version(data: bytes) -> str | None:
    """The version string an x86 bzImage's setup header points at, or None if it has none."""
    if len(data) < 0x210 or data[0x202:0x206] != b"HdrS":
        return None
    offset = 0x200 + int.from_bytes(data[0x20E:0x210], "little")
    end = data.find(b"\0", offset, offset + 512)
    if end < 0:
        return None
    return data[offset:end].decode("ascii", "replace")


def _gunzip(data: bytes) -> bytes | None:
    """One complete gzip stream, decompressed, or None if it is not one or exceeds the bound."""
    if data[:2] != b"\x1f\x8b":
        return None
    inflater = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        image = inflater.decompress(data, MAX_IMAGE_BYTES)
    except zlib.error:
        return None
    if inflater.unconsumed_tail or not inflater.eof:
        return None
    return image


def check_kernel_image(data: bytes, exp: Expectation) -> list[str]:
    """The kernel image must be a kernel of this release for this architecture."""
    name, kr = exp.vmlinuz_name, exp.kernelrelease
    if exp.kernel_arch == "x86_64":
        version = _bzimage_version(data)
        if version is None:
            return [f"{name}: not a bzImage (no setup header with a kernel version string)"]
        if not version.startswith(f"{kr} "):
            return [f"{name}: the bzImage reports {version.split(' ', 1)[0]!r}, not {kr}"]
        return []
    magic = IMAGE_MAGIC.get(exp.kernel_arch)
    if magic is None:
        return [f"{name}: no image check is known for kernel architecture {exp.kernel_arch}"]
    image = _gunzip(data)
    if image is None:
        return [f"{name}: not a complete gzip-compressed Image within {MAX_IMAGE_BYTES} bytes"]
    if image[IMAGE_MAGIC_OFFSET : IMAGE_MAGIC_OFFSET + len(magic)] != magic:
        return [f"{name}: the decompressed image has no {exp.kernel_arch} Image header"]
    if f"Linux version {kr} ".encode() not in image:
        return [f"{name}: the decompressed image has no 'Linux version {kr} ' banner"]
    return []


def check_image(image: Path, directory: Path, exp: Expectation) -> list[str]:
    """The image package must carry the kernel beside it and the configuration it was built from."""
    vmlinuz = f"./boot/vmlinuz-{exp.kernelrelease}"
    config = f"./boot/config-{exp.kernelrelease}"
    members = image_members(image, (vmlinuz, config))
    if members is None:
        return [f"{image.name}: dpkg-deb cannot read its data archive"]
    problems = []
    for member, beside in ((vmlinuz, exp.vmlinuz_name), (config, exp.config_name)):
        data = members.get(member)
        if not data:
            problems.append(f"{image.name}: carries no non-empty {member}")
            continue
        path = directory / beside
        if path.is_file() and path.read_bytes() != data:
            problems.append(f"{beside}: differs from {member} in {image.name}")
    if members.get(vmlinuz):
        problems.extend(check_kernel_image(members[vmlinuz], exp))
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
    parser.add_argument(
        "--cross",
        action="store_true",
        help="the build ran on another architecture, so it made no headers package",
    )
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
