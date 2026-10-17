# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1](https://github.com/cordanaLLM/nucleus/compare/nucleus-v0.3.0...nucleus-v0.3.1) (2026-10-17)


### Features

* **boot:** boot arm64 and riscv64 kernels under QEMU in the matrix ([#170](https://github.com/cordanaLLM/nucleus/issues/170)) ([3e88a16](https://github.com/cordanaLLM/nucleus/commit/3e88a1645356a23a9895a7ae830622cdbb15277b))
* **boot:** refuse each leg's console when judged with another release ([#188](https://github.com/cordanaLLM/nucleus/issues/188)) ([11ad0ee](https://github.com/cordanaLLM/nucleus/commit/11ad0ee77096bf5626078ea96a3aaf983b00dc37))
* **build:** sign kernel modules after the build with a recorded step ([#183](https://github.com/cordanaLLM/nucleus/issues/183)) ([8582d5f](https://github.com/cordanaLLM/nucleus/commit/8582d5f0c14467e8db61549bacd33d9eee957e41))
* **kconfig:** build a Kubernetes node on every stream, and refuse a build without a claimed option ([#138](https://github.com/cordanaLLM/nucleus/issues/138)) ([b7f1342](https://github.com/cordanaLLM/nucleus/commit/b7f134257ab75a33521f0bd05dd905b506394599))
* **kconfig:** build the eight options the reference node sets that lusoris2 left off ([#145](https://github.com/cordanaLLM/nucleus/issues/145)) ([0949e1d](https://github.com/cordanaLLM/nucleus/commit/0949e1dddd7f959b4bee3c87cf87cbaa7a16634d)), closes [#144](https://github.com/cordanaLLM/nucleus/issues/144)
* **requirements:** check one kernel configuration against a requirement ([#176](https://github.com/cordanaLLM/nucleus/issues/176)) ([677dd40](https://github.com/cordanaLLM/nucleus/commit/677dd401b68363b9ac3a1f2e891e6c1a1e9a0d9a))
* **requirements:** verify module-abi against the build record (ADR-0015) ([#165](https://github.com/cordanaLLM/nucleus/issues/165)) ([6e642de](https://github.com/cordanaLLM/nucleus/commit/6e642def003fe7f4a5b409319f271dc8bf0b279f))
* **uki:** build a UKI from each leg's own kernel in the build matrix ([#174](https://github.com/cordanaLLM/nucleus/issues/174)) ([1bf4330](https://github.com/cordanaLLM/nucleus/commit/1bf433093936ae47e50be70e5df093d05af16617))


### Bug Fixes

* **boot-smoke:** accept a printk timestamp after the release on the init's line ([#127](https://github.com/cordanaLLM/nucleus/issues/127)) ([d57e889](https://github.com/cordanaLLM/nucleus/commit/d57e889f0eca6a1afc8a8b07277ffc8ac60bfdb7)), closes [#126](https://github.com/cordanaLLM/nucleus/issues/126)
* **ci:** bound the required-checks wait by a wall-clock deadline ([#185](https://github.com/cordanaLLM/nucleus/issues/185)) ([ee7e4dd](https://github.com/cordanaLLM/nucleus/commit/ee7e4dd72842aa3270341f48a36c166d2cf622f9))
* **ci:** clear the eleven HISS findings in existing files ([#108](https://github.com/cordanaLLM/nucleus/issues/108)) ([b431d3e](https://github.com/cordanaLLM/nucleus/commit/b431d3e31fd81ace4ba159e31f709d2fae6928c5))
* **ci:** close the two open CodeQL notes ([#149](https://github.com/cordanaLLM/nucleus/issues/149)) ([78511c0](https://github.com/cordanaLLM/nucleus/commit/78511c025101bcd932a42e9924da86a4c01e94ba)), closes [#148](https://github.com/cordanaLLM/nucleus/issues/148)
* **ci:** judge stale check statuses and wait for late checks in required-checks ([#82](https://github.com/cordanaLLM/nucleus/issues/82)) ([47c9fdd](https://github.com/cordanaLLM/nucleus/commit/47c9fdd06417c3a0686bc878844732df75d3390a))
* **ci:** pass a skipped job only when the change detector excluded it ([#71](https://github.com/cordanaLLM/nucleus/issues/71)) ([dd825dc](https://github.com/cordanaLLM/nucleus/commit/dd825dc233b74a754d88597574f5a32705dc0fc1))
* **ci:** refuse to attest when supply-chain has no package ([#68](https://github.com/cordanaLLM/nucleus/issues/68)) ([074432c](https://github.com/cordanaLLM/nucleus/commit/074432c7f9dbcf270ff3900b3a8084cfcf7a350b))
* **docker:** build the builder container from the CI image and toolchain ([#80](https://github.com/cordanaLLM/nucleus/issues/80)) ([84dd57c](https://github.com/cordanaLLM/nucleus/commit/84dd57cd38bc7ae65a9f7081fd7a3ee9332d7c0f))
* **gate:** refuse an artifact above imago's 512 MiB per-artifact bound ([#96](https://github.com/cordanaLLM/nucleus/issues/96)) ([f6e56a6](https://github.com/cordanaLLM/nucleus/commit/f6e56a684b41956fee894f7c1dbc9fe631a51ec5)), closes [#95](https://github.com/cordanaLLM/nucleus/issues/95)
* **governance:** name the praetor pin PRAETOR_REF so git hooks use it ([#167](https://github.com/cordanaLLM/nucleus/issues/167)) ([125edb8](https://github.com/cordanaLLM/nucleus/commit/125edb844be6dc5083435d62912c2a4a622e8ef9))
* **hindsight-mcp:** flush only the bank's own buffered facts, refuse https instead of sending it in clear ([#88](https://github.com/cordanaLLM/nucleus/issues/88)) ([328dcdf](https://github.com/cordanaLLM/nucleus/commit/328dcdf9bee611d0cbc15fb635d12bac19e2801d))
* **kconfig:** build BTF into riscv64 kernels and gate BTF on every arch ([#159](https://github.com/cordanaLLM/nucleus/issues/159)) ([5b25755](https://github.com/cordanaLLM/nucleus/commit/5b25755cb93662f3e52603fa59e21065a8fa3416))
* **kconfig:** carry zlib deflate on every leg, and move all streams to current upstream ([#106](https://github.com/cordanaLLM/nucleus/issues/106)) ([987043f](https://github.com/cordanaLLM/nucleus/commit/987043fe397f76e6c4138dfe6090f2dfba207191)), closes [#105](https://github.com/cordanaLLM/nucleus/issues/105)
* **kconfig:** enable sched-ext on riscv64 ([#162](https://github.com/cordanaLLM/nucleus/issues/162)) ([d1ab20e](https://github.com/cordanaLLM/nucleus/commit/d1ab20ec600be8c9075e824b9a91b74acb3f5a45))
* **lint:** make fmt-check, audit and the function-length check fail on findings ([#75](https://github.com/cordanaLLM/nucleus/issues/75)) ([a83e1bc](https://github.com/cordanaLLM/nucleus/commit/a83e1bc59ac943f62096f634c7ec8b0bd3c9cfd0))
* **package:** date debian/changelog from SOURCE_DATE_EPOCH ([#171](https://github.com/cordanaLLM/nucleus/issues/171)) ([8ad187a](https://github.com/cordanaLLM/nucleus/commit/8ad187aa0b804fd181e8a8a7153f76fb7d8b87b5))
* **patches:** drop the carried patch that applied to no stream, and check the rest apply ([#173](https://github.com/cordanaLLM/nucleus/issues/173)) ([331ab42](https://github.com/cordanaLLM/nucleus/commit/331ab42e61ca842289a8fe440d9c907cb487b72e))
* **release:** generate the artifact manifest in a tested script that proves every digest ([#90](https://github.com/cordanaLLM/nucleus/issues/90)) ([3d10057](https://github.com/cordanaLLM/nucleus/commit/3d1005710f236cae11f01a6d85e4383a6a4b6c16))
* **release:** refuse to publish a tag whose release already exists ([#100](https://github.com/cordanaLLM/nucleus/issues/100)) ([ed537d0](https://github.com/cordanaLLM/nucleus/commit/ed537d0ca69de2e26b4ef63f80c2155171dee000)), closes [#99](https://github.com/cordanaLLM/nucleus/issues/99)
* **repro:** compare two real build directories, and give no verdict on a dry run ([#78](https://github.com/cordanaLLM/nucleus/issues/78)) ([a7b416a](https://github.com/cordanaLLM/nucleus/commit/a7b416ac0223e5dd31fb8541deb439a0eeda5efb))
* **supply-chain:** verify each published release instead of re-signing nothing ([#134](https://github.com/cordanaLLM/nucleus/issues/134)) ([89a192c](https://github.com/cordanaLLM/nucleus/commit/89a192c4e20de098e0f4267a0fae4f2e2967a1c9)), closes [#133](https://github.com/cordanaLLM/nucleus/issues/133)

## [0.3.0](https://github.com/cordanaLLM/nucleus/compare/nucleus-v0.2.0...nucleus-v0.3.0) (2026-10-11)


### ⚠ BREAKING CHANGES

* **licence:** licence Apache-2.0 -> EUPL-1.2 Migration: no code changes; a consumer of 0.3.0 or later reads the licence section of the README for what the new terms cover.

### Bug Fixes

* **kconfig:** let BPF LSM attach on every leg, and release revisions above 1 ([#47](https://github.com/cordanaLLM/nucleus/issues/47)) ([852be74](https://github.com/cordanaLLM/nucleus/commit/852be742eb173700d5ef93b0c6f867b855f9c640))


### Miscellaneous Chores

* **licence:** relicense to EUPL-1.2 and check licence information ([#55](https://github.com/cordanaLLM/nucleus/issues/55)) ([89bd949](https://github.com/cordanaLLM/nucleus/commit/89bd949dbd51e87cc42fd138bfd4ccfdd083a1ad)), closes [#54](https://github.com/cordanaLLM/nucleus/issues/54)

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
