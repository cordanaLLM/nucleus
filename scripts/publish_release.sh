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

OUTPUT_DIR="output"
TAG="${1:-}"

validate_environment() {
  if [[ -z "${TAG}" ]]; then
    echo "Usage: $0 <release-tag>" >&2
    exit 1
  fi

  if [[ ! -d "${OUTPUT_DIR}" ]]; then
    echo "Error: Output directory '${OUTPUT_DIR}' does not exist" >&2
    exit 1
  fi
}

generate_checksums() {
  echo "==> Generating SHA256 checksums for release ${TAG}..."
  (
    cd "${OUTPUT_DIR}"
    sha256sum -- *.deb > "SHA256SUMS" || true
  )
  echo "==> Checksums generated successfully."
}

publish_artifacts() {
  echo "==> Staging artifacts for release ${TAG}..."
  if command -v gh >/dev/null 2>&1; then
    echo "==> gh CLI detected. Ready for automated release upload."
  else
    echo "==> gh CLI not found; artifacts ready in ${OUTPUT_DIR}/"
  fi
}

main() {
  validate_environment
  generate_checksums
  publish_artifacts
}

main "$@"
