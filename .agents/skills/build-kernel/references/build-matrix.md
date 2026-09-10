# Kernel Stream & Architecture Build Matrix Reference

> Layer 3 reference for `/build-kernel`. Details target matrix, compiler toolchains, configuration layering, and packaging artifacts.

---

## 1. Release Stream Matrix

| Stream | Version | Upstream Status | Primary Focus | Default Scheduler |
| :--- | :--- | :--- | :--- | :--- |
| `bleeding` | 7.3-rc2 | mainline | Next-gen silicon (Blackwell, CXL 3.0, AVX10.2) | `sched-ext` / EEVDF |
| `mainstream` | 7.2.4 | stable | Production standard (Battlemage Xe2, ROCm 10, RTX 50) | EEVDF |
| `lts` | 6.18.50 | longterm | Enterprise Kubernetes, OpenZFS 2.3, CNPG databases | EEVDF |
| `realtime` | 7.2-rt | preempt_rt | Industrial edge, audio, deterministic low-latency | PREEMPT_RT |

---

## 2. Architecture Target Matrix

| Architecture | Target Triple | Compiler Toolchain | KConfig Base | Target Artifacts |
| :--- | :--- | :--- | :--- | :--- |
| `x86_64` | `x86_64-linux-gnu` | GCC 15 / Clang 20 | `kconfig/x86_64.config` | `linux-image-*.deb`, `*.efi` |
| `arm64` | `aarch64-linux-gnu` | GCC 15 / Clang 20 | `kconfig/arm64.config` | `linux-image-*.deb`, `*.efi` |
| `riscv64` | `riscv64-linux-gnu` | GCC 15 / Clang 20 | `kconfig/riscv64.config` | `linux-image-*.deb` |

---

## 3. KConfig Fragment Hierarchy

Compilation layers fragments in deterministic order:
1. `kconfig/<arch>.config`: Base architecture, CPU topologies, memory models, hardware timers.
2. `kconfig/security-hardened.config`: KSPP baseline, stack protection, usercopy hardening, eBPF containment.
3. Stream/Patch overlays: e.g. `0001-sched-ext-tuning.patch` enabling `CONFIG_SCHED_CLASS_EXT=y`.

---

## 4. Verification Checkpoints

- Check MD5/SHA256 of downloaded tarball against kernel.org release signatures.
- Execute `make olddefconfig` or `scripts/kconfig/merge_config.sh` without interactive prompts.
- Ensure output packages include both image and development headers (`linux-image-*`, `linux-headers-*`).
