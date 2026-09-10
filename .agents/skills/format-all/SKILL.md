---
name: format-all
description: Automated code, shell script, YAML, and Markdown formatting across the repository.
references:
  - references/format-guidelines.md
---

# /format-all — Code & Document Formatting Skill

Enforce consistent formatting across shell scripts, Python test suites, JSON manifests, and Markdown documentation.

## Usage

```bash
# 1. Format code across the repository
make fmt

# 2. Verify formatting without modifying files
make fmt-check
```

## Step-by-Step Procedure

1. **Shell Scripts Formatting**:
   - Check or format all shell scripts in `scripts/` using `shfmt` with 2-space indentation and binary operators at start of line (`shfmt -i 2 -bn -ci`).

2. **Python Code Formatting**:
   - Format test suites in `tests/` and scripts in `.agents/hooks-scripts/` using `ruff format` or `black` (line length 100).

3. **JSON & Schema Formatting**:
   - Format `versions.json` and schema files with 2-space indentation.

4. **Markdown Documents**:
   - Ensure trailing newlines, normalize list bullets, and ensure fenced code blocks specify language identifiers.

5. **Format Validation (`fmt-check`)**:
   - Verify `git diff --exit-code` to confirm that all tracked files comply with formatting standards without uncommitted diffs.

## Progressive Disclosure & Reference

For complete language style conventions, tab widths, and editor configurations, consult:
- [`references/format-guidelines.md`](references/format-guidelines.md)

## Invariants to Preserve
1. **Deterministic Output**: Formatting tools must produce consistent, reproducible output.
2. **License Headers**: Formatting must preserve the Apache 2.0 / `Copyright 2026 The Lusoris Authors` header at line 1-2.
3. **No Hidden Trailing Spaces**: Strip trailing whitespace from all lines.
