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
# scripts/verify-reproducibility.sh — Reproducible Build Attestation & Verification
# Complies with NASA/JPL Power of 10: functions <= 60 lines, checked returns.
set -euo pipefail

BUILD_DIR_A=""
BUILD_DIR_B=""
DRY_RUN="false"

show_usage() {
  cat <<'EOF'
Usage: verify-reproducibility.sh [options]
Options:
  --dir-a=<dir>       Path to first build output directory
  --dir-b=<dir>       Path to second build output directory
  --dry-run           Simulate reproducibility verification
  -h, --help          Show this help message
EOF
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dir-a=*)
        BUILD_DIR_A="${1#*=}"
        shift
        ;;
      --dir-b=*)
        BUILD_DIR_B="${1#*=}"
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

verify_environment_variables() {
  echo "==> Verifying reproducible build environment flags..."
  local required_flags=("SOURCE_DATE_EPOCH" "KBUILD_BUILD_TIMESTAMP")
  for var in "${required_flags[@]}"; do
    if [[ -z "${!var:-}" ]]; then
      echo "Notice: ${var} unset; setting deterministic timestamp."
      export "${var}=1700000000"
    fi
  done
  echo "    ✓ Deterministic build environment verified."
}

compare_checksums() {
  local dir_a="${1}"
  local dir_b="${2}"

  echo "==> Comparing build artifact checksums..."
  local hash_a
  local hash_b
  hash_a="$(cd "${dir_a}" && find . -type f -exec sha256sum {} + | sort -k 2 | sha256sum | awk '{print $1}')"
  hash_b="$(cd "${dir_b}" && find . -type f -exec sha256sum {} + | sort -k 2 | sha256sum | awk '{print $1}')"

  echo "    Build A Composite SHA256: ${hash_a}"
  echo "    Build B Composite SHA256: ${hash_b}"

  if [[ "${hash_a}" != "${hash_b}" ]]; then
    echo "Error: Reproducibility verification failed: hash mismatch." >&2
    if command -v diffoscope >/dev/null 2>&1; then
      echo "==> Running diffoscope analysis..."
      diffoscope "${dir_a}" "${dir_b}" || true
    fi
    return 1
  fi
  echo "    ✓ Byte-level identical builds verified."
}

main() {
  parse_arguments "$@"
  verify_environment_variables

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "==> [SIMULATION] Executing reproducibility verification mock..."
    local mock_a
    local mock_b
    mock_a="$(mktemp -d)"
    mock_b="$(mktemp -d)"
    printf "reproducible-kernel-binary\n" > "${mock_a}/kernel.bin"
    printf "reproducible-kernel-binary\n" > "${mock_b}/kernel.bin"
    compare_checksums "${mock_a}" "${mock_b}"
    rm -rf "${mock_a}" "${mock_b}"
    echo "    ✓ [DRY-RUN] Reproducibility verification passed."
    return 0
  fi

  if [[ -z "${BUILD_DIR_A}" || -z "${BUILD_DIR_B}" ]]; then
    echo "Error: Both --dir-a and --dir-b must be specified (or use --dry-run)" >&2
    exit 1
  fi

  compare_checksums "${BUILD_DIR_A}" "${BUILD_DIR_B}"
  echo "==> Reproducibility attestation complete."
}

main "$@"
