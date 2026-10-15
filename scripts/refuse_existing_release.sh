#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/refuse_existing_release.sh — refuse execution if a GitHub release already exists for TAG.
# Packages are not reproducible (module signatures and debian/changelog date differ per build),
# so re-running a release for an existing tag would upload differing digests under the same tag
# via softprops/action-gh-release, breaking any downstream consumer pinned to the original digests.

set -euo pipefail

REPO=""
TAG=""
GH_ERR_FILE=""

cleanup() {
  if [[ -n "${GH_ERR_FILE}" && -f "${GH_ERR_FILE}" ]]; then
    rm -f "${GH_ERR_FILE}"
  fi
}
trap cleanup EXIT

show_usage() {
  cat <<'EOF'
Usage: refuse_existing_release.sh --repo OWNER/NAME --tag TAG

Refuses execution if a GitHub release already exists for TAG in OWNER/NAME.
Exit status:
  0: No release exists (gh reports "release not found").
  1: A release already exists (emits ::error::), or gh failed (auth/network, fail closed).
EOF
}

refuse() {
  echo "Refused: $*" >&2
  exit 1
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo)
        [[ $# -ge 2 ]] || refuse "--repo requires an argument"
        REPO="$2"
        shift
        ;;
      --repo=*)
        REPO="${1#*=}"
        ;;
      --tag)
        [[ $# -ge 2 ]] || refuse "--tag requires an argument"
        TAG="$2"
        shift
        ;;
      --tag=*)
        TAG="${1#*=}"
        ;;
      -h | --help)
        show_usage
        exit 0
        ;;
      *)
        show_usage >&2
        refuse "unknown option $1"
        ;;
    esac
    shift
  done
}

validate_arguments() {
  [[ -n "${REPO}" ]] || refuse "--repo OWNER/NAME is required"
  [[ -n "${TAG}" ]] || refuse "--tag TAG is required"
  command -v gh >/dev/null 2>&1 || refuse "gh CLI is required and not installed"
}

# gh 2.102 prints exactly "release not found" and exits 1 for a tag without a release; any
# other failure (authentication, network, rate limit) is not proof of absence and refuses.
check_existing_release() {
  local assets="" gh_err="" status=0
  GH_ERR_FILE="$(mktemp)"

  set +e
  assets="$(gh release view "${TAG}" --repo "${REPO}" --json assets --jq '.assets | length' 2>"${GH_ERR_FILE}")"
  status=$?
  set -e
  gh_err="$(cat "${GH_ERR_FILE}")"

  if [[ ${status} -eq 0 ]]; then
    echo "::error::Release for tag '${TAG}' already exists in ${REPO} with ${assets} asset(s). Packages are not reproducible; cut revision lusoris<N+1> to release again. Deleting the release is the operator's act." >&2
    exit 1
  fi
  if [[ "${gh_err}" == "release not found" ]]; then
    echo "No release exists for tag '${TAG}' in repository ${REPO}; proceeding."
    return 0
  fi
  echo "Error: gh release view failed for tag '${TAG}' in repository ${REPO} (fail closed):" >&2
  echo "${gh_err:-gh exited with status ${status}}" >&2
  exit 1
}

main() {
  parse_arguments "$@"
  validate_arguments
  check_existing_release
}

main "$@"
