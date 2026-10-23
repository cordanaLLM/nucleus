"""Tests for scripts/install-build-toolchain.sh package selection, caching, and flags."""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "scripts" / "install-build-toolchain.sh"

AARCH64_CROSS = "gcc-aarch64-linux-gnu"
RISCV64_CROSS = "gcc-riscv64-linux-gnu"


@pytest.fixture
def apt_stub(tmp_path):
    """Create a temporary directory containing an apt-get stub that logs invocations."""
    log_file = tmp_path / "apt_calls.log"
    stub = tmp_path / "apt-get"
    stub.write_text(
        f"""#!/usr/bin/env bash
echo "$*" >> "{log_file}"
exit 0
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return tmp_path, log_file


def _run_installer(
    args: list[str],
    env_arch: str | None = None,
    apt_stub_dir: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_arch is not None:
        env["ARCH"] = env_arch
    else:
        env.pop("ARCH", None)
    if apt_stub_dir is not None:
        env["PATH"] = f"{apt_stub_dir}:{env['PATH']}"

    return subprocess.run(
        ["bash", str(INSTALLER)] + args,
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


def _get_calls(log_file: Path) -> list[str]:
    if not log_file.exists():
        return []
    return [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_arch_x86_64_installs_neither_cross_package(apt_stub):
    stub_dir, log_file = apt_stub
    _run_installer([], env_arch="x86_64", apt_stub_dir=stub_dir)
    calls = _get_calls(log_file)
    assert len(calls) == 2, f"Expected 2 apt-get calls, got {len(calls)}"
    install_call = calls[1]
    assert install_call.startswith("install")
    assert AARCH64_CROSS not in install_call
    assert RISCV64_CROSS not in install_call


def test_arch_riscv64_installs_riscv_only(apt_stub):
    stub_dir, log_file = apt_stub
    _run_installer([], env_arch="riscv64", apt_stub_dir=stub_dir)
    calls = _get_calls(log_file)
    assert len(calls) == 2
    install_call = calls[1]
    assert install_call.startswith("install")
    assert RISCV64_CROSS in install_call
    assert AARCH64_CROSS not in install_call


def test_arch_arm64_installs_aarch64_only(apt_stub):
    stub_dir, log_file = apt_stub
    _run_installer([], env_arch="arm64", apt_stub_dir=stub_dir)
    calls = _get_calls(log_file)
    assert len(calls) == 2
    install_call = calls[1]
    assert install_call.startswith("install")
    assert AARCH64_CROSS in install_call
    assert RISCV64_CROSS not in install_call


def test_all_arches_installs_both_cross_packages(apt_stub):
    stub_dir, log_file = apt_stub
    _run_installer(["--all-arches"], env_arch="x86_64", apt_stub_dir=stub_dir)
    calls = _get_calls(log_file)
    assert len(calls) == 2
    install_call = calls[1]
    assert install_call.startswith("install")
    assert AARCH64_CROSS in install_call
    assert RISCV64_CROSS in install_call


def test_arch_unset_installs_both_cross_packages(apt_stub):
    stub_dir, log_file = apt_stub
    _run_installer([], env_arch=None, apt_stub_dir=stub_dir)
    calls = _get_calls(log_file)
    assert len(calls) == 2
    install_call = calls[1]
    assert install_call.startswith("install")
    assert AARCH64_CROSS in install_call
    assert RISCV64_CROSS in install_call


def test_arch_bogus_refused_nonzero(apt_stub):
    stub_dir, log_file = apt_stub
    proc = _run_installer([], env_arch="bogus", apt_stub_dir=stub_dir, check=False)
    assert proc.returncode != 0
    assert "Refused" in proc.stderr
    assert "unknown architecture bogus" in proc.stderr
    assert _get_calls(log_file) == []


def test_archive_dir_passes_options_and_creates_partial_dir(apt_stub, tmp_path):
    stub_dir, log_file = apt_stub
    archive_dir = tmp_path / "apt_cache"
    assert not archive_dir.exists()
    _run_installer([f"--archive-dir={archive_dir}"], env_arch="x86_64", apt_stub_dir=stub_dir)
    assert (archive_dir / "partial").is_dir()
    calls = _get_calls(log_file)
    assert len(calls) == 2
    for call in calls:
        assert f"-o Dir::Cache::Archives={archive_dir}" in call
        assert "-o APT::Keep-Downloaded-Packages=true" in call


def test_retries_and_timeouts_present_on_both_calls(apt_stub):
    stub_dir, log_file = apt_stub
    _run_installer([], env_arch="x86_64", apt_stub_dir=stub_dir)
    calls = _get_calls(log_file)
    assert len(calls) == 2
    for call in calls:
        assert "-o Acquire::Retries=5" in call
        assert "-o Acquire::http::Timeout=30" in call
        assert "-o Acquire::https::Timeout=30" in call


def test_planted_negative_missing_cross_compiler_when_required(apt_stub):
    """Planted negative: asserting that an arm64 build has no cross-compiler fails."""
    stub_dir, log_file = apt_stub
    _run_installer([], env_arch="arm64", apt_stub_dir=stub_dir)
    calls = _get_calls(log_file)
    install_call = calls[1]
    with pytest.raises(AssertionError):
        assert AARCH64_CROSS not in install_call
