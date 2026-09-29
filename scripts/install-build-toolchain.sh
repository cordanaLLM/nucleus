#!/usr/bin/env bash
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
#   --with-qemu   also install qemu-system-x86 for scripts/boot_smoke.py
set -euo pipefail

readonly PACKAGES=(
  build-essential gcc-aarch64-linux-gnu gcc-riscv64-linux-gnu bc bison flex dwarves
  libelf-dev libdw-dev libssl-dev dpkg-dev debhelper kmod cpio zstd rsync ccache
  python3 git ca-certificates curl gpg gpgv xz-utils gnu-coreutils
)

main() {
  local -a packages=("${PACKAGES[@]}")
  case "${1:-}" in
    "") ;;
    --with-qemu) packages+=(qemu-system-x86) ;;
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
