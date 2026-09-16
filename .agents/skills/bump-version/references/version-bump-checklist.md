# Kernel Version Bump Quality Gate Checklist

> Verification checklist for upgrading kernel stream versions in `cordanaLLM/nucleus`.

---

## 1. Upstream Authority Verification
- [ ] Upstream release is announced on `kernel.org` or LKML.
- [ ] Tarball URL matches official CDN pattern: `https://cdn.kernel.org/pub/linux/kernel/vX.x/linux-X.Y.Z.tar.xz` or official git.kernel.org tag archive.
- [ ] PGP signature validated using kernel.org release keys.

## 2. Manifest Schema & Syntax (`versions.json`)
- [ ] Stream version updated under `.streams.<stream>.version`.
- [ ] Git tag updated under `.streams.<stream>.tag`.
- [ ] Tarball URL updated under `.streams.<stream>.tarball_url`.
- [ ] Metadata date updated under `.metadata.updated` (`YYYY-MM-DD`).
- [ ] Validated with `make lint-manifest` (`jsonschema` validation against `versions.schema.json`).

## 3. Documentation Synchrony (Same Commit)
- [ ] `docs/streams.md` updated with the new version, date, and status.
- [ ] `README.md` matrix updated to match new version numbers.
- [ ] `make docs-build` passes with 0 warnings.

## 4. Test Suite Verification
- [ ] `pytest tests/test_manifest.py -v` passes.
- [ ] `pytest tests/test_scripts.py -v` passes.
- [ ] `pytest tests/test_security_privacy.py -v` passes.
- [ ] `./scripts/audit-repository-health.sh` passes all 7 quality gates.

## 5. Downstream Dispatch Preparation
- [ ] Target repository identified (`cordanaLLM/imago`).
- [ ] Dispatch event validated: `kernel_release_published`.
- [ ] Dry-run notification tested: `./scripts/notify_downstream.sh <stream> <version> true`.
