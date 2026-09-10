## Description
<!-- Provide a concise description of the changes introduced by this PR. -->

## Changes Checklist
- [ ] **Single Source of Truth**: All kernel streams, tags, URLs, and architectures originate exclusively from `versions.json` (no hardcoded versions in shell scripts or CI).
- [ ] **NASA/JPL Power of 10**: All shell script functions are <= 60 lines, enforce `set -euo pipefail`, and contain bounded loops.
- [ ] **ShellCheck & Linters**: `make lint` passes locally with zero warnings across all scripts and workflows.
- [ ] **Automated Tests**: `make test` passes (`pytest tests/ -v`).
- [ ] **Privacy Invariant**: Verified zero occurrences of private IP ranges (`10.x`, `192.168.x`, `172.16-31.x`) or local workstation home paths.
- [ ] **Docs & Code Synchrony**: User-discoverable changes reflected in documentation (`README.md`, `docs/`) in the exact same commit.
- [ ] **Conventional Commits**: Commit messages follow `type(scope): subject` syntax.

## Related Issues / Epics
Fixes #
Relates to EPIC-
