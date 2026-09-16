# Security Policy & Audit Ledger

> Authoritative security governance, vulnerability reporting protocols, cryptographic attestation, and continuous audit ledger for `cordanaLLM/nucleus`.

---

## 1. Supported Releases & Security Maintenance

`cordanaLLM/nucleus` actively patches, tests, and backports security advisories for the following release channels:

| Stream Channel | Tracked Upstream Version | Security Maintenance Status | Update Frequency |
| :--- | :--- | :--- | :--- |
| **`bleeding`** | Linux `7.3-rc2` / Mainline | Active (Day-0 fixes for next-gen silicon) | Weekly / On-demand |
| **`mainstream`** | Linux `7.2.4` / Stable | Active (Full production support) | Weekly point releases |
| **`lts`** | Linux `6.18.50` / Longterm | Active (Enterprise extended security) | Bi-weekly / Immediate on CVE |
| **`realtime`** | Linux `7.2-rt` / PREEMPT_RT | Active (Deterministic low-latency) | Monthly / On RT patch release |

Older point releases are deprecated as soon as superseded by the next version declared in [`versions.json`](versions.json).

---

## 2. Reporting a Vulnerability

We treat all security vulnerability disclosures with utmost urgency and confidentiality:

1. **Private GitHub Security Advisory**:
   - Navigate to [Report a Vulnerability](https://github.com/cordanaLLM/nucleus/security/advisories/new).
   - Provide a clear vulnerability description, affected kernel streams, proof-of-concept if available, and any relevant kconfig flags.
2. **Never File Public Issues**: Do not submit vulnerability reports, zero-day disclosures, or exploit PoCs to public GitHub Issues or PRs.

### Response SLA:
- **Initial Acknowledgement**: Within 24 hours.
- **Triage & Reproduction**: Within 72 hours.
- **Remediation & Coordinated Disclosure**: Private fix developed, tested against QEMU boot test suites, and published with a CVE and GitHub Security Advisory (GHSA).

---

## 3. Cryptographic Supply Chain & Build Attestation

To safeguard the kernel supply chain against tampering and compromise:

1. **Cryptographic Package Signing**:
   - Debian packages (`.deb`) and repository metadata (`InRelease`) are cryptographically signed using GPG with 4096-bit RSA keys.
   - Unified Kernel Images (`.efi`) are signed with custom UEFI Secure Boot keys using `sbsign` and attested with **Cosign** keyless signatures via GitHub Actions OIDC.
2. **Software Bill of Materials (SBOM)**:
   - Every release generates both **CycloneDX** (`bom.cdx.json`) and **SPDX** (`bom.spdx.json`) files via Syft, indexing all compiled objects, firmware blobs, and C header sources.
3. **Reproducible Builds**:
   - Builds enforce fixed timestamps (`KBUILD_BUILD_TIMESTAMP`), fixed build users, and module stripping to guarantee byte-for-byte reproducibility across independent builder nodes.
   - Verified continuously via Diffoscope in `.github/workflows/reproducibility.yml`.

---

## 4. Continuous Automated Security Scans

The repository executes mandatory static and dynamic security checks on every pull request:

- **Zero-Leak Invariant Test (`tests/test_security_privacy.py`)**: Asserts zero private RFC 1918 addresses (`10.x`, `192.168.x`, `172.16-31.x`) and zero workstation paths (`/home/...`) exist across the codebase.
- **Semgrep SAST**: Scans shell scripts, Python tests, and GitHub Actions workflows for dangerous shell constructs, credential leaks, and injection vulnerabilities.
- **Trivy Scanner**: Scans all containerized builder environments and downstream package artifacts for known CVEs.
- **OpenSSF Scorecard**: Audits repository posture, requiring strict top-level read-only workflow permissions, branch protection, and pinned GitHub Actions commit hashes.
