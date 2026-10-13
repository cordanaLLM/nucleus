#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/check-openzfs-module.sh — build and verify the OpenZFS out-of-tree kernel
# module against a verified kernel headers Debian package.
#
# Reads module source, version, cryptographic pins and supported Linux kernel range
# from versions.json (modules.openzfs). Verifies tarball sha256 and GPG detached
# signature against keys/<FINGERPRINT>.asc. Configures and builds the module under
# the extracted headers tree, then verifies vermagic matches the target kernel release.
set -euo pipefail

HEADERS_DEB=""
WORK=""
JOBS=4
VERSIONS="versions.json"
KEYS_DIR="keys"
PRINT_PLAN="false"

ZFS_VERSION=""
ZFS_URL=""
ZFS_SIG_URL=""
ZFS_SHA256=""
LINUX_MINIMUM=""
LINUX_MAXIMUM=""
declare -a SIGNERS=()
KR=""
HEADERS_TREE=""

show_usage() {
  cat <<'EOF'
Usage: check-openzfs-module.sh --headers-deb=<path> --work=<empty-dir> [options]
  --headers-deb=<path>  linux-headers-<KR>_*.deb package to build against
  --work=<dir>          scratch directory for downloads and build (must be empty)
  --jobs=<N>            parallel compilation jobs [default: 4]
  --versions=<path>     manifest declaring modules.openzfs [default: versions.json]
  --keys=<dir>          armored signer keys directory [default: keys]
  --print-plan          print plan (url, sha, KR, range) and exit 0 without writing
EOF
}

refuse() {
  echo "Refused: $*" >&2
  exit 1
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --headers-deb=*) HEADERS_DEB="${1#*=}" ;;
      --headers-deb)
        [[ $# -ge 2 ]] || refuse "--headers-deb requires a path argument"
        HEADERS_DEB="$2"
        shift
        ;;
      --work=*) WORK="${1#*=}" ;;
      --work)
        [[ $# -ge 2 ]] || refuse "--work requires a directory argument"
        WORK="$2"
        shift
        ;;
      --jobs=*) JOBS="${1#*=}" ;;
      --jobs)
        [[ $# -ge 2 ]] || refuse "--jobs requires a number argument"
        JOBS="$2"
        shift
        ;;
      --versions=*) VERSIONS="${1#*=}" ;;
      --versions)
        [[ $# -ge 2 ]] || refuse "--versions requires a file argument"
        VERSIONS="$2"
        shift
        ;;
      --keys=*) KEYS_DIR="${1#*=}" ;;
      --keys)
        [[ $# -ge 2 ]] || refuse "--keys requires a directory argument"
        KEYS_DIR="$2"
        shift
        ;;
      --print-plan) PRINT_PLAN="true" ;;
      -h | --help)
        show_usage
        exit 0
        ;;
      *)
        show_usage >&2
        refuse "unknown option: $1"
        ;;
    esac
    shift
  done
}

validate_arguments() {
  [[ -n "${HEADERS_DEB}" ]] || refuse "--headers-deb is required"
  [[ -f "${HEADERS_DEB}" ]] || refuse "--headers-deb '${HEADERS_DEB}' is not a regular file"
  if [[ "${PRINT_PLAN}" == "false" ]]; then
    [[ -n "${WORK}" ]] || refuse "--work is required"
    if [[ -e "${WORK}" ]]; then
      [[ -d "${WORK}" ]] || refuse "work: ${WORK} exists and is not a directory"
      [[ -z "$(ls -A "${WORK}")" ]] || refuse "work: ${WORK} is not empty; an empty directory is required"
    fi
  fi
  [[ -f "${VERSIONS}" ]] || refuse "manifest file '${VERSIONS}' not found"
  [[ -d "${KEYS_DIR}" ]] || refuse "keys directory '${KEYS_DIR}' not found"
}

check_tools() {
  local tool
  local -a tools=(gpg gpgv curl sha256sum tar dpkg-deb make gcc modinfo python3)
  for tool in "${tools[@]}"; do
    command -v "${tool}" >/dev/null 2>&1 || refuse "tools: required tool '${tool}' is missing"
  done
}

read_manifest() {
  local py_code='
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
modules = data.get("modules", {})
zfs = modules.get("openzfs")
if not zfs:
    sys.exit("modules.openzfs missing from manifest")
src = zfs.get("source", {})
for k in ("version", "linux_minimum", "linux_maximum"):
    if k not in zfs:
        sys.exit(f"modules.openzfs.{k} missing")
for k in ("url", "signature_url", "sha256", "signers"):
    if k not in src:
        sys.exit(f"modules.openzfs.source.{k} missing")
ver = zfs["version"]
url = src["url"]
sig_url = src["signature_url"]
sha = src["sha256"]
signers = ",".join(src["signers"])
min_ver = zfs["linux_minimum"]
max_ver = zfs["linux_maximum"]
print(f"ZFS_VERSION={ver}")
print(f"ZFS_URL={url}")
print(f"ZFS_SIG_URL={sig_url}")
print(f"ZFS_SHA256={sha}")
print(f"ZFS_SIGNERS={signers}")
print(f"LINUX_MINIMUM={min_ver}")
print(f"LINUX_MAXIMUM={max_ver}")
'
  local output
  if ! output="$(python3 -c "${py_code}" "${VERSIONS}")"; then
    refuse "manifest: failed to read modules.openzfs from ${VERSIONS}"
  fi
  while IFS='=' read -r key val; do
    case "${key}" in
      ZFS_VERSION) ZFS_VERSION="${val}" ;;
      ZFS_URL) ZFS_URL="${val}" ;;
      ZFS_SIG_URL) ZFS_SIG_URL="${val}" ;;
      ZFS_SHA256) ZFS_SHA256="${val}" ;;
      ZFS_SIGNERS) IFS=',' read -r -a SIGNERS <<<"${val}" ;;
      LINUX_MINIMUM) LINUX_MINIMUM="${val}" ;;
      LINUX_MAXIMUM) LINUX_MAXIMUM="${val}" ;;
    esac
  done <<<"${output}"
}

derive_kr() {
  local pkg_name
  if ! pkg_name="$(dpkg-deb -f "${HEADERS_DEB}" Package 2>/dev/null)"; then
    refuse "headers: failed to read Package field from ${HEADERS_DEB} with dpkg-deb"
  fi
  if [[ "${pkg_name}" != linux-headers-* ]]; then
    refuse "headers: package '${pkg_name}' is not a linux-headers package (expected linux-headers-<KR>)"
  fi
  KR="${pkg_name#linux-headers-}"
  if [[ -z "${KR}" ]]; then
    refuse "headers: kernel release string in ${pkg_name} is empty"
  fi
}

check_kernel_range() {
  local kr_major kr_minor min_major min_minor max_major max_minor
  if [[ ! "${KR}" =~ ^([0-9]+)\.([0-9]+) ]]; then
    refuse "range: cannot parse major.minor from kernel release '${KR}'"
  fi
  kr_major="${BASH_REMATCH[1]}"
  kr_minor="${BASH_REMATCH[2]}"

  if [[ ! "${LINUX_MINIMUM}" =~ ^([0-9]+)\.([0-9]+) ]]; then
    refuse "range: cannot parse major.minor from linux_minimum '${LINUX_MINIMUM}'"
  fi
  min_major="${BASH_REMATCH[1]}"
  min_minor="${BASH_REMATCH[2]}"

  if [[ ! "${LINUX_MAXIMUM}" =~ ^([0-9]+)\.([0-9]+) ]]; then
    refuse "range: cannot parse major.minor from linux_maximum '${LINUX_MAXIMUM}'"
  fi
  max_major="${BASH_REMATCH[1]}"
  max_minor="${BASH_REMATCH[2]}"

  local out_of_range=0
  if ((kr_major < min_major)) || ((kr_major == min_major && kr_minor < min_minor)); then
    out_of_range=1
  fi
  if ((kr_major > max_major)) || ((kr_major == max_major && kr_minor > max_minor)); then
    out_of_range=1
  fi

  if ((out_of_range == 1)); then
    refuse "range: kernel release '${KR}' (${kr_major}.${kr_minor}) is outside supported range [${LINUX_MINIMUM}, ${LINUX_MAXIMUM}]"
  fi
}

print_plan() {
  cat <<EOF
url: ${ZFS_URL}
sha256: ${ZFS_SHA256}
kernelrelease: ${KR}
range: [${LINUX_MINIMUM}, ${LINUX_MAXIMUM}]
EOF
}

download_and_verify() {
  mkdir -p "${WORK}"
  local tarball="${WORK}/${ZFS_URL##*/}"
  local sig="${WORK}/${ZFS_SIG_URL##*/}"

  echo "==> Downloading OpenZFS tarball"
  if ! curl --fail --silent --show-error --location --retry 3 --connect-timeout 30 --max-time 600 \
    --proto '=https' --proto-redir '=https' --output "${tarball}.part" "${ZFS_URL}"; then
    rm -f "${tarball}.part"
    refuse "download: failed to fetch tarball from ${ZFS_URL}"
  fi
  mv "${tarball}.part" "${tarball}"

  echo "==> Downloading OpenZFS detached signature"
  if ! curl --fail --silent --show-error --location --retry 3 --connect-timeout 30 --max-time 600 \
    --proto '=https' --proto-redir '=https' --output "${sig}.part" "${ZFS_SIG_URL}"; then
    rm -f "${sig}.part"
    refuse "download: failed to fetch signature from ${ZFS_SIG_URL}"
  fi
  mv "${sig}.part" "${sig}"

  echo "==> Verifying OpenZFS sha256 checksum"
  local actual_sha
  actual_sha="$(sha256sum "${tarball}" | cut -d' ' -f1)"
  if [[ "${actual_sha}" != "${ZFS_SHA256}" ]]; then
    refuse "sha256: checksum mismatch for ${tarball##*/}: expected ${ZFS_SHA256}, got ${actual_sha}"
  fi
  echo "    ✓ sha256 matches pin"

  echo "==> Verifying OpenZFS detached signature with gpgv"
  local keyring="${WORK}/openzfs-signers.gpg"
  local dearmor_scratch="${WORK}/gnupg-dearmor"
  rm -rf "${dearmor_scratch}"
  mkdir -m 700 "${dearmor_scratch}"
  : >"${keyring}"
  local fpr
  for fpr in "${SIGNERS[@]}"; do
    if [[ ! -f "${KEYS_DIR}/${fpr}.asc" ]]; then
      rm -rf "${dearmor_scratch}"
      refuse "gpgv: signer key ${fpr} missing at ${KEYS_DIR}/${fpr}.asc"
    fi
    gpg --homedir "${dearmor_scratch}" --batch --quiet --dearmor --output - <"${KEYS_DIR}/${fpr}.asc" >>"${keyring}"
  done
  rm -rf "${dearmor_scratch}"

  if ! GNUPGHOME="${WORK}" gpgv --keyring "${keyring}" "${sig}" "${tarball}"; then
    refuse "gpgv: signature verification failed for ${tarball##*/}"
  fi
  echo "    ✓ signature verified by gpgv"
}

unpack_headers() {
  local headers_dest="${WORK}/headers"
  mkdir -p "${headers_dest}"
  echo "==> Extracting kernel headers package ${HEADERS_DEB##*/}"
  if ! dpkg-deb -x "${HEADERS_DEB}" "${headers_dest}"; then
    refuse "dpkg-deb: failed to extract ${HEADERS_DEB}"
  fi

  # make bindeb-pkg installs the headers tree at /usr/src/linux-headers-<KR>.
  local tree="${headers_dest}/usr/src/linux-headers-${KR}"
  if [[ ! -f "${tree}/Makefile" ]]; then
    refuse "headers: ${HEADERS_DEB##*/} has no kernel headers tree at /usr/src/linux-headers-${KR}"
  fi
  HEADERS_TREE="${tree}"
  echo "    ✓ kernel headers located at ${HEADERS_TREE}"
}

build_and_check_module() {
  local tarball="${WORK}/${ZFS_URL##*/}"
  local src_dir="${WORK}/src"
  mkdir -p "${src_dir}"
  echo "==> Extracting OpenZFS source"
  if ! tar -xzf "${tarball}" -C "${src_dir}" --strip-components=1; then
    refuse "tar: failed to extract ${tarball##*/}"
  fi

  echo "==> Configuring OpenZFS module"
  cd "${src_dir}"
  if ! ./configure --with-config=kernel \
    --with-linux="${HEADERS_TREE}" \
    --with-linux-obj="${HEADERS_TREE}"; then
    refuse "configure: OpenZFS ./configure failed"
  fi

  echo "==> Building OpenZFS kernel module"
  if ! make -j "${JOBS}" -C module; then
    refuse "make: OpenZFS module build failed"
  fi

  echo "==> Verifying module/zfs.ko and vermagic"
  # module/Kbuild builds spl.o and zfs.o at the top of module/ (obj-$(CONFIG_ZFS)).
  local ko_path="${src_dir}/module/zfs.ko"
  if [[ ! -f "${ko_path}" ]]; then
    refuse "module: zfs.ko not found at ${ko_path}"
  fi

  local vermagic vermagic_kr
  if ! vermagic="$(modinfo -F vermagic "${ko_path}")"; then
    refuse "modinfo: failed to inspect vermagic on ${ko_path}"
  fi
  vermagic_kr="${vermagic%% *}"
  if [[ "${vermagic_kr}" != "${KR}" ]]; then
    refuse "vermagic: mismatch: expected '${KR}', found '${vermagic_kr}'"
  fi

  echo "openzfs ${ZFS_VERSION} built against ${KR}: vermagic ok"
}

main() {
  parse_arguments "$@"
  validate_arguments
  check_tools
  read_manifest
  derive_kr
  check_kernel_range
  if [[ "${PRINT_PLAN}" == "true" ]]; then
    print_plan
    exit 0
  fi
  download_and_verify
  unpack_headers
  build_and_check_module
}

main "$@"
