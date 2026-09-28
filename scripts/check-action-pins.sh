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
# scripts/check-action-pins.sh — verify every SHA-pinned action against its upstream.
#
# Every `uses: owner/repo[/path]@<40-hex> # <tag>` in .github/workflows must name a
# tag of owner/repo that points to exactly the pinned commit (annotated tags are
# dereferenced). A tag that points elsewhere is reported with the commit it does
# point to, and a pinned commit that does not exist upstream is reported as such.
# A remote `uses:` without a full commit SHA and a `# <tag>` comment fails; local
# actions (./...) are skipped. One line per pin; exit 1 when any pin fails.
#
# Needs the gh CLI with a token (GH_TOKEN) and network access, which is why it is
# `make lint-pins` and not part of the offline `make lint`.
# Complies with NASA/JPL Power of 10: short functions (<= 60 lines), bounded loops.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOWS_DIR="${ROOT_DIR}/.github/workflows"
readonly MAX_USES=500
readonly MAX_TAG_DEPTH=4
readonly PIN_RE='^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(/[^@[:space:]]+)?@([0-9a-f]{40})[[:space:]]+#[[:space:]]*([A-Za-z0-9._/+-]+)'

declare -A PIN_SITES=()
declare -a PIN_KEYS=()
declare -a BAD_USES=()
declare -a LOCAL_USES=()

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --workflows-dir=*)
        WORKFLOWS_DIR="${1#*=}"
        shift
        ;;
      -h | --help)
        echo "Usage: $0 [--workflows-dir=<dir>]"
        exit 0
        ;;
      *)
        echo "Error: Unknown option $1" >&2
        exit 2
        ;;
    esac
  done
}

require_environment() {
  if ! command -v gh >/dev/null 2>&1; then
    echo "Error: the gh CLI is required to query GitHub; pins cannot be verified without it" >&2
    exit 1
  fi
  if [[ ! -d "${WORKFLOWS_DIR}" ]]; then
    echo "Error: workflow directory '${WORKFLOWS_DIR}' does not exist" >&2
    exit 1
  fi
}

# Records one `uses:` value found at <file>:<line>.
record_use() {
  local value="$1" site="$2" key
  value="${value//\"/}"
  value="${value//\'/}"
  value="${value#"${value%%[![:space:]]*}"}"
  if [[ "${value}" == ./* ]]; then
    LOCAL_USES+=("${value%%[[:space:]]*}  [${site}]")
    return 0
  fi
  if [[ ! "${value}" =~ ${PIN_RE} ]]; then
    BAD_USES+=("${value}  [${site}]")
    return 0
  fi
  key="${BASH_REMATCH[1]} ${BASH_REMATCH[1]}${BASH_REMATCH[2]} ${BASH_REMATCH[3]} ${BASH_REMATCH[4]}"
  if [[ -z "${PIN_SITES[${key}]+set}" ]]; then
    PIN_KEYS+=("${key}")
    PIN_SITES["${key}"]="${site}"
  else
    PIN_SITES["${key}"]+=" ${site}"
  fi
}

collect_uses() {
  local file line_no text count=0
  local -a files=()
  mapfile -t files < <(find "${WORKFLOWS_DIR}" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "Error: no workflow files in ${WORKFLOWS_DIR}" >&2
    exit 1
  fi
  for file in "${files[@]}"; do
    while IFS=: read -r line_no text; do
      count=$((count + 1))
      if [[ ${count} -gt ${MAX_USES} ]]; then
        echo "Error: more than ${MAX_USES} uses: lines; refusing to continue" >&2
        exit 1
      fi
      record_use "${text#*uses:}" "$(basename "${file}"):${line_no}"
    done < <(grep -nE '^[[:space:]]*(-[[:space:]]+)?uses:' "${file}" || true)
  done
}

# Runs `gh api <endpoint> --jq <expr>`; on failure prints a one-line reason and returns 1.
gh_query() {
  local endpoint="$1" expr="$2" out
  local http_re='\(HTTP ([0-9]{3})\)'
  if out="$(gh api "${endpoint}" --jq "${expr}" 2>&1)"; then
    echo "${out}"
    return 0
  fi
  if [[ "${out}" =~ ${http_re} ]]; then
    echo "HTTP ${BASH_REMATCH[1]} from ${endpoint}"
  else
    echo "gh api ${endpoint} failed: ${out%%$'\n'*}"
  fi
  return 1
}

# Prints the commit that tags/<tag> of <repo> points to, following annotated tags.
resolve_tag() {
  local repo="$1" tag="$2" object depth
  local expr='.object.type + " " + .object.sha'
  if ! object="$(gh_query "repos/${repo}/git/ref/tags/${tag}" "${expr}")"; then
    echo "${object}"
    return 1
  fi
  for ((depth = 0; depth < MAX_TAG_DEPTH; depth++)); do
    if [[ "${object}" != "tag "* ]]; then
      break
    fi
    if ! object="$(gh_query "repos/${repo}/git/tags/${object#tag }" "${expr}")"; then
      echo "${object}"
      return 1
    fi
  done
  if [[ ! "${object}" =~ ^commit\ ([0-9a-f]{40})$ ]]; then
    echo "does not resolve to a commit: ${object}"
    return 1
  fi
  echo "${BASH_REMATCH[1]}"
}

# Verifies one pin and prints its line; returns 1 when the pin fails.
check_pin() {
  local key="$1" repo action sha tag sites tag_sha commit_note
  read -r repo action sha tag <<<"${key}"
  sites="${PIN_SITES[${key}]}"
  # A tag of the upstream repository that points to the pinned commit proves the commit
  # exists there, so the commit itself is only queried to explain a failure.
  if tag_sha="$(resolve_tag "${repo}" "${tag}")" && [[ "${tag_sha}" == "${sha}" ]]; then
    echo "OK    ${action}@${sha} # ${tag}  [${sites}]"
    return 0
  fi
  if commit_note="$(gh_query "repos/${repo}/commits/${sha}" '.sha')" && [[ "${commit_note}" == "${sha}" ]]; then
    commit_note="the pinned commit exists upstream"
  else
    commit_note="the pinned commit does not exist upstream (${commit_note})"
  fi
  if [[ "${tag_sha}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "FAIL  ${action}@${sha} # ${tag}: tag ${tag} points to ${tag_sha}, not to the pin; ${commit_note}  [${sites}]"
  else
    echo "FAIL  ${action}@${sha} # ${tag}: tag ${tag} not found (${tag_sha}); ${commit_note}  [${sites}]"
  fi
  return 1
}

main() {
  parse_args "$@"
  require_environment
  collect_uses
  local key entry failures=0
  echo "==> Verifying action pins in ${WORKFLOWS_DIR#"${ROOT_DIR}/"} against their upstream tags..."
  for key in "${PIN_KEYS[@]}"; do
    check_pin "${key}" || failures=$((failures + 1))
  done
  for entry in "${BAD_USES[@]}"; do
    echo "FAIL  ${entry}: not pinned as owner/repo@<40-hex commit> # <tag>"
    failures=$((failures + 1))
  done
  for entry in "${LOCAL_USES[@]}"; do
    echo "SKIP  ${entry}: local action"
  done
  echo "==> ${#PIN_KEYS[@]} pin(s) checked, ${#BAD_USES[@]} unpinned use(s), ${failures} failure(s)"
  if [[ ${failures} -gt 0 ]]; then
    exit 1
  fi
}

main "$@"
