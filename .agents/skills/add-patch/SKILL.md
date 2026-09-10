---
name: add-patch
description: Kernel patch integration, header validation, series management, and clean dry-run application.
references:
  - references/patch-standards.md
---

# /add-patch — Kernel Patch Integration Skill

Integrate, validate, and document custom Linux kernel patches for hardware acceleration, dynamic schedulers, and network stack enhancements.

## Usage

```bash
# 1. Validate patch formatting and headers
git apply --stat patches/common/0001-sched-ext-tuning.patch

# 2. Test dry-run application against a clean tree
git apply --check patches/common/0001-sched-ext-tuning.patch

# 3. Verify repository integrity after adding patch
make lint
make test
```

## Step-by-Step Procedure

1. **Target Directory Selection**:
   - For patches applicable across multiple streams, place in `patches/common/`.
   - For stream-specific patches (e.g. bleeding edge or realtime fixes), place in `patches/<stream>/` (where `<stream>` is `bleeding`, `mainstream`, `lts`, or `realtime`).

2. **Patch Header & Author Attribution**:
   - Ensure the patch has standard git unified diff format.
   - Include clear metadata: `From:`, `Date:`, `Subject: [PATCH] ...`, and `Signed-off-by:`.
   - Include upstream link, LKML reference, or GitHub commit reference where applicable.

3. **Series Ordering**:
   - Prefix patch filename with 4-digit sequential ordering (e.g. `0001-feature.patch`, `0002-fix.patch`).

4. **Dry-Run Validation**:
   - Run `git apply --check <patch_file>` against the extracted upstream kernel source tree.
   - Ensure zero rejects, zero offsets, and zero trailing whitespace warnings.

5. **Documentation & Tests**:
   - Document the patch rationale in `docs/patches.md`.
   - Run repository test suite: `make test`.

## Progressive Disclosure & Reference

For patch naming conventions, signed-off-by standards, and upstream backport policies, consult:
- [`references/patch-standards.md`](references/patch-standards.md)

## Invariants to Preserve
1. **Zero Monolithic Blobs**: Keep patches focused, single-purpose, and atomic.
2. **Upstream First**: Prefer upstream LKML or backported fixes over out-of-tree hacks.
3. **Zero-Leak Invariant**: Zero private RFC 1918 IPs or developer workstation home paths inside patches.
