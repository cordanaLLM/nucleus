"""The artifact gate opens every package and refuses anything that is not what it claims.

Packages are built here with ``dpkg-deb --build``; the tests that need one are skipped where
dpkg-deb is not installed (the build containers and the Ubuntu CI runners have it).
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_kernel_artifacts as gate  # noqa: E402

STREAM, ARCH, VERSION, KV = "mainstream", "x86_64", "7.2.8", "7.2.8"
KR = f"{KV}-lusoris1-{STREAM}"
PKGVER = f"{VERSION}-lusoris1"
CONFIG = b"CONFIG_X86_64=y\nCONFIG_DEBUG_INFO_BTF=y\n"
# The review's fake kernel: non-empty bytes, identical in the package and beside it, no kernel.
FAKE_KERNEL = b"MZ\x00fake-bzImage" * 64


def bzimage(version: str) -> bytes:
    """The smallest x86 bzImage layout the gate reads: MZ, HdrS at 0x202, the version pointer."""
    data = bytearray(b"MZ" + b"\0" * 0x3FE)
    data[0x202:0x206] = b"HdrS"
    data[0x20E:0x210] = (0x300 - 0x200).to_bytes(2, "little")
    text = f"{version} (nucleus@forge) #lusoris1 SMP PREEMPT_DYNAMIC Fri Sep 25 14:37:14 UTC 2026".encode()
    data[0x300 : 0x300 + len(text) + 1] = text + b"\0"
    return bytes(data)


def arm_image(banner_release: str, magic: bytes = b"ARM\x64") -> bytes:
    """A gzip-compressed Image: the header magic at 0x38 and the kernel's version banner inside."""
    image = bytearray(0x40)
    image[0x38 : 0x38 + len(magic)] = magic
    image += f"\0Linux version {banner_release} (nucleus@forge) (gcc) #lusoris1 SMP\n\0".encode()
    return gzip.compress(bytes(image), mtime=0)


KERNEL = bzimage(KR)

needs_dpkg = pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not installed")


def _exp(kernelrelease: str = KR, revision: int = 1, stream: str = STREAM, arch: str = ARCH,
         cross: bool = False) -> gate.Expectation:
    args = argparse.Namespace(stream=stream, arch=arch, revision=revision, kernelrelease=kernelrelease,
                              cross=cross)
    return gate.expectation(REPO_ROOT / "versions.json", args)


def _deb(out: Path, package: str, version: str = PKGVER, arch: str = "amd64",
         files: dict[str, bytes] | None = None, name: str | None = None) -> Path:
    root = out.parent / f"pkgroot-{package}-{len(list(out.parent.iterdir()))}"
    (root / "DEBIAN").mkdir(parents=True)
    control = (f"Package: {package}\nVersion: {version}\nArchitecture: {arch}\n"
               "Maintainer: test <test@example.com>\nDescription: test package\n")
    (root / "DEBIAN" / "control").write_text(control, encoding="utf-8")
    for member, data in (files or {}).items():
        target = root / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    deb = out / (name or f"{package}_{version}_{arch}.deb")
    subprocess.run(["dpkg-deb", "-Zgzip", "--root-owner-group", "--build", str(root), str(deb)],
                   check=True, capture_output=True)
    return deb


def _image_files(config: bytes = CONFIG, kernel: bytes = KERNEL) -> dict[str, bytes]:
    return {f"boot/vmlinuz-{KR}": kernel, f"boot/config-{KR}": config}


def _release(tmp_path: Path, kernel: bytes = KERNEL, config: bytes = CONFIG) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    _deb(out, f"linux-image-{KR}", files=_image_files(config=config, kernel=kernel))
    _deb(out, f"linux-headers-{KR}")
    _deb(out, "linux-libc-dev")
    (out / f"vmlinuz-{KR}").write_bytes(kernel)
    (out / f"kernel-{STREAM}-{ARCH}.config").write_bytes(config)
    return out


def _cli(out: Path, kernelrelease: str = KR, stream: str = STREAM, *extra: str, env: dict | None = None):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_kernel_artifacts.py"), f"--dir={out}",
         f"--stream={stream}", f"--arch={ARCH}", f"--kernelrelease={kernelrelease}", "--revision=1",
         f"--versions={REPO_ROOT / 'versions.json'}", *extra],
        capture_output=True, text=True, check=False, env=env,
    )


# --- without packages ---------------------------------------------------------------------------


def test_expectation_comes_from_versions_json():
    exp = _exp()
    assert (exp.kernelversion, exp.package_version, exp.debian_arch) == (KV, PKGVER, "amd64")
    assert exp.packages == (f"linux-image-{KR}", f"linux-headers-{KR}", "linux-libc-dev")
    assert exp.required == exp.packages, "a native build must make all three packages"
    assert _exp(cross=True).required == (f"linux-image-{KR}", "linux-libc-dev")
    assert _exp(revision=4).package_version == f"{VERSION}-lusoris4"


def test_a_release_candidate_package_sorts_before_its_release_and_names_no_tilde():
    """dpkg orders 7.3~rc5 before 7.3 and 7.3-rc5 after it; GitHub and imago refuse a tilde."""
    exp = _exp("7.3.0-rc5-lusoris1-bleeding", stream="bleeding")
    assert exp.package_version == "7.3~rc5-lusoris1"
    name = gate.package_file_name("linux-libc-dev", exp.package_version, exp.debian_arch)
    assert name == "linux-libc-dev_7.3.rc5-lusoris1_amd64.deb"
    assert gate.package_file_name("linux-libc-dev", PKGVER, "amd64") == f"linux-libc-dev_{PKGVER}_amd64.deb"


# --- the kernel image ---------------------------------------------------------------------------


def test_the_reviews_fake_kernel_is_not_a_kernel():
    assert gate.check_kernel_image(FAKE_KERNEL, _exp()) == [
        f"vmlinuz-{KR}: not a bzImage (no setup header with a kernel version string)"
    ]


@pytest.mark.parametrize(
    ("data", "finding"),
    [
        (KERNEL, None),
        (bzimage("7.2.8-lusoris1-realtime"), "the bzImage reports '7.2.8-lusoris1-realtime', not"),
        (bzimage(KR + "+"), f"the bzImage reports '{KR}+'"),
        (KERNEL[:0x300], "not a bzImage"),
        (KERNEL.replace(b"HdrS", b"HdrX"), "not a bzImage"),
        (arm_image(KR), "not a bzImage"),
    ],
)
def test_an_x86_image_must_be_a_bzimage_of_this_release(data, finding):
    problems = gate.check_kernel_image(data, _exp())
    if finding is None:
        assert problems == []
    else:
        assert len(problems) == 1 and finding in problems[0], problems


@pytest.mark.parametrize(("arch", "magic"), [("arm64", b"ARM\x64"), ("riscv64", b"RSC\x05")])
def test_an_arm64_or_riscv_image_must_be_a_compressed_image_of_this_release(arch, magic):
    exp = _exp(arch=arch, cross=True)
    assert gate.check_kernel_image(arm_image(KR, magic), exp) == []
    cases = [
        (bzimage(KR), "not a complete gzip-compressed Image"),
        (arm_image(KR, magic)[:-12], "not a complete gzip-compressed Image"),
        (gzip.compress(b"\0" * 0x40 + f"Linux version {KR} ".encode()), f"no {exp.kernel_arch} Image header"),
        (arm_image(KR, b"MZ\0\0"), f"no {exp.kernel_arch} Image header"),
        (arm_image("7.2.8-lusoris1-lts", magic), f"no 'Linux version {KR} ' banner"),
        (arm_image(KR + "x", magic), f"no 'Linux version {KR} ' banner"),
    ]
    for data, finding in cases:
        problems = gate.check_kernel_image(data, exp)
        assert len(problems) == 1 and finding in problems[0], (finding, problems)


def test_a_decompression_past_the_bound_is_refused(monkeypatch):
    monkeypatch.setattr(gate, "MAX_IMAGE_BYTES", 32)
    problems = gate.check_kernel_image(arm_image(KR), _exp(arch="arm64"))
    assert problems and "not a complete gzip-compressed Image within 32 bytes" in problems[0]


def test_an_architecture_without_a_known_image_check_is_refused():
    exp = gate.Expectation(stream=STREAM, arch="s390x", kernel_arch="s390", kernelversion=KV,
                           package_version=PKGVER, debian_arch="s390x",
                           localversion="-lusoris1-mainstream", kernelrelease=KR)
    assert gate.check_kernel_image(KERNEL, exp) == [
        f"vmlinuz-{KR}: no image check is known for kernel architecture s390"
    ]


@pytest.mark.parametrize(
    ("kernelrelease", "finding"),
    [
        (KR, None),
        ("7.2.80-lusoris1-mainstream", "does not start with the mainstream kernel version 7.2.8"),
        ("6.18.54-lusoris1-mainstream", "does not start with"),
        ("7.2.8-lusoris1-realtime", "does not end with the build's -lusoris1-mainstream"),
        ("7.2.8", "does not end with"),
        ("not a release", "is not a kernel release string"),
    ],
)
def test_kernelrelease_must_be_the_streams_version_with_the_builds_suffix(kernelrelease, finding):
    problems = gate.check_release(_exp(kernelrelease))
    if finding is None:
        assert problems == []
    else:
        assert any(finding in problem for problem in problems), problems


def test_listing_refuses_zero_bytes_strangers_and_missing_files(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "linux-image.deb").write_bytes(b"")
    (out / "README").write_text("hello", encoding="utf-8")
    (out / "sub").mkdir()
    problems, debs = gate.check_listing(out, _exp())
    text = "\n".join(problems)
    assert "linux-image.deb: zero bytes" in text
    assert "README: not an artifact of this build" in text
    assert "sub: not a regular file" in text
    assert f"vmlinuz-{KR}: missing" in text and f"kernel-{STREAM}-{ARCH}.config: missing" in text
    assert "no Debian package" in text and debs == []


def test_an_unknown_stream_cannot_be_evaluated(tmp_path):
    if shutil.which("dpkg-deb") is None:
        pytest.skip("dpkg-deb not installed")
    result = _cli(tmp_path, stream="nightly")
    assert result.returncode == 2
    assert "unknown stream" in result.stderr or "nightly" in result.stderr


# --- with packages ------------------------------------------------------------------------------


@needs_dpkg
def test_a_real_build_layout_passes(tmp_path):
    result = _cli(_release(tmp_path))
    assert result.returncode == 0, result.stderr
    assert f"holds {KR} for {STREAM}/{ARCH}" in result.stdout


@needs_dpkg
def test_a_cross_build_without_headers_passes(tmp_path):
    out = _release(tmp_path)
    next(out.glob("linux-headers-*.deb")).unlink()
    result = _cli(out, KR, STREAM, "--cross")
    assert result.returncode == 0, result.stderr


@needs_dpkg
def test_a_native_build_without_headers_is_refused(tmp_path):
    out = _release(tmp_path)
    next(out.glob("linux-headers-*.deb")).unlink()
    result = _cli(out)
    assert result.returncode == 1
    assert f"no linux-headers-{KR} package" in result.stderr


@needs_dpkg
@pytest.mark.parametrize("extra", [(), ("--cross",)])
def test_a_build_without_linux_libc_dev_is_refused(tmp_path, extra):
    out = _release(tmp_path)
    next(out.glob("linux-libc-dev*.deb")).unlink()
    result = _cli(out, KR, STREAM, *extra)
    assert result.returncode == 1
    assert "no linux-libc-dev package" in result.stderr


@needs_dpkg
def test_the_reviews_fake_kernel_is_refused_in_a_release_layout(tmp_path):
    """Non-empty bytes at ./boot/vmlinuz-<kr>, identical beside the package, used to pass."""
    result = _cli(_release(tmp_path, kernel=FAKE_KERNEL))
    assert result.returncode == 1
    assert f"vmlinuz-{KR}: not a bzImage" in result.stderr


@needs_dpkg
def test_a_dpkg_deb_that_fails_after_streaming_the_archive_is_refused(tmp_path):
    """The members streamed through, but dpkg-deb exited non-zero: the archive is not trusted."""
    out = _release(tmp_path)
    shim = tmp_path / "shim"
    shim.mkdir()
    real = shutil.which("dpkg-deb")
    (shim / "dpkg-deb").write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "--fsys-tarfile" ]; then "{real}" "$@"; exit 2; fi\n'
        f'exec "{real}" "$@"\n',
        encoding="utf-8",
    )
    (shim / "dpkg-deb").chmod(0o755)
    env = {**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ.get('PATH', '')}"}
    result = _cli(out, env=env)
    assert result.returncode == 1
    assert f"linux-image-{KR}_{PKGVER}_amd64.deb: dpkg-deb cannot read its data archive" in result.stderr


@needs_dpkg
def test_a_release_candidate_named_with_a_tilde_is_refused(tmp_path):
    kr = "7.3.0-rc5-lusoris1-bleeding"
    out = tmp_path / "out"
    out.mkdir()
    image = {f"boot/vmlinuz-{kr}": bzimage(kr), f"boot/config-{kr}": CONFIG}
    _deb(out, f"linux-image-{kr}", version="7.3~rc5-lusoris1", files=image,
         name=f"linux-image-{kr}_7.3.rc5-lusoris1_amd64.deb")
    _deb(out, f"linux-headers-{kr}", version="7.3~rc5-lusoris1", name=f"linux-headers-{kr}_7.3.rc5-lusoris1_amd64.deb")
    _deb(out, "linux-libc-dev", version="7.3~rc5-lusoris1")
    (out / f"vmlinuz-{kr}").write_bytes(bzimage(kr))
    (out / f"kernel-bleeding-{ARCH}.config").write_bytes(CONFIG)
    result = _cli(out, kr, "bleeding")
    assert result.returncode == 1
    assert "linux-libc-dev_7.3~rc5-lusoris1_amd64.deb: file name does not match linux-libc-dev_7.3.rc5-lusoris1_amd64.deb" in result.stderr
    next(out.glob("linux-libc-dev_*.deb")).rename(out / "linux-libc-dev_7.3.rc5-lusoris1_amd64.deb")
    result = _cli(out, kr, "bleeding")
    assert result.returncode == 0, result.stderr


@needs_dpkg
@pytest.mark.parametrize(
    ("mutate", "finding"),
    [
        (lambda out: next(out.glob("linux-libc-dev*.deb")).write_bytes(b""), "zero bytes"),
        (lambda out: (out / "linux-libc-dev_7.2.8-lusoris1_amd64.deb").write_text("Lusoris Linux Libc Dev\n"),
         "dpkg-deb cannot read Package, Version and Architecture"),
        (lambda out: _deb(out, "linux-libc-dev", version="7.2.8-20260928-lusoris1",
                          name="linux-libc-dev_7.2.8-lusoris1_amd64.deb"), "version 7.2.8-20260928-lusoris1"),
        (lambda out: _deb(out, "linux-libc-dev", arch="arm64",
                          name="linux-libc-dev_7.2.8-lusoris1_amd64.deb"), "architecture arm64, expected amd64"),
        (lambda out: _deb(out, f"linux-image-{KR}-dbg"), f"package linux-image-{KR}-dbg is none of"),
        (lambda out: _deb(out, "linux-libc-dev", name="libc-dev.deb"), "also in"),
        (lambda out: (out / f"kernel-{STREAM}-{ARCH}.config").write_bytes(CONFIG + b"# CONFIG_X is not set\n"),
         f"kernel-{STREAM}-{ARCH}.config: differs from ./boot/config-{KR}"),
        (lambda out: (out / f"vmlinuz-{KR}").write_bytes(KERNEL[:-1] + b"!"),
         f"vmlinuz-{KR}: differs from ./boot/vmlinuz-{KR}"),
        (lambda out: _deb(out, f"linux-image-{KR}", files=_image_files(kernel=bzimage("7.2.8-lusoris1-lts")),
                          name=next(out.glob("linux-image-*.deb")).name),
         f"vmlinuz-{KR}: the bzImage reports '7.2.8-lusoris1-lts'"),
        (lambda out: (out / "SHA256SUMS").write_text("x  y\n"), "SHA256SUMS: not an artifact of this build"),
    ],
)
def test_every_misrepresentation_is_refused(tmp_path, mutate, finding):
    out = _release(tmp_path)
    mutate(out)
    result = _cli(out)
    assert result.returncode == 1, result.stdout
    assert finding in result.stderr, result.stderr


@needs_dpkg
def test_an_image_without_the_kernel_is_refused(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _deb(out, f"linux-image-{KR}", files={f"boot/config-{KR}": CONFIG})
    (out / f"vmlinuz-{KR}").write_bytes(KERNEL)
    (out / f"kernel-{STREAM}-{ARCH}.config").write_bytes(CONFIG)
    result = _cli(out)
    assert result.returncode == 1
    assert f"carries no non-empty ./boot/vmlinuz-{KR}" in result.stderr


@needs_dpkg
def test_a_release_without_an_image_package_is_refused(tmp_path):
    out = _release(tmp_path)
    next(out.glob("linux-image-*.deb")).unlink()
    result = _cli(out)
    assert result.returncode == 1
    assert f"no linux-image-{KR} package" in result.stderr


@needs_dpkg
def test_a_measured_release_the_packages_do_not_carry_is_refused(tmp_path):
    out = _release(tmp_path)
    other = "7.2.8-lusoris1-mainstream+"
    result = _cli(out, kernelrelease=other)
    assert result.returncode == 1
    assert "does not end with" in result.stderr or "none of" in result.stderr
