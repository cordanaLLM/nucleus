"""scripts/boot_smoke.py: the initramfs it packs, the verdict it reaches, and its refusals.

These tests are hermetic: QEMU is replaced by a stub that prints a console transcript, and the
init is a placeholder file, so no kernel and no emulator is needed. The real boot of a built
kernel runs in the build-matrix workflow on every x86_64 leg, and in the evidence for issue #18.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "boot_smoke.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import boot_smoke  # noqa: E402

KR = "7.2.8-lusoris1-mainstream"
BANNER = f"[    0.000000] Linux version {KR} (nucleus@forge) (x86_64-linux-gnu-gcc 15.2.0) #lusoris1 SMP\n"
MARKER = f"NUCLEUS-BOOT-SMOKE release={KR}\n"


def _read_newc(data: bytes) -> list[tuple[str, int, bytes, tuple[int, int]]]:
    """Parse a newc archive independently of the writer."""
    entries, offset = [], 0
    # Every entry, the trailer included, takes at least its 110-byte header.
    for _ in range(len(data) // 110):
        assert data[offset : offset + 6] == b"070701", offset
        fields = [int(data[offset + 6 + 8 * i : offset + 14 + 8 * i], 16) for i in range(13)]
        mode, size, rdev, namesize = fields[1], fields[6], (fields[9], fields[10]), fields[11]
        name_start = offset + 110
        name = data[name_start : name_start + namesize - 1].decode()
        assert data[name_start + namesize - 1] == 0
        data_start = name_start + namesize + (-(110 + namesize) % 4)
        assert data_start % 4 == 0
        body = data[data_start : data_start + size]
        offset = data_start + size + (-size % 4)
        if name == "TRAILER!!!":
            assert offset == len(data)
            return entries
        entries.append((name, mode, body, rdev))
    pytest.fail("the archive ends without a TRAILER!!! entry")


def test_initramfs_holds_the_init_and_a_console_node():
    entries = _read_newc(boot_smoke.initramfs(b"\x7fELF-init"))
    assert [(name, oct(mode)) for name, mode, _, _ in entries] == [
        ("dev", "0o40755"),
        ("dev/console", "0o20600"),
        ("init", "0o100755"),
    ]
    assert entries[1][3] == (5, 1)
    assert entries[2][2] == b"\x7fELF-init"


def test_newc_padding_holds_for_every_length():
    for length in range(9):
        entries = _read_newc(boot_smoke.newc_archive([boot_smoke.CpioEntry("f" * (length + 1), 0o100644, b"x" * length)]))
        assert entries[0][2] == b"x" * length


@pytest.mark.parametrize(
    ("console", "missing"),
    [
        (BANNER + MARKER, []),
        (MARKER, ["no 'Linux version"]),
        (BANNER, ["init never printed"]),
        (BANNER.replace(KR, "7.2.80-lusoris1-mainstream") + MARKER, ["no 'Linux version"]),
        (BANNER + MARKER.replace(KR, "7.2.8-lusoris1-realtime"), ["init never printed"]),
    ],
)
def test_verdict_needs_the_banner_and_the_init_line_for_this_release(console, missing):
    found = boot_smoke.verdict(console, KR)
    assert len(found) == len(missing)
    for text, expected in zip(found, missing, strict=True):
        assert expected in text


def test_the_boot_cannot_hang_or_reboot_into_a_loop():
    args = boot_smoke._parser().parse_args(["--kernel=/k", f"--kernelrelease={KR}"])
    command = boot_smoke.qemu_command(args, Path("/initrd"))
    assert "-no-reboot" in command and "-nographic" in command
    append = command[command.index("-append") + 1]
    assert "panic=-1" in append and "console=ttyS0" in append and "rdinit=/init" in append
    assert command[command.index("-initrd") + 1] == "/initrd"
    assert args.accel == "tcg" and 1 <= args.timeout <= 3600


def _stub(tmp_path: Path, body: str) -> Path:
    stub = tmp_path / "qemu-stub"
    stub.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _cli(tmp_path: Path, stub: Path, *extra: str):
    kernel = tmp_path / "vmlinuz"
    kernel.write_bytes(b"MZ-kernel")
    init = tmp_path / "init"
    init.write_bytes(b"\x7fELF")
    return subprocess.run(
        [sys.executable, str(SCRIPT), f"--kernel={kernel}", f"--kernelrelease={KR}",
         f"--qemu={stub}", f"--init-binary={init}", f"--log={tmp_path / 'console.log'}", *extra],
        capture_output=True, text=True, check=False,
    )


def test_a_kernel_that_reaches_userspace_passes(tmp_path):
    stub = _stub(tmp_path, f"printf '%s' '{BANNER}{MARKER}'")
    result = _cli(tmp_path, stub)
    assert result.returncode == 0, result.stderr
    assert f"booted {KR} to userspace" in result.stdout
    assert MARKER.strip() in (tmp_path / "console.log").read_text(encoding="utf-8")


def test_a_kernel_that_panics_is_refused(tmp_path):
    stub = _stub(tmp_path, f"printf '%s' '{BANNER}Kernel panic - not syncing: No working init found.'")
    result = _cli(tmp_path, stub)
    assert result.returncode == 1
    assert "init never printed" in result.stderr and "Kernel panic" in result.stderr


def test_a_kernel_that_hangs_is_killed_at_the_timeout_and_refused(tmp_path):
    stub = _stub(tmp_path, f"printf '%s' '{BANNER}'; exec sleep 30")
    result = _cli(tmp_path, stub, "--timeout=1")
    assert result.returncode == 1
    assert "QEMU was killed after 1s" in result.stderr


def test_the_smoke_test_refuses_to_run_without_qemu(tmp_path):
    result = _cli(tmp_path, _stub(tmp_path, "exit 0"), "--qemu=/nonexistent/qemu-system-x86_64")
    assert result.returncode == 2
    assert "is required for the boot smoke test" in result.stderr


def test_the_smoke_test_refuses_an_empty_kernel_image(tmp_path):
    kernel = tmp_path / "vmlinuz"
    kernel.write_bytes(b"")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), f"--kernel={kernel}", f"--kernelrelease={KR}",
         f"--qemu={_stub(tmp_path, 'exit 0')}"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "is not a kernel image" in result.stderr
