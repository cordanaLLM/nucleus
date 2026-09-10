---
name: regen-docs
description: Strict MkDocs build validation, local documentation preview, and link verification.
references:
  - references/docs-structure.md
---

# /regen-docs — Documentation Build & Verification Skill

Validate and build the documentation portal using Material for MkDocs in strict mode, ensuring zero dead links and complete navigation parity.

## Usage

```bash
# 1. Build documentation strictly
make docs-build

# 2. Serve documentation locally for live review
make docs-serve
```

## Step-by-Step Procedure

1. **Check Prerequisites**:
   - Ensure `mkdocs` and `mkdocs-material` are installed in the environment.

2. **Validate Navigation References**:
   - Cross-check `mkdocs.yml` `nav:` entries against physical Markdown files under `docs/`.
   - Ensure every linked page exists and has an `H1` title.

3. **Strict Build Execution**:
   - Run `mkdocs build --strict`.
   - In strict mode, MkDocs converts any broken link, missing nav file, or formatting warning into an immediate build failure.

4. **Live Preview Verification**:
   - When previewing locally, execute `mkdocs serve -a 127.0.0.1:8000` to review rendered pages, Mermaid diagrams, and navigation hierarchy.

## Progressive Disclosure & Reference

For the comprehensive documentation site taxonomy, navigation schema, and Mermaid standards, consult:
- [`references/docs-structure.md`](references/docs-structure.md)

## Invariants to Preserve
1. **Zero Broken Links**: `mkdocs build --strict` must terminate with exit code 0.
2. **Language**: All docs written in professional, neutral English.
3. **Zero-Leak Invariant**: Zero private RFC 1918 IPs or developer workstation paths in documentation.
