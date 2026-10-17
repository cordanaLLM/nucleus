#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/lint-pins.sh — verify every action and image pin in .github/workflows with zizmor.
#
# Runs zizmor (pinned in requirements-test.txt) with the pedantic persona and keeps only its
# pin audits: ref-version-mismatch (the `# <tag>` comment is missing or names a tag that
# does not point to the pinned commit), unpinned-uses (a remote `uses:` without a full
# commit SHA), unpinned-images (a container image without a sha256 digest) and
# impostor-commit (a pinned commit that the named repository does not contain). Other
# zizmor findings are not this gate's subject. Needs a GitHub token in GH_TOKEN; exit 1 on
# any pin finding or when zizmor itself fails.
set -euo pipefail

PIN_AUDITS='["ref-version-mismatch","unpinned-uses","unpinned-images","impostor-commit"]'

main() {
  local target="${1:-.github/workflows}" out rc=0
  command -v zizmor >/dev/null || {
    echo "lint-pins: zizmor is not installed" >&2
    return 1
  }
  command -v jq >/dev/null || {
    echo "lint-pins: jq is not installed" >&2
    return 1
  }
  out=$(zizmor --persona pedantic --format json --no-progress "$target") || rc=$?
  case "$rc" in
    0 | 11 | 12 | 13 | 14) ;;
    *)
      echo "lint-pins: zizmor failed with exit code $rc" >&2
      return 1
      ;;
  esac
  report "$out"
}

report() {
  local n
  n=$(jq --argjson ids "$PIN_AUDITS" '[.[] | select(.ident as $i | $ids | index($i))] | length' <<<"$1")
  jq -r --argjson ids "$PIN_AUDITS" '.[] | select(.ident as $i | $ids | index($i))
		| .locations[0] as $l
		| "\($l.symbolic.key.Local.verbatim_path // "?"):\($l.concrete.location.start_point.row + 1): \(.ident): \(.desc)"' <<<"$1"
  echo "lint-pins: ${n} pin finding(s)"
  [[ "$n" == 0 ]]
}

main "$@"
