#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/install-build-toolchain.sh — install the Ubuntu 26.04 packages a kernel build needs.
#
# The one list build-matrix.yml and publish-release.yml install, so a release compiles with the
# toolchain the matrix proved. Run as root in an ubuntu:26.04 container. Both cross compilers
# are installed on every host: the kernel is always built with the triplet-named compiler from
# versions.json (cross_compile), which on the native architecture is the native gcc.
# gnu-coreutils provides gnuinstall, the GNU install scripts/build_kernel.sh builds with, because
# the uutils install Ubuntu 26.04 ships races in the kernel's parallel device-tree install.
#
# Usage: install-build-toolchain.sh [--with-qemu]
#   --with-qemu   also install QEMU for $ARCH (default x86_64) for scripts/boot_smoke.py
set -euo pipefail

readonly PACKAGES=(
  build-essential gcc-aarch64-linux-gnu gcc-riscv64-linux-gnu bc bison flex dwarves
  libelf-dev libdw-dev libssl-dev dpkg-dev debhelper kmod cpio zstd rsync ccache patch
  python3 git ca-certificates curl gpg gpgv xz-utils gnu-coreutils openssl
)

main() {
  local -a packages=("${PACKAGES[@]}")
  case "${1:-}" in
    "") ;;
    --with-qemu)
      local arch="${ARCH:-x86_64}"
      case "${arch}" in
        x86_64) packages+=(qemu-system-x86) ;;
        arm64) packages+=(qemu-system-arm) ;;
        riscv64) packages+=(qemu-system-riscv opensbi) ;;
        *)
          echo "Refused: unknown architecture ${arch} for --with-qemu" >&2
          exit 1
          ;;
      esac
      ;;
    *)
      echo "Refused: unknown argument $1 (usage: $0 [--with-qemu])" >&2
      exit 1
      ;;
  esac
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends "${packages[@]}"
}

main "$@"
