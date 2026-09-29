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
# scripts/package-deb.sh — build a stream's Debian packages from a verified kernel source tree.
#
# A thin wrapper over scripts/build_kernel.sh, which resolves the configuration, compiles with
# bindeb-pkg and gates the packages. It takes a tree scripts/fetch-kernel-source.sh verified;
# without one a production run is refused and writes nothing. It used to write text files named
# .deb and checksum them when --source-tree was omitted (issue #31).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
declare -a FORWARD=()
SOURCE_TREE=""
DRY_RUN="false"

show_usage() {
  cat <<'USAGE'
Usage: package-deb.sh --stream=<stream> --arch=<arch> --source-tree=<dir> [options]
  --stream=<stream>     a stream of versions.json [default: mainstream]
  --arch=<arch>         an architecture of versions.json [default: x86_64]
  --source-tree=<dir>   a tree scripts/fetch-kernel-source.sh verified (required unless --dry-run)
  --output-dir=<dir>    where the packages go [default: output/<stream>-<arch>]
  --revision=<N>        the forge revision [default: 1]
  --dry-run             state what the build would do; writes nothing
  -h, --help            show this help
The packages, their version and the kernel release come from scripts/build_kernel.sh, which
reads versions.json; see that script for the full set of options.
USAGE
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --source-tree=*)
        SOURCE_TREE="${1#*=}"
        FORWARD+=("$1")
        ;;
      --dry-run)
        DRY_RUN="true"
        FORWARD+=("$1")
        ;;
      --stream=* | --arch=* | --output-dir=* | --revision=*) FORWARD+=("$1") ;;
      -h | --help)
        show_usage
        exit 0
        ;;
      *)
        echo "Refused: unknown argument $1" >&2
        exit 1
        ;;
    esac
    shift
  done
}

main() {
  parse_arguments "$@"
  if [[ "${DRY_RUN}" != "true" && -z "${SOURCE_TREE}" ]]; then
    echo "Refused: a production package build needs --source-tree=<dir>, a tree" >&2
    echo "         scripts/fetch-kernel-source.sh verified. Nothing was written." >&2
    exit 1
  fi
  exec "${SCRIPT_DIR}/build_kernel.sh" "${FORWARD[@]}"
}

main "$@"
