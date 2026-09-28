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

This repository does not compile a kernel yet. Everything around the compile step is
implemented; the compile step itself refuses.

| Stage | State |
| :--- | :--- |
| `versions.json` stream and architecture manifest | real |
| `kconfig/` fragments and `scripts/merge-config.sh` | real: merges `security-hardened.config`, the architecture fragment, then `kconfig/streams/<stream>.config` when `--stream` names one; keeps `# CONFIG_X is not set`, refuses non-kconfig lines, writes no timestamp |
| `verify-requirements.yml` | real: reads imago's and Aegis-OS's requirement documents at pinned commits and checks each against the declared fragments of the streams it is bound to; first green run 36493866531 |
| `scripts/build_kernel.sh` **production path** | **refuses with exit 1** (issue #18) |
| `scripts/build_kernel.sh --dry-run` | real: states what a build would do |
| `scripts/package-deb.sh` | implemented, but never fed a real kernel |
| `scripts/package-uki.sh` | real, unsigned: `ukify` build, then `package-uki.sh` deletes any output that `scripts/check_uki.py` refuses (not a PE with the UKI sections it wires) before it is checksummed; CI builds one from Ubuntu's kernel, never yet from a nucleus kernel; no release workflow publishes a UKI; **refuses** without a kernel or `ukify` (issue #21) |
| `scripts/publish_release.sh`, `publish-release.yml` | real: SBOM, checksums, keyless cosign signature |
| `.github/workflows/build-matrix.yml` | real: 4 streams x 3 architectures, Ubuntu 26.04 container |
| downstream dispatch to `cordanaLLM/imago` | wired, has never run: `publish-release.yml` has no runs yet. `kernel_release_published` carries stream, version and tag, and needs the `KERNEL_FORGE_TOKEN` secret, which is not configured; without it the run stops with an error naming the secret |

The production path used to `touch` two empty `.deb` files and exit 0, so the matrix
reported success on all twelve legs in under three minutes, and `publish-release` would
have packaged those empty files with sixteen megabytes of `/dev/urandom` named as a UKI,
generated an SBOM for them, and signed the result with cosign. That is why it refuses
now: **a forge that cannot compile must not report that it did.** Do not restore a
placeholder to make the build green.

Implementing the real build is issue #18. In short: fetch the pinned tarball from
kernel.org, verify its signature, merge the kconfig fragments, run `bindeb-pkg`, and
cross-compile for `arm64` and `riscv64` with the matching toolchain.

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
  - It proves the fragments *declare* a symbol (evidence level `declared`). `make olddefconfig` can
    still drop one whose Kconfig dependencies are unmet, which only the real build (#18) can show.
    The fragments declare the chains the requirement symbols need, such as `EXPERT` for
    `PREEMPT_RT` and `DEBUG_KERNEL` with `DEBUG_INFO_DWARF5` for `DEBUG_INFO_BTF`, and say why.
- **Outbound**: `kernel-<stream>.manifest.json`, shape `imago.nucleus.kernel-artifact.v1`, carrying the stream, version, kernel release, config digest, per-artifact digests and sizes, the checksum file, and provenance (tag, revision, cosign bundle, signer identity).
- Imago verifies the cosign bundle over `SHA256SUMS`, then the checksum file digest, then every per-artifact digest, before an image build consumes anything.

That verification passes on whatever is published. It would have passed on empty files,
which is the second reason the production path refuses rather than fabricates.

---

## 2. Mission & Purpose

`cordanaLLM/nucleus` compiles, patches, hardens, and packages high-performance Linux kernels for virtualization, container orchestration, and hardware acceleration in `imago`.

Key capabilities:
- **Multi-Stream Releases**: Curates and compiles `bleeding` (7.3-rc2), `mainstream` (7.2.4), `lts` (6.18.50), and `realtime` (7.2-rt).
- **Multi-Architecture Matrix**: Native compilation for `x86_64`, `arm64`, and `riscv64`.
- **Hardened KConfig Fragments**: Minimalist, modular kernel configuration fragments prioritizing security (KSPP), performance (`mq-deadline`, BBRv3), and container agility (`crun`, sched-ext, eBPF).
- **Automated Downstream Sync**: Automated GitHub Actions workflows dispatching new release notifications downstream to `imago`.

---

## 3. Working Sequence

1. **Inspect Authority**: Read `docs/principles.md` and `versions.json` before touching build scripts or kconfigs.
2. **Smallest Coherent Patch**: Make the minimal changes necessary. Do not rewrite nearby build scripts or kconfigs for style alone.
3. **Run Local Checks**: Execute `make lint` and `make test` before pushing or creating a pull request.
   `make build-kernel STREAM=<stream> ARCH=<arch> DRY_RUN=true` exercises the pipeline without
   producing an artifact; without `DRY_RUN` it refuses, by design (section 1).
4. **Prove it in CI, not only locally**: `gh workflow run build-matrix.yml -R cordanaLLM/nucleus -f dry_run=true`
   runs all twelve legs. A change to the build path that has not been run there has not been tested.

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
