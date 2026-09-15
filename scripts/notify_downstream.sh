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

STREAM="${1:-mainstream}"
VERSION="${2:-7.2.4-lusoris1}"
DRY_RUN="${3:-false}"
# Release tag the downstream verifier downloads; imago refuses a payload without it.
RELEASE_TAG="${RELEASE_TAG:-v${VERSION}}"
TARGET_REPO="cordanaLLM/imago"
EVENT_TYPE="kernel_release_published"

validate_parameters() {
  if [[ -z "${STREAM}" || -z "${VERSION}" || -z "${RELEASE_TAG}" ]]; then
    echo "Usage: [RELEASE_TAG=vX.Y.Z] $0 <stream> <version> [dry-run]" >&2
    exit 1
  fi
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
    echo "Warning: GITHUB_TOKEN not set; skipping live dispatch."
    return 0
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
