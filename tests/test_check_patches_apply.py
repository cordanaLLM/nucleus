# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for scripts/check_patches_apply.py and its workflow integration."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_patches_apply import main  # noqa: E402

HAVE_PATCH = shutil.which("patch") is not None


def _make_fake_tree(tmp_path: Path, with_makefile: bool = True) -> Path:
    tree = tmp_path / "kernel-tree"
    tree.mkdir(parents=True, exist_ok=True)
    if with_makefile:
        (tree / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    (tree / "target.txt").write_text("alpha\nbravo\ncharlie\n", encoding="utf-8")
    return tree


def _make_versions(tmp_path: Path, streams: tuple[str, ...] = ("mainstream", "lts")) -> Path:
    versions = {
        "streams": {s: {"version": "7.2.10"} for s in streams},
        "architectures": {"x86_64": {}},
    }
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(versions), encoding="utf-8")
    return path


GOOD_PATCH = """\
--- a/target.txt
+++ b/target.txt
@@ -1,3 +1,3 @@
 alpha
-bravo
+bravo modified
 charlie
"""

BAD_PATCH = """\
--- a/target.txt
+++ b/target.txt
@@ -1,3 +1,3 @@
-delta
+echo
 foxtrot
"""


def test_passes_on_no_patches_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = _make_fake_tree(tmp_path)
    versions = _make_versions(tmp_path)
    patches_dir = tmp_path / "nonexistent-patches"

    rc = main(
        [
            "--stream",
            "mainstream",
            "--source-tree",
            str(tree),
            "--patches-dir",
            str(patches_dir),
            "--versions",
            str(versions),
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 0
    assert "no carried patches for mainstream" in out
    assert not err


@pytest.mark.skipif(not HAVE_PATCH, reason="patch binary not found in PATH")
def test_passes_on_common_patch_that_applies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _make_fake_tree(tmp_path)
    versions = _make_versions(tmp_path)
    patches_dir = tmp_path / "patches"
    common_dir = patches_dir / "common"
    common_dir.mkdir(parents=True)
    (common_dir / "0001-good.patch").write_text(GOOD_PATCH, encoding="utf-8")

    rc = main(
        [
            "--stream",
            "mainstream",
            "--source-tree",
            str(tree),
            "--patches-dir",
            str(patches_dir),
            "--versions",
            str(versions),
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 0
    assert "all 1 carried patch(es) apply cleanly for mainstream" in out
    assert not err


@pytest.mark.skipif(not HAVE_PATCH, reason="patch binary not found in PATH")
def test_refuses_common_patch_that_does_not_apply(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _make_fake_tree(tmp_path)
    versions = _make_versions(tmp_path)
    patches_dir = tmp_path / "patches"
    common_dir = patches_dir / "common"
    common_dir.mkdir(parents=True)
    (common_dir / "0001-bad.patch").write_text(BAD_PATCH, encoding="utf-8")

    rc = main(
        [
            "--stream",
            "mainstream",
            "--source-tree",
            str(tree),
            "--patches-dir",
            str(patches_dir),
            "--versions",
            str(versions),
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 1
    assert "0001-bad.patch: does not apply to" in err


def test_refuses_patch_in_unknown_subdir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _make_fake_tree(tmp_path)
    versions = _make_versions(tmp_path)
    patches_dir = tmp_path / "patches"
    unknown_dir = patches_dir / "experimental"
    unknown_dir.mkdir(parents=True)
    (unknown_dir / "0001-test.patch").write_text(GOOD_PATCH, encoding="utf-8")

    rc = main(
        [
            "--stream",
            "mainstream",
            "--source-tree",
            str(tree),
            "--patches-dir",
            str(patches_dir),
            "--versions",
            str(versions),
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 1
    assert "subdirectory is neither 'common' nor a declared stream" in err


def test_refuses_non_patch_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = _make_fake_tree(tmp_path)
    versions = _make_versions(tmp_path)
    patches_dir = tmp_path / "patches"
    common_dir = patches_dir / "common"
    common_dir.mkdir(parents=True)
    (common_dir / "README.txt").write_text("some notes\n", encoding="utf-8")

    rc = main(
        [
            "--stream",
            "mainstream",
            "--source-tree",
            str(tree),
            "--patches-dir",
            str(patches_dir),
            "--versions",
            str(versions),
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 1
    assert "file does not match '*.patch'" in err


def test_refuses_unknown_stream(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = _make_fake_tree(tmp_path)
    versions = _make_versions(tmp_path)
    patches_dir = tmp_path / "patches"

    rc = main(
        [
            "--stream",
            "nonexistent_stream",
            "--source-tree",
            str(tree),
            "--patches-dir",
            str(patches_dir),
            "--versions",
            str(versions),
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 1
    assert "unknown stream 'nonexistent_stream'" in err


def test_refuses_tree_without_makefile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = _make_fake_tree(tmp_path, with_makefile=False)
    versions = _make_versions(tmp_path)
    patches_dir = tmp_path / "patches"

    rc = main(
        [
            "--stream",
            "mainstream",
            "--source-tree",
            str(tree),
            "--patches-dir",
            str(patches_dir),
            "--versions",
            str(versions),
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 1
    assert "source tree missing top-level Makefile" in err


@pytest.mark.skipif(not HAVE_PATCH, reason="patch binary not found in PATH")
def test_per_stream_patch_only_checked_for_its_stream(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tree = _make_fake_tree(tmp_path)
    versions = _make_versions(tmp_path, streams=("mainstream", "lts"))
    patches_dir = tmp_path / "patches"
    mainstream_dir = patches_dir / "mainstream"
    lts_dir = patches_dir / "lts"
    mainstream_dir.mkdir(parents=True)
    lts_dir.mkdir(parents=True)
    (mainstream_dir / "0001-mainstream.patch").write_text(GOOD_PATCH, encoding="utf-8")
    (lts_dir / "0001-lts.patch").write_text(BAD_PATCH, encoding="utf-8")

    # Checking mainstream should succeed: only mainstream patch checked, lts bad patch ignored
    rc_mainstream = main(
        [
            "--stream",
            "mainstream",
            "--source-tree",
            str(tree),
            "--patches-dir",
            str(patches_dir),
            "--versions",
            str(versions),
        ]
    )
    out_m, err_m = capsys.readouterr()
    assert rc_mainstream == 0
    assert "all 1 carried patch(es) apply cleanly for mainstream" in out_m
    assert not err_m

    # Checking lts should fail: lts bad patch fails
    rc_lts = main(
        [
            "--stream",
            "lts",
            "--source-tree",
            str(tree),
            "--patches-dir",
            str(patches_dir),
            "--versions",
            str(versions),
        ]
    )
    out_l, err_l = capsys.readouterr()
    assert rc_lts == 1
    assert "0001-lts.patch: does not apply to" in err_l


def test_workflow_runs_check_patches_apply_before_resolve() -> None:
    workflow_path = REPO_ROOT / ".github" / "workflows" / "verify-requirements.yml"
    assert workflow_path.is_file()
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    resolve_job = workflow.get("jobs", {}).get("resolve", {})
    steps = resolve_job.get("steps", [])
    step_names = [s.get("name", "") for s in steps]

    assert "Check the Carried Patches Apply" in step_names
    assert "Save the Source Download" in step_names
    assert "Resolve the Configuration" in step_names

    check_idx = step_names.index("Check the Carried Patches Apply")
    save_idx = step_names.index("Save the Source Download")
    resolve_idx = step_names.index("Resolve the Configuration")

    assert save_idx < check_idx < resolve_idx

    check_step = next(s for s in steps if s.get("name") == "Check the Carried Patches Apply")
    assert check_step.get("shell") == "bash"
    assert check_step.get("env", {}).get("STREAM") == "${{ matrix.stream }}"
    run_cmd = check_step.get("run", "")
    assert "set -euo pipefail" in run_cmd
    assert "scripts/check_patches_apply.py" in run_cmd
    assert '--stream="${STREAM}"' in run_cmd
    assert '--source-tree="${RUNNER_TEMP}/linux-${STREAM}"' in run_cmd
