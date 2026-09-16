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
- **Flavors**: `appliance-game-server`, `appliance-gateway-dns`.
