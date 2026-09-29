"""The artifact gate opens every package and refuses anything that is not what it claims.

Packages are built here with ``dpkg-deb --build``; the tests that need one are skipped where
dpkg-deb is not installed (the build containers and the Ubuntu CI runners have it).
"""

from __future__ import annotations

import argparse
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
KERNEL = b"MZ\x00fake-bzImage" * 64

needs_dpkg = pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not installed")


def _exp(kernelrelease: str = KR, revision: int = 1) -> gate.Expectation:
    args = argparse.Namespace(stream=STREAM, arch=ARCH, revision=revision, kernelrelease=kernelrelease)
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


def _release(tmp_path: Path, **image) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    _deb(out, f"linux-image-{KR}", files=_image_files(**image))
    _deb(out, f"linux-headers-{KR}")
    _deb(out, "linux-libc-dev")
    (out / f"vmlinuz-{KR}").write_bytes(KERNEL)
    (out / f"kernel-{STREAM}-{ARCH}.config").write_bytes(CONFIG)
    return out


def _cli(out: Path, kernelrelease: str = KR, stream: str = STREAM):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_kernel_artifacts.py"), f"--dir={out}",
         f"--stream={stream}", f"--arch={ARCH}", f"--kernelrelease={kernelrelease}", "--revision=1",
         f"--versions={REPO_ROOT / 'versions.json'}"],
        capture_output=True, text=True, check=False,
    )


# --- without packages ---------------------------------------------------------------------------


def test_expectation_comes_from_versions_json():
    exp = _exp()
    assert (exp.kernelversion, exp.package_version, exp.debian_arch) == (KV, PKGVER, "amd64")
    assert exp.packages == (f"linux-image-{KR}", f"linux-headers-{KR}", "linux-libc-dev")
    assert _exp(revision=4).package_version == f"{VERSION}-lusoris4"


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
    assert _cli(out).returncode == 0


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
