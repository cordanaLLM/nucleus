# Onboarding & Agent Migration Runbook

> Authoritative runbook for autonomous AI coding agents and human contributors onboarding or executing migrations in `cordanaLLM/nucleus`.

---

## 0. Start here

**This forge compiles kernels, and refuses to emit anything it did not compile.**
`scripts/build_kernel.sh` fetches a stream's signed source, resolves the kconfig fragments
against it, runs `bindeb-pkg`, and passes the packages through an artifact gate that opens every
one of them before anything is checksummed (`docs/adr/0009-kernel-compilation-and-artifact-gate.md`).
`build-matrix.yml` compiles all four streams for `x86_64`, `arm64` and `riscv64` and boots every
x86_64 kernel under QEMU; `publish-release.yml` builds, gates, signs and publishes the tagged
stream for x86_64.

The refusals are deliberate, and they stay. The production path once touched two empty `.deb`
files and exited 0, the release workflow added sixteen megabytes of `/dev/urandom` named as a
Unified Kernel Image, and cosign signed the result. Imago verifies these artifacts by digest and
provenance before an image consumes them, and that verification would have passed on empty
files. Never make a build green by writing a placeholder.

Issue #18 (the build) and #31 (the packaging scripts) are the history; `AGENTS.md` section 1 has
the stage-by-stage state and the contract with imago.

---

## 1. System Role & Ecosystem Topology

`cordanaLLM/nucleus` is the kernel compilation sister repository to `cordanaLLM/imago`.

Instead of compiling kernels inside image builders (which slows down Packer and wastes CI compute), `cordanaLLM/nucleus` compiles, patches, and packages standardized `.deb` packages for four discrete release channels across `x86_64`, `arm64`, and `riscv64`.

```mermaid
sequenceDiagram
    autonumber
    participant KF as nucleus (Sister Repo)
    participant GH as GitHub Releases & Apt Repository
    participant CI as imago (Image Forge)

    Note over KF: Kernel source fetched, verified, compiled
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
- `bleeding`: Linux 7.3-rc5, from the signed tag
- `mainstream`: Linux 7.2.8
- `lts`: Linux 6.18.54
- `realtime`: Linux 7.2.8 with the in-tree `PREEMPT_RT`

---

## 3. Operational Recipes

### Recipe A: Compiling a Kernel Locally
Run it in `ubuntu:26.04` with `scripts/install-build-toolchain.sh`, the toolchain the workflows
use; a leg needs 8 to 16 GiB of disk, depending on the architecture
(`scripts/probe-build-space.sh`, and `disk_gib` in `build-matrix.yml`).
```bash
# State the fetch, the resolution, the make command and the expected kernel release:
./scripts/build_kernel.sh --stream=mainstream --arch=x86_64 --dry-run

# Full compilation (all CPU threads); writes output/mainstream-x86_64/ and build/mainstream-x86_64/
./scripts/build_kernel.sh --stream=mainstream --arch=x86_64

# Boot the result to userspace under QEMU
python3 scripts/boot_smoke.py --kernel=output/mainstream-x86_64/vmlinuz-7.2.8-lusoris1-mainstream \
  --kernelrelease=7.2.8-lusoris1-mainstream
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
1. Edit [`versions.json`](https://github.com/cordanaLLM/nucleus/blob/main/versions.json): the new `version` and `tag`, and the `source` with them. For a tarball, the URL, the signature URL and the `sha256`, taken from kernel.org's `sha256sums.asc` after `gpgv` verifies it against the autosigner key in `keys/`; for a release candidate, the tag and the commit `git ls-remote <repository> 'refs/tags/<tag>^{}'` reports. [Kernel Streams](streams.md#3-sources-and-signatures) has the details.
2. Prove it: `make fetch-source STREAM=<stream>`, then `make resolve-config STREAM=<stream> ARCH=<arch>` for each architecture. A symbol the new release dropped fails the survival check; fix the fragment, never the check.
3. Update [`docs/streams.md`](streams.md) and [`README.md`](https://github.com/cordanaLLM/nucleus/blob/main/README.md) in the exact same commit.
4. Validate schema: `make lint && make test`.
5. Submit PR via short-lived branch (`chore/bump-<stream>-kernel`); `verify-requirements.yml` resolves all twelve stream and architecture legs again.

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
