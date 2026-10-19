#!/usr/bin/env bash
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
#
# scripts/build_sched_ext_scheduler.sh — compile an in-tree sched_ext BPF scheduler
# (tools/sched_ext/scx_<name>) as a static binary for scripts/boot_smoke.py.
#
# Step 1 builds the BPF object, skeleton and libbpf.a with the kernel's tools/sched_ext
# Makefile into a temporary O= directory (never passing LDFLAGS=-static to make, which
# breaks its shared libbpf build). Step 2 links scx_<name>.o and libbpf.a statically with
# gcc, verifies the output is an ELF executable without PT_INTERP, and copies it to --output.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

SOURCE_TREE=""
VMLINUX=""
OUTPUT=""
SCHEDULER="simple"
DRY_RUN="false"
TMP_DIR=""

cleanup() {
  if [[ -n "${TMP_DIR:-}" && -d "${TMP_DIR}" ]]; then
    rm -rf -- "${TMP_DIR}"
  fi
}
trap cleanup EXIT

refuse() {
  echo "Refused: $*" >&2
  exit 1
}

show_usage() {
  cat <<'EOF'
Usage: build_sched_ext_scheduler.sh --source-tree=<dir> --vmlinux=<file> --output=<file> [options]
  --source-tree=<dir>   verified kernel source tree containing tools/sched_ext/
  --vmlinux=<file>      built vmlinux image carrying BTF
  --output=<file>       destination path for the static scheduler binary
  --scheduler=<name>    in-tree scheduler name under tools/sched_ext/scx_<name>.bpf.c [default: simple]
  --dry-run             print the build commands and write nothing
  -h, --help            show this help
EOF
}

parse_args() {
  local -i limit=50
  while [[ $# -gt 0 ]] && ((limit-- > 0)); do
    case "$1" in
      --source-tree=*) SOURCE_TREE="${1#*=}" ;;
      --source-tree)
        [[ $# -ge 2 ]] || refuse "--source-tree requires an argument"
        SOURCE_TREE="$2"
        shift
        ;;
      --vmlinux=*) VMLINUX="${1#*=}" ;;
      --vmlinux)
        [[ $# -ge 2 ]] || refuse "--vmlinux requires an argument"
        VMLINUX="$2"
        shift
        ;;
      --output=*) OUTPUT="${1#*=}" ;;
      --output)
        [[ $# -ge 2 ]] || refuse "--output requires an argument"
        OUTPUT="$2"
        shift
        ;;
      --scheduler=*) SCHEDULER="${1#*=}" ;;
      --scheduler)
        [[ $# -ge 2 ]] || refuse "--scheduler requires an argument"
        SCHEDULER="$2"
        shift
        ;;
      --dry-run) DRY_RUN="true" ;;
      -h | --help)
        show_usage
        exit 0
        ;;
      *) refuse "unknown option $1" ;;
    esac
    shift
  done
}

state_plan() {
  local tree="${SOURCE_TREE:-<source-tree>}"
  local vmlinux="${VMLINUX:-<vmlinux>}"
  local out="${OUTPUT:-<output>}"
  local target="scx_${SCHEDULER}"
  echo "==> Plan for sched_ext scheduler '${target}'"
  echo "    make -C ${tree}/tools/sched_ext O=<work> VMLINUX_BTF=${vmlinux} ${target}"
  echo "    gcc -static -pthread -o ${out} <work>/build/obj/sched_ext/${target}.o <work>/build/obj/libbpf/libbpf.a -lelf -lz -lzstd"
}

check_request() {
  [[ "${SCHEDULER}" =~ ^[a-z0-9_]+$ ]] \
    || refuse "--scheduler '${SCHEDULER}' must match [a-z0-9_]+"
  if [[ "${DRY_RUN}" == "true" ]]; then
    return 0
  fi
  [[ -n "${SOURCE_TREE}" ]] || refuse "--source-tree is required"
  [[ -n "${VMLINUX}" ]] || refuse "--vmlinux is required"
  [[ -n "${OUTPUT}" ]] || refuse "--output is required"
  local bpf_src="${SOURCE_TREE}/tools/sched_ext/scx_${SCHEDULER}.bpf.c"
  [[ -f "${bpf_src}" ]] \
    || refuse "${SOURCE_TREE} has no tools/sched_ext/scx_${SCHEDULER}.bpf.c"
  [[ -f "${VMLINUX}" && -s "${VMLINUX}" ]] \
    || refuse "--vmlinux ${VMLINUX} is missing or empty"
  if [[ -d "${OUTPUT}" || -s "${OUTPUT}" ]]; then
    refuse "--output ${OUTPUT} already exists and is not empty"
  fi
  local tool
  for tool in make clang gcc python3; do
    command -v "${tool}" >/dev/null 2>&1 \
      || refuse "${tool} is required to build scx_${SCHEDULER} and is not installed"
  done
}

readonly CHECK_STATIC_ELF_PY='
import sys
from pathlib import Path
import boot_smoke
try:
    data = Path(sys.argv[1]).read_bytes()
    boot_smoke.check_static_elf(data, sys.argv[1])
except (boot_smoke.SmokeError, OSError) as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
'

build_scheduler() {
  local tree vmlinux target staged err
  tree="$(realpath "${SOURCE_TREE}")"
  vmlinux="$(realpath "${VMLINUX}")"
  target="scx_${SCHEDULER}"
  TMP_DIR="$(mktemp -d)"
  staged="${TMP_DIR}/${target}.static"
  echo "==> Building ${target} from ${tree}/tools/sched_ext"
  make -C "${tree}/tools/sched_ext" O="${TMP_DIR}" VMLINUX_BTF="${vmlinux}" "${target}" \
    || refuse "make failed for ${target}"
  gcc -static -pthread -o "${staged}" \
    "${TMP_DIR}/build/obj/sched_ext/${target}.o" \
    "${TMP_DIR}/build/obj/libbpf/libbpf.a" \
    -lelf -lz -lzstd \
    || refuse "gcc static link failed for ${target}"
  if ! err="$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${SCRIPT_DIR}" \
    python3 -c "${CHECK_STATIC_ELF_PY}" "${staged}" 2>&1)"; then
    refuse "${err}"
  fi
  mkdir -p "$(dirname "${OUTPUT}")"
  cp -- "${staged}" "${OUTPUT}"
  chmod 0755 "${OUTPUT}"
  echo "==> Built static ${target} at ${OUTPUT}"
}

main() {
  parse_args "$@"
  check_request
  state_plan
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY-RUN] Nothing was compiled or written."
    return 0
  fi
  build_scheduler
}

main "$@"
