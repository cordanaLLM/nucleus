# Verify and Install a Release

> How to download, verify and install a kernel release published by `cordanaLLM/nucleus`.

---

## 1. What a Release Contains

A kernel release is tagged `v<version>-<stream>-lusoris<N>` ([Packaging](packaging.md), section 5.4). Pick the tag from the [releases list](https://github.com/cordanaLLM/nucleus/releases), not from the forge's "Latest release" link: tags of the form `nucleus-v<X.Y.Z>` are releases of the repository tooling, carry no kernel, and can be the latest one.

Each kernel release carries ten assets, the files the `Publish to GitHub Release` step of `.github/workflows/publish-release.yml` uploads:

| Asset | Content |
| :--- | :--- |
| `linux-image-<kernelrelease>_<pkgversion>_amd64.deb` | The kernel image and its modules |
| `linux-headers-<kernelrelease>_<pkgversion>_amd64.deb` | Headers for building out-of-tree modules |
| `linux-libc-dev_<pkgversion>_amd64.deb` | Kernel headers for userspace |
| `vmlinuz-<kernelrelease>` | The kernel image of the image package, as a single file |
| `kernel-<stream>-x86_64.config` | The resolved configuration, the same file as `/boot/config-<kernelrelease>` |
| `kernel-<stream>.cdx.json` | CycloneDX inventory of the release directory (`syft`) |
| `kernel-<stream>.spdx.json` | SPDX inventory of the release directory (`syft`) |
| `kernel-<stream>.manifest.json` | Digests and provenance for `cordanaLLM/imago` (`imago.nucleus.kernel-artifact.v1`) |
| `SHA256SUMS` | Checksums of the seven files above the manifest |
| `SHA256SUMS.bundle` | Keyless cosign signature and certificate over `SHA256SUMS` |

For a stable version, `<kernelrelease>` is `<version>-lusoris<N>-<stream>` and `<pkgversion>` is `<version>-lusoris<N>`. A release candidate is spelled differently in both; [Packaging](packaging.md), section 2.2, gives the rule.

## 2. Download

```bash
gh release download <release-tag> -R cordanaLLM/nucleus
```

The same files are on the release's page in the releases list.

## 3. Verify

Check the signature over the checksum file first, then the files against it. The certificate names the workflow and the tag that built the release:

```bash
cosign verify-blob --bundle SHA256SUMS.bundle --certificate-identity "https://github.com/cordanaLLM/nucleus/.github/workflows/publish-release.yml@refs/tags/<release-tag>" --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS
```

`cosign` prints `Verified OK`. It refuses a `SHA256SUMS` with a changed byte, and a certificate that another workflow obtained.

```bash
sha256sum -c SHA256SUMS
```

Seven lines end in `OK`. The manifest and the bundle are not listed, because the workflow writes both after the checksum file.

Before the workflow signs, `scripts/check_kernel_artifacts.py` has opened every package and `scripts/boot_smoke.py` has booted the kernel under QEMU ([Packaging](packaging.md), sections 2.4 and 2.5).

## 4. Install

On a Debian or Ubuntu host of the `amd64` architecture:

```bash
sudo dpkg -i linux-image-*.deb linux-headers-*.deb
```

The image package places `/boot/vmlinuz-<kernelrelease>`, `/boot/config-<kernelrelease>`, `/boot/System.map-<kernelrelease>` and `/lib/modules/<kernelrelease>`; `dpkg -c` lists the content of a package before it is installed.

Its maintainer script then runs the hooks the host keeps in `/etc/kernel/postinst.d` and `/usr/share/kernel/postinst.d`. The initramfs and the boot entry come from those hooks, which belong to the host's initramfs generator and boot loader packages; nucleus ships none. `dpkg --ctrl-tarfile linux-image-*.deb | tar -xO ./postinst` prints the script.

`.github/workflows/package-lifecycle.yml` performs sections 2–4 weekly on the two newest revisions of every stream, in a container with `initramfs-tools` and `kmod`. It verifies clean package installation, side-by-side coexistence of successive kernel releases, byte-identity of `/boot/vmlinuz-<kernelrelease>` and `/boot/config-<kernelrelease>` against the release assets, generation of `/boot/initrd.img-<kernelrelease>` by the host's postinst hook, and clean uninstallation via `dpkg --purge` without leftover files in `/boot`, `/lib/modules`, or `/usr/src`.

## 5. Not Provided Today

- **Other architectures**: a release is `x86_64` only. `build-matrix.yml` compiles and gates `arm64` and `riscv64` kernels, and no workflow publishes them.
- **An APT archive**: the packages are release assets, so `apt` does not deliver an update.
- **A Secure Boot signature**: no step signs the kernel image (`git grep -n -iE "sbsign|pesign" -- scripts .github/workflows` finds nothing).
- **A Unified Kernel Image**: no release workflow publishes one ([Packaging](packaging.md), section 3).
- **A tested boot from the installed package**: the workflow boots the kernel image directly; the host's hooks and boot loader are not part of any test here.
