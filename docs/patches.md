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

```
patches/
├── common/             # Applied across all compatible streams
│   └── 0001-sched-ext-tuning.patch
├── bleeding/           # Specific to Linux 7.3-rc
├── mainstream/         # Specific to Linux 7.2.x
└── lts/                # Specific to Linux 6.18.x
```

---

## 3. Retiring Patches

When an upstream minor or major release incorporates a previously backported patch:
1. Remove the corresponding patch file from `patches/<stream>/`.
2. Document the retirement in the PR commit message.
3. Re-verify the build pipeline with `scripts/build_kernel.sh`.
