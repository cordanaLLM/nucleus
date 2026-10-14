#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/verify-reproducibility.sh — compares two build output directories byte for byte.
# A dry run states the comparison and gives no verdict; only two real directories do.
# Complies with NASA/JPL Power of 10: functions <= 60 lines, checked returns, bounded loops.
set -euo pipefail

BUILD_DIR_A=""
BUILD_DIR_B=""
DRY_RUN="false"
# A kernel build writes a few dozen files; more than this is not a build output directory.
MAX_ENTRIES=10000

show_usage() {
  cat <<'USAGE'
Usage: verify-reproducibility.sh --dir-a=<dir> --dir-b=<dir> [--dry-run]
Options:
  --dir-a=<dir>       First build output directory
  --dir-b=<dir>       Second build output directory, built from the same source
  --dry-run           State the comparison; compare nothing and give no verdict
  -h, --help          Show this help message
USAGE
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dir-a=*)
        BUILD_DIR_A="${1#*=}"
        shift
        ;;
      --dir-b=*)
        BUILD_DIR_B="${1#*=}"
        shift
        ;;
      --dry-run)
        DRY_RUN="true"
        shift
        ;;
      -h | --help)
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

# Refuses what would make a comparison pass without comparing builds.
check_directories() {
  if [[ -z "${BUILD_DIR_A}" || -z "${BUILD_DIR_B}" ]]; then
    echo "Error: both --dir-a and --dir-b are required." >&2
    return 1
  fi
  local dir
  for dir in "${BUILD_DIR_A}" "${BUILD_DIR_B}"; do
    if [[ ! -d "${dir}" ]]; then
      echo "Error: ${dir} is not a directory." >&2
      return 1
    fi
  done
  if [[ "$(realpath "${BUILD_DIR_A}")" == "$(realpath "${BUILD_DIR_B}")" ]]; then
    echo "Error: --dir-a and --dir-b are the same directory; that compares nothing." >&2
    return 1
  fi
}

# One line per entry: the SHA-256 of a regular file, the target of a link. Fails on an empty
# directory, on an entry that is neither, and on more than MAX_ENTRIES entries.
manifest() {
  local dir="${1}"
  local entries=()
  mapfile -d '' -t entries < <(cd "${dir}" && find . -mindepth 1 ! -type d -print0 | sort -z)
  if [[ "${#entries[@]}" -eq 0 ]]; then
    echo "Error: ${dir} holds no files." >&2
    return 1
  fi
  if [[ "${#entries[@]}" -gt "${MAX_ENTRIES}" ]]; then
    echo "Error: ${dir} holds more than ${MAX_ENTRIES} files." >&2
    return 1
  fi
  local entry sum
  for entry in "${entries[@]}"; do
    if [[ -L "${dir}/${entry}" ]]; then
      printf 'link %s %s\n' "$(readlink "${dir}/${entry}")" "${entry}"
    elif [[ -f "${dir}/${entry}" ]]; then
      sum="$(sha256sum "${dir}/${entry}")" || return 1
      printf 'file %s %s\n' "${sum%% *}" "${entry}"
    else
      echo "Error: ${dir}/${entry} is neither a file nor a link." >&2
      return 1
    fi
  done
}

# diffoscope explains a difference; it exits 1 when it finds one, anything else is its failure.
explain_difference() {
  if ! command -v diffoscope >/dev/null 2>&1; then
    echo "    diffoscope is not installed; no detailed comparison." >&2
    return 0
  fi
  echo "==> Running diffoscope..."
  local status=0
  diffoscope "${BUILD_DIR_A}" "${BUILD_DIR_B}" || status=$?
  if [[ "${status}" -gt 1 ]]; then
    echo "Error: diffoscope failed with status ${status}." >&2
  fi
}

compare_directories() {
  local manifest_a manifest_b
  manifest_a="$(manifest "${BUILD_DIR_A}")" || return 1
  manifest_b="$(manifest "${BUILD_DIR_B}")" || return 1
  echo "==> Comparing $(wc -l <<<"${manifest_a}") entries with $(wc -l <<<"${manifest_b}")..."
  if [[ "${manifest_a}" == "${manifest_b}" ]]; then
    echo "    ✓ ${BUILD_DIR_A} and ${BUILD_DIR_B} are byte-identical."
    return 0
  fi
  echo "Error: the builds differ:" >&2
  local status=0
  diff <(printf '%s\n' "${manifest_a}") <(printf '%s\n' "${manifest_b}") \
    | sed -n 's/^\([<>]\) [a-z]* [^ ]* /    \1 /p' | sort -u >&2 || status="${PIPESTATUS[0]}"
  if [[ "${status}" -gt 1 ]]; then
    echo "Error: diff failed with status ${status}." >&2
  fi
  explain_difference
  return 1
}

main() {
  parse_arguments "$@"
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "==> [DRY-RUN] Would compare every file under '${BUILD_DIR_A:-<dir-a>}' with '${BUILD_DIR_B:-<dir-b>}' by SHA-256, and links by target."
    echo "    No comparison ran; this is not a reproducibility result."
    return 0
  fi
  check_directories
  compare_directories
}

main "$@"
