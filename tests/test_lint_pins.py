"""Tests for scripts/lint-pins.sh, the zizmor pin gate.

Two layers. The offline tests replace zizmor with a stand-in that prints a recorded
JSON report and exits with a chosen code, so they prove what the wrapper keeps, what it
ignores and how it treats a zizmor failure. The fixture tests run the real, pinned
zizmor against tests/vectors/action-pins: every file under red/ must fail with the audit
its comment names, every file under green/ must pass. They need the network and a GitHub
token, so they skip without them, except where NUCLEUS_REQUIRE_PIN_FIXTURES=1 is set (the
CI step that runs `make lint-pins`), where a skip would hide a gate that never ran.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "lint-pins.sh"
VECTORS = REPO_ROOT / "tests" / "vectors" / "action-pins"

EXPECTED_RED = {
    "docker-tag.yml": {"unpinned-images"},
    "impostor-commit.yml": {"impostor-commit", "ref-version-mismatch"},
    "mismatched-comment.yml": {"ref-version-mismatch"},
    "missing-comment.yml": {"ref-version-mismatch"},
    "unpinned-tag.yml": {"unpinned-uses"},
}

FAKE_ZIZMOR = """#!/usr/bin/env bash
cat "${FAKE_ZIZMOR_REPORT}"
exit "${FAKE_ZIZMOR_RC}"
"""


def _finding(ident: str, path: str = "w.yml", row: int = 6) -> dict:
    return {
        "ident": ident,
        "desc": f"stand-in {ident}",
        "locations": [
            {
                "symbolic": {"key": {"Local": {"verbatim_path": path}}},
                "concrete": {"location": {"start_point": {"row": row, "column": 0}}},
            }
        ],
    }


def _run_fake(tmp_path: Path, findings: list[dict], rc: int) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "zizmor"
    fake.write_text(FAKE_ZIZMOR, encoding="utf-8")
    fake.chmod(0o755)
    report = tmp_path / "report.json"
    report.write_text(json.dumps(findings), encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_ZIZMOR_REPORT": str(report),
        "FAKE_ZIZMOR_RC": str(rc),
    }
    return subprocess.run(
        [str(SCRIPT), str(tmp_path)], capture_output=True, text=True, env=env, check=False
    )


def test_pin_finding_fails_with_file_and_line(tmp_path):
    result = _run_fake(tmp_path, [_finding("ref-version-mismatch", "ci.yml", 11)], 12)
    assert result.returncode == 1
    assert "ci.yml:12: ref-version-mismatch: stand-in ref-version-mismatch" in result.stdout
    assert "lint-pins: 1 pin finding(s)" in result.stdout


def test_other_audits_are_not_this_gates_subject(tmp_path):
    result = _run_fake(tmp_path, [_finding("artipacked"), _finding("dangerous-triggers")], 14)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "lint-pins: 0 pin finding(s)" in result.stdout


@pytest.mark.parametrize("rc", [1, 2, 3, 99])
def test_zizmor_failure_is_not_a_pass(tmp_path, rc):
    result = _run_fake(tmp_path, [], rc)
    assert result.returncode == 1
    assert f"zizmor failed with exit code {rc}" in result.stderr


def test_every_red_fixture_is_listed():
    assert {p.name for p in (VECTORS / "red").glob("*.yml")} == set(EXPECTED_RED)
    assert {p.name for p in (VECTORS / "green").glob("*.yml")}


def _require_live() -> None:
    missing = [
        what
        for what, ok in (
            ("zizmor", shutil.which("zizmor")),
            ("jq", shutil.which("jq")),
            ("GH_TOKEN", os.environ.get("GH_TOKEN")),
        )
        if not ok
    ]
    if not missing:
        return
    message = f"live pin fixtures need {', '.join(missing)}"
    if os.environ.get("NUCLEUS_REQUIRE_PIN_FIXTURES") == "1":
        pytest.fail(message)
    pytest.skip(message)


def _live(path: Path) -> tuple[int, set[str]]:
    result = subprocess.run([str(SCRIPT), str(path)], capture_output=True, text=True, check=False)
    idents = set(re.findall(r":\d+: ([a-z-]+): ", result.stdout))
    return result.returncode, idents


@pytest.mark.parametrize("name", sorted(EXPECTED_RED))
def test_red_fixture_fails_live(name):
    _require_live()
    rc, idents = _live(VECTORS / "red" / name)
    assert rc == 1
    assert idents == EXPECTED_RED[name]


@pytest.mark.parametrize("name", ["docker-digest.yml", "tagged-pin.yml"])
def test_green_fixture_passes_live(name):
    _require_live()
    rc, idents = _live(VECTORS / "green" / name)
    assert (rc, idents) == (0, set())


def test_lint_pins_runs_in_ci_and_stays_out_of_offline_lint():
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^lint-pins: .*## ", makefile, re.MULTILINE)
    assert "./scripts/lint-pins.sh" in makefile
    lint_prerequisites = re.search(r"^lint:([^#\n]*)", makefile, re.MULTILINE).group(1)
    assert "lint-pins" not in lint_prerequisites, "make lint must stay offline"

    ci = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    steps = [step for job in ci["jobs"].values() for step in job["steps"]]
    pins = [step for step in steps if "make lint-pins" in step.get("run", "")]
    assert len(pins) == 1, "ci.yml must run make lint-pins once"
    assert pins[0]["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert pins[0]["env"]["NUCLEUS_REQUIRE_PIN_FIXTURES"] == "1"
    assert "tests/test_lint_pins.py" in pins[0]["run"]


def test_zizmor_is_pinned_in_the_test_lock():
    lock = (REPO_ROOT / "requirements-test.txt").read_text(encoding="utf-8")
    assert re.search(r"^zizmor==\d+\.\d+\.\d+ \\$", lock, re.MULTILINE)
