# ADR-0009: Kernel Compilation, the Artifact Gate and the Boot Smoke Test

Date: 2026-09-29

## Status

Proposed

Once accepted, this ADR supersedes part of an accepted ADR:

- [ADR-0004](0004-native-debian-and-uki-dual-packaging.md), section 1, bullets 2 and 3: the
  localversion is `-lusoris<N>-<stream>` without an architecture suffix, since the package's
  `Architecture` field carries the architecture, and a cross-compiled build ships no
  `linux-headers` package (section 3). The rest of section 1, and section 2 (the UKI), stand; no
  UKI is built from these packages yet.

---

## Context

Issue #18 is the real build. [ADR-0008](0008-signed-kernel-sources-and-resolved-configuration.md)
delivered its first half: a verified source tree per stream and a configuration resolved against
it. The compile itself refused, as it has since #19, because it used to `touch` two empty `.deb`
files and exit 0. Issue #31 found two more scripts of the same class: `package-deb.sh` wrote text
files named `.deb` whenever `--source-tree` was omitted, with a package version taken from the
build date, and `publish_release.sh` ran `sha256sum -- *.deb > SHA256SUMS || true` in a
hardcoded `output/`, which left a zero-byte `SHA256SUMS` and exit status 0.

The consumers constrain what a build must deliver:

- `cordanaLLM/imago` verifies `imago.nucleus.kernel-artifact.v1`: `kernel.release` (the kernel's
  `uname -r`), `kernel.config_digest`, and a digest and size per artifact. Its contract has no
  architecture field, so a release is one architecture: x86_64. It accepts an artifact of size 0
  (`pkg/kernel/artifact.go`), so a forge that checksums an empty file gets it verified.
- Aegis-OS milestone M10 (Aegis-OS #150, #151) boots the published kernel under QEMU and needs a
  bootable x86_64 image and its release string, and a `uname -r` that names the build, so that the
  guest's kernel cannot be mistaken for the host's.

What the kernel's own packaging does was read in the 7.2.8 tree rather than assumed:

- `scripts/Makefile.package`: `bindeb-pkg` runs `dpkg-buildpackage --no-check-builddeps` in the
  object tree, so the packages land in its parent directory.
- `scripts/package/debian/rules`: the build re-runs `olddefconfig`, and passes
  `KBUILD_BUILD_VERSION` as the Debian revision of `KDEB_PKGVERSION` (`lusoris<N>`).
- `scripts/package/builddeb`: modules are installed with `INSTALL_MOD_STRIP=1`, and the object
  tree's `.config` is copied to `/boot/config-<kernelrelease>`.
- `scripts/package/install-extmod-build`: when `CC` differs from `HOSTCC`, the headers package
  rebuilds its host programs with `CC`, `sign-file` among them, which links the target's
  `libcrypto`. A cross-compiled headers package therefore needs `libssl-dev` of the target
  architecture; `scripts/package/mkdebian` expresses this as
  `libssl-dev <!pkg.linux-upstream.nokernelheaders>`.

Ubuntu 26.04's `/usr/bin/install` is uutils coreutils 0.8.0, and its `install -D` fails with
"cannot create directory" when parallel calls create the same parent directory. The kernel's
`scripts/Makefile.dtbinst` installs every device tree of an arm64 or riscv64 build that way, in
parallel: mainstream/riscv64 failed so in build-matrix run 36504114874, and 40 rounds of 24
parallel `install -D` calls reproduce it (GNU `install` 9.7: no failure). GNU coreutils cannot
simply replace uutils there: `build-essential` depends on `coreutils-from-uutils`, and
`bindeb-pkg` checks the build dependencies, `build-essential` among them.

GitHub's hosted runners for public repositories include `ubuntu-24.04-arm` (4 vCPU, 16 GB RAM,
14 GB SSD, the same as x86_64); there is no riscv64 runner
(<https://docs.github.com/en/actions/reference/runners/github-hosted-runners>, read 2026-09-29).

---

## Alternatives considered

1. **Cross-compile every architecture on x86_64, with multiarch `libssl-dev:<arch>` for the
   headers.** Rejected: Ubuntu serves arm64 and riscv64 from `ports.ubuntu.com`, which the
   container would need as a second apt source, and arm64 has a free native runner.
2. **Cross-compile every architecture, without headers packages.** Rejected for arm64, whose
   native runner builds the headers package at no cost; kept for riscv64.
3. **A localversion without the stream (`-lusoris1`).** Rejected: `mainstream` and `realtime` are
   both 7.2.8, so their packages would share a name and a `uname -r`.
4. **Build the release on the runner's own Ubuntu 24.04.** Rejected: the resolved configuration
   records the compiler (`CONFIG_CC_VERSION_TEXT`), so the release would carry a configuration
   no other workflow resolved.
5. **Rely on the consumer's verification.** Rejected: a signature over `SHA256SUMS` proves which
   bytes were published, not that they are a kernel, and imago accepts a zero-byte artifact.
6. **Publish the debug-symbol package.** Deferred: the `pkg.linux-upstream.nokerneldbg` profile
   leaves it out; imago bounds each artifact at 512 MiB.
7. **A busybox initramfs for the boot test.** Rejected: an extra package for a test that needs
   one line of output; a static C `init` of twenty lines proves userspace was reached.

---

## Decision

### 1. One script compiles one leg

`scripts/build_kernel.sh --stream=<s> --arch=<a>` runs, and stops with exit status 1 at the
first failure:

1. `scripts/fetch-kernel-source.sh`, unless `--source-tree` names a tree it verified;
2. `scripts/merge-config.sh --source-tree`, which writes `kernel-<s>-<a>.config` into the
   output directory and leaves the resolved `.config` in the object tree;
3. `make O=<work>/<s>-<a>/kbuild ARCH=<kernel_arch> CROSS_COMPILE=<cross_compile> bindeb-pkg`;
4. `make -s kernelrelease`, and the extraction of `./boot/vmlinuz-<kernelrelease>` from the image
   package to `vmlinuz-<kernelrelease>` beside it;
5. the artifact gate (section 4); then the build record `<work>/<s>-<a>/build.json`.

The output directory must be empty: a forge does not mix the artifacts of two builds. A request
that can be refused without writing (unknown stream, non-empty output directory, a source tree
that is not a kernel, a missing tool) is refused before anything is written.

The build carries no clock and no builder identity. `SOURCE_DATE_EPOCH` is the modification time
of the tree's `Makefile`, which `git archive` sets to the release commit's time, in kernel.org's
tarballs and in the git-tag export alike; `KBUILD_BUILD_TIMESTAMP` is that time as a `date`
string; `KBUILD_BUILD_USER=nucleus`, `KBUILD_BUILD_HOST=forge`, `LC_ALL=C`, `TZ=UTC`.

### 2. Release and package names

| | Form | `mainstream` example |
| :--- | :--- | :--- |
| `LOCALVERSION` | `-lusoris<N>-<stream>` | `-lusoris1-mainstream` |
| kernelrelease (`uname -r`) | `<kernelversion>-lusoris<N>-<stream>` | `7.2.8-lusoris1-mainstream` |
| `KDEB_PKGVERSION` | `<version>-lusoris<N>` | `7.2.8-lusoris1` |
| image package | `linux-image-<kernelrelease>_<pkgversion>_<debian_arch>.deb` | `linux-image-7.2.8-lusoris1-mainstream_7.2.8-lusoris1_amd64.deb` |

The other streams are `7.3.0-rc5-lusoris1-bleeding`, `6.18.54-lusoris1-lts` and
`7.2.8-lusoris1-realtime`. `<N>` is `--revision` (default 1); the release workflow passes the
revision its tag names, and `scripts/resolve_release_tag.py` accepts only 1 for now.

### 3. Toolchain, architecture and profiles

`CROSS_COMPILE` is the architecture's `cross_compile` from `versions.json` on every host, the
native one included, as ADR-0008 decided for the resolution; the native and cross compilers of
Ubuntu 26.04 report the same version string (`(Ubuntu 15.2.0-16ubuntu1) 15.2.0`). The packages'
Debian architecture is the new `architectures.<arch>.debian_arch` field.

Every build uses the `pkg.linux-upstream.nokerneldbg` profile. A build whose host architecture
differs from its target adds `pkg.linux-upstream.nokernelheaders` (section Context). In the
matrix, x86_64 and arm64 build natively and ship a headers package; riscv64 cross-compiles and
does not.

### 4. Nothing is checksummed before the artifact gate has opened it

`scripts/check_kernel_artifacts.py` refuses a build directory unless:

- it holds only non-empty regular files: packages, `vmlinuz-<kernelrelease>` and
  `kernel-<stream>-<arch>.config`;
- every package reads, with `dpkg-deb -f`, as `linux-image-<kernelrelease>`,
  `linux-headers-<kernelrelease>` or `linux-libc-dev`, at `<version>-lusoris<N>` and the
  architecture's `debian_arch`, under the file name `<Package>_<Version>_<Architecture>.deb`,
  with the image package present and no package twice;
- the kernelrelease starts with the stream's kernel version and ends with `-lusoris<N>-<stream>`;
- the image package carries `./boot/vmlinuz-<kernelrelease>`, byte-identical to the extracted
  `vmlinuz-<kernelrelease>`, and `./boot/config-<kernelrelease>`, byte-identical to the resolved
  configuration.

It runs at the end of every build and again in `publish-release.yml`, on the release host, before
the SBOMs, `SHA256SUMS` and the signature.

### 5. Checksums cover exactly what is published

`scripts/publish_release.sh` reads `OUTPUT_DIR` (default `output`), and writes `SHA256SUMS` over
every file in it, when every file is a publishable kind (`*.deb`, `vmlinuz-*`, `*.config`,
`*.cdx.json`, `*.spdx.json`), none is empty, at least one is a package and no `SHA256SUMS`
exists. Otherwise it refuses and writes nothing; a `sha256sum` failure is never swallowed.
`scripts/package-deb.sh` becomes a wrapper over `build_kernel.sh` that refuses a production run
without `--source-tree`.

### 6. The release is the tagged stream, built in the matrix's userland

`publish-release.yml` builds the tagged stream for x86_64 with `build_kernel.sh` inside an
`ubuntu:26.04` container that is given neither the job's token nor its OIDC credentials (the
checkout does not persist credentials), then gates, generates the SBOMs, checksums and signs. It
publishes the packages, `vmlinuz-<kernelrelease>` (the image Aegis-OS M10 boots),
`kernel-<stream>-x86_64.config`, the SBOMs, `SHA256SUMS`, its bundle and the manifest. The
manifest's `kernel.release` is the recorded kernelrelease, `kernel.config_digest` the digest of
the resolved configuration (the one the gate compared with `/boot/config-<kernelrelease>`), and
`provenance.revision` the commit the tag points at, checked against the run's ref.

### 7. The matrix compiles all twelve legs

`build-matrix.yml` runs four streams by three architectures in `ubuntu:26.04`: x86_64 and
riscv64 on `ubuntu-24.04`, arm64 on `ubuntu-24.04-arm`. The job may read the repository and
nothing else. It installs the toolchain with `scripts/install-build-toolchain.sh` (the list the
release installs too; `build_kernel.sh` puts GNU `install`, which Ubuntu keeps as `gnuinstall`,
first on the build's `PATH` when `install` is not GNU, and refuses when neither is there), refuses a runner without room for the leg (10 GiB for x86_64, 16 GiB
for arm64, 8 GiB for riscv64, from the measured object trees) in its first minute
(`scripts/probe-build-space.sh`, which also uses the runner's `/mnt` disk when that has more
room), restores a compiler cache keyed by stream, architecture and the inputs' digest and saves
it under the resolved configuration's digest, boots every x86_64 kernel (section 8), and
checksums and uploads each leg's directory with its build record.

### 8. Every x86_64 kernel boots to userspace before it is kept

`scripts/boot_smoke.py` compiles a static `init` that prints the running release and powers the
machine off, packs it with a `/dev/console` node into a newc initramfs, and boots the kernel with
`qemu-system-x86_64 -nographic -no-reboot -append 'console=ttyS0 panic=-1 rdinit=/init'` under
TCG. It passes only when the console shows both `Linux version <kernelrelease>` (followed by a space) and the init's
line within the timeout; a panic ends QEMU at once, a hang is killed at the timeout.

---

## Consequences

### Positive

- A green leg means a kernel was compiled, packaged, opened, and, on x86_64, booted.
- imago's verifier passes on a release this forge assembles and refuses one flipped bit
  (evidence for issue #18).
- Each stream's `uname -r` names the stream and the forge revision.

### Negative

- `CONFIG_MODULE_SIG_ALL` signs the modules with a key generated for each build, which the build
  tree keeps and the workflow discards; two builds of one tag produce different module
  signatures, so the packages are not bit-for-bit reproducible.
- riscv64 packages have no headers package, so out-of-tree modules cannot be built against them.
- The compiler cache takes up to 700 MB per leg, about 8.4 GB of the repository's 10 GB cache
  allowance for twelve legs; older entries are evicted first.
- A release compiles the kernel again instead of reusing a matrix artifact, so it takes one leg's
  build time.

### Neutral

- The boot test proves userspace on x86_64 only; arm64 and riscv64 are compiled, gated and
  packaged but not booted.
- riscv64 kernels carry no debug information and so no BTF: `kconfig/riscv64.config` does not
  request `DEBUG_INFO_BTF`, unlike the x86_64 and arm64 fragments.
- `verify_kernel_requirement.py` still reports the module ABI as unverifiable: it reads
  configurations, not build records.

---

## Compliance

1. **`tests/test_build_kernel.py`**: the dry run of every stream and architecture states the
   make command, the localversion, the package version, the profiles and the expected
   kernelrelease and writes nothing; malformed requests, a non-empty output directory, a tree
   that is not a kernel, an existing unnamed tree and a missing toolchain are refused before
   anything is written; `package-deb.sh` refuses production without `--source-tree`.
2. **`tests/test_check_kernel_artifacts.py`**: packages built with `dpkg-deb`; a correct layout
   passes, and a zero-byte file, a text file named `.deb`, a wrong version, a wrong architecture,
   a debug package, a duplicate package, a changed configuration, a changed kernel image, a
   stranger, a missing kernel in the image package, a missing image package and a mismatching
   kernelrelease are refused.
3. **`tests/test_publish_release.py`**: `OUTPUT_DIR` is honoured, the checksums cover exactly
   the files, and an empty directory, a directory without a package, a zero-byte file, a
   stranger, an existing `SHA256SUMS` and a failing `sha256sum` are refused with no
   `SHA256SUMS` written.
4. **`tests/test_qemu_boot.py`**: the initramfs format, the verdict, and a stub QEMU that boots,
   panics or hangs.
5. **`tests/test_workflows.py`**: the matrix, its permissions, runners, gate, boot and cache; the
   release's build, gate and manifest fields.
6. **`build-matrix.yml`**: every leg compiles, gates and, on x86_64, boots; run 36507112520
   passed on all twelve legs.

---

## References

- nucleus issues #18 (the real build) and #31 (fabricated packages and checksums), #19 (the
  refusal), PR #36 (ADR-0008).
- Aegis-OS #150 (E10-4, Nucleus kernel result recorded), #151 (E10-5, kernel requirement
  satisfied by the built kernel).
- imago `pkg/kernel/artifact.go` and `pkg/kernel/verify.go` at 16f964b (the v1 contract).
- Linux 7.2.8: `scripts/Makefile.package`, `scripts/package/builddeb`,
  `scripts/package/mkdebian`, `scripts/package/install-extmod-build`,
  `scripts/package/debian/rules`.
- Decisions recorded for issue #18: arm64 builds natively and riscv64 cross-compiles without
  headers; the debug-symbol package is not published; the release is x86_64 until a manifest
  with an architecture field is agreed with imago.
