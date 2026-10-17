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

The repository carries no patches. The layout:

```text
patches/
├── common/             # Patches applied across all streams
└── <stream>/           # Patches scoped to a specific stream
```

`patches/common/` applies to every stream, `patches/<stream>/` only to the stream of that name, which must be declared in `versions.json`.

`scripts/check_patches_apply.py` refuses a directory that is neither, a file that is not a `.patch`, and a patch that does not apply with `patch -p1 --dry-run` to the stream's verified source tree. It runs per stream in the `resolve` job of `.github/workflows/verify-requirements.yml`. Each patch is checked against the pristine tree, not on top of the patches before it. The build pipeline (`scripts/build_kernel.sh`) currently applies no patches from `patches/`.

Patches carry the kernel's licence, `GPL-2.0-only`, through the `patches/**` annotation in `REUSE.toml`. With no patch carried, `LICENSES/GPL-2.0-only.txt` was removed, because `reuse lint` refuses an unused licence; the first new patch brings it back with `reuse download GPL-2.0-only`, which `reuse lint` then requires.

---

## 3. Retiring Patches

When an upstream release incorporates a previously backported patch:

1. Remove the corresponding patch file from `patches/common/` or `patches/<stream>/`.
2. Document the retirement in the PR commit message.
3. Re-verify the build pipeline with `scripts/build_kernel.sh`.
