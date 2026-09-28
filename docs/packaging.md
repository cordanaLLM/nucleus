# Kernel Packaging & Delivery Architecture

> Authoritative engineering guide covering Debian `bindeb-pkg` compilation, systemd-ukify Unified Kernel Image (UKI) synthesis, initramfs contracts, and OCI distribution.

---

## 1. Overview & Dual Packaging Strategy

`cordanaLLM/nucleus` delivers compiled kernels through two complementary delivery formats:

1. **Standard Debian Packages (`.deb`)**:
   - `linux-image-<version>-<stream>-<arch>.deb`: Contains the compressed kernel binary (`vmlinuz`), core drivers/modules (`/lib/modules/<version>`), and Device Tree Blobs (for ARM64/RISC-V).
   - `linux-headers-<version>-<stream>-<arch>.deb`: C headers and Makefiles required for out-of-tree DKMS modules (NVIDIA open kernel modules, OpenZFS 2.3).
   - `linux-libc-dev-<version>-<stream>-<arch>.deb`: Linux API user-space headers for glibc/musl compilation.
   - Designed for standard Debian/Ubuntu OS installations, container base hosts, and golden image provisioning in `cordanaLLM/imago`.

2. **Unified Kernel Images (UKI, `.efi`)**:
   - Single, signed, self-contained UEFI PE binary combining the Linux kernel (`.linux`), microcode + initramfs (`.initrd`), kernel command line (`.cmdline`), and OS release metadata (`.osrel`).
   - Designed for modern UEFI Secure Boot, TPM 2.0 measured boot, and direct network streaming (iPXE / systemd-boot).

```mermaid
flowchart TD
    SRC["Upstream Kernel Source + Curated Patches"] --> KCONF["Merged Hardened KConfig (.config)"]
    KCONF --> BUILD["Hermetic LLVM/Clang Builder"]
    
    BUILD -->|"make bindeb-pkg"| DEB_STAGE["Debian Packaging Pipeline"]
    DEB_STAGE --> DEB_IMG["linux-image-*.deb"]
    DEB_STAGE --> DEB_HDR["linux-headers-*.deb"]
    DEB_STAGE --> DEB_DEV["linux-libc-dev-*.deb"]
    
    BUILD -->|"vmlinux / bzImage + modules"| UKI_STAGE["systemd-ukify Pipeline"]
    INITRD["Minimal Dracut Initramfs + CPU Microcode"] --> UKI_STAGE
    CMDLINE["Immutable Kernel Cmdline (console=ttyS0 quiet)"] --> UKI_STAGE
    CERT["UEFI Secure Boot Keys / Cosign OIDC"] --> UKI_STAGE
    
    UKI_STAGE --> UKI_BIN["signed-kernel-*.efi (UKI)"]
    
    DEB_IMG --> APT_REPO["APT Repository (apt.example.com)"]
    DEB_HDR --> APT_REPO
    DEB_DEV --> APT_REPO
    
    UKI_BIN --> OCI_REG["OCI Registry (ghcr.io/cordanallm/nucleus/kernels)"]
```

---

## 2. Native Debian Packaging (`bindeb-pkg`)

The Linux kernel source tree features native Debian packaging targets (`deb-pkg` and `bindeb-pkg`). We utilize `bindeb-pkg` to avoid generating redundant source Debian tarballs (`.orig.tar.gz`), focusing strictly on binary artifacts.

### 2.1 Invocation & Environment Controls
Hermetic builds enforce reproducible timestamps and identity metadata:
```bash
make -C /usr/src/linux \
  O=/opt/lusoris/build/mainstream-x86_64 \
  ARCH=x86_64 \
  LLVM=1 \
  KDEB_PKGVERSION="7.2.4-lusoris1" \
  KBUILD_BUILD_TIMESTAMP="2026-09-10T00:00:00Z" \
  KBUILD_BUILD_USER="builder" \
  KBUILD_BUILD_HOST="kernel-forge.lusoris.org" \
  -j"$(nproc)" \
  bindeb-pkg
```

### 2.2 Localversion & Package Naming Contract
The kernel version string is constructed from upstream release plus a deterministic localversion:
- Upstream: `7.2.4`
- Localversion: `-lusoris1-mainstream-amd64`
- Resulting Kernel Release (`uname -r`): `7.2.4-lusoris1-mainstream-amd64`

This convention prevents collisions with distribution stock kernels (`linux-image-generic`, `linux-image-amd64`) and allows side-by-side installations in `/boot`.

### 2.3 Module Stripping & Debug Symbols
Production builds strip debug symbols from in-tree kernel modules before packaging, reducing `linux-image` size from >800MB to ~85MB:
- `CONFIG_DEBUG_INFO=n` or `CONFIG_DEBUG_INFO_DWARF5=y` with `CONFIG_DEBUG_INFO_SPLIT=y`.
- Module compression uses Zstandard (`CONFIG_MODULE_COMPRESS_ZSTD=y`), accelerating cold-boot module loading times by up to 45%.

---

## 3. Unified Kernel Images (UKI) via `systemd-ukify`

A Unified Kernel Image is an executable UEFI PE binary conforming to the [UAPI Group Boot Loader Specification (Type 2)](https://uapi-group.org/specifications/specs/boot_loader_specification/).

### 3.1 UKI Section Layout
Inside the synthesized `.efi` file, multiple sections are embedded:

| PE Section Name | Contents | Purpose |
| :--- | :--- | :--- |
| `.linux` | `bzImage` / `Image.gz` | The raw compressed Linux kernel executable |
| `.initrd` | CPIO archive (Dracut / Microcode) | Combined early CPU microcode + rootfs discovery initramfs |
| `.cmdline` | UTF-8 text string | Cryptographically pinned kernel parameters (e.g. `root=LABEL=cloudimg-rootfs ro console=ttyS0`) |
| `.osrel` | `/etc/os-release` | System identification for systemd-boot menu presentation |
| `.sbat` | SBAT metadata string | Secure Boot Advanced Targeting for revocation management |
| `.pcrpkey` | Public key PEM | Public key used for TPM 2.0 policy sealing |

### 3.2 Synthesis with `ukify`
`scripts/package-uki.sh` calls `ukify` to assemble and measure the image:
```bash
ukify build \
  --linux=/opt/lusoris/build/mainstream-x86_64/arch/x86/boot/bzImage \
  --initrd=/opt/lusoris/build/initramfs-mainstream-x86_64.img \
  --cmdline="console=tty1 console=ttyS0,115200 root=UUID=5f6a9e10-3b4c-4e8f-9a2d-1c3b5e7f9a12 ro quiet splash loglevel=3 mitigations=auto" \
  --os-release="@/etc/os-release" \
  --uname="7.2.4-lusoris1-mainstream-amd64" \
  --sbat="sbat,1,SBAT Version,sbat,1,https://github.com/systemd/systemd/blob/main/docs/SBAT.md\nlusoris,1,Lusoris Linux,lusoris,1,https://github.com/cordanaLLM/nucleus" \
  --secureboot-private-key="/etc/ssl/certs/db.key" \
  --secureboot-certificate="/etc/ssl/certs/db.crt" \
  --measure \
  --output="/opt/lusoris/output/mainstream-x86_64/BOOTX64.EFI"
```

### 3.3 TPM 2.0 PCR 11 Measurement & Sealing
When booting via `systemd-boot`, the UEFI boot loader measures the entire UKI payload directly into **TPM 2.0 PCR 11**:
- PCR 11 matches the cryptographic digest calculated during `ukify --measure`.
- Disk encryption keys (LUKS2 with `systemd-cryptenroll`) can be sealed to PCR 11: if the kernel, initramfs, or cmdline is altered by even a single bit, the TPM refuses to release the encryption key.

### 3.4 Automated Packaging CLI Drivers
Developers and automation pipelines utilize dedicated shell drivers complying with NASA/JPL Power of 10:

```bash
# Generate native Debian packages (.deb) with headers and libc-dev
./scripts/package-deb.sh --stream=mainstream --arch=x86_64 --dry-run
make package-deb STREAM=mainstream ARCH=x86_64

# Synthesize Unified Kernel Image (UKI) PE binary (.efi) with PCR 11 measurements
./scripts/package-uki.sh --stream=mainstream --arch=x86_64 --dry-run
make package-uki STREAM=mainstream ARCH=x86_64

# Verify byte-level build reproducibility across compilation passes
./scripts/verify-reproducibility.sh --dry-run
make verify-reproducibility
```

---

## 4. Initramfs Contracts & Driver Profiles

Initramfs creation is orchestrated via `dracut` using two distinct deployment profiles:

### 4.1 MicroVM / Cloud Instance Profile (`dracut-micro`)
For virtualization in Proxmox VE, KVM, and QEMU microVMs, boot latency must remain sub-second (< 350ms):
- **Omitted**: Bluetooth, sound, wireless, PCMCIA, legacy IDE, floppy, ISDN.
- **Included**: `virtio_pci`, `virtio_blk`, `virtio_net`, `virtio_scsi`, `virtio_console`, `nvme`, `overlay`, `ext4`, `xfs`.
- **Driver Model**: Core VirtIO storage and network drivers are built directly into the kernel (`=y`), allowing instantaneous pivot-root without waiting for module loading.

### 4.2 Bare-Metal & SAN Profile (`dracut-enterprise`)
For bare-metal physical hypervisors and storage appliances:
- **Included**: Intel i40e/ice, Mellanox ConnectX-5/6/7 (`mlx5_core`), Broadcom bnxt, Broadcom MegaRAID, NVMe-over-Fabrics (TCP/RDMA), OpenZFS root (`zfs`), and multipath.

---

## 5. Distribution & Downstream Delivery

### 5.1 Authenticated APT Repository
Compiled `.deb` packages are imported into an authenticated Debian archive powered by `reprepro`:
- **Repository URL**: `https://apt.example.com/kernels/`
- **Distributions**: `bookworm`, `trixie`, `resolute`
- **Architectures**: `amd64`, `arm64`, `riscv64`
- **Signing**: InRelease files signed with project OpenPGP key.

```bash
# Example downstream consumption in Debian/Ubuntu:
curl -fsSL https://apt.example.com/kernels/archive-key.gpg | gpg --dearmor -o /etc/apt/trusted.gpg.d/lusoris-kernel.gpg
echo "deb [signed-by=/etc/apt/trusted.gpg.d/lusoris-kernel.gpg] https://apt.example.com/kernels/ resolute main" > /etc/apt/sources.list.d/lusoris-kernel.list
apt-get update
apt-get install -y linux-image-7.2.4-lusoris1-mainstream-amd64 linux-headers-7.2.4-lusoris1-mainstream-amd64
```

### 5.2 OCI Registry Distribution (UKI Artifacts)
Signed `.efi` UKI binaries are pushed as OCI artifacts conforming to the OCI Artifact Specification, one OCI repository per stream and architecture under `ghcr.io/cordanallm/nucleus/kernels` ([ADR-0006](adr/0006-oci-registry-namespace.md)). OCI repository names are lowercase, so the `cordanaLLM` organization appears as `cordanallm`. No workflow runs this push yet: `publish-release.yml` publishes GitHub Release assets only (section 5.3).
```bash
# Packaging UKI as an OCI artifact using oras:
oras push ghcr.io/cordanallm/nucleus/kernels/mainstream-x86_64:7.2.4-lusoris1 \
  --artifact-type application/vnd.efi.uki \
  BOOTX64.EFI:application/octet-stream \
  SHA256SUMS:text/plain
```
Downstream bare-metal provisioning systems (`cordanaLLM/imago` iPXE streaming server or `systemd-sysupdate`) pull the OCI artifact and deploy it directly into the EFI System Partition (`/efi/EFI/Linux/`).

### 5.3 GitHub Release Assets & Downstream Artifact Manifest
`publish-release.yml` publishes one GitHub Release per kernel release tag (section 5.4) with `*.deb`, `linux-<stream>-<version>-uki.efi`, `kernel-<stream>.config` (the merged kconfig written by `scripts/merge-config.sh`), `kernel-<stream>.cdx.json`, `kernel-<stream>.spdx.json`, `SHA256SUMS`, its keyless cosign bundle `SHA256SUMS.bundle`, and `kernel-<stream>.manifest.json`.

The manifest follows `imago.nucleus.kernel-artifact.v1`, a contract owned by the consumer `cordanaLLM/imago` (`pkg/kernel`). It is generated after `SHA256SUMS` is signed and is deliberately not listed in it:

```json
{
  "schema": "imago.nucleus.kernel-artifact.v1",
  "provider": "cordanaLLM/nucleus",
  "stream": "mainstream",
  "version": "7.2.4-lusoris1",
  "kernel": {"release": "7.2.4-lusoris1", "config_digest": "sha256:<digest of kernel-mainstream.config>"},
  "artifacts": [{"name": "linux-image-7.2.4-lusoris1_x86_64.deb", "sha256": "<64 hex>", "size": 123456}],
  "checksums": {"file": "SHA256SUMS", "sha256": "<64 hex>"},
  "provenance": {
    "repository": "cordanaLLM/nucleus",
    "tag": "v7.2.4-mainstream-lusoris1",
    "revision": "<40 hex commit>",
    "bundle": "SHA256SUMS.bundle",
    "signer_identity": "https://github.com/cordanaLLM/nucleus/.github/workflows/publish-release.yml@refs/tags/v7.2.4-mainstream-lusoris1"
  }
}
```

The downstream `repository_dispatch` payload (`kernel_release_published`) carries `stream`, `version` (`<version>-lusoris<N>`, the same string as the manifest's `version`), and `tag`; it is sent with the `KERNEL_FORGE_TOKEN` secret (section 5.5). Imago downloads the release named by `tag`, verifies the cosign bundle over `SHA256SUMS`, recomputes the `SHA256SUMS` digest and every artifact digest and size against the manifest, and only then pins `kernel.streams.<stream>` (version, `artifact_digest`, provenance) in its `versions.json`.

### 5.4 Release Tags

Two tag namespaces share this repository, and only one of them releases a kernel:

| Tag | Example | Created by | Starts `publish-release.yml` |
| :--- | :--- | :--- | :--- |
| `v<version>-<stream>-lusoris<N>` | `v7.2.4-mainstream-lusoris1` | a maintainer releasing a kernel | yes |
| `nucleus-v<X.Y.Z>` | `nucleus-v0.2.0` | release-please, when its release pull request merges | no |

To release a kernel, push a tag in the first form. `scripts/resolve_release_tag.py` checks it against `versions.json` before anything is built:

- `<stream>` is a key of `streams`. It is spelled out because two streams may carry the same upstream version.
- `<version>` equals that stream's `version` exactly. `v7.2.40-mainstream-lusoris1` does not match a stream at `7.2.4`.
- `<N>` is the forge revision, an integer of at least 1 without leading zeros. Raise it to release the same upstream version again.

Any other tag is refused with an error naming the mismatch, and nothing is built, signed, published or dispatched; there is no default stream. The resolver prints what a tag resolves to, so a tag can be checked before it is pushed:

```bash
python3 scripts/resolve_release_tag.py --tag v7.2.4-mainstream-lusoris1
```

It writes `stream`, `version`, `rev`, `release_tag` and `release_version` (`<version>-lusoris<N>`, the `version` the manifest and the downstream payload carry). `tests/test_resolve_release_tag.py` derives its cases from `versions.json`.

The repository's own releases never start a kernel release. `release-please-config.json` sets `include-component-in-tag: true`, so release-please tags them `<component>-v<X.Y.Z>` with the component taken from `package-name` (`nucleus`); that tag does not match the `v*` trigger, and the resolver would refuse it as well. release-please also writes the released version into `VERSION` through `version-file`. Sources: the release-please manifest documentation ([Subsequent Versions](https://github.com/googleapis/release-please/blob/main/docs/manifest-releaser.md#subsequent-versions)), the `--component` option in its [CLI reference](https://github.com/googleapis/release-please/blob/main/docs/cli.md), and `version-file` in its [configuration schema](https://github.com/googleapis/release-please/blob/main/schemas/config.json) ("Used by `ruby` and `simple` strategies").

### 5.5 Downstream Dispatch Credential (`KERNEL_FORGE_TOKEN`)

`publish-release.yml` sends `kernel_release_published` to `cordanaLLM/imago` with the repository secret `KERNEL_FORGE_TOKEN`. It must be a token that may create repository dispatch events in `cordanaLLM/imago`: a fine-grained personal access token with **Contents: write** on that repository, or a classic token with the `repo` scope ([permissions for fine-grained tokens](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)).

There is no fallback to `GITHUB_TOKEN`, which is scoped to this repository and cannot dispatch to another one. When the secret is empty, the step `Require KERNEL_FORGE_TOKEN for the Downstream Dispatch` fails with an error that names the secret. That step runs after the release has been published, so configure the secret before pushing the first kernel release tag. ADR-0005 names this credential `DISPATCH_ACCESS_TOKEN`; the workflows use `KERNEL_FORGE_TOKEN`.

Every third-party action in `.github/workflows/` is pinned to a commit SHA with its release tag as a comment. `make lint-pins` (`scripts/check-action-pins.sh`, also run by `ci.yml`) verifies that each tag points to the pinned commit, since a SHA that no upstream commit carries fails only when a job using it starts.
