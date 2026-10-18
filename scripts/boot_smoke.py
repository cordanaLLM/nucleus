#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Boot a built kernel under QEMU and refuse it unless it reaches userspace.

The smoke test compiles a static ``/init`` that prints the running kernel's release and powers
the machine off, packs it into a newc initramfs with a ``/dev/console`` node, and boots:

    x86_64:
      qemu-system-x86_64 -kernel <vmlinuz> -initrd <initramfs> -nographic -no-reboot \\
          -append 'console=ttyS0 panic=-1 rdinit=/init'

    arm64:
      qemu-system-aarch64 -M virt -cpu max -nic none -kernel <Image> -initrd <initramfs> -nographic -no-reboot \\
          -append 'console=ttyAMA0 panic=-1 rdinit=/init'

    riscv64:
      qemu-system-riscv64 -M virt -bios default -nic none -kernel <Image> -initrd <initramfs> -nographic -no-reboot \\
          -append 'console=ttyS0 panic=-1 rdinit=/init'

It passes only when the serial console shows both ``Linux version <kernelrelease> `` and the
init's ``NUCLEUS-BOOT-SMOKE release=<kernelrelease>`` line within the timeout. A kernel that
panics exits QEMU at once (``panic=-1`` with ``-no-reboot``); one that hangs is killed at the
timeout. TCG is used unless ``--accel=kvm`` asks for KVM, so no special device is needed.

Exit status 0 when the kernel booted, 1 when it did not, 2 when the smoke test cannot run
(no kernel image, no QEMU, no C compiler).

``--judge-log=<log> --kernelrelease=<r>`` judges a console an earlier boot wrote, without
booting; the build matrix runs it on each leg's log with the leg's release, which must pass,
and with a release the leg did not build, which must be refused.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

MARKER = "NUCLEUS-BOOT-SMOKE release="
MAX_IMAGE_BYTES = 256 * 1024 * 1024

DEFAULT_QEMU: dict[str, str] = {
    "x86_64": "qemu-system-x86_64",
    "arm64": "qemu-system-aarch64",
    "riscv64": "qemu-system-riscv64",
}

DEFAULT_CONSOLE: dict[str, str] = {
    "x86_64": "ttyS0",
    "arm64": "ttyAMA0",
    "riscv64": "ttyS0",
}

ARCH_MACHINE_ARGS: dict[str, list[str]] = {
    "x86_64": [],
    "arm64": ["-M", "virt", "-cpu", "max", "-nic", "none"],
    "riscv64": ["-M", "virt", "-bios", "default", "-nic", "none"],
}

INIT_SOURCE = r"""
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/utsname.h>
#include <termios.h>
#include <unistd.h>

static void load_modules(void)
{
    DIR *dir = opendir("/modules");
    if (!dir)
        return;
    int loaded = 0;
    for (int i = 0; i < 32 && loaded < 16; i++) {
        struct dirent *ent = readdir(dir);
        if (!ent)
            break;
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0)
            continue;
        char path[512];
        snprintf(path, sizeof(path), "/modules/%s", ent->d_name);
        struct stat st;
        if (stat(path, &st) == 0 && S_ISREG(st.st_mode)) {
            loaded++;
            int fd = open(path, O_RDONLY | O_CLOEXEC);
            int rc = 0;
            if (fd >= 0) {
                if (syscall(SYS_finit_module, fd, "", 0) != 0)
                    rc = errno;
                close(fd);
            } else {
                rc = errno;
            }
            printf("NUCLEUS-BOOT-SMOKE module=%s rc=%d\n", ent->d_name, rc);
            fflush(stdout);
        }
    }
    closedir(dir);
}

static void report_congestion(void)
{
    int fd = open("/proc/sys/net/ipv4/tcp_available_congestion_control", O_RDONLY);
    if (fd >= 0) {
        char buf[512];
        ssize_t n = read(fd, buf, sizeof(buf) - 1);
        if (n >= 0) {
            buf[n] = '\0';
            if (n > 0 && buf[n - 1] == '\n')
                buf[n - 1] = '\0';
            printf("NUCLEUS-BOOT-SMOKE congestion=%s\n", buf);
        } else {
            printf("NUCLEUS-BOOT-SMOKE congestion=unreadable errno=%d\n", errno);
        }
        close(fd);
    } else {
        printf("NUCLEUS-BOOT-SMOKE congestion=unreadable errno=%d\n", errno);
    }
    fflush(stdout);
}

static void report_sched_ext(void)
{
    int fd = open("/sys/kernel/sched_ext/state", O_RDONLY);
    if (fd >= 0) {
        char buf[256];
        ssize_t n = read(fd, buf, sizeof(buf) - 1);
        if (n >= 0) {
            buf[n] = '\0';
            int start = 0;
            for (int i = 0; i < n && isspace((unsigned char)buf[i]); i++)
                start++;
            for (int i = (int)n - 1; i >= start && isspace((unsigned char)buf[i]); i--)
                buf[i] = '\0';
            printf("NUCLEUS-BOOT-SMOKE sched_ext=%s\n", buf + start);
        } else {
            printf("NUCLEUS-BOOT-SMOKE sched_ext=unreadable errno=%d\n", errno);
        }
        close(fd);
    } else {
        printf("NUCLEUS-BOOT-SMOKE sched_ext=unreadable errno=%d\n", errno);
    }
    fflush(stdout);
}

int main(void)
{
    struct utsname name;

    if (uname(&name) == 0)
        printf("NUCLEUS-BOOT-SMOKE release=%s\n", name.release);
    fflush(stdout);

    mount("proc", "/proc", "proc", 0, 0);
    mount("sysfs", "/sys", "sysfs", 0, 0);

    load_modules();
    report_congestion();
    report_sched_ext();

    tcdrain(STDOUT_FILENO);
    sync();
    reboot(RB_POWER_OFF);
    return 1;
}
"""

INIT_SOURCE_ARM64 = r"""/* Freestanding init for arm64 (AArch64). */
#define SYS_uname 160
#define SYS_write 64
#define SYS_sync 81
#define SYS_ioctl 29
#define TCSBRK 0x5409
#define SYS_reboot 142
#define SYS_exit 93

#define LINUX_REBOOT_MAGIC1 0xfee1dead
#define LINUX_REBOOT_MAGIC2 672274793
#define LINUX_REBOOT_CMD_POWER_OFF 0x4321fedc

struct new_utsname {
    char sysname[65];
    char nodename[65];
    char release[65];
    char version[65];
    char machine[65];
    char domainname[65];
};

static inline long sys_uname(struct new_utsname *buf) {
    register long x8 __asm__("x8") = SYS_uname;
    register long x0 __asm__("x0") = (long)buf;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8) : "memory");
    return x0;
}

static inline long sys_write(int fd, const void *buf, unsigned long count) {
    register long x8 __asm__("x8") = SYS_write;
    register long x0 __asm__("x0") = (long)fd;
    register long x1 __asm__("x1") = (long)buf;
    register long x2 __asm__("x2") = (long)count;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}

static inline long sys_ioctl(int fd, unsigned long cmd, unsigned long arg) {
    register long x8 __asm__("x8") = SYS_ioctl;
    register long x0 __asm__("x0") = (long)fd;
    register long x1 __asm__("x1") = (long)cmd;
    register long x2 __asm__("x2") = (long)arg;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}

static inline long sys_sync(void) {
    register long x8 __asm__("x8") = SYS_sync;
    register long x0 __asm__("x0");
    __asm__ volatile("svc #0" : "=r"(x0) : "r"(x8) : "memory");
    return x0;
}

static inline long sys_reboot(int magic1, int magic2, int cmd, void *arg) {
    register long x8 __asm__("x8") = SYS_reboot;
    register long x0 __asm__("x0") = (long)magic1;
    register long x1 __asm__("x1") = (long)magic2;
    register long x2 __asm__("x2") = (long)cmd;
    register long x3 __asm__("x3") = (long)arg;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3) : "memory");
    return x0;
}

static inline void sys_exit(int code) {
    register long x8 __asm__("x8") = SYS_exit;
    register long x0 __asm__("x0") = (long)code;
    __asm__ volatile("svc #0" : : "r"(x0), "r"(x8) : "memory");
    for (;;) {}
}

static unsigned long mystrlen(const char *s) {
    unsigned long n = 0;
    while (*s++) n++;
    return n;
}

void _start(void) {
    struct new_utsname name;
    if (sys_uname(&name) == 0) {
        static const char prefix[] = "NUCLEUS-BOOT-SMOKE release=";
        sys_write(1, prefix, sizeof(prefix) - 1);
        sys_write(1, name.release, mystrlen(name.release));
        sys_write(1, "\n", 1);
    }
    sys_ioctl(1, TCSBRK, 1);
    sys_sync();
    sys_reboot(LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2, LINUX_REBOOT_CMD_POWER_OFF, 0);
    sys_exit(1);
}
"""

INIT_SOURCE_RISCV64 = r"""/* Freestanding init for riscv64. */
#define SYS_uname 160
#define SYS_write 64
#define SYS_sync 81
#define SYS_ioctl 29
#define TCSBRK 0x5409
#define SYS_reboot 142
#define SYS_exit 93

#define LINUX_REBOOT_MAGIC1 0xfee1dead
#define LINUX_REBOOT_MAGIC2 672274793
#define LINUX_REBOOT_CMD_POWER_OFF 0x4321fedc

struct new_utsname {
    char sysname[65];
    char nodename[65];
    char release[65];
    char version[65];
    char machine[65];
    char domainname[65];
};

static inline long sys_uname(struct new_utsname *buf) {
    register long a7 __asm__("a7") = SYS_uname;
    register long a0 __asm__("a0") = (long)buf;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a7) : "memory");
    return a0;
}

static inline long sys_write(int fd, const void *buf, unsigned long count) {
    register long a7 __asm__("a7") = SYS_write;
    register long a0 __asm__("a0") = (long)fd;
    register long a1 __asm__("a1") = (long)buf;
    register long a2 __asm__("a2") = (long)count;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a7), "r"(a1), "r"(a2) : "memory");
    return a0;
}

static inline long sys_ioctl(int fd, unsigned long cmd, unsigned long arg) {
    register long a7 __asm__("a7") = SYS_ioctl;
    register long a0 __asm__("a0") = (long)fd;
    register long a1 __asm__("a1") = (long)cmd;
    register long a2 __asm__("a2") = (long)arg;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a7), "r"(a1), "r"(a2) : "memory");
    return a0;
}

static inline long sys_sync(void) {
    register long a7 __asm__("a7") = SYS_sync;
    register long a0 __asm__("a0");
    __asm__ volatile("ecall" : "=r"(a0) : "r"(a7) : "memory");
    return a0;
}

static inline long sys_reboot(int magic1, int magic2, int cmd, void *arg) {
    register long a7 __asm__("a7") = SYS_reboot;
    register long a0 __asm__("a0") = (long)magic1;
    register long a1 __asm__("a1") = (long)magic2;
    register long a2 __asm__("a2") = (long)cmd;
    register long a3 __asm__("a3") = (long)arg;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a7), "r"(a1), "r"(a2), "r"(a3) : "memory");
    return a0;
}

static inline void sys_exit(int code) {
    register long a7 __asm__("a7") = SYS_exit;
    register long a0 __asm__("a0") = (long)code;
    __asm__ volatile("ecall" : : "r"(a0), "r"(a7) : "memory");
    for (;;) {}
}

static unsigned long mystrlen(const char *s) {
    unsigned long n = 0;
    while (*s++) n++;
    return n;
}

void _start(void) {
    struct new_utsname name;
    if (sys_uname(&name) == 0) {
        static const char prefix[] = "NUCLEUS-BOOT-SMOKE release=";
        sys_write(1, prefix, sizeof(prefix) - 1);
        sys_write(1, name.release, mystrlen(name.release));
        sys_write(1, "\n", 1);
    }
    sys_ioctl(1, TCSBRK, 1);
    sys_sync();
    sys_reboot(LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2, LINUX_REBOOT_CMD_POWER_OFF, 0);
    sys_exit(1);
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


def init_source(arch: str) -> str:
    """Return the C source code for the init program for the given architecture."""
    if arch == "x86_64":
        return INIT_SOURCE
    if arch == "arm64":
        return INIT_SOURCE_ARM64
    if arch == "riscv64":
        return INIT_SOURCE_RISCV64
    raise SmokeError(f"unsupported architecture for init: {arch}")


def default_cc(arch: str) -> str:
    """Return the default C compiler for building the init on the given architecture."""
    if arch == "x86_64":
        return os.environ.get("CC", "gcc")
    if "CC" in os.environ:
        return os.environ["CC"]
    machine = platform.machine().lower()
    if arch == "arm64":
        if machine in ("aarch64", "arm64"):
            return "gcc"
        return "aarch64-linux-gnu-gcc"
    if arch == "riscv64":
        if machine in ("riscv64", "rv64"):
            return "gcc"
        return "riscv64-linux-gnu-gcc"
    return "gcc"


def decompress_kernel(kernel_path: Path, arch: str, workdir: Path) -> Path:
    """Decompress a gzip kernel to a temp Image before boot for arm64/riscv64."""
    if arch == "x86_64":
        return kernel_path
    try:
        data = kernel_path.read_bytes()
    except OSError as exc:
        raise SmokeError(f"cannot read kernel image {kernel_path}: {exc}") from exc
    if data[:2] != b"\x1f\x8b":
        raise SmokeError(f"{kernel_path}: not a gzip-compressed Image for {arch}")
    inflater = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        image = inflater.decompress(data, MAX_IMAGE_BYTES)
    except zlib.error as exc:
        raise SmokeError(f"{kernel_path}: gzip decompression failed: {exc}") from exc
    if inflater.unconsumed_tail or not inflater.eof:
        raise SmokeError(
            f"{kernel_path}: not a complete gzip-compressed Image within {MAX_IMAGE_BYTES} bytes"
        )
    dest = workdir / "Image"
    dest.write_bytes(image)
    return dest


def build_init(cc: str, workdir: Path, arch: str = "x86_64") -> bytes:
    """Compile the static init program and return it."""
    if shutil.which(cc) is None:
        raise SmokeError(f"{cc} is required to build the smoke test's init and is not installed")
    source = workdir / "init.c"
    binary = workdir / "init"
    source.write_text(init_source(arch), encoding="utf-8")
    if arch == "x86_64":
        cmd = [cc, "-static", "-Os", "-o", str(binary), str(source)]
    else:
        cmd = [
            cc,
            "-static",
            "-nostdlib",
            "-ffreestanding",
            "-fno-stack-protector",
            "-Os",
            "-o",
            str(binary),
            str(source),
        ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SmokeError(f"{cc} could not build a static init:\n{result.stderr.strip()}")
    return binary.read_bytes()


def build_initramfs(
    cc: str,
    arch: str = "x86_64",
    init_binary: Path | None = None,
    modules: Sequence[Path | str] = (),
) -> bytes:
    """Compile the static init (or read init_binary) and pack into a newc initramfs."""
    if init_binary is not None:
        init = init_binary.read_bytes()
    else:
        with tempfile.TemporaryDirectory(prefix="boot-smoke-init-") as tmp:
            init = build_init(cc, Path(tmp), arch)
    return initramfs(init, modules)


def initramfs(init: bytes, modules: Sequence[Path | str] = ()) -> bytes:
    """The smallest root that boots: /init and the console it writes to."""
    if len(modules) > 16:
        raise SmokeError(f"at most 16 modules supported, got {len(modules)}")

    module_entries = []
    for m in modules:
        if not m or not str(m).strip():
            raise SmokeError("empty module path")
        p = Path(m)
        name = p.name
        if any(name.endswith(ext) for ext in (".ko.zst", ".ko.xz", ".ko.gz")):
            raise SmokeError(f"{p}: compressed modules are not supported in initramfs")
        if not name.endswith(".ko"):
            raise SmokeError(f"{p}: module must end in .ko")
        if not p.is_file():
            raise SmokeError(f"{p}: module does not exist")
        if p.stat().st_size == 0:
            raise SmokeError(f"{p}: module is empty")
        module_entries.append(CpioEntry(f"modules/{name}", 0o100644, p.read_bytes()))

    return newc_archive(
        [
            CpioEntry("dev", 0o040755),
            CpioEntry("dev/console", 0o020600, rdev=(5, 1)),
            CpioEntry("init", 0o100755, init),
            CpioEntry("proc", 0o040755),
            CpioEntry("sys", 0o040755),
            CpioEntry("modules", 0o040755),
            *module_entries,
        ]
    )


def qemu_command(args: argparse.Namespace, initrd: Path, kernel: Path | None = None) -> list[str]:
    """The QEMU invocation for one boot."""
    arch = getattr(args, "arch", "x86_64")
    qemu = (
        args.qemu if getattr(args, "qemu", None) else DEFAULT_QEMU.get(arch, "qemu-system-x86_64")
    )
    console = args.console if getattr(args, "console", None) else DEFAULT_CONSOLE.get(arch, "ttyS0")
    kernel_path = kernel if kernel is not None else args.kernel
    machine_args = ARCH_MACHINE_ARGS.get(arch, [])
    cmd = [qemu]
    cmd.extend(machine_args)
    cmd.extend(
        [
            "-accel",
            args.accel,
            "-m",
            "512M",
            "-smp",
            "1",
            "-kernel",
            str(kernel_path),
            "-initrd",
            str(initrd),
            "-append",
            f"console={console} panic=-1 rdinit=/init",
            "-nographic",
            "-no-reboot",
        ]
    )
    return cmd


def _check_congestion(console: str, required: Sequence[str], missing: list[str]) -> None:
    found_tokens: list[str] | None = None
    prefix = "NUCLEUS-BOOT-SMOKE congestion="
    for line in console.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            val = line[len(prefix) :]
            cleaned = re.sub(r"\[\s*\d+\.\d+\].*$", "", val).strip()
            found_tokens = cleaned.split()
            break
    if found_tokens is None:
        for name in required:
            missing.append(
                f"congestion control {name} not registered (observed: no congestion line)"
            )
        return
    for name in required:
        if name not in found_tokens:
            missing.append(f"congestion control {name} not registered (observed: {found_tokens})")


def _check_sched_ext(console: str, missing: list[str]) -> None:
    prefix = "NUCLEUS-BOOT-SMOKE sched_ext="
    valid_states = {"disabled", "enabled", "prepping", "enabling", "disabling"}
    found_state: str | None = None
    for line in console.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            val = line[len(prefix) :]
            found_state = re.sub(r"\[\s*\d+\.\d+\].*$", "", val).strip()
            break
    if found_state is None:
        missing.append("no sched_ext state on the console (observed: no sched_ext line)")
    elif found_state not in valid_states:
        missing.append(f"no sched_ext state on the console (observed: {found_state})")


def _check_modules(console: str, modules: Sequence[Path | str], missing: list[str]) -> None:
    """Every module the initramfs carried must report rc=0; a silent init proves nothing."""
    loaded: dict[str, str] = {}
    for line in console.splitlines():
        m = re.match(r"^NUCLEUS-BOOT-SMOKE module=(\S+)\s+rc=(-?\d+)", line.strip())
        if m:
            loaded.setdefault(m.group(1), m.group(2))
    for module in modules:
        name = Path(module).name
        rc = loaded.get(name)
        if rc is None:
            missing.append(f"module {name} never reported a load")
        elif int(rc) != 0:
            missing.append(f"module {name} failed to load: rc {rc}")


def verdict(
    console: str,
    kernelrelease: str,
    congestion: Sequence[str] = (),
    sched_ext: bool = False,
    modules: Sequence[Path | str] = (),
) -> list[str]:
    """Every missing proof of a boot; an empty list means the kernel reached userspace."""
    missing = []
    if f"Linux version {kernelrelease} " not in console:
        missing.append(f"no 'Linux version {kernelrelease} ' banner on the console")
    # The kernel may print onto the init's line before its newline reaches the console
    # ("...release=<r>[    2.866941] input: ..."), so a printk timestamp may follow the
    # release; any other text after it is a different release and is refused.
    init_line = re.compile(rf"{re.escape(MARKER + kernelrelease)}(\[\s*\d+\.\d+\].*)?")
    if not any(init_line.fullmatch(line.strip()) for line in console.splitlines()):
        missing.append(f"init never printed '{MARKER}{kernelrelease}'")

    if congestion:
        _check_congestion(console, congestion, missing)
    if sched_ext:
        _check_sched_ext(console, missing)
    if modules:
        _check_modules(console, modules, missing)

    return missing


def boot(args: argparse.Namespace, initrd: Path, kernel: Path | None = None) -> tuple[str, bool]:
    """Run QEMU once; return the console and whether it was killed at the timeout."""
    try:
        result = subprocess.run(
            qemu_command(args, initrd, kernel),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=args.timeout,
            check=False,
        )
        return result.stdout.decode("utf-8", "replace") + result.stderr.decode(
            "utf-8", "replace"
        ), False
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b""
        return partial.decode("utf-8", "replace"), True


def run(args: argparse.Namespace) -> tuple[list[str], str]:
    """Build the initramfs, boot, and judge the console."""
    if not args.kernel.is_file() or args.kernel.stat().st_size == 0:
        raise SmokeError(f"{args.kernel} is not a kernel image")
    arch = getattr(args, "arch", "x86_64")
    qemu = (
        args.qemu if getattr(args, "qemu", None) else DEFAULT_QEMU.get(arch, "qemu-system-x86_64")
    )
    if shutil.which(qemu) is None:
        raise SmokeError(f"{qemu} is required for the boot smoke test and is not installed")
    cc = args.cc if getattr(args, "cc", None) else default_cc(arch)
    with tempfile.TemporaryDirectory(prefix="boot-smoke-") as tmp:
        workdir = Path(tmp)
        kernel = decompress_kernel(args.kernel, arch, workdir)
        initrd = workdir / "initramfs.cpio"
        initrd.write_bytes(
            build_initramfs(
                cc,
                arch,
                getattr(args, "init_binary", None),
                modules=args.module,
            )
        )
        console, timed_out = boot(args, initrd, kernel)
    missing = verdict(
        console,
        args.kernelrelease,
        congestion=args.require_congestion,
        sched_ext=args.require_sched_ext,
        modules=args.module,
    )
    if timed_out and missing:
        missing.append(f"QEMU was killed after {args.timeout}s")
    return missing, console


def judge_log(
    log: Path,
    kernelrelease: str,
    congestion: Sequence[str] = (),
    sched_ext: bool = False,
    modules: Sequence[Path | str] = (),
) -> int:
    """Judge a console a boot already wrote; the matrix plants a wrong release with it."""
    try:
        console = log.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"boot_smoke: {exc}", file=sys.stderr)
        return 2
    missing = verdict(
        console,
        kernelrelease,
        congestion=congestion,
        sched_ext=sched_ext,
        modules=modules,
    )
    if missing:
        print(f"Refused: {log} does not show {kernelrelease} reaching userspace:", file=sys.stderr)
        for line in missing:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"==> {log} shows {kernelrelease} reaching userspace")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--arch",
        choices=("x86_64", "arm64", "riscv64"),
        default="x86_64",
        help="target architecture (default: x86_64)",
    )
    parser.add_argument("--kernel", type=Path, help="the vmlinuz to boot")
    parser.add_argument("--kernelrelease", help="the release it must report")
    parser.add_argument("--timeout", type=int, default=180, help="seconds before QEMU is killed")
    parser.add_argument("--log", type=Path, help="write the serial console here")
    parser.add_argument("--qemu", help="QEMU binary (default: per architecture)")
    parser.add_argument("--console", help="serial console device (default: per architecture)")
    parser.add_argument("--accel", choices=("tcg", "kvm"), default="tcg")
    parser.add_argument("--cc", help="builds the static init (default: per architecture)")
    parser.add_argument("--init-binary", type=Path, help="a prebuilt init instead of compiling one")
    parser.add_argument(
        "--write-initramfs",
        type=Path,
        help="write the static-init initramfs for --arch to this path and exit 0 without booting",
    )
    parser.add_argument(
        "--judge-log",
        type=Path,
        help="judge this console log against --kernelrelease without booting",
    )
    parser.add_argument(
        "--module",
        action="append",
        type=Path,
        default=[],
        help="kernel module (.ko) to include in initramfs and load at boot (x86_64 only)",
    )
    parser.add_argument(
        "--require-congestion",
        action="append",
        default=[],
        help="require congestion control algorithm registered in tcp_available_congestion_control (x86_64 only)",
    )
    parser.add_argument(
        "--require-sched-ext",
        action="store_true",
        default=False,
        help="require sched_ext state readable in /sys/kernel/sched_ext/state (x86_64 only)",
    )
    return parser


def _handle_write_initramfs(args: argparse.Namespace) -> int:
    target = args.write_initramfs
    if target.exists():
        print(f"boot_smoke: refusing to overwrite existing initramfs: {target}", file=sys.stderr)
        return 2
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        data = build_initramfs(
            args.cc,
            args.arch,
            getattr(args, "init_binary", None),
            modules=args.module,
        )
        target.write_bytes(data)
    except (SmokeError, OSError) as exc:
        print(f"boot_smoke: {exc}", file=sys.stderr)
        return 2
    print(f"==> Wrote {args.arch} initramfs to {target}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.arch != "x86_64" and (args.module or args.require_congestion or args.require_sched_ext):
        print(
            "boot_smoke: error: --module, --require-congestion, and --require-sched-ext are only supported on --arch=x86_64",
            file=sys.stderr,
        )
        return 2
    if args.cc is None:
        args.cc = default_cc(args.arch)
    if args.write_initramfs is not None:
        return _handle_write_initramfs(args)

    if args.judge_log is not None and args.kernelrelease is not None:
        return judge_log(
            args.judge_log,
            args.kernelrelease,
            congestion=args.require_congestion,
            sched_ext=args.require_sched_ext,
            modules=args.module,
        )
    if args.kernel is None or args.kernelrelease is None:
        print(
            "boot_smoke: error: the following arguments are required: --kernel, --kernelrelease",
            file=sys.stderr,
        )
        return 2

    if not 1 <= args.timeout <= 3600:
        print("boot_smoke: --timeout must be between 1 and 3600 seconds", file=sys.stderr)
        return 2
    if args.qemu is None:
        args.qemu = DEFAULT_QEMU[args.arch]
    if args.console is None:
        args.console = DEFAULT_CONSOLE[args.arch]
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
