# ADR-0013: BTF on Every Architecture

Date: 2026-10-16

## Status

Proposed (2026-10-16)

Supersedes the riscv64 debug information consequence of [ADR-0009](0009-kernel-compilation-and-artifact-gate.md) ("riscv64 kernels carry no debug information and so no BTF: `kconfig/riscv64.config` does not request `DEBUG_INFO_BTF`, unlike the x86_64 and arm64 fragments"). The rest of ADR-0009 stands.

---

## Context

Compile Once – Run Everywhere (CO-RE) eBPF programs require runtime type information exported by the kernel via `/sys/kernel/btf/vmlinux`.

When multi-architecture kernel compilation was standardized in [ADR-0009](0009-kernel-compilation-and-artifact-gate.md), `kconfig/x86_64.config` and `kconfig/arm64.config` declared the BPF Type Format (BTF) dependency chain (`CONFIG_DEBUG_KERNEL=y`, `CONFIG_DEBUG_INFO_DWARF5=y`, and `CONFIG_DEBUG_INFO_BTF=y`), but `kconfig/riscv64.config` omitted debug information and BTF. Consequently, riscv64 kernels lacked `/sys/kernel/btf/vmlinux`, preventing modern CO-RE eBPF tools from loading on riscv64 nodes.

Because riscv64 lacked BTF, BTF presence could not be enforced as an architectural invariant across the repository's entire compilation matrix: the resolved configuration checks (`scripts/check_required_config.py`) and the artifact gate (`scripts/check_kernel_artifacts.py`) could not mandate BTF on all architectures without failing riscv64 builds.

---

## Decision

1. **Every Architecture Fragment Declares the BTF Chain**:
   All architecture fragments under `kconfig/*.config` (`x86_64.config`, `arm64.config`, `riscv64.config`) declare the full BTF dependency chain:

   ```kconfig
   CONFIG_DEBUG_KERNEL=y
   CONFIG_DEBUG_INFO_DWARF5=y
   CONFIG_DEBUG_INFO_BTF=y
   ```

   In `kconfig/riscv64.config`, the default kernel configuration (`defconfig`) does not enable `CONFIG_DEBUG_INFO_REDUCED`, so unlike `arm64.config`, no explicit unsetting is required.

2. **Repository-Wide BTF Claim in Required Configuration**:
   Add a `btf` claim to `kconfig/required-config.json` covering all streams and all architectures:

   ```json
   {
     "id": "btf",
     "claim": "BTF in vmlinux for CO-RE eBPF (docs/streams.md, issue #112)",
     "streams": "all",
     "architectures": "all",
     "symbols": {
       "CONFIG_DEBUG_INFO_BTF": "y"
     }
   }
   ```

   During configuration resolution (`scripts/merge-config.sh --source-tree`), `scripts/check_required_config.py` evaluates this claim against the generated `.config`. Any stream or architecture leg where `CONFIG_DEBUG_INFO_BTF=y` fails to survive `olddefconfig` is refused, and `merge-config.sh` exits non-zero.

3. **Artifact Gate Enforces BTF in Kernel Packages**:
   In `scripts/check_kernel_artifacts.py`, add `check_kernel_config(data, exp)` within `check_image`. The gate extracts `/boot/config-<kernelrelease>` from the `linux-image` Debian package using `dpkg-deb` and asserts that `CONFIG_DEBUG_INFO_BTF=y` is present. Any kernel package lacking BTF in its packaged configuration is refused before artifacts can be checksummed, attested, or published.

4. **Automated Verification Suites**:
   Enforce the decision through dedicated unit and integration tests:
   - `tests/test_kconfig.py::test_every_architecture_fragment_declares_btf`: verifies that every fragment in `kconfig/*.config` declares `CONFIG_DEBUG_INFO_BTF=y`.
   - `tests/test_check_kernel_artifacts.py::test_kernel_config_must_carry_btf`: verifies `check_kernel_config` rejects configurations lacking BTF across all architectures (`x86_64`, `arm64`, `riscv64`).
   - `tests/test_check_kernel_artifacts.py::test_check_image_refuses_config_without_btf`: verifies `check_image` rejects Debian image packages whose internal configuration lacks BTF.
   - `tests/test_check_kernel_artifacts.py::test_a_release_whose_config_lacks_btf_is_refused`: end-to-end CLI gate test refusing a release whose configuration lacks BTF.

This decision landed in pull request #159 (addressing issue #112), merged in commit `5b25755cb`.

---

## Evidence

The changes were compiled, resolved, and verified in GitHub Actions workflow `build-matrix.yml` run 38829455519 with `dry_run=false` across all twelve stream and architecture legs (4 streams x 3 architectures):

- **Compilation and Gating**: All twelve legs compiled and passed `scripts/check_kernel_artifacts.py`. The cross-compiled riscv64 legs took 23 to 39 minutes each, including runner setup.
- **Resolved Configuration**: Inspection of the resolved `.config` artifacts for riscv64 (`mainstream` 7.2.10 and `lts` 6.18.56) verified that `CONFIG_PAHOLE_VERSION=131` was detected and `CONFIG_DEBUG_INFO_BTF=y` survived `olddefconfig`.
- **Artifact Sizes**: Measured artifact sizes for riscv64 packages in run 38829455519:
  - `linux-image` Debian package: 30,110,494 bytes (`lts`) and 31,559,200 bytes (`mainstream`).
  - `vmlinuz` kernel image: 12,868,774 bytes (`lts`) and 13,189,948 bytes (`mainstream`).
  All generated artifacts remain well under the 512 MiB per-artifact ceiling enforced by `cordanaLLM/imago` and `scripts/check_kernel_artifacts.py`.

---

## Consequences

### Positive

- **Full CO-RE eBPF Capability**: riscv64 kernels generate `/sys/kernel/btf/vmlinux`, allowing eBPF instrumentation, tracing tools, and observability agents to load without kernel headers.
- **Uniform Architectural Contract**: BTF is a mandatory requirement across all four streams (`bleeding`, `mainstream`, `lts`, `realtime`) and all three architectures (`x86_64`, `arm64`, `riscv64`).
- **Two-Barrier Gate Enforcement**: Missing BTF is refused declaratively at configuration resolution time via `kconfig/required-config.json` and artifactually at packaging time via `scripts/check_kernel_artifacts.py`.

### Negative

- **Larger Package and Image Sizes**: the riscv64 image package grows from 16.8 MB and `vmlinuz` from 11.0 MB (mainstream, local build of 2026-09-29, the size table in [Packaging](../packaging.md)) to 30.1 to 31.6 MB and 12.9 to 13.2 MB (run 38829455519).
- **Longer Builds**: DWARF5 debug information and the `pahole` BTF pass add compile time to every riscv64 leg; this record has no like-for-like measurement of the increase.
- **Toolchain Dependency**: Kernel builds require `pahole` 1.22 or newer; the `ubuntu:26.04` builder resolved `CONFIG_PAHOLE_VERSION=131`.

### Neutral

- **sched-ext on riscv64**: While `CONFIG_SCHED_CLASS_EXT` requires `CONFIG_DEBUG_INFO_BTF`, `SCHED_CLASS_EXT` is not yet declared for riscv64; tracked in issue #160.
- **Cross-Compilation Headers**: riscv64 builds continue to be cross-compiled and omit `linux-headers` packages.

---

## Compliance

1. **`kconfig/required-config.json`**: The `btf` claim is validated for every stream and architecture by `scripts/check_required_config.py` during source tree configuration merging (`scripts/merge-config.sh --source-tree`).
2. **`scripts/check_kernel_artifacts.py`**: The `check_kernel_config` routine rejects any `linux-image` Debian package lacking `CONFIG_DEBUG_INFO_BTF=y` in its `/boot/config-<kernelrelease>`.
3. **`tests/test_kconfig.py`**: `test_every_architecture_fragment_declares_btf` ensures no architecture fragment drops `CONFIG_DEBUG_INFO_BTF=y`.
4. **`tests/test_check_kernel_artifacts.py`**: `test_kernel_config_must_carry_btf`, `test_check_image_refuses_config_without_btf`, and `test_a_release_whose_config_lacks_btf_is_refused` verify gate refusal on configurations lacking BTF.
5. **Continuous Integration**: `.github/workflows/verify-requirements.yml` and `.github/workflows/build-matrix.yml` resolve, survival-check and gate all twelve legs on pull requests that touch the build path (the `paths` filters of both workflows).

---

## References

- Issue #112: `riscv64 kernels carry no BTF: kconfig/riscv64.config does not request debug information`.
- Pull Request #159: `fix(kconfig): build BTF into riscv64 kernels and gate BTF on every arch` (merge commit `5b25755cb93662f3e52603fa59e21065a8fa3416`).
- Issue #160: `riscv64 kernels build without sched-ext: SCHED_CLASS_EXT is not declared`.
- GitHub Actions run 38829455519: `build-matrix.yml` (dry_run=false).
- [ADR-0008: Signed Kernel Sources and a Resolved, Survival-Checked Kernel Configuration](0008-signed-kernel-sources-and-resolved-configuration.md).
- [ADR-0009: Kernel Compilation, the Artifact Gate and the Boot Smoke Test](0009-kernel-compilation-and-artifact-gate.md).
- [ADR-0011: Function Tracing in the Baseline for BPF Trampolines, and Release Revisions Above 1](0011-bpf-trampoline-ftrace-and-release-revisions.md).
