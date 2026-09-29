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
"""Boot a built x86_64 kernel under QEMU and refuse it unless it reaches userspace.

The smoke test compiles a static ``/init`` that prints the running kernel's release and powers
the machine off, packs it into a newc initramfs with a ``/dev/console`` node, and boots::

    qemu-system-x86_64 -kernel <vmlinuz> -initrd <initramfs> -nographic -no-reboot \\
        -append 'console=ttyS0 panic=-1 rdinit=/init'

It passes only when the serial console shows both ``Linux version <kernelrelease> `` and the
init's ``NUCLEUS-BOOT-SMOKE release=<kernelrelease>`` line within the timeout. A kernel that
panics exits QEMU at once (``panic=-1`` with ``-no-reboot``); one that hangs is killed at the
timeout. TCG is used unless ``--accel=kvm`` asks for KVM, so no special device is needed.

Exit status 0 when the kernel booted, 1 when it did not, 2 when the smoke test cannot run
(no kernel image, no QEMU, no C compiler).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

MARKER = "NUCLEUS-BOOT-SMOKE release="
INIT_SOURCE = r"""
#include <stdio.h>
#include <sys/reboot.h>
#include <sys/utsname.h>
#include <unistd.h>

int main(void)
{
    struct utsname name;

    if (uname(&name) == 0)
        printf("NUCLEUS-BOOT-SMOKE release=%s\n", name.release);
    fflush(stdout);
    sync();
    reboot(RB_POWER_OFF);
    return 1;
}
"""


class SmokeError(RuntimeError):
    """The smoke test cannot run."""


@dataclass(frozen=True)
class CpioEntry:
    """One member of a newc archive."""

    name: str
    mode: int
    data: bytes = b""
    rdev: tuple[int, int] = (0, 0)


def _pad(length: int) -> bytes:
    return b"\0" * (-length % 4)


def newc_archive(entries: Sequence[CpioEntry]) -> bytes:
    """A newc (SVR4, no CRC) cpio archive of the entries, owned by root, dated 0."""
    out = bytearray()
    members = [*entries, CpioEntry("TRAILER!!!", 0)]
    for ino, entry in enumerate(members, start=1):
        name = entry.name.encode() + b"\0"
        nlink = 2 if entry.mode & 0o040000 else 1
        fields = (ino, entry.mode, 0, 0, nlink, 0, len(entry.data), 0, 0, *entry.rdev, len(name), 0)
        header = b"070701" + b"".join(f"{value:08X}".encode() for value in fields)
        out += header + name + _pad(len(header) + len(name))
        out += entry.data + _pad(len(entry.data))
    return bytes(out)


def build_init(cc: str, workdir: Path) -> bytes:
    """Compile the static init program and return it."""
    if shutil.which(cc) is None:
        raise SmokeError(f"{cc} is required to build the smoke test's init and is not installed")
    source = workdir / "init.c"
    binary = workdir / "init"
    source.write_text(INIT_SOURCE, encoding="utf-8")
    result = subprocess.run(
        [cc, "-static", "-Os", "-o", str(binary), str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SmokeError(f"{cc} could not build a static init:\n{result.stderr.strip()}")
    return binary.read_bytes()


def initramfs(init: bytes) -> bytes:
    """The smallest root that boots: /init and the console it writes to."""
    return newc_archive(
        [
            CpioEntry("dev", 0o040755),
            CpioEntry("dev/console", 0o020600, rdev=(5, 1)),
            CpioEntry("init", 0o100755, init),
        ]
    )


def qemu_command(args: argparse.Namespace, initrd: Path) -> list[str]:
    """The QEMU invocation for one boot."""
    return [
        args.qemu,
        "-accel",
        args.accel,
        "-m",
        "512M",
        "-smp",
        "1",
        "-kernel",
        str(args.kernel),
        "-initrd",
        str(initrd),
        "-append",
        "console=ttyS0 panic=-1 rdinit=/init",
        "-nographic",
        "-no-reboot",
    ]


def verdict(console: str, kernelrelease: str) -> list[str]:
    """Every missing proof of a boot; an empty list means the kernel reached userspace."""
    missing = []
    if f"Linux version {kernelrelease} " not in console:
        missing.append(f"no 'Linux version {kernelrelease} ' banner on the console")
    lines = {line.strip() for line in console.splitlines()}
    if f"{MARKER}{kernelrelease}" not in lines:
        missing.append(f"init never printed '{MARKER}{kernelrelease}'")
    return missing


def boot(args: argparse.Namespace, initrd: Path) -> tuple[str, bool]:
    """Run QEMU once; return the console and whether it was killed at the timeout."""
    try:
        result = subprocess.run(
            qemu_command(args, initrd),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=args.timeout,
            check=False,
        )
        return result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace"), False
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b""
        return partial.decode("utf-8", "replace"), True


def run(args: argparse.Namespace) -> tuple[list[str], str]:
    """Build the initramfs, boot, and judge the console."""
    if not args.kernel.is_file() or args.kernel.stat().st_size == 0:
        raise SmokeError(f"{args.kernel} is not a kernel image")
    if shutil.which(args.qemu) is None:
        raise SmokeError(f"{args.qemu} is required for the boot smoke test and is not installed")
    with tempfile.TemporaryDirectory(prefix="boot-smoke-") as tmp:
        workdir = Path(tmp)
        init = args.init_binary.read_bytes() if args.init_binary else build_init(args.cc, workdir)
        initrd = workdir / "initramfs.cpio"
        initrd.write_bytes(initramfs(init))
        console, timed_out = boot(args, initrd)
    missing = verdict(console, args.kernelrelease)
    if timed_out and missing:
        missing.append(f"QEMU was killed after {args.timeout}s")
    return missing, console


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--kernel", type=Path, required=True, help="the vmlinuz to boot")
    parser.add_argument("--kernelrelease", required=True, help="the release it must report")
    parser.add_argument("--timeout", type=int, default=180, help="seconds before QEMU is killed")
    parser.add_argument("--log", type=Path, help="write the serial console here")
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    parser.add_argument("--accel", choices=("tcg", "kvm"), default="tcg")
    parser.add_argument("--cc", default=os.environ.get("CC", "gcc"), help="builds the static init")
    parser.add_argument("--init-binary", type=Path, help="a prebuilt init instead of compiling one")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.timeout <= 3600:
        print("boot_smoke: --timeout must be between 1 and 3600 seconds", file=sys.stderr)
        return 2
    try:
        missing, console = run(args)
    except (SmokeError, OSError) as exc:
        print(f"boot_smoke: {exc}", file=sys.stderr)
        return 2
    if args.log:
        args.log.write_text(console, encoding="utf-8")
    if missing:
        print(f"Refused: {args.kernel} did not boot to userspace:", file=sys.stderr)
        for line in missing:
            print(f"  - {line}", file=sys.stderr)
        print("--- last console lines ---", file=sys.stderr)
        print("\n".join(console.splitlines()[-25:]), file=sys.stderr)
        return 1
    print(f"==> Boot smoke passed: {args.kernel} booted {args.kernelrelease} to userspace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
