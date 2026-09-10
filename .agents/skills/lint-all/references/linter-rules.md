# Static Analysis & Linter Rules Reference

> Layer 3 reference for `/lint-all`. Defines configurations, rules, and suppression policies for repository linters.

---

## 1. ShellCheck Configuration

- **Target Files**: All `scripts/*.sh` and `.agents/hooks-scripts/*.sh`.
- **Dialect**: `bash` (`-s bash`).
- **Severity**: Error on any warning or info diagnostic unless explicitly justified.
- **Key Rules**:
  - `SC2086`: Double quote variables to prevent globbing and word splitting.
  - `SC2155`: Declare and assign separately to avoid masking return values.
  - `SC2164`: Use `cd ... || exit` in subshells.
  - `SC2034`: Variables appearing unused must be prefixed or marked.

---

## 2. Yamllint Configuration (`.yamllint.yml`)

- **Indentation**: 2 spaces, no hard tabs.
- **Line Length**: Max 120 characters (soft limit where practical).
- **Truthy Values**: Enforce `true` / `false` lower-case literals.
- **Comments**: Single space after `#`.

---

## 3. Actionlint Configuration

- **Target Files**: `.github/workflows/*.yml`.
- **Checks**:
  - Validate GitHub context expressions (`${{ github.event ... }}`).
  - Enforce full 40-character commit SHA pins for third-party actions (`actions/checkout@v4` -> pinned SHA).
  - Verify runner environments and shell specifications.

---

## 4. Manifest Validator (`versions.schema.json`)

- Strict draft-07 JSON Schema validation.
- Additional properties forbidden on stream definitions.
- Regular expression checks on semantic versioning strings and URLs.
