# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for the patch series of scripts/check_patches_apply.py: pins, order, --apply and patches/bbr3."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_patches_apply import load_manifest, main, validate_series  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("patch") is None, reason="needs GNU patch")

SHA_1 = "1" * 40
SHA_2 = "2" * 40
EPOCH = 1_700_000_000

FIRST = f"""From {SHA_1} Mon Sep 17 00:00:00 2001
Subject: [PATCH 1/2] first

--- a/target.txt
+++ b/target.txt
@@ -1,3 +1,3 @@
 alpha
-bravo
+bravo one
 charlie
"""

# Applies only on top of FIRST: it changes the line FIRST wrote, and adds a file in a new directory.
SECOND = f"""From {SHA_2} Mon Sep 17 00:00:00 2001
Subject: [PATCH 2/2] second

--- a/target.txt
+++ b/target.txt
@@ -1,3 +1,3 @@
 alpha
-bravo one
+bravo two
 charlie
--- /dev/null
+++ b/net/new/added.c
@@ -0,0 +1 @@
+int added;
"""

WRONG_BASE = f"""From {SHA_2} Mon Sep 17 00:00:00 2001
Subject: [PATCH 2/2] second, written for another base

--- a/target.txt
+++ b/target.txt
@@ -1,3 +1,3 @@
 alpha
-bravo zero
+bravo two
 charlie
"""


def _tree(tmp_path: Path) -> Path:
    tree = tmp_path / "linux"
    tree.mkdir()
    (tree / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    (tree / "target.txt").write_text("alpha\nbravo\ncharlie\n", encoding="utf-8")
    os.utime(tree / "Makefile", (EPOCH, EPOCH))
    return tree


def _versions(tmp_path: Path, commits: list[str], streams: list[str] | None = None) -> Path:
    data = {
        "streams": {"mainstream": {}, "lts": {}},
        "patches": {"demo": {"streams": streams or ["mainstream"], "commits": commits}},
    }
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _patches(tmp_path: Path, *texts: str) -> Path:
    directory = tmp_path / "patches" / "demo"
    directory.mkdir(parents=True)
    for idx, text in enumerate(texts, start=1):
        (directory / f"{idx:04d}-demo.patch").write_text(text, encoding="utf-8")
    return directory.parent


def _run(tree: Path, patches: Path, versions: Path, *extra: str, stream: str = "mainstream") -> int:
    return main(
        [
            "--stream",
            stream,
            "--source-tree",
            str(tree),
            "--patches-dir",
            str(patches),
            "--versions",
            str(versions),
            *extra,
        ]
    )


def _digest(tree: Path) -> str:
    files = sorted(p for p in tree.rglob("*") if p.is_file())
    return hashlib.sha256(b"".join(p.name.encode() + p.read_bytes() for p in files)).hexdigest()


def test_series_applies_in_order_without_touching_the_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _tree(tmp_path)
    before = _digest(tree)
    rc = _run(tree, _patches(tmp_path, FIRST, SECOND), _versions(tmp_path, [SHA_1, SHA_2]))
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert "all 2 carried patch(es) apply cleanly for mainstream" in out
    assert _digest(tree) == before


def test_series_is_not_carried_for_a_stream_it_does_not_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _tree(tmp_path)
    rc = _run(
        tree, _patches(tmp_path, FIRST, SECOND), _versions(tmp_path, [SHA_1, SHA_2]), stream="lts"
    )
    assert rc == 0
    assert "no carried patches for lts" in capsys.readouterr().out


def test_refuses_files_out_of_pinned_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run(
        _tree(tmp_path), _patches(tmp_path, FIRST, SECOND), _versions(tmp_path, [SHA_2, SHA_1])
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert f"0001-demo.patch: does not start with 'From {SHA_2} '" in err


def test_refuses_a_series_with_a_missing_patch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run(_tree(tmp_path), _patches(tmp_path, FIRST), _versions(tmp_path, [SHA_1, SHA_2]))
    assert rc == 1
    assert "holds 1 patches, versions.json pins 2 commits" in capsys.readouterr().err


def test_refuses_a_series_for_an_undeclared_stream(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    versions = _versions(tmp_path, [SHA_1], streams=["mainstream", "nightly"])
    rc = _run(_tree(tmp_path), _patches(tmp_path, FIRST), versions)
    assert rc == 1
    assert "patches.demo: bad name or streams ['nightly']" in capsys.readouterr().err


def test_refuses_a_patch_of_another_base(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run(
        _tree(tmp_path), _patches(tmp_path, FIRST, WRONG_BASE), _versions(tmp_path, [SHA_1, SHA_2])
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "0002-demo.patch: does not apply to" in err


def test_apply_stops_before_a_patch_of_another_base(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _tree(tmp_path)
    rc = _run(
        tree, _patches(tmp_path, FIRST, WRONG_BASE), _versions(tmp_path, [SHA_1, SHA_2]), "--apply"
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "(applied before it: demo/0001-demo.patch)" in err
    assert (tree / "target.txt").read_text(encoding="utf-8") == "alpha\nbravo one\ncharlie\n"
    assert not (tree / ".nucleus-patches.json").exists()
    assert sorted(p.name for p in tree.iterdir()) == ["Makefile", "target.txt"]


def test_apply_patches_the_tree_records_it_and_pins_mtimes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _tree(tmp_path)
    patches = _patches(tmp_path, FIRST, SECOND)
    rc = _run(tree, patches, _versions(tmp_path, [SHA_1, SHA_2]), "--apply")
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert "all 2 carried patch(es) applied for mainstream" in out
    assert (tree / "target.txt").read_text(encoding="utf-8") == "alpha\nbravo two\ncharlie\n"
    assert (tree / "net/new/added.c").read_text(encoding="utf-8") == "int added;\n"
    record = json.loads((tree / ".nucleus-patches.json").read_text(encoding="utf-8"))
    assert [entry["patch"] for entry in record] == ["demo/0001-demo.patch", "demo/0002-demo.patch"]
    assert (
        record[1]["sha256"]
        == hashlib.sha256((patches / "demo/0002-demo.patch").read_bytes()).hexdigest()
    )
    for rel in ("target.txt", "net", "net/new", "net/new/added.c", ".nucleus-patches.json"):
        assert int((tree / rel).stat().st_mtime) == EPOCH, rel


def test_apply_refuses_a_tree_it_already_patched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _tree(tmp_path)
    patches = _patches(tmp_path, FIRST, SECOND)
    versions = _versions(tmp_path, [SHA_1, SHA_2])
    assert _run(tree, patches, versions, "--apply") == 0
    capsys.readouterr()
    assert _run(tree, patches, versions, "--apply") == 1
    assert "the tree is already patched" in capsys.readouterr().err


def test_carried_series_matches_its_pin() -> None:
    _, series = load_manifest(REPO_ROOT / "versions.json")
    assert set(series) == {"bbr3"}
    assert validate_series(REPO_ROOT / "patches", series) == []
    assert "lts" not in series["bbr3"].streams


def test_carried_series_out_of_order_is_refused(tmp_path: Path) -> None:
    _, series = load_manifest(REPO_ROOT / "versions.json")
    shutil.copytree(REPO_ROOT / "patches", tmp_path / "patches")
    first, second = sorted((tmp_path / "patches" / "bbr3").glob("*.patch"))[:2]
    first_text, second_text = first.read_bytes(), second.read_bytes()
    first.write_bytes(second_text)
    second.write_bytes(first_text)
    problems = validate_series(tmp_path / "patches", series)
    assert len(problems) == 2
    assert all("does not start with 'From " in problem for problem in problems)


def test_schema_refuses_a_series_pinned_by_a_branch_name() -> None:
    schema = json.loads((REPO_ROOT / "versions.schema.json").read_text(encoding="utf-8"))
    versions = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
    jsonschema.validate(versions, schema)
    planted = copy.deepcopy(versions)
    planted["patches"]["bbr3"]["commits"][0] = "bbr-v3-2026-09-16-01"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(planted, schema)


# Its first file applies, its second does not: patch without a dry run would change target.txt.
HALF = f"""From {SHA_1} Mon Sep 17 00:00:00 2001
Subject: [PATCH] half

--- a/target.txt
+++ b/target.txt
@@ -1,3 +1,3 @@
 alpha
-bravo
+bravo half
 charlie
--- a/other.txt
+++ b/other.txt
@@ -1,3 +1,3 @@
 xray
-yankee
+yankee half
 zulu
"""

# Its leading context line differs from the tree: patch applies it with fuzz 1, never with -F0.
FUZZY = f"""From {SHA_1} Mon Sep 17 00:00:00 2001
Subject: [PATCH] fuzzy

--- a/target.txt
+++ b/target.txt
@@ -1,3 +1,3 @@
 alpha changed upstream
-bravo
+bravo fuzzy
 charlie
"""


def test_apply_leaves_no_half_applied_patch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _tree(tmp_path)
    (tree / "other.txt").write_text("xray\nyankee changed\nzulu\n", encoding="utf-8")
    rc = _run(tree, _patches(tmp_path, HALF), _versions(tmp_path, [SHA_1]), "--apply")
    assert rc == 1
    assert "(applied before it: none)" in capsys.readouterr().err
    assert (tree / "target.txt").read_text(encoding="utf-8") == "alpha\nbravo\ncharlie\n"


@pytest.mark.parametrize("mode", [(), ("--apply",)])
def test_refuses_a_patch_that_needs_fuzz(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: tuple[str, ...]
) -> None:
    tree = _tree(tmp_path)
    rc = _run(tree, _patches(tmp_path, FUZZY), _versions(tmp_path, [SHA_1]), *mode)
    assert rc == 1
    assert "0001-demo.patch: does not apply to" in capsys.readouterr().err
    assert (tree / "target.txt").read_text(encoding="utf-8") == "alpha\nbravo\ncharlie\n"
