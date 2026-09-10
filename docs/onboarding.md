# Onboarding & Agent Migration Runbook

> Authoritative runbook for autonomous AI coding agents and human contributors onboarding or executing migrations in `lusoris-kernel-forge`.

---

## 1. System Role & Ecosystem Topology

`lusoris-kernel-forge` is the kernel compilation sister repository to `lusoris-cloud-images`. 

Instead of compiling kernels inside image builders (which slows down Packer and wastes CI compute), `lusoris-kernel-forge` compiles, patches, and packages standardized `.deb` packages for four discrete release channels across `x86_64`, `arm64`, and `riscv64`.

```mermaid
sequenceDiagram
    autonumber
    participant KF as lusoris-kernel-forge (Sister Repo)
    participant GH as GitHub Releases & Apt Repository
    participant CI as lusoris-cloud-images (Image Forge)

    Note over KF: Kernel source fetched, patched, compiled
    KF->>KF: make bindeb-pkg (linux-image, linux-headers)
    KF->>GH: Upload signed .deb packages & SHA256 checksums
    KF->>CI: repository_dispatch (kernel_release_published)
    CI->>CI: sync-kernel-manifest workflow updates versions.json
    CI->>CI: Packer builders install pre-compiled .deb packages
```

---

## 2. Agent Migration Orders & Initial Execution Checklist

When an autonomous agent is initialized in or migrated to this repository, it must execute the following sequential orders:

### Order 1: Verify Environment & Tools
Verify that the host environment has GNU Make, Python 3.12+, ShellCheck, Yamllint, and kernel build utilities:
```bash
make --version
python3 --version
shellcheck --version
yamllint --version
```

### Order 2: Validate Manifest & Invariants
Run the local quality gates:
```bash
make lint
make test
```
Confirm:
- `versions.json` matches `versions.schema.json`.
- Zero private RFC 1918 IPs exist across the codebase.
- Zero developer workstation paths exist across the codebase.

### Order 3: Inspect Stream Targets in `versions.json`
Verify the target kernel streams:
- `bleeding`: Linux 7.3-rc2
- `mainstream`: Linux 7.2.4
- `lts`: Linux 6.18.50
- `realtime`: Linux 7.2-rt

---

## 3. Operational Recipes

### Recipe A: Compiling a Kernel Locally
```bash
# Dry-run validation of download and config merge:
./scripts/build_kernel.sh --stream=mainstream --arch=x86_64 --dry-run

# Full compilation (utilizes all available CPU threads):
./scripts/build_kernel.sh --stream=mainstream --arch=x86_64
```

### Recipe B: Adding a Curated Kernel Patch
1. Create a descriptive patch file under `patches/<stream>/` or `patches/common/`:
   ```bash
   # Example: patches/common/0002-bbrv3-congestion-tuning.patch
   ```
2. Verify that the patch applies cleanly against the unpacked kernel source:
   ```bash
   patch -p1 --dry-run < patches/common/0002-bbrv3-congestion-tuning.patch
   ```
3. Commit adhering to Conventional Commits: `feat(patches): add bbrv3 congestion tuning patch for mainstream`.

### Recipe C: Bumping a Kernel Version
1. Edit [`versions.json`](https://github.com/lusoris/lusoris-kernel-forge/blob/main/versions.json) with the new version, tag, and upstream tarball URL.
2. Update [`docs/streams.md`](streams.md) and [`README.md`](https://github.com/lusoris/lusoris-kernel-forge/blob/main/README.md) in the exact same commit.
3. Validate schema: `make lint && make test`.
4. Submit PR via short-lived branch (`chore/bump-<stream>-kernel`).

---

## 4. Compliance & Invariant Guardrails

1. **Zero-Leak Invariant**:
   - Never commit RFC 1918 IP addresses (`10.x`, `192.168.x`, `172.16-31.x`).
   - Never commit `/home/...` or `/Users/...` workstation paths.
   - Always verify with `pytest tests/test_security_privacy.py`.
2. **NASA/JPL Power of 10**:
   - Keep shell functions under 60 lines.
   - Enforce `set -euo pipefail`.
   - Always verify with `shellcheck -s bash scripts/*.sh`.
