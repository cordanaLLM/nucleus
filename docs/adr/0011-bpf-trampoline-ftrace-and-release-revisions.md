# ADR-0011: Function Tracing in the Baseline for BPF Trampolines, and Release Revisions Above 1

Date: 2026-09-29

## Status

Proposed

The owner of this repository decided both parts on 2026-09-29; this record states them.

Once accepted, this ADR supersedes one sentence of an accepted ADR:

- [ADR-0009](0009-kernel-compilation-and-artifact-gate.md), section 2, the clause that
  `scripts/resolve_release_tag.py` accepts only revision 1 for now: the resolver accepts every
  revision the build accepts (section 2 below). The rest of ADR-0009 stands, including `<N>` as the
  `--revision` of `scripts/build_kernel.sh`.

It adds four symbols to `kconfig/security-hardened.config` and supersedes nothing of
[ADR-0002](0002-modular-kconfig-architecture.md) or
[ADR-0008](0008-signed-kernel-sources-and-resolved-configuration.md): the symbols are resolved and
survival-checked like every other request.

---

## Context

Aegis-OS booted the first `realtime` release, `v7.2.8-realtime-lusoris1`. Its BPF LSM program
(`action_gate`) loaded, and its attach returned `-EBUSY`. Aegis proved the cause with an A/B
build: the release does not set `CONFIG_FUNCTION_TRACER`.

A BPF LSM program, and every `fentry`, `fexit` and `fmod_ret` program, runs from a BPF
trampoline. In Linux 7.2.8, `register_fentry()` in `kernel/bpf/trampoline.c` asks ftrace for a
call site at the entry of the target function (`ftrace_location()`):

- With a call site, ftrace calls the trampoline directly. That needs
  `CONFIG_DYNAMIC_FTRACE_WITH_DIRECT_CALLS`: without it, the trampoline has no ftrace ops and the
  attach returns `-ENOTSUPP`.
- Without a call site, which is the case without `CONFIG_FUNCTION_TRACER`, it falls back to
  `bpf_arch_text_poke()`. That refuses a kernel function on all three architectures:
  `-EBUSY` on x86_64, where there is no 5-byte nop to patch (`__bpf_arch_text_poke()`,
  `arch/x86/net/bpf_jit_comp.c`), `-ENOTSUPP` on arm64 ("Only poking bpf text is supported"),
  and `-EFAULT` on riscv64.

Every stream sets `CONFIG_BPF_LSM=y` on x86_64, and `CONFIG_LSM` lists `bpf`, so BPF LSM is
active at boot on every x86_64 release and no program can attach to it. The same holds for
`fentry` and `fexit` on every architecture.

The kernel's Kconfig (`kernel/trace/Kconfig`, the same in 6.18.54, 7.2.8 and 7.3-rc5):

- `FUNCTION_TRACER` is the only prompted symbol of the chain. It sits inside `if FTRACE`,
  depends on `HAVE_FUNCTION_TRACER`, and selects `KALLSYMS`, `GENERIC_TRACER`,
  `CONTEXT_SWITCH_TRACER`, `GLOB`, `NEED_TASKS_RCU` and `TASKS_RUDE_RCU`. Nothing in the chain
  refers to `PREEMPT_RT`.
- `FTRACE` defaults to `y` under `DEBUG_KERNEL`, which every architecture fragment sets, but the
  arm64 `defconfig` records `# CONFIG_FTRACE is not set`, and that line survives
  `olddefconfig`.
- `DYNAMIC_FTRACE` defaults to `y` under `FUNCTION_TRACER` and `HAVE_DYNAMIC_FTRACE`.
- `DYNAMIC_FTRACE_WITH_DIRECT_CALLS` has no prompt: `def_bool y`, depending on
  `DYNAMIC_FTRACE_WITH_REGS || DYNAMIC_FTRACE_WITH_ARGS` and on the architecture's
  `HAVE_DYNAMIC_FTRACE_WITH_DIRECT_CALLS`.

What the architectures require for `HAVE_DYNAMIC_FTRACE_WITH_DIRECT_CALLS` (`arch/*/Kconfig`):

| Architecture | 6.18.54 and 7.2.8 | 7.3-rc5 |
| :--- | :--- | :--- |
| x86 | selected unconditionally | the same |
| arm64 | `DYNAMIC_FTRACE_WITH_ARGS` (the compiler supports `-fpatchable-function-entry=2`) and `DYNAMIC_FTRACE_WITH_CALL_OPS` (no `CFI`, and not `CC_OPTIMIZE_FOR_SIZE` under GCC) | `DYNAMIC_FTRACE_WITH_ARGS` only |
| riscv | `HAVE_DYNAMIC_FTRACE_WITH_CALL_OPS` (`DYNAMIC_FTRACE_WITH_ARGS` and no `CFI`); `HAVE_DYNAMIC_FTRACE` needs `-fpatchable-function-entry=8` and, with compressed instructions, `CC_HAS_MIN_FUNCTION_ALIGNMENT` | the same |

With the Ubuntu 26.04 toolchain (GCC 15.2.0, native and cross), every one of these holds on all
twelve legs; none sets `CFI` or `CC_OPTIMIZE_FOR_SIZE`.

The first release of each stream has shipped at revision 1. Re-releasing a stream with a changed
configuration needs a second revision of the same upstream version, and the tag resolver refused
any revision but 1, although `scripts/build_kernel.sh --revision`, the artifact gate and
`publish-release.yml` already carried the revision end to end.

---

## Alternatives considered

1. **Declare the chain only where BPF LSM is built (x86_64).** Rejected: `fentry` and `fexit`
   need the same trampoline on every architecture, the owner asked for every stream and
   architecture, and the arm64 and riscv64 fragments would then carry a different tracing
   surface without a reason.
2. **Declare it in a stream fragment (`realtime`).** Rejected: every stream builds BPF LSM on
   x86_64, so every stream has the defect.
3. **Declare `FUNCTION_TRACER` alone and let the rest follow by default.** Rejected: the
   survival check proves only what is requested. `DYNAMIC_FTRACE_WITH_DIRECT_CALLS` is the
   symbol the trampoline needs, and it silently resolves `n` when an architecture condition
   fails (a compiler without `-fpatchable-function-entry`, `CFI`, optimizing for size).
   Requesting it makes such a leg fail its resolution instead of shipping a kernel whose BPF
   LSM cannot attach. `FTRACE` is requested because the arm64 `defconfig` unsets it.
4. **Keep function tracing out, as kernel-hardening-checker recommends.** Its
   `cut_attack_surface` checks (commit `b2acfd4`) want `FTRACE`, `FUNCTION_TRACER` and
   `GENERIC_TRACER` unset. Rejected: without them, BPF LSM, which the downstream security
   policy is built on, cannot attach at all. The surface is bounded instead (section 1).
5. **Release the fix as a new upstream version only.** Rejected: waits on kernel.org, and mixes
   an upstream bump with a configuration fix in one release.

---

## Decision

### 1. The baseline declares the trampoline chain on every leg

`kconfig/security-hardened.config` requests:

```ini
CONFIG_FTRACE=y
CONFIG_FUNCTION_TRACER=y
CONFIG_DYNAMIC_FTRACE=y
CONFIG_DYNAMIC_FTRACE_WITH_DIRECT_CALLS=y
```

All twelve legs resolve all four at `=y` and pass the survival check, so no architecture
fragment carries them. A leg on which one does not survive fails its resolution
(`scripts/kconfig_survival.py`); the fix then belongs in that architecture's fragment, never in a
weaker check.

Function tracing is compiled in and does not run:

- `DYNAMIC_FTRACE` keeps every call site a nop until a tracer or a BPF trampoline registers it.
  At boot `current_tracer` is `nop` and `enabled_functions` is empty.
- No fragment declares another tracer, `FTRACE_SYSCALLS` or `BOOTTIME_TRACING`, and nothing in
  this repository passes `ftrace=` on a kernel command line. `tests/test_kconfig.py` holds the
  first.
- tracefs (`/sys/kernel/tracing`) is created with mode `0700` (`TRACEFS_DEFAULT_MODE`,
  `fs/tracefs/inode.c`), root only.
- Loading a tracing or LSM program needs `CAP_BPF` and `CAP_PERFMON` (`kernel/bpf/syscall.c`),
  and `BPF_UNPRIV_DEFAULT_OFF` stays set.

What else changes, from the resolved configurations before and after (defaults, not requests):
`FUNCTION_GRAPH_TRACER=y` on every leg (a default under `FUNCTION_TRACER`, idle until used),
`KPROBES_ON_FTRACE=y` on x86_64, `FUNCTION_ALIGNMENT` 8 on arm64 and riscv64, and on arm64 the
tracing menu the `defconfig` had switched off: event tracing, uprobe events and `BPF_EVENTS`
(arm64 builds no kprobes, so no kprobe events). Every tracer the menu offers stays at its default, off.

### 2. A release tag may name any revision the build accepts

`scripts/resolve_release_tag.py` accepts `<N>` from 1 to 9999 without leading zeros, which is
exactly what `scripts/build_kernel.sh --revision` accepts (`^[1-9][0-9]{0,3}$`); a test holds the
two ranges together. The rest was in place: `publish-release.yml` passes the resolved `rev` to
the build and to the artifact gate, the manifest's `version` is the resolver's
`release_version` (`<version>-lusoris<N>`), and its `kernel.release` is the release the build
recorded (`<kernelversion>-lusoris<N>-<stream>`).

A revision is a new kernel release. `linux-image-<kernelrelease>` and
`linux-headers-<kernelrelease>` are new package names that install next to the earlier revision;
`linux-libc-dev` keeps its name and upgrades, since dpkg orders `7.2.8-lusoris2` after
`7.2.8-lusoris1` (and `lusoris10` after `lusoris9`).

All four streams are re-released as revision 2 with the chain of section 1:
`v7.3-rc5-bleeding-lusoris2`, `v7.2.8-mainstream-lusoris2`, `v6.18.54-lts-lusoris2` and
`v7.2.8-realtime-lusoris2`.

---

## Consequences

### Positive

- BPF LSM, `fentry`, `fexit` and `fmod_ret` programs attach on every stream and architecture.
  The realtime x86_64 build of this change attaches an LSM program on `file_open` and an
  `fentry` program on `security_file_open` in QEMU, and both run; the released
  `7.2.8-lusoris1-realtime` refuses both with `-EBUSY`.
- A leg whose toolchain loses direct calls fails its resolution instead of shipping.
- A configuration fix can be released without waiting for an upstream version.

### Negative

- More kernel attack surface: tracefs, the function tracer, the function graph tracer, and on
  arm64 uprobe events are compiled in. They are root only and idle, but a root user, or a process
  with `CAP_BPF` and `CAP_PERFMON`, can now trace kernel functions. This departs from
  kernel-hardening-checker's `cut_attack_surface` recommendation for `FTRACE`,
  `FUNCTION_TRACER` and `GENERIC_TRACER`.
- Every traceable function entry carries a patchable nop, and ftrace records every call site:
  56130 entries in 220 pages on the realtime x86_64 kernel. The realtime x86_64 `vmlinuz` grows
  from 16851968 to 17388544 bytes (3.2 percent).
- Revision 1 and revision 2 of a stream coexist on a host as two kernels until one is removed.

### Neutral

- x86_64 and riscv64 already built `FTRACE` (the `DEBUG_KERNEL` default); arm64 did not.
- `CONFIG_BPF_LSM` is still declared for x86_64 only. arm64 and riscv64 gain the trampoline, not
  BPF LSM.
- The downstream requirement documents do not ask for these symbols yet. Aegis-OS plans a row
  for `DYNAMIC_FTRACE_WITH_DIRECT_CALLS` (`REQ-P06-05`); once published, the verifier checks it
  like any other.

---

## Compliance

1. **`scripts/kconfig_survival.py`** through `scripts/merge-config.sh --source-tree`, run for all
   twelve legs by `verify-requirements.yml` and before every compile by
   `scripts/build_kernel.sh`: the four symbols must resolve `=y`.
2. **`tests/test_kconfig.py`**: the chain is in the baseline, no architecture or stream fragment
   drops it, and no fragment declares a tracing symbol beyond it.
3. **`tests/test_resolve_release_tag.py`**: revisions 2 to 9999 resolve, `0`, leading zeros and
   10000 are refused; the resolver and `build_kernel.sh --revision` accept the same range; a
   revision-2 tag of each stream resolves to `rev=2` and plans `LOCALVERSION=-lusoris2-<stream>`
   and `KDEB_PKGVERSION=<debian_version>-lusoris2`; build, gate and manifest take the revision
   from the resolver.
4. **`tests/test_notify_downstream.py`**: a revision-2 tag dispatches its own version and tag.
5. **`build-matrix.yml`** and **`publish-release.yml`**: every x86_64 kernel boots to userspace
   under QEMU before it is kept or signed.

---

## References

- Aegis-OS finding (2026-09-29, M19): `action_gate` BPF LSM attach `-EBUSY` on
  `v7.2.8-realtime-lusoris1`, root cause `CONFIG_FUNCTION_TRACER` not set, proven by A/B build;
  cordanaLLM/Aegis-OS#151.
- Owner decision, 2026-09-29: fix in the security baseline for every stream and architecture, and
  re-release all four streams as revision 2.
- Linux 6.18.54, 7.2.8 and 7.3-rc5: `kernel/trace/Kconfig`, `kernel/bpf/trampoline.c`
  (`register_fentry`), `kernel/bpf/syscall.c` (`is_perfmon_prog_type`),
  `arch/x86/net/bpf_jit_comp.c` (`__bpf_arch_text_poke`), `arch/arm64/net/bpf_jit_comp.c` and
  `arch/riscv/net/bpf_jit_comp64.c` (`bpf_arch_text_poke`), `arch/{x86,arm64,riscv}/Kconfig`,
  `fs/tracefs/inode.c`.
- kernel-hardening-checker, `kernel_hardening_checker/checks.py` at `b2acfd4`
  (`cut_attack_surface`: `FTRACE`, `FUNCTION_TRACER`, `GENERIC_TRACER`).
- ADR-0008 (resolution and survival), ADR-0009 section 2 (release and package names).
