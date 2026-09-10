---
name: build-kernel
description: Step-by-step local Linux kernel compilation, kconfig fragment layering, and dry-run simulation.
references:
  - references/build-matrix.md
---

# /build-kernel — Kernel Compilation Skill

Execute hermetic Linux kernel compilation, kconfig fragment layering, patch queue application, and Debian/UKI packaging across supported release streams.

## Usage

```bash
# 1. Execute dry-run build for mainstream kernel on x86_64
./scripts/build_kernel.sh --stream=mainstream --arch=x86_64 --dry-run

# 2. Dry-run build for bleeding edge on arm64
./scripts/build_kernel.sh --stream=bleeding --arch=arm64 --dry-run

# 3. Via Makefile targets
make build-kernel STREAM=mainstream ARCH=x86_64
make build-kernel-dry STREAM=bleeding ARCH=arm64
```

## Step-by-Step Procedure

1. **Parameter & Manifest Resolution**:
   - Inspect `versions.json` to verify the requested stream (`bleeding`, `mainstream`, `lts`, `realtime`) and architecture (`x86_64`, `arm64`, `riscv64`).
   - Extract upstream tarball URL, version tag, and description.

2. **KConfig Fragment Assembly**:
   - Locate architecture base config in `kconfig/<arch>.config`.
   - Merge mandatory security hardening overlay from `kconfig/security-hardened.config`.
   - Apply optional workload fragment (e.g. realtime or hardware-specific acceleration).

3. **Patch Application**:
   - Apply common patches from `patches/common/`.
   - Apply stream-specific patches from `patches/<stream>/`.
   - Validate with `git apply --check` before applying.

4. **Package Synthesis**:
   - Build Debian packages (`linux-image`, `linux-headers`, `linux-libc-dev`) using `bindeb-pkg`.
   - Generate SHA256 checksums in `output/SHA256SUMS`.

## Progressive Disclosure & Reference

For the detailed matrix of kernel streams, architecture targets, compiler toolchains, and package outputs, consult:
- [`references/build-matrix.md`](references/build-matrix.md)

## Invariants to Preserve
1. **Single Source of Truth**: All kernel versions originate exclusively from `versions.json`.
2. **NASA/JPL Power of 10**: Scripts execute with `set -euo pipefail` and functions $\le 60$ lines.
3. **Zero-Leak Invariant**: Zero private RFC 1918 IPs and zero workstation home paths in build scripts or logs.
