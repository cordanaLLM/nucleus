#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/install-build-toolchain.sh — install the Ubuntu 26.04 packages a kernel build needs.
#
# The one list build-matrix.yml and publish-release.yml install, so a release compiles with the
# toolchain the matrix proved. Run as root in an ubuntu:26.04 container.
# Cross compilers are installed only for the target architecture specified in ARCH (or both with
# --all-arches or when ARCH is unset) to avoid stalling package downloads on unused cross toolchains.
# Robust apt timeouts and retries are passed to tolerate transient archive.ubuntu.com mirror latency.
# An optional --archive-dir caches downloaded .deb packages without being purged by docker-clean.
# gnu-coreutils provides gnuinstall, the GNU install scripts/build_kernel.sh builds with, because
# the uutils install Ubuntu 26.04 ships races in the kernel's parallel device-tree install.
#
# Usage: install-build-toolchain.sh [--with-qemu] [--all-arches] [--archive-dir=<dir>]
#   --with-qemu          also install QEMU for $ARCH (default x86_64) for scripts/boot_smoke.py, and on
#                        x86_64 install clang, llvm, zlib1g-dev and libzstd-dev to build the sched_ext
#                        BPF scheduler (scripts/build_sched_ext_scheduler.sh)
#   --all-arches         install both cross compilers regardless of $ARCH (backward compatible)
#   --archive-dir=<dir>  cache downloaded packages in <dir> (preserves debs from docker-clean)
set -euo pipefail

refuse() {
  echo "Refused: $*" >&2
  exit 1
}

readonly PACKAGES=(
  build-essential bc bison flex dwarves
  libelf-dev libdw-dev libssl-dev dpkg-dev debhelper kmod cpio zstd rsync ccache patch
  python3 git ca-certificates curl gpg gpgv xz-utils gnu-coreutils openssl
)

validate_arch() {
  local arch="$1"
  if [[ -n "${arch}" ]]; then
    case "${arch}" in
      x86_64 | arm64 | riscv64) ;;
      *)
        refuse "unknown architecture ${arch}"
        ;;
    esac
  fi
}

resolve_cross_packages() {
  local target_arch="$1"
  local all_arches="$2"
  if [[ "${all_arches}" == "true" || -z "${target_arch}" ]]; then
    echo "gcc-aarch64-linux-gnu gcc-riscv64-linux-gnu"
  elif [[ "${target_arch}" == "arm64" ]]; then
    echo "gcc-aarch64-linux-gnu"
  elif [[ "${target_arch}" == "riscv64" ]]; then
    echo "gcc-riscv64-linux-gnu"
  fi
}

resolve_qemu_packages() {
  local qemu_arch="$1"
  case "${qemu_arch}" in
    x86_64) echo "qemu-system-x86 clang llvm zlib1g-dev libzstd-dev" ;;
    arm64) echo "qemu-system-arm" ;;
    riscv64) echo "qemu-system-riscv opensbi" ;;
    *)
      refuse "unknown architecture ${qemu_arch} for --with-qemu"
      ;;
  esac
}

WITH_QEMU=false
ALL_ARCHES=false
ARCHIVE_DIR=""

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --with-qemu) WITH_QEMU=true ;;
      --all-arches) ALL_ARCHES=true ;;
      --archive-dir=*) ARCHIVE_DIR="${1#*=}" ;;
      --archive-dir)
        [[ $# -ge 2 ]] || refuse "missing argument for --archive-dir"
        ARCHIVE_DIR="$2"
        shift
        ;;
      *)
        refuse "unknown argument $1 (usage: $0 [--with-qemu] [--all-arches] [--archive-dir=<dir>])"
        ;;
    esac
    shift
  done
}

main() {
  parse_args "$@"
  local target_arch="${ARCH:-}"
  validate_arch "${target_arch}"

  local -a cross_pkgs=()
  local cross_str
  cross_str="$(resolve_cross_packages "${target_arch}" "${ALL_ARCHES}")"
  if [[ -n "${cross_str}" ]]; then
    read -r -a cross_pkgs <<<"${cross_str}"
  fi

  local -a qemu_pkgs=()
  if [[ "${WITH_QEMU}" == "true" ]]; then
    local qemu_str
    qemu_str="$(resolve_qemu_packages "${target_arch:-x86_64}")"
    read -r -a qemu_pkgs <<<"${qemu_str}"
  fi

  local -a apt_opts=(
    -o "Acquire::Retries=5"
    -o "Acquire::http::Timeout=30"
    -o "Acquire::https::Timeout=30"
  )
  if [[ -n "${ARCHIVE_DIR}" ]]; then
    mkdir -p "${ARCHIVE_DIR}/partial"
    apt_opts+=(
      -o "Dir::Cache::Archives=${ARCHIVE_DIR}"
      -o "APT::Keep-Downloaded-Packages=true"
    )
  fi

  local -a packages=("${PACKAGES[@]}" "${cross_pkgs[@]}" "${qemu_pkgs[@]}")
  export DEBIAN_FRONTEND=noninteractive
  apt-get update "${apt_opts[@]}"
  apt-get install -y --no-install-recommends "${apt_opts[@]}" "${packages[@]}"
}

main "$@"
