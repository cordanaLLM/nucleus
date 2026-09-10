# Engineering Principles & Architectural Contracts

> Authoritative architectural baseline and engineering laws for `lusoris-kernel-forge`.

---

## 1. Authority Classes & Precedence

1. **Class 0 (Legal & Security)**: Zero-leak privacy invariant (no private RFC 1918 IPs, no workstation paths), Apache 2.0 license compliance.
2. **Class 1 (Declarative SSOT)**: [`versions.json`](https://github.com/lusoris/lusoris-kernel-forge/blob/main/versions.json) is the sole authority for kernel versions, release tags, upstream tarball URLs, and supported architectures.
3. **Class 2 (Code Contracts)**: NASA/JPL Power of 10 rules for shell scripts and automated build tooling.
4. **Class 3 (Documentation & ADRs)**: Architectural Decision Records under `docs/` govern durable technical strategy.

---

## 2. NASA / JPL Power of 10 Adaptations

1. **Rule 1 (Simple Control Flow)**: Shell scripts must avoid recursion and unbounded loops.
2. **Rule 2 (Fixed Loop Bounds)**: All wait loops or retry loops must enforce an explicit maximum counter and timeout.
3. **Rule 4 (Short Functions)**: Shell functions must not exceed 60 lines of executable code.
4. **Rule 5 (Explicit Error Checking)**: Enforce `set -euo pipefail`. Check exit codes of all external commands (`wget`, `tar`, `patch`, `make`).
5. **Rule 7 (Restricted Scope)**: Keep shell variables localized (`local var=...`).
6. **Rule 9 (Static Analysis)**: All shell scripts must pass `shellcheck` with zero warnings before merging.

---

## 3. Kernel Packaging Contracts

1. **Native Debian Packaging**: Builds generate standard `.deb` packages via upstream `make bindeb-pkg`:
   - `linux-image-<version>-<stream>-<arch>.deb`
   - `linux-headers-<version>-<stream>-<arch>.deb`
   - `linux-libc-dev-<version>-<stream>-<arch>.deb`
2. **Package Versioning**: Packages carry a reproducible localversion suffix, e.g. `-lusoris1`.
3. **Reproducible Checksums**: Upstream source archives must match cryptographically verified SHA-256 digests.
4. **Modular KConfig**: Avoid monolith `.config` files. Configuration is partitioned into composable fragments merged via `scripts/kconfig/merge_config.sh`.
