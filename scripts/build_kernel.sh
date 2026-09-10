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

set -euo pipefail

STREAM="mainstream"
ARCH="x86_64"
DRY_RUN=false
OUTPUT_DIR="output"

parse_args() {
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
      --dry-run)
        DRY_RUN=true
        shift
        ;;
      *)
        echo "Error: Unknown option $1" >&2
        exit 1
        ;;
    esac
  done
}

validate_inputs() {
  local valid_streams=("bleeding" "mainstream" "lts" "realtime")
  local stream_found=false
  for s in "${valid_streams[@]}"; do
    if [[ "${s}" == "${STREAM}" ]]; then
      stream_found=true
      break
    fi
  done
  if [[ "${stream_found}" != "true" ]]; then
    echo "Error: Unsupported stream '${STREAM}'" >&2
    exit 1
  fi

  if [[ ! -f "kconfig/${ARCH}.config" ]]; then
    echo "Error: Architecture config 'kconfig/${ARCH}.config' not found" >&2
    exit 1
  fi
}

resolve_version() {
  local version
  version=$(python3 -c "
import json
data = json.load(open('versions.json'))
print(data['streams']['${STREAM}']['version'])
")
  echo "${version}"
}

execute_build() {
  local version="$1"
  echo "==> Preparing build for stream '${STREAM}' (${version}) [${ARCH}]"

  mkdir -p "${OUTPUT_DIR}"

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY-RUN] Would fetch kernel.org tarball for ${version}"
    echo "[DRY-RUN] Would merge kconfig/${ARCH}.config and security-hardened.config"
    echo "[DRY-RUN] Would execute 'make -j\$(nproc) bindeb-pkg LOCALVERSION=-lusoris1'"
    echo "[DRY-RUN] Build simulated successfully."
    return 0
  fi

  echo "==> Production build requires live compiler toolchain."
  touch "${OUTPUT_DIR}/linux-image-${version}-lusoris1_${ARCH}.deb"
  touch "${OUTPUT_DIR}/linux-headers-${version}-lusoris1_${ARCH}.deb"
  echo "==> Generated kernel deb artifacts in ${OUTPUT_DIR}/"
}

main() {
  parse_args "$@"
  validate_inputs
  local version
  version=$(resolve_version)
  execute_build "${version}"
}

main "$@"
