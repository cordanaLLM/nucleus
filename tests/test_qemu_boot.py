"""scripts/boot_smoke.py: the initramfs it packs, the verdict it reaches, and its refusals.

These tests are hermetic: QEMU is replaced by a stub that prints a console transcript, and the
init is a placeholder file, so no kernel and no emulator is needed. The real boot of a built
kernel runs in the build-matrix workflow on every x86_64 leg, and in the evidence for issue #18.
"""

from __future__ import annotations

import os
import re
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
            break
        entries.append((name, mode, body, rdev))
    else:
        pytest.fail("the archive ends without a TRAILER!!! entry")
    return entries


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
        entries = _read_newc(
            boot_smoke.newc_archive(
                [boot_smoke.CpioEntry("f" * (length + 1), 0o100644, b"x" * length)]
            )
        )
        assert entries[0][2] == b"x" * length


@pytest.mark.parametrize(
    ("console", "missing"),
    [
        (BANNER + MARKER, []),
        (MARKER, ["no 'Linux version"]),
        (BANNER, ["init never printed"]),
        (BANNER.replace(KR, "7.2.80-lusoris1-mainstream") + MARKER, ["no 'Linux version"]),
        (BANNER + MARKER.replace(KR, "7.2.8-lusoris1-realtime"), ["init never printed"]),
        # The kernel printed onto the init's line (build-matrix run 38525968854, lts x86_64).
        (BANNER + MARKER.rstrip("\n") + "[    2.866941] input: ImExPS/2 Mouse\n", []),
        (BANNER + MARKER.rstrip("\n") + "2\n", ["init never printed"]),
        (BANNER + MARKER.rstrip("\n") + " trailing text\n", ["init never printed"]),
        (BANNER + "noise " + MARKER, ["init never printed"]),
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
        [
            sys.executable,
            str(SCRIPT),
            f"--kernel={kernel}",
            f"--kernelrelease={KR}",
            f"--qemu={stub}",
            f"--init-binary={init}",
            f"--log={tmp_path / 'console.log'}",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_kernel_that_reaches_userspace_passes(tmp_path):
    stub = _stub(tmp_path, f"printf '%s' '{BANNER}{MARKER}'")
    result = _cli(tmp_path, stub)
    assert result.returncode == 0, result.stderr
    assert f"booted {KR} to userspace" in result.stdout
    assert MARKER.strip() in (tmp_path / "console.log").read_text(encoding="utf-8")


def test_a_kernel_that_panics_is_refused(tmp_path):
    stub = _stub(
        tmp_path, f"printf '%s' '{BANNER}Kernel panic - not syncing: No working init found.'"
    )
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
        [
            sys.executable,
            str(SCRIPT),
            f"--kernel={kernel}",
            f"--kernelrelease={KR}",
            f"--qemu={_stub(tmp_path, 'exit 0')}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "is not a kernel image" in result.stderr


@pytest.mark.parametrize(("extra", "accel"), [((), "tcg"), (("--accel=kvm",), "kvm")])
def test_the_accelerator_reaches_qemu(tmp_path, extra, accel):
    """`--accel` picks QEMU's accelerator; TCG unless KVM is asked for. No /dev/kvm is used."""
    stub = _stub(tmp_path, f"echo \"argv: $*\"; printf '%s' '{BANNER}{MARKER}'")
    result = _cli(tmp_path, stub, *extra)
    assert result.returncode == 0, result.stderr
    console = (tmp_path / "console.log").read_text(encoding="utf-8")
    assert f" -accel {accel} " in console


def test_an_unknown_accelerator_is_refused(tmp_path):
    result = _cli(tmp_path, _stub(tmp_path, "exit 0"), "--accel=hvf")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr and "hvf" in result.stderr


def _cc_stub(tmp_path: Path, name: str, body: str) -> Path:
    """A compiler stand-in; build_init calls it as `<cc> -static -Os -o <binary> <source>`."""
    cc = tmp_path / name
    cc.write_text(f'#!/bin/sh\nout="$4"\n{body}\n', encoding="utf-8")
    cc.chmod(0o755)
    return cc


def _cli_compiling(tmp_path: Path, *extra: str, env: dict[str, str] | None = None):
    """The CLI without --init-binary, so it compiles the init with the chosen compiler."""
    kernel = tmp_path / "vmlinuz"
    kernel.write_bytes(b"MZ-kernel")
    # The QEMU stub boots only an initramfs that holds the compiled init's bytes.
    qemu = _stub(
        tmp_path,
        'while [ "$1" != -initrd ]; do shift; done\n'
        f"grep -q INIT-FROM-CC \"$2\" && printf '%s' '{BANNER}{MARKER}'",
    )
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            f"--kernel={kernel}",
            f"--kernelrelease={KR}",
            f"--qemu={qemu}",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", **(env or {})},
    )


def test_the_init_is_built_with_the_compiler_cc_names(tmp_path):
    cc = _cc_stub(tmp_path, "planted-cc", 'printf INIT-FROM-CC > "$out"')
    result = _cli_compiling(tmp_path, f"--cc={cc}")
    assert result.returncode == 0, result.stderr


def test_the_cc_environment_variable_chooses_the_compiler(tmp_path):
    cc = _cc_stub(tmp_path, "env-cc", 'printf INIT-FROM-CC > "$out"')
    result = _cli_compiling(tmp_path, env={"CC": str(cc)})
    assert result.returncode == 0, result.stderr


def test_a_missing_compiler_is_refused_by_name(tmp_path):
    result = _cli_compiling(tmp_path, "--cc=/nonexistent/planted-cc")
    assert result.returncode == 2
    assert "/nonexistent/planted-cc is required to build the smoke test's init" in result.stderr


def test_a_compiler_that_fails_is_refused_with_its_message(tmp_path):
    cc = _cc_stub(tmp_path, "broken-cc", "echo 'planted: cannot find -lc' >&2; exit 1")
    result = _cli_compiling(tmp_path, f"--cc={cc}")
    assert result.returncode == 2
    assert "could not build a static init" in result.stderr
    assert "planted: cannot find -lc" in result.stderr


@pytest.mark.parametrize(
    ("arch", "machine_args", "console", "qemu"),
    [
        ("x86_64", [], "ttyS0", "qemu-system-x86_64"),
        ("arm64", ["-M", "virt", "-cpu", "max", "-nic", "none"], "ttyAMA0", "qemu-system-aarch64"),
        (
            "riscv64",
            ["-M", "virt", "-bios", "default", "-nic", "none"],
            "ttyS0",
            "qemu-system-riscv64",
        ),
    ],
)
def test_qemu_command_per_arch(arch, machine_args, console, qemu):
    args = boot_smoke._parser().parse_args(
        [f"--arch={arch}", "--kernel=/k", f"--kernelrelease={KR}"]
    )
    command = boot_smoke.qemu_command(args, Path("/initrd"))
    expected = [
        qemu,
        *machine_args,
        "-accel",
        "tcg",
        "-m",
        "512M",
        "-smp",
        "1",
        "-kernel",
        "/k",
        "-initrd",
        "/initrd",
        "-append",
        f"console={console} panic=-1 rdinit=/init",
        "-nographic",
        "-no-reboot",
    ]
    assert command == expected


def test_qemu_command_console_override():
    args = boot_smoke._parser().parse_args(
        ["--arch=arm64", "--kernel=/k", f"--kernelrelease={KR}", "--console=ttyS9"]
    )
    command = boot_smoke.qemu_command(args, Path("/initrd"))
    append = command[command.index("-append") + 1]
    assert "console=ttyS9" in append


def test_init_source_selection():
    x86_src = boot_smoke.init_source("x86_64")
    arm_src = boot_smoke.init_source("arm64")
    riscv_src = boot_smoke.init_source("riscv64")

    assert "#include <stdio.h>" in x86_src
    assert "main(void)" in x86_src
    assert "NUCLEUS-BOOT-SMOKE release=" in x86_src

    assert "svc #0" in arm_src
    assert "SYS_uname 160" in arm_src
    assert "SYS_write 64" in arm_src
    assert "SYS_sync 81" in arm_src
    assert "SYS_reboot 142" in arm_src
    assert "SYS_exit 93" in arm_src
    assert "_start" in arm_src
    assert "NUCLEUS-BOOT-SMOKE release=" in arm_src

    assert "ecall" in riscv_src
    assert "SYS_uname 160" in riscv_src
    assert "SYS_write 64" in riscv_src
    assert "SYS_sync 81" in riscv_src
    assert "SYS_reboot 142" in riscv_src
    assert "SYS_exit 93" in riscv_src
    assert "_start" in riscv_src
    assert "NUCLEUS-BOOT-SMOKE release=" in riscv_src

    with pytest.raises(boot_smoke.SmokeError, match="unsupported architecture"):
        boot_smoke.init_source("unknown_arch")


@pytest.mark.parametrize("arch", ["arm64", "riscv64"])
def test_freestanding_init_needs_no_libc_copy(arch):
    """A string array built on the stack makes gcc call memcpy, which -nostdlib cannot link.

    Ubuntu's riscv64-linux-gnu-gcc 15.2 did exactly that for the prefix (build-matrix run
    38917396118); only static or file-scope arrays live in .rodata without a copy.
    """
    src = boot_smoke.init_source(arch)
    assert not re.search(r"^\s+(?!static\b)(const\s+)?char\s+\w+\[\]\s*=", src, re.MULTILINE)


@pytest.mark.parametrize("arch", ["x86_64", "arm64", "riscv64"])
def test_init_drains_console_before_power_off(arch):
    """sync() does not wait for the serial line; without a drain, power-off cut the release.

    bleeding/arm64 printed 'NUCLEUS-BOOT-SMOKE release=' and nothing more (run 38917396118).
    """
    src = boot_smoke.init_source(arch)
    drain = (
        src.find("tcdrain(STDOUT_FILENO)")
        if arch == "x86_64"
        else src.find("sys_ioctl(1, TCSBRK, 1)")
    )
    power_off = (
        src.rfind("RB_POWER_OFF")
        if arch == "x86_64"
        else src.rfind("LINUX_REBOOT_CMD_POWER_OFF, 0)")
    )
    assert 0 <= drain < power_off
    if arch != "x86_64":
        assert "#define TCSBRK 0x5409" in src
        assert "#define SYS_ioctl 29" in src


def test_decompress_kernel_x86_passthrough(tmp_path):
    k = tmp_path / "vmlinuz"
    k.write_bytes(b"plain-x86-bzImage")
    assert boot_smoke.decompress_kernel(k, "x86_64", tmp_path) == k


@pytest.mark.parametrize("arch", ["arm64", "riscv64"])
def test_decompress_kernel_non_gzip_refused(tmp_path, arch):
    k = tmp_path / f"vmlinuz-{arch}"
    k.write_bytes(b"not-gzip-data")
    with pytest.raises(boot_smoke.SmokeError, match="not a gzip-compressed Image"):
        boot_smoke.decompress_kernel(k, arch, tmp_path)


@pytest.mark.parametrize("arch", ["arm64", "riscv64"])
def test_decompress_kernel_oversize_refused(tmp_path, arch, monkeypatch):
    import gzip

    k = tmp_path / f"vmlinuz-{arch}.gz"
    k.write_bytes(gzip.compress(b"x" * 1024))
    monkeypatch.setattr(boot_smoke, "MAX_IMAGE_BYTES", 512)
    with pytest.raises(boot_smoke.SmokeError, match="within 512 bytes"):
        boot_smoke.decompress_kernel(k, arch, tmp_path)


@pytest.mark.parametrize("arch", ["arm64", "riscv64"])
def test_decompress_kernel_success(tmp_path, arch):
    import gzip

    k = tmp_path / f"vmlinuz-{arch}.gz"
    payload = b"uncompressed-kernel-Image-bytes"
    k.write_bytes(gzip.compress(payload))
    out = boot_smoke.decompress_kernel(k, arch, tmp_path)
    assert out.is_file()
    assert out.read_bytes() == payload


@pytest.mark.parametrize("arch", ["arm64", "riscv64"])
def test_fake_qemu_missing_init_line_fails_verdict_on_arch(tmp_path, arch):
    import gzip

    kernel = tmp_path / f"vmlinuz-{arch}"
    kernel.write_bytes(gzip.compress(b"fake-image"))
    stub = _stub(tmp_path, f"printf '%s' '{BANNER}'")
    init = tmp_path / "init"
    init.write_bytes(b"\x7fELF")
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            f"--arch={arch}",
            f"--kernel={kernel}",
            f"--kernelrelease={KR}",
            f"--qemu={stub}",
            f"--init-binary={init}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 1
    assert "init never printed" in res.stderr


def test_default_cc_selection(monkeypatch):
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setattr(boot_smoke.platform, "machine", lambda: "x86_64")
    assert boot_smoke.default_cc("x86_64") == "gcc"
    assert boot_smoke.default_cc("arm64") == "aarch64-linux-gnu-gcc"
    assert boot_smoke.default_cc("riscv64") == "riscv64-linux-gnu-gcc"

    monkeypatch.setattr(boot_smoke.platform, "machine", lambda: "aarch64")
    assert boot_smoke.default_cc("arm64") == "gcc"

    monkeypatch.setenv("CC", "my-custom-cc")
    assert boot_smoke.default_cc("x86_64") == "my-custom-cc"
    assert boot_smoke.default_cc("arm64") == "my-custom-cc"
    assert boot_smoke.default_cc("riscv64") == "my-custom-cc"


# The Ubuntu 26.04 package that ships each QEMU binary boot_smoke.py runs, from the package file
# lists at packages.ubuntu.com/resolute (checked 2026-10-16). qemu-system-misc no longer ships
# qemu-system-riscv64; qemu-system-riscv does.
QEMU_BINARY_PACKAGE = {
    "qemu-system-x86_64": "qemu-system-x86",
    "qemu-system-aarch64": "qemu-system-arm",
    "qemu-system-riscv64": "qemu-system-riscv",
}


@pytest.mark.parametrize("arch", ["x86_64", "arm64", "riscv64"])
def test_toolchain_installs_the_package_that_ships_the_arch_qemu(tmp_path, arch):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    log = tmp_path / "apt.log"
    stub = stub_dir / "apt-get"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}", "ARCH": arch}
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "install-build-toolchain.sh"), "--with-qemu"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    installed = log.read_text().split("install", 1)[1].split()
    assert QEMU_BINARY_PACKAGE[boot_smoke.DEFAULT_QEMU[arch]] in installed
