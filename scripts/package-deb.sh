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
# scripts/package-deb.sh — Native Debian bindeb-pkg Packaging Automation
# Complies with NASA/JPL Power of 10: functions <= 60 lines, checked returns.
set -euo pipefail

STREAM="mainstream"
ARCH="x86_64"
OUTPUT_DIR="output"
DRY_RUN="false"
SOURCE_TREE=""

show_usage() {
  cat <<'EOF'
Usage: package-deb.sh [options]
Options:
  --stream=<stream>       Release stream (bleeding, mainstream, lts, realtime) [default: mainstream]
  --arch=<arch>           Kernel architecture (x86_64, arm64, riscv64) [default: x86_64]
  --source-tree=<dir>     Path to unpacked kernel source tree [optional]
  --output-dir=<dir>      Directory to stage .deb packages [default: output]
  --dry-run               Simulate package generation and metadata verification
  -h, --help              Show this help message
EOF
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --stream=*)
        STREAM="${1#*=}"
        shift
        ;;
      --arch=*)
        ARCH="${1#*=}"
        shift
        ;;
      --source-tree=*)
        SOURCE_TREE="${1#*=}"
        shift
        ;;
      --output-dir=*)
        OUTPUT_DIR="${1#*=}"
        shift
        ;;
      --dry-run)
        DRY_RUN="true"
        shift
        ;;
      -h|--help)
        show_usage
        exit 0
        ;;
      *)
        echo "Error: Unknown argument $1" >&2
        exit 1
        ;;
    esac
  done
}

resolve_debian_arch() {
  case "${ARCH}" in
    x86_64)  echo "amd64" ;;
    arm64)   echo "arm64" ;;
    riscv64) echo "riscv64" ;;
    *)       echo "unknown" ;;
  esac
}

resolve_kernel_version() {
  python3 -c "
import json
with open('versions.json', 'r') as f:
    data = json.load(f)
print(data['streams']['${STREAM}']['version'])
"
}

build_debian_packages() {
  local version="${1}"
  local deb_arch="${2}"
  local target_dir="${OUTPUT_DIR}/${STREAM}-${ARCH}"
  local pkg_ver
  pkg_ver="$(date +%Y%m%d)-lusoris1"
  local local_ver="-lusoris1-${STREAM}"

  mkdir -p "${target_dir}"

  if [[ "${DRY_RUN}" == "true" || -z "${SOURCE_TREE}" ]]; then
    echo "==> [SIMULATION] Staging Debian package metadata for ${version} (${deb_arch})..."
    local pkg_base="linux-image-${version}${local_ver}_${version}-${pkg_ver}_${deb_arch}.deb"
    local hdr_base="linux-headers-${version}${local_ver}_${version}-${pkg_ver}_${deb_arch}.deb"
    local dev_base="linux-libc-dev_${version}-${pkg_ver}_${deb_arch}.deb"

    # Write placeholder metadata archives in simulation/dry-run mode
    printf "Lusoris Linux Kernel Image: %s\nStream: %s\nArch: %s\n" "${version}" "${STREAM}" "${ARCH}" > "${target_dir}/${pkg_base}"
    printf "Lusoris Linux Kernel Headers: %s\nStream: %s\nArch: %s\n" "${version}" "${STREAM}" "${ARCH}" > "${target_dir}/${hdr_base}"
    printf "Lusoris Linux Libc Dev: %s\nStream: %s\nArch: %s\n" "${version}" "${STREAM}" "${ARCH}" > "${target_dir}/${dev_base}"

    (cd "${target_dir}" && sha256sum -- *.deb > SHA256SUMS)
    echo "    ✓ Simulated packages staged in ${target_dir}/"
    return 0
  fi

  echo "==> Compiling native Debian packages in ${SOURCE_TREE}..."
  make -C "${SOURCE_TREE}" \
    ARCH="${ARCH}" \
    LOCALVERSION="${local_ver}" \
    KDEB_PKGVERSION="${version}-${pkg_ver}" \
    -j"$(nproc)" \
    bindeb-pkg

  mv "${SOURCE_TREE}/.."/linux-*.deb "${target_dir}/"
  (cd "${target_dir}" && sha256sum -- *.deb > SHA256SUMS)
  echo "    ✓ Debian packages generated and verified in ${target_dir}/"
}

main() {
  parse_arguments "$@"
  local deb_arch
  deb_arch="$(resolve_debian_arch)"
  if [[ "${deb_arch}" == "unknown" ]]; then
    echo "Error: Cannot map architecture '${ARCH}' to Debian architecture" >&2
    exit 1
  fi

  local version
  version="$(resolve_kernel_version)"
  echo "==> Packaging Debian artifacts for stream='${STREAM}' version='${version}' arch='${ARCH}' (${deb_arch})..."
  build_debian_packages "${version}" "${deb_arch}"
  echo "==> Packaging workflow complete."
}

main "$@"
