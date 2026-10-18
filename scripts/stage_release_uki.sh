#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/stage_release_uki.sh — build and stage a release UKI for x86_64
# Complies with NASA/JPL Power of 10: functions <= 60 lines, checked returns.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STREAM=""
VERSION=""
VMLINUZ=""
OUTPUT_DIR=""
TMP_DIR=""

cleanup() {
  if [[ -n "${TMP_DIR:-}" && -d "${TMP_DIR}" ]]; then
    rm -rf -- "${TMP_DIR}"
  fi
}
trap cleanup EXIT

refuse() {
  echo "Refused: $*" >&2
  exit 1
}

show_usage() {
  cat <<'EOF'
Usage: stage_release_uki.sh --stream=<s> --version=<v> --vmlinuz=<file> --output-dir=<dir>

Options:
  --stream=<s>        Release stream name (e.g. mainstream, bleeding, lts, realtime)
  --version=<v>       Package version <version>-lusoris<N>
  --vmlinuz=<file>    Path to kernel image binary
  --output-dir=<dir>  Target directory to copy the release UKI into
  -h, --help          Show this help message
EOF
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --stream=*)
        STREAM="${1#*=}"
        shift
        ;;
      --version=*)
        VERSION="${1#*=}"
        shift
        ;;
      --vmlinuz=*)
        VMLINUZ="${1#*=}"
        shift
        ;;
      --output-dir=*)
        OUTPUT_DIR="${1#*=}"
        shift
        ;;
      -h | --help)
        show_usage
        exit 0
        ;;
      *)
        refuse "Unknown argument: $1"
        ;;
    esac
  done
}

validate_arguments() {
  [[ -n "${STREAM}" ]] || refuse "--stream is required"
  [[ -n "${VERSION}" ]] || refuse "--version is required"
  [[ -n "${VMLINUZ}" ]] || refuse "--vmlinuz is required"
  [[ -n "${OUTPUT_DIR}" ]] || refuse "--output-dir is required"

  if [[ ! -f "${VMLINUZ}" ]]; then
    refuse "--vmlinuz='${VMLINUZ}' is not a regular file"
  fi
}

build_and_stage_uki() {
  local version_clean="${VERSION//\~/.}"
  local target_file="${OUTPUT_DIR}/linux-${STREAM}-${version_clean}-uki.efi"

  if [[ -e "${target_file}" ]]; then
    refuse "target UKI already exists: ${target_file}"
  fi

  TMP_DIR="$(mktemp -d --tmpdir "lusoris-stage-uki.XXXXXX")"
  local initramfs="${TMP_DIR}/initramfs.cpio"
  local uki_out="${TMP_DIR}/uki"
  local synthesized_efi="${uki_out}/${STREAM}-x86_64/BOOTX64.EFI"

  echo "==> Generating static-init initramfs for release UKI..."
  python3 "${SCRIPT_DIR}/boot_smoke.py" --arch=x86_64 --write-initramfs="${initramfs}"

  echo "==> Synthesizing x86_64 UKI via package-uki.sh..."
  "${SCRIPT_DIR}/package-uki.sh" --stream="${STREAM}" --arch=x86_64 \
    --vmlinuz="${VMLINUZ}" --initrd="${initramfs}" --output-dir="${uki_out}"

  if [[ ! -f "${synthesized_efi}" || ! -s "${synthesized_efi}" ]]; then
    refuse "BOOTX64.EFI is missing or empty at ${synthesized_efi}"
  fi

  echo "==> Verifying synthesized UKI with check_uki.py..."
  python3 "${SCRIPT_DIR}/check_uki.py" --arch=x86_64 --expect-initrd "${synthesized_efi}"

  if [[ -e "${target_file}" ]]; then
    refuse "target UKI already exists: ${target_file}"
  fi

  mkdir -p "${OUTPUT_DIR}"
  cp -- "${synthesized_efi}" "${target_file}"
  echo "    ✓ Successfully staged release UKI: ${target_file}"
}

main() {
  parse_arguments "$@"
  validate_arguments
  build_and_stage_uki
}

main "$@"
