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
# scripts/package-uki.sh — Unified Kernel Image (UKI) PE Synthesis Automation
# Complies with NASA/JPL Power of 10: functions <= 60 lines, checked returns.
set -euo pipefail

STREAM="mainstream"
ARCH="x86_64"
VMLINUZ_FILE=""
INITRD_FILE=""
CMDLINE_ARG="console=ttyS0 console=tty0 root=LABEL=lusoris-root ro quiet loglevel=4"
OUTPUT_DIR="output"
DRY_RUN="false"

show_usage() {
  cat <<'EOF'
Usage: package-uki.sh [options]
Options:
  --stream=<stream>       Release stream (bleeding, mainstream, lts, realtime) [default: mainstream]
  --arch=<arch>           Kernel architecture (x86_64, arm64, riscv64) [default: x86_64]
  --vmlinuz=<file>        Path to vmlinuz kernel binary [optional]
  --initrd=<file>         Path to initrd / initramfs archive [optional]
  --cmdline=<string>      Kernel commandline parameters
  --output-dir=<dir>      Target directory for UKI output [default: output]
  --dry-run               Simulate UKI synthesis and PCR measurements
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
      --vmlinuz=*)
        VMLINUZ_FILE="${1#*=}"
        shift
        ;;
      --initrd=*)
        INITRD_FILE="${1#*=}"
        shift
        ;;
      --cmdline=*)
        CMDLINE_ARG="${1#*=}"
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

resolve_efi_name() {
  case "${ARCH}" in
    x86_64)  echo "BOOTX64.EFI" ;;
    arm64)   echo "BOOTAA64.EFI" ;;
    riscv64) echo "BOOTRISCV64.EFI" ;;
    *)       echo "BOOTUNKNOWN.EFI" ;;
  esac
}

generate_uki_metadata() {
  local staging_dir="${1}"
  local version="${2}"

  echo "==> Preparing UKI sections (os-release, sbat, cmdline)..."
  mkdir -p "${staging_dir}"

  cat <<EOF > "${staging_dir}/os-release"
NAME="Lusoris Linux"
ID=lusoris
VERSION="${version}"
VERSION_ID="${version}"
PRETTY_NAME="Lusoris Linux Kernel ${version} (${STREAM})"
HOME_URL="https://github.com/cordanaLLM/nucleus"
SUPPORT_URL="https://github.com/cordanaLLM/nucleus/discussions"
BUG_REPORT_URL="https://github.com/cordanaLLM/nucleus/issues"
EOF

  cat <<'EOF' > "${staging_dir}/sbat.csv"
sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md
lusoris,1,Lusoris Linux,lusoris,1,https://github.com/cordanaLLM/nucleus
EOF

  echo -n "${CMDLINE_ARG}" > "${staging_dir}/cmdline"
}

# A simulated UKI is a text file with an MZ prefix, not a Unified Kernel Image.
# It exists so --dry-run can exercise the pipeline, and it is now only reachable
# when --dry-run was asked for explicitly. A production run must never land here:
# the artifact is checksummed and covered by the release cosign signature, so a
# simulated one would arrive at a consumer as a signed, verified UKI that is a stub.
simulate_uki_binary() {
  local version="${1}"
  local efi_name="${2}"
  local target_dir="${3}"
  local staging_dir="${4}"
  local target_efi="${target_dir}/${efi_name}"

  echo "==> [SIMULATION] Synthesizing UKI ${efi_name} for ${version} (${ARCH})..."
  printf "MZ\x90\x00Lusoris Unified Kernel Image %s (%s %s)\n" "${version}" "${STREAM}" "${ARCH}" > "${target_efi}"
  cat "${staging_dir}/cmdline" >> "${target_efi}"
  echo "" >> "${target_efi}"

  # Calculate simulated TPM 2.0 PCR 11 measurement
  local pcr11_digest
  # Hash stdin: with a filename argument, sha256sum prefixes the digest with "\"
  # whenever the path contains a backslash, which corrupts the JSON below.
  pcr11_digest="$(sha256sum < "${target_efi}" | awk '{print $1}')"
  cat <<EOF > "${target_dir}/pcr11-measurements.json"
{
  "stream": "${STREAM}",
  "version": "${version}",
  "arch": "${ARCH}",
  "binary": "${efi_name}",
  "simulated": true,
  "pcr11_sha256": "${pcr11_digest}"
}
EOF
  (cd "${target_dir}" && sha256sum "${efi_name}" > "${efi_name}.sha256")
  echo "    ✓ Simulated UKI and PCR 11 measurement staged in ${target_dir}/"
}

# Refuse rather than fabricate. Both missing inputs are build environment
# defects: no kernel means the forge produced nothing to package, and no ukify
# means the runner is not provisioned. Copying vmlinuz to a .efi name yields a
# file with no stub, no embedded cmdline, no os-release, no SBAT and no
# signature, which firmware cannot boot and which nothing downstream can tell
# from the real artifact by its name. See issue #21.
build_uki_binary() {
  local version="${1}"
  local efi_name="${2}"
  local target_dir="${3}"
  local staging_dir="${4}"
  local target_efi="${target_dir}/${efi_name}"

  if [[ ! -f "${VMLINUZ_FILE}" ]]; then
    echo "Error: no kernel image to package: --vmlinuz='${VMLINUZ_FILE}' is not a file." >&2
    echo "       Build the kernel first, or pass --dry-run to simulate the pipeline." >&2
    return 1
  fi

  if ! command -v ukify >/dev/null 2>&1; then
    echo "Error: ukify is not installed; a Unified Kernel Image cannot be built without it." >&2
    echo "       Install systemd-ukify on this runner. Refusing to emit a bare kernel named ${efi_name}." >&2
    return 1
  fi

  echo "==> Invoking ukify synthesis engine for ${version}..."
  if ! ukify build \
    --linux="${VMLINUZ_FILE}" \
    --initrd="${INITRD_FILE}" \
    --cmdline="${staging_dir}/cmdline" \
    --os-release="@${staging_dir}/os-release" \
    --sbat="@${staging_dir}/sbat.csv" \
    --output="${target_efi}"; then
    rm -f "${target_efi}"
    echo "Error: ukify build failed; no UKI was written." >&2
    return 1
  fi

  if [[ ! -s "${target_efi}" ]]; then
    rm -f "${target_efi}"
    echo "Error: ukify reported success but produced no output at ${target_efi}." >&2
    return 1
  fi

  (cd "${target_dir}" && sha256sum "${efi_name}" > "${efi_name}.sha256")
  echo "    ✓ Successfully synthesized ${target_efi}"
}

# The helpers are called directly, not as `helper || status=$?`: bash disables
# errexit for the whole body of a function invoked in a `||` context, so a
# failing ukify would carry on and checksum whatever it left behind. Cleanup
# of the staging directory is an EXIT trap so it runs on refusal too.
synthesize_uki_binary() {
  local version="${1}"
  local efi_name="${2}"
  local target_dir="${OUTPUT_DIR}/${STREAM}-${ARCH}"
  local staging_dir
  staging_dir="$(mktemp -d "${TMPDIR:-/tmp}/lusoris-uki-${STREAM}-${ARCH}.XXXXXX")"
  # shellcheck disable=SC2064 # expand now: staging_dir is local and out of scope at EXIT
  trap "rm -rf '${staging_dir}'" EXIT
  mkdir -p "${target_dir}"

  generate_uki_metadata "${staging_dir}" "${version}"

  if [[ "${DRY_RUN}" == "true" ]]; then
    simulate_uki_binary "${version}" "${efi_name}" "${target_dir}" "${staging_dir}"
  else
    build_uki_binary "${version}" "${efi_name}" "${target_dir}" "${staging_dir}"
  fi
}

main() {
  parse_arguments "$@"
  local efi_name
  efi_name="$(resolve_efi_name)"
  if [[ "${efi_name}" == "BOOTUNKNOWN.EFI" ]]; then
    echo "Error: Unknown EFI architecture for ${ARCH}" >&2
    exit 1
  fi

  local version
  version=$(python3 -c "
import json
with open('versions.json', 'r') as f:
    data = json.load(f)
print(data['streams']['${STREAM}']['version'])
")

  echo "==> Synthesizing UKI for stream='${STREAM}' version='${version}' arch='${ARCH}'..."
  synthesize_uki_binary "${version}" "${efi_name}"
  echo "==> UKI synthesis workflow complete."
}

main "$@"
