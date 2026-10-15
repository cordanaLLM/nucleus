# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""The supported Python range is declared once and both of its ends are tested in CI.

pyproject.toml declares the floor, ruff targets it, and the lint-and-test job runs the suite on
the floor and on Python 3.14, the python3 of the ubuntu:26.04 build container (gap row C13-3).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
# python3 in ubuntu:26.04 (apt candidate 3.14.3-0ubuntu2 at the pinned digest).
BUILD_CONTAINER_PYTHON = "3.14"


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _floor() -> str:
    requires = _pyproject()["project"]["requires-python"]
    assert requires.startswith(">="), f"requires-python {requires!r} has no floor"
    return requires.removeprefix(">=").strip()


def _lint_and_test() -> dict:
    ci = yaml.safe_load((WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8"))
    return ci["jobs"]["lint-and-test"]


def test_the_floor_is_declared_and_ruff_targets_it():
    assert _floor() == "3.12"
    target = _pyproject()["tool"]["ruff"]["target-version"]
    assert target == "py" + _floor().replace(".", "")


def test_ci_sets_up_the_floor_as_default_and_the_build_container_python():
    job = _lint_and_test()
    setup = [s for s in job["steps"] if "actions/setup-python@" in s.get("uses", "")]
    assert len(setup) == 1
    versions = str(setup[0]["with"]["python-version"]).split()
    assert versions[-1] == _floor(), "the last listed version is the default python"
    assert BUILD_CONTAINER_PYTHON in versions


def test_ci_runs_the_suite_on_the_build_container_python():
    job = _lint_and_test()
    runs = [s.get("run", "") for s in job["steps"]]
    venv = f"python{BUILD_CONTAINER_PYTHON} -m venv"
    step = [r for r in runs if venv in r]
    assert len(step) == 1, f"no step creates a {BUILD_CONTAINER_PYTHON} environment"
    assert "--require-hashes -r requirements-test.txt" in step[0]
    assert "-m pytest" in step[0]


def test_the_required_context_keeps_its_name():
    """The live ruleset names this context; a matrix suffix would leave it unreported."""
    assert _lint_and_test()["name"] == "Lint & Pytest Quality Gates"


def test_every_literal_python_version_meets_the_floor():
    floor = tuple(int(x) for x in _floor().split("."))
    for workflow in sorted(WORKFLOWS_DIR.glob("*.yml")):
        jobs = (yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}).get("jobs") or {}
        for job_id, job in jobs.items():
            for step in job.get("steps") or []:
                if "actions/setup-python@" not in step.get("uses", ""):
                    continue
                for version in str(step["with"]["python-version"]).split():
                    assert tuple(int(x) for x in version.split(".")) >= floor, (
                        f"{workflow.name}:{job_id} sets up Python {version}, below {_floor()}"
                    )
