#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Verify installation, coexistence, and uninstallation of kernel Debian packages.

Exercises the user entrypoint documented in docs/install.md section 4:
    sudo dpkg -i linux-image-*.deb linux-headers-*.deb

Verifies:
a. Initial install of previous release (if provided).
b. Install of target release.
c. Installed release state: dpkg-query status, byte identity of vmlinuz and config,
   presence of System.map, modules.dep, linux-headers directory, and initrd.img.
d. Side-by-side coexistence of previous and target release files.
e. Purge of previous release without clobbering target release.
f. Purge of target release leaving no residue in /boot, /lib/modules, or /usr/src.

Must be executed with euid 0 in a disposable container with NUCLEUS_DISPOSABLE_ROOT=1.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


def check_environment(
    euid: int | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Verify execution runs as root inside a disposable container."""
    current_euid = os.geteuid() if euid is None else euid
    current_env = os.environ if env is None else env
    if current_euid != 0:
        raise PermissionError(
            "package_lifecycle.py installs real kernels as root and must run in a throwaway container (euid != 0)"
        )
    if current_env.get("NUCLEUS_DISPOSABLE_ROOT") != "1":
        raise PermissionError(
            "package_lifecycle.py installs real kernels and must run in a throwaway container; "
            "NUCLEUS_DISPOSABLE_ROOT=1 is required"
        )


def derive_kernelrelease(release_dir: Path) -> str:
    """Derive the kernel release string from the single vmlinuz-* asset."""
    if not release_dir.is_dir():
        raise FileNotFoundError(f"release directory does not exist: {release_dir}")
    vmlinuz_assets = sorted(
        p for p in release_dir.iterdir() if p.is_file() and p.name.startswith("vmlinuz-")
    )
    if not vmlinuz_assets:
        raise ValueError(f"expected single vmlinuz-* asset in {release_dir}, found 0")
    if len(vmlinuz_assets) > 1:
        names = ", ".join(p.name for p in vmlinuz_assets)
        raise ValueError(
            f"expected single vmlinuz-* asset in {release_dir}, found {len(vmlinuz_assets)}: {names}"
        )
    kr = vmlinuz_assets[0].name[len("vmlinuz-") :]
    if not kr:
        raise ValueError(f"empty kernelrelease in {vmlinuz_assets[0].name}")
    return kr


def find_package_debs(release_dir: Path, kr: str) -> tuple[Path, Path]:
    """Locate the image and headers deb packages for kernelrelease kr."""
    image_debs = sorted(
        p
        for p in release_dir.iterdir()
        if p.is_file() and p.name.startswith(f"linux-image-{kr}_") and p.name.endswith(".deb")
    )
    headers_debs = sorted(
        p
        for p in release_dir.iterdir()
        if p.is_file() and p.name.startswith(f"linux-headers-{kr}_") and p.name.endswith(".deb")
    )
    if len(image_debs) != 1:
        raise FileNotFoundError(
            f"expected 1 linux-image deb for {kr} in {release_dir}, found {len(image_debs)}"
        )
    if len(headers_debs) != 1:
        raise FileNotFoundError(
            f"expected 1 linux-headers deb for {kr} in {release_dir}, found {len(headers_debs)}"
        )
    return image_debs[0], headers_debs[0]


def _run_cmd(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run an external command and return CompletedProcess, raising on failure."""
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        err_msg = res.stderr.strip() or res.stdout.strip()
        raise RuntimeError(f"command failed ({res.returncode}): {' '.join(cmd)}\n{err_msg}")
    return res


def verify_dpkg_status(pkg: str) -> None:
    """Verify that dpkg-query reports 'install ok installed' for package."""
    cmd = ["dpkg-query", "-W", "-f=${Status}", pkg]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"dpkg-query failed for {pkg} (exit {res.returncode}): {res.stderr.strip()}"
        )
    status = res.stdout.strip()
    if status != "install ok installed":
        raise ValueError(f"package {pkg} status is '{status}', expected 'install ok installed'")


def verify_installed_release(
    kr: str,
    release_dir: Path,
    stream: str,
    root: Path = Path("/"),
) -> None:
    """Verify package status and installed files for kernelrelease kr."""
    image_pkg = f"linux-image-{kr}"
    headers_pkg = f"linux-headers-{kr}"
    verify_dpkg_status(image_pkg)
    verify_dpkg_status(headers_pkg)

    boot_dir = root / "boot"
    boot_vmlinuz = boot_dir / f"vmlinuz-{kr}"
    asset_vmlinuz = release_dir / f"vmlinuz-{kr}"
    if not boot_vmlinuz.is_file():
        raise FileNotFoundError(f"missing installed kernel image: {boot_vmlinuz}")
    if not asset_vmlinuz.is_file():
        raise FileNotFoundError(f"missing release asset: {asset_vmlinuz}")
    if boot_vmlinuz.read_bytes() != asset_vmlinuz.read_bytes():
        raise ValueError(f"{boot_vmlinuz} is not byte-identical to {asset_vmlinuz}")

    boot_config = boot_dir / f"config-{kr}"
    asset_config = release_dir / f"kernel-{stream}-x86_64.config"
    if not boot_config.is_file():
        raise FileNotFoundError(f"missing installed kernel config: {boot_config}")
    if not asset_config.is_file():
        raise FileNotFoundError(f"missing release config asset: {asset_config}")
    if boot_config.read_bytes() != asset_config.read_bytes():
        raise ValueError(f"{boot_config} is not byte-identical to {asset_config}")

    system_map = boot_dir / f"System.map-{kr}"
    if not system_map.is_file():
        raise FileNotFoundError(f"missing System.map file: {system_map}")

    modules_dep = root / "lib" / "modules" / kr / "modules.dep"
    if not modules_dep.is_file():
        raise FileNotFoundError(f"missing modules.dep file: {modules_dep}")

    usr_headers = root / "usr" / "src" / f"linux-headers-{kr}"
    if not usr_headers.exists():
        raise FileNotFoundError(f"missing headers directory: {usr_headers}")

    initrd = boot_dir / f"initrd.img-{kr}"
    if not initrd.is_file():
        raise FileNotFoundError(f"missing initramfs image: {initrd}")


def verify_purged_release(kr: str, root: Path = Path("/")) -> None:
    """Verify that all files and directories for kernelrelease kr are removed."""
    boot_dir = root / "boot"
    if boot_dir.is_dir():
        leftover_boot = [p.name for p in boot_dir.iterdir() if kr in p.name]
        if leftover_boot:
            files = ", ".join(leftover_boot)
            raise RuntimeError(f"files for {kr} left in {boot_dir} after purge: {files}")

    modules_dir = root / "lib" / "modules" / kr
    if modules_dir.exists():
        raise RuntimeError(f"modules directory left after purge: {modules_dir}")

    headers_dir = root / "usr" / "src" / f"linux-headers-{kr}"
    if headers_dir.exists():
        raise RuntimeError(f"headers directory left after purge: {headers_dir}")


def install_release(release_dir: Path, kr: str) -> None:
    """Run dpkg -i on image and headers packages for kernelrelease kr."""
    image_deb, headers_deb = find_package_debs(release_dir, kr)
    _run_cmd(["dpkg", "-i", str(image_deb), str(headers_deb)])


def purge_release(kr: str) -> None:
    """Run dpkg --purge on image and headers packages for kernelrelease kr."""
    image_pkg = f"linux-image-{kr}"
    headers_pkg = f"linux-headers-{kr}"
    _run_cmd(["dpkg", "--purge", image_pkg, headers_pkg])


def run_lifecycle(
    stream: str,
    release_dir: Path,
    previous_dir: Path | None = None,
    root: Path = Path("/"),
) -> None:
    """Execute the full package install, upgrade/coexistence, and purge lifecycle."""
    kr = derive_kernelrelease(release_dir)
    prev_kr: str | None = None

    if previous_dir is not None:
        prev_kr = derive_kernelrelease(previous_dir)
        print(f"STEP a: Installing previous release {prev_kr} from {previous_dir}")
        install_release(previous_dir, prev_kr)
        verify_installed_release(prev_kr, previous_dir, stream, root)
        print(f"OK: previous release {prev_kr} installed and verified")

    print(f"STEP b: Installing target release {kr} from {release_dir}")
    install_release(release_dir, kr)

    print(f"STEP c: Verifying target release {kr} installed state")
    verify_installed_release(kr, release_dir, stream, root)
    print(f"OK: target release {kr} installed and verified")

    if previous_dir is not None and prev_kr is not None:
        print(f"STEP d: Verifying previous release {prev_kr} remains intact alongside {kr}")
        verify_installed_release(prev_kr, previous_dir, stream, root)
        print(f"OK: previous release {prev_kr} remains intact alongside {kr}")

        print(f"STEP e: Purging previous release {prev_kr}")
        purge_release(prev_kr)
        verify_purged_release(prev_kr, root)
        print(f"OK: previous release {prev_kr} purged cleanly")

        print(f"STEP e (cont): Verifying target release {kr} still intact after previous purge")
        verify_installed_release(kr, release_dir, stream, root)
        print(f"OK: target release {kr} intact after previous purge")

    print(f"STEP f: Purging target release {kr}")
    purge_release(kr)
    verify_purged_release(kr, root)
    print(f"OK: target release {kr} purged cleanly")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify installation, coexistence, and uninstallation lifecycle of kernel deb packages."
    )
    parser.add_argument("--stream", required=True, help="kernel stream name (e.g. mainstream)")
    parser.add_argument(
        "--release",
        required=True,
        type=Path,
        help="directory containing target release assets",
    )
    parser.add_argument(
        "--previous",
        type=Path,
        default=None,
        help="directory containing previous release assets",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/"),
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        check_environment()
        run_lifecycle(
            stream=args.stream,
            release_dir=args.release,
            previous_dir=args.previous,
            root=args.root,
        )
    except Exception as exc:
        print(f"package_lifecycle: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
