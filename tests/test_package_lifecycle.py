# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Unit tests for scripts/package_lifecycle.py."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.package_lifecycle as plc

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_check_environment_refuses_non_root() -> None:
    """Refuse execution when effective UID is not 0."""
    with pytest.raises(PermissionError, match="must run in a throwaway container \\(euid != 0\\)"):
        plc.check_environment(euid=1000, env={"NUCLEUS_DISPOSABLE_ROOT": "1"})


def test_check_environment_refuses_missing_disposable_env() -> None:
    """Refuse execution when NUCLEUS_DISPOSABLE_ROOT is not 1."""
    with pytest.raises(PermissionError, match="NUCLEUS_DISPOSABLE_ROOT=1 is required"):
        plc.check_environment(euid=0, env={})

    with pytest.raises(PermissionError, match="NUCLEUS_DISPOSABLE_ROOT=1 is required"):
        plc.check_environment(euid=0, env={"NUCLEUS_DISPOSABLE_ROOT": "0"})


def test_check_environment_passes_for_root_in_disposable_container() -> None:
    """Accept execution when euid is 0 and NUCLEUS_DISPOSABLE_ROOT is 1."""
    plc.check_environment(euid=0, env={"NUCLEUS_DISPOSABLE_ROOT": "1"})


def test_subprocess_refusal_when_non_root(tmp_path: Path) -> None:
    """When executed headlessly by non-root user, package_lifecycle.py exits 1 with message."""
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "package_lifecycle.py"),
        "--stream",
        "mainstream",
        "--release",
        str(tmp_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 1
    assert "package_lifecycle:" in res.stderr
    assert "throwaway container" in res.stderr


def test_derive_kernelrelease_success(tmp_path: Path) -> None:
    """Extract kernelrelease from the single vmlinuz-* asset."""
    (tmp_path / "vmlinuz-7.2.8-lusoris2-mainstream").write_bytes(b"kernel")
    kr = plc.derive_kernelrelease(tmp_path)
    assert kr == "7.2.8-lusoris2-mainstream"


def test_derive_kernelrelease_refuses_zero_or_multiple(tmp_path: Path) -> None:
    """Refuse directories with 0 or >1 vmlinuz-* assets."""
    with pytest.raises(ValueError, match="found 0"):
        plc.derive_kernelrelease(tmp_path)

    (tmp_path / "vmlinuz-7.2.8-lusoris1-mainstream").write_bytes(b"1")
    (tmp_path / "vmlinuz-7.2.8-lusoris2-mainstream").write_bytes(b"2")
    with pytest.raises(ValueError, match="found 2:"):
        plc.derive_kernelrelease(tmp_path)


def test_derive_kernelrelease_refuses_empty_suffix(tmp_path: Path) -> None:
    """Refuse vmlinuz- without suffix."""
    (tmp_path / "vmlinuz-").write_bytes(b"")
    with pytest.raises(ValueError, match="empty kernelrelease"):
        plc.derive_kernelrelease(tmp_path)


def test_find_package_debs(tmp_path: Path) -> None:
    """Locate matching image and headers deb packages."""
    kr = "7.2.8-lusoris2-mainstream"
    img = tmp_path / f"linux-image-{kr}_7.2.8-lusoris2_amd64.deb"
    hdr = tmp_path / f"linux-headers-{kr}_7.2.8-lusoris2_amd64.deb"
    img.write_bytes(b"image-deb")
    hdr.write_bytes(b"headers-deb")

    found_img, found_hdr = plc.find_package_debs(tmp_path, kr)
    assert found_img == img
    assert found_hdr == hdr


def test_find_package_debs_refuses_missing_or_duplicate(tmp_path: Path) -> None:
    """Refuse missing or duplicate image/headers debs."""
    kr = "7.2.8-lusoris2-mainstream"
    with pytest.raises(FileNotFoundError, match="expected 1 linux-image deb"):
        plc.find_package_debs(tmp_path, kr)

    img1 = tmp_path / f"linux-image-{kr}_1_amd64.deb"
    img2 = tmp_path / f"linux-image-{kr}_2_amd64.deb"
    img1.write_bytes(b"1")
    img2.write_bytes(b"2")
    with pytest.raises(FileNotFoundError, match="found 2"):
        plc.find_package_debs(tmp_path, kr)


@pytest.fixture
def mock_lifecycle_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up mock release assets, fake root tree, and stub dpkg/dpkg-query tools."""
    fake_root = tmp_path / "root"
    fake_root.mkdir()
    (fake_root / "boot").mkdir()
    (fake_root / "lib" / "modules").mkdir(parents=True)
    (fake_root / "usr" / "src").mkdir(parents=True)

    prev_dir = tmp_path / "prev_release"
    prev_dir.mkdir()
    prev_kr = "7.2.8-lusoris1-mainstream"
    (prev_dir / f"vmlinuz-{prev_kr}").write_bytes(b"VMLINUZ_PREV_12345")
    (prev_dir / "kernel-mainstream-x86_64.config").write_bytes(b"CONFIG_PREV_12345")
    (prev_dir / f"linux-image-{prev_kr}_7.2.8-lusoris1_amd64.deb").write_bytes(b"deb")
    (prev_dir / f"linux-headers-{prev_kr}_7.2.8-lusoris1_amd64.deb").write_bytes(b"deb")

    rel_dir = tmp_path / "target_release"
    rel_dir.mkdir()
    rel_kr = "7.2.8-lusoris2-mainstream"
    (rel_dir / f"vmlinuz-{rel_kr}").write_bytes(b"VMLINUZ_TARGET_67890")
    (rel_dir / "kernel-mainstream-x86_64.config").write_bytes(b"CONFIG_TARGET_67890")
    (rel_dir / f"linux-image-{rel_kr}_7.2.8-lusoris2_amd64.deb").write_bytes(b"deb")
    (rel_dir / f"linux-headers-{rel_kr}_7.2.8-lusoris2_amd64.deb").write_bytes(b"deb")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    dpkg_query_script = bin_dir / "dpkg-query"
    dpkg_query_script.write_text("#!/bin/sh\necho 'install ok installed'\n", encoding="utf-8")
    dpkg_query_script.chmod(dpkg_query_script.stat().st_mode | stat.S_IEXEC)

    dpkg_script = bin_dir / "dpkg"
    dpkg_script_content = f"""#!/bin/sh
set -e
action="$1"
shift
ROOT="{fake_root}"
if [ "$action" = "-i" ]; then
  for deb in "$@"; do
    base=$(basename "$deb")
    dir=$(dirname "$deb")
    case "$base" in
      linux-image-*)
        kr=$(echo "$base" | sed -E 's/^linux-image-([^_]+)_.*$/\\1/')
        if [ "${{CLOBBER_OTHER_KERNELS:-}}" = "1" ]; then
          rm -f "$ROOT"/boot/vmlinuz-*
        fi
        mkdir -p "$ROOT/boot" "$ROOT/lib/modules/$kr"
        cp "$dir/vmlinuz-$kr" "$ROOT/boot/vmlinuz-$kr"
        cp "$dir"/kernel-*-x86_64.config "$ROOT/boot/config-$kr"
        touch "$ROOT/boot/System.map-$kr"
        touch "$ROOT/boot/initrd.img-$kr"
        touch "$ROOT/lib/modules/$kr/modules.dep"
        ;;
      linux-headers-*)
        kr=$(echo "$base" | sed -E 's/^linux-headers-([^_]+)_.*$/\\1/')
        mkdir -p "$ROOT/usr/src/linux-headers-$kr"
        ;;
    esac
  done
elif [ "$action" = "--purge" ]; then
  for pkg in "$@"; do
    case "$pkg" in
      linux-image-*)
        kr=${{pkg#linux-image-}}
        rm -f "$ROOT/boot/vmlinuz-$kr" "$ROOT/boot/config-$kr" "$ROOT/boot/System.map-$kr" "$ROOT/boot/initrd.img-$kr"
        rm -rf "$ROOT/lib/modules/$kr"
        ;;
      linux-headers-*)
        kr=${{pkg#linux-headers-}}
        rm -rf "$ROOT/usr/src/linux-headers-$kr"
        ;;
    esac
  done
fi
"""
    dpkg_script.write_text(dpkg_script_content, encoding="utf-8")
    dpkg_script.chmod(dpkg_script.stat().st_mode | stat.S_IEXEC)

    new_path = f"{bin_dir}:{os.environ.get('PATH', '')}"
    monkeypatch.setenv("PATH", new_path)
    monkeypatch.setattr(plc, "check_environment", lambda *a, **k: None)

    return {
        "fake_root": fake_root,
        "prev_dir": prev_dir,
        "rel_dir": rel_dir,
        "prev_kr": prev_kr,
        "rel_kr": rel_kr,
        "bin_dir": bin_dir,
    }


def test_full_lifecycle_single_release(mock_lifecycle_env: dict[str, Path | str]) -> None:
    """Verify package lifecycle without --previous (install, verify, purge)."""
    fake_root = mock_lifecycle_env["fake_root"]
    rel_dir = mock_lifecycle_env["rel_dir"]

    rc = plc.main(
        [
            "--stream",
            "mainstream",
            "--release",
            str(rel_dir),
            "--root",
            str(fake_root),
        ]
    )
    assert rc == 0


def test_full_lifecycle_with_upgrade_coexistence(
    mock_lifecycle_env: dict[str, Path | str],
) -> None:
    """Verify package lifecycle with --previous (coexistence and clean uninstallation)."""
    fake_root = mock_lifecycle_env["fake_root"]
    prev_dir = mock_lifecycle_env["prev_dir"]
    rel_dir = mock_lifecycle_env["rel_dir"]

    rc = plc.main(
        [
            "--stream",
            "mainstream",
            "--release",
            str(rel_dir),
            "--previous",
            str(prev_dir),
            "--root",
            str(fake_root),
        ]
    )
    assert rc == 0


def test_verify_installed_release_vmlinuz_mismatch(
    mock_lifecycle_env: dict[str, Path | str],
) -> None:
    """Refuse installed release when /boot/vmlinuz-KR differs from release asset."""
    fake_root = mock_lifecycle_env["fake_root"]
    rel_dir = mock_lifecycle_env["rel_dir"]
    rel_kr = str(mock_lifecycle_env["rel_kr"])

    plc.install_release(Path(rel_dir), rel_kr)
    (Path(fake_root) / "boot" / f"vmlinuz-{rel_kr}").write_bytes(b"CORRUPTED_BYTES")

    with pytest.raises(ValueError, match="not byte-identical"):
        plc.verify_installed_release(rel_kr, Path(rel_dir), "mainstream", Path(fake_root))


def test_verify_installed_release_config_mismatch(
    mock_lifecycle_env: dict[str, Path | str],
) -> None:
    """Refuse installed release when /boot/config-KR differs from release config."""
    fake_root = mock_lifecycle_env["fake_root"]
    rel_dir = mock_lifecycle_env["rel_dir"]
    rel_kr = str(mock_lifecycle_env["rel_kr"])

    plc.install_release(Path(rel_dir), rel_kr)
    (Path(fake_root) / "boot" / f"config-{rel_kr}").write_bytes(b"CORRUPTED_CONFIG")

    with pytest.raises(ValueError, match="not byte-identical"):
        plc.verify_installed_release(rel_kr, Path(rel_dir), "mainstream", Path(fake_root))


def test_verify_installed_release_missing_initrd(
    mock_lifecycle_env: dict[str, Path | str],
) -> None:
    """Refuse installed release when initrd.img-KR was not generated by postinst hook."""
    fake_root = mock_lifecycle_env["fake_root"]
    rel_dir = mock_lifecycle_env["rel_dir"]
    rel_kr = str(mock_lifecycle_env["rel_kr"])

    plc.install_release(Path(rel_dir), rel_kr)
    (Path(fake_root) / "boot" / f"initrd.img-{rel_kr}").unlink()

    with pytest.raises(FileNotFoundError, match="missing initramfs image"):
        plc.verify_installed_release(rel_kr, Path(rel_dir), "mainstream", Path(fake_root))


def test_verify_installed_release_missing_modules_dep(
    mock_lifecycle_env: dict[str, Path | str],
) -> None:
    """Refuse installed release when modules.dep is missing."""
    fake_root = mock_lifecycle_env["fake_root"]
    rel_dir = mock_lifecycle_env["rel_dir"]
    rel_kr = str(mock_lifecycle_env["rel_kr"])

    plc.install_release(Path(rel_dir), rel_kr)
    (Path(fake_root) / "lib" / "modules" / rel_kr / "modules.dep").unlink()

    with pytest.raises(FileNotFoundError, match="missing modules.dep"):
        plc.verify_installed_release(rel_kr, Path(rel_dir), "mainstream", Path(fake_root))


def test_verify_installed_release_missing_system_map(
    mock_lifecycle_env: dict[str, Path | str],
) -> None:
    """Refuse installed release when System.map is missing."""
    fake_root = mock_lifecycle_env["fake_root"]
    rel_dir = mock_lifecycle_env["rel_dir"]
    rel_kr = str(mock_lifecycle_env["rel_kr"])

    plc.install_release(Path(rel_dir), rel_kr)
    (Path(fake_root) / "boot" / f"System.map-{rel_kr}").unlink()

    with pytest.raises(FileNotFoundError, match="missing System.map"):
        plc.verify_installed_release(rel_kr, Path(rel_dir), "mainstream", Path(fake_root))


def test_verify_installed_release_missing_headers_dir(
    mock_lifecycle_env: dict[str, Path | str],
) -> None:
    """Refuse installed release when usr/src/linux-headers-KR is missing."""
    fake_root = mock_lifecycle_env["fake_root"]
    rel_dir = mock_lifecycle_env["rel_dir"]
    rel_kr = str(mock_lifecycle_env["rel_kr"])

    plc.install_release(Path(rel_dir), rel_kr)
    (Path(fake_root) / "usr" / "src" / f"linux-headers-{rel_kr}").rmdir()

    with pytest.raises(FileNotFoundError, match="missing headers directory"):
        plc.verify_installed_release(rel_kr, Path(rel_dir), "mainstream", Path(fake_root))


def test_verify_dpkg_status_not_installed(
    mock_lifecycle_env: dict[str, Path | str],
) -> None:
    """Refuse when dpkg status is not 'install ok installed'."""
    bin_dir = Path(mock_lifecycle_env["bin_dir"])
    dpkg_query_script = bin_dir / "dpkg-query"
    dpkg_query_script.write_text("#!/bin/sh\necho 'unknown ok not-installed'\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected 'install ok installed'"):
        plc.verify_dpkg_status("linux-image-test")


def test_verify_purged_release_leftovers(
    mock_lifecycle_env: dict[str, Path | str],
) -> None:
    """Refuse when files or directories remain after purge."""
    fake_root = Path(mock_lifecycle_env["fake_root"])
    kr = "7.2.8-lusoris1-mainstream"

    # Residual boot file
    (fake_root / "boot" / f"vmlinuz-{kr}").write_bytes(b"residual")
    with pytest.raises(RuntimeError, match="files for .* left in .* after purge"):
        plc.verify_purged_release(kr, fake_root)
    (fake_root / "boot" / f"vmlinuz-{kr}").unlink()

    # Residual modules dir
    (fake_root / "lib" / "modules" / kr).mkdir(parents=True)
    with pytest.raises(RuntimeError, match="modules directory left after purge"):
        plc.verify_purged_release(kr, fake_root)
    (fake_root / "lib" / "modules" / kr).rmdir()

    # Residual headers dir
    (fake_root / "usr" / "src" / f"linux-headers-{kr}").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="headers directory left after purge"):
        plc.verify_purged_release(kr, fake_root)
    (fake_root / "usr" / "src" / f"linux-headers-{kr}").rmdir()


def test_an_upgrade_that_removes_the_previous_kernel_is_refused(
    mock_lifecycle_env: dict[str, Path | str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Step d: installing the new release must leave the previous kernel bootable."""
    monkeypatch.setenv("CLOBBER_OTHER_KERNELS", "1")
    prev_kr = mock_lifecycle_env["prev_kr"]
    rc = plc.main(
        [
            "--stream",
            "mainstream",
            "--release",
            str(mock_lifecycle_env["rel_dir"]),
            "--previous",
            str(mock_lifecycle_env["prev_dir"]),
            "--root",
            str(mock_lifecycle_env["fake_root"]),
        ]
    )
    assert rc == 1
    assert (
        f"missing installed kernel image: {mock_lifecycle_env['fake_root']}/boot/vmlinuz-{prev_kr}"
        in (capsys.readouterr().err)
    )
