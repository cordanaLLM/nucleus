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
"""Refuse a Unified Kernel Image (UKI) that is not one.

scripts/package-uki.sh runs this after ukify and before the image is checksummed, and deletes
an image this refuses, so it never reaches a checksum or the release signature. This script
only reads the image; it deletes nothing.

The checks are structural and use the standard library only:

1. the file is not empty and starts with an MZ DOS header;
2. e_lfanew points inside the file, at a "PE\\0\\0" signature and a COFF header;
3. the COFF machine type matches --arch;
4. the optional header is PE32 or PE32+ with subsystem EFI application (10);
5. the section table and every section's raw data lie inside the file;
6. .linux, .osrel, .cmdline, .uname and .sbat are each present once and not empty;
7. .sdmagic carries the systemd-stub marker, so the image was built on systemd-stub;
8. .initrd is present if and only if --expect-initrd is given;
9. no UKI section that package-uki.sh does not wire yet is present (UNWIRED_SECTIONS), so a
   host ukify.conf or a ukify default cannot add microcode, a device tree, a splash image,
   firmware, HWIDs, PCR signatures or profiles;
10. a .linux kernel that starts with MZ is a PE for the --arch machine type, so an x64 stub
    around a kernel for another architecture is refused;
11. with --expect-cmdline, .cmdline holds exactly the requested command line.

Header layout: Microsoft PE format specification. Section names: UAPI Group UKI specification.
This proves shape, not bootability or provenance: a deliberately crafted file with these
sections passes. It exists to stop a failed or missing tool, or a placeholder, from being
published under a UKI name.

Exit status: 0 when the image passes, 1 when it is refused, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path
from typing import NamedTuple

DOS_HEADER_SIZE = 0x40
E_LFANEW_OFFSET = 0x3C
PE_SIGNATURE = b"PE\0\0"
COFF_HEADER_SIZE = 20
SECTION_HEADER_SIZE = 40
# The Windows loader limit from the PE format specification; it also bounds the section loop.
MAX_SECTIONS = 96
OPTIONAL_HEADER_MAGIC = (0x10B, 0x20B)  # PE32, PE32+
SUBSYSTEM_OFFSET = 68
SUBSYSTEM_EFI_APPLICATION = 10
# PE machine type per architecture name in versions.json ("architectures").
MACHINE_BY_ARCH = {"x86_64": 0x8664, "arm64": 0xAA64, "riscv64": 0x5064}
REQUIRED_SECTIONS = (".linux", ".osrel", ".cmdline", ".uname", ".sbat", ".sdmagic")
# UKI sections package-uki.sh does not pass to ukify. Each stays refused until it is wired on
# purpose, so the image does not depend on what the build host has installed or configured.
UNWIRED_SECTIONS = (
    ".ucode",
    ".splash",
    ".dtb",
    ".dtbauto",
    ".efifw",
    ".hwids",
    ".pcrsig",
    ".pcrpkey",
    ".profile",
)
SDMAGIC_PREFIX = b"#### LoaderInfo: systemd-stub "
# How much of a mismatched command line a refusal quotes.
QUOTE_LIMIT = 200


class NotAUki(Exception):
    """The file is not a Unified Kernel Image this forge may publish."""


class Section(NamedTuple):
    """One entry of the PE section table."""

    name: str
    virtual_size: int
    raw_size: int
    raw_offset: int


class PeHeader(NamedTuple):
    """What the COFF header says, and where the optional header lies."""

    machine: int
    section_count: int
    optional_offset: int
    optional_size: int


def _unpack(fmt: str, data: bytes, offset: int, what: str) -> tuple:
    """struct.unpack_from, refusing any read that would leave the file."""
    end = offset + struct.calcsize(fmt)
    if offset < 0 or end > len(data):
        raise NotAUki(
            f"{what} at offset {offset:#x} runs past the end of the {len(data)}-byte file"
        )
    return struct.unpack_from(fmt, data, offset)


def read_pe_header(data: bytes) -> PeHeader:
    """Check the MZ DOS header and the PE signature, and read the COFF header."""
    if not data:
        raise NotAUki("the file is empty")
    if len(data) < DOS_HEADER_SIZE or data[:2] != b"MZ":
        raise NotAUki("no MZ DOS header: not a PE image")
    (e_lfanew,) = _unpack("<I", data, E_LFANEW_OFFSET, "e_lfanew")
    if e_lfanew < DOS_HEADER_SIZE:
        raise NotAUki(f"e_lfanew {e_lfanew:#x} points into the DOS header")
    (signature,) = _unpack("<4s", data, e_lfanew, "PE signature")
    if signature != PE_SIGNATURE:
        raise NotAUki(f"no PE signature at e_lfanew {e_lfanew:#x}")
    coff = _unpack("<HHIIIHH", data, e_lfanew + 4, "COFF header")
    return PeHeader(coff[0], coff[1], e_lfanew + 4 + COFF_HEADER_SIZE, coff[5])


def check_machine(machine: int, arch: str) -> None:
    """Require the PE machine type that belongs to arch."""
    expected = MACHINE_BY_ARCH[arch]
    if machine != expected:
        raise NotAUki(f"machine type {machine:#06x} is not {arch} ({expected:#06x})")


def read_coff_header(data: bytes, arch: str) -> PeHeader:
    """Check the image's own PE headers: the machine type for arch and a sane section count."""
    header = read_pe_header(data)
    check_machine(header.machine, arch)
    if not 0 < header.section_count <= MAX_SECTIONS:
        raise NotAUki(f"{header.section_count} sections; expected 1 to {MAX_SECTIONS}")
    return header


def check_optional_header(data: bytes, offset: int, size: int) -> None:
    """Check the optional header is PE32 or PE32+ and marks an EFI application."""
    if size < SUBSYSTEM_OFFSET + 2:
        raise NotAUki(f"optional header of {size} bytes is too small to hold a subsystem")
    _unpack(f"<{size}s", data, offset, "optional header")
    (magic,) = _unpack("<H", data, offset, "optional header magic")
    if magic not in OPTIONAL_HEADER_MAGIC:
        raise NotAUki(f"optional header magic {magic:#x} is neither PE32 nor PE32+")
    (subsystem,) = _unpack("<H", data, offset + SUBSYSTEM_OFFSET, "subsystem")
    if subsystem != SUBSYSTEM_EFI_APPLICATION:
        raise NotAUki(
            f"subsystem {subsystem} is not an EFI application ({SUBSYSTEM_EFI_APPLICATION})"
        )


def read_sections(data: bytes, table: int, count: int) -> list[Section]:
    """Parse the section table, refusing any section whose raw data leaves the file."""
    sections = []
    for index in range(count):
        offset = table + index * SECTION_HEADER_SIZE
        raw_name, virtual_size, _, raw_size, raw_offset = _unpack(
            "<8sIIII", data, offset, f"section header {index}"
        )
        # An eight-character name such as .cmdline has no terminating NUL.
        name = raw_name.rstrip(b"\0").decode("ascii", errors="replace")
        if raw_size and raw_offset + raw_size > len(data):
            raise NotAUki(
                f"section {name} data ({raw_offset:#x}+{raw_size:#x}) runs past the end of the file"
            )
        sections.append(Section(name, virtual_size, raw_size, raw_offset))
    return sections


def section_data(data: bytes, section: Section) -> bytes:
    """The bytes a section holds in the file, without the file-alignment padding."""
    return data[
        section.raw_offset : section.raw_offset + min(section.virtual_size, section.raw_size)
    ]


def named_data(data: bytes, sections: list[Section], name: str) -> bytes:
    """The bytes of the first section called name; check_uki_sections requires it once."""
    return section_data(data, next(section for section in sections if section.name == name))


def check_uki_sections(data: bytes, sections: list[Section], expect_initrd: bool) -> None:
    """Require the UKI sections once each and non-empty, and .initrd exactly when expected.

    Refuse every section package-uki.sh does not wire yet.
    """
    names = [section.name for section in sections]
    required = REQUIRED_SECTIONS + ((".initrd",) if expect_initrd else ())
    for name in required:
        if name not in names:
            raise NotAUki(f"missing section {name}")
        if names.count(name) > 1:
            raise NotAUki(f"section {name} appears {names.count(name)} times")
        if not section_data(data, sections[names.index(name)]):
            raise NotAUki(f"section {name} is empty")
    if not expect_initrd and ".initrd" in names:
        raise NotAUki("carries an .initrd section although no initrd was passed")
    for name in UNWIRED_SECTIONS:
        if name in names:
            raise NotAUki(f"carries a {name} section, which package-uki.sh does not wire yet")
    if not named_data(data, sections, ".sdmagic").startswith(SDMAGIC_PREFIX):
        raise NotAUki(".sdmagic does not carry the systemd-stub marker")


def check_kernel_machine(data: bytes, sections: list[Section], arch: str) -> None:
    """A .linux kernel with an EFI stub (it starts with MZ) must be built for arch.

    The image's own machine type is the systemd-stub's. Without this check an x64 stub
    wrapped around a kernel for another architecture passes every other check.
    """
    kernel = named_data(data, sections, ".linux")
    if kernel[:2] != b"MZ":
        return
    try:
        check_machine(read_pe_header(kernel).machine, arch)
    except NotAUki as err:
        raise NotAUki(f"the .linux kernel: {err}") from err


def check_cmdline(data: bytes, sections: list[Section], expected: bytes) -> None:
    """Require .cmdline to hold exactly the requested command line, not, say, a file path."""
    actual = named_data(data, sections, ".cmdline")
    if actual != expected:
        raise NotAUki(
            f".cmdline holds {actual[:QUOTE_LIMIT]!r}, not the requested command line "
            f"{expected[:QUOTE_LIMIT]!r}"
        )


def check_uki(
    data: bytes, arch: str, expect_initrd: bool = False, expect_cmdline: bytes | None = None
) -> list[Section]:
    """Run every check on an image held in memory; raise NotAUki on the first failure."""
    header = read_coff_header(data, arch)
    check_optional_header(data, header.optional_offset, header.optional_size)
    table = header.optional_offset + header.optional_size
    sections = read_sections(data, table, header.section_count)
    check_uki_sections(data, sections, expect_initrd)
    check_kernel_machine(data, sections, arch)
    if expect_cmdline is not None:
        check_cmdline(data, sections, expect_cmdline)
    return sections


def text_or_file(value: str) -> bytes:
    """--expect-cmdline: literal text, or "@<file>" for the file's bytes, as ukify reads it."""
    if not value.startswith("@"):
        return value.encode("utf-8")
    try:
        return Path(value[1:]).read_bytes()
    except OSError as err:
        raise argparse.ArgumentTypeError(f"cannot read {value[1:]}: {err.strerror}") from err


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refuse a Unified Kernel Image that is not one.")
    parser.add_argument(
        "--arch", required=True, choices=sorted(MACHINE_BY_ARCH), help="target architecture"
    )
    parser.add_argument(
        "--expect-initrd",
        action="store_true",
        help="an initrd was passed to ukify: require .initrd (without this flag, .initrd is refused)",
    )
    parser.add_argument(
        "--expect-cmdline",
        type=text_or_file,
        metavar="TEXT|@FILE",
        help="require .cmdline to hold exactly TEXT, or the bytes of FILE",
    )
    parser.add_argument("image", type=Path, help="the .efi file to check")
    return parser.parse_args(argv)


def run(argv: list[str]) -> int:
    """Check one image and report; returns the exit status."""
    args = parse_arguments(argv)
    try:
        image = args.image.read_bytes()
        sections = check_uki(image, args.arch, args.expect_initrd, args.expect_cmdline)
    except (NotAUki, OSError) as err:
        print(f"check_uki: REFUSED {args.image}: {err}", file=sys.stderr)
        return 1
    names = " ".join(section.name for section in sections)
    print(f"check_uki: OK {args.image}: {args.arch} UKI, sections {names}")
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
