# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for the Atheris requirement parser fuzz harness (fuzz/fuzz_verify_kernel_requirement.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import verify_kernel_requirement as vkr  # noqa: E402

HARNESS_PATH = REPO_ROOT / "fuzz" / "fuzz_verify_kernel_requirement.py"
CORPUS_DIR = REPO_ROOT / "fuzz" / "corpus" / "verify_kernel_requirement"


@pytest.fixture(scope="module")
def harness() -> ModuleType:
    """Load the fuzz harness module dynamically."""
    spec = importlib.util.spec_from_file_location("fuzz_verify_kernel_requirement", HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_harness_imports_without_atheris(monkeypatch: pytest.MonkeyPatch) -> None:
    """The harness must import without atheris installed (e.g. in standard test runner)."""
    monkeypatch.setitem(sys.modules, "atheris", None)
    spec = importlib.util.spec_from_file_location(
        "fuzz_verify_kernel_requirement_no_atheris", HARNESS_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "test_one_input")
    assert hasattr(mod, "main")


def test_harness_scorecard_tokens_present() -> None:
    """Ensure the harness text contains the exact tokens Scorecard requires for Python fuzzing detection."""
    content = HARNESS_PATH.read_text(encoding="utf-8")
    assert "import atheris" in content, (
        "Scorecard v5.6.0 requires literal 'import atheris' in a *.py file"
    )
    assert "atheris.Fuzz" in content, "Harness must call atheris.Fuzz()"


def test_corpus_seeds_run_clean(harness: ModuleType) -> None:
    """Every seed file in the corpus must execute through test_one_input without error."""
    seeds = sorted(CORPUS_DIR.iterdir())
    assert len(seeds) >= 5, f"Expected at least 5 seeds in {CORPUS_DIR}, found {len(seeds)}"
    for seed in seeds:
        assert seed.is_file(), f"Expected regular file: {seed}"
        data = seed.read_bytes()
        harness.test_one_input(data)


@pytest.mark.parametrize(
    "raw_input",
    [
        b"",
        b"{",
        b"\xff\xfe",
        b"[]",
        b"null",
        b"{" * 20000,
        b"CONFIG_A=y\n# CONFIG_B is not set",
        ("7." * 40).encode("utf-8"),
        ("9" * 100).encode("utf-8"),
    ],
)
def test_hostile_inputs_run_clean(harness: ModuleType, raw_input: bytes) -> None:
    """Hostile or boundary inputs must execute without uncaught exceptions."""
    harness.test_one_input(raw_input)


def test_planted_defect_parse_config_text_index_error(
    harness: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unexpected exception in parse_config_text must propagate as crash signal."""

    def _faulty_parse_config(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise IndexError("planted defect: unexpected index error")

    monkeypatch.setattr(vkr, "parse_config_text", _faulty_parse_config)
    with pytest.raises(IndexError, match="planted defect"):
        harness.test_one_input(b"CONFIG_TEST=y\n")


def test_planted_defect_release_components_negative(
    harness: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative component from release_components must trigger explicit AssertionError."""
    monkeypatch.setattr(vkr, "release_components", lambda _release: (-1,))
    with pytest.raises(AssertionError):
        harness.test_one_input(b"7.3\n")


def test_planted_defect_parse_requirement_key_error(
    harness: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unexpected KeyError from parse_requirement must propagate as crash signal."""

    def _faulty_parse_requirement(*_args: object, **_kwargs: object) -> vkr.Requirement:
        raise KeyError("planted defect: unexpected key error")

    monkeypatch.setattr(vkr, "parse_requirement", _faulty_parse_requirement)
    with pytest.raises(KeyError, match="planted defect"):
        harness.test_one_input(b"{}")


def test_planted_defect_at_least_not_reflexive(
    harness: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release that is not at least itself must trigger an explicit AssertionError."""
    monkeypatch.setattr(vkr, "at_least", lambda _release, _floor: False)
    with pytest.raises(AssertionError, match="is not True"):
        harness.test_one_input(b"7.3")


def test_harness_instruments_every_function() -> None:
    """Instrumenting only the entry points leaves the parser's helpers without coverage."""
    content = HARNESS_PATH.read_text(encoding="utf-8")
    assert "atheris.instrument_all()" in content
    assert "instrument_func" not in content
