"""scripts/build_kernel.sh and its wrapper scripts/package-deb.sh: plans, refusals, no fabrication.

The production path compiles a kernel, which these hermetic tests do not do; the compile is
proven in the build-matrix workflow and in the evidence recorded for issue #18. What is tested
here is everything that must hold without a compile: the dry run states the exact build, and
every request that cannot produce an honest artifact is refused before anything is written.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD = REPO_ROOT / "scripts" / "build_kernel.sh"
PACKAGE = REPO_ROOT / "scripts" / "package-deb.sh"
VERSIONS = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
HOST = {"x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64", "riscv64": "riscv64"}.get(
    os.uname().machine, os.uname().machine
)


def _run(script: Path, *args: str, cwd: Path = REPO_ROOT, env: dict | None = None):
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )


def _dirs(tmp_path: Path) -> tuple[str, str]:
    return f"--work-dir={tmp_path / 'build'}", f"--output-dir={tmp_path / 'out'}"


def _kernelversion(stream: str) -> str:
    version = VERSIONS["streams"][stream]["version"]
    base, _, rc = version.partition("-")
    parts = base.split(".")
    parts += ["0"] * (3 - len(parts))
    return ".".join(parts) + (f"-{rc}" if rc else "")


@pytest.mark.parametrize("stream", VERSIONS["streams"])
@pytest.mark.parametrize("arch", VERSIONS["architectures"])
def test_dry_run_states_the_exact_build_and_writes_nothing(tmp_path, stream, arch):
    result = _run(BUILD, f"--stream={stream}", f"--arch={arch}", "--dry-run", *_dirs(tmp_path))
    assert result.returncode == 0, result.stderr
    data = VERSIONS["architectures"][arch]
    version = VERSIONS["streams"][stream]["version"]
    out = result.stdout
    assert f"ARCH={data['kernel_arch']} CROSS_COMPILE={data['cross_compile']}" in out
    assert f"bindeb-pkg LOCALVERSION=-lusoris1-{stream} KDEB_PKGVERSION={version}-lusoris1" in out
    assert f"expected {_kernelversion(stream)}-lusoris1-{stream}" in out
    assert "pkg.linux-upstream.nokerneldbg" in out
    cross = HOST != arch
    assert ("pkg.linux-upstream.nokernelheaders" in out) is cross
    assert f"{data['debian_arch']} packages at {version}-lusoris1" in out
    assert "[DRY-RUN] Nothing was fetched, compiled or written." in out
    assert list(tmp_path.iterdir()) == []


def test_revision_is_threaded_into_localversion_and_package_version(tmp_path):
    result = _run(BUILD, "--stream=lts", "--arch=x86_64", "--revision=3", "--dry-run", *_dirs(tmp_path))
    assert result.returncode == 0, result.stderr
    version = VERSIONS["streams"]["lts"]["version"]
    assert f"LOCALVERSION=-lusoris3-lts KDEB_PKGVERSION={version}-lusoris3" in result.stdout
    assert "-lusoris1" not in result.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--stream=nightly",), "no buildable stream 'nightly'"),
        (("--arch=mips",), "no buildable architecture 'mips'"),
        (("--revision=0",), "--revision must be a positive integer"),
        (("--revision=1a",), "--revision must be a positive integer"),
        (("--jobs=-4",), "--jobs must be a positive integer"),
        (("--bogus",), "unknown option --bogus"),
    ],
)
def test_malformed_requests_are_refused(tmp_path, args, message):
    result = _run(BUILD, *args, *_dirs(tmp_path))
    assert result.returncode == 1
    assert message in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_a_non_empty_output_directory_is_refused_before_anything_is_written(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "linux-image-stale.deb").write_bytes(b"old")
    result = _run(BUILD, "--stream=mainstream", "--arch=x86_64", *_dirs(tmp_path))
    assert result.returncode == 1
    assert "is not an empty directory" in result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out"]
    assert [p.name for p in out.iterdir()] == ["linux-image-stale.deb"]


def test_a_source_tree_that_is_not_a_kernel_is_refused(tmp_path):
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "Makefile").write_text("all:\n", encoding="utf-8")
    result = _run(BUILD, "--stream=mainstream", "--arch=x86_64", f"--source-tree={fake}", *_dirs(tmp_path))
    assert result.returncode == 1
    assert "is not a kernel source tree" in result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fake"]


def test_an_existing_unnamed_source_tree_is_not_reused_silently(tmp_path):
    existing = tmp_path / "build" / "linux-mainstream"
    existing.mkdir(parents=True)
    (existing / "Makefile").write_text("x\n", encoding="utf-8")
    result = _run(BUILD, "--stream=mainstream", "--arch=x86_64", *_dirs(tmp_path))
    assert result.returncode == 1
    assert "already exists; pass --source-tree=" in result.stderr
    assert not (tmp_path / "out").exists()


def test_missing_toolchain_is_refused_before_anything_is_written(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "python3", "uname", "nproc", "ls", "cat"):
        found = shutil.which(tool)
        assert found, tool
        (bin_dir / tool).symlink_to(found)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path)}
    result = _run(BUILD, "--stream=mainstream", "--arch=x86_64", *_dirs(tmp_path), env=env)
    assert result.returncode == 1
    assert "make is required" in result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bin"]


def _uutils_bin(tmp_path: Path, with_gnuinstall: bool) -> dict:
    """A PATH with every build tool stubbed, an install that reports uutils, and maybe gnuinstall."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "python3", "uname", "nproc", "ls", "cat", "head", "mkdir", "ln", "realpath",
                 "rm", "stat", "date", "dirname"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    stubs = ["make", "dpkg-buildpackage", "dpkg-deb", "dh_testdir", "depmod", "cpio", "rsync", "tar",
             VERSIONS["architectures"]["x86_64"]["cross_compile"] + "gcc"]
    if with_gnuinstall:
        stubs.append("gnuinstall")
    for tool in stubs:
        (bin_dir / tool).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / tool).chmod(0o755)
    (bin_dir / "install").write_text("#!/bin/sh\necho 'install (uutils coreutils) 0.8.0'\n", encoding="utf-8")
    (bin_dir / "install").chmod(0o755)
    return {"PATH": str(bin_dir), "HOME": str(tmp_path)}


def test_a_uutils_install_without_gnuinstall_is_refused(tmp_path):
    """uutils `install -D` races on a shared parent; the device-tree install runs it in parallel."""
    env = _uutils_bin(tmp_path, with_gnuinstall=False)
    result = _run(BUILD, "--stream=mainstream", "--arch=x86_64", *_dirs(tmp_path), env=env)
    assert result.returncode == 1
    assert "install is not GNU coreutils and gnuinstall is not installed" in result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bin"]


def test_a_uutils_install_is_shadowed_by_gnuinstall_for_the_build(tmp_path):
    env = _uutils_bin(tmp_path, with_gnuinstall=True)
    tree = tmp_path / "tree"
    (tree / "scripts" / "package").mkdir(parents=True)
    (tree / "Makefile").write_text("VERSION = 7\n", encoding="utf-8")
    (tree / "scripts" / "package" / "builddeb").write_text("#!/bin/sh\n", encoding="utf-8")
    (tree / "scripts" / "package" / "builddeb").chmod(0o755)
    result = _run(BUILD, "--stream=mainstream", "--arch=x86_64", f"--source-tree={tree}", *_dirs(tmp_path), env=env)
    shim = tmp_path / "build" / "mainstream-x86_64" / "gnu-install" / "install"
    assert "install is not GNU coreutils; the build uses" in result.stdout, result.stderr
    assert shim.is_symlink() and Path(os.readlink(shim)).name == "gnuinstall"
    assert result.returncode == 1, "the stand-in tree cannot be resolved, so the build stops there"


def test_no_placeholder_artifact_survives_in_the_build_script():
    text = BUILD.read_text(encoding="utf-8")
    assert "touch " not in text
    assert "/dev/urandom" not in text
    assert "-lusoris1" not in text, "the revision comes from --revision, not a literal"


# --- package-deb.sh (issue #31) -----------------------------------------------------------------


def test_package_deb_refuses_production_without_a_source_tree(tmp_path):
    result = _run(PACKAGE, "--stream=mainstream", "--arch=x86_64", cwd=tmp_path)
    assert result.returncode == 1
    assert "needs --source-tree" in result.stderr
    assert list(tmp_path.rglob("*")) == [], "no .deb and no SHA256SUMS may be written"


def test_package_deb_dry_run_delegates_to_the_build_and_writes_nothing(tmp_path):
    result = _run(PACKAGE, "--stream=realtime", "--arch=riscv64", "--dry-run", f"--output-dir={tmp_path / 'o'}")
    assert result.returncode == 0, result.stderr
    assert "LOCALVERSION=-lusoris1-realtime" in result.stdout
    assert "[DRY-RUN] Nothing was fetched, compiled or written." in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_package_deb_refuses_unknown_arguments(tmp_path):
    result = _run(PACKAGE, "--stream=mainstream", "--jobs=2", cwd=tmp_path)
    assert result.returncode == 1
    assert "unknown argument --jobs=2" in result.stderr


def test_package_deb_has_no_date_based_version_and_no_simulation():
    text = PACKAGE.read_text(encoding="utf-8")
    assert "date +" not in text
    assert "SIMULATION" not in text and "printf" not in text
    assert "sha256sum" not in text, "checksums are publish_release.sh's, over gated artifacts"
