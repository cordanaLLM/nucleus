# Project Governance & Technical Stewardship

> Governance framework, decision-making processes, maintainer responsibilities, and Architecture Decision Record (ADR) contracts for `lusoris-kernel-forge`.

---

## 1. Governance Overview

`lusoris-kernel-forge` is an open-source engineering project providing foundational infrastructure to `lusoris-cloud-images` and the wider Lusoris ecosystem. This document outlines how architectural decisions are reached, how maintainership is earned, and how release channels are governed.

---

## 2. Decision Making & Architectural Decision Records (ADRs)

To prevent architectural drift and preserve institutional knowledge across human engineers and autonomous AI coding agents:

1. **Routine Changes**: Bug fixes, dependency updates, and minor kconfig adjustments are submitted as standard pull requests and require approval by at least one Maintainer.
2. **Substantial Architectural Shifts**: Any of the following require a formal **Architecture Decision Record (ADR)** under `docs/adr/`:
   - Adding or deprecating a kernel release stream.
   - Introducing new CPU architecture cross-compilation targets.
   - Modifying core packaging formats (e.g. UKI specifications or APT repository metadata).
   - Changing security baseline invariants (KSPP or CIS Level 2).
   - Altering the downstream dispatch protocol to `lusoris-cloud-images`.
3. **ADR Immutability**: Once an ADR is merged with status `Accepted`, its contents become immutable history. Any future directional pivot must be documented in a new, superseding ADR referencing the original.

---

## 3. Maintainer Roles & Responsibilities

| Role | Responsibilities | Rights |
| :--- | :--- | :--- |
| **Maintainer** | Triaging issues, reviewing PRs, maintaining CI/CD runners, managing signing keys, publishing official kernel releases | Direct merge rights on `main`, release signing authority, repository settings administration |
| **Contributor** | Submitting code patches, kconfig improvements, test additions, documenting bugs, refining guides | Branch creation, PR submission, community discussion |

### Becoming a Maintainer:
Contributors who consistently demonstrate:
1. Strict adherence to code quality gates (NASA/JPL Power of 10, clean ShellCheck, test coverage).
2. Deep understanding of Linux kernel configuration and packaging contracts.
3. Constructive, collaborative code reviews across multiple release cycles.

may be nominated and confirmed by consensus of the existing Maintainers.

---

## 4. Release Cadence & Upstream Tracking

- **Longterm (LTS)**: Follows the stable cadence of kernel.org longterm branches (6.18.y), updated on a bi-weekly schedule or immediately upon critical security disclosures.
- **Mainstream (Stable)**: Tracks active stable trees (7.2.y), updated weekly following upstream point releases.
- **Bleeding (Mainline)**: Rebuilt against release candidates (7.3-rc) within 24 hours of upstream tagging.
- **Realtime (RT)**: Synchronized with the Linux Foundation PREEMPT_RT patch sets.
