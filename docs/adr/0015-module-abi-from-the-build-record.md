# ADR-0015: Module ABI Verification from the Kernel Build Record

Date: 2026-10-16

## Status

Proposed (2026-10-16)

Supersedes only the module-abi sentence of [ADR-0007](0007-document-driven-kernel-requirements.md) ("`abi.module-abi` names the release of a built kernel and cannot be verified before one exists (issue #18), so a document that sets it fails, saying so."). The rest of ADR-0007 stands.

---

## Context

Downstream consumer requirement documents (`aegis.p01-nucleus.kernel-requirement.v1`) declare architectural, configuration, and binary constraints for kernels published by this forge.

In the contract owner crate `cordanaLLM/Aegis-OS` (`crates/aegis-fabrica-defs/src/kernel.rs`, `unmet_identity`), module ABI verification is defined as exact string equality between `abi.module-abi` and the compiled kernel profile's release string (`Unmet::AbiMismatch { observed, required }`). The module ABI of a kernel is its measured `make -s kernelrelease`.

When requirement verification was introduced in [ADR-0007](0007-document-driven-kernel-requirements.md), `cordanaLLM/nucleus` had not yet established automated kernel builds (issue #18 was open). Consequently, `abi.module-abi` could not be verified against a compiled kernel, and `scripts/verify_kernel_requirement.py` failed closed whenever a requirement document set `abi.module-abi`.

Following [ADR-0009](0009-kernel-compilation-and-artifact-gate.md) and the resolution of issue #18, `scripts/build_kernel.sh` compiles production Debian packages and writes a build record `<work-dir>/<stream>-<arch>/build.json` capturing the stream, architecture, measured `kernelrelease`, and configuration digest. However, `scripts/verify_kernel_requirement.py` lacked a mechanism to ingest build records, leaving `abi.module-abi` permanently unverifiable.

---

## Decision

1. **Build Records Capture Module Symbols and Digest**:
   In `scripts/build_kernel.sh`, each build records `module_symvers_sha256` (the SHA-256 digest of `${KBUILD_DIR}/Module.symvers`) and `module_symbols` (the line count of `Module.symvers`) in `build.json`. The build script verifies `${KBUILD_DIR}/Module.symvers` and refuses (`refuse`) if the file is missing or empty after compilation.

2. **Ingestion via `--build-record` Option**:
   `scripts/verify_kernel_requirement.py` provides a `--build-record STREAM:ARCH=PATH` command-line option. The verifier parses each referenced `build.json` and refuses (`parser.error`) if the record's recorded `stream` or `arch` differs from the option key, or if `kernelrelease` is missing.

3. **Evaluation Semantics in `decide()`**:
   When `abi.module-abi` is present in a requirement document, `decide()` evaluates it for every bound stream and every listed architecture:
   - **Missing Build Record**: If no build record is provided for a bound `(stream, arch)` pair, verification fails with reason:
     `<cid>: abi.module-abi <x>: no build record for <stream> on <arch>; it cannot be verified`
   - **Mismatched Kernel Release**: If the build record's `kernelrelease` does not equal `abi.module-abi`, verification fails with reason:
     `<cid>: abi.module-abi <x> does not match <stream> on <arch>: observed kernel release <y>`
   - **Matching Kernel Release**: If `kernelrelease` matches `abi.module-abi`, no failure is recorded for the module ABI check.
   - **Held Streams**: Bound streams that satisfy release floor requirements, all feature checks, and the module ABI check are recorded in `held`.
   - **Unset `module-abi`**: When `abi.module-abi` is null, build records are not required, and behavior remains unchanged.

4. **Architecture Token Mapping**:
   Requirement documents use `x86-64` hyphenated architecture tokens while `versions.json` uses `x86_64`. The mapping (`ARCHITECTURES`) already used for `--resolved-config` is reused for `--build-record`.

5. **CI Workflow Stance**:
   The `verify-requirements.yml` workflow resolves kernel configurations from source trees but performs no kernel compilation. Because no build records are provided, any document that specifies `abi.module-abi` fails closed in `verify-requirements.yml`, maintaining the invariant that unverifiable claims never pass.

---

## Evidence

The implementation is verified through unit and integration suites:

- `tests/test_verify_kernel_requirement.py`:
  - `test_a_module_abi_without_build_record_fails_naming_stream_and_arch`: verifies failure when `module-abi` is set without a build record.
  - `test_a_module_abi_mismatch_with_build_record_fails_with_both_values`: verifies refusal when `kernelrelease` differs from `module-abi`, reporting both values.
  - `test_a_module_abi_matching_build_record_passes`: verifies passing evaluation when `kernelrelease` matches `module-abi`.
  - `test_main_refuses_a_build_record_with_wrong_stream_key`: verifies CLI argument refusal when a build record does not match the key.
  - `test_main_refuses_a_build_record_without_kernelrelease`: verifies CLI argument refusal when `kernelrelease` is missing.
  - `test_main_evaluates_module_abi_with_build_record`: end-to-end CLI execution verifying a document with `module-abi`.
- `tests/test_build_kernel.py`:
  - `test_record_py_records_module_symvers_sha256_and_symbols`: verifies `RECORD_PY` records `module_symvers_sha256` and `module_symbols` in `build.json`.
  - `test_record_py_fails_when_module_symvers_missing`: verifies `RECORD_PY` fails when `Module.symvers` does not exist.

---

## Consequences

### Positive

- **Contract Conformance**: Accurately implements the exact matching semantics defined in `crates/aegis-fabrica-defs/src/kernel.rs` (`unmet_identity`).
- **Cryptographic & Symbol Traceability**: Captures `Module.symvers` digest and line count in the build record, facilitating downstream driver and module compilation verification.
- **Fail-Closed Safety**: Documents specifying `abi.module-abi` cannot pass without a verified build record.

### Negative

- **Requires Compilation**: Verifying `abi.module-abi` requires compiling the target kernel, which cannot be evaluated during fast configuration-only sweeps.

### Neutral

- **Unbound Streams**: Unbound streams continue to be reported as informational and never gate.
- **Unset Module ABI**: Documents omitting `abi.module-abi` require no build records.

---

## Compliance

Ongoing compliance is enforced by:

- `tests/test_verify_kernel_requirement.py`
- `tests/test_build_kernel.py`
- Repository governance quality gate (`make verify-all`)
