# ADR-0008: Signed Kernel Sources and a Resolved, Survival-Checked Kernel Configuration

Date: 2026-09-29

## Status

Proposed

Once accepted, this ADR supersedes two parts of accepted ADRs, and amends one proposed ADR:

- [ADR-0001](0001-declarative-kernel-manifest.md), decision item 1: a stream no longer declares
  `tarball_url`; it declares a `source` (section 1), and `architectures` is an object that
  carries each architecture's build data (section 5). Items 2 and 3 stand.
- [ADR-0002](0002-modular-kconfig-architecture.md), decision items 1 and 2: there is no
  `kconfig/base.config`, and the fragments are not composed on their own; they are applied on
  top of the architecture's defconfig and resolved (section 4). Item 3 stands.
- [ADR-0007](0007-document-driven-kernel-requirements.md), section 4: the `resolved` evidence
  level it announced comes from a resolved `.config`, not from a built kernel (section 6).

---

## Context

Issue #18 is the real build. Its first half is everything before the compiler runs: a kernel
source this forge can prove, and a configuration that holds what the fragments ask for. Neither
existed. `versions.json` carried one `tarball_url` per stream and nothing that proved what it
pointed at, and three of the four URLs were stale: kernel.org publishes 7.3-rc5, 7.2.8 and
6.18.54, while the manifest pinned 7.3-rc2, 7.2.4 and 6.18.50. The fourth,
`projects/rt/7.2/linux-7.2-rt.tar.xz`, returns HTTP 404; the rt directory holds
`patch-7.2-rt5` and its patch series, based on v7.2.

kernel.org signs its sources in two ways (<https://www.kernel.org/signature.html>):

- A release tarball has a detached `.tar.sign` over the **uncompressed** tar, checked with
  `xz -cd linux-X.tar.xz | gpg --verify linux-X.tar.sign -`. Stable and longterm tarballs are
  signed by Greg Kroah-Hartman (`647F28654894E3BD457199BE38DBBDC86092693E`); 7.2.8 and 6.18.54
  are.
- A release candidate has no signed tarball: `releases.json` lists `pgp: null` for mainline, and
  the `git.kernel.org` snapshot is generated on request. Its tag in `torvalds/linux.git` is
  signed by Linus Torvalds (`ABAF11C65A2970B130ABE3C479BE3E4300411886`).
- Each release directory also has `sha256sums.asc`, signed by the checksum autosigner
  (`B8868C80BA62A1FFFAF5FDA9632D3A06589DA6B1`). signature.html says it is a mirror check and
  does not replace a developer signature.

The fragments had never been resolved against a kernel tree. Merged on their own and passed to
`make olddefconfig`, they produce a kernel without `EXT4_FS`, `NET`, `MODULES` or `EFI_STUB`,
because a fragment names what differs from a base, not a whole configuration. Applied on top of
each architecture's defconfig and resolved against the pinned sources, they lost, silently:
`DEFAULT_MQ_DEADLINE` on every leg (no tree has that symbol); `VIRTIO_FS` on x86_64 (unset) and
arm64 (capped at `m`); `TCP_CONG_BBR` and `DEFAULT_BBR` on arm64 and riscv64; `KVM_GUEST` on arm64;
and `RANDOMIZE_BASE` on riscv64 from 7.2 on. The kernel's own `scripts/kconfig/merge_config.sh`
only prints a warning for a value that did not survive.

---

## Alternatives considered

- **Sources.** A `sha256` pin alone (trust on first use) proves the bytes did not change after
  the pin, not who published them; it is the only option for a release-candidate snapshot, which
  has no signature. `sha256sums.asc` is signed, but by an automated key that kernel.org itself
  ranks below the developer signatures. The rt patch on top of v7.2 was the third option for
  `realtime`: it carries fixes for arm (32-bit), powerpc, i915 and the 8250 serial driver, none
  for x86, arm64 or riscv, and it is based on v7.2, not on the 7.2.y stable releases.
- **Base configuration.** A distribution configuration (Ubuntu's) plus the fragments builds
  thousands of modules this forge does not need, and it is not published per release in a form
  the build can pin. The fragments alone resolve to a kernel that cannot boot a disk.
- **Survival.** `merge_config.sh` without `-m` runs `alldefconfig` and warns; a strict mode of it
  (`-s`) refuses a fragment that redefines a value, which is the layering this forge relies on
  (`realtime.config` after the architecture fragment), and still does not refuse a dropped
  value.

---

## Decision

### 1. Every stream names a signed source, and nothing is used before it is proven

`versions.json` `streams.<stream>.source` is one of two kinds (`versions.schema.json`):

- `tarball`: `url` of the `.tar.xz`, `signature_url` of its `.tar.sign`, the `sha256` of the
  `.tar.xz`, and `signers`.
- `git-tag`: the `repository`, the `tag`, the `commit` it points at, and `signers`.

`signers` lists the primary-key fingerprints whose signature is accepted for that source.
`scripts/fetch-kernel-source.sh --stream=<stream> --dest=<dir>` proves the source and then writes
the tree; any failure refuses with exit status 1 and writes nothing:

- `tarball`: the `.tar.xz` matches the pinned `sha256`, then
  `xz -cd <tar.xz> | gpgv --keyring <keyring> <tar.sign> -` succeeds. Both are required: the
  signature proves who published the release, the pin proves it is the one reviewed here.
- `git-tag`: `git fetch --depth=1` of the one tag, `git verify-tag` in a throwaway GnuPG home, the
  tag object names the tag, and the tag points at the pinned commit. The tree is exported with
  `git archive`.
- In both cases the GnuPG status must carry a good signature from an unexpired, unrevoked key, and
  every `VALIDSIG` must name, as its primary-key fingerprint, a listed signer. The keyring or
  GnuPG home holds the listed signers' keys and no others.
- The tree's top-level `Makefile` must report the release `versions.json` names for the stream.

`bleeding` is a `git-tag` source; `mainstream`, `lts` and `realtime` are `tarball` sources signed
by Greg Kroah-Hartman.

### 2. The keys are in the repository

`keys/<FINGERPRINT>.asc` holds the armored, minimal export of each key, fetched through the
kernel.org Web Key Directory as signature.html instructs. The autosigner key is kept to check
`sha256sums.asc` when a tarball pin is bumped and is no stream's signer. `gpgv` does not read an
armored keyring, so the fetch script dearmors the listed keys into a binary one. `keys/README.md`
records the provenance and how to refresh a key.

### 3. `realtime` is the mainline stable release with the in-tree `PREEMPT_RT`

`realtime` pins the same 7.2.8 tarball as `mainstream`; `kconfig/streams/realtime.config` sets
`PREEMPT_RT`, `HZ_1000` and `EXPERT`. `PREEMPT_RT` depends on `EXPERT && ARCH_SUPPORTS_RT &&
!COMPILE_TEST`, and x86, arm64 and riscv select `ARCH_SUPPORTS_RT` in 6.18.54, 7.2.8 and
7.3-rc5 alike, so all three `realtime` legs resolve with it. No rt patch is applied.

### 4. The configuration is resolved against the source

`scripts/merge-config.sh --stream=<stream> --arch=<arch> --source-tree=<verified tree>`:

1. `make ARCH=<kernel_arch> CROSS_COMPILE=<cross_compile> <base_config>`: the architecture's
   in-tree defconfig (`x86_64_defconfig`, or `defconfig` for arm64 and riscv64);
2. `scripts/kconfig/merge_config.sh -m` of the tree, with `security-hardened.config`, the
   architecture fragment and the stream fragment, in the order ADR-0007 and the declared merge use;
3. `make olddefconfig`;
4. the survival check (section 5);
5. `output/kernel-<stream>-<arch>.config`, which carries no timestamp. The build directory
   (`--build-dir`, the `O=` directory) is where the compile will run.

### 5. Every requested value must survive

`scripts/kconfig_survival.py` reads the fragments in merge order with the grammar the declared
merge uses, the last line for a symbol winning, and compares every requested `CONFIG_X=value` and
every `# CONFIG_X is not set` with the resolved `.config`. A value that differs, or a symbol the
resolved `.config` does not record, fails the resolution, and the report names the symbol, the
requested value, the resolved value and the fragment. An unset request is met only by the
resolved `# CONFIG_X is not set` line. The check is never relaxed to make a leg pass: a symbol
an architecture cannot have moves to that architecture's fragment, and a dependency the base
does not carry is declared next to what needs it.

Resolving all twelve legs against the pinned sources required these fragment changes:
`DEFAULT_MQ_DEADLINE` removed (no such symbol; `block/elevator.c` makes `mq-deadline` the default
for single-queue devices); `FUSE_FS=y` for `VIRTIO_FS=y` on x86_64 and arm64;
`TCP_CONG_ADVANCED=y` for BBR, which only the x86_64 defconfig sets; `KVM_GUEST` removed from
arm64, where it does not exist; `RELOCATABLE=y` on riscv64, on which `RANDOMIZE_BASE` depends
since Linux 7.2.

### 6. The requirement verifier gains the `resolved` evidence level

`verify-requirements.yml` keeps the declared check, then resolves: `verify_kernel_requirement.py
--plan` lists every bound stream with every architecture its documents list, a job per stream
fetches and verifies the source in an `ubuntu:26.04` container and resolves each architecture,
and the verifier decides each document again with `--resolved-config STREAM:ARCH=PATH`. At this
level a bound stream is judged on its resolved `.config`; a bound stream and listed architecture
without one is an error, not a pass. Unbound streams stay declared, as information. Nothing is
compiled; `abi.module-abi` still fails closed until a kernel is built.

### 7. The toolchain is part of the resolution

`versions.json` `architectures.<arch>` carries `kernel_arch`, `base_config` and `cross_compile`.
The prefix is used on every host, the native one included (Ubuntu names its native compiler
`x86_64-linux-gnu-gcc` too), so symbols that depend on the compiler, such as the
`CC_HAS_*` probes and `CC_VERSION_TEXT`, resolve against the compiler that builds. `pahole` is
required, because `DEBUG_INFO_BTF` depends on `PAHOLE_VERSION >= 122` and `SCHED_CLASS_EXT` on
BTF.

---

## Consequences

### Positive

- A source is used only when kernel.org's signature and this repository's pin agree. A
  compromised mirror, a replaced tarball and a moved tag are all refused.
- A fragment line that configures nothing fails at resolution time, on every leg, instead of
  shipping a kernel without it. The requirement gate checks the configuration a build will use.
- The `realtime` stream builds from a release that exists and receives stable fixes.

### Negative

- Bumping a stream now takes a `sha256` or a commit and, for a new signer, a key; `tests/test_manifest.py`
  checks the source against the version, not against kernel.org.
- The `bleeding` fetch transfers about 290 MB of git objects per resolution job; a release
  candidate has no smaller signed form.
- `mainstream` and `realtime` resolve to the same `make kernelrelease` (7.2.8). The build must set
  a localversion per stream before both are packaged.

### Neutral

- The resolved `.config` records the compiler (`CONFIG_CC_VERSION_TEXT`, `CONFIG_GCC_VERSION`),
  so its digest changes with the toolchain.
- `publish-release.yml` still publishes the declared merge as `kernel-<stream>.config` until the
  build replaces it with the resolved one.

---

## Compliance

1. **`tests/test_fetch_kernel_source.py`**: a throwaway key, a fake tarball and a local repository
   with signed tags; a good tarball and a good tag are written, and a wrong `sha256`, a signature
   over other content, a missing signature, a signature by an unlisted key, a key file holding
   another key, a tree of another release, an unknown stream, a tag at another commit, a tag
   signed by an unlisted key and an unsigned tag are refused with no tree left behind.
2. **`tests/test_kconfig_survival.py`**: the survival rules on synthetic configurations, and
   `scripts/merge-config.sh --source-tree` against a stand-in tree, including the refusal of a
   dropped request and of a tree of another release.
3. **`tests/test_manifest.py`**: every source names its stream's release, every signer has a key,
   a release candidate comes from a tag; no kernel version is written in a test.
4. **`tests/test_verify_kernel_requirement.py`**: the resolved level, missing resolved evidence,
   and the plan.
5. **`.github/workflows/verify-requirements.yml`**: resolves every bound stream on every listed
   architecture from its verified source on each pull request and push that touches the
   fragments, the scripts, `keys/` or `versions.json`, and weekly.

---

## References

- nucleus issue #18 (the real build), PR #35 (ADR-0007, the requirement gate).
- Decisions recorded for issue #18: all twelve stream and architecture legs are built; the
  `realtime` source is the mainline stable 7.2.y release with `CONFIG_EXPERT` and
  `CONFIG_PREEMPT_RT`, without the rt patch; the `bleeding` source is the latest mainline release
  candidate through its signed tag and `git verify-tag`; the base configuration is the
  architecture defconfig with the fragments and a survival check.
- <https://www.kernel.org/signature.html>, <https://www.kernel.org/releases.json> (2026-09-29).
- Linux 7.2.8: `scripts/kconfig/merge_config.sh`, `kernel/Kconfig.preempt`,
  `arch/{x86,arm64,riscv}/Kconfig` (`ARCH_SUPPORTS_RT`, riscv `RANDOMIZE_BASE`),
  `net/ipv4/Kconfig` (`TCP_CONG_ADVANCED`), `fs/fuse/Kconfig` (`VIRTIO_FS`), `block/elevator.c`.
- GnuPG `doc/DETAILS` (`VALIDSIG`, `GOODSIG`, `EXPKEYSIG`, `REVKEYSIG`).
