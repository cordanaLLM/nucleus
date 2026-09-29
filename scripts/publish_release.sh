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
# scripts/publish_release.sh — write SHA256SUMS over exactly the files a release publishes.
#
# Usage: OUTPUT_DIR=<dir> publish_release.sh <release-tag-or-label>
#
# OUTPUT_DIR (default: output) must hold only publishable files: Debian packages, vmlinuz-*,
# *.config and the *.cdx.json / *.spdx.json SBOMs. Anything else, an empty file, a directory
# with nothing to publish, or an existing SHA256SUMS is refused with exit status 1, and no
# SHA256SUMS is written. A sha256sum failure is never swallowed. The artifact gate
# (scripts/check_kernel_artifacts.py) runs before this and has already opened the packages;
# this script checks only what a checksum file needs.
set -euo pipefail

OUTPUT_DIR="${OUTPUT_DIR:-output}"
TAG="${1:-}"
declare -a PUBLISHED=()

refuse() {
  echo "Refused: $*" >&2
  exit 1
}

validate_environment() {
  [[ -n "${TAG}" ]] || refuse "usage: OUTPUT_DIR=<dir> $0 <release-tag-or-label>"
  [[ -d "${OUTPUT_DIR}" ]] || refuse "output directory '${OUTPUT_DIR}' does not exist"
  [[ ! -e "${OUTPUT_DIR}/SHA256SUMS" ]] || refuse "${OUTPUT_DIR}/SHA256SUMS already exists; it is written once, over a fresh build"
}

publishable() {
  case "$1" in
    *.deb | vmlinuz-* | *.config | *.cdx.json | *.spdx.json) return 0 ;;
    *) return 1 ;;
  esac
}

collect_published() {
  local entry name debs=0
  for entry in "${OUTPUT_DIR}"/* "${OUTPUT_DIR}"/.[!.]*; do
    [[ -e "${entry}" || -L "${entry}" ]] || continue
    name="${entry##*/}"
    [[ -f "${entry}" && ! -L "${entry}" ]] || refuse "${name} is not a regular file"
    publishable "${name}" || refuse "${name} is not a publishable artifact"
    [[ -s "${entry}" ]] || refuse "${name} is empty; a zero-byte artifact is never checksummed"
    [[ "${name}" != *.deb ]] || debs=$((debs + 1))
    PUBLISHED+=("${name}")
  done
  [[ "${#PUBLISHED[@]}" -gt 0 ]] || refuse "nothing to checksum in ${OUTPUT_DIR}"
  [[ "${debs}" -gt 0 ]] || refuse "no Debian package in ${OUTPUT_DIR}; a release without a kernel is not published"
}

generate_checksums() {
  echo "==> Writing SHA256SUMS for ${TAG} over ${#PUBLISHED[@]} files in ${OUTPUT_DIR}"
  local partial="${OUTPUT_DIR}/.SHA256SUMS.partial"
  if ! (cd "${OUTPUT_DIR}" && sha256sum -- "${PUBLISHED[@]}") >"${partial}"; then
    rm -f "${partial}"
    refuse "sha256sum failed; no SHA256SUMS was written"
  fi
  mv -- "${partial}" "${OUTPUT_DIR}/SHA256SUMS"
  cat "${OUTPUT_DIR}/SHA256SUMS"
}

main() {
  validate_environment
  collect_published
  generate_checksums
}

main "$@"
