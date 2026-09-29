# Engineering Principles & Architectural Contracts

> Authoritative architectural baseline and engineering laws for `cordanaLLM/nucleus`.

---

## 1. Authority Classes & Precedence

1. **Class 0 (Legal & Security)**: Zero-leak privacy invariant (no private RFC 1918 IPs, no workstation paths), Apache 2.0 license compliance.
2. **Class 1 (Declarative SSOT)**: [`versions.json`](https://github.com/cordanaLLM/nucleus/blob/main/versions.json) is the sole authority for kernel versions, release tags, kernel sources (URL or repository, the pinned `sha256` or commit, and the accepted signers), and the supported architectures with their `ARCH`, base defconfig and toolchain prefix.
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
3. **Verified Sources**: A kernel source is used only after `scripts/fetch-kernel-source.sh` has proven it: a tarball matches its pinned SHA-256 and carries a good kernel.org signature, over the uncompressed tar, by a key the stream lists; a release-candidate tag passes `git verify-tag` and points at its pinned commit. The public keys are in `keys/`. Anything else is refused ([ADR-0008](adr/0008-signed-kernel-sources-and-resolved-configuration.md)).
4. **Modular KConfig**: Avoid monolith `.config` files. Configuration is partitioned into composable fragments that `scripts/merge-config.sh` merges in a fixed order: `kconfig/security-hardened.config`, `kconfig/<arch>.config`, then `kconfig/streams/<stream>.config` when the stream has one. The last line for a symbol wins, `# CONFIG_X is not set` included, and the output carries no timestamp. Against a verified tree (`--source-tree`), the fragments are applied with the kernel's `scripts/kconfig/merge_config.sh -m` on top of the architecture's defconfig and resolved with `make olddefconfig`; there is no `kconfig/base.config`.
5. **Survival**: A resolved configuration is used only when every value a fragment requests is in it unchanged, `# CONFIG_X is not set` included (`scripts/kconfig_survival.py`). A fragment line `olddefconfig` dropped configures nothing, so the check fails instead of the kernel silently lacking it. A symbol an architecture cannot have belongs in that architecture's fragment, never in a shared one, and the check is never relaxed to make a leg pass.
