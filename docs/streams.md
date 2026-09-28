# Kernel Release Streams

> Comprehensive specifications and workload mappings for all 4 kernel release streams in `cordanaLLM/nucleus`.

---

## 1. Stream Architecture

```
                                upstream: kernel.org
                                         │
        ┌───────────────────┬────────────┴───────┬───────────────────┐
        ▼                   ▼                    ▼                   ▼
    bleeding            mainstream              lts               realtime
  (Linux 7.3-rc2)      (Linux 7.2.4)       (Linux 6.18.50)      (Linux 7.2-rt)
        │                   │                    │                   │
        ▼                   ▼                    ▼                   ▼
  Blackwell B200       Intel Xe2 Arc        Enterprise K8s      Game Servers
  RTX 5090 / CXL 3.0   AMD ROCm 10          OpenZFS 2.3 kmod    Low-latency UDP
  sched-ext eBPF       NVIDIA 565/610       CloudNativePG       WireGuard Gateway
```

---

## 2. Stream Specifications

### 2.1 Bleeding Stream (`bleeding`)
- **Upstream Release**: Linux 7.3-rc2 (Mainline)
- **Target Hardware**: NVIDIA Blackwell RTX 5090 and B200 accelerators, PCIe 6.0 / CXL 3.0 interconnects.
- **Key Features**: sched-ext user-space eBPF scheduler support, cutting-edge DRMs, prototype memory tiering.
- **Flavors**: `ai-infer-nvidia-bleeding`, `k8s-node-nvidia-bleeding`, `docker-nvidia-bleeding`.

### 2.2 Mainstream Stream (`mainstream`)
- **Upstream Release**: Linux 7.2.4 (Stable)
- **Target Hardware**: Intel Arc Battlemage Xe2, AMD Radeon RX 7000/8000 (ROCm 10), NVIDIA Ada/Ampere (565/610).
- **Key Features**: BBRv3 congestion control, VirtIO `mq-deadline`, Intel Level Zero compute.
- **Flavors**: `base-*`, `docker-*`, `podman-*`, `ai-infer-*`.

### 2.3 Long-Term Support Stream (`lts`)
- **Upstream Release**: Linux 6.18.50 (Longterm)
- **Target Workloads**: Enterprise Kubernetes nodes, OpenZFS 2.3.x root filesystems, PostgreSQL / CNPG databases.
- **Key Features**: Rock-solid stability, proven OpenZFS kernel module API stability, strict memory overcommit controls.
- **Flavors**: `k8s-node-*`, `cloudnative-storage`, `cloudnative-pg`.

### 2.4 Realtime Stream (`realtime`)
- **Upstream Release**: Linux 7.2-rt (PREEMPT_RT)
- **Target Workloads**: Low-jitter gaming servers (Counter-Strike 2, dedicated game hosts), software-defined radio, audio processing.
- **Key Features**: Deterministic interrupt handling, high-resolution timers, threaded IRQs.
- **Kconfig**: `kconfig/streams/realtime.config` sets `PREEMPT_RT`, `HZ_1000` and `EXPERT` (section 3).
- **Flavors**: `appliance-game-server`, `appliance-gateway-dns`.

---

## 3. Stream Fragments

`scripts/merge-config.sh --stream=<stream>` layers `kconfig/streams/<stream>.config` after
`kconfig/security-hardened.config` and the architecture fragment. The last line for a symbol
wins, and a `# CONFIG_X is not set` line in a later fragment unsets an earlier assignment. A
stream without a fragment builds the architecture configuration unchanged.

| Stream | Fragment | What it declares |
| :--- | :--- | :--- |
| `realtime` | `kconfig/streams/realtime.config` | `CONFIG_PREEMPT_RT=y` and `CONFIG_HZ_1000=y`, which Aegis-OS asks for together (`REQ-P07-01`), and `CONFIG_EXPERT=y`, because `PREEMPT_RT` depends on `EXPERT && ARCH_SUPPORTS_RT` (`kernel/Kconfig.preempt`); x86, arm64 and riscv select `ARCH_SUPPORTS_RT` |
| `bleeding`, `mainstream`, `lts` | none | nothing beyond the architecture fragment |

```bash
# Merge x86_64 with the realtime layer and check the security baseline, without writing a file
make merge-config ARCH=x86_64 STREAM=realtime DRY_RUN=true
```

---

## 4. Consumer Bindings

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
and never gate. The policy and the contract are [ADR-0007](adr/0007-document-driven-kernel-requirements.md).
