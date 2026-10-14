# Curated Patch Queue & Upstreaming Policy

> Rules governing the inclusion, maintenance, and upstreaming of custom Linux kernel patches in `cordanaLLM/nucleus`.

---

## 1. Patch Inclusion Criteria

Every patch introduced into `cordanaLLM/nucleus` must meet all of the following conditions:

1. **Targeted Justification**: Must address a verified performance, hardware enablement, or security need not yet merged in the upstream release branch.
2. **Upstream Path**: Must either be queued in an upstream maintainer tree (e.g. `tip.git`, `net-next.git`, `drm-next.git`) or explicitly tracked against an upstream LKML discussion.
3. **Clean Application**: Must apply cleanly with `patch -p1` with zero rejects (`*.rej`).
4. **Isolated Scope**: Patches should touch the minimal set of files required and adhere to Linux kernel coding style (`scripts/checkpatch.pl`).

---

## 2. Directory Layout

The directory layout currently holds only one common patch:

```text
patches/
└── common/
    └── 0001-sched-ext-tuning.patch
```

The build pipeline (`scripts/build_kernel.sh`) currently applies no patches from `patches/`. The single patch present (`patches/common/0001-sched-ext-tuning.patch`) changes a license identifier and does not apply cleanly to the current kernel source.

---

## 3. Retiring Patches

When an upstream release incorporates a previously backported patch:

1. Remove the corresponding patch file from `patches/common/` or `patches/<stream>/`.
2. Document the retirement in the PR commit message.
3. Re-verify the build pipeline with `scripts/build_kernel.sh`.
