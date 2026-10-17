"""scripts/build_kernel.sh and its wrapper scripts/package-deb.sh: plans, refusals, no fabrication.

The production path compiles a kernel, which these hermetic tests do not do; the compile is
proven in the build-matrix workflow and in the evidence recorded for issue #18. What is tested
here is everything that must hold without a compile: the dry run states the exact build, and
every request that cannot produce an honest artifact is refused before anything is written.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD = REPO_ROOT / "scripts" / "build_kernel.sh"
PACKAGE = REPO_ROOT / "scripts" / "package-deb.sh"
DATE_WRAPPER = REPO_ROOT / "scripts" / "lib" / "date-from-epoch.sh"
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


def _debian_version(stream: str) -> str:
    """The Debian upstream version: a release candidate's -rc<N> is ~rc<N>, as mkdebian spells it."""
    return VERSIONS["streams"][stream]["version"].replace("-rc", "~rc")


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
    version = _debian_version(stream)
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


def test_dry_run_states_the_signing_step_after_an_unsigned_build(tmp_path):
    """Issue #77: the build trusts a certificate, signs nothing, and a recorded step signs after."""
    result = _run(BUILD, "--stream=mainstream", "--arch=x86_64", "--dry-run", *_dirs(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "key:     a key generated for this build, deleted after signing" in result.stdout
    assert "certs/nucleus-module-signing.pem (CONFIG_SYSTEM_TRUSTED_KEYS)" in result.stdout
    assert "sign:    scripts/sign_kernel_modules.py over the linux-image package" in result.stdout
    key = tmp_path / "key.pem"
    key.write_text("stand-in\n", encoding="utf-8")
    result = _run(BUILD, "--stream=mainstream", "--arch=x86_64", "--dry-run", f"--module-key={key}")
    assert result.returncode == 0, result.stderr
    assert f"key:     {key};" in result.stdout


def test_a_module_key_that_is_not_a_file_is_refused(tmp_path):
    """Refused with the request, before anything is fetched or written (a dry run checks nothing)."""
    missing = tmp_path / "missing.pem"
    result = _run(
        BUILD,
        "--stream=mainstream",
        "--arch=x86_64",
        f"--module-key={missing}",
        *_dirs(tmp_path / "dirs"),
    )
    assert result.returncode == 1
    assert f"--module-key {missing} is not a file" in result.stderr
    assert not (tmp_path / "dirs").exists()


def test_a_release_candidate_package_version_sorts_before_the_release(tmp_path):
    """bleeding 7.3-rcN packages as 7.3~rcN: dpkg orders it before 7.3, where 7.3-rcN sorts after."""
    version = VERSIONS["streams"]["bleeding"]["version"]
    match = re.fullmatch(r"(\d+\.\d+)-rc(\d+)", version)
    if match is None:
        pytest.skip(f"bleeding pins {version}, not a release candidate")
    base, rc = match.groups()
    result = _run(BUILD, "--stream=bleeding", "--arch=x86_64", "--dry-run", *_dirs(tmp_path))
    assert result.returncode == 0, result.stderr
    assert (
        f"LOCALVERSION=-lusoris1-bleeding KDEB_PKGVERSION={base}~rc{rc}-lusoris1" in result.stdout
    )
    assert f"expected {base}.0-rc{rc}-lusoris1-bleeding" in result.stdout, (
        "the kernelrelease keeps -rc"
    )
    assert f"<package>_{base}.rc{rc}-lusoris1_amd64.deb" in result.stdout, "GitHub renames ~"
    if shutil.which("dpkg"):
        compare = ["dpkg", "--compare-versions"]
        assert (
            subprocess.run(
                [*compare, f"{base}~rc{rc}-lusoris1", "lt", f"{base}-lusoris1"], check=False
            ).returncode
            == 0
        )
        assert (
            subprocess.run(
                [*compare, f"{base}-rc{rc}-lusoris1", "lt", f"{base}-lusoris1"], check=False
            ).returncode
            != 0
        )


def test_revision_is_threaded_into_localversion_and_package_version(tmp_path):
    result = _run(
        BUILD, "--stream=lts", "--arch=x86_64", "--revision=3", "--dry-run", *_dirs(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    version = VERSIONS["streams"]["lts"]["version"]
    assert f"LOCALVERSION=-lusoris3-lts KDEB_PKGVERSION={version}-lusoris3" in result.stdout
    assert "-lusoris1" not in result.stdout


@pytest.mark.skipif(shutil.which("dpkg") is None, reason="dpkg orders Debian versions")
def test_a_later_revision_is_a_newer_package_version():
    """linux-libc-dev keeps its name across revisions, so dpkg must upgrade lusoris1 to lusoris2."""
    compare = ["dpkg", "--compare-versions"]
    for older, newer in (
        ("7.2.8-lusoris1", "7.2.8-lusoris2"),
        ("7.2.8-lusoris9", "7.2.8-lusoris10"),
        ("7.3~rc5-lusoris2", "7.3-lusoris1"),
    ):
        assert subprocess.run([*compare, older, "lt", newer], check=False).returncode == 0, (
            older,
            newer,
        )


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
    result = _run(
        BUILD, "--stream=mainstream", "--arch=x86_64", f"--source-tree={fake}", *_dirs(tmp_path)
    )
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
    for tool in (
        "bash",
        "python3",
        "uname",
        "nproc",
        "ls",
        "cat",
        "head",
        "mkdir",
        "ln",
        "realpath",
        "rm",
        "stat",
        "date",
        "dirname",
    ):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    stubs = [
        "make",
        "dpkg-buildpackage",
        "dpkg-deb",
        "dh_testdir",
        "depmod",
        "cpio",
        "rsync",
        "tar",
        "openssl",
        VERSIONS["architectures"]["x86_64"]["cross_compile"] + "gcc",
    ]
    if with_gnuinstall:
        stubs.append("gnuinstall")
    for tool in stubs:
        (bin_dir / tool).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (bin_dir / tool).chmod(0o755)
    (bin_dir / "install").write_text(
        "#!/bin/sh\necho 'install (uutils coreutils) 0.8.0'\n", encoding="utf-8"
    )
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
    result = _run(
        BUILD,
        "--stream=mainstream",
        "--arch=x86_64",
        f"--source-tree={tree}",
        *_dirs(tmp_path),
        env=env,
    )
    shim = tmp_path / "build" / "mainstream-x86_64" / "gnu-install" / "install"
    assert "install is not GNU coreutils; the build uses" in result.stdout, result.stderr
    assert shim.is_symlink() and Path(os.readlink(shim)).name == "gnuinstall"
    assert result.returncode == 1, "the stand-in tree cannot be resolved, so the build stops there"


def test_a_stale_build_record_is_removed_before_the_build(tmp_path):
    """A failed rebuild must not leave the earlier build's record claiming a gated kernel."""
    env = _uutils_bin(tmp_path, with_gnuinstall=True)
    tree = tmp_path / "tree"
    (tree / "scripts" / "package").mkdir(parents=True)
    (tree / "Makefile").write_text("VERSION = 7\n", encoding="utf-8")
    (tree / "scripts" / "package" / "builddeb").write_text("#!/bin/sh\n", encoding="utf-8")
    (tree / "scripts" / "package" / "builddeb").chmod(0o755)
    record = tmp_path / "build" / "mainstream-x86_64" / "build.json"
    record.parent.mkdir(parents=True)
    record.write_text('{"kernelrelease": "7.2.8-lusoris1-mainstream"}\n', encoding="utf-8")
    result = _run(
        BUILD,
        "--stream=mainstream",
        "--arch=x86_64",
        f"--source-tree={tree}",
        *_dirs(tmp_path),
        env=env,
    )
    assert result.returncode == 1, "the stand-in tree cannot be resolved, so the build stops there"
    assert not record.exists()


def test_no_placeholder_artifact_survives_in_the_build_script():
    text = BUILD.read_text(encoding="utf-8")
    assert "touch " not in text
    assert "/dev/urandom" not in text
    assert "-lusoris1" not in text, "the revision comes from --revision, not a literal"


def _extract_record_py() -> str:
    text = BUILD.read_text(encoding="utf-8")
    match = re.search(r"readonly RECORD_PY='(.*?)'\n\n", text, re.DOTALL)
    assert match is not None
    return match.group(1)


def test_record_py_records_module_symvers_sha256_and_symbols(tmp_path):
    record_py = _extract_record_py()
    record_file = tmp_path / "build.json"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    config_file = out_dir / "kernel-mainstream-x86_64.config"
    config_file.write_text("CONFIG_FOO=y\n", encoding="utf-8")

    symvers = tmp_path / "Module.symvers"
    symvers_content = (
        "0x12345678\tfoo\tvmlinux\tEXPORT_SYMBOL\n0x87654321\tbar\tvmlinux\tEXPORT_SYMBOL_GPL\n"
    )
    symvers.write_text(symvers_content, encoding="utf-8")
    signing = tmp_path / "signing.json"
    signing.write_text('{"hash": "sha512", "modules": 280}\n', encoding="utf-8")

    res = subprocess.run(
        [
            sys.executable,
            "-c",
            record_py,
            str(record_file),
            str(out_dir),
            str(symvers),
            str(signing),
            "module_key=ephemeral",
            "stream=mainstream",
            "arch=x86_64",
            "version=7.2.10",
            "kernelversion=7.2.10",
            "revision=1",
            "localversion=-lusoris1-mainstream",
            "kernelrelease=7.2.10-lusoris1-mainstream",
            "package_version=7.2.10-lusoris1",
            "kernel_arch=x86",
            "debian_arch=amd64",
            "cross_compile=",
            "host_arch=x86_64",
            "profiles=",
            "source_date_epoch=1234567890",
            "build_timestamp=2026-10-16",
            "build_seconds=42",
            "config=kernel-mainstream-x86_64.config",
            f"output_dir={out_dir}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(record_file.read_text(encoding="utf-8"))
    assert data["module_symvers_sha256"] == hashlib.sha256(symvers.read_bytes()).hexdigest()
    assert data["module_symbols"] == 2
    assert data["module_signing"] == {"key": "ephemeral", "hash": "sha512", "modules": 280}
    assert data["kernelrelease"] == "7.2.10-lusoris1-mainstream"


def test_record_py_fails_when_module_symvers_missing(tmp_path):
    record_py = _extract_record_py()
    record_file = tmp_path / "build.json"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    config_file = out_dir / "kernel-mainstream-x86_64.config"
    config_file.write_text("CONFIG_FOO=y\n", encoding="utf-8")
    missing_symvers = tmp_path / "nonexistent-Module.symvers"

    res = subprocess.run(
        [
            sys.executable,
            "-c",
            record_py,
            str(record_file),
            str(out_dir),
            str(missing_symvers),
            "stream=mainstream",
            "arch=x86_64",
            "version=7.2.10",
            "kernelversion=7.2.10",
            "revision=1",
            "localversion=-lusoris1-mainstream",
            "kernelrelease=7.2.10-lusoris1-mainstream",
            "package_version=7.2.10-lusoris1",
            "kernel_arch=x86",
            "debian_arch=amd64",
            "cross_compile=",
            "host_arch=x86_64",
            "profiles=",
            "source_date_epoch=1234567890",
            "build_timestamp=2026-10-16",
            "build_seconds=42",
            "config=kernel-mainstream-x86_64.config",
            f"output_dir={out_dir}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0


# --- package-deb.sh (issue #31) -----------------------------------------------------------------


def test_package_deb_refuses_production_without_a_source_tree(tmp_path):
    result = _run(PACKAGE, "--stream=mainstream", "--arch=x86_64", cwd=tmp_path)
    assert result.returncode == 1
    assert "needs --source-tree" in result.stderr
    assert list(tmp_path.rglob("*")) == [], "no .deb and no SHA256SUMS may be written"


def test_package_deb_dry_run_delegates_to_the_build_and_writes_nothing(tmp_path):
    result = _run(
        PACKAGE,
        "--stream=realtime",
        "--arch=riscv64",
        "--dry-run",
        f"--output-dir={tmp_path / 'o'}",
    )
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


def test_date_wrapper_formats_rfc_with_epoch():
    env = {**os.environ, "SOURCE_DATE_EPOCH": "1700000000"}
    result = _run(DATE_WRAPPER, "-R", env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Tue, 14 Nov 2023 22:13:20 +0000"


def test_date_wrapper_passes_other_arguments_through():
    result = _run(DATE_WRAPPER, "+%s", "-d", "@5")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "5"


@pytest.mark.parametrize(
    "epoch",
    [
        None,
        "",
        "garbage",
        "1700000000abc",
        "-1700000000",
        "1700 0000",
    ],
)
def test_date_wrapper_refuses_unset_or_garbage_epoch(epoch):
    env = {k: v for k, v in os.environ.items() if k != "SOURCE_DATE_EPOCH"}
    if epoch is not None:
        env["SOURCE_DATE_EPOCH"] = epoch
    result = _run(DATE_WRAPPER, "-R", env=env)
    assert result.returncode == 1
    assert "SOURCE_DATE_EPOCH" in result.stderr


def test_build_kernel_dry_run_mentions_date_wrapper(tmp_path):
    result = _run(BUILD, "--stream=mainstream", "--arch=x86_64", "--dry-run", *_dirs(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "date wrapper" in result.stdout
    assert "prepended to PATH for bindeb-pkg" in result.stdout


def test_date_wrapper_placed_on_path_only_for_bindeb_pkg():
    text = BUILD.read_text(encoding="utf-8")
    assert 'PATH="${date_dir}:${PATH}"' in text
    assert "kernel_make" in text and "bindeb-pkg" in text
    # The date wrapper must NOT be exported globally into ambient PATH.
    compile_fn = text.split("compile_packages()")[1].split("collect_artifacts()")[0]
    assert "export PATH=" not in compile_fn
    # Wrapper itself must resolve the real date without PATH search (never recurse).
    wrapper_text = DATE_WRAPPER.read_text(encoding="utf-8")
    assert "command -v date" not in wrapper_text
    assert "which date" not in wrapper_text
    assert "type -p date" not in wrapper_text


def test_fixed_environment_drops_the_builders_debian_identity():
    # mkdebian prefers DEBFULLNAME and DEBEMAIL over KBUILD_BUILD_USER and KBUILD_BUILD_HOST
    # for the debian/changelog maintainer line, which every package ships.
    text = BUILD.read_text(encoding="utf-8")
    fixed = text.split("fixed_environment() {")[1].split("\n}\n")[0]
    assert re.search(r"^\s*unset DEBFULLNAME DEBEMAIL$", fixed, re.MULTILINE)
