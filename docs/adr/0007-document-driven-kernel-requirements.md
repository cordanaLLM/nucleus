# ADR-0007: Document-Driven Kernel Requirement Verification with Per-Consumer Stream Binding

Date: 2026-09-29

## Status

Accepted (2026-09-29)

Once accepted, this ADR supersedes section 2, "Upstream Requirement Verification", of
[ADR-0005](0005-bidirectional-image-forge-synchronization.md), and Channel 2 of its decision
diagram. That channel ends with "Post pass/fail status to commit check run" in the consumer.
Nothing is posted back to a consumer, and this forge holds no token that could post it: the
result of a run is the nucleus run summary and its `kernel-requirement-report` artifact
(section 5). Section 1, section 3 and Channel 1 of ADR-0005 stand.

It merged as Proposed because two of its questions belonged to the contract owner, Aegis-OS:
the `required-by` grammar and the array form (section 2), and whether a listed architecture is
a promise or an option (section 3). Aegis-OS answered both on 2026-09-29 (Aegis-OS#167, merged
as `18c9791`), and the owner of this repository accepted this record the same day.

---

## Context

ADR-0005 section 2 says that `verify-requirements.yml` "validates that all active `.config`
trees contain the requested symbols". Until this decision the workflow compared nineteen
`SYMBOL=y` strings, copied by hand into the workflow, against two fragments (issue #20).

The requirement already exists as a document with an owner:

- `cordanaLLM/Aegis-OS` publishes `build/kernel-requirement.json` and `cordanaLLM/imago`
  publishes `kernel/requirement.json`, both of shape `aegis.p01-nucleus.kernel-requirement.v1`.
- The normative definition is the Aegis crate `crates/aegis-fabrica-defs`: `src/kernel.rs`
  (the document, its states, probes and invariants), `src/field.rs` (every field encoding and
  the release order) and `src/payload.rs` (the byte bound). Aegis publishes no JSON Schema.

A hand-copied list loses what the document carries. At `c5fbeb8` it checked 2 of the 13
symbols Aegis requires. It cannot express `module` against `built-in`, or `present` and
`absent`. When a symbol is missing it cannot name the `required-by` entry that breaks.

"All active `.config` trees" is also the wrong policy for more than one consumer. Aegis asks
for `CONFIG_PREEMPT_RT` and `CONFIG_HZ_1000` (`REQ-P07-01`), and only the `realtime` stream
carries them. Holding every stream to every document either turns every stream into a
realtime kernel or fails Aegis on every run.

## Alternatives considered

1. **Every stream at or above the release floor meets every feature.** Fails Aegis unless
   every stream becomes a realtime kernel.
2. **At least one stream meets every feature.** Passes today, but a regression in the stream
   a consumer uses stays green while another stream satisfies the document, which is the
   drift issue #20 exists to catch.
3. **Filter streams by `abi.target-release`.** The owner records `target-release` and never
   checks it (`kernel.rs`, `KernelAbi::target_release`): the target named by its sources is
   not a released version. Aegis's `7.3` target would select `bleeding`, which has no
   `PREEMPT_RT`.
4. **Exempt symbols per stream inside this forge.** Encodes one consumer's semantics in the
   forge, and lists streams as satisfying a document they do not satisfy.
5. **Bind each consumer to streams in `versions.json`.** Chosen.

## Decision

### 1. The document is the requirement

`versions.json` `downstream.requirements` declares every document: `label`, `repository`,
`path`, whether the consumer `dispatched` it, and the `streams` the consumer is bound to.

| Label | Document | Dispatches | Bound streams |
| :--- | :--- | :--- | :--- |
| `imago` | `cordanaLLM/imago` `kernel/requirement.json` | yes | `bleeding`, `mainstream`, `lts`, `realtime` |
| `aegis-os` | `cordanaLLM/Aegis-OS` `build/kernel-requirement.json` | no | `realtime` |

- **imago** syncs and pins a `kernel-<stream>.manifest.json` for any of the four streams:
  `.github/workflows/sync-kernel-manifest.yml` downloads `kernel-${STREAM}.manifest.json`,
  `pkg/kernel/artifact.go` accepts `Streams = {bleeding, mainstream, lts, realtime}`, and its
  `versions.schema.json` allows the same four names under `kernel.streams` (imago `16f964b`).
- **Aegis-OS** requires `PREEMPT_RT` and `HZ_1000` (`REQ-P07-01`), which only the `realtime`
  stream declares (`kconfig/streams/realtime.config`).

`scripts/fetch-kernel-requirements.sh` reads each document at one commit. A
`repository_dispatch` pins the dispatched row whose label equals the payload's `source` to the
dispatched commit, and is refused only when no dispatched row carries that label. Every other
row is read at the commit its default branch resolves to at fetch time. The commit and the
document's SHA-256 are printed and recorded in the report. Payload fields reach the script
through the environment and are never interpolated into a script.

`scripts/verify_kernel_requirement.py` proves a dispatched digest and correlation id before the
document is used, decodes it the way the owner does, evaluates every stream and decides.

### 2. Decoding follows the owner, with two divergences

| Rule | Owner (`aegis-fabrica-defs`) | This forge |
| :--- | :--- | :--- |
| Byte bound | 16384, checked before parsing | same |
| Unknown field, any level | refused (`deny_unknown_fields`) | same; a duplicate JSON key too |
| Another `schema` | `UnknownVersion`, distinct from `Malformed`; a lenient peek reads `schema` and skips every other field, repeats included | same |
| `correlation-id` | 1 to 128 bytes of `[A-Za-z0-9._:-]` | same |
| `architectures` | 1 to 4 of `x86-64`, `arm64`; a repeat is accepted | same; evaluated once, `x86-64` builds `x86_64` |
| `features` | 1 to 64; empty is `NoFeatures`; a repeated symbol is `DuplicateSymbol` | same |
| `symbol` | `CONFIG_` and `[A-Z0-9_]`, at most 64 bytes | same |
| `state` | `built-in`, `module`, `present`, `absent` | same |
| Releases | start with a digit, `[0-9A-Za-z._+-]`, at most 64 bytes | same |
| `target-release` older than `minimum-release` | `InvertedRelease` | same |
| `artifact` | optional: `digest` (64 lower-case hex), optional `signature` (even-length hex, at most 256) | accepted and echoed in the report, marked not verified |
| `required-by` | `REQ-` prefix, `[A-Z0-9-]`, at most 128 bytes; the bare `REQ-` is refused | **`[A-Z][A-Z0-9-]*`, at most 128 bytes, for every source**; the bare `REQ-` is refused |
| Array form | a JSON array of the fields in declaration order decodes (serde's derived `visit_seq`); an array whose first element is another version is `UnknownVersion` | **refused as `Malformed`** |

The `required-by` divergence exists because imago's live document names its flavors
(`FLAVOR-BASE`, `FLAVOR-K8S-NODE`), which the owner's `RequirementId` refuses. It applies to
every source, Aegis-OS's own document included; every identifier the owner accepts is still
accepted. Aegis-OS resolved it by widening `required-by` to the same grammar (its decision D103,
Aegis-OS#167); `REQ-` is now enforced only on the documents Aegis-OS itself issues.

The array form is refused here. `deny_unknown_fields` does not apply to a JSON array, so the
owner's derived decoder accepts a document written as an array of its values, in field order.
No publisher writes that form. Aegis-OS confirmed it as a decoder bug and now decodes every
contract struct from a JSON object only (Aegis-OS#167), so both sides refuse it.

`tests/test_verify_kernel_requirement.py` ports the owner's vectors from
`tests/kernel_requirement.rs` where they apply, and pins both divergences.

### 3. Every bound stream must hold the document

A bound stream holds a document when:

- its release is at least `abi.minimum-release`, compared as the owner's
  `KernelRelease::at_least` does: leading dotted numeric components, a missing one reads as
  zero, a suffix is ignored. `7.2-rt` reads as 7.2; `7.3-rc2` meets a `7.3.0` floor; a release
  exactly at the floor is accepted;
- every feature is in exactly its state on **every** architecture the document lists: `y` for
  `built-in`, `m` for `module`, either for `present`, and an explicit
  `# CONFIG_X is not set` for `absent`. A symbol no fragment records satisfies nothing,
  `absent` included. The lookup is by exact symbol, so `CONFIG_KVM_GUEST` never answers for
  `CONFIG_KVM`.

The document passes when every bound stream holds it, and fails otherwise. `held` names the
bound streams that passed. Each failure names the correlation id, the stream, the
architecture, the symbol, its `required-by`, the required state and the observed value.
Streams a consumer is not bound to are evaluated and reported as information, and never gate.

`abi.target-release` is recorded and never checked. `abi.module-abi` names the release of a
built kernel and cannot be verified before one exists (issue #18), so a document that sets it
fails, saying so.

The owner's own check against a reference profile accepts a profile whose architecture is any
listed one (`kernel.rs`, `unmet_identity`). This forge builds for every listed architecture,
so it requires all of them; Aegis-OS confirmed that a listed
architecture is a promise and made its own check all-of as well (its decision D104,
Aegis-OS#167). Both live documents list only `x86-64`.

### 4. The evidence level is `declared`

The configuration checked is the fragments as `scripts/merge-config.sh` merges them:
`security-hardened.config`, the architecture fragment, then `kconfig/streams/<stream>.config`.
The merge keeps `# CONFIG_X is not set` lines, so a later fragment can unset an earlier
assignment, refuses any line that is not kconfig, and writes no timestamp. The verifier reads
the same grammar and a parity test compares the two for every stream and architecture in
`versions.json`.

`make olddefconfig` can still drop a declared symbol whose dependencies are unmet. The
fragments therefore declare the dependency chains the requirement symbols need:
`CONFIG_EXPERT` for `PREEMPT_RT`, `DEBUG_KERNEL` and `DEBUG_INFO_DWARF5` for
`DEBUG_INFO_BTF`, `IKCONFIG_PROC` for the `kernel-config` probe, `PCI` for `VFIO_PCI` and
`INTEL_RAPL`, and `CONFIG_LSM` with `bpf` for BPF LSM. Issue #18 adds a `resolved` level from
the built `.config`, through the same `stream_result` entry point.

The four runtime probes (`lsm-list`, `btf-vmlinux`, `powercap`, `iommu-groups`) read a running
kernel. They are checked through their Kconfig symbol, and the report marks each one "probe
deferred to boot".

### 5. The report is internal; the result is the artifact manifest

The verifier writes `nucleus.kernel-requirement-report.v1`: the correlation id, the source
(label, repository, path, commit, SHA-256), the nucleus revision, the policy, and per stream,
architecture and feature the symbol, `required-by`, required state, observed value,
satisfaction and probe. It is internal, not a contract. The result a consumer verifies remains
the `imago.nucleus.kernel-artifact.v1` manifest `publish-release.yml` emits (Aegis-OS#150).

---

## Consequences

### Positive

- A symbol a consumer adds reaches this gate through the document, with its state and its
  `required-by`; nothing is copied by hand.
- A regression in the stream a consumer is bound to fails the gate, even while another
  stream would satisfy the document.
- A document the owner accepts in object form is accepted here. The two exceptions,
  `required-by` and the array form, are named in section 2.

### Negative

- `declared` proves the fragments ask for each symbol, not that a build keeps it. The
  fragments are overlays on an architecture defconfig: resolved against an `allnoconfig` base
  instead, `KVM`, `VFIO=m`, `BPF_LSM` and `INTEL_RAPL=m` are lost. The base configuration is
  part of issue #18.
- Binding a new consumer is a `versions.json` change reviewed here, not a self-service step.
- imago's dispatch has not completed a run yet, so the schedule, pull request and push
  triggers are what exercise this gate today.

### Neutral

- The `realtime` source is issue #18's decision: its `versions.json` tarball URL returns 404
  (the rt directory holds `patch-7.2-rt5`). Mainline Linux 7.2 carries `PREEMPT_RT`
  (`kernel/Kconfig.preempt`), and `kconfig/streams/realtime.config` resolves against it.

---

## Compliance

1. **`tests/test_verify_kernel_requirement.py`**: the owner's decoding vectors, release order,
   state semantics, the policy (mixed streams, a bound stream below the floor, a release exactly
   at the floor, `7.3-rc2` against `7.3.0`, `target-release` never filtering, `m` against
   `built-in`, `n` against `module`, `absent`), parity between the verifier and
   `scripts/merge-config.sh`, the binding invariants of `versions.json`, and the command line,
   including a dispatch whose digest and correlation id match.
2. **`tests/test_fetch_kernel_requirements.py`**: which row a dispatch pins, which rows are read
   at their default branch, and which payloads are refused, against a stub `gh`; and the fetched
   arguments driving the verifier end to end.
3. **`tests/test_kconfig.py`**: the dependency chains the fragments declare.
4. **`.github/workflows/verify-requirements.yml`**: runs on dispatch, weekly, and on every pull
   request and push that touches the fragments, the scripts, `versions.json` or the workflow.
   Each dispatch run has a concurrency group of its own, so a later run never cancels a pending
   pinned verification, and the job runs `bash` with `pipefail`, so the verifier's exit status
   survives `tee`. `tests/test_workflows.py` pins both.

---

## References

- nucleus issue #20; issue #18 (the real build).
- Aegis-OS#151 (E10-5), #150 (E10-4), #117, #50; `crates/aegis-fabrica-defs` at `54c710c`.
- imago `16f964b`: `kernel/requirement.json`, `.github/workflows/dispatch-kernel-requirements.yml`,
  `.github/workflows/sync-kernel-manifest.yml`, `pkg/kernel/artifact.go`.
- Linux 7.2: `kernel/Kconfig.preempt` (`PREEMPT_RT`), `lib/Kconfig.debug` (`DEBUG_INFO_BTF`),
  `init/Kconfig` (`IKCONFIG_PROC`, `EXPERT`), `security/Kconfig` (`LSM`).
