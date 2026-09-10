---
name: lint-all
description: Run ShellCheck, Yamllint, Actionlint, and versions manifest validation across the repository.
references:
  - references/linter-rules.md
---

# /lint-all — Repository Linting Skill

Execute comprehensive static analysis, linter checks, and manifest schema validations across all codebase components.

## Usage

```bash
# 1. Run standard repository linter suite
make lint

# 2. Run workflow validation separately
make lint-workflows

# 3. Run manifest schema validation separately
make lint-manifest
```

## Step-by-Step Execution Sequence

1. **Format Check**:
   - Verify shell script formatting and YAML integrity.

2. **Shell Script Analysis (ShellCheck)**:
   - Run `shellcheck -s bash scripts/*.sh`.
   - Ensure zero warnings, zero errors.

3. **CI Workflow Verification (Actionlint)**:
   - Run `actionlint .github/workflows/*.yml` (or via `$HOME/go/bin/actionlint`).
   - Validate GitHub Actions expression syntax, runner labels, and action version pins.

4. **Manifest Schema Validation**:
   - Validate `versions.json` against `versions.schema.json` via `python3` jsonschema.
   - Verify that all streams, tags, tarball URLs, and architectures meet schema constraints.

5. **YAML Linting (Yamllint)**:
   - Validate GitHub Actions and config YAML files against `.yamllint.yml`.

## Progressive Disclosure & Reference

For detailed linter configurations, suppression policies, and error resolution guides, consult:
- [`references/linter-rules.md`](references/linter-rules.md)

## Invariants to Preserve
1. **Zero Warnings Tolerance**: Quality gates must pass cleanly with 0 warnings.
2. **NASA/JPL Power of 10**: All shell scripts must maintain strict error handling (`set -euo pipefail`).
