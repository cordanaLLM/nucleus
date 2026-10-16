#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/audit-repository-health.sh — Repository Health & Quality Gate Audit
# Complies with NASA/JPL Power of 10: short functions (<= 60 lines), checked returns.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

audit_privacy_and_secrets() {
  echo "==> [1/7] Auditing privacy and zero-leak invariants..."
  local leak_count=0

  while IFS= read -r file; do
    if [[ "${file}" == *.md || "${file}" == docs/* || "${file}" == tests/* ]]; then
      continue
    fi
    if grep -nE '\b(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[0-1])\.[0-9]{1,3}\.[0-9]{1,3})\b' "${ROOT_DIR}/${file}" >/dev/null 2>&1; then
      echo "    Error: RFC 1918 IP address detected in ${file}"
      leak_count=$((leak_count + 1))
    fi
  done < <(git -C "${ROOT_DIR}" ls-files)

  while IFS= read -r file; do
    if [[ "${file}" == tests/* || "${file}" == *.pyc ]]; then
      continue
    fi
    if grep -nE '/(home|Users)/[a-zA-Z0-9_-]+/(dev|workspace|src)' "${ROOT_DIR}/${file}" >/dev/null 2>&1; then
      echo "    Error: Local workstation user path detected in ${file}"
      leak_count=$((leak_count + 1))
    fi
  done < <(git -C "${ROOT_DIR}" ls-files)

  if [ "${leak_count}" -ne 0 ]; then
    echo "    Privacy audit failed with ${leak_count} violation(s)."
    return 1
  fi
  echo "    ✓ Privacy & zero-leak audit passed."
}

audit_license_headers() {
  echo "==> [2/7] Auditing copyright license headers on scripts..."
  local missing=0
  while IFS= read -r -d '' script; do
    if ! grep -q "Copyright 2026 The Lusoris Authors" "${script}"; then
      echo "    Error: Missing copyright license header in ${script}"
      missing=$((missing + 1))
    fi
  done < <(find "${ROOT_DIR}/scripts" "${ROOT_DIR}/.agents/hooks-scripts" -type f \( -name "*.sh" -o -name "*.py" \) -print0)

  if [ "${missing}" -ne 0 ]; then
    return 1
  fi
  echo "    ✓ All shell and automation scripts carry required license headers."
}

audit_executable_permissions() {
  echo "==> [3/7] Auditing script executable permissions..."
  local missing=0
  while IFS= read -r -d '' script; do
    if [ ! -x "${script}" ]; then
      echo "    Error: ${script} is not executable"
      missing=$((missing + 1))
    fi
  done < <(find "${ROOT_DIR}/scripts" -type f -name "*.sh" -print0)
  if [ "${missing}" -ne 0 ]; then
    return 1
  fi
  echo "    ✓ Script permissions verified."
}

# An audit stage whose tool is missing fails and names it; it never passes by skipping.
require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "    Error: $1 is not installed; this stage cannot run."
    return 1
  fi
}

audit_manifest_schema() {
  echo "==> [4/7] Auditing versions.json against versions.schema.json..."
  python3 -c "
import json, jsonschema, sys
with open(sys.argv[1], encoding='utf-8') as sf, open(sys.argv[2], encoding='utf-8') as mf:
    schema = json.load(sf)
    manifest = json.load(mf)
    jsonschema.validate(instance=manifest, schema=schema)
" "${ROOT_DIR}/versions.schema.json" "${ROOT_DIR}/versions.json" || {
    echo "    Error: versions.json failed schema validation!"
    return 1
  }
  echo "    ✓ versions.json schema valid."
}

audit_stream_synchrony() {
  echo "==> [5/7] Auditing stream synchrony across versions.json, docs, and build scripts..."
  local streams
  if ! streams="$(python3 "${ROOT_DIR}/scripts/versions_query.py" --versions="${ROOT_DIR}/versions.json" list streams)"; then
    echo "    Error: Failed to query streams from versions.json" >&2
    return 1
  fi

  for s in ${streams}; do
    if ! python3 "${ROOT_DIR}/scripts/versions_query.py" --versions="${ROOT_DIR}/versions.json" source "${s}" >/dev/null; then
      echo "    Error: Stream '${s}' does not resolve to a buildable source (scripts/versions_query.py)"
      return 1
    fi
    if ! grep -qi "${s}" "${ROOT_DIR}/docs/streams.md"; then
      echo "    Error: Stream '${s}' not documented in docs/streams.md"
      return 1
    fi
  done
  echo "    ✓ All streams (${streams//$'\n'/ }) synchronized across manifest, build source, and docs."
}

audit_static_linters() {
  echo "==> [6/7] Running ShellCheck and Yamllint..."
  require_tool shellcheck
  shellcheck "${ROOT_DIR}/scripts/"*.sh
  echo "    ✓ ShellCheck passed with zero warnings."

  require_tool yamllint
  yamllint -c "${ROOT_DIR}/.yamllint.yml" "${ROOT_DIR}"
  echo "    ✓ Yamllint passed."
}

audit_actionlint() {
  echo "==> [7/7] Auditing GitHub Actions workflows with actionlint..."
  require_tool actionlint
  local wf_count
  wf_count=$(find "${ROOT_DIR}/.github/workflows" -maxdepth 1 \( -name "*.yml" -o -name "*.yaml" \) | wc -l)
  if [ "${wf_count}" -eq 0 ]; then
    echo "    Error: no workflows under .github/workflows"
    return 1
  fi
  actionlint "${ROOT_DIR}/.github/workflows/"*.yml
  echo "    ✓ Actionlint passed across ${wf_count} workflow(s)."
}

main() {
  echo "=========================================================="
  echo " Lusoris Kernel Forge — Repository Health Quality Audit"
  echo "=========================================================="
  audit_privacy_and_secrets
  audit_license_headers
  audit_executable_permissions
  audit_manifest_schema
  audit_stream_synchrony
  audit_static_linters
  audit_actionlint
  echo "=========================================================="
  echo " ✓ REPOSITORY HEALTH AUDIT: ALL QUALITY GATES PASSED"
  echo "=========================================================="
}

main "$@"
