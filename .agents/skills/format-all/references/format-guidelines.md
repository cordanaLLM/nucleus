# Repository Formatting & Style Guidelines

> Layer 3 reference for `/format-all`. Standard styling conventions for `lusoris-kernel-forge`.

---

## 1. Shell Scripting (`*.sh`)

- **Indent**: 2 spaces (no tabs).
- **Shebang**: `#!/usr/bin/env bash` on line 1.
- **Strict Mode**: `set -euo pipefail` on line 16 (after license header).
- **Function Style**:
  ```bash
  my_function() {
    local var="$1"
    # body <= 60 lines
  }
  ```

---

## 2. Python Code (`*.py`)

- **Formatter**: Ruff or Black compatible.
- **Line Length**: 100 characters.
- **Imports**: Sorted according to PEP 8 / isort grouping (standard library, third-party, local).
- **Type Annotations**: Mandatory for function signatures in test and automation harnesses.

---

## 3. JSON Configuration (`*.json`)

- **Indent**: 2 spaces.
- **Trailing Newline**: Exactly one newline at EOF.
- **Key Ordering**: Consistent with schema definitions.

---

## 4. Markdown (`*.md`)

- **Headings**: ATX style (`#`, `##`, `###`) with space after hashes.
- **Code Blocks**: Fenced code blocks with language specifiers (`bash`, `python`, `json`, `diff`, `text`).
- **Lists**: Hyphen `-` for unordered list items.
- **Alerts**: GitHub Flavored Markdown blockquotes (`> [!NOTE]`, `> [!IMPORTANT]`, `> [!WARNING]`).
