# ADR-0012: SLSA Build Level 3 via Reusable Release Build Workflow

Date: 2026-10-16

## Status

Proposed (2026-10-16)

This record proposes upgrading the release pipeline to SLSA Build Level 3 compliance by separating release compilation, provenance attestation, and signing into an isolated reusable GitHub Actions workflow (`release-build.yml`), called by the entrypoint `publish-release.yml`.

---

## Context

Prior to this decision, `.github/workflows/publish-release.yml` executed the entire kernel release lifecycle within a single workflow: preflight authorization checks, compilation and boot smoke tests, artifact gating, SBOM generation, keyless cosign signing over `SHA256SUMS`, and asset upload.

While the build container ran with least privileges and checkout mounted read-only, the workflow lacked formal SLSA Build Level 3 isolation:

1. No reusable workflow boundary separated build execution from caller authority.
2. Build provenance was not cryptographically attested via an immutable SLSA v1.0 attestation predicate (`actions/attest-build-provenance`).
3. The cosign signer identity was tied directly to `publish-release.yml`, preventing dual-verification with cryptographic isolation.
4. Downstream consumers and repository gates had to record a HISS-11 exception for `publish-release.yml`.

To reach SLSA Build Level 3, GitHub Actions requires:

- An isolated, reusable workflow (`release-build.yml`) triggered via `workflow_call`.
- Build provenance attested inside the build job before artifacts are handed over to any publishing or distribution step.
- The build job must generate and attest artifacts without downloading or restoring unverified blobs from external caches or caller inputs.
- Verification tooling must accept the legacy identity for historical releases while enforcing the new reusable workflow identity for all subsequent releases.

---

## Decision

1. **Reusable Build Workflow (`release-build.yml`)**:
   Implement `.github/workflows/release-build.yml` with `on: workflow_call`, top-level `permissions: contents: read`, and a dedicated `build` job with `permissions: contents: read, actions: read, id-token: write, attestations: write`. Move kernel resolution, container build, QEMU boot test, artifact gating, SBOM generation, SLSA provenance attestation, and cosign signing into this job.

2. **In-Job SLSA v1.0 Build Provenance Attestation**:
   After `SHA256SUMS` is computed over publishable packages, kernel image, configuration, and SBOMs, invoke `actions/attest-build-provenance@v4` with `subject-path` matching all publishable release files. Copy the resulting attestation bundle to `output/kernel-<stream>.provenance.sigstore.json`. The provenance bundle is published as a release asset but excluded from `SHA256SUMS`, mirroring the manifest contract.

3. **In-Job Cosign Signing and Verification**:
   Cosign signs `SHA256SUMS` with keyless OIDC credentials and immediately verifies the signature in-job against the new identity:
   `https://github.com/${GITHUB_REPOSITORY}/.github/workflows/release-build.yml@${GITHUB_REF}`.

4. **Caller Workflow Separation (`publish-release.yml`)**:
   `publish-release.yml` retains its name and triggers, acting solely as an orchestration caller:
   - `preflight` job: verifies the `KERNEL_FORGE_TOKEN` dispatch secret with `contents: read`.
   - `build` job: calls `release-build.yml` with OIDC and attestation write permissions.
   - `publish` job: runs with `contents: write` ONLY (no `id-token` or `attestations` permissions), downloads verified build artifacts, publishes them to GitHub Releases, and dispatches downstream notification to `cordanaLLM/imago`.

5. **Dual Signer Identity Verification (`release-signers.json`)**:
   Record the 20 legacy release tags in `release-signers.json`. Implement `scripts/release_signer_identity.py` to resolve the expected identity: legacy workflow for the 20 historical tags, reusable workflow for all subsequent tags. Wire this resolver into `scripts/verify-release.sh`, `.github/workflows/package-lifecycle.yml`, and `.github/workflows/openzfs-module.yml`.

6. **HISS-11 Compliance**:
   Remove the HISS-11 exception for `publish-release.yml` from `.standards.yaml`.

---

## Consequences

### Positive

- Reaches full SLSA Build Level 3 provenance for all published kernel releases.
- Publishing jobs no longer hold `id-token: write` authority, preventing credential escalation during asset upload.
- Full backwards compatibility with all 20 historical releases is preserved without pattern wildcards or security downgrades.
- Attestation bundles can be verified by adopters via standard tooling: `gh attestation verify <file> -R cordanaLLM/nucleus`.

### Neutral

- An additional asset (`kernel-<stream>.provenance.sigstore.json`) is published with each release, bringing total release assets from 10 to 11.
- Release verification scripts require `release_signer_identity.py` and `release-signers.json`.
