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
# scripts/merge-config.sh — Composable KConfig Fragment Merger
# Complies with NASA/JPL Power of 10: functions <= 60 lines, checked returns.
set -euo pipefail

ARCH="x86_64"
STREAM=""
OUTPUT_FILE=""
DRY_RUN="false"
declare -a EXTRA_FRAGMENTS=()

# The fragment grammar, shared with read_config() in scripts/verify_kernel_requirement.py.
# After trimming surrounding whitespace, a line is blank, a "CONFIG_X=value" assignment,
# the Kconfig unset form "# CONFIG_X is not set", or another "#" comment. The last line
# for a symbol wins, whichever of the two forms it takes, so a later fragment can unset
# what an earlier one set. Any other line is refused rather than passed through.
# The single quotes are deliberate: $0 is an awk field, not a shell expansion.
# shellcheck disable=SC2016
readonly MERGE_AWK='
{
  line = $0
  sub(/^[ \t\r]+/, "", line)
  sub(/[ \t\r]+$/, "", line)
}
line ~ /^CONFIG_[A-Za-z0-9_]+=/ {
  key = line
  sub(/=.*/, "", key)
  lines[key] = line
  next
}
line ~ /^# CONFIG_[A-Za-z0-9_]+ is not set$/ {
  key = line
  sub(/^# /, "", key)
  sub(/ is not set$/, "", key)
  lines[key] = line
  next
}
line == "" || line ~ /^#/ { next }
{
  printf "%s:%d: not an assignment, an unset or a comment: %s\n", FILENAME, FNR, line > "/dev/stderr"
  refused = 1
}
END {
  if (refused) exit 1
  for (key in lines) printf "%s\t%s\n", key, lines[key]
}'

show_usage() {
  cat <<'EOF'
Usage: merge-config.sh [options] [extra_fragments...]
Options:
  --arch=<arch>        Target architecture from versions.json [default: x86_64]
  --stream=<stream>    Release stream from versions.json; layers kconfig/streams/<stream>.config
                       after the architecture fragment when that file exists [default: none]
  --output=<path>      Output path for merged .config [default: output/.config-<arch>]
  --dry-run            Simulate configuration merge without writing target file
  -h, --help           Show this help message

Fragments merge in this order, the last assignment of a symbol winning:
  kconfig/security-hardened.config, kconfig/<arch>.config, kconfig/streams/<stream>.config,
  then each extra fragment. The output is byte-reproducible: sorted by symbol, no timestamp.
EOF
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --arch=*)
        ARCH="${1#*=}"
        shift
        ;;
      --stream=*)
        STREAM="${1#*=}"
        shift
        ;;
      --output=*)
        OUTPUT_FILE="${1#*=}"
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
        EXTRA_FRAGMENTS+=("$1")
        shift
        ;;
    esac
  done
}

# Succeeds when versions.json lists <value> under <key>, a stream or an architecture.
declared_in_versions() {
  python3 -c 'import json, sys; sys.exit(sys.argv[2] not in json.load(open("versions.json"))[sys.argv[1]])' "$1" "$2"
}

validate_environment() {
  if [[ ! -f "versions.json" ]]; then
    echo "Error: versions.json not found; run from the repository root" >&2
    return 1
  fi
  if ! declared_in_versions architectures "${ARCH}"; then
    echo "Error: Unsupported target architecture: ${ARCH} (not in versions.json)" >&2
    return 1
  fi
  if [[ ! -f "kconfig/${ARCH}.config" ]]; then
    echo "Error: Architecture fragment kconfig/${ARCH}.config not found" >&2
    return 1
  fi
  if [[ ! -f "kconfig/security-hardened.config" ]]; then
    echo "Error: Security baseline kconfig/security-hardened.config not found" >&2
    return 1
  fi
  # A stream must be one versions.json publishes: an unknown name would silently merge
  # no stream fragment, and the name is used to build a path.
  if [[ -n "${STREAM}" ]] && ! declared_in_versions streams "${STREAM}"; then
    echo "Error: Unknown release stream '${STREAM}' (not in versions.json)" >&2
    return 1
  fi
}

# Prints the fragments to merge, one per line, in merge order.
# scripts/verify_kernel_requirement.py reproduces this order; keep the two in step.
fragment_sources() {
  echo "kconfig/security-hardened.config"
  echo "kconfig/${ARCH}.config"
  if [[ -n "${STREAM}" && -f "kconfig/streams/${STREAM}.config" ]]; then
    echo "kconfig/streams/${STREAM}.config"
  fi
  local extra
  for extra in "${EXTRA_FRAGMENTS[@]}"; do
    if [[ -f "${extra}" ]]; then
      echo "${extra}"
    fi
  done
}

merge_fragments() {
  local target_out="${1}"
  local -a sources
  mapfile -t sources < <(fragment_sources)

  local src
  for src in "${sources[@]}"; do
    echo "==> Merging fragment: ${src}"
  done

  local body
  if ! body="$(awk "${MERGE_AWK}" "${sources[@]}" | LC_ALL=C sort -t $'\t' -k1,1 | cut -f2-)"; then
    echo "Error: a fragment carries a line that is not kconfig; nothing was written" >&2
    return 1
  fi

  mkdir -p "$(dirname "${target_out}")"
  {
    echo "# Generated by nucleus scripts/merge-config.sh"
    echo "# Architecture: ${ARCH}"
    echo "# Stream: ${STREAM:-none}"
    echo "# Fragments: ${sources[*]}"
    if [[ -n "${body}" ]]; then
      printf '%s\n' "${body}"
    fi
  } > "${target_out}"
}

verify_security_symbols() {
  local config_path="${1}"
  local -a mandatory_symbols=(
    "CONFIG_STRICT_KERNEL_RWX=y"
    "CONFIG_STACKPROTECTOR_STRONG=y"
    "CONFIG_RANDOMIZE_BASE=y"
    "CONFIG_VMAP_STACK=y"
    "CONFIG_SLAB_FREELIST_HARDENED=y"
  )

  for sym in "${mandatory_symbols[@]}"; do
    if ! grep -qxF "${sym}" "${config_path}"; then
      echo "Error: Mandatory security symbol '${sym}' missing from merged config" >&2
      return 1
    fi
  done
  echo "    ✓ Security baseline symbols verified in ${config_path}"
}

main() {
  parse_arguments "$@"
  validate_environment

  if [[ -z "${OUTPUT_FILE}" ]]; then
    OUTPUT_FILE="output/.config-${ARCH}"
  fi

  local target="architecture '${ARCH}'"
  if [[ -n "${STREAM}" ]]; then
    target+=", stream '${STREAM}'"
  fi
  echo "==> Starting KConfig merge for ${target}..."
  if [[ "${DRY_RUN}" == "true" ]]; then
    local tmp_check
    tmp_check="$(mktemp)"
    merge_fragments "${tmp_check}"
    verify_security_symbols "${tmp_check}"
    rm -f "${tmp_check}"
    echo "    ✓ [DRY-RUN] KConfig merge and validation succeeded."
    return 0
  fi

  merge_fragments "${OUTPUT_FILE}"
  verify_security_symbols "${OUTPUT_FILE}"
  echo "==> Successfully written merged configuration to ${OUTPUT_FILE}"
}

main "$@"
