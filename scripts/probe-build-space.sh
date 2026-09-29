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
# scripts/probe-build-space.sh — choose where a kernel build has room, or refuse before it starts.
#
# Usage: probe-build-space.sh [--need-gib=<N>] <dir>...
# Prints the existing directory among <dir>... with the most free space, when it has at least N
# GiB free (default 12). Measured per leg: an object tree of 5.1 to 5.9 GiB on x86_64, 11 GiB on
# arm64 and 1.1 GiB on riscv64, a 1.5 GiB source tree, a compiler cache of up to 0.7 GiB and 0.1 GiB of packages.
# Otherwise exits 1 and says so, so a build without room fails in its first minute rather than
# an hour in.
set -euo pipefail

NEED_GIB=12
declare -a CANDIDATES=()

refuse() {
  echo "Refused: $*" >&2
  exit 1
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --need-gib=*) NEED_GIB="${1#*=}" ;;
      -*) refuse "unknown option $1" ;;
      *) CANDIDATES+=("$1") ;;
    esac
    shift
  done
  [[ "${NEED_GIB}" =~ ^[1-9][0-9]{0,3}$ ]] || refuse "--need-gib must be a positive integer"
  [[ "${#CANDIDATES[@]}" -gt 0 ]] || refuse "usage: $0 [--need-gib=<N>] <dir>..."
}

main() {
  parse_arguments "$@"
  local dir kib best="" best_kib=0
  for dir in "${CANDIDATES[@]}"; do
    [[ -d "${dir}" ]] || continue
    kib="$(df --output=avail -k "${dir}" | tail -n 1 | tr -d ' ')"
    [[ "${kib}" =~ ^[0-9]+$ ]] || refuse "df reported no free space for ${dir}"
    echo "    ${dir}: $((kib / 1024 / 1024)) GiB free" >&2
    if [[ "${kib}" -gt "${best_kib}" ]]; then
      best="${dir}"
      best_kib="${kib}"
    fi
  done
  [[ -n "${best}" ]] || refuse "none of ${CANDIDATES[*]} exists"
  if [[ "${best_kib}" -lt $((NEED_GIB * 1024 * 1024)) ]]; then
    refuse "the most free space is $((best_kib / 1024 / 1024)) GiB in ${best}; a kernel build needs ${NEED_GIB} GiB"
  fi
  echo "${best}"
}

main "$@"
