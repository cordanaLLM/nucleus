# ADR-0006: OCI Registry Namespace for UKI Artifacts

Date: 2026-09-29

## Status

Proposed

Supersedes the OCI destination of [ADR-0004](0004-native-debian-and-uki-dual-packaging.md), `ghcr.io/lusoris/kernels`, in its decision diagram and in section 2 of its decision. The rest of ADR-0004 stands.

---

## Context

ADR-0004 publishes signed UKI binaries as OCI artifacts to `ghcr.io/lusoris/kernels`. That path was written before this repository moved to `cordanaLLM/nucleus`. `lusoris` is the maintainer's user account, not the organization that owns this repository, so the first push to that path would publish the forge's artifacts outside the organization, under a name the release does not carry anywhere else:

- `publish-release.yml` signs `SHA256SUMS` as `https://github.com/cordanaLLM/nucleus/.github/workflows/publish-release.yml@<tag ref>`.
- The artifact manifest names `cordanaLLM/nucleus` as its provider and provenance repository ([Packaging](../packaging.md), section 5.3).

Issue #28 lists this path together with the other identities left over from before the move.

ADR-0004 is Accepted, and an Accepted record is immutable ([ADR index](README.md)): a changed decision is recorded by a new ADR that supersedes it. The replacement is also a choice rather than a mechanical rename, because more than one path under the organization is valid:

- OCI repository names are lowercase (the `<name>` grammar of the [OCI Distribution Specification](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)), so the organization appears in every registry path as `cordanallm`.
- A repository name may have several path segments below the owner; the existing layout `<registry path>/<stream>-<arch>` already relies on that. GHCR holds repositories three segments below the owner, the depth the decision below needs: `ghcr.io/homebrew/core/openssl/3` lists its tags to an anonymous client.
- The sibling repository `cordanaLLM/praetor` names its image repository after its repository identity in lowercase, `ghcr.io/cordanallm/praetor` (decision 5 of its ADR-0013).

No workflow pushes to a registry today. `publish-release.yml` publishes GitHub Release assets, and `oras push` appears only as an example in [Packaging](../packaging.md), section 5.2. The path is decided now so that the first workflow that pushes has a path to use.

---

## Decision

UKI OCI artifacts are published under `ghcr.io/cordanallm/nucleus/kernels`, one OCI repository per stream and architecture, tagged with the package version: `ghcr.io/cordanallm/nucleus/kernels/<stream>-<arch>:<version>`, for example `ghcr.io/cordanallm/nucleus/kernels/mainstream-x86_64:7.2.4-lusoris1`.

1. **Prefix**: `ghcr.io/cordanallm/nucleus` is the repository identity `cordanaLLM/nucleus` in lowercase. A consumer derives it from the identity it already verifies, the cosign signer and the manifest's `provenance.repository`, without a lookup table. Praetor's container image follows the same rule; its Helm chart goes to the organization-wide `ghcr.io/cordanallm/charts` (decision 3 of its ADR-0013).
2. **Layout below the prefix**: the `kernels` segment and the `<stream>-<arch>:<version>` layout are kept from ADR-0004 and [Packaging](../packaging.md), so only the prefix changes.
3. **Other OCI outputs**: anything else this repository pushes to a registry, such as the builder container defined in `docker/Dockerfile.builder`, goes under the same prefix. That container's `org.opencontainers.image.source` label names `https://github.com/cordanaLLM/nucleus`, the label GHCR reads to connect a package to its repository.

### Alternatives considered

- **`ghcr.io/cordanallm/kernels`**: the smallest edit to the path in ADR-0004. Rejected because the name does not say which repository produced the artifact, and any repository in the organization could publish a package called `kernels`.
- **Keep `ghcr.io/lusoris/kernels`**: rejected, because it publishes the organization's artifacts under a user account.

---

## Consequences

### Positive

- The registry path, the release signer and the artifact manifest name the same producer, `cordanaLLM/nucleus`.
- Every artifact reference names the repository that produced it.

### Negative

- References are one segment longer than with `ghcr.io/cordanallm/kernels`.
- The repository name is part of every artifact reference: renaming the repository again changes the registry path, and consumers have to follow it.
- Nothing has been pushed under `ghcr.io/cordanallm/nucleus` yet. The anonymous listing in Context shows that GHCR holds names at this depth, not that a workflow in this repository may create packages under the organization; the first workflow that pushes has to show that.

---

## Compliance

Enforced via automated test gates:

1. **Identity Scan (`tests/test_identity.py`)**: fails when a tracked file, workflows included, names a pre-move identity (`ghcr.io/lusoris`, `github.com/lusoris/`, `lusoris.github.io` or `lusoris-kernel-forge`) outside the historical records: `CHANGELOG.md`, ADR-0004, this ADR and the test itself.
2. **Canonical Identities (`tests/test_identity.py`)**: asserts that `docs/packaging.md` and this ADR name `ghcr.io/cordanallm/nucleus/kernels`, and that the setup script's owner, the `mkdocs.yml` site URL and the builder container's source label name `cordanaLLM/nucleus`.
