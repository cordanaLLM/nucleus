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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
  --vmlinuz=<file>        Path to vmlinuz kernel binary [required unless --dry-run]
  --initrd=<file>         Path to initrd / initramfs archive [optional; no .initrd without it]
  --cmdline=<string>      Kernel commandline parameters
  --output-dir=<dir>      Target directory for UKI output [default: output]
  --dry-run               Write a marked simulation to <output-dir>/<stream>-<arch>-dry-run/;
                          never a .efi, never a checksum
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

# ukify picks the systemd-stub by EFI architecture (linux<efi-arch>.efi.stub), and without
# --efi-arch it uses the build host's, which wraps an arm64 kernel in an x64 stub on an x86_64
# runner. Passing it makes a missing stub fail the build with an error naming the stub.
resolve_efi_arch() {
  case "${ARCH}" in
    x86_64)  echo "x64" ;;
    arm64)   echo "aa64" ;;
    riscv64) echo "riscv64" ;;
    *)       return 1 ;;
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

# A dry run writes a marked text file, not an image, so it can exercise the pipeline
# without producing anything shaped like a UKI (issue #21: a stand-in must not use the
# .efi name). It lands in <output-dir>/<stream>-<arch>-dry-run/ as <efi name>.simulated.txt,
# with no checksum, so it can neither be taken for a UKI nor overwrite a real one.
simulate_uki_binary() {
  local version="${1}"
  local efi_name="${2}"
  local target_dir="${3}"
  local staging_dir="${4}"
  local simulated="${efi_name}.simulated.txt"

  echo "==> [SIMULATION] Standing in for UKI ${efi_name} for ${version} (${ARCH})..."
  {
    printf 'SIMULATED, not a Unified Kernel Image: stands in for %s\n' "${efi_name}"
    printf 'Lusoris Unified Kernel Image %s (%s %s)\n' "${version}" "${STREAM}" "${ARCH}"
    cat "${staging_dir}/cmdline"
    echo ""
  } > "${target_dir}/${simulated}"

  # Calculate simulated TPM 2.0 PCR 11 measurement
  local pcr11_digest
  # Hash stdin: with a filename argument, sha256sum prefixes the digest with "\"
  # whenever the path contains a backslash, which corrupts the JSON below.
  pcr11_digest="$(sha256sum < "${target_dir}/${simulated}" | awk '{print $1}')"
  cat <<EOF > "${target_dir}/pcr11-measurements.json"
{
  "stream": "${STREAM}",
  "version": "${version}",
  "arch": "${ARCH}",
  "binary": "${simulated}",
  "simulated": true,
  "pcr11_sha256": "${pcr11_digest}"
}
EOF
  echo "    ✓ Simulation and simulated PCR 11 measurement staged in ${target_dir}/"
}

# Remove what an earlier run left at the target path: the image, its checksum and a PCR 11
# measurement. A production run does this before anything else, so a refused run leaves no
# earlier UKI or checksum behind to be taken for its result, and again if ukify or the
# checker fails.
discard_uki_outputs() {
  local target_dir="${1}"
  local efi_name="${2}"

  rm -f -- "${target_dir}/${efi_name}" "${target_dir}/${efi_name}.sha256" \
    "${target_dir}/pcr11-measurements.json"
}

# Refuse rather than fabricate. Every missing input is a build environment
# defect: no kernel means the forge produced nothing to package, and no ukify
# means the runner is not provisioned. Copying vmlinuz to a .efi name yields a
# file with no stub, no embedded cmdline, no os-release, no SBAT and no
# signature, which firmware cannot boot and which nothing downstream can tell
# from the real artifact by its name. See issue #21.
require_uki_inputs() {
  local efi_name="${1}"

  if [[ ! -f "${VMLINUZ_FILE}" ]]; then
    echo "Error: no kernel image to package: --vmlinuz='${VMLINUZ_FILE}' is not a file." >&2
    echo "       Build the kernel first, or pass --dry-run to simulate the pipeline." >&2
    return 1
  fi

  if [[ -n "${INITRD_FILE}" && ! -f "${INITRD_FILE}" ]]; then
    echo "Error: --initrd='${INITRD_FILE}' is not a file." >&2
    return 1
  fi

  if ! command -v ukify >/dev/null 2>&1; then
    echo "Error: ukify is not installed; a Unified Kernel Image cannot be built without it." >&2
    echo "       Install systemd-ukify on this runner. Refusing to emit a bare kernel named ${efi_name}." >&2
    return 1
  fi
}

# ukify reads --cmdline as literal text unless it starts with "@", so the
# cmdline file is passed as "@<file>"; without the "@" the UKI would boot with
# the staging path as its command line. --initrd is passed only when one was
# given: ukify fails on an empty --initrd=. --config=/dev/null stops ukify from
# reading the first ukify.conf it finds in /etc/systemd, /run/systemd,
# /usr/local/lib/systemd or /usr/lib/systemd, which could add sections or sign
# the image. Whatever ukify writes is then checked by check_uki.py (PE headers,
# machine types, UKI sections, the command line text) before it is checksummed,
# and deleted here if the check refuses it.
build_uki_binary() {
  local version="${1}"
  local efi_name="${2}"
  local target_dir="${3}"
  local staging_dir="${4}"
  local target_efi="${target_dir}/${efi_name}"
  local efi_arch
  efi_arch="$(resolve_efi_arch)"
  local -a ukify_args=(
    --config=/dev/null
    --efi-arch="${efi_arch}"
    --linux="${VMLINUZ_FILE}"
    --cmdline="@${staging_dir}/cmdline"
    --os-release="@${staging_dir}/os-release"
    --sbat="@${staging_dir}/sbat.csv"
    --output="${target_efi}"
  )
  local -a check_args=(--arch="${ARCH}" --expect-cmdline="@${staging_dir}/cmdline")

  discard_uki_outputs "${target_dir}" "${efi_name}"
  require_uki_inputs "${efi_name}"
  if [[ -n "${INITRD_FILE}" ]]; then
    ukify_args+=(--initrd="${INITRD_FILE}")
    check_args+=(--expect-initrd)
  fi

  echo "==> Invoking ukify synthesis engine for ${version} (EFI architecture ${efi_arch})..."
  if ! ukify build "${ukify_args[@]}"; then
    discard_uki_outputs "${target_dir}" "${efi_name}"
    echo "Error: ukify build failed; no UKI was written." >&2
    return 1
  fi

  if ! python3 "${SCRIPT_DIR}/check_uki.py" "${check_args[@]}" "${target_efi}"; then
    discard_uki_outputs "${target_dir}" "${efi_name}"
    echo "Error: ${target_efi} is not a Unified Kernel Image; deleted, not checksummed." >&2
    return 1
  fi

  (cd "${target_dir}" && sha256sum "${efi_name}" > "${efi_name}.sha256")
  echo "    ✓ Successfully synthesized ${target_efi}"
}

# The helpers are called directly, not as `helper || status=$?`: bash disables
# errexit for the whole body of a function invoked in a `||` context, so a
# failing ukify would carry on and checksum whatever it left behind. Cleanup
# of the staging directory is an EXIT trap so it runs on refusal too; printf %q
# quotes its path for the trap, so a quote in TMPDIR cannot break the cleanup.
# A dry run writes to a directory of its own, never next to a real UKI.
synthesize_uki_binary() {
  local version="${1}"
  local efi_name="${2}"
  local target_dir="${OUTPUT_DIR}/${STREAM}-${ARCH}"
  local staging_dir
  if [[ "${DRY_RUN}" == "true" ]]; then
    target_dir="${target_dir}-dry-run"
  fi
  staging_dir="$(mktemp -d --tmpdir "lusoris-uki-${STREAM}-${ARCH}.XXXXXX")"
  # shellcheck disable=SC2064 # expand now: staging_dir is local and out of scope at EXIT
  trap "rm -rf -- $(printf '%q' "${staging_dir}")" EXIT
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
