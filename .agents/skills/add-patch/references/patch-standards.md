# Kernel Patch Quality & Format Standards

> Layer 3 reference for `/add-patch`. Standards for Linux kernel patches in `lusoris-kernel-forge`.

---

## 1. Patch Directory Structure

```
patches/
├── common/             # Applied to all streams
│   └── 0001-sched-ext-tuning.patch
├── bleeding/           # Applied exclusively to bleeding (mainline 7.3-rc2)
├── mainstream/         # Applied exclusively to mainstream (stable 7.2.4)
├── lts/                # Applied exclusively to lts (longterm 6.18.50)
└── realtime/           # Applied exclusively to realtime (7.2-rt)
```

---

## 2. Header & Commit Message Template

Every patch file must contain a complete header matching standard kernel git patches:

```diff
From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: Contributor Name <contributor@example.com>
Date: Thu, 10 Sep 2026 12:00:00 +0000
Subject: [PATCH] subsystem: clear summary of change in imperative mood

Detailed problem description explaining why this change is necessary,
what problem it solves, and how it impacts kernel behavior.

Link: https://lore.kernel.org/all/...
Signed-off-by: Contributor Name <contributor@example.com>
---
 path/to/source.c | 10 +++++-----
 1 file changed, 5 insertions(+), 5 deletions(-)

diff --git a/path/to/source.c b/path/to/source.c
...
```

---

## 3. Formatting & Review Rules

- **Unified Diff**: Generate patches with `git format-patch -1 <commit>` or `git diff -p`.
- **Indentation**: Follow Linux kernel coding style (8-character tabs, 80-character line width guideline).
- **Checkpatch.pl**: If the kernel tree is present, run `scripts/checkpatch.pl --no-tree <patch>` and resolve all errors.
- **Atomic Scope**: One patch per logical change. Do not bundle unrelated refactorings or stylistic edits.
- **Privacy Assurance**: Never embed local developer paths, internal corporate server hostnames, or private RFC 1918 IPs in patch bodies.
