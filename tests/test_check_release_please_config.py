# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for scripts/check_release_please_config.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_release_please_config.py"
OPTION = "bump-patch-for-minor-pre-major"


def _run(config: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), f"--config={config}"]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _write(tmp_path: Path, config: object) -> Path:
    path = tmp_path / "release-please-config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _package(**extra: object) -> dict:
    return {"packages": {".": {"release-type": "simple", "bump-minor-pre-major": True, **extra}}}


def test_repository_config_bumps_minor_for_a_feature():
    result = _run(REPO_ROOT / "release-please-config.json")
    assert result.returncode == 0, result.stderr
    assert "bumps the minor version" in result.stdout


def test_planted_patch_bump_is_refused(tmp_path):
    result = _run(_write(tmp_path, _package(**{OPTION: True})))
    assert result.returncode == 1
    assert f"package '.' sets {OPTION}=true" in result.stderr


def test_planted_top_level_patch_bump_is_refused(tmp_path):
    config = _package()
    config[OPTION] = True
    result = _run(_write(tmp_path, config))
    assert result.returncode == 1
    assert f"top level sets {OPTION}=true" in result.stderr


@pytest.mark.parametrize("value", ["true", 1, None])
def test_a_value_that_is_not_false_is_refused(tmp_path, value):
    result = _run(_write(tmp_path, _package(**{OPTION: value})))
    assert result.returncode == 1


def test_explicit_false_is_accepted(tmp_path):
    result = _run(_write(tmp_path, _package(**{OPTION: False})))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("config", [[], {}, {"packages": []}, {"packages": {".": "simple"}}])
def test_a_file_that_is_not_a_configuration_is_refused(tmp_path, config):
    assert _run(_write(tmp_path, config)).returncode == 1


def test_unreadable_json_is_refused(tmp_path):
    path = tmp_path / "release-please-config.json"
    path.write_text("{", encoding="utf-8")
    assert _run(path).returncode == 1


def test_missing_file_is_refused(tmp_path):
    assert _run(tmp_path / "absent.json").returncode == 1
