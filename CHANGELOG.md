# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0](https://github.com/cordanaLLM/nucleus/compare/v0.1.0...v0.2.0) (2026-09-16)


### ⚠ BREAKING CHANGES

* **build:** refuse to emit a kernel artifact this forge did not compile ([#19](https://github.com/cordanaLLM/nucleus/issues/19))
* **release:** publish the imago.nucleus.kernel-artifact.v1 manifest and carry the release tag downstream ([#11](https://github.com/cordanaLLM/nucleus/issues/11))

### Features

* **governance:** declare the praetor os-image profile ([#13](https://github.com/cordanaLLM/nucleus/issues/13)) ([b7a0ef1](https://github.com/cordanaLLM/nucleus/commit/b7a0ef198d7fe7bb8e4feea33bf5268f581b9c38))
* **init:** bootstrap lusoris-kernel-forge repository ([e527cd3](https://github.com/cordanaLLM/nucleus/commit/e527cd3400c76dac9aa2812f50e732a8fdfafbfb))
* **onboarding:** establish full ecosystem and repository parity with lusoris-cloud-images ([#7](https://github.com/cordanaLLM/nucleus/issues/7)) ([c3397fc](https://github.com/cordanaLLM/nucleus/commit/c3397fcec25c8b71776f4d58b4cd61f00b5a8423))
* **packaging:** implement multi-arch builder container, dual packaging pipeline, and qemu boot verification ([#9](https://github.com/cordanaLLM/nucleus/issues/9)) ([494ea58](https://github.com/cordanaLLM/nucleus/commit/494ea58dcd7032e19fc82691398f06a14749ece1))
* **release:** publish the imago.nucleus.kernel-artifact.v1 manifest and carry the release tag downstream ([#11](https://github.com/cordanaLLM/nucleus/issues/11)) ([3c9643b](https://github.com/cordanaLLM/nucleus/commit/3c9643bf03a266ef1befa67b8de206bec643235e))


### Bug Fixes

* **build:** refuse to emit a kernel artifact this forge did not compile ([#19](https://github.com/cordanaLLM/nucleus/issues/19)) ([c5fbeb8](https://github.com/cordanaLLM/nucleus/commit/c5fbeb8c271caadef8dbefdb23c2a3a1c460db17))
* **ci:** build the kernel on 26.04 with a shell that can parse the build step ([#17](https://github.com/cordanaLLM/nucleus/issues/17)) ([74ba997](https://github.com/cordanaLLM/nucleus/commit/74ba997e37a8e12565aeacf4869e17fefe07e68a))

## [Unreleased]

### Added
- Kernel artifact manifest `kernel-<stream>.manifest.json` in the `imago.nucleus.kernel-artifact.v1` shape owned by `cordanaLLM/imago`: generated in `publish-release.yml` after `SHA256SUMS` is signed (stream, version, kernel release, `config_digest` over the shipped `kernel-<stream>.config`, per-artifact `sha256` and `size` from `SHA256SUMS`, checksums digest, and provenance with tag, commit, bundle name, and the workflow signer identity), uploaded with the release assets, and covered by `tests/test_workflows.py`.
- `kernel-<stream>.config`, the merged kconfig written by `scripts/merge-config.sh`, ships as a release asset and is listed in `SHA256SUMS`.
- Extended Renovate configuration (`renovate.json`) with automated GitHub Actions digest pinning, Monday batch scheduling, and SSOT regex managers for `versions.json`.
- Root-level governance policies and community contracts (`CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`, `MAINTAINERS.md`, `SECURITY.md`, `SUPPORT.md`).
- Static code hygiene tooling configs (`.editorconfig`, `.gitleaks.toml`, `.markdownlint.json`, `.codespellrc`, `.pre-commit-config.yaml`, `.semgrepignore`).
- Google Release Please automated release management (`release-please-config.json`, `.release-please-manifest.json`).
- Python test harness dependencies (`requirements-test.txt`) and unified pytest/coverage/ruff configurations in `pyproject.toml`.

### Changed
- **Breaking**: the `kernel_release_published` dispatch payload sent to `cordanaLLM/imago` carries `tag` next to `stream` and `version`; imago downloads the release named by the tag, verifies the cosign bundle over `SHA256SUMS` and every manifest digest, and refuses a payload without all three. `scripts/notify_downstream.sh` sends the same `tag` (`RELEASE_TAG`, default `v<version>`).
- `publish-release.yml` exports `RAW_TAG` to the stream resolver (it was read from the environment but never exported, so every release resolved to `mainstream`) and emits a `release_tag` output.

## [0.1.0] - 2026-09-10

### Added
- Initial repository bootstrap for `lusoris-kernel-forge`.
- Multi-stream kernel compilation matrix across 4 streams:
  - `bleeding` (Linux 7.3-rc2) for Blackwell RTX 5090/B200, CXL 3.0, and sched-ext.
  - `mainstream` (Linux 7.2.4) for Intel Battlemage Xe2, AMD ROCm 10, and NVIDIA 565/610.
  - `lts` (Linux 6.18.50) for enterprise Kubernetes nodes, OpenZFS 2.3, and CloudNativePG.
  - `realtime` (Linux 7.2-rt) for full PREEMPT_RT deterministic low-latency edge workloads.
- Multi-architecture native compilation and cross-compilation support for `x86_64`, `arm64`, and `riscv64`.
- Modular, security-hardened KConfig fragment framework meeting Kernel Self-Protection Project (KSPP) and CIS Linux Benchmark Level 2 baselines.
- Single Source of Truth `versions.json` declarative manifest backed by JSON Schema validation `versions.schema.json`.
- NASA/JPL Power of 10 compliant shell build pipelines (`scripts/build-kernel.sh`, `scripts/kconfig/merge_config.sh`) with strict ShellCheck compliance.
- Automated downstream dispatch contracts for integration into `lusoris-cloud-images`.
