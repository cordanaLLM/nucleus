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
"""Behaviour of scripts/package-uki.sh and scripts/check_uki.py: never publish a UKI that is not one.

A UKI that is not a UKI is checksummed and covered by the release cosign signature, so it
reaches a consumer as a signed, verified artifact (issue #21). These tests pin the refusals:
no kernel, no ukify, a failing ukify, and any output that is empty, not a PE, a PE without
the UKI sections, a PE with sections package-uki.sh does not wire, a kernel for another
machine, or a command line other than the requested one. Refused output is deleted before a
checksum is computed, and a refused run leaves nothing an earlier run wrote at that path.
A dry run writes a marked text file in a directory of its own, never a .efi.

The package-uki.sh tests are hermetic. PATH holds only symlinks to the tools the script
needs, plus a stand-in ukify when a test installs one, so whether the host has ukify
installed cannot change a result. The stand-in writes a PE image built here with struct.

test_real_ukify_builds_a_uki_the_checker_accepts is the exception: it runs the installed
ukify on a real kernel image, and only when NUCLEUS_UKI_TEST_VMLINUZ names one. The
real-ukify job in .github/workflows/ci.yml sets it and fails if the test skips.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "package-uki.sh"
CHECKER = REPO_ROOT / "scripts" / "check_uki.py"
BASH = shutil.which("bash")
VERSIONS = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
STREAM = next(iter(VERSIONS["streams"]))
# The images built below carry the AMD64 machine type, so the script runs for x86_64.
ARCH = "x86_64"
EFI_NAME = "BOOTX64.EFI"
# Every external command package-uki.sh and the ukify stand-in run, except python3.
TOOLS = ("bash", "cat", "dirname", "mkdir", "mktemp", "rm", "sha256sum", "awk")
REAL_KERNEL = os.environ.get("NUCLEUS_UKI_TEST_VMLINUZ", "")

needs_bash = pytest.mark.skipif(BASH is None, reason="bash is required to run package-uki.sh")

# PE machine types from the Microsoft PE format specification, stated here independently of
# the checker so a wrong constant there fails a test.
PE_MACHINE = {"x86_64": 0x8664, "arm64": 0xAA64, "riscv64": 0x5064}
# UEFI removable-media boot file name and ukify --efi-arch per architecture, stated here
# independently of package-uki.sh so a wrong mapping there fails a test.
EFI_TARGET = {
    "x86_64": ("BOOTX64.EFI", "x64"),
    "arm64": ("BOOTAA64.EFI", "aa64"),
    "riscv64": ("BOOTRISCV64.EFI", "riscv64"),
}
# Sections of the UAPI Group UKI specification that package-uki.sh does not pass to ukify.
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
E_LFANEW = 0x40
COFF_OFFSET = E_LFANEW + 4
OPTIONAL_OFFSET = COFF_OFFSET + 20
OPTIONAL_HEADER_SIZE = 240  # PE32+ with 16 data directories
SECTION_TABLE_OFFSET = OPTIONAL_OFFSET + OPTIONAL_HEADER_SIZE
FILE_ALIGNMENT = 0x200

# Stand-ins for what ukify embeds. The payloads are test data, not a bootable kernel.
UKI_SECTIONS = {
    ".text": b"\xcc" * 32,
    ".sdmagic": b"#### LoaderInfo: systemd-stub 000-test ####",
    ".osrel": b'ID=lusoris\nNAME="Lusoris Linux"\n',
    ".cmdline": b"console=ttyS0",
    ".uname": b"0.0.0-test",
    ".sbat": b"sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md\n",
    ".linux": b"\x00" * 64,
}
# package-uki.sh runs are given this command line, so the stand-in images match it.
UKI_CMDLINE = UKI_SECTIONS[".cmdline"].decode("ascii")
# What ukify embeds when --cmdline lacks the "@": the path of the file, as text.
CMDLINE_PATH_AS_TEXT = b"/staging/lusoris-uki/cmdline"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_uki", CHECKER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK_UKI = _load_checker()


def _align(value: int) -> int:
    return (value + FILE_ALIGNMENT - 1) // FILE_ALIGNMENT * FILE_ALIGNMENT


def build_pe(sections, *, machine: int = 0x8664, subsystem: int = 10, magic: int = 0x20B) -> bytes:
    """A minimal PE image: DOS header, PE signature, COFF and optional headers, sections."""
    pairs = list(sections.items()) if isinstance(sections, dict) else list(sections)
    data_offset = _align(SECTION_TABLE_OFFSET + 40 * len(pairs))
    dos = bytearray(E_LFANEW)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, E_LFANEW)
    coff = struct.pack("<HHIIIHH", machine, len(pairs), 0, 0, 0, OPTIONAL_HEADER_SIZE, 0x22)
    optional = bytearray(OPTIONAL_HEADER_SIZE)
    struct.pack_into("<H", optional, 0, magic)
    struct.pack_into("<H", optional, 68, subsystem)
    table, body = bytearray(), bytearray()
    for index, (name, payload) in enumerate(pairs):
        raw_size = _align(len(payload))
        table += struct.pack(
            "<8sIIIIIIHHI",
            name.encode("ascii"),
            len(payload),
            0x1000 * (index + 1),
            raw_size,
            data_offset + len(body),
            0,
            0,
            0,
            0,
            0x40000040,
        )
        body += payload.ljust(raw_size, b"\0")
    headers = bytes(dos) + b"PE\0\0" + coff + bytes(optional) + bytes(table)
    return headers.ljust(data_offset, b"\0") + bytes(body)


def _without(name: str) -> dict[str, bytes]:
    return {key: value for key, value in UKI_SECTIONS.items() if key != name}


def _with(name: str, payload: bytes) -> bytes:
    """The stand-in UKI with one section replaced or added."""
    return build_pe({**UKI_SECTIONS, name: payload})


def _kernel_pe(machine: int) -> bytes:
    """A .linux payload with an EFI stub header, as a real kernel image has, for machine."""
    return build_pe({".text": b"\xcc" * 16}, machine=machine)


def _patch(image: bytes, offset: int, fmt: str, value) -> bytes:
    patched = bytearray(image)
    struct.pack_into(fmt, patched, offset, value)
    return bytes(patched)


VALID_UKI = build_pe(UKI_SECTIONS)


# --- scripts/check_uki.py ------------------------------------------------------------------


def test_checker_accepts_a_minimal_uki():
    sections = CHECK_UKI.check_uki(VALID_UKI, ARCH)
    assert [section.name for section in sections] == list(UKI_SECTIONS)
    cmdline = next(section for section in sections if section.name == ".cmdline")
    assert CHECK_UKI.section_data(VALID_UKI, cmdline) == UKI_SECTIONS[".cmdline"]


def test_checker_accepts_an_initrd_only_when_one_was_passed():
    image = _with(".initrd", b"initrd")
    assert CHECK_UKI.check_uki(image, ARCH, expect_initrd=True)
    with pytest.raises(CHECK_UKI.NotAUki, match="no initrd was passed"):
        CHECK_UKI.check_uki(image, ARCH, expect_initrd=False)
    with pytest.raises(CHECK_UKI.NotAUki, match="missing section .initrd"):
        CHECK_UKI.check_uki(VALID_UKI, ARCH, expect_initrd=True)


@pytest.mark.parametrize("arch", VERSIONS["architectures"])
def test_checker_knows_the_pe_machine_of_every_architecture_in_versions_json(arch):
    assert arch in PE_MACHINE, (
        f"add the PE machine type for {arch} to this test and to check_uki.py"
    )
    image = build_pe({**UKI_SECTIONS, ".linux": _kernel_pe(PE_MACHINE[arch])}, machine=PE_MACHINE[arch])
    assert CHECK_UKI.check_uki(image, arch)


def test_checker_accepts_a_kernel_built_for_the_target_machine():
    assert CHECK_UKI.check_uki(_with(".linux", _kernel_pe(PE_MACHINE[ARCH])), ARCH)


def test_checker_compares_the_command_line_when_asked():
    requested = UKI_SECTIONS[".cmdline"]
    assert CHECK_UKI.check_uki(VALID_UKI, ARCH, expect_cmdline=requested)
    with pytest.raises(CHECK_UKI.NotAUki, match="not the requested command line"):
        CHECK_UKI.check_uki(VALID_UKI, ARCH, expect_cmdline=requested + b" quiet")
    with pytest.raises(CHECK_UKI.NotAUki, match="not the requested command line"):
        CHECK_UKI.check_uki(_with(".cmdline", CMDLINE_PATH_AS_TEXT), ARCH, expect_cmdline=requested)


@pytest.mark.parametrize("name", [".linux", ".osrel", ".cmdline", ".uname", ".sbat", ".sdmagic"])
def test_checker_refuses_a_pe_missing_a_uki_section(name):
    with pytest.raises(CHECK_UKI.NotAUki, match=f"missing section {name}"):
        CHECK_UKI.check_uki(build_pe(_without(name)), ARCH)


@pytest.mark.parametrize("name", UNWIRED_SECTIONS)
def test_checker_refuses_a_section_package_uki_does_not_wire(name):
    with pytest.raises(CHECK_UKI.NotAUki, match=re.escape(f"carries a {name} section")):
        CHECK_UKI.check_uki(_with(name, b"payload"), ARCH)


@pytest.mark.parametrize(
    ("image", "reason"),
    [
        pytest.param(b"", "the file is empty", id="zero-length"),
        pytest.param(b"MZ-uki", "no MZ DOS header", id="six-byte-non-PE"),
        pytest.param(b"\x7fELF" + bytes(0x100), "no MZ DOS header", id="ELF"),
        pytest.param(
            _patch(VALID_UKI, 0x3C, "<I", len(VALID_UKI)),
            "runs past the end",
            id="e_lfanew-past-EOF",
        ),
        pytest.param(
            _patch(VALID_UKI, 0x3C, "<I", 0x10), "into the DOS header", id="e_lfanew-in-DOS-header"
        ),
        pytest.param(
            _patch(VALID_UKI, E_LFANEW, "<4s", b"NE\0\0"), "no PE signature", id="no-PE-signature"
        ),
        pytest.param(build_pe(UKI_SECTIONS, machine=0xAA64), "is not x86_64", id="wrong-machine"),
        pytest.param(_patch(VALID_UKI, COFF_OFFSET + 2, "<H", 0), "0 sections", id="no-sections"),
        pytest.param(
            _patch(VALID_UKI, COFF_OFFSET + 2, "<H", 97), "97 sections", id="too-many-sections"
        ),
        pytest.param(
            _patch(VALID_UKI, COFF_OFFSET + 16, "<H", 16),
            "too small",
            id="optional-header-too-small",
        ),
        pytest.param(
            build_pe(UKI_SECTIONS, magic=0x107), "neither PE32 nor", id="bad-optional-magic"
        ),
        pytest.param(
            build_pe(UKI_SECTIONS, subsystem=3), "not an EFI application", id="not-an-EFI-app"
        ),
        # Empty sections have no raw data, so only the fourth header can leave the file.
        pytest.param(
            build_pe({".a": b"", ".b": b"", ".c": b"", ".d": b""})[: SECTION_TABLE_OFFSET + 40 * 3],
            "section header 3",
            id="truncated-section-table",
        ),
        pytest.param(VALID_UKI[:-0x100], "section .linux data", id="section-data-past-EOF"),
        pytest.param(_with(".cmdline", b""), "section .cmdline is empty", id="empty-cmdline"),
        pytest.param(
            build_pe([*UKI_SECTIONS.items(), (".linux", b"\x01" * 8)]),
            "appears 2 times",
            id="duplicate-linux",
        ),
        pytest.param(_with(".sdmagic", b"not a stub"), "systemd-stub marker", id="foreign-sdmagic"),
        # An x64 stub around an arm64 kernel: the image's own machine type is the stub's.
        pytest.param(
            _with(".linux", _kernel_pe(PE_MACHINE["arm64"])),
            "the .linux kernel: machine type 0xaa64 is not x86_64",
            id="kernel-for-another-machine",
        ),
        pytest.param(
            _with(".linux", b"MZ" + bytes(0x100)),
            "the .linux kernel: e_lfanew 0x0 points into the DOS header",
            id="kernel-MZ-without-PE",
        ),
    ],
)
def test_checker_refuses_what_is_not_a_uki(image, reason):
    with pytest.raises(CHECK_UKI.NotAUki, match=reason):
        CHECK_UKI.check_uki(image, ARCH)


def test_checker_command_line_exit_status(tmp_path):
    valid, bogus = tmp_path / "valid.efi", tmp_path / "bogus.efi"
    valid.write_bytes(VALID_UKI)
    bogus.write_bytes(b"MZ-uki")
    cmdline = tmp_path / "cmdline"
    cmdline.write_bytes(UKI_SECTIONS[".cmdline"])

    def check(*args: str) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(CHECKER), f"--arch={ARCH}", *args]
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    accepted = check(str(valid))
    assert accepted.returncode == 0, accepted.stderr
    assert "check_uki: OK" in accepted.stdout
    refused = check(str(bogus))
    assert refused.returncode == 1
    assert "check_uki: REFUSED" in refused.stderr
    assert check(str(tmp_path / "absent.efi")).returncode == 1
    assert check(f"--expect-cmdline=@{cmdline}", str(valid)).returncode == 0
    assert check(f"--expect-cmdline={UKI_CMDLINE}", str(valid)).returncode == 0
    assert check("--expect-cmdline=console=tty0", str(valid)).returncode == 1
    assert check(f"--expect-cmdline=@{tmp_path / 'absent'}", str(valid)).returncode == 2
    usage = [sys.executable, str(CHECKER), "--arch=not-an-arch", str(valid)]
    assert subprocess.run(usage, capture_output=True, check=False).returncode == 2


# --- scripts/package-uki.sh ----------------------------------------------------------------


def _isolated_bin(tmp_path: Path) -> Path:
    """A PATH directory holding only the tools package-uki.sh needs, and no ukify."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    links = {tool: shutil.which(tool) for tool in TOOLS}
    links["python3"] = sys.executable
    for tool, target in links.items():
        if target is None:
            pytest.skip(f"{tool} is required to run package-uki.sh")
        try:
            (bin_dir / tool).symlink_to(target)
        except OSError as err:
            pytest.skip(f"cannot build an isolated PATH here: {err}")
    return bin_dir


def _install_ukify_stub(bin_dir: Path, output: bytes | None, exit_code: int = 0) -> Path:
    """A stand-in ukify: logs its arguments, copies the @cmdline file, writes `output`."""
    work = bin_dir.parent
    log, cmdline_copy, payload = (
        work / "ukify-args.txt",
        work / "ukify-cmdline.txt",
        work / "ukify-output",
    )
    cases = [f'    --cmdline=@*) cat "${{arg#--cmdline=@}}" > {shlex.quote(str(cmdline_copy))} ;;']
    if output is not None:
        payload.write_bytes(output)
        cases.append(f'    --output=*) cat {shlex.quote(str(payload))} > "${{arg#--output=}}" ;;')
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(log))}",
        'for arg in "$@"; do',
        '  case "${arg}" in',
        *cases,
        "  esac",
        "done",
        f"exit {exit_code}",
    ]
    stub = bin_dir / "ukify"
    stub.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    return log


def _fake_kernel(tmp_path: Path) -> Path:
    """Stands in for vmlinuz; only the stand-in ukify ever reads the path."""
    kernel = tmp_path / "vmlinuz"
    kernel.write_bytes(b"kernel")
    return kernel


def _run(
    tmp_path: Path,
    bin_dir: Path,
    *args: str,
    arch: str = ARCH,
    cmdline: str = UKI_CMDLINE,
    tmp_name: str = "tmp",
) -> subprocess.CompletedProcess:
    (tmp_path / tmp_name).mkdir(exist_ok=True)
    env = {"PATH": str(bin_dir), "TMPDIR": str(tmp_path / tmp_name), "LC_ALL": "C"}
    cmd = [
        BASH,
        str(SCRIPT),
        f"--stream={STREAM}",
        f"--arch={arch}",
        f"--output-dir={tmp_path / 'out'}",
        f"--cmdline={cmdline}",
        *args,
    ]
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False)


def _target(tmp_path: Path, arch: str = ARCH) -> Path:
    return tmp_path / "out" / f"{STREAM}-{arch}"


def _assert_nothing_published(tmp_path: Path) -> None:
    assert not (_target(tmp_path) / EFI_NAME).exists(), "a refused image must be deleted"
    assert not (_target(tmp_path) / f"{EFI_NAME}.sha256").exists(), (
        "a refused image must never be checksummed"
    )


@needs_bash
def test_dry_run_writes_a_marked_simulation_and_nothing_named_like_a_uki(tmp_path):
    result = _run(tmp_path, _isolated_bin(tmp_path), "--dry-run")
    assert result.returncode == 0, result.stderr
    dry_run = tmp_path / "out" / f"{STREAM}-{ARCH}-dry-run"
    simulated = dry_run / f"{EFI_NAME}.simulated.txt"
    text = simulated.read_text(encoding="utf-8")
    assert text.startswith("SIMULATED, not a Unified Kernel Image"), text
    measurement = json.loads((dry_run / "pcr11-measurements.json").read_text(encoding="utf-8"))
    assert measurement["simulated"] is True, "a simulated measurement must be marked as one"
    assert measurement["binary"] == simulated.name
    written = [path.name for path in (tmp_path / "out").rglob("*")]
    assert not [name for name in written if name.lower().endswith((".efi", ".sha256"))], written
    assert not _target(tmp_path).exists(), "a dry run must not write where a real UKI goes"


@needs_bash
def test_production_without_a_kernel_refuses_and_writes_nothing(tmp_path):
    result = _run(tmp_path, _isolated_bin(tmp_path))
    assert result.returncode == 1, result.stderr
    assert "no kernel image to package" in result.stderr
    _assert_nothing_published(tmp_path)
    assert not (_target(tmp_path) / "pcr11-measurements.json").exists(), (
        "no fall-through to simulation"
    )


@needs_bash
def test_a_refused_run_after_a_dry_run_leaves_no_uki(tmp_path):
    bin_dir = _isolated_bin(tmp_path)
    assert _run(tmp_path, bin_dir, "--dry-run").returncode == 0
    result = _run(tmp_path, bin_dir)
    assert result.returncode == 1, result.stderr
    _assert_nothing_published(tmp_path)


@needs_bash
@pytest.mark.parametrize(
    ("output", "exit_code", "kernel", "reason"),
    [
        pytest.param(VALID_UKI, 0, "absent", "no kernel image to package", id="no-kernel"),
        pytest.param(None, 1, "vmlinuz", "ukify build failed", id="ukify-fails"),
        pytest.param(
            build_pe(_without(".linux")), 0, "vmlinuz", "missing section .linux", id="refused"
        ),
    ],
)
def test_a_refused_run_removes_what_an_earlier_run_left(tmp_path, output, exit_code, kernel, reason):
    bin_dir = _isolated_bin(tmp_path)
    _install_ukify_stub(bin_dir, VALID_UKI)
    assert _run(tmp_path, bin_dir, f"--vmlinuz={_fake_kernel(tmp_path)}").returncode == 0
    assert (_target(tmp_path) / f"{EFI_NAME}.sha256").is_file()
    (_target(tmp_path) / "pcr11-measurements.json").write_text("{}", encoding="utf-8")
    _install_ukify_stub(bin_dir, output, exit_code)
    result = _run(tmp_path, bin_dir, f"--vmlinuz={tmp_path / kernel}")
    assert result.returncode == 1, result.stderr
    assert reason in result.stderr
    _assert_nothing_published(tmp_path)
    assert not (_target(tmp_path) / "pcr11-measurements.json").exists(), "nor its measurement"


@needs_bash
def test_production_without_ukify_refuses_and_writes_nothing(tmp_path):
    result = _run(tmp_path, _isolated_bin(tmp_path), f"--vmlinuz={_fake_kernel(tmp_path)}")
    assert result.returncode == 1, result.stderr
    assert "ukify is not installed" in result.stderr
    _assert_nothing_published(tmp_path)


@needs_bash
def test_a_quote_in_tmpdir_does_not_break_the_cleanup(tmp_path):
    bin_dir = _isolated_bin(tmp_path)
    simulated = _run(tmp_path, bin_dir, "--dry-run", tmp_name="it's tmp")
    assert simulated.returncode == 0, simulated.stderr
    refused = _run(tmp_path, bin_dir, tmp_name="it's tmp")
    assert refused.returncode == 1, refused.stderr
    assert not list((tmp_path / "it's tmp").iterdir()), "the staging directory must be removed"


@needs_bash
def test_an_initrd_that_is_not_a_file_is_refused(tmp_path):
    bin_dir = _isolated_bin(tmp_path)
    _install_ukify_stub(bin_dir, VALID_UKI)
    result = _run(
        tmp_path, bin_dir, f"--vmlinuz={_fake_kernel(tmp_path)}", f"--initrd={tmp_path / 'absent'}"
    )
    assert result.returncode == 1, result.stderr
    assert "is not a file" in result.stderr
    _assert_nothing_published(tmp_path)


@needs_bash
def test_ukify_reads_the_cmdline_file_and_gets_no_empty_initrd(tmp_path):
    bin_dir = _isolated_bin(tmp_path)
    log = _install_ukify_stub(bin_dir, _with(".cmdline", b"console=ttyS0 quiet"))
    kernel = _fake_kernel(tmp_path)
    result = _run(tmp_path, bin_dir, f"--vmlinuz={kernel}", cmdline="console=ttyS0 quiet")
    assert result.returncode == 0, result.stderr
    args = log.read_text(encoding="utf-8").splitlines()
    assert f"--linux={kernel}" in args
    cmdline = [arg for arg in args if arg.startswith("--cmdline=")]
    assert len(cmdline) == 1 and cmdline[0].startswith("--cmdline=@"), (
        f"ukify embeds a bare path as text: {args}"
    )
    assert (tmp_path / "ukify-cmdline.txt").read_text(encoding="utf-8") == "console=ttyS0 quiet"
    assert not [arg for arg in args if arg.startswith("--initrd")], f"no initrd was given: {args}"


@needs_bash
@pytest.mark.parametrize("arch", VERSIONS["architectures"])
def test_ukify_gets_the_stub_for_the_target_arch_and_no_host_config(tmp_path, arch):
    assert arch in EFI_TARGET, f"add the boot file name and EFI architecture for {arch}"
    efi_name, efi_arch = EFI_TARGET[arch]
    bin_dir = _isolated_bin(tmp_path)
    log = _install_ukify_stub(bin_dir, build_pe(UKI_SECTIONS, machine=PE_MACHINE[arch]))
    result = _run(tmp_path, bin_dir, f"--vmlinuz={_fake_kernel(tmp_path)}", arch=arch)
    assert result.returncode == 0, result.stderr
    args = log.read_text(encoding="utf-8").splitlines()
    assert f"--efi-arch={efi_arch}" in args, f"ukify would pick the build host's stub: {args}"
    assert "--config=/dev/null" in args, f"a host ukify.conf could add sections or sign: {args}"
    assert (_target(tmp_path, arch) / f"{efi_name}.sha256").is_file()


@needs_bash
def test_an_initrd_is_passed_to_ukify_and_required_in_the_image(tmp_path):
    bin_dir = _isolated_bin(tmp_path)
    initrd = tmp_path / "initrd.img"
    initrd.write_bytes(b"initrd")
    log = _install_ukify_stub(bin_dir, _with(".initrd", b"initrd"))
    result = _run(tmp_path, bin_dir, f"--vmlinuz={_fake_kernel(tmp_path)}", f"--initrd={initrd}")
    assert result.returncode == 0, result.stderr
    assert f"--initrd={initrd}" in log.read_text(encoding="utf-8").splitlines()
    assert (_target(tmp_path) / f"{EFI_NAME}.sha256").is_file()


@needs_bash
def test_an_initrd_missing_from_the_image_is_refused(tmp_path):
    bin_dir = _isolated_bin(tmp_path)
    initrd = tmp_path / "initrd.img"
    initrd.write_bytes(b"initrd")
    _install_ukify_stub(bin_dir, VALID_UKI)
    result = _run(tmp_path, bin_dir, f"--vmlinuz={_fake_kernel(tmp_path)}", f"--initrd={initrd}")
    assert result.returncode == 1, result.stderr
    assert "missing section .initrd" in result.stderr
    _assert_nothing_published(tmp_path)


@needs_bash
@pytest.mark.parametrize(
    ("output", "exit_code", "reason"),
    [
        # Writes partial output, then fails: the case errexit used to miss inside `fn || status=$?`.
        pytest.param(b"partial", 1, "ukify build failed", id="ukify-fails-after-a-partial-write"),
        pytest.param(None, 0, "No such file or directory", id="ukify-succeeds-without-output"),
        pytest.param(b"", 0, "the file is empty", id="zero-length"),
        pytest.param(b"MZ-uki", 0, "no MZ DOS header", id="six-byte-non-PE"),
        pytest.param(
            build_pe(_without(".linux")), 0, "missing section .linux", id="PE-without-linux"
        ),
        pytest.param(
            _with(".initrd", b"x"), 0, "no initrd was passed", id="unrequested-initrd"
        ),
        pytest.param(
            _with(".cmdline", CMDLINE_PATH_AS_TEXT),
            0,
            "not the requested command line",
            id="cmdline-is-a-file-path",
        ),
        pytest.param(
            _with(".ucode", b"microcode"), 0, "carries a .ucode section", id="host-config-microcode"
        ),
        pytest.param(
            _with(".linux", _kernel_pe(PE_MACHINE["arm64"])),
            0,
            "the .linux kernel",
            id="kernel-for-another-machine",
        ),
    ],
)
def test_bad_ukify_output_is_deleted_and_never_checksummed(tmp_path, output, exit_code, reason):
    bin_dir = _isolated_bin(tmp_path)
    _install_ukify_stub(bin_dir, output, exit_code)
    result = _run(tmp_path, bin_dir, f"--vmlinuz={_fake_kernel(tmp_path)}")
    assert result.returncode == 1, result.stderr
    assert reason in result.stderr
    _assert_nothing_published(tmp_path)


@needs_bash
def test_a_valid_uki_is_checked_then_checksummed(tmp_path):
    bin_dir = _isolated_bin(tmp_path)
    _install_ukify_stub(bin_dir, VALID_UKI)
    result = _run(tmp_path, bin_dir, f"--vmlinuz={_fake_kernel(tmp_path)}")
    assert result.returncode == 0, result.stderr
    assert "check_uki: OK" in result.stdout
    target = _target(tmp_path)
    assert (target / EFI_NAME).read_bytes() == VALID_UKI
    checksum = (target / f"{EFI_NAME}.sha256").read_text(encoding="utf-8").split()
    assert checksum == [hashlib.sha256(VALID_UKI).hexdigest(), EFI_NAME]


# --- real ukify, opt-in --------------------------------------------------------------------


@needs_bash
@pytest.mark.skipif(
    shutil.which("ukify") is None or not REAL_KERNEL,
    reason="needs ukify and NUCLEUS_UKI_TEST_VMLINUZ naming a kernel image; the ci real-ukify job sets both",
)
@pytest.mark.parametrize("with_initrd", [False, True], ids=["no-initrd", "initrd"])
def test_real_ukify_builds_a_uki_the_checker_accepts(tmp_path, with_initrd):
    cmdline = "console=ttyS0 quiet"
    args = [f"--vmlinuz={REAL_KERNEL}", f"--cmdline={cmdline}"]
    initrd = b"test initrd: ukify embeds these bytes without parsing them"
    if with_initrd:
        (tmp_path / "initrd.img").write_bytes(initrd)
        args.append(f"--initrd={tmp_path / 'initrd.img'}")
    env = {**os.environ, "TMPDIR": str(tmp_path)}
    cmd = [
        BASH,
        str(SCRIPT),
        f"--stream={STREAM}",
        f"--arch={ARCH}",
        f"--output-dir={tmp_path / 'out'}",
        *args,
    ]
    result = subprocess.run(
        cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    image = (_target(tmp_path) / EFI_NAME).read_bytes()
    sections = CHECK_UKI.check_uki(
        image, ARCH, expect_initrd=with_initrd, expect_cmdline=cmdline.encode()
    )
    content = {section.name: CHECK_UKI.section_data(image, section) for section in sections}
    assert content[".cmdline"] == cmdline.encode(), (
        "the cmdline text, not the path of the file holding it"
    )
    assert b"ID=lusoris" in content[".osrel"]
    assert b"lusoris,1,Lusoris Linux" in content[".sbat"]
    assert content[".uname"].strip()
    assert content[".linux"] == Path(REAL_KERNEL).read_bytes()
    assert (initrd in content[".initrd"]) if with_initrd else ".initrd" not in content
    assert not set(content) & set(UNWIRED_SECTIONS), f"unwired sections: {sorted(content)}"
    checksum = (_target(tmp_path) / f"{EFI_NAME}.sha256").read_text(encoding="utf-8").split()
    assert checksum == [hashlib.sha256(image).hexdigest(), EFI_NAME]
