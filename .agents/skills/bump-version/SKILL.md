---
name: bump-version
description: Coordinated kernel version bump procedure across versions.json, schema validation, and documentation.
references:
  - references/version-bump-checklist.md
---

# /bump-version — Version Bump Skill

Execute coordinated kernel stream version bumps across Single Source of Truth (`versions.json`), schema validators, and documentation portal.

## Usage

```bash
# 1. Validate manifest before editing
make lint-manifest

# 2. Update stream in versions.json (manual or automated)
# e.g. update mainstream to new upstream release tag

# 3. Validate updated manifest against schema
make lint-manifest

# 4. Run test suite to verify synchronization
make test

# 5. Build documentation strictly to ensure docs parity
make docs-build
```

## Step-by-Step Procedure

1. **Upstream Verification**:
   - Query `https://www.kernel.org/releases.json` or authoritative git tags on `git.kernel.org`.
   - Confirm official release tag, tarball URL, and checksums.

2. **Update Manifest (`versions.json`)**:
   - Update `version`, `tag`, and `tarball_url` under target stream.
   - Update `metadata.updated` timestamp to current date (`YYYY-MM-DD`).

3. **Schema & Test Validation**:
   - Run `make lint-manifest` to validate against `versions.schema.json`.
   - Run `pytest tests/test_manifest.py` to ensure stream properties are valid.

4. **Synchronize Documentation**:
   - Update `docs/streams.md` with new version details, changelog highlights, and upstream status.
   - Update stream table in `README.md` in same commit.

5. **Commit & Branch**:
   - Commit following Conventional Commits: `chore(versions): bump <stream> kernel to <version>`.

## Progressive Disclosure & Reference

Full version bump verification checklist:

- [`references/version-bump-checklist.md`](references/version-bump-checklist.md)

## Invariants to Preserve

1. **SSOT Rule**: Versions must be changed ONLY in `versions.json`. Never hardcode version strings in build scripts.
2. **Docs Synchrony**: Never bump version without updating `docs/streams.md` and `README.md` in same commit.
3. **Quality Gates**: All tests must remain green before opening a pull request.
