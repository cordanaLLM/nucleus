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
# scripts/fetch-kernel-source.sh — fetch the kernel source of one stream and prove it before
# anything uses it. versions.json streams.<stream>.source says where it lives and how it is
# proven (docs/adr/0008-signed-kernel-sources-and-resolved-configuration.md):
#
#   tarball  the .tar.xz and its detached .tar.sign from kernel.org. The .tar.xz must match
#            the pinned sha256, and the signature, which covers the uncompressed tar, is
#            checked with `xz -cd <tar.xz> | gpgv --keyring <dearmored keyring> <sign> -`.
#   git-tag  a shallow fetch of one signed tag. `git verify-tag` checks it, the tag object
#            must name the tag, and the tag must point at the pinned commit. The tree is
#            exported with `git archive`.
#
# Either way the signature must be good and made by a key the stream lists in signers, read
# from keys/<FINGERPRINT>.asc, and the tree's top-level Makefile must name the release. Any
# other outcome is refused with exit status 1. The signature and the pin are checked before
# the tree is written, the release after it; no tree is left behind unless every check passed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
STREAM=""
DEST=""
WORK=""
VERSIONS="versions.json"
KEYS="keys"
TREE_WRITTEN="false"
declare -A SRC=()
declare -a SIGNERS=()

show_usage() {
  cat <<'EOF'
Usage: fetch-kernel-source.sh --stream=<stream> --dest=<dir> [options]
  --stream=<stream>  a stream of versions.json
  --dest=<dir>       where the verified source tree is written; must not exist or be empty
  --work=<dir>       downloads, keyrings and the git object store [default: <dest>.work]
                     A download already there is reused, and verified like a fresh one.
  --versions=<path>  the manifest to read the source from [default: versions.json]
  --keys=<dir>       armored public keys named <FINGERPRINT>.asc [default: keys]
Needs curl, xz, gpgv, gpg, sha256sum and tar; git for a git-tag source.
EOF
}

refuse() {
  echo "Refused: $*" >&2
  exit 1
}

# Removes a tree this run wrote when the run did not finish: an unproven tree is never left.
cleanup_on_failure() {
  local status=$?
  if [[ "${status}" -ne 0 && "${TREE_WRITTEN}" == "true" ]]; then
    rm -rf "${DEST}"
  fi
  return "${status}"
}

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --stream=*) STREAM="${1#*=}" ;;
      --dest=*) DEST="${1#*=}" ;;
      --work=*) WORK="${1#*=}" ;;
      --versions=*) VERSIONS="${1#*=}" ;;
      --keys=*) KEYS="${1#*=}" ;;
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
  [[ -n "${STREAM}" ]] || refuse "--stream is required"
  [[ -n "${DEST}" ]] || refuse "--dest is required"
  if [[ -e "${DEST}" && -n "$(ls -A "${DEST}" 2>/dev/null || echo not-a-directory)" ]]; then
    refuse "${DEST} exists and is not empty; a verified tree is never mixed with another"
  fi
  local tool
  for tool in curl xz gpgv gpg sha256sum tar python3; do
    command -v "${tool}" >/dev/null 2>&1 || refuse "${tool} is required and not installed"
  done
  # gpgv reads a --keyring without a slash from its home directory, so every path is absolute.
  DEST="$(realpath -m "${DEST}")"
  WORK="$(realpath -m "${WORK:-${DEST}.work}")"
  KEYS="$(realpath -m "${KEYS}")"
}

load_source() {
  local output line
  if ! output="$(python3 "${SCRIPT_DIR}/versions_query.py" --versions="${VERSIONS}" source "${STREAM}")"; then
    refuse "versions.json declares no usable source for stream '${STREAM}'"
  fi
  while IFS= read -r line; do
    SRC["${line%%=*}"]="${line#*=}"
  done <<<"${output}"
  read -r -a SIGNERS <<<"${SRC[signers]}"
  local fpr
  for fpr in "${SIGNERS[@]}"; do
    [[ -f "${KEYS}/${fpr}.asc" ]] || refuse "signer ${fpr} has no public key at ${KEYS}/${fpr}.asc"
  done
}

download() {
  local url="$1" target="$2"
  if [[ -s "${target}" ]]; then
    echo "==> Reusing ${target##*/}; it is verified below like a fresh download"
    return 0
  fi
  echo "==> Downloading ${url}"
  if ! curl --fail --silent --show-error --location --retry 3 \
    --proto '=https,file' --proto-redir '=https' --output "${target}.part" "${url}"; then
    rm -f "${target}.part"
    refuse "could not download ${url}"
  fi
  mv "${target}.part" "${target}"
}

# Succeeds when the fingerprint is one the stream lists in signers.
listed_signer() {
  local candidate="$1" fpr
  for fpr in "${SIGNERS[@]}"; do
    [[ "${candidate}" == "${fpr}" ]] && return 0
  done
  return 1
}

# Reads GnuPG status lines. The signature must be good, from an unexpired and unrevoked key,
# and every VALIDSIG must name, as its primary-key fingerprint, a signer the stream lists.
check_signature_status() {
  local status="$1" what="$2"
  local problems
  problems="$(grep -Eo '^\[GNUPG:\] (BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG|NO_PUBKEY)' "${status}" |
    cut -d' ' -f2 | sort -u | tr '\n' ' ' || true)"
  [[ -z "${problems}" ]] || refuse "${what}: the signature does not verify (${problems% })"
  grep -q '^\[GNUPG:\] GOODSIG ' "${status}" || refuse "${what}: no good signature"
  local -a signed_by=()
  mapfile -t signed_by < <(awk '$1 == "[GNUPG:]" && $2 == "VALIDSIG" && NF >= 12 { print $NF }' "${status}")
  [[ "${#signed_by[@]}" -gt 0 ]] || refuse "${what}: no valid signature"
  local fpr
  for fpr in "${signed_by[@]}"; do
    listed_signer "${fpr}" || refuse "${what}: signed by ${fpr}, which versions.json does not list for ${STREAM}"
  done
  echo "    ✓ ${what}: good signature by ${signed_by[*]}"
}

# gpgv refuses an armored keyring (invalid packet, ctb=2d), so the signers' keys are
# dearmored and concatenated into one binary keyring, which is what gpgv reads.
build_keyring() {
  local keyring="$1" scratch="${WORK}/gnupg-dearmor" fpr
  rm -rf "${scratch}"
  mkdir -m 700 "${scratch}"
  : >"${keyring}"
  for fpr in "${SIGNERS[@]}"; do
    gpg --homedir "${scratch}" --batch --quiet --dearmor --output - <"${KEYS}/${fpr}.asc" >>"${keyring}"
  done
  rm -rf "${scratch}"
}

fetch_tarball() {
  local name="${SRC[url]##*/}"
  local tarball="${WORK}/${name}" signature="${WORK}/${SRC[signature_url]##*/}"
  download "${SRC[url]}" "${tarball}"
  download "${SRC[signature_url]}" "${signature}"
  local actual
  actual="$(sha256sum "${tarball}" | cut -d' ' -f1)"
  if [[ "${actual}" != "${SRC[sha256]}" ]]; then
    refuse "${name}: sha256 ${actual}, versions.json pins ${SRC[sha256]} (remove ${WORK} to download again)"
  fi
  echo "    ✓ ${name}: sha256 matches the pin"
  local keyring="${WORK}/signers.gpg" status="${WORK}/${name}.status" log="${WORK}/${name}.gpgv.log"
  build_keyring "${keyring}"
  if ! xz -cd "${tarball}" | GNUPGHOME="${WORK}" gpgv --status-fd 3 --keyring "${keyring}" \
    "${signature}" - 3>"${status}" 2>"${log}"; then
    tail -n 5 "${log}" >&2
    check_signature_status "${status}" "${name}"
    refuse "${name}: gpgv did not accept the signature"
  fi
  check_signature_status "${status}" "${name}"
  mkdir -p "${DEST}"
  TREE_WRITTEN="true"
  tar -xJf "${tarball}" -C "${DEST}" --strip-components=1 --no-same-owner
}

# A throwaway GnuPG home holding only the signers' keys, for git verify-tag. Verifying needs
# no secret key, so gpg-agent is neither started nor required (no-autostart).
import_signers() {
  local gnupg="$1" fpr
  rm -rf "${gnupg}"
  mkdir -m 700 "${gnupg}"
  echo "no-autostart" >"${gnupg}/gpg.conf"
  for fpr in "${SIGNERS[@]}"; do
    if ! gpg --homedir "${gnupg}" --batch --quiet --import "${KEYS}/${fpr}.asc" 2>"${WORK}/import.log"; then
      cat "${WORK}/import.log" >&2
      refuse "could not import the key of signer ${fpr}"
    fi
  done
}

fetch_git_tag() {
  command -v git >/dev/null 2>&1 || refuse "git is required for a git-tag source"
  local repo="${WORK}/repo.git" gnupg="${WORK}/gnupg" tag="${SRC[tag]}"
  # Neither the system nor the user git configuration takes part in the proof.
  export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0
  [[ -d "${repo}" ]] || git init --quiet --bare "${repo}"
  import_signers "${gnupg}"
  echo "==> Fetching tag ${tag} from ${SRC[repository]}"
  if ! git -C "${repo}" fetch --quiet --depth=1 --no-tags "${SRC[repository]}" \
    "+refs/tags/${tag}:refs/tags/${tag}"; then
    refuse "could not fetch tag ${tag} from ${SRC[repository]}"
  fi
  local status="${WORK}/${tag}.status"
  if ! GNUPGHOME="${gnupg}" git -C "${repo}" -c gpg.format=openpgp -c gpg.program=gpg \
    verify-tag --raw "${tag}" 2>"${status}"; then
    check_signature_status "${status}" "tag ${tag}"
    refuse "tag ${tag}: git verify-tag did not accept it"
  fi
  check_signature_status "${status}" "tag ${tag}"
  local object name commit
  object="$(git -C "${repo}" cat-file tag "refs/tags/${tag}")"
  name="$(awk '$1 == "tag" { print $2; exit }' <<<"${object}")"
  [[ "${name}" == "${tag}" ]] || refuse "the signed tag object names '${name}', not ${tag}"
  commit="$(git -C "${repo}" rev-parse --verify --quiet "refs/tags/${tag}^{commit}")" ||
    refuse "tag ${tag} does not point at a commit"
  if [[ "${commit}" != "${SRC[commit]}" ]]; then
    refuse "tag ${tag} points at ${commit}; versions.json pins ${SRC[commit]}"
  fi
  echo "    ✓ tag ${tag}: points at the pinned commit ${commit}"
  mkdir -p "${DEST}"
  TREE_WRITTEN="true"
  git -C "${repo}" archive --format=tar "${commit}" | tar -x -C "${DEST}" --no-same-owner
}

# What `make kernelversion` prints, read from the top-level Makefile without running make.
tree_kernelversion() {
  awk '
    $1 == "VERSION" && $2 == "=" { v = $3 }
    $1 == "PATCHLEVEL" && $2 == "=" { p = $3 }
    $1 == "SUBLEVEL" && $2 == "=" { s = $3 }
    $1 == "EXTRAVERSION" && $2 == "=" { printf "%s.%s.%s%s\n", v, p, s, $3; exit }
  ' "${DEST}/Makefile"
}

main() {
  parse_arguments "$@"
  validate_arguments
  load_source
  trap cleanup_on_failure EXIT
  mkdir -p "${WORK}"
  echo "==> Fetching the ${STREAM} source (${SRC[kind]}, ${SRC[version]})"
  case "${SRC[kind]}" in
    tarball) fetch_tarball ;;
    git-tag) fetch_git_tag ;;
    *) refuse "unknown source kind ${SRC[kind]}" ;;
  esac
  local found
  found="$(tree_kernelversion 2>/dev/null || true)"
  if [[ "${found}" != "${SRC[kernelversion]}" ]]; then
    refuse "the tree is kernel '${found}', versions.json names ${SRC[version]} (${SRC[kernelversion]}) for ${STREAM}"
  fi
  echo "==> Verified ${STREAM} source ${SRC[kernelversion]} written to ${DEST}"
}

main "$@"
