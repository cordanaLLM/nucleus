# Security Policy & Audit Ledger

> Authoritative security governance, vulnerability reporting protocols, cryptographic attestation, and continuous audit ledger for `cordanaLLM/nucleus`.

---

## 1. Supported Releases & Security Maintenance

`cordanaLLM/nucleus` actively patches, tests, and backports security advisories for the following release channels:

| Stream Channel | Tracked Upstream Version | Security Maintenance Status | Update Frequency |
| :--- | :--- | :--- | :--- |
| **`bleeding`** | Linux `7.3-rc7` / Mainline | Active (Day-0 fixes for next-gen silicon) | Weekly / On-demand |
| **`mainstream`** | Linux `7.2.10` / Stable | Active (Full production support) | Weekly point releases |
| **`lts`** | Linux `6.18.56` / Longterm | Active (Enterprise extended security) | Bi-weekly / Immediate on CVE |
| **`realtime`** | Linux `7.2.10` / in-tree PREEMPT_RT | Active (Deterministic low-latency) | With the `mainstream` stable release |

Older point releases are deprecated as soon as superseded by the next version declared in [`versions.json`](versions.json).

---

## 2. Reporting a Vulnerability

We treat all security vulnerability disclosures with utmost urgency and confidentiality:

1. **Private GitHub Security Advisory**:
   - Navigate to [Report a Vulnerability](https://github.com/cordanaLLM/nucleus/security/advisories/new).
   - Provide a clear vulnerability description, affected kernel streams, proof-of-concept if available, and any relevant kconfig flags.
2. **Never File Public Issues**: Do not submit vulnerability reports, zero-day disclosures, or exploit PoCs to public GitHub Issues or PRs.

### Response SLA

- **Initial Acknowledgement**: Within 24 hours.
- **Triage & Reproduction**: Within 72 hours.
- **Remediation & Coordinated Disclosure**: Private fix developed, tested against QEMU boot test suites, and published with a CVE and GitHub Security Advisory (GHSA).

---

## 3. Cryptographic Supply Chain & Build Attestation

A published release carries a keyless Cosign signature over its checksum file and an SLSA Build Level 3 build provenance attestation. This section states what that signature covers, how to verify it, what is checked before signing, and what is not provided:

1. **Release Checksums and Signatures**:
   - Every release runs `scripts/publish_release.sh` in the reusable `.github/workflows/release-build.yml` workflow called by `.github/workflows/publish-release.yml` to generate `SHA256SUMS` over all publishable assets in the release directory.
   - The reusable workflow attests SLSA Build Level 3 build provenance (`actions/attest-build-provenance`) in the build job, saving the attestation as `kernel-<stream>.provenance.sigstore.json`.
   - The workflow signs `SHA256SUMS` using keyless Cosign (`cosign sign-blob`) and publishes the signature as `SHA256SUMS.bundle`.
   - The signed `SHA256SUMS` file covers the Debian packages (`*.deb`), the extracted kernel image (`vmlinuz-<kernelrelease>`), the resolved kernel configuration (`kernel-<stream>-x86_64.config`), and the Syft SBOM files (`kernel-<stream>.cdx.json` and `kernel-<stream>.spdx.json`).
   - The signature does not cover `kernel-<stream>.manifest.json` or `kernel-<stream>.provenance.sigstore.json`, which the workflow writes after `SHA256SUMS` is signed. The manifest is unsigned; a consumer checks the digests it carries against the signed `SHA256SUMS`.
   - `.github/workflows/supply-chain.yml` builds and signs nothing. After every successful `publish-release` run, and on dispatch for a kernel release tag, it downloads the release and runs `scripts/verify-release.sh` over it, the checks of the next section (`tests/test_supply_chain_workflow.py`). It does not run on the release event: the release workflow creates the release with `GITHUB_TOKEN`, and GitHub starts no workflow on an event that token raised.
   - Unified Kernel Images (`.efi`) are not signed yet. `sbsign` Secure Boot signing is not wired into `scripts/package-uki.sh`, and no release workflow publishes a UKI, so the release **Cosign** signature over `SHA256SUMS` covers no UKI. Both are planned.

2. **Verifying a Release**:
   - Download the release assets into one directory, verify the Cosign signature over `SHA256SUMS` against the identity of the release workflow at that tag, then check the files against it:

     ```bash
     cosign verify-blob \
       --bundle SHA256SUMS.bundle \
       --certificate-identity "https://github.com/cordanaLLM/nucleus/.github/workflows/release-build.yml@refs/tags/<release-tag>" \
       --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
       SHA256SUMS
     sha256sum -c SHA256SUMS
     ```

   - Releases following the SLSA Build Level 3 transition are built and signed inside `.github/workflows/release-build.yml`, so the expected certificate identity is `https://github.com/cordanaLLM/nucleus/.github/workflows/release-build.yml@refs/tags/<release-tag>`.
   - The 20 legacy tags listed in `release-signers.json` were built directly by `.github/workflows/publish-release.yml`, so their expected certificate identity is `https://github.com/cordanaLLM/nucleus/.github/workflows/publish-release.yml@refs/tags/<release-tag>`.
   - The script `scripts/release_signer_identity.py --repo cordanaLLM/nucleus --tag <release-tag>` evaluates `release-signers.json` and prints the exact identity for any given tag.
   - `cosign verify-blob` refuses a `SHA256SUMS` with one changed byte and a certificate from any other workflow.
   - Verify SLSA Build Level 3 provenance for published release assets using GitHub CLI:

     ```bash
     gh attestation verify <file> -R cordanaLLM/nucleus
     ```

   - `scripts/verify-release.sh <asset-dir> <release-tag> cordanaLLM/nucleus` runs both commands, and also refuses an asset that `SHA256SUMS` does not list and a manifest whose tag, signer identity or `SHA256SUMS` digest is not the verified one. It needs `cosign`, `sha256sum` and `jq`.
   - `cordanaLLM/imago` consumes `kernel-<stream>.manifest.json` (`imago.nucleus.kernel-artifact.v1`) and verifies `SHA256SUMS.bundle`, the `SHA256SUMS` digest, and every artifact digest before an image build uses a kernel.

3. **Checks Before Signing**:
   - **Upstream Source Verification (`scripts/fetch-kernel-source.sh`)**: Verifies kernel tarball checksums and OpenPGP signatures against keys in `keys/`, or verifies git tags and commit hashes using `git verify-tag`.
   - **Fixed Build Environment (`scripts/build_kernel.sh`)**: Sets `SOURCE_DATE_EPOCH` and `KBUILD_BUILD_TIMESTAMP` from the release commit timestamp of the kernel `Makefile`, sets `KBUILD_BUILD_USER=nucleus` and `KBUILD_BUILD_HOST=forge`, and sets `LC_ALL=C` and `TZ=UTC`.
   - **Artifact Gate (`scripts/check_kernel_artifacts.py`)**: Opens every package with `dpkg-deb` and refuses an empty or unexpected file, a wrong package name, version or Debian architecture, a missing image, headers or `linux-libc-dev` package, a `vmlinuz` or configuration that differs from the one inside the image package, and a `vmlinuz` whose bzImage setup header does not name the stream's kernel release.
   - **Boot Smoke Test (`scripts/boot_smoke.py`)**: Boots the x86_64 kernel with a static-init initramfs under QEMU (TCG) and requires the `Linux version <kernelrelease>` banner and the init's line before anything is checksummed.

4. **Software Bill of Materials (SBOM)**:
   - Every release generates CycloneDX (`kernel-<stream>.cdx.json`) and SPDX (`kernel-<stream>.spdx.json`) files via Syft (`syft dir:output/`).
   - The SBOM inventories the files in the release `output/` directory. It does not index compiled source objects, firmware blobs, or C header source trees.

5. **Not Provided Today**:
   - **No GPG-Signed APT Archive**: Debian packages are published as GitHub Release assets; no APT repository or signed `InRelease` metadata is maintained.
   - **Releases Not Rebuildable Byte for Byte**: Two builds of one tag differ by their module signing key. The build signs nothing; `scripts/sign_kernel_modules.py` signs every module after packaging with a key that `--module-key` supplies or that is generated for that build and deleted after it ([ADR-0016](https://github.com/cordanaLLM/nucleus/blob/main/docs/adr/0016-module-signing-after-the-build.md)). No stable release key exists, so released packages carry a per-build key. The `Reproducible Build (mainstream / x86_64)` job in `.github/workflows/build-matrix.yml` builds twice with one key and fails unless the outputs are byte-identical, but a release's key is deleted after the build, so a released package cannot be rebuilt to its published digest.
   - **No UKI Signing**: Unified Kernel Images (`.efi`) are not signed with Secure Boot keys, and no release workflow publishes UKI binaries.
   - **x86_64 Releases Only**: arm64 and riscv64 kernels are compiled and pass the artifact gate in `.github/workflows/build-matrix.yml`, but they are not booted and not released; `imago.nucleus.kernel-artifact.v1` has no architecture field.

---

## 4. Continuous Automated Security Scans

The repository runs the following security checks. Semgrep and Trivy run on every push to `main` and on a weekly schedule, and on a pull request when it changes code, build configuration, tests or workflows (`.github/workflows/security-scans.yml`):

- **Zero-Leak Invariant Test (`tests/test_security_privacy.py`)**: Asserts zero private RFC 1918 addresses (`10.x`, `192.168.x`, `172.16-31.x`) and zero workstation paths (`/home/...`) exist in any file git tracks (`git ls-files`). A tracked file it cannot read fails the test instead of being skipped, and planted cases in a throwaway repository prove that each pattern is reported.
- **Semgrep SAST**: Scans shell scripts, Python tests, and GitHub Actions workflows for dangerous shell constructs, credential leaks, and injection vulnerabilities.
- **Trivy Scanner**: Scans the repository filesystem for known high- and critical-severity CVEs.
- **OpenSSF Scorecard**: Audits repository posture on a weekly schedule and on push to `main` for read-only workflow permissions, branch protection, and pinned GitHub Actions commit hashes.
