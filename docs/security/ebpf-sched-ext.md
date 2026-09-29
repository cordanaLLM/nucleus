# eBPF Verifier Hardening & sched-ext Containment

> In-depth engineering guide on eBPF verifier security, JIT blinding, BPF LSM access control, and failsafe containment for `sched-ext` dynamic CPU schedulers.

---

## 1. Architectural Role of eBPF in Lusoris

Extended Berkeley Packet Filter (eBPF) provides kernel-level programmability across three foundational pillars in `cordanaLLM/imago`:

1. **High-Performance Networking**: Cilium eBPF datapaths, XDP (eXpress Data Path) DDoS mitigation, and BBRv3 congestion telemetry.
2. **Runtime Security Telemetry**: BPF LSM (Linux Security Module) hooks, system call interception, and container privilege monitoring.
3. **Dynamic CPU Scheduling (`sched-ext`)**: User-space extensible scheduler framework (`CONFIG_SCHED_CLASS_EXT=y`) enabling workload-specific schedulers (e.g., `scx_bpfland`, `scx_rusty`, `scx_lavd`) to optimize cache affinity and gaming/database latencies.

However, loading arbitrary bytecode into the kernel introduces severe risks if not strictly contained. `cordanaLLM/nucleus` implements defense-in-depth verifier hardening and hardware watchdog mechanisms.

```mermaid
flowchart TD
    subgraph UserSpace["User-Space Applications"]
        APP["Cilium / Tetragon / scx_rusty"]
        UNPRIV["Unprivileged Processes"]
    end

    subgraph SecurityGate["Security Gate & Verifier"]
        UNPRIV_CHK["bpf_unpriv_disabled check (CAP_BPF / CAP_SYS_ADMIN)"]
        VERIFIER["In-Kernel eBPF Verifier (DAG, Pointer & Range Checks)"]
        JIT_HARD["Constant Blinding & JIT Hardening (net.core.bpf_jit_harden=2)"]
    end

    subgraph Execution["Hardened Kernel Subsystem"]
        JIT_EXEC["Direct JIT Execution (Interpreter Permanently Disabled)"]
        BPF_LSM["BPF LSM Hooks (Security Policies)"]
        SCX["sched-ext Dynamic Scheduler Engine"]
        WATCHDOG["Hardware Watchdog & Automatic EEVDF Fallback"]
    end

    UNPRIV -->|Blocked with EPERM| UNPRIV_CHK
    APP -->|Verified CAP_BPF| VERIFIER
    VERIFIER --> JIT_HARD
    JIT_HARD --> JIT_EXEC
    JIT_EXEC --> BPF_LSM
    JIT_EXEC --> SCX
    SCX -.->|Stall or Panic Detected| WATCHDOG
    WATCHDOG ==>|Instant Recovery| EEVDF["Default CFS/EEVDF Scheduler"]
```

---

## 2. Kernel Verifier & JIT Hardening Configuration

The following configuration fragment is enforced across all production kernel streams (`kconfig/security-hardened.config`):

```ini
# Core eBPF Subsystem
CONFIG_BPF=y
CONFIG_BPF_SYSCALL=y
CONFIG_BPF_JIT=y
CONFIG_BPF_JIT_ALWAYS_ON=y
CONFIG_BPF_JIT_DEFAULT_ON=y

# Verifier Hardening & Attack Surface Elimination
CONFIG_BPF_UNPRIV_DEFAULT_OFF=y
CONFIG_BPF_PRELOAD=y
CONFIG_BPF_LSM=y

# Extensible Dynamic Scheduler Class (Linux 6.12+ / 7.x)
CONFIG_SCHED_CLASS_EXT=y

# Security Tracing & BTF Metadata
CONFIG_DEBUG_INFO_BTF=y
CONFIG_BPF_EVENTS=y
CONFIG_KPROBES=y
CONFIG_UPROBES=y
```

### Critical Security Rationale:
- **`CONFIG_BPF_JIT_ALWAYS_ON=y`**: Completely compiles out the eBPF bytecode interpreter from the kernel binary. By eliminating the interpreter, an attacker cannot stitch together interpreter opcodes into Return-Oriented Programming (ROP) gadgets.
- **`CONFIG_BPF_UNPRIV_DEFAULT_OFF=y`**: Enforces by default that unprivileged local users cannot invoke `sys_bpf()`. Only processes with `CAP_BPF` or `CAP_SYS_ADMIN` can load programs or inspect maps.
- **`CONFIG_DEBUG_INFO_BTF=y`**: Generates BPF Type Format (BTF) deduplicated debug metadata directly within the kernel image, enabling Compile Once – Run Everywhere (CO-RE) verification without requiring host compiler installations.

---

## 3. Runtime JIT Blinding & Telemetry Controls

At runtime, the following sysctl parameters are applied:

```ini
# /etc/sysctl.d/99-ebpf-hardening.conf
net.core.bpf_jit_harden = 2
net.core.bpf_jit_kallsyms = 1
kernel.unprivileged_bpf_disabled = 1
```

### JIT Constant Blinding (`bpf_jit_harden = 2`):
When user-space programs load BPF instructions containing immediate scalar constants (e.g. `mov r1, 0xdeadbeef`), an attacker might attempt JIT spraying to inject executable shellcode fragments into JIT memory pages.

Setting `bpf_jit_harden = 2` forces the JIT compiler to blind all user-supplied constants:
1. The kernel generates a cryptographically secure random 32/64-bit mask $M$.
2. The constant $C$ is transformed into $C \oplus M$.
3. The JIT emits instructions that unmask the value at runtime (`val = (C ^ M) ^ M`).
4. As a result, the compiled machine code no longer contains the raw user payload in memory, completely neutralizing JIT-spraying attacks.

---

## 4. `sched-ext` Architecture & Watchdog Containment

`sched-ext` allows developers to replace the default Linux CPU scheduler (`EEVDF`) with dynamic, user-space-directed BPF schedulers:

### 4.1 Failsafe Watchdog & Automatic CFS/EEVDF Fallback
The primary hazard of dynamic scheduling is starvation: an errant BPF scheduler could starve vital system tasks, deadlocking the machine.

To prevent this, the kernel's `sched-ext` subsystem enforces strict safety invariants:
1. **Heartbeat Timer**: The kernel runs an independent hardware watchdog timer (`scx_watchdog`).
2. **Stall Detection**: If any runnable thread is not scheduled within `scx_watchdog_timeout` (default: 30 seconds), the watchdog triggers.
3. **Graceful Eviction**: The kernel immediately detaches the active `sched-ext` scheduler (`scx_ops`), logs a descriptive trace to `dmesg`, and seamlessly falls back to standard CFS/EEVDF scheduling without dropping active processes or rebooting.

```mermaid
sequenceDiagram
    autonumber
    participant App as BPF Scheduler (scx_bpfland)
    participant SCX as Kernel sched-ext Subsystem
    participant WD as Kernel Hardware Watchdog
    participant EEVDF as Native EEVDF Scheduler

    App->>SCX: Register scx_ops (ops.select_cpu, ops.enqueue, ops.dispatch)
    SCX->>SCX: Switch default CPU scheduling class to SCX
    loop Normal Execution
        SCX->>WD: Pet watchdog heartbeat
    end
    Note over App: Bug or deadlock in user-space scheduler!
    WD->>WD: Watchdog timeout expires (heartbeat missed)
    WD->>SCX: Trigger emergency scx_ops detachment
    SCX->>EEVDF: Fall back to native CFS/EEVDF
    Note over EEVDF: System continues running uninterrupted
```

### 4.2 `sched-ext` & Realtime (`PREEMPT_RT`) Coexistence Contract
- In standard kernels (`bleeding`, `mainstream`), `sched-ext` operates freely.
- In the `realtime` (7.2.8, in-tree `PREEMPT_RT`) stream, `CONFIG_PREEMPT_RT=y` requires deterministic microsecond bounded latency. Tasks marked `SCHED_FIFO` or `SCHED_RR` always bypass `sched-ext` and are serviced directly by the real-time scheduling class.

---

## 5. Audit & Diagnostics

To verify eBPF hardening and `sched-ext` status:

```bash
# Check BPF sysctl status
sysctl net.core.bpf_jit_harden kernel.unprivileged_bpf_disabled

# Inspect currently loaded BPF programs and memory consumption
bpftool prog show
bpftool map show

# Check active scheduler class
cat /sys/kernel/sched_ext/state
```
Under operational status, `sysctl` reports `bpf_jit_harden = 2` and `/sys/kernel/sched_ext/state` confirms whether an extensible scheduler is enabled or in fallback state.

---

## 6. BPF Trampolines and Function Tracing

A BPF LSM program, and every `fentry`, `fexit` and `fmod_ret` program, runs from a BPF
trampoline, and the kernel reaches the trampoline through ftrace. `register_fentry()` in
`kernel/bpf/trampoline.c` hands the attach to ftrace when the target function has an ftrace call
site, which needs `CONFIG_DYNAMIC_FTRACE_WITH_DIRECT_CALLS`. Without `CONFIG_FUNCTION_TRACER` there
is no call site, and the fallback refuses a kernel function: `-EBUSY` on x86_64, `-ENOTSUPP` on
arm64, `-EFAULT` on riscv64. The program loads and cannot attach. That is what happened to
Aegis-OS's BPF LSM program on the revision 1 releases, which did not set `CONFIG_FUNCTION_TRACER`.

`kconfig/security-hardened.config` therefore requests the chain on every stream and architecture:

```ini
CONFIG_FTRACE=y
CONFIG_FUNCTION_TRACER=y
CONFIG_DYNAMIC_FTRACE=y
CONFIG_DYNAMIC_FTRACE_WITH_DIRECT_CALLS=y
```

All twelve legs resolve the four at `=y`, and the survival check refuses a leg on which one does
not survive, for example when a toolchain loses the compiler support that direct calls need on
arm64 and riscv64. The decision, the per-architecture conditions and the trade-off are
[ADR-0011](../adr/0011-bpf-trampoline-ftrace-and-release-revisions.md).

### 6.1 Compiled in, not running

- **No tracing at boot.** `DYNAMIC_FTRACE` keeps every call site a nop until a tracer or a BPF
  trampoline registers it. A booted kernel reports `current_tracer` as `nop` and an empty
  `enabled_functions`. No fragment declares another tracer, `FTRACE_SYSCALLS` or
  `BOOTTIME_TRACING` (`tests/test_kconfig.py`), and nothing here passes `ftrace=` on the command
  line.
- **Root only.** tracefs, at `/sys/kernel/tracing`, is created with mode `0700`
  (`TRACEFS_DEFAULT_MODE` in `fs/tracefs/inode.c`). Loading a tracing or LSM program needs
  `CAP_BPF` and `CAP_PERFMON` (`kernel/bpf/syscall.c`), and `CONFIG_BPF_UNPRIV_DEFAULT_OFF` stays
  set.
- **What comes with it.** `FUNCTION_GRAPH_TRACER` is built by default under `FUNCTION_TRACER`,
  x86_64 gains `KPROBES_ON_FTRACE`, and arm64, whose `defconfig` had switched the tracing menu
  off, gains event tracing, uprobe events and `BPF_EVENTS`. Each tracer stays off until root
  starts it. This departs from kernel-hardening-checker, whose `cut_attack_surface` checks want
  `FTRACE`, `FUNCTION_TRACER` and `GENERIC_TRACER` unset; without them BPF LSM cannot attach.
- **Cost.** Every traceable function starts with a patchable nop, and ftrace records every call
  site: 56130 in 220 pages on the realtime x86_64 kernel, whose `vmlinuz` grows by 3.2 percent.

### 6.2 Checking an attach

```bash
# Functions that currently have a BPF trampoline attached: the "D" flag and "direct-->bpf_trampoline"
sudo cat /sys/kernel/tracing/enabled_functions

# Loaded LSM and tracing programs
sudo bpftool prog show
```

Before a program attaches, `enabled_functions` is empty. On the realtime x86_64 build of this
change, an LSM program on `file_open` and an `fentry` program on `security_file_open` attach and
run under QEMU, and the file lists both with `direct-->bpf_trampoline_...`; the released
`7.2.8-lusoris1-realtime` refuses both attaches with `-EBUSY`.
