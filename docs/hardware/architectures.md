# Multi-Architecture Strategy & Compilation Matrix

> In-depth technical guide on cross-compiling, validating, and optimizing Linux kernels for `x86_64`, `arm64`, and `riscv64` targets.

---

## 1. Architectural Support Matrix

`cordanaLLM/nucleus` targets three primary ISA families, each optimized for specific virtualization, cloud-native, and bare-metal hardware platforms:

| Architecture | ISA Baseline | Target Microarchitectures | Boot Artifact | Page Size | Primary Virtualization |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`x86_64`** | `x86-64-v3` (AVX2, BMI2) | AMD Zen 3/4/5, Intel Sapphire Rapids / Emerald Rapids / Granite Rapids | `arch/x86/boot/bzImage` | 4 KB | KVM (Intel VMX / AMD SVM) |
| **`arm64`** | `armv8.2-a+crypto+fp16` | Ampere Altra/AmpereOne, AWS Graviton 3/4, Apple Silicon VMs | `arch/arm64/boot/Image.gz` | 4 KB / 64 KB | KVM (ARM GICv3 / GICv4) |
| **`riscv64`** | `rva22` / `rva23` (RV64GCVB) | SiFive P550/P670, StarFive JH7110, Tenstorrent Ascalon | `arch/riscv/boot/Image` | 4 KB | KVM (RISC-V AIA / PLIC) |

```mermaid
graph TD
    SRC["Unified Upstream Source (versions.json)"] --> KCONF_MERGE["scripts/merge-config.sh"]

    KCONF_MERGE --> X86_CONF["x86_64 Hardened Config"]
    KCONF_MERGE --> ARM_CONF["arm64 Hardened Config"]
    KCONF_MERGE --> RISCV_CONF["riscv64 Hardened Config"]

    X86_CONF --> TOOL_X86["LLVM/Clang 20 (Native/Cross)"]
    ARM_CONF --> TOOL_ARM["LLVM/Clang 20 + aarch64-linux-gnu-binutils"]
    RISCV_CONF --> TOOL_RISCV["LLVM/Clang 20 + riscv64-linux-gnu-binutils"]

    TOOL_X86 --> OUT_X86["linux-image-*_amd64.deb / BOOTX64.EFI"]
    TOOL_ARM --> OUT_ARM["linux-image-*_arm64.deb / BOOTAA64.EFI"]
    TOOL_RISCV --> OUT_RISCV["linux-image-*_riscv64.deb / BOOTRISCV64.EFI"]
```

---

## 2. Toolchain Selection: LLVM/Clang 20 vs. GNU GCC 15

All kernels in `cordanaLLM/nucleus` default to the **LLVM/Clang 20** toolchain via `LLVM=1`:

```bash
make ARCH=arm64 LLVM=1 -j"$(nproc)" bindeb-pkg
```

### Advantages of LLVM=1:
1. **ThinLTO (Link-Time Optimization)**: Whole-program cross-translation unit optimization (`CONFIG_LTO_CLANG_THIN=y`), generating 8–12% higher throughput on IPC-bound database workloads and tighter kernel text layout.
2. **CFI (Control Flow Integrity)**: Forward-edge control flow integrity (`CONFIG_CFI_CLANG=y`) stops Return-Oriented Programming (ROP) and Jump-Oriented Programming (JOP) attacks by validating indirect call targets at runtime.
3. **Single Cross-Target Binary**: A single Clang binary (`clang-20`) can compile for `x86_64`, `aarch64`, and `riscv64` simply by supplying `--target=...`, avoiding fragmented GNU cross-compilers.
4. **Integrated LLVM Utilities**: Build scripts utilize `llvm-ar`, `llvm-nm`, `llvm-objcopy`, `llvm-strip`, and `lld` (the LLVM linker), ensuring 3x faster link times compared to GNU `ld.bfd`.

---

## 3. Architecture-Specific Kernel Configurations

### 3.1 `x86_64` (AMD64)
- **Architecture Level**: Built with `CONFIG_GENERIC_CPU=y` with microarchitecture level `v3` baseline (`AVX2`, `BMI1`, `BMI2`, `FMA`).
- **Memory Management**: 5-level paging enabled (`CONFIG_X86_5LEVEL=y`) supporting up to 4 PB physical memory and 128 PB virtual address space.
- **Microcode Loader**: Early microcode updating (`CONFIG_MICROCODE=y`) built-in for early speculative execution mitigation (Spectre v2, Retbleed, Downfall, SRSO).
- **Virtualization Acceleration**: the host hypervisor `CONFIG_KVM=m` with both vendor backends, `CONFIG_KVM_INTEL=m` and `CONFIG_KVM_AMD=m` (imago `FLAVOR-BASE`, Aegis-OS `REQ-BOOT-01`), and device passthrough through `CONFIG_VFIO=m` and `CONFIG_VFIO_PCI=m` behind the Intel and AMD IOMMU drivers.
- **Requirement Prerequisites** (`kconfig/x86_64.config`): `CONFIG_IKCONFIG_PROC` for the `/proc/config.gz` probe; `CONFIG_DEBUG_KERNEL` and `CONFIG_DEBUG_INFO_DWARF5` so that `CONFIG_DEBUG_INFO_BTF` survives `olddefconfig` (the build host needs pahole 1.22 or newer); `CONFIG_BPF_LSM`; `CONFIG_POWERCAP` with `CONFIG_INTEL_RAPL=m`. The requirement documents and their bindings are in [Kernel Streams](../streams.md#5-consumer-bindings).

### 3.2 `arm64` (AArch64)
- **Architecture Level**: Enforces ARMv8.2-A with Crypto Extensions (`CONFIG_ARM64_CRYPTO=y`, AES/SHA2/SHA3 accelerated via NEON/SVE).
- **Pointer Authentication & BTI**:
  - `CONFIG_ARM64_PTR_AUTH=y`: In-kernel pointer authentication preventing return address hijacking.
  - `CONFIG_ARM64_BTI=y`: Branch Target Identification to enforce valid branch landing pads.
- **Page Size Policy**:
  - Standard clouds (Proxmox, KVM, Docker): 4 KB page size (`CONFIG_ARM64_4K_PAGES=y`) for compatibility with commercial container images.
  - High-Performance Database / HPC: Optional 64 KB page configuration (`CONFIG_ARM64_64K_PAGES=y`) to minimize TLB miss penalties on multi-terabyte memory nodes.
- **ACPI & DeviceTree Dual Boot**: Supports both ACPI enterprise boot (Neoverse servers, Ampere Altra) and Flattened Device Tree (FDT) for edge boards.
- **BTF for sched-ext**: `CONFIG_SCHED_CLASS_EXT` depends on `CONFIG_DEBUG_INFO_BTF`, so `kconfig/arm64.config` declares its chain: `CONFIG_DEBUG_KERNEL`, `CONFIG_DEBUG_INFO_DWARF5`, and `# CONFIG_DEBUG_INFO_REDUCED is not set`, which the arm64 defconfig would otherwise set.
- **BPF trampolines**: the arm64 defconfig unsets `CONFIG_FTRACE`; the security baseline sets it with `CONFIG_FUNCTION_TRACER`, `CONFIG_DYNAMIC_FTRACE` and `CONFIG_DYNAMIC_FTRACE_WITH_DIRECT_CALLS`. Direct calls need GCC's `-fpatchable-function-entry` (`DYNAMIC_FTRACE_WITH_ARGS`) and, before Linux 7.3, `DYNAMIC_FTRACE_WITH_CALL_OPS`, which a `CFI` or size-optimized build loses ([ADR-0011](../adr/0011-bpf-trampoline-ftrace-and-release-revisions.md)).

### 3.3 `riscv64` (RISC-V 64-bit)
- **Profile Alignment**: RVA22 / RVA23 profiles with standard extensions: `rv64imafdc_zicsr_zifencei_zba_zbb_zbs_zicboz_zicbom`.
- **Vector Extension**: `CONFIG_RISCV_ISA_V=y` enabling hardware vector computing (RVV 1.0) for in-kernel cryptographic hashing and user-space AI workloads.
- **Advanced Interrupt Architecture (AIA)**:
  - `CONFIG_RISCV_AIA=y`: Native message-signaled interrupts (IMSIC) and incoming interrupt controller (APLIC) replacing legacy PLIC bottlenecks.
- **SBI (Supervisor Binary Interface)**: Compliant with RISC-V SBI v2.0+ specification for system reset, timer, and IPI handling.
- **KASLR**: `CONFIG_RANDOMIZE_BASE` (the security baseline) depends on `CONFIG_RELOCATABLE` on riscv since Linux 7.2, and the riscv defconfig leaves it unset, so `kconfig/riscv64.config` declares it.
- **BPF trampolines**: `CONFIG_DYNAMIC_FTRACE_WITH_DIRECT_CALLS` (the security baseline) needs `DYNAMIC_FTRACE_WITH_CALL_OPS` on riscv, which a `CFI` build loses, and `HAVE_DYNAMIC_FTRACE` needs GCC's `-fpatchable-function-entry=8` and, with compressed instructions (`RISCV_ISA_C`), `-fmin-function-alignment` ([ADR-0011](../adr/0011-bpf-trampoline-ftrace-and-release-revisions.md)).

---

## 4. Hermetic Cross-Compilation Pipeline

Cross-compilation is executed without installing system-wide foreign packages through pinned OCI build images built from `docker/Dockerfile.builder`:

```bash
# Build multi-architecture builder container
make docker-builder
# Or manually:
docker build -t lusoris-kernel-builder -f docker/Dockerfile.builder .
```

The container provides LLVM/Clang, cross-compilers (`gcc-aarch64-linux-gnu`, `gcc-riscv64-linux-gnu`), `systemd-ukify`, `dracut`, `diffoscope`, and QEMU emulators:

```bash
#!/usr/bin/env bash
# scripts/cross-compile-arm64.sh
set -euo pipefail

TARGET_ARCH="arm64"
CROSS_COMPILE="aarch64-linux-gnu-"
KERNEL_OUT="/opt/lusoris/build/mainstream-${TARGET_ARCH}"

# Fetch the mainstream source and prove it: pinned sha256 and kernel.org signature
./scripts/fetch-kernel-source.sh --stream=mainstream --dest=/usr/src/linux

# Resolve the configuration: the arm64 defconfig, the fragments through the kernel's
# merge_config.sh, make olddefconfig, then the survival check. The ARCH, the defconfig and
# the CROSS_COMPILE prefix come from versions.json architectures.arm64.
./scripts/merge-config.sh --arch="${TARGET_ARCH}" --stream=mainstream \
  --source-tree=/usr/src/linux --build-dir="${KERNEL_OUT}"

# Compile kernel, modules, and generate Debian packages
make -C /usr/src/linux \
  O="${KERNEL_OUT}" \
  ARCH="${TARGET_ARCH}" \
  CC="clang" \
  CROSS_COMPILE="${CROSS_COMPILE}" \
  LLVM=1 \
  -j"$(nproc)" \
  bindeb-pkg
```

---

## 5. MicroVM Boot Verification Matrix

To ensure build integrity before pushing to release registries, each compiled kernel undergoes an automated headless QEMU microVM boot verification test:

```bash
# Automated verification test (tests/test_qemu_boot.py)
# Boot timeout: 3.0 seconds max; asserts sub-second cold boot to init
qemu-system-aarch64 \
  -M virt,gic-version=3 \
  -cpu max \
  -m 512M \
  -kernel /opt/lusoris/build/mainstream-arm64/arch/arm64/boot/Image \
  -initrd /opt/lusoris/build/test-initramfs.img \
  -append "console=ttyAMA0 earlycon panic=1" \
  -nographic \
  -no-reboot
```

Each architecture must verify:
1. Zero kernel panics or unhandled IRQ traps.
2. Clean initialization of VirtIO block and network devices.
3. Successful invocation of `/sbin/init` or `/init` test harness in $\le 650$ ms.
