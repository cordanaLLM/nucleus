#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/lib/date-from-epoch.sh — date wrapper for reproducible debian/changelog.
# When invoked with exactly `-R` (as scripts/package/mkdebian does), delegates to the
# real date using SOURCE_DATE_EPOCH in UTC; passes all other invocations through unchanged.
set -euo pipefail

refuse() {
  echo "Refused: $*" >&2
  exit 1
}

# Resolve the real date binary without a PATH search that could recurse into this wrapper.
resolve_real_date() {
  if [[ -n "${REAL_DATE:-}" ]]; then
    echo "${REAL_DATE}"
    return 0
  fi
  if [[ -x "/usr/bin/date" ]]; then
    echo "/usr/bin/date"
    return 0
  fi
  if [[ -x "/bin/date" ]]; then
    echo "/bin/date"
    return 0
  fi
  refuse "real date binary not found"
}

main() {
  local real_date
  real_date="$(resolve_real_date)"

  if [[ $# -eq 1 && "$1" == "-R" ]]; then
    if [[ -z "${SOURCE_DATE_EPOCH:-}" || ! "${SOURCE_DATE_EPOCH}" =~ ^[0-9]+$ ]]; then
      refuse "SOURCE_DATE_EPOCH must be set to digits, got '${SOURCE_DATE_EPOCH:-}'"
    fi
    exec "${real_date}" -u -R -d "@${SOURCE_DATE_EPOCH}"
  fi

  exec "${real_date}" "$@"
}

main "$@"
