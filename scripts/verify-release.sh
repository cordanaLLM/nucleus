#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/verify-release.sh — verify the assets of a published kernel release, as a consumer does.
#
# Usage: verify-release.sh <asset-dir> <release-tag> <owner/repository>
#
# <asset-dir> holds every asset of the release <release-tag> (gh release download). The script
# refuses, with exit status 1:
#   - a tag that is not a kernel release tag (v<version>-<stream>-lusoris<N>);
#   - a missing SHA256SUMS, SHA256SUMS.bundle or kernel-<stream>.manifest.json;
#   - an asset that SHA256SUMS does not list (besides those three), or a listed one that is
#     missing or differs (sha256sum --check --strict);
#   - a cosign bundle that is not a keyless signature over SHA256SUMS by the release workflow
#     of <owner/repository> at refs/tags/<release-tag>;
#   - a manifest whose tag, signer identity or SHA256SUMS digest is not the verified one.
# It writes nothing.
set -euo pipefail

readonly OIDC_ISSUER="https://token.actions.githubusercontent.com"

die() {
  echo "verify-release: $*" >&2
  exit 1
}

check_args() {
  local dir="$1" tag="$2" repo="$3"
  [[ -d "${dir}" ]] || die "no asset directory: ${dir}"
  [[ "${tag}" =~ ^v[0-9][0-9A-Za-z.~-]*-[a-z]+-lusoris[1-9][0-9]{0,3}$ ]] \
    || die "not a kernel release tag: ${tag}"
  [[ "${repo}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "not an owner/repository: ${repo}"
  local stream="${tag%-lusoris*}"
  stream="${stream##*-}"
  local required
  for required in SHA256SUMS SHA256SUMS.bundle "kernel-${stream}.manifest.json"; do
    [[ -s "${dir}/${required}" ]] || die "missing or empty asset: ${required}"
  done
}

# Every asset is listed in SHA256SUMS, apart from the checksum file, its bundle and the
# manifest, which the release workflow writes after the checksums.
check_listing() {
  local dir="$1" stream="$2"
  local -a listed=()
  local line name
  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    name="${line#* }"
    listed+=("${name#[ *]}")
  done <"${dir}/SHA256SUMS"
  [[ "${#listed[@]}" -gt 0 ]] || die "SHA256SUMS lists no file"
  local path
  for path in "${dir}"/*; do
    name="$(basename "${path}")"
    case "${name}" in
      SHA256SUMS | SHA256SUMS.bundle | "kernel-${stream}.manifest.json") continue ;;
    esac
    printf '%s\n' "${listed[@]}" | grep -Fqx -- "${name}" \
      || die "asset not listed in SHA256SUMS: ${name}"
  done
  (cd "${dir}" && sha256sum --check --strict --quiet SHA256SUMS) \
    || die "an asset does not match SHA256SUMS"
}

check_signature() {
  local dir="$1" identity="$2"
  cosign verify-blob \
    --bundle "${dir}/SHA256SUMS.bundle" \
    --certificate-identity "${identity}" \
    --certificate-oidc-issuer "${OIDC_ISSUER}" \
    "${dir}/SHA256SUMS" \
    || die "the cosign bundle is not a signature of ${identity} over SHA256SUMS"
}

check_manifest() {
  local manifest="$1" tag="$2" identity="$3" sums_digest="$4"
  local field expected
  for field in tag signer_identity; do
    expected="${tag}"
    [[ "${field}" == "tag" ]] || expected="${identity}"
    [[ "$(jq -er ".provenance.${field}" "${manifest}")" == "${expected}" ]] \
      || die "manifest provenance.${field} is not ${expected}"
  done
  [[ "$(jq -er '.checksums.sha256' "${manifest}")" == "${sums_digest}" ]] \
    || die "manifest checksums.sha256 is not the digest of the verified SHA256SUMS"
}

main() {
  [[ "$#" -eq 3 ]] || die "usage: verify-release.sh <asset-dir> <release-tag> <owner/repository>"
  local dir="$1" tag="$2" repo="$3"
  check_args "${dir}" "${tag}" "${repo}"
  local stream="${tag%-lusoris*}"
  stream="${stream##*-}"
  local identity="https://github.com/${repo}/.github/workflows/publish-release.yml@refs/tags/${tag}"
  check_listing "${dir}" "${stream}"
  check_signature "${dir}" "${identity}"
  local sums_digest
  sums_digest="$(sha256sum "${dir}/SHA256SUMS")"
  check_manifest "${dir}/kernel-${stream}.manifest.json" "${tag}" "${identity}" "${sums_digest%% *}"
  echo "verify-release: ${tag} verified (SHA256SUMS signed by ${identity}; every asset matches)"
}

main "$@"
