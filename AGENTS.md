# AGENTS.md — Agent & Contributor Directives

> Authoritative operating guide for all autonomous engineering agents and human contributors in `cordanaLLM/nucleus`.
>
> **Read [`docs/principles.md`](docs/principles.md) before changing this repository.** It defines the authority classes, Holzmann Power of 10 adaptations, and architectural contracts for kernel compilation and packaging.

---

## 🌟 TOP-PRIORITY GLOBAL RULES

These rules apply to ALL agents, ALL tools, and ALL commits — without exception:

1. **Read `docs/principles.md` First**: Defines authority classes, Holzmann Power of 10 adaptations, and kernel packaging contracts.
2. **Single Source of Truth (`versions.json`)**: Never hardcode kernel tarball URLs, release tags, or architecture targets in shell build scripts or CI workflows. All versions must originate exclusively from [`versions.json`](versions.json).
3. **Trunk-Based PR Merge Flow**: Direct commits to `main` are strictly prohibited. Always create a short-lived branch (`feat/*`, `fix/*`, `chore/*`), run all local quality gates (`make lint`, `make test`), push, and open a PR with `gh pr create`.
4. **Docs & Code Synchrony**: Every user-discoverable change (new stream, configuration variable, or build script modification) must be reflected in documentation ([README.md](README.md) and [`docs/`](docs/)) in the **exact same commit/PR**.
5. **NASA/JPL Power of 10 Compliance**: All shell build and automation functions must be $\le$ 60 lines, enforce `set -euo pipefail`, check return codes, and pass ShellCheck with zero warnings.
6. **Privacy & Zero-Leak Invariant**: Never commit private RFC 1918 IP addresses (`10.x`, `192.168.x`, `172.16-31.x`) or local workstation home paths. Use standard documentation placeholders (`192.0.2.x`, `kernel.example.com`).
7. **All Documentation in English**: All commit messages, code comments, documentation, and agent reports must be written in a neutral, professional English register.

---

## 1. State of the forge — read this before planning anything

This repository compiles kernels: four streams for three architectures, each build opened by an
artifact gate before anything is checksummed, and every x86_64 kernel booted under QEMU before it
is kept ([ADR-0009](docs/adr/0009-kernel-compilation-and-artifact-gate.md)). What it does not do
yet is said in the table as plainly as what it does.

| Stage | State |
| :--- | :--- |
| `versions.json` stream and architecture manifest | real: each stream names a signed `source` (a tarball with its `sha256` and signers, or a git tag with its commit and signers); each architecture its `ARCH`, base defconfig and toolchain prefix |
| `scripts/fetch-kernel-source.sh` | real: fetches a stream's source and proves it (pinned `sha256` and `gpgv` over the uncompressed tar, or `git verify-tag` and the pinned commit) against `keys/`, then checks the tree's release; refuses otherwise and leaves no tree behind |
| `kconfig/` fragments and `scripts/merge-config.sh` | real: merges `security-hardened.config`, the architecture fragment, then `kconfig/streams/<stream>.config` when `--stream` names one; keeps `# CONFIG_X is not set`, refuses non-kconfig lines, writes no timestamp. With `--source-tree` it resolves them against a verified tree (defconfig, the kernel's `merge_config.sh -m`, `olddefconfig`) and refuses when a requested value did not survive (`scripts/kconfig_survival.py`); all twelve legs survive |
| `verify-requirements.yml` | real: reads imago's and Aegis-OS's requirement documents at pinned commits and checks each against the declared fragments of the streams it is bound to (first green run 36493866531), then resolves all twelve stream and architecture legs from their verified sources with the survival check, and checks each document against the resolved `.config` of each bound stream on each listed architecture |
| `scripts/build_kernel.sh` production path | real: fetch and verify, resolve, `make bindeb-pkg` with `LOCALVERSION=-lusoris<N>-<stream>` and `KDEB_PKGVERSION=<debian_version>-lusoris<N>` in a fixed environment (`SOURCE_DATE_EPOCH` from the release commit; the packages are still not reproducible, since module signatures and the `debian/changelog` date differ per build), record `make -s kernelrelease`, extract `vmlinuz-<kernelrelease>`, run the gate. A release candidate's package version is `7.3~rc5-lusoris1` (dpkg orders it before `7.3`), and its file names spell `~` as `.`, which GitHub and imago require. Built locally for all four streams on x86_64 and for `mainstream` on arm64 and riscv64 (cross); a cross build ships no headers package. Refuses a non-empty output directory, a tree that is not a kernel and a missing tool before writing anything |
| `scripts/build_kernel.sh --dry-run` | real: states the fetch, the resolution, the make command and the expected kernel release; writes nothing |
| `scripts/check_kernel_artifacts.py` | real: the artifact gate. Opens every package with `dpkg-deb`; refuses empty files, strangers, a wrong package name, version or Debian architecture, a missing image or `linux-libc-dev` package, a missing headers package on a native build, a kernel release that is not the stream's, a `vmlinuz` or `/boot/config` that differs from the files beside it, a `dpkg-deb` that fails, and a `vmlinuz` that is not a kernel of that release for that architecture (x86_64: the bzImage setup header's version string; arm64 and riscv64: the decompressed `Image` header and its `Linux version` banner) |
| `scripts/boot_smoke.py` | real: boots an x86_64 kernel with a static-init initramfs under QEMU (TCG) and requires `Linux version <kernelrelease>` and the init's line; all four x86_64 kernels boot locally |
| `scripts/package-deb.sh` | real: a wrapper over `build_kernel.sh`; **refuses** a production run without `--source-tree` (issue #31) |
| `patches/` | **not applied** by the build. The one patch there, `common/0001-sched-ext-tuning.patch`, changes a license identifier and does not apply to 7.2.8 (`patch -p1 --dry-run`) |
| `scripts/package-uki.sh` | real, unsigned: `ukify` build, then `package-uki.sh` deletes any output that `scripts/check_uki.py` refuses (not a PE with the UKI sections it wires) before it is checksummed; CI builds one from Ubuntu's kernel, never yet from a nucleus kernel; no release workflow publishes a UKI; **refuses** without a kernel or `ukify` (issue #21) |
| `scripts/publish_release.sh` | real: `SHA256SUMS` over exactly the files in `OUTPUT_DIR`; **refuses** an empty directory, a zero-byte file, a file that is not a publishable artifact, a directory without a package, and a `sha256sum` failure, writing nothing (issue #31) |
| `publish-release.yml` | real, has never run: builds the tagged stream for x86_64 in the pinned `ubuntu:26.04` image with the checkout mounted read-only, gates, boots it under QEMU, gates again on the host, SBOM, checksums, keyless cosign signature; publishes the packages, `vmlinuz-<kernelrelease>` and `kernel-<stream>-x86_64.config`. The manifest's `kernel.release` is the recorded kernel release, `config_digest` the resolved configuration's digest, `revision` the tag's commit; imago's verifier (16f964b) accepts a release assembled this way locally and refuses one flipped bit |
| `.github/workflows/build-matrix.yml` | real: 4 streams x 3 architectures in `ubuntu:26.04` (pinned by digest, the one every workflow uses), arm64 natively on `ubuntu-24.04-arm`, riscv64 cross-compiled; disk probe, compiler cache, gate, x86_64 boot, checksums; job permissions `contents: read`. Run 36507112520 compiled and gated all twelve legs, booted the four x86_64 kernels, and built the arm64 headers packages natively. An earlier run lost a riscv64 leg to a race in the uutils `install -D` that Ubuntu 26.04 ships; the build now uses GNU `install` (`gnuinstall`) |
| downstream dispatch to `cordanaLLM/imago` | wired, has never run: `publish-release.yml` has no runs yet. `kernel_release_published` carries stream, version and tag, and needs the `KERNEL_FORGE_TOKEN` secret, which is not configured; without it the run stops with an error naming the secret |

The production path used to `touch` two empty `.deb` files and exit 0, so the matrix
reported success on all twelve legs in under three minutes, and `publish-release` would
have packaged those empty files with sixteen megabytes of `/dev/urandom` named as a UKI,
generated an SBOM for them, and signed the result with cosign. It refused from #19 until the
real build replaced it (issue #18), and the packaging scripts that still fabricated, text files
named `.deb` and an empty `SHA256SUMS`, now refuse too (issue #31): **a forge that cannot
compile must not report that it did.** Do not restore a placeholder to make a build green; the
artifact gate exists so that one cannot pass.

What is not done: no UKI is built from a nucleus kernel yet (`package-uki.sh` needs an initramfs
and per-architecture stubs); arm64 and riscv64 kernels are compiled and gated but not booted;
riscv64 kernels carry no debug information and therefore no BTF, because
`kconfig/riscv64.config` does not request it; a release is x86_64 only, because
`imago.nucleus.kernel-artifact.v1` has no architecture field; and `verify_kernel_requirement.py`
still reports the module ABI as unverifiable, since it reads configurations, not build records.

`scripts/package-uki.sh` had two placeholders of the same class, both removed under
issue #21. It copied `vmlinuz` to a `.efi` name when `ukify` was absent, and it fell
through to its simulation branch whenever `--vmlinuz` was missing, even without
`--dry-run`, writing an `MZ`-prefixed text file named `BOOTX64.EFI` plus a PCR 11
measurement of it. Both now refuse. Simulation happens only on an explicit `--dry-run`,
which writes `<stream>-<arch>-dry-run/<efi name>.simulated.txt`, never a `.efi` or a
checksum, and its `pcr11-measurements.json` carries `"simulated": true`. A production run
removes any image, checksum and measurement an earlier run left at its path. Its `ukify` call was also
wrong: it embedded the cmdline file's path as the kernel command line, and passed an
empty `--initrd=`, on which `ukify` crashes. Whatever `ukify` writes now goes through
`scripts/check_uki.py` (PE headers, machine type, UKI sections, no section the script does
not wire, the command line text) and is deleted by `package-uki.sh`, not checksummed, if
refused. `ukify` runs with `--config=/dev/null`, so a host `ukify.conf` cannot change the
image. The `UKI Real ukify Build` job in `ci.yml` builds a real UKI from Ubuntu's kernel
image on every pull request.

### The contract with imago

`cordanaLLM/imago` consumes what this forge publishes, and verifies it:

- **Inbound**: requirement documents of shape `aegis.p01-nucleus.kernel-requirement.v1`, declared in
  `versions.json` under `downstream.requirements`, each bound to the streams its consumer uses:
  imago's `kernel/requirement.json` to all four, Aegis-OS's `build/kernel-requirement.json` to
  `realtime`. The policy is [ADR-0007](docs/adr/0007-document-driven-kernel-requirements.md).
  - `scripts/fetch-kernel-requirements.sh` reads each document at one commit. A
    `kernel_requirements_updated` dispatch pins its own row to the dispatched commit, and the
    verifier proves the dispatched SHA-256 before parsing and the correlation id before the
    document is used; the other rows are read at the commit their default branch resolves to.
    imago's dispatch workflow has not completed a run yet (its only run, 35072144482, failed at
    job setup), so the weekly schedule and the pull request and push triggers are what exercise
    this gate today.
  - `scripts/verify_kernel_requirement.py` decodes a document the way its owner does (the Rust crate
    `crates/aegis-fabrica-defs` in Aegis-OS), with two named divergences: a wider `required-by`
    and a refused array form. Every bound stream must meet `abi.minimum-release` and every
    feature's exact state on every listed architecture; a failure names the correlation id,
    stream, architecture, symbol, `required-by`, required state and observed value. Unbound
    streams are reported, never gating.
  - It checks at two evidence levels. `declared`: the fragments name the symbol. `resolved`: the
    symbol is in the `.config` that `make olddefconfig` produced from the stream's verified source,
    which catches a symbol whose Kconfig dependencies are unmet. The fragments declare the chains
    the requirement symbols need, such as `EXPERT` for `PREEMPT_RT` and `DEBUG_KERNEL` with
    `DEBUG_INFO_DWARF5` for `DEBUG_INFO_BTF`, and say why.
- **Outbound**: `kernel-<stream>.manifest.json`, shape `imago.nucleus.kernel-artifact.v1`, carrying the stream, version, kernel release, config digest, per-artifact digests and sizes, the checksum file, and provenance (tag, revision, cosign bundle, signer identity).
- Imago verifies the cosign bundle over `SHA256SUMS`, then the checksum file digest, then every per-artifact digest, before an image build consumes anything.

That verification passes on whatever is published. It would have passed on empty files,
which is the second reason the production path refuses rather than fabricates.

---

## 2. Mission & Purpose

`cordanaLLM/nucleus` compiles, patches, hardens, and packages high-performance Linux kernels for virtualization, container orchestration, and hardware acceleration in `imago`.

Key capabilities:
- **Multi-Stream Releases**: Curates and compiles `bleeding` (7.3-rc5), `mainstream` (7.2.8), `lts` (6.18.54), and `realtime` (7.2.8 with in-tree `PREEMPT_RT`).
- **Multi-Architecture Matrix**: Native compilation for `x86_64`, `arm64`, and `riscv64`.
- **Hardened KConfig Fragments**: Minimalist, modular kernel configuration fragments prioritizing security (KSPP), performance (`mq-deadline`, BBRv3), and container agility (`crun`, sched-ext, eBPF).
- **Automated Downstream Sync**: Automated GitHub Actions workflows dispatching new release notifications downstream to `imago`.

---

## 3. Working Sequence

1. **Inspect Authority**: Read `docs/principles.md` and `versions.json` before touching build scripts or kconfigs.
2. **Smallest Coherent Patch**: Make the minimal changes necessary. Do not rewrite nearby build scripts or kconfigs for style alone.
3. **Run Local Checks**: Execute `make lint` and `make test` before pushing or creating a pull request.
   `make build-kernel STREAM=<stream> ARCH=<arch> DRY_RUN=true` states the plan without
   producing an artifact; `DRY_RUN=false` compiles, in `ubuntu:26.04` with
   `scripts/install-build-toolchain.sh` (3 to 9 minutes and 8 to 16 GiB per leg at 32 threads).
4. **Prove it in CI, not only locally**: `gh workflow run build-matrix.yml -R cordanaLLM/nucleus --ref <branch> -f dry_run=false`
   compiles all twelve legs; `-f dry_run=true` only states each leg's plan. A change to the build
   path that has not been compiled there has not been tested.

### Working on Windows

The build container runs `sh`, not `bash`: a step using arrays or `[[` needs an explicit
`shell: bash`, and the workflow installs `bash` for that reason. Two test failures,
`test_scripts_executable` and `test_audit_repository_health_script_executable`, are exec-bit
checks that fail on a Windows checkout and pass in CI; the index modes are already `100755`.
4. **Conventional Commits**: Draft clear, descriptive commit messages adhering strictly to Conventional Commits (`type(scope): subject`).

---

## 4. Hard Rules

1. **Single Source of Truth**: All kernel streams, tags, URLs, and architectures originate exclusively from [`versions.json`](versions.json).
2. **Never `git push --force` to `main`**.
3. **Never commit directly to `main`**.
4. **Never merge without `make lint` + `make test` green**.
5. **Every commit message follows Conventional Commits** (`type(scope): subject`).
6. **Every new script starts with license header** (`Copyright 2026 The Lusoris Authors`).
7. **Every user-discoverable surface ships human-readable documentation** under `docs/` in the same PR.
8. **Power of 10 compliance**: Shell functions $\le$ 60 lines, `set -euo pipefail`, bounded loops, zero linter warnings.
9. **Privacy & Zero-Leak invariant**: Zero private RFC 1918 IPs, zero `/home/*` workstation paths.
