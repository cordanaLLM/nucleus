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
# scripts/fetch-kernel-requirements.sh — fetch every kernel requirement document that
# versions.json downstream.requirements declares, each pinned to one commit, and write the
# arguments scripts/verify_kernel_requirement.py checks them with (<out>/args).
#
# Event data arrives through the environment only and is never evaluated:
#   EVENT_NAME              github.event_name
#   PAYLOAD_SOURCE          client_payload.source             (repository_dispatch only)
#   PAYLOAD_SCHEMA          client_payload.schema
#   PAYLOAD_PATH            client_payload.requirement.path
#   PAYLOAD_REF             client_payload.requirement.ref
#   PAYLOAD_SHA256          client_payload.requirement.sha256
#   PAYLOAD_CORRELATION_ID  client_payload.correlation_id
# A repository_dispatch pins the dispatched row whose label equals PAYLOAD_SOURCE to
# PAYLOAD_REF, and the verifier proves PAYLOAD_SHA256 and PAYLOAD_CORRELATION_ID against the
# document before it is used. It is refused only when no dispatched row carries that label.
# Every other row is read at the commit its default branch resolves to at fetch time.
# Complies with NASA/JPL Power of 10: functions <= 60 lines, checked returns.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
VERSIONS="versions.json"
OUT_DIR="requirements"

show_usage() {
  cat <<'EOF'
Usage: fetch-kernel-requirements.sh [--versions=<path>] [--out=<dir>]
  --versions=<path>  versions.json to read downstream.requirements from [default: versions.json]
  --out=<dir>        directory for the documents and the verifier arguments [default: requirements]
Needs gh, authenticated through GH_TOKEN. Reads the dispatch payload from PAYLOAD_* variables.
EOF
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --versions=*)
        VERSIONS="${1#*=}"
        shift
        ;;
      --out=*)
        OUT_DIR="${1#*=}"
        shift
        ;;
      -h|--help)
        show_usage
        exit 0
        ;;
      *)
        echo "Error: Unknown option $1" >&2
        exit 1
        ;;
    esac
  done
}

# A payload value as a log line may carry it: bounded, and any character outside a
# conservative set replaced, so a payload cannot inject a workflow command into the log.
shown() {
  local value="${1:0:80}"
  printf '%s' "${value//[^A-Za-z0-9._:\/-]/?}"
}

refuse() {
  echo "::error::${1}" >&2
  exit 1
}

# Prints one tab-separated row per declared requirement: label, repository, path, dispatched.
requirement_rows() {
  python3 - "${VERSIONS}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    rows = json.load(handle)["downstream"]["requirements"]
for row in rows:
    print(row["label"], row["repository"], row["path"], str(row["dispatched"]).lower(), sep="\t")
PY
}

# Validates the dispatch payload and prints the label of the dispatched row it names.
dispatched_label() {
  local rows="$1" schema label repo path dispatched match=""
  schema="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import verify_kernel_requirement as v; print(v.SCHEMA)' "${SCRIPT_DIR}")"
  while IFS=$'\t' read -r label repo path dispatched; do
    if [[ "${dispatched}" == "true" && "${label}" == "${PAYLOAD_SOURCE:-}" ]]; then
      match="${label}"
      if [[ "${PAYLOAD_PATH:-}" != "${path}" ]]; then
        refuse "dispatched path '$(shown "${PAYLOAD_PATH:-}")' is not ${path}, which versions.json declares for ${label} (${repo})"
      fi
    fi
  done <<< "${rows}"
  if [[ -z "${match}" ]]; then
    refuse "dispatch source '$(shown "${PAYLOAD_SOURCE:-}")' matches no dispatched requirement in ${VERSIONS}"
  fi
  if [[ "${PAYLOAD_SCHEMA:-}" != "${schema}" ]]; then
    refuse "dispatched schema '$(shown "${PAYLOAD_SCHEMA:-}")' is not ${schema}"
  fi
  if [[ ! "${PAYLOAD_REF:-}" =~ ^[0-9a-f]{40}$ ]]; then
    refuse "dispatched ref '$(shown "${PAYLOAD_REF:-}")' is not a full commit SHA"
  fi
  if [[ ! "${PAYLOAD_SHA256:-}" =~ ^[0-9a-f]{64}$ ]]; then
    refuse "dispatched sha256 '$(shown "${PAYLOAD_SHA256:-}")' is not a lower-case SHA-256 digest"
  fi
  if [[ ! "${PAYLOAD_CORRELATION_ID:-}" =~ ^[A-Za-z0-9._:-]{1,128}$ ]]; then
    refuse "dispatched correlation_id '$(shown "${PAYLOAD_CORRELATION_ID:-}")' is missing or malformed"
  fi
  echo "${match}"
}

# Prints the commit the default branch of <repository> points at now.
default_branch_commit() {
  local repo="$1" sha
  if ! sha="$(gh api "repos/${repo}/commits/HEAD" --jq .sha)"; then
    refuse "could not resolve the default branch of ${repo}"
  fi
  if [[ ! "${sha}" =~ ^[0-9a-f]{40}$ ]]; then
    refuse "the default branch of ${repo} resolved to '$(shown "${sha}")', not a commit"
  fi
  echo "${sha}"
}

# Fetches <path> of <repository> at <ref> into <out>/<label>.json and records it for the verifier.
fetch_document() {
  local label="$1" repo="$2" path="$3" ref="$4" target digest
  target="${OUT_DIR}/${label}.json"
  if ! gh api -H "Accept: application/vnd.github.raw+json" "repos/${repo}/contents/${path}?ref=${ref}" > "${target}"; then
    refuse "could not fetch ${repo}/${path} at ${ref}"
  fi
  digest="$(sha256sum "${target}" | cut -d' ' -f1)"
  echo "==> ${label}: ${repo}/${path} at ${ref}, sha256 ${digest}"
  {
    echo "--requirement=${label}=${target}"
    echo "--ref=${label}=${ref}"
  } >> "${OUT_DIR}/args"
}

main() {
  parse_arguments "$@"
  local rows label repo path ref pinned=""
  rows="$(requirement_rows)"
  if [[ -z "${rows}" ]]; then
    refuse "${VERSIONS} declares no downstream requirement"
  fi
  mkdir -p "${OUT_DIR}"
  : > "${OUT_DIR}/args"
  if [[ "${EVENT_NAME:-}" == "repository_dispatch" ]]; then
    pinned="$(dispatched_label "${rows}")"
  fi
  while IFS=$'\t' read -r label repo path _; do
    if [[ -n "${pinned}" && "${label}" == "${pinned}" ]]; then
      ref="${PAYLOAD_REF}"
      echo "==> ${label}: pinned by the dispatch to ${ref}; sha256 ${PAYLOAD_SHA256} is proven before parsing"
      {
        echo "--sha256=${label}=${PAYLOAD_SHA256}"
        echo "--correlation-id=${label}=${PAYLOAD_CORRELATION_ID}"
      } >> "${OUT_DIR}/args"
    else
      ref="$(default_branch_commit "${repo}")"
    fi
    fetch_document "${label}" "${repo}" "${path}" "${ref}"
  done <<< "${rows}"
}

main "$@"
