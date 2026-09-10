# AGENTS.md — Agent & Contributor Directives

> Authoritative operating guide for all autonomous engineering agents and human contributors in `lusoris-kernel-forge`.
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

## 1. Mission & Purpose

`lusoris-kernel-forge` compiles, patches, hardens, and packages high-performance Linux kernels for virtualization, container orchestration, and hardware acceleration in `lusoris-cloud-images`.

Key capabilities:
- **Multi-Stream Releases**: Curates and compiles `bleeding` (7.3-rc2), `mainstream` (7.2.4), `lts` (6.18.50), and `realtime` (7.2-rt).
- **Multi-Architecture Matrix**: Native compilation for `x86_64`, `arm64`, and `riscv64`.
- **Hardened KConfig Fragments**: Minimalist, modular kernel configuration fragments prioritizing security (KSPP), performance (`mq-deadline`, BBRv3), and container agility (`crun`, sched-ext, eBPF).
- **Automated Downstream Sync**: Automated GitHub Actions workflows dispatching new release notifications downstream to `lusoris-cloud-images`.

---

## 2. Working Sequence

1. **Inspect Authority**: Read `docs/principles.md` and `versions.json` before touching build scripts or kconfigs.
2. **Smallest Coherent Patch**: Make the minimal changes necessary. Do not rewrite nearby build scripts or kconfigs for style alone.
3. **Run Local Checks**: Execute `make lint` and `make test` before pushing or creating a pull request.
4. **Conventional Commits**: Draft clear, descriptive commit messages adhering strictly to Conventional Commits (`type(scope): subject`).

---

## 3. Hard Rules

1. **Single Source of Truth**: All kernel streams, tags, URLs, and architectures originate exclusively from [`versions.json`](versions.json).
2. **Never `git push --force` to `main`**.
3. **Never commit directly to `main`**.
4. **Never merge without `make lint` + `make test` green**.
5. **Every commit message follows Conventional Commits** (`type(scope): subject`).
6. **Every new script starts with license header** (`Copyright 2026 The Lusoris Authors`).
7. **Every user-discoverable surface ships human-readable documentation** under `docs/` in the same PR.
8. **Power of 10 compliance**: Shell functions $\le$ 60 lines, `set -euo pipefail`, bounded loops, zero linter warnings.
9. **Privacy & Zero-Leak invariant**: Zero private RFC 1918 IPs, zero `/home/*` workstation paths.
