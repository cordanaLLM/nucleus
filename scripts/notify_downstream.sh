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

set -euo pipefail

# Manual counterpart of the dispatch step in publish-release.yml. It sends the same payload
# for a kernel release that already exists. Nothing is defaulted: the tag must be a kernel
# release tag that scripts/resolve_release_tag.py accepts, and a live dispatch needs a token.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STREAM="${1:-}"
VERSION="${2:-}"
DRY_RUN="${3:-false}"
# Release tag the downstream verifier downloads; imago refuses a payload without it.
RELEASE_TAG="${RELEASE_TAG:-}"
TARGET_REPO="cordanaLLM/imago"
EVENT_TYPE="kernel_release_published"
RESOLVED=""

usage() {
  echo "Usage: RELEASE_TAG=v<version>-<stream>-lusoris<N> $0 <stream> <version>-lusoris<N> [true]" >&2
  echo "       (a third argument of 'true' prints the dispatch without sending it)" >&2
  exit 1
}

resolved_value() {
  printf '%s\n' "${RESOLVED}" | sed -n "s/^$1=//p"
}

require_match() {
  local label="$1" given="$2" key="$3" expected
  expected="$(resolved_value "${key}")"
  if [[ "${given}" != "${expected}" ]]; then
    echo "Error: ${label} '${given}' does not match '${expected}', which RELEASE_TAG '${RELEASE_TAG}' resolves to." >&2
    exit 1
  fi
}

validate_parameters() {
  if [[ -z "${STREAM}" || -z "${VERSION}" || -z "${RELEASE_TAG}" ]]; then
    usage
  fi
  # The resolver reports its own refusal on stderr.
  RESOLVED="$(python3 "${SCRIPT_DIR}/resolve_release_tag.py" --tag "${RELEASE_TAG}")" || exit 1
  require_match "Stream" "${STREAM}" "stream"
  require_match "Version" "${VERSION}" "release_version"
}

send_dispatch() {
  echo "==> Dispatching downstream release event to ${TARGET_REPO}..."
  echo "    Stream:  ${STREAM}"
  echo "    Version: ${VERSION}"
  echo "    Tag:     ${RELEASE_TAG}"

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY-RUN] Would dispatch '${EVENT_TYPE}' to ${TARGET_REPO}"
    return 0
  fi

  if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    echo "Error: GITHUB_TOKEN is not set; a live dispatch needs a token that may send" >&2
    echo "       repository_dispatch events to ${TARGET_REPO}. Nothing was sent." >&2
    exit 1
  fi

  gh api "repos/${TARGET_REPO}/dispatches" \
    --raw-field event_type="${EVENT_TYPE}" \
    --field client_payload[stream]="${STREAM}" \
    --field client_payload[version]="${VERSION}" \
    --field client_payload[tag]="${RELEASE_TAG}"
  echo "==> Dispatch successfully sent."
}

main() {
  validate_parameters
  send_dispatch
}

main "$@"
