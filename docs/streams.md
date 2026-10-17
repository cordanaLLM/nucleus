# Kernel Release Streams

> Comprehensive specifications and workload mappings for all 4 kernel release streams in `cordanaLLM/nucleus`.

---

## 1. Stream Architecture

```text
                                upstream: kernel.org
                                         │
        ┌───────────────────┬────────────┴───────┬───────────────────┐
        ▼                   ▼                    ▼                   ▼
    bleeding            mainstream              lts               realtime
  (Linux 7.3-rc7)      (Linux 7.2.10)      (Linux 6.18.56)   (Linux 7.2.10 + RT)
        │                   │                    │                   │
        ▼                   ▼                    ▼                   ▼
  Blackwell B200       Intel Xe2 Arc (xe)   Enterprise K8s      Game Servers
  RTX 5090 / CXL 3.0   AMD ROCm (amdgpu)    OpenZFS 2.3 kmod    Low-latency UDP
  sched-ext eBPF       Kubernetes node      CloudNativePG       WireGuard Gateway
```

The versions above are the pins of `versions.json` on 2026-10-14; `versions.json` is the
authority, and section 3 says how each source is proven.

---

## 2. Stream Specifications

### 2.1 Bleeding Stream (`bleeding`)

- **Upstream Release**: Linux 7.3-rc7 (Mainline release candidate, from the signed tag)
- **Target Hardware**: NVIDIA Blackwell RTX 5090 and B200 accelerators, whose open kernel modules are checked against headers in CI ([packaging 5.7](packaging.md#57-nvidia-open-gpu-kernel-modules-build-githubworkflowsnvidia-open-moduleyml)); PCIe 6.0 / CXL 3.0 interconnects.
- **Key Features**: sched-ext user-space eBPF scheduler support, cutting-edge DRMs, prototype memory tiering.
- **Flavors**: `ai-infer-nvidia-bleeding`, `k8s-node-nvidia-bleeding`, `docker-nvidia-bleeding`.

### 2.2 Mainstream Stream (`mainstream`)

- **Upstream Release**: Linux 7.2.10 (Stable)
- **Target Hardware**: Intel Arc Battlemage Xe2 on `xe`, AMD Radeon RX 7000/8000 for ROCm on `amdgpu` with the KFD compute interface (`HSA_AMD`). NVIDIA's open kernel modules are checked against headers in CI ([packaging 5.7](packaging.md#57-nvidia-open-gpu-kernel-modules-build-githubworkflowsnvidia-open-moduleyml)). Section 4.1 lists the symbols and the check that refuses a build without them.
- **Key Features**: BBR congestion control (BBRv1 the default, BBRv3 as the `tcp_bbr3` module from the `bbr3` patch series), `mq-deadline` (the default for single-queue devices when built), Intel Level Zero compute.
- **Flavors**: `base-*`, `docker-*`, `podman-*`, `ai-infer-*`.

### 2.3 Long-Term Support Stream (`lts`)

- **Upstream Release**: Linux 6.18.56 (Longterm)
- **Target Workloads**: Enterprise Kubernetes nodes, OpenZFS 2.3.x root filesystems, PostgreSQL / CNPG databases.
- **Key Features**: Rock-solid stability, proven OpenZFS kernel module API stability, strict memory overcommit controls.
- **Flavors**: `k8s-node-*`, `cloudnative-storage`, `cloudnative-pg`.

### 2.4 Realtime Stream (`realtime`)

- **Upstream Release**: Linux 7.2.10 (Stable), built with the in-tree `PREEMPT_RT` option; no out-of-tree rt patch is applied
- **Target Workloads**: Low-jitter gaming servers (Counter-Strike 2, dedicated game hosts), software-defined radio, audio processing.
- **Key Features**: Deterministic interrupt handling, high-resolution timers, threaded IRQs.
- **Kconfig**: `kconfig/streams/realtime.config` sets `PREEMPT_RT`, `HZ_1000` and `EXPERT` (section 3).
- **Flavors**: `appliance-game-server`, `appliance-gateway-dns`.

---

## 3. Sources and Signatures

Every stream names its source in `versions.json` `streams.<stream>.source`, and
`scripts/fetch-kernel-source.sh --stream=<stream> --dest=<dir>` fetches it and proves it before
anything reads it. A failed check refuses with exit status 1 and leaves no tree behind: the
signature and the pin are checked before the tree is written, the release after, and a tree
that fails that last check is removed.

| Stream | Kind | Proof |
| :--- | :--- | :--- |
| `bleeding` | `git-tag` | `git verify-tag` on the tag fetched with `--depth=1` from `torvalds/linux.git`; the tag object must name the tag and point at the pinned `commit`. kernel.org signs no release-candidate tarball, so the tag is the only signed path. |
| `mainstream`, `lts`, `realtime` | `tarball` | the `.tar.xz` must match the pinned `sha256`, and its detached `.tar.sign`, which covers the uncompressed tar, must verify with `xz -cd linux-X.tar.xz \| gpgv --keyring <keyring> linux-X.tar.sign -`. |

The signature must be good and made by a key the stream lists in `signers`; the keyring holds
those keys and no others, read from [`keys/`](https://github.com/cordanaLLM/nucleus/tree/main/keys).
`mainstream` and `realtime` pin the same release and the same tarball: `realtime` differs only
in its fragment (section 4). The policy is
[ADR-0008](adr/0008-signed-kernel-sources-and-resolved-configuration.md).

```bash
# Fetch and verify the bleeding source into build/linux-bleeding (about 290 MB of git objects)
make fetch-source STREAM=bleeding
```

To bump a stream, change `version`, `tag` and `source` together: for a tarball the URL, the
signature URL and the `sha256` from kernel.org's signed `sha256sums.asc`; for a tag the tag and
the commit `git ls-remote` reports for `refs/tags/<tag>^{}`. `tests/test_manifest.py` checks that
the source names the stream's release.

---

## 4. Stream Fragments and the Resolved Configuration

`scripts/merge-config.sh --stream=<stream>` layers `kconfig/streams/<stream>.config` after
`kconfig/security-hardened.config`, the architecture fragment and `kconfig/k8s-node.config`
(section 4.1). The last line for a symbol
wins, and a `# CONFIG_X is not set` line in a later fragment unsets an earlier assignment. A
stream without a fragment builds the architecture configuration unchanged.

| Stream | Fragment | What it declares |
| :--- | :--- | :--- |
| `realtime` | `kconfig/streams/realtime.config` | `CONFIG_PREEMPT_RT=y` and `CONFIG_HZ_1000=y`, which Aegis-OS asks for together (`REQ-P07-01`), and `CONFIG_EXPERT=y`, because `PREEMPT_RT` depends on `EXPERT && ARCH_SUPPORTS_RT` (`kernel/Kconfig.preempt`); x86, arm64 and riscv select `ARCH_SUPPORTS_RT` in 6.18.56, 7.2.10 and 7.3-rc7 |
| `bleeding`, `mainstream`, `lts` | `kconfig/streams/<stream>.config` | `CONFIG_NETFILTER_XTABLES_LEGACY=y`, `CONFIG_IP_NF_IPTABLES_LEGACY=m`, `CONFIG_IP_NF_RAW=m` for kube-proxy and Cilium iptables mode (section 4.1) |

Every leg also carries the baseline, `kconfig/security-hardened.config`. It requests the chain
BPF trampolines need, so that BPF LSM, `fentry` and `fexit` programs can attach: `FTRACE`,
`FUNCTION_TRACER`, `DYNAMIC_FTRACE` and `DYNAMIC_FTRACE_WITH_DIRECT_CALLS`. Direct calls depend on
the architecture's `HAVE_DYNAMIC_FTRACE_WITH_DIRECT_CALLS`. x86 selects it unconditionally. arm64
selects it when `DYNAMIC_FTRACE_WITH_ARGS` resolves, and in 6.18.56 and 7.2.10 also needs
`DYNAMIC_FTRACE_WITH_CALL_OPS` (no `CFI`, not optimized for size). riscv selects it with
`DYNAMIC_FTRACE_WITH_CALL_OPS`. All twelve legs meet these conditions with the Ubuntu 26.04
toolchain, so none needs an architecture override
([ADR-0011](adr/0011-bpf-trampoline-ftrace-and-release-revisions.md)).

The baseline also requests `CRYPTO_DEFLATE`, which selects `ZLIB_DEFLATE`. OpenZFS refuses to
configure against a kernel without `ZLIB_DEFLATE`, and that symbol has no prompt: on the x86_64
defconfig base its only selector was `DRM_I915_COMPRESS_ERROR`, and `DRM_I915` depends on
`!PREEMPT_RT`, so the `realtime` kernels up to `7.2.8-lusoris2-realtime` built without it. The
baseline requests `ZLIB_DEFLATE=y` as well, so the survival check refuses any leg on which nothing
selects it (`scripts/kconfig_survival.py`).

With `--source-tree=<verified tree>`, the same fragments are resolved against the stream's
source: the architecture's `base_config` from `versions.json` (`x86_64_defconfig`, or
`defconfig` for arm64 and riscv64), then the kernel's `scripts/kconfig/merge_config.sh -m` with
the fragments in the order above, then `make olddefconfig`. The survival check
(`scripts/kconfig_survival.py`) then compares every value a fragment requests with the resolved
`.config` and refuses the result, listing symbol, requested value, resolved value and fragment,
when one did not survive. The result is `output/kernel-<stream>-<arch>.config`, with no
timestamp. The compiler is the architecture's `cross_compile` toolchain on every host, so
compiler-dependent symbols resolve as they will in the build, and `pahole` is required because
`CONFIG_DEBUG_INFO_BTF` depends on it.

```bash
# Merge x86_64 with the realtime layer and check the security baseline, without writing a file
make merge-config ARCH=x86_64 STREAM=realtime DRY_RUN=true

# Resolve realtime/arm64 against the verified tree and run the survival check
make fetch-source STREAM=realtime
make resolve-config STREAM=realtime ARCH=arm64
```

All twelve stream and architecture legs resolve with every requested value intact, and
`verify-requirements.yml` resolves all twelve on every pull request and push that touches the
fragments, the scripts, `keys/` or `versions.json`, whether or not a requirement document binds
the stream or lists the architecture. A failed resolution leaves no `.config` behind, neither
at the output path nor in the build directory. The kernel
release each resolved tree reports (`make kernelrelease`) is `7.3.0-rc7` for `bleeding`,
`7.2.10` for `mainstream` and `realtime`, and `6.18.56` for `lts`.

The build (`scripts/build_kernel.sh`, [packaging section 2](packaging.md)) adds the localversion
`-lusoris<N>-<stream>`, so every stream's kernel names itself, and `mainstream` and `realtime`,
built from the same tree, do not share a release or a package name:

| Stream | Kernel release (`uname -r`) | Package version | x86_64 boot to userspace |
| :--- | :--- | :--- | :--- |
| `bleeding` | `7.3.0-rc7-lusoris3-bleeding` | `7.3~rc7-lusoris3` | booted |
| `mainstream` | `7.2.10-lusoris3-mainstream` | `7.2.10-lusoris3` | booted |
| `lts` | `6.18.56-lusoris3-lts` | `6.18.56-lusoris3` | booted |
| `realtime` | `7.2.10-lusoris3-realtime` | `7.2.10-lusoris3` | booted |

A release candidate's package version spells `-rc7` as `~rc7`, so dpkg orders it before the
final release, and its package files spell that `~` as `.` ([packaging section 2.2](packaging.md)).

The table shows revision 3 of the current pins (tags `v7.3-rc7-bleeding-lusoris3`,
`v7.2.10-mainstream-lusoris3`, `v6.18.56-lts-lusoris3`, `v7.2.10-realtime-lusoris3`), which adds
user namespaces, PSI, the iSCSI TCP initiator, XFS, ZRAM, dm-thin, NBD and, except on `realtime`,
the legacy iptables raw table to the Kubernetes node configuration (section 4.1). Revision 2
added the Kubernetes node fragment and, on `mainstream`, the `xe` and `amdgpu` drivers; revision
1 of these pins carries the BPF trampoline chain and zlib deflate without them.
The earlier pins were released twice: revision 1 (`7.3.0-rc5-lusoris1-bleeding`,
`7.2.8-lusoris1-mainstream`, `6.18.54-lusoris1-lts`, `7.2.8-lusoris1-realtime`) without the
trampoline chain, and revision 2 (`-lusoris2`) with it, but without zlib deflate on `realtime`.
A new revision or a new upstream version is a new kernel release, so its image and headers
packages install next to the earlier one's ([packaging section 5.4](packaging.md)).

`build-matrix.yml` compiles every stream for x86_64, arm64 and riscv64, opens every package with
the artifact gate, and boots every x86_64 kernel under QEMU before it keeps the packages;
`publish-release.yml` boots the release kernel the same way before it signs anything.

### 4.1 The Kubernetes Node Fragment and the Required-Config Check

Every stream on every architecture is built to run a Kubernetes node with a container runtime
and the Cilium network plugin (imago `FLAVOR-K8S-NODE`). `kconfig/k8s-node.config` holds what
that needs: overlayfs, veth, user namespaces, pressure stall information (PSI) for the kubelet
and cgroup v2, VXLAN, Geneve, the bridge with `br_netfilter`, 802.1Q VLANs, nf_tables, ip_set,
the xtables matches and targets of Cilium's transparent proxy, the tc ingress hook with the
BPF classifier, dm-crypt, dm-thin, the iSCSI TCP initiator and transport class, XFS, ZRAM swap,
network block devices (NBD), NVMe authentication, vsock, UIO and iommufd. The legacy iptables raw
table for kube-proxy and Cilium iptables mode comes from the stream fragments of `bleeding`,
`mainstream` and `lts` (`kconfig/streams/{bleeding,mainstream,lts}.config`); `realtime` has none
because upstream forbids legacy xtables with `PREEMPT_RT` (kube-proxy nftables mode and Cilium
eBPF mode do not need it). The x86_64 fragment adds the platform drivers a reference node loads
(AES-NI and Camellia for dm-crypt, the AMD crypto coprocessor, VM generation ID, the QEMU firmware
configuration, VMware sockets, Intel platform telemetry) and the GPU drivers `mainstream` claims:
`xe` for Intel Arc Battlemage with the MEI GSC clients and the card's flash, and `amdgpu` with
`HSA_AMD` for ROCm. The list was taken from the modules a running node loads that the
`7.2.10-lusoris1-mainstream` release did not build; each fragment line says why it is there.

sched-ext is mainline since Linux 6.12 (`kernel/sched/ext.c` first appears at `v6.12`), and
every pinned stream is newer, so nucleus carries no patch for it: the architecture fragments set
`CONFIG_SCHED_CLASS_EXT=y` with the BTF chain it depends on, and the `sched-ext` claim below holds
it on every resolved leg (issue #191).

`kconfig/required-config.json` lists every claim this documentation makes about what a stream
builds, with the streams and architectures it covers and the symbols that back it:

| Claim | Streams | Architectures | Backed by |
| :--- | :--- | :--- | :--- |
| `k8s-node` | all | all | the common symbols of `kconfig/k8s-node.config` |
| `k8s-node-iptables-legacy` | `bleeding`, `mainstream`, `lts` | all | `NETFILTER_XTABLES_LEGACY=y`, `IP_NF_IPTABLES_LEGACY`, `IP_NF_RAW` |
| `k8s-node-x86_64` | all | x86_64 | the platform drivers of the x86_64 fragment |
| `mainstream-gpu` | `mainstream` | x86_64 | `DRM_XE`, the MEI GSC clients, `MTD_INTEL_DG`, `DRM_AMDGPU`, `HSA_AMD=y` |
| `bbr` | all | all | `TCP_CONG_BBR=y`, `DEFAULT_BBR=y`, `MQ_IOSCHED_DEADLINE=y` |
| `bbr3` | `bleeding`, `mainstream`, `realtime` | all | `TCP_CONG_BBR3` built, from the `bbr3` patch series ([patches.md](patches.md)) |
| `btf` | all | all | `DEBUG_INFO_BTF=y` |
| `sched-ext` | all | all | `SCHED_CLASS_EXT=y` |
| `realtime` | `realtime` | all | `PREEMPT_RT=y` |

`scripts/check_required_config.py` refuses a configuration in which a listed symbol is off
(exit status 1), naming the symbol, its value and the claim. A symbol listed `built` must be
`=y` or `=m`, one listed `y` must be `=y`. `scripts/merge-config.sh --source-tree` runs it on
every resolved configuration after the survival check, so the build (`scripts/build_kernel.sh`)
and the resolution of all twelve legs in `verify-requirements.yml` stop before a configuration
is written. The survival check proves that the fragments hold; this check proves that the
fragments still carry what is claimed: deleting `CONFIG_OVERLAY_FS=m` from the fragment passes
the survival check and is refused here. `tests/test_required_config.py` checks the declared
fragments of every leg against the list, that the list covers every line of
`kconfig/k8s-node.config`, and each way a configuration or the list can be wrong.

```bash
# Check a resolved configuration against the claims for its stream and architecture
python3 scripts/check_required_config.py --config output/kernel-mainstream-x86_64.config \
  --stream mainstream --arch x86_64
```

A claim that this documentation adds or a driver it names is added to the list in the same
change; a claim the forge cannot build is removed from the documentation instead.

---

## 5. Consumer Bindings

Downstream consumers publish their kernel requirements as `aegis.p01-nucleus.kernel-requirement.v1`
documents. `versions.json` `downstream.requirements[].streams` binds each document to the streams
its consumer uses:

| Consumer | Document | Bound streams | Why |
| :--- | :--- | :--- | :--- |
| `imago` | `cordanaLLM/imago` `kernel/requirement.json` | `bleeding`, `mainstream`, `lts`, `realtime` | imago pins a `kernel-<stream>.manifest.json` for any of the four (`sync-kernel-manifest.yml`) |
| `aegis-os` | `cordanaLLM/Aegis-OS` `build/kernel-requirement.json` | `realtime` | only `realtime` declares `PREEMPT_RT` and `HZ_1000` |

`verify-requirements.yml` fails a document when a bound stream does not hold it: the stream's
release is below `abi.minimum-release`, or a required symbol is not in its exact state on every
architecture the document lists. Streams a consumer is not bound to are reported as information
and never gate. It checks twice: against the declared fragments, then against the resolved
`.config` of every bound stream on every architecture the document lists (section 4), fetched
from the verified source; a missing resolved `.config` is an error, not a pass. A resolved
`.config` is accepted only for the leg its Kconfig header names (`# Linux/<ARCH> <release>
Kernel Configuration`); `mainstream` and `realtime` share a release, so the header cannot tell
those two apart. When a document specifies `abi.module-abi`, the verifier checks it against the
kernel release recorded in `--build-record STREAM:ARCH=PATH` for each bound stream on each listed
architecture ([ADR-0015](adr/0015-module-abi-from-the-build-record.md)); `verify-requirements.yml`
builds no kernels and provides no build records, so any document setting `abi.module-abi` fails
there. The policy and the contract are [ADR-0007](adr/0007-document-driven-kernel-requirements.md).

### 5.1 The owner's JSON Schema

Aegis-OS publishes a JSON Schema for the document (`build/kernel-requirement.schema.json`, draft
2020-12, generated from the owner's Rust types). `versions.json` `downstream.requirement_schema`
pins it by repository, path, commit and sha256, and
`tests/fixtures/kernel-requirement/aegis-kernel-requirement.schema.json` is the pinned file. It
applies to every row of `downstream.requirements`. Nothing else records the commit or the digest.

Before the verifier's verdict, `verify-requirements.yml` fetches the schema at the pinned commit
and refuses it unless its sha256 is the pinned one. `scripts/check_requirement_schema.py check`
then reads each fetched document twice, through the schema and through the verifier's parser.
Only a document both accept passes. A document one accepts and the other refuses fails the run,
and the report says which way. Three rules of the owner's decoder are outside what JSON Schema can
state, so the schema accepts and the verifier refuses a document that breaks one, and the report
names the rule: the 16384-byte bound, a `symbol` listed twice, and a `target-release` older than
`minimum-release`. The decision is [ADR-0010](adr/0010-owner-json-schema-for-requirement-documents.md).

To check documents against the pinned schema without the network:

```bash
python3 scripts/check_requirement_schema.py check \
  --requirement imago=tests/fixtures/kernel-requirement/imago.json \
  --requirement aegis-os=tests/fixtures/kernel-requirement/aegis-os.json
```

`make test` holds the rest offline: the vendored file's digest equals the pin, both committed
documents validate, the schema's names and bounds equal the verifier's constants, and a corpus of
documents runs through both readings and fails on any disagreement, listing each case.

**Refreshing the pin.** It moves when Aegis-OS changes the contract, in a reviewed change:

```bash
sha="$(gh api repos/cordanaLLM/Aegis-OS/commits/main --jq .sha)"
file=tests/fixtures/kernel-requirement/aegis-kernel-requirement.schema.json
gh api -H "Accept: application/vnd.github.raw+json" \
  "repos/cordanaLLM/Aegis-OS/contents/build/kernel-requirement.schema.json?ref=${sha}" > "${file}"
sha256sum "${file}"
```

Put the commit and the digest in `downstream.requirement_schema`, then run `make lint-manifest`
and `pytest tests/test_requirement_schema.py`. When the file did not change, only `commit` moves.
When it did, a failing test is the signal: a name or bound the verifier's constants do not carry,
or a corpus case on which the two readings split, is a change of the contract that
`scripts/verify_kernel_requirement.py` must follow in the same change.

### 5.2 Single-kernel checking (`--kernel-config`)

Downstream consumers can verify an individual kernel configuration against a requirement document
without `versions.json` or repository fragments, using `--kernel-config` and `--requirement-file`.
This is the single implementation Aegis-OS consumes from its pinned nucleus checkout
(`build/contract/producers.pin.json`) instead of maintaining `tools/kernel_requirement_check.py`.

```bash
python3 scripts/verify_kernel_requirement.py \
  --kernel-config /path/to/kernel.config \
  --requirement-file build/kernel-requirement.json \
  --kernel-release 7.2.10 \
  --report-json report.json
```

- `--kernel-config PATH`: the kernel configuration text to verify.
- `--requirement-file PATH`: the requirement document (`aegis.p01-nucleus.kernel-requirement.v1`).
- `--kernel-release RELEASE`: optional; checked against `abi.minimum-release` and exact `abi.module-abi`.
  When omitted, the check prints `release: not given; minimum-release and module-abi not checked`.
- `--report-json PATH`: writes the machine-readable decision (`nucleus.kernel-check-report.v1`).

Exit codes:

- `0`: all features and ABI floor/module-ABI rules satisfied.
- `1`: one or more features or ABI requirements are unmet.
- `2`: requirement document refused, or configuration unreadable or ungrammatical.
