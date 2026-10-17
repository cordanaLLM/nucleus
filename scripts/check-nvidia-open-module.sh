#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/check-nvidia-open-module.sh — build and verify the NVIDIA Open GPU
# kernel modules against a verified kernel headers Debian package.
#
# Reads module source, version, cryptographic pins and target streams from
# versions.json (modules.nvidia_open). Verifies tarball sha256 digest.
# Builds the modules under the extracted headers tree, then verifies vermagic
# matches the target kernel release and required .ko files exist.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HEADERS_DEB=""
TARBALL=""
WORK=""
JOBS=2
VERSIONS="versions.json"
PRINT_PLAN="false"
TAG=""

NV_VERSION=""
NV_URL=""
NV_SHA256=""
NV_COMMIT=""
NV_STREAMS=""
NV_ARCHITECTURES=""
KR=""
HEADERS_TREE=""

show_usage() {
  cat <<'EOF'
Usage: check-nvidia-open-module.sh --headers-deb=<path> [options]
  --headers-deb=<path>  linux-headers-<KR>_*.deb package to build against
  --tarball=<path>      pre-downloaded source tarball (optional; fetched if omitted)
  --work=<dir>          scratch directory for downloads and build (must be empty)
  --jobs=<N>            parallel compilation jobs [default: 2]
  --versions=<path>     manifest declaring modules.nvidia_open [default: versions.json]
  --tag=<version>       verify specific upstream release tag matches pin
  --print-plan          print plan (url, sha, KR, legs) and exit 0 without writing
EOF
}

refuse() {
  echo "Refused: $*" >&2
  exit 1
}

parse_arguments() {
  local -i limit=50
  while [[ $# -gt 0 ]] && ((limit-- > 0)); do
    case "$1" in
      --headers-deb=*) HEADERS_DEB="${1#*=}" ;;
      --headers-deb)
        [[ $# -ge 2 ]] || refuse "--headers-deb requires a path argument"
        HEADERS_DEB="$2"
        shift
        ;;
      --tarball=*) TARBALL="${1#*=}" ;;
      --tarball)
        [[ $# -ge 2 ]] || refuse "--tarball requires a path argument"
        TARBALL="$2"
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
      --tag=* | --version=*) TAG="${1#*=}" ;;
      --tag | --version)
        [[ $# -ge 2 ]] || refuse "$1 requires a version argument"
        TAG="$2"
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
  [[ -n "${HEADERS_DEB}" ]] || refuse "headers deb missing: --headers-deb is required"
  [[ -e "${HEADERS_DEB}" ]] || refuse "headers deb missing: '${HEADERS_DEB}' does not exist"
  [[ -f "${HEADERS_DEB}" ]] || refuse "headers deb missing: '${HEADERS_DEB}' is not a regular file"
  [[ -s "${HEADERS_DEB}" ]] || refuse "headers deb empty: '${HEADERS_DEB}' has zero size"
  if [[ -n "${TARBALL}" ]]; then
    [[ -e "${TARBALL}" ]] || refuse "tarball: '${TARBALL}' does not exist"
    [[ -f "${TARBALL}" ]] || refuse "tarball: '${TARBALL}' is not a regular file"
  fi
  if [[ "${PRINT_PLAN}" == "false" ]]; then
    [[ -n "${WORK}" ]] || refuse "--work is required"
    if [[ -e "${WORK}" ]]; then
      [[ -d "${WORK}" ]] || refuse "work: ${WORK} exists and is not a directory"
      [[ -z "$(ls -A "${WORK}")" ]] || refuse "work: ${WORK} is not empty; an empty directory is required"
    fi
    mkdir -p "${WORK}" || refuse "work: cannot create ${WORK}"
    WORK="$(cd "${WORK}" && pwd)" || refuse "work: cannot resolve ${WORK}"
    echo "==> Work directory ${WORK}"
  fi
  [[ -f "${VERSIONS}" ]] || refuse "manifest file '${VERSIONS}' not found"
}

check_tools() {
  local tool
  local -a tools=(sha256sum tar gzip git curl dpkg-deb make gcc modinfo python3)
  local -i limit=20
  for tool in "${tools[@]}"; do
    ((limit-- > 0)) || break
    command -v "${tool}" >/dev/null 2>&1 || refuse "tools: required tool '${tool}' is missing"
  done
}

read_manifest() {
  local output
  if ! output="$(python3 "${SCRIPT_DIR}/versions_query.py" --versions="${VERSIONS}" nvidia-open)"; then
    refuse "manifest: failed to read modules.nvidia_open from ${VERSIONS}"
  fi
  local -i limit=50
  while IFS='=' read -r key val && ((limit-- > 0)); do
    case "${key}" in
      version) NV_VERSION="${val}" ;;
      url) NV_URL="${val}" ;;
      sha256) NV_SHA256="${val}" ;;
      commit) NV_COMMIT="${val}" ;;
      streams) NV_STREAMS="${val}" ;;
      architectures) NV_ARCHITECTURES="${val}" ;;
    esac
  done <<<"${output}"
}

derive_kr() {
  local pkg_name
  if ! pkg_name="$(dpkg-deb -f "${HEADERS_DEB}" Package 2>/dev/null)"; then
    refuse "headers deb kernel release unknown: failed to read Package field from ${HEADERS_DEB} with dpkg-deb"
  fi
  if [[ "${pkg_name}" != linux-headers-* ]]; then
    refuse "headers deb kernel release unknown: package '${pkg_name}' is not a linux-headers package (expected linux-headers-<KR>)"
  fi
  KR="${pkg_name#linux-headers-}"
  if [[ -z "${KR}" ]]; then
    refuse "headers deb kernel release unknown: kernel release string in '${pkg_name}' is empty"
  fi
}

check_version_tag() {
  if [[ -n "${TAG}" && "${TAG}" != "${NV_VERSION}" ]]; then
    refuse "unknown tag: '${TAG}' (does not equal pin '${NV_VERSION}')"
  fi
}

print_plan() {
  cat <<EOF
url: ${NV_URL}
sha256: ${NV_SHA256}
commit: ${NV_COMMIT}
kernelrelease: ${KR}
legs: ${NV_STREAMS}
streams: ${NV_STREAMS}
architectures: ${NV_ARCHITECTURES}
EOF
}

obtain_and_verify_tarball() {
  mkdir -p "${WORK}"
  local tarball
  if [[ -n "${TARBALL}" ]]; then
    tarball="${TARBALL}"
  else
    tarball="${WORK}/${NV_URL##*/}"
    echo "==> Downloading NVIDIA open-gpu-kernel-modules tarball (${NV_COMMIT})"
    if ! curl --fail --silent --show-error --location --retry 3 --connect-timeout 30 --max-time 600 \
      --proto '=https' --proto-redir '=https' --output "${tarball}.part" "${NV_URL}"; then
      rm -f "${tarball}.part"
      refuse "download: failed to fetch tarball from ${NV_URL}"
    fi
    mv "${tarball}.part" "${tarball}"
  fi

  echo "==> Verifying NVIDIA open tarball sha256 checksum"
  local actual_sha
  actual_sha="$(sha256sum "${tarball}" | cut -d' ' -f1)"
  if [[ "${actual_sha}" != "${NV_SHA256}" ]]; then
    refuse "tarball sha256 mismatch for ${tarball##*/}: expected ${NV_SHA256}, got ${actual_sha}"
  fi
  echo "    ✓ sha256 matches pin"
  local archive_commit
  if ! archive_commit="$(gzip -dc "${tarball}" | git get-tar-commit-id)"; then
    refuse "tarball ${tarball##*/} carries no git archive commit id"
  fi
  if [[ "${archive_commit}" != "${NV_COMMIT}" ]]; then
    refuse "tarball commit mismatch for ${tarball##*/}: expected ${NV_COMMIT}, got ${archive_commit}"
  fi
  echo "    ✓ archive commit matches pin (${NV_COMMIT})"
  TARBALL="${tarball}"
}

unpack_headers() {
  local headers_dest="${WORK}/headers"
  mkdir -p "${headers_dest}"
  echo "==> Extracting kernel headers package ${HEADERS_DEB##*/}"
  if ! dpkg-deb -x "${HEADERS_DEB}" "${headers_dest}"; then
    refuse "dpkg-deb: failed to extract ${HEADERS_DEB}"
  fi

  local tree="${headers_dest}/usr/src/linux-headers-${KR}"
  if [[ ! -f "${tree}/Makefile" ]]; then
    refuse "headers: ${HEADERS_DEB##*/} has no kernel headers tree at /usr/src/linux-headers-${KR}"
  fi
  HEADERS_TREE="${tree}"
  echo "    ✓ kernel headers located at ${HEADERS_TREE}"
}

build_and_check_module() {
  local src_dir="${WORK}/src"
  mkdir -p "${src_dir}"
  echo "==> Extracting NVIDIA open module source"
  if ! tar -xzf "${TARBALL}" -C "${src_dir}" --strip-components=1; then
    refuse "tar: failed to extract ${TARBALL##*/}"
  fi

  echo "==> Building NVIDIA open GPU kernel modules"
  cd "${src_dir}"
  if ! make -j "${JOBS:-2}" modules SYSSRC="${HEADERS_TREE}" SYSOUT="${HEADERS_TREE}"; then
    refuse "make: NVIDIA open module build failed"
  fi

  echo "==> Verifying kernel modules and vermagic"
  local -a required_modules=(
    "kernel-open/nvidia.ko"
    "kernel-open/nvidia-uvm.ko"
    "kernel-open/nvidia-modeset.ko"
    "kernel-open/nvidia-drm.ko"
  )
  local mod_rel ko_path vermagic vermagic_kr
  local -i limit=10
  for mod_rel in "${required_modules[@]}"; do
    ((limit-- > 0)) || break
    ko_path="${src_dir}/${mod_rel}"
    if [[ ! -f "${ko_path}" ]]; then
      refuse "module: required module ${mod_rel} not found at ${ko_path}"
    fi
    if ! vermagic="$(modinfo -F vermagic "${ko_path}")"; then
      refuse "modinfo: failed to inspect vermagic on ${ko_path}"
    fi
    vermagic_kr="${vermagic%% *}"
    if [[ "${vermagic_kr}" != "${KR}" ]]; then
      refuse "vermagic: mismatch on ${mod_rel}: expected '${KR}', found '${vermagic_kr}'"
    fi
    echo "built module: ${ko_path}"
  done

  echo "nvidia-open ${NV_VERSION} built against ${KR}: all 4 modules verified"
}

main() {
  parse_arguments "$@"
  validate_arguments
  check_tools
  read_manifest
  derive_kr
  check_version_tag
  if [[ "${PRINT_PLAN}" == "true" ]]; then
    print_plan
    exit 0
  fi
  obtain_and_verify_tarball
  unpack_headers
  build_and_check_module
}

main "$@"
