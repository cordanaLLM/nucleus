# Documentation Portal Structure & Architecture Reference

> Layer 3 reference for `/regen-docs`. Defines documentation layout, navigation topology, and rendering guidelines.

---

## 1. Documentation Taxonomy (`docs/`)

```
docs/
├── index.md                 # Landing page and portal introduction
├── principles.md            # Foundational engineering and packaging contracts
├── onboarding.md            # Operator and agent onboarding guide
├── streams.md               # Active kernel stream definitions and lifecycle
├── patches.md               # Curated patch queues and series rationale
├── packaging.md             # Native Debian and UKI binary packaging
├── hardware/                # Silicon enablement
│   ├── architectures.md     # x86_64, arm64, riscv64 strategy
│   ├── amd-rocm.md          # ROCm 10 and Mesa RADV
│   ├── intel-xe2.md         # Battlemage Xe2 and Level Zero
│   └── nvidia-blackwell.md  # Blackwell B200 / RTX 5090 & CUDA
├── security/                # Security baselines
│   ├── kspp-baseline.md     # KSPP defense-in-depth flags
│   ├── cis-l2.md            # CIS Linux Benchmark Level 2
│   └── ebpf-sched-ext.md    # eBPF verifier containment
├── adr/                     # Architectural Decision Records
│   ├── README.md            # ADR index and template
│   └── 0001-...             # Numbered decision records
└── community/               # Community governance
    ├── code-of-conduct.md   # Contributor Covenant
    ├── contributing.md      # Contribution workflow
    ├── governance.md        # Governance model
    └── security.md          # Vulnerability disclosure policy
```

---

## 2. Mermaid Diagram Requirements

- Use fenced code blocks: ```` ```mermaid ````.
- Primary styles: `flowchart TD`, `sequenceDiagram`, `stateDiagram-v2`.
- Quote labels with brackets: e.g. `step["Build Package (.deb)"]`.
- Ensure dark/light palette readability with neutral contrast.

---

## 3. Strict Mode Quality Standards

- No unanchored internal links.
- Use root-relative or document-relative paths (`../principles.md`).
- Every page must begin with an ATX `# Title` heading.
