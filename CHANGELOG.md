# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1](https://github.com/cordanaLLM/nucleus/compare/nucleus-v0.2.0...nucleus-v0.2.1) (2026-09-29)


### Bug Fixes

* **kconfig:** let BPF LSM attach on every leg, and release revisions above 1 ([#47](https://github.com/cordanaLLM/nucleus/issues/47)) ([852be74](https://github.com/cordanaLLM/nucleus/commit/852be742eb173700d5ef93b0c6f867b855f9c640))

## [0.2.0](https://github.com/cordanaLLM/nucleus/compare/nucleus-v0.1.0...nucleus-v0.2.0) (2026-09-29)


### ⚠ BREAKING CHANGES

* **forge:** compile every leg and gate its artifacts before signing ([#37](https://github.com/cordanaLLM/nucleus/issues/37))
* **packaging:** refuse to emit a UKI that is not one ([#27](https://github.com/cordanaLLM/nucleus/issues/27))
* **release:** make the release path startable and its tags unambiguous ([#33](https://github.com/cordanaLLM/nucleus/issues/33))
* **build:** refuse to emit a kernel artifact this forge did not compile ([#19](https://github.com/cordanaLLM/nucleus/issues/19))
* **release:** publish the imago.nucleus.kernel-artifact.v1 manifest and carry the release tag downstream ([#11](https://github.com/cordanaLLM/nucleus/issues/11))

### Features

* **forge:** compile every leg and gate its artifacts before signing ([#37](https://github.com/cordanaLLM/nucleus/issues/37)) ([82aa6b7](https://github.com/cordanaLLM/nucleus/commit/82aa6b7a3c68a42a6330370c81ec642482014c9f))
* **forge:** fetch signed kernel sources and resolve the kernel configuration ([#36](https://github.com/cordanaLLM/nucleus/issues/36)) ([30253b9](https://github.com/cordanaLLM/nucleus/commit/30253b996b3407b91754ff9a845c3c60c065f1e1))
* **governance:** declare the praetor os-image profile ([#13](https://github.com/cordanaLLM/nucleus/issues/13)) ([b7a0ef1](https://github.com/cordanaLLM/nucleus/commit/b7a0ef198d7fe7bb8e4feea33bf5268f581b9c38))
* **init:** bootstrap lusoris-kernel-forge repository ([e527cd3](https://github.com/cordanaLLM/nucleus/commit/e527cd3400c76dac9aa2812f50e732a8fdfafbfb))
* **onboarding:** establish full ecosystem and repository parity with lusoris-cloud-images ([#7](https://github.com/cordanaLLM/nucleus/issues/7)) ([c3397fc](https://github.com/cordanaLLM/nucleus/commit/c3397fcec25c8b71776f4d58b4cd61f00b5a8423))
* **packaging:** implement multi-arch builder container, dual packaging pipeline, and qemu boot verification ([#9](https://github.com/cordanaLLM/nucleus/issues/9)) ([494ea58](https://github.com/cordanaLLM/nucleus/commit/494ea58dcd7032e19fc82691398f06a14749ece1))
* **release:** publish the imago.nucleus.kernel-artifact.v1 manifest and carry the release tag downstream ([#11](https://github.com/cordanaLLM/nucleus/issues/11)) ([3c9643b](https://github.com/cordanaLLM/nucleus/commit/3c9643bf03a266ef1befa67b8de206bec643235e))
* **requirements:** read requirement documents through the owner's JSON Schema ([#44](https://github.com/cordanaLLM/nucleus/issues/44)) ([2222d61](https://github.com/cordanaLLM/nucleus/commit/2222d61661fd89bb34442a49d6b355c34988536a))
* **requirements:** verify kernel requirement documents per bound stream ([#35](https://github.com/cordanaLLM/nucleus/issues/35)) ([0a4eac9](https://github.com/cordanaLLM/nucleus/commit/0a4eac93f29fef432bfa9d892ad236568ce2f482))


### Bug Fixes

* **build:** refuse to emit a kernel artifact this forge did not compile ([#19](https://github.com/cordanaLLM/nucleus/issues/19)) ([c5fbeb8](https://github.com/cordanaLLM/nucleus/commit/c5fbeb8c271caadef8dbefdb23c2a3a1c460db17))
* **ci:** build the kernel on 26.04 with a shell that can parse the build step ([#17](https://github.com/cordanaLLM/nucleus/issues/17)) ([74ba997](https://github.com/cordanaLLM/nucleus/commit/74ba997e37a8e12565aeacf4869e17fefe07e68a))
* **ci:** let the repository's own release pull request through its governance gate ([#25](https://github.com/cordanaLLM/nucleus/issues/25)) ([8672247](https://github.com/cordanaLLM/nucleus/commit/8672247ff1bd22ed6b6b89d498116f20ff00c2ab))
* **ci:** refuse pull requests whose squash message would skip CI ([#40](https://github.com/cordanaLLM/nucleus/issues/40)) ([2f889fd](https://github.com/cordanaLLM/nucleus/commit/2f889fd2e969c298e2e35e17419f1c337d024081))
* **packaging:** refuse to emit a UKI that is not one ([#27](https://github.com/cordanaLLM/nucleus/issues/27)) ([454ae97](https://github.com/cordanaLLM/nucleus/commit/454ae973e2bb5f7ffe85725dfbcfb45c3af58052))
* **release:** make the release path startable and its tags unambiguous ([#33](https://github.com/cordanaLLM/nucleus/issues/33)) ([8365d5d](https://github.com/cordanaLLM/nucleus/commit/8365d5d7462385fbd8195226172e0a01c6782755))
* **requirements:** write no bytecode into a consumer's checkout ([#41](https://github.com/cordanaLLM/nucleus/issues/41)) ([9e050cf](https://github.com/cordanaLLM/nucleus/commit/9e050cf8fc2fb62de943229f22ab104dce455e92))

## [Unreleased]

### Changed

- The repository is `cordanaLLM/nucleus` and its downstream is `cordanaLLM/imago`;
  139 references to the former names are updated across documentation, agent
  definitions and scripts. The `-lusoris1` kernel suffix is unchanged, because it
  names published packages rather than the repository.
- `AGENTS.md` and `docs/onboarding.md` open with the state of the forge: what is
  implemented, what refuses, and the contract imago verifies. A reader can no longer
  conclude from either document that this repository compiles kernels today.

### Added
- Kernel artifact manifest `kernel-<stream>.manifest.json` in the `imago.nucleus.kernel-artifact.v1` shape owned by `cordanaLLM/imago`: generated in `publish-release.yml` after `SHA256SUMS` is signed (stream, version, kernel release, `config_digest` over the shipped `kernel-<stream>.config`, per-artifact `sha256` and `size` from `SHA256SUMS`, checksums digest, and provenance with tag, commit, bundle name, and the workflow signer identity), uploaded with the release assets, and covered by `tests/test_workflows.py`.
- `kernel-<stream>.config`, the merged kconfig written by `scripts/merge-config.sh`, ships as a release asset and is listed in `SHA256SUMS`.
- Extended Renovate configuration (`renovate.json`) with automated GitHub Actions digest pinning, Monday batch scheduling, and SSOT regex managers for `versions.json`.
- Root-level governance policies and community contracts (`CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`, `MAINTAINERS.md`, `SECURITY.md`, `SUPPORT.md`).
- Static code hygiene tooling configs (`.editorconfig`, `.gitleaks.toml`, `.markdownlint.json`, `.codespellrc`, `.pre-commit-config.yaml`, `.semgrepignore`).
- Google Release Please automated release management (`release-please-config.json`, `.release-please-manifest.json`).
- Python test harness dependencies (`requirements-test.txt`) and unified pytest/coverage/ruff configurations in `pyproject.toml`.

### Changed
- **Breaking**: the `kernel_release_published` dispatch payload sent to `cordanaLLM/imago` carries `tag` next to `stream` and `version`; imago downloads the release named by the tag, verifies the cosign bundle over `SHA256SUMS` and every manifest digest, and refuses a payload without all three. `scripts/notify_downstream.sh` sends the same `tag` (`RELEASE_TAG`, required; the script refuses a tag the resolver refuses, and exits 1 without a token instead of skipping the dispatch).
- **Breaking**: a kernel release tag is `v<version>-<stream>-lusoris<N>`, where `<stream>` is a key of `versions.json` and `<version>` is that stream's version. `scripts/resolve_release_tag.py` replaces the inline resolver of `publish-release.yml`, which fell back to `mainstream` for any tag (the previous `RAW_TAG` export was never set, so every release resolved to `mainstream`). Only `N=1` is accepted until the kernel build reads the revision (issue #18). `publish-release.yml` also requires the run to start from that tag (`GITHUB_REF` must be `refs/tags/<tag>`) and names it as the release tag. A tag such as `v7.2.4-lusoris1` is refused.
- **Breaking**: the repository's own releases are tagged `nucleus-v<X.Y.Z>` (`include-component-in-tag`) and never start `publish-release.yml`.
- `publish-release.yml` requires `KERNEL_FORGE_TOKEN` as its first step and fails there with an error naming the secret; the downstream dispatch has no fallback to `GITHUB_TOKEN`. The dispatch action is pinned to `peter-evans/repository-dispatch` v3.0.0, since the previous pin did not exist upstream.
- `make lint-pins` (`scripts/check-action-pins.sh`, also run by `ci.yml`) verifies that every action pin resolves upstream to the tag named in its comment.

## [0.1.0] - 2026-09-10

### Added
- Initial repository bootstrap for `cordanaLLM/nucleus`.
- Multi-stream kernel compilation matrix across 4 streams:
  - `bleeding` (Linux 7.3-rc2) for Blackwell RTX 5090/B200, CXL 3.0, and sched-ext.
  - `mainstream` (Linux 7.2.4) for Intel Battlemage Xe2, AMD ROCm 10, and NVIDIA 565/610.
  - `lts` (Linux 6.18.50) for enterprise Kubernetes nodes, OpenZFS 2.3, and CloudNativePG.
  - `realtime` (Linux 7.2-rt) for full PREEMPT_RT deterministic low-latency edge workloads.
- Multi-architecture native compilation and cross-compilation support for `x86_64`, `arm64`, and `riscv64`.
- Modular, security-hardened KConfig fragment framework meeting Kernel Self-Protection Project (KSPP) and CIS Linux Benchmark Level 2 baselines.
- Single Source of Truth `versions.json` declarative manifest backed by JSON Schema validation `versions.schema.json`.
- NASA/JPL Power of 10 compliant shell build pipelines (`scripts/build-kernel.sh`, `scripts/kconfig/merge_config.sh`) with strict ShellCheck compliance.
- Automated downstream dispatch contracts for integration into `cordanaLLM/imago`.
