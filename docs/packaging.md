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
   - Single, self-contained, unsigned UEFI PE binary combining the systemd-stub, the Linux kernel (`.linux`), the kernel command line (`.cmdline`), and OS release, kernel release and SBAT metadata (`.osrel`, `.uname`, `.sbat`). An initramfs (`.initrd`) is embedded only when one is passed; no CPU microcode is embedded (section 3.1).
   - Designed for UEFI boot and direct network streaming (iPXE / systemd-boot). Secure Boot signing and TPM 2.0 PCR 11 pre-calculation are planned and not wired yet (sections 3.2 and 3.4).

```mermaid
flowchart TD
    SRC["Upstream Kernel Source + Curated Patches"] --> KCONF["Merged Hardened KConfig (.config)"]
    KCONF --> BUILD["Hermetic LLVM/Clang Builder"]

    BUILD -->|"make bindeb-pkg"| DEB_STAGE["Debian Packaging Pipeline"]
    DEB_STAGE --> DEB_IMG["linux-image-*.deb"]
    DEB_STAGE --> DEB_HDR["linux-headers-*.deb"]
    DEB_STAGE --> DEB_DEV["linux-libc-dev-*.deb"]

    BUILD -->|"vmlinux / bzImage + modules"| UKI_STAGE["systemd-ukify Pipeline"]
    INITRD["Optional Initramfs (--initrd)"] -.-> UKI_STAGE
    CMDLINE["Immutable Kernel Cmdline (console=ttyS0 quiet)"] --> UKI_STAGE

    UKI_STAGE --> UKI_BIN["BOOTX64.EFI (unsigned UKI)"]

    DEB_IMG --> APT_REPO["APT Repository (apt.example.com)"]
    DEB_HDR --> APT_REPO
    DEB_DEV --> APT_REPO

    UKI_BIN -.->|"planned"| OCI_REG["OCI Registry (ghcr.io/cordanallm/nucleus/kernels)"]
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

`ukify` builds the image on the systemd-stub and adds one PE section per input. Section names
follow the [UAPI Group UKI specification](https://uapi-group.org/specifications/specs/unified_kernel_image/).
This is what `scripts/package-uki.sh` produces, and what `scripts/check_uki.py` requires before
the image may be checksummed:

| PE Section Name | Contents | In the image | Checked |
| :--- | :--- | :--- | :--- |
| `.linux` | The `--vmlinuz` kernel image, unchanged | Always | Required, not empty |
| `.initrd` | The `--initrd` archive | Only when `--initrd` is given | Required if given, refused if not |
| `.cmdline` | The kernel command line text (`--cmdline`, or the script's default) | Always | Required, not empty |
| `.osrel` | os-release written from `versions.json` (`ID=lusoris`, stream version) | Always | Required, not empty |
| `.uname` | Kernel release, detected by `ukify` from the kernel image | Always | Required, not empty |
| `.sbat` | The stub's SBAT entries plus the `lusoris` entry | Always | Required, not empty |
| `.sdmagic` | The systemd-stub version marker | Always, from the stub | Required, must name systemd-stub |
| `.ucode`, `.splash`, `.dtb`, `.dtbauto`, `.efifw`, `.hwids`, `.pcrsig`, `.pcrpkey`, `.profile` | CPU microcode, boot splash, device trees, firmware, HWIDs, TPM 2.0 PCR 11 signature and key, extra profiles | Never: `package-uki.sh` passes none of them | Refused if present |

No CPU microcode is embedded, and the image is not Secure Boot signed. The refusal in the last row
is what enforces this: a section joins the image only when `package-uki.sh` is changed to wire it,
so the image does not depend on what the build host has installed or configured.

### 3.2 Synthesis with `ukify`

`build_uki_binary` in `scripts/package-uki.sh` writes the os-release, SBAT and command line inputs
to a private staging directory and calls `ukify build` with `--config=/dev/null`, `--efi-arch`,
`--linux`, `--cmdline=@<file>`, `--os-release=@<file>`, `--sbat=@<file>` and
`--output=output/<stream>-<arch>/BOOTX64.EFI` (`BOOTAA64.EFI`, `BOOTRISCV64.EFI`), plus
`--initrd` only when one was given.

- `ukify` reads a `--cmdline` value as literal text unless it starts with `@`. Without the `@`,
  the image would boot with the staging file's path as its kernel command line.
- `ukify` fails on an empty `--initrd=`, so the flag is omitted rather than passed empty.
- `ukify` reads the first `ukify.conf` it finds in `/etc/systemd`, `/run/systemd`,
  `/usr/local/lib/systemd` or `/usr/lib/systemd`, which can add microcode, device tree, splash
  or PCR signature sections or sign the image. `--config=/dev/null` stops that.
- `ukify` picks the systemd-stub by EFI architecture (`linux<efi-arch>.efi.stub`) and, without
  `--efi-arch`, uses the build host's. The script passes it (`x86_64` to `x64`, `arm64` to `aa64`,
  `riscv64` to `riscv64`), so an arm64 or riscv64 build on a host without that stub fails with an
  error that names the missing stub instead of wrapping the kernel in an x64 stub. Building
  for those architectures needs the matching stub and kernel (issue #18).
- The stub comes from `systemd-boot-efi`, which Ubuntu installs only as a recommendation of
  `systemd-ukify`. Install both on the runner.
- Secure Boot signing (`--secureboot-private-key`, `--secureboot-certificate`) and PCR 11
  pre-calculation (`--measure`, `--pcr-private-key`) are not wired yet.

### 3.3 Refusing an Image That Is Not a UKI

`scripts/package-uki.sh` runs `scripts/check_uki.py` on whatever `ukify` wrote, before
`sha256sum`. If the check fails, the script deletes the image and exits non-zero, so a
refused image never gets a checksum. The checker itself only reads the file. A production run
also removes the image, its `.sha256` and `pcr11-measurements.json` from an earlier run before
it checks any input, so a refused run leaves nothing at that path. The checker uses only the
Python standard library. It refuses a file that:

- is empty, or does not start with an `MZ` DOS header;
- has an `e_lfanew` outside the file, or no `PE\0\0` signature at it;
- has a COFF machine type other than `--arch` (`0x8664` x86_64, `0xaa64` arm64, `0x5064` riscv64);
- is not PE32 or PE32+ with subsystem EFI application (10);
- has a section table or section data that runs past the end of the file;
- lacks a section that 3.1 marks required, carries one of them twice, or carries `.initrd`
  without `--expect-initrd`;
- carries a section that `package-uki.sh` does not wire (last row of 3.1);
- holds a `.linux` kernel that starts with `MZ` but has another machine type than `--arch`, so an
  x64 stub around a kernel for another architecture is refused;
- with `--expect-cmdline`, holds a `.cmdline` other than the requested text (`package-uki.sh`
  passes the command line it wrote), so a command line that is a file path is refused.

These checks prove shape, not bootability or provenance: a deliberately crafted file with these
sections passes. They stop a failed or missing tool, or a placeholder, from being published
under a UKI name (issue #21). To check an image by hand:

```bash
python3 scripts/check_uki.py --arch=x86_64 [--expect-initrd] [--expect-cmdline=TEXT|@FILE] \
  output/mainstream-x86_64/BOOTX64.EFI
```

How this is tested:

- `tests/test_package_uki.py` runs `package-uki.sh` with a stand-in `ukify` on a `PATH` that
  holds nothing else, so the result does not depend on whether the host has `ukify`. The
  stand-in writes PE images the test builds with `struct`: valid, empty, non-PE, PE images
  missing a section, and images with an unwired section, a kernel for another machine or a
  wrong command line.
- The `UKI Real ukify Build` job (`uki-real-ukify` in `.github/workflows/ci.yml`) builds a UKI in
  `ubuntu:26.04` with the real `ukify` from Ubuntu's own kernel image, since no nucleus kernel
  exists until issue #18. It first installs a `ukify.conf` that asks for a microcode section and
  shows that `ukify` honours it without `--config=/dev/null`. It then checks that the image has
  no such section, prints `ukify inspect`, verifies the checksum, and runs the tests with a real
  kernel, failing if any test skips. Its name does not carry the container tag, so an Ubuntu
  bump does not rename the check.
- To run the real-`ukify` test locally, install `ukify` and the stub, then run
  `NUCLEUS_UKI_TEST_VMLINUZ=<kernel image> pytest tests/test_package_uki.py`.

### 3.4 TPM 2.0 PCR 11 Measurement & Sealing

When booting via `systemd-boot`, the UEFI boot loader measures the entire UKI payload directly into **TPM 2.0 PCR 11**:

- PCR 11 matches the cryptographic digest calculated during `ukify --measure`.
- Disk encryption keys (LUKS2 with `systemd-cryptenroll`) can be sealed to PCR 11: if the kernel, initramfs, or cmdline is altered by even a single bit, the TPM refuses to release the encryption key.

`scripts/package-uki.sh` does not pass `--measure` yet. The `pcr11-measurements.json` that
`--dry-run` writes hashes the simulated file and is marked `"simulated": true`.

### 3.5 Automated Packaging CLI Drivers

Developers and automation pipelines utilize dedicated shell drivers complying with NASA/JPL Power of 10:

```bash
# Generate native Debian packages (.deb) with headers and libc-dev
./scripts/package-deb.sh --stream=mainstream --arch=x86_64 --dry-run
make package-deb STREAM=mainstream ARCH=x86_64

# Simulate UKI synthesis: writes output/mainstream-x86_64-dry-run/BOOTX64.EFI.simulated.txt and a
# "simulated": true PCR 11 digest. Never a .efi and never a checksum
./scripts/package-uki.sh --stream=mainstream --arch=x86_64 --dry-run
make package-uki STREAM=mainstream ARCH=x86_64 DRY_RUN=true
# Production: needs a built kernel and ukify, refuses without either, and deletes any
# output that scripts/check_uki.py refuses before it is checksummed
./scripts/package-uki.sh --stream=mainstream --arch=x86_64 --vmlinuz=<path> [--initrd=<path>]
make package-uki STREAM=mainstream ARCH=x86_64 DRY_RUN=false VMLINUZ=<path> [INITRD=<path>]

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
Planned: UKI binaries (`.efi`) are pushed as OCI artifacts conforming to the OCI Artifact Specification, one OCI repository per stream and architecture under `ghcr.io/cordanallm/nucleus/kernels` ([ADR-0006](adr/0006-oci-registry-namespace.md)). OCI repository names are lowercase, so the `cordanaLLM` organization appears as `cordanallm`. The image is unsigned today (section 3.1), and no workflow publishes or pushes one: CI builds one only to test it, and `publish-release.yml` publishes GitHub Release assets, none of them a UKI (section 5.3).
```bash
# Packaging UKI as an OCI artifact using oras:
oras push ghcr.io/cordanallm/nucleus/kernels/mainstream-x86_64:7.2.4-lusoris1 \
  --artifact-type application/vnd.efi.uki \
  BOOTX64.EFI:application/octet-stream \
  SHA256SUMS:text/plain
```
Downstream bare-metal provisioning systems (`cordanaLLM/imago` iPXE streaming server or `systemd-sysupdate`) are meant to pull the OCI artifact and deploy it directly into the EFI System Partition (`/efi/EFI/Linux/`).

### 5.3 GitHub Release Assets & Downstream Artifact Manifest
`publish-release.yml` publishes one GitHub Release per kernel release tag (section 5.4) with `*.deb`, `kernel-<stream>.config` (the stream-layered merge `scripts/merge-config.sh --stream=<stream>` writes: the security baseline, the architecture fragment, then `kconfig/streams/<stream>.config`, sorted by symbol and without a timestamp, so its digest is reproducible; until issue #18 builds kernels it is the declared merge, not the `olddefconfig`-resolved `.config`), `kernel-<stream>.cdx.json`, `kernel-<stream>.spdx.json`, `SHA256SUMS`, its keyless cosign bundle `SHA256SUMS.bundle`, and `kernel-<stream>.manifest.json`. No UKI (`.efi`) is uploaded yet: the workflow uploads no `.efi`, and `SHA256SUMS` covers `*.deb`, `*.json` and `*.config` only because `publish-release.yml` does not call `scripts/package-uki.sh`.

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
- `<N>` is the forge revision. Only `1` is accepted for now, so `v7.2.4-mainstream-lusoris2` is refused. Nothing consumes the revision yet: `scripts/package-deb.sh` writes `-lusoris1` into the kernel release and the package version, and a higher `<N>` would put a version in the manifest and the downstream payload that the forge did not build. Issue #18, which implements the kernel build, threads the revision into `LOCALVERSION` and `KDEB_PKGVERSION`; the resolver then accepts integers of at least 1 without leading zeros, and a revision above 1 releases the same upstream version again.

Any other tag is refused with an error naming the mismatch, and nothing is built, signed, published or dispatched; there is no default stream. The run must also have started from that tag: the workflow passes `GITHUB_REF` to the resolver, which refuses unless it is `refs/tags/<tag>`. Checkout, the signer identity in the manifest and the GitHub Release all follow the ref, so re-running a release by hand means `gh workflow run publish-release.yml --ref <tag> -f tag=<tag>`; starting it from a branch, or from another tag, is refused before anything is built. The resolver prints what a tag resolves to, so a tag can be checked before it is pushed:

```bash
python3 scripts/resolve_release_tag.py --tag v7.2.4-mainstream-lusoris1
```

It writes `stream`, `version`, `rev`, `release_tag` and `release_version` (`<version>-lusoris<N>`, the `version` the manifest and the downstream payload carry). `tests/test_resolve_release_tag.py` derives its cases from `versions.json`.

The repository's own releases never start a kernel release. `release-please-config.json` sets `include-component-in-tag: true`, so release-please tags them `<component>-v<X.Y.Z>` with the component taken from `package-name` (`nucleus`); that tag does not match the `v*` trigger, and the resolver would refuse it as well. release-please also writes the released version into `VERSION` through `version-file`. Sources: the release-please manifest documentation ([Subsequent Versions](https://github.com/googleapis/release-please/blob/main/docs/manifest-releaser.md#subsequent-versions)), the `--component` option in its [CLI reference](https://github.com/googleapis/release-please/blob/main/docs/cli.md), and `version-file` in its [configuration schema](https://github.com/googleapis/release-please/blob/main/schemas/config.json) ("Used by `ruby` and `simple` strategies").

### 5.5 Downstream Dispatch Credential (`KERNEL_FORGE_TOKEN`)

`publish-release.yml` sends `kernel_release_published` to `cordanaLLM/imago` with the repository secret `KERNEL_FORGE_TOKEN`. It must be a token that may create repository dispatch events in `cordanaLLM/imago`: a fine-grained personal access token with **Contents: write** on that repository, or a classic token with the `repo` scope ([permissions for fine-grained tokens](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)).

There is no fallback to `GITHUB_TOKEN`, which is scoped to this repository and cannot dispatch to another one. When the secret is empty, the step `Require KERNEL_FORGE_TOKEN for the Downstream Dispatch` fails with an error that names the secret. That step is the first step of the job, so a missing secret stops the run before anything is built, signed or published, and no release exists that imago was never told about. The step receives only whether the secret is set (`secrets.KERNEL_FORGE_TOKEN != ''`), never its value; the dispatch step is the only one that reads the token, as ADR-0005 requires.

The manual counterpart is `scripts/notify_downstream.sh`. It requires `RELEASE_TAG` (checked by `scripts/resolve_release_tag.py`, with the stream and version arguments checked against it) and, unless it is a dry run, a `GITHUB_TOKEN` that may dispatch to `cordanaLLM/imago`; it exits 1 when either is missing instead of skipping the dispatch:

```bash
RELEASE_TAG=v7.2.4-mainstream-lusoris1 ./scripts/notify_downstream.sh mainstream 7.2.4-lusoris1 true
``` ADR-0005 names this credential `DISPATCH_ACCESS_TOKEN`; the workflows use `KERNEL_FORGE_TOKEN`.

Every third-party action in `.github/workflows/` is pinned to a commit SHA with its release tag as a comment. `make lint-pins` (`scripts/check-action-pins.sh`, also run by `ci.yml`) verifies that each tag points to the pinned commit, since a SHA that no upstream commit carries fails only when a job using it starts. When the API refuses a query with HTTP 403, as an organization IP allow list does for the Actions token even on public repositories (`aquasecurity` is one), the script resolves that tag over anonymous `git ls-remote` instead and says so on the pin's line.
