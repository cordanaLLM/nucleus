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
# scripts/build_kernel.sh — compile one stream for one architecture into Debian packages
# (docs/adr/0009-kernel-compilation-and-artifact-gate.md). Run from the repository root.
#
# The production path runs these steps in order and stops, exit status 1, at the first failure:
#   1. fetch and verify the stream's signed source with scripts/fetch-kernel-source.sh, unless
#      --source-tree names a tree that script already wrote;
#   2. resolve the kconfig fragments against it with scripts/merge-config.sh --source-tree;
#   3. make bindeb-pkg with LOCALVERSION=-lusoris<N>-<stream> and KDEB_PKGVERSION=
#      <version>-lusoris<N>, without the debug-symbol package, in a fixed build environment;
#   4. record `make -s kernelrelease` and extract ./boot/vmlinuz-<kernelrelease> from the image
#      package;
#   5. pass what it produced through the artifact gate, scripts/check_kernel_artifacts.py.
# The output directory then holds exactly the packages, vmlinuz-<kernelrelease> and
# kernel-<stream>-<arch>.config, and <work-dir>/<stream>-<arch>/build.json records the build.
set -euo pipefail

STREAM="mainstream"
ARCH="x86_64"
DRY_RUN=false
REVISION="1"
SOURCE_TREE=""
WORK_DIR="build"
OUTPUT_DIR=""
JOBS=""
declare -A SRC=() ARC=()
LOCAL_VERSION=""
PACKAGE_VERSION=""
LEG_DIR=""
KBUILD_DIR=""
HOST_ARCH=""
PROFILES=""
KERNELRELEASE=""

show_usage() {
  cat <<'EOF'
Usage: build_kernel.sh --stream=<stream> --arch=<arch> [options]
  --stream=<stream>     a stream of versions.json [default: mainstream]
  --arch=<arch>         an architecture of versions.json [default: x86_64]
  --revision=<N>        the forge revision, a positive integer: LOCALVERSION -lusoris<N>-<stream>,
                        package version <version>-lusoris<N> [default: 1]
  --source-tree=<dir>   a tree scripts/fetch-kernel-source.sh verified; fetched when omitted
  --work-dir=<dir>      source, object tree, log and build record [default: build]
  --output-dir=<dir>    where the artifacts go; must not exist or be empty
                        [default: output/<stream>-<arch>]
  --jobs=<N>            parallel make jobs [default: nproc]
  --dry-run             state what a build would do; fetch, compile and write nothing
Needs, besides what fetch-kernel-source.sh and merge-config.sh need: dpkg-dev, debhelper,
kmod, cpio, rsync and the architecture's cross compiler from versions.json.
EOF
}

refuse() {
  echo "Refused: $*" >&2
  exit 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --stream=*) STREAM="${1#*=}" ;;
      --arch=*) ARCH="${1#*=}" ;;
      --revision=*) REVISION="${1#*=}" ;;
      --source-tree=*) SOURCE_TREE="${1#*=}" ;;
      --work-dir=*) WORK_DIR="${1#*=}" ;;
      --output-dir=*) OUTPUT_DIR="${1#*=}" ;;
      --jobs=*) JOBS="${1#*=}" ;;
      --dry-run) DRY_RUN=true ;;
      -h | --help)
        show_usage
        exit 0
        ;;
      *) refuse "unknown option $1" ;;
    esac
    shift
  done
}

# Every value placed on a make command line comes from versions.json through
# scripts/versions_query.py, which checks its shape and refuses an unknown stream or architecture.
load_manifest() {
  local output line
  output="$(python3 scripts/versions_query.py source "${STREAM}")" ||
    refuse "versions.json has no buildable stream '${STREAM}'"
  while IFS= read -r line; do
    SRC["${line%%=*}"]="${line#*=}"
  done <<<"${output}"
  output="$(python3 scripts/versions_query.py arch "${ARCH}")" ||
    refuse "versions.json has no buildable architecture '${ARCH}'"
  while IFS= read -r line; do
    ARC["${line%%=*}"]="${line#*=}"
  done <<<"${output}"
  [[ "${REVISION}" =~ ^[1-9][0-9]{0,3}$ ]] || refuse "--revision must be a positive integer, not '${REVISION}'"
  [[ -z "${JOBS}" || "${JOBS}" =~ ^[1-9][0-9]{0,3}$ ]] || refuse "--jobs must be a positive integer"
  [[ -f "kconfig/${ARCH}.config" ]] || refuse "kconfig/${ARCH}.config not found; run from the repository root"
}

# The forge name of the machine this runs on. A build for another architecture is a cross build,
# and a cross build omits the headers package: its host programs would have to be rebuilt for
# the target and link the target's libcrypto (scripts/package/install-extmod-build).
detect_host() {
  case "$(uname -m)" in
    x86_64) HOST_ARCH="x86_64" ;;
    aarch64 | arm64) HOST_ARCH="arm64" ;;
    riscv64) HOST_ARCH="riscv64" ;;
    *) HOST_ARCH="$(uname -m)" ;;
  esac
  PROFILES="pkg.linux-upstream.nokerneldbg"
  if [[ "${HOST_ARCH}" != "${ARCH}" ]]; then
    PROFILES+=" pkg.linux-upstream.nokernelheaders"
  fi
}

derive_names() {
  LOCAL_VERSION="-lusoris${REVISION}-${STREAM}"
  PACKAGE_VERSION="${SRC[version]}-lusoris${REVISION}"
  OUTPUT_DIR="${OUTPUT_DIR:-output/${STREAM}-${ARCH}}"
  LEG_DIR="${WORK_DIR}/${STREAM}-${ARCH}"
  KBUILD_DIR="${LEG_DIR}/kbuild"
  JOBS="${JOBS:-$(nproc)}"
  detect_host
}

state_plan() {
  local source="${SOURCE_TREE:-${WORK_DIR}/linux-${STREAM}}"
  echo "==> Plan for stream '${STREAM}' ${SRC[version]} (kernel ${SRC[kernelversion]}) on ${ARCH}, built on ${HOST_ARCH}"
  if [[ -z "${SOURCE_TREE}" ]]; then
    echo "    fetch:   scripts/fetch-kernel-source.sh --stream=${STREAM} --dest=${source} (${SRC[kind]}, signature checked)"
  else
    echo "    source:  ${source} (verified by scripts/fetch-kernel-source.sh)"
  fi
  echo "    resolve: scripts/merge-config.sh --arch=${ARCH} --stream=${STREAM} --source-tree=${source} --build-dir=${KBUILD_DIR} --output=${OUTPUT_DIR}/kernel-${STREAM}-${ARCH}.config"
  echo "    build:   DEB_BUILD_PROFILES='${PROFILES}' make O=${KBUILD_DIR} ARCH=${ARC[kernel_arch]} CROSS_COMPILE=${ARC[cross_compile]} -j${JOBS} bindeb-pkg LOCALVERSION=${LOCAL_VERSION} KDEB_PKGVERSION=${PACKAGE_VERSION}"
  echo "    env:     SOURCE_DATE_EPOCH and KBUILD_BUILD_TIMESTAMP from the source release, KBUILD_BUILD_USER=nucleus, KBUILD_BUILD_HOST=forge"
  echo "    record:  make -s kernelrelease (expected ${SRC[kernelversion]}${LOCAL_VERSION}), ${LEG_DIR}/build.json"
  echo "    gate:    scripts/check_kernel_artifacts.py over ${OUTPUT_DIR}: ${ARC[debian_arch]} packages at ${PACKAGE_VERSION}, ./boot/vmlinuz-<kernelrelease>, /boot/config == the resolved config"
}

check_tools() {
  local tool
  for tool in make python3 dpkg-buildpackage dpkg-deb dh_testdir depmod cpio rsync tar install "${ARC[cross_compile]}gcc"; do
    command -v "${tool}" >/dev/null 2>&1 || refuse "${tool} is required to build ${ARCH} packages and is not installed"
  done
  if ! gnu_install >/dev/null; then
    refuse "install is not GNU coreutils and gnuinstall is not installed; the uutils install -D races in the parallel device-tree install"
  fi
}

# The kernel installs device trees with parallel `install -D`. The uutils install that Ubuntu 26.04
# ships fails when two calls create the same directory; GNU install does not. Ubuntu keeps GNU
# install as gnuinstall (gnu-coreutils), and the build uses it through a PATH shim.
gnu_install() {
  if [[ "$(install --version 2>/dev/null | head -n 1)" == *"GNU coreutils"* ]]; then
    command -v install
  else
    command -v gnuinstall
  fi
}

use_gnu_install() {
  local gnu
  gnu="$(gnu_install)"
  if [[ "${gnu##*/}" == "gnuinstall" ]]; then
    mkdir -p "${LEG_DIR}/gnu-install"
    ln -sfn "${gnu}" "${LEG_DIR}/gnu-install/install"
    export PATH="${LEG_DIR}/gnu-install:${PATH}"
    echo "==> install is not GNU coreutils; the build uses ${gnu}"
  fi
}

# Everything that can be refused without writing is refused here, before anything is written.
# A forge must never mix the artifacts of two builds, so the output directory starts empty.
check_request() {
  if [[ -e "${OUTPUT_DIR}" ]] && [[ ! -d "${OUTPUT_DIR}" || -n "$(ls -A "${OUTPUT_DIR}")" ]]; then
    refuse "${OUTPUT_DIR} is not an empty directory; remove it or pass another --output-dir"
  fi
  if [[ -n "${SOURCE_TREE}" ]]; then
    [[ -f "${SOURCE_TREE}/Makefile" && -x "${SOURCE_TREE}/scripts/package/builddeb" ]] ||
      refuse "${SOURCE_TREE} is not a kernel source tree"
  elif [[ -e "${WORK_DIR}/linux-${STREAM}" && -n "$(ls -A "${WORK_DIR}/linux-${STREAM}" 2>/dev/null)" ]]; then
    refuse "${WORK_DIR}/linux-${STREAM} already exists; pass --source-tree=${WORK_DIR}/linux-${STREAM} if fetch-kernel-source.sh wrote it, or remove it"
  fi
  check_tools
}

prepare_dirs() {
  mkdir -p "${OUTPUT_DIR}" "${LEG_DIR}"
  OUTPUT_DIR="$(realpath "${OUTPUT_DIR}")"
  LEG_DIR="$(realpath "${LEG_DIR}")"
  KBUILD_DIR="${LEG_DIR}/kbuild"
  # Packages of an earlier build of this leg would otherwise be collected with this one.
  rm -f "${LEG_DIR}"/*.deb "${LEG_DIR}"/*.buildinfo "${LEG_DIR}"/*.changes
}

obtain_source() {
  if [[ -z "${SOURCE_TREE}" ]]; then
    SOURCE_TREE="${WORK_DIR}/linux-${STREAM}"
    ./scripts/fetch-kernel-source.sh --stream="${STREAM}" --dest="${SOURCE_TREE}"
  fi
  SOURCE_TREE="$(realpath "${SOURCE_TREE}")"
}

# The build carries no clock and no builder identity: the timestamp is the release commit's,
# which git archive gives every file in the tree, the Makefile included.
fixed_environment() {
  local epoch
  epoch="$(stat -c %Y "${SOURCE_TREE}/Makefile")"
  [[ "${epoch}" =~ ^[1-9][0-9]*$ ]] || refuse "cannot read the release time of ${SOURCE_TREE}"
  export SOURCE_DATE_EPOCH="${epoch}"
  KBUILD_BUILD_TIMESTAMP="$(LC_ALL=C TZ=UTC date -u -d "@${epoch}")"
  export KBUILD_BUILD_TIMESTAMP KBUILD_BUILD_USER="nucleus" KBUILD_BUILD_HOST="forge"
  export LC_ALL=C TZ=UTC DEB_BUILD_PROFILES="${PROFILES}"
  echo "==> SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH} (${KBUILD_BUILD_TIMESTAMP}), profiles: ${PROFILES}"
}

resolve_config() {
  ./scripts/merge-config.sh --arch="${ARCH}" --stream="${STREAM}" --source-tree="${SOURCE_TREE}" \
    --build-dir="${KBUILD_DIR}" --output="${OUTPUT_DIR}/kernel-${STREAM}-${ARCH}.config"
}

kernel_make() {
  make -C "${SOURCE_TREE}" O="${KBUILD_DIR}" ARCH="${ARC[kernel_arch]}" \
    CROSS_COMPILE="${ARC[cross_compile]}" LOCALVERSION="${LOCAL_VERSION}" "$@"
}

compile_packages() {
  local log="${LEG_DIR}/build.log"
  echo "==> make -j${JOBS} bindeb-pkg (log: ${log})"
  SECONDS=0
  if ! kernel_make -j"${JOBS}" KDEB_PKGVERSION="${PACKAGE_VERSION}" bindeb-pkg >"${log}" 2>&1; then
    tail -n 60 "${log}" >&2
    refuse "make bindeb-pkg failed for ${STREAM}/${ARCH} after ${SECONDS}s"
  fi
  BUILD_SECONDS="${SECONDS}"
  KERNELRELEASE="$(kernel_make -s kernelrelease)"
  echo "==> Compiled ${KERNELRELEASE} in ${BUILD_SECONDS}s"
}

collect_artifacts() {
  local -a debs=() images=()
  mapfile -t debs < <(find "${LEG_DIR}" -maxdepth 1 -type f -name '*.deb' | sort)
  [[ "${#debs[@]}" -gt 0 ]] || refuse "make bindeb-pkg wrote no package into ${LEG_DIR}"
  mv -- "${debs[@]}" "${OUTPUT_DIR}/"
  mapfile -t images < <(find "${OUTPUT_DIR}" -maxdepth 1 -type f -name "linux-image-${KERNELRELEASE}_*.deb")
  [[ "${#images[@]}" -eq 1 ]] || refuse "expected one linux-image-${KERNELRELEASE} package, found ${#images[@]}"
  local vmlinuz="${OUTPUT_DIR}/vmlinuz-${KERNELRELEASE}"
  if ! dpkg-deb --fsys-tarfile "${images[0]}" | tar -xOf - "./boot/vmlinuz-${KERNELRELEASE}" >"${vmlinuz}"; then
    rm -f "${vmlinuz}"
    refuse "${images[0]##*/} carries no ./boot/vmlinuz-${KERNELRELEASE}"
  fi
}

# The record is what the release reads: the measured kernelrelease and the digest of the
# configuration that went into the packages. Values reach Python as arguments, never as code.
readonly RECORD_PY='
import hashlib, json, sys
from pathlib import Path
record_path, out = Path(sys.argv[1]), Path(sys.argv[2])
record = dict(arg.split("=", 1) for arg in sys.argv[3:])
for key in ("revision", "source_date_epoch", "build_seconds"):
    record[key] = int(record[key])
record["profiles"] = record["profiles"].split()
config = out / record["config"]
record["config_sha256"] = hashlib.sha256(config.read_bytes()).hexdigest()
record["artifacts"] = sorted(path.name for path in out.iterdir())
record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
'

write_record() {
  python3 -c "${RECORD_PY}" "${LEG_DIR}/build.json" "${OUTPUT_DIR}" \
    "stream=${STREAM}" "arch=${ARCH}" "version=${SRC[version]}" "kernelversion=${SRC[kernelversion]}" \
    "revision=${REVISION}" "localversion=${LOCAL_VERSION}" "kernelrelease=${KERNELRELEASE}" \
    "package_version=${PACKAGE_VERSION}" "kernel_arch=${ARC[kernel_arch]}" \
    "debian_arch=${ARC[debian_arch]}" "cross_compile=${ARC[cross_compile]}" "host_arch=${HOST_ARCH}" \
    "profiles=${PROFILES}" "source_date_epoch=${SOURCE_DATE_EPOCH}" \
    "build_timestamp=${KBUILD_BUILD_TIMESTAMP}" "build_seconds=${BUILD_SECONDS}" \
    "config=kernel-${STREAM}-${ARCH}.config" "output_dir=${OUTPUT_DIR}"
}

run_gate() {
  python3 scripts/check_kernel_artifacts.py --dir="${OUTPUT_DIR}" --stream="${STREAM}" --arch="${ARCH}" \
    --revision="${REVISION}" --kernelrelease="${KERNELRELEASE}"
}

main() {
  parse_args "$@"
  load_manifest
  derive_names
  state_plan
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY-RUN] Nothing was fetched, compiled or written."
    return 0
  fi
  check_request
  prepare_dirs
  use_gnu_install
  obtain_source
  fixed_environment
  resolve_config
  compile_packages
  collect_artifacts
  run_gate
  write_record
  echo "==> ${STREAM}/${ARCH}: ${KERNELRELEASE} in ${OUTPUT_DIR}, record ${LEG_DIR}/build.json"
}

BUILD_SECONDS=0
main "$@"
