"""Tests for the event-driven one-shot required-checks status evaluator (`--once`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_SCRIPT = REPO_ROOT / "scripts" / "required_checks_gate.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import required_checks_gate as gate  # noqa: E402

NOW = datetime(2026, 10, 22, 4, 0, 0, tzinfo=UTC)
NOW_ISO = "2026-10-22T04:00:00Z"
BOUND = 19800
AGGREGATOR_PATH = ".github/workflows/required-aggregator.yml"
EXCLUDED = frozenset({AGGREGATOR_PATH})
IGNORED = frozenset({"Evaluate required checks", "required-checks"})
ALLOW_SKIP = frozenset({"Semgrep SAST", "Trivy Filesystem CVE Scan", "verify-tool-release"})


def _check(
    name: str,
    conclusion: str | None = "success",
    status: str = "completed",
    run_id: int = 1,
    check_suite: int | None = None,
) -> dict:
    item = {"id": run_id, "name": name, "status": status, "conclusion": conclusion}
    if check_suite is not None:
        item["check_suite"] = check_suite
    return item


def _wf(
    name: str = "build-matrix",
    path: str = ".github/workflows/build-matrix.yml",
    status: str = "completed",
    conclusion: str | None = "success",
    created_at: str = "2026-10-22T03:00:00Z",
    run_id: int = 10,
    event: str = "push",
    check_suite_id: int | None = None,
) -> dict:
    item = {
        "id": run_id,
        "name": name,
        "path": path,
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at,
        "run_attempt": 1,
        "event": event,
    }
    if check_suite_id is not None:
        item["check_suite_id"] = check_suite_id
    return item


def test_decide_positive_all_success_and_allowed_skips():
    checks = [
        _check("Lint & Pytest Quality Gates", "success", run_id=1),
        _check("Compile Kernel (mainstream / x86_64)", "success", run_id=2),
        _check("Semgrep SAST", "skipped", run_id=3),
    ]
    wfs = [_wf("ci", ".github/workflows/ci.yml"), _wf("build-matrix")]
    state, desc = gate.decide(
        checks, wfs, "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "success"
    assert desc == "3 check runs passed"


def test_decide_negative_planted_failing_build_check_run():
    checks = [
        _check("Lint & Pytest Quality Gates", "success", run_id=1),
        _check("Compile Kernel (mainstream / x86_64)", "failure", run_id=2),
    ]
    wfs = [_wf("ci", ".github/workflows/ci.yml"), _wf("build-matrix", conclusion="failure")]
    state, desc = gate.decide(
        checks, wfs, "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "failure"
    assert desc == "Compile Kernel (mainstream / x86_64): failure (+1 more)"


@pytest.mark.parametrize(
    ("check_name", "conclusion", "expected"),
    [
        ("Compile Kernel (lts / arm64)", "cancelled", "Compile Kernel (lts / arm64): cancelled"),
        ("Lint & Pytest Quality Gates", "skipped", "Lint & Pytest Quality Gates: skipped"),
    ],
)
def test_decide_negative_cancelled_and_disallowed_skip(check_name, conclusion, expected):
    checks = [_check("REUSE lint", "success", run_id=1), _check(check_name, conclusion, run_id=2)]
    state, desc = gate.decide(
        checks, [_wf()], "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "failure"
    assert desc == expected


def test_decide_negative_multiple_problems_summarises_count():
    checks = [
        _check("Alpha Check", "failure", run_id=1),
        _check("Beta Check", "cancelled", run_id=2),
        _check("Gamma Check", "timed_out", run_id=3),
    ]
    state, desc = gate.decide(
        checks, [_wf()], "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "failure"
    assert desc == "Alpha Check: failure (+2 more)"


@pytest.mark.parametrize("conclusion", ["startup_failure", "cancelled", "failure", "timed_out"])
def test_decide_negative_failed_workflow_run_without_failing_check_run(conclusion):
    """A workflow that failed before its jobs reported fails the status on its own."""
    wfs = [_wf("ci", ".github/workflows/ci.yml"), _wf("build-matrix", conclusion=conclusion)]
    state, desc = gate.decide(
        [_check("REUSE lint")], wfs, "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "failure"
    assert desc == f"build-matrix: {conclusion}"


@pytest.mark.parametrize("conclusion", ["skipped", "neutral"])
def test_decide_boundary_skipped_or_neutral_workflow_run_passes(conclusion):
    wfs = [_wf("ci", ".github/workflows/ci.yml"), _wf("build-matrix", conclusion=conclusion)]
    state, _ = gate.decide(
        [_check("REUSE lint")], wfs, "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "success"


def test_decide_pending_workflow_run_younger_than_bound():
    wfs = [
        _wf("ci", ".github/workflows/ci.yml", status="completed"),
        _wf(
            "build-matrix", status="in_progress", conclusion=None, created_at="2026-10-22T02:00:00Z"
        ),
    ]
    state, desc = gate.decide(
        [_check("REUSE lint")], wfs, "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "pending"
    assert desc == "waiting on 1: build-matrix"


def test_decide_boundary_exact_bound_is_pending_and_one_second_over_fails():
    # NOW is 04:00:00Z; 19800s (5h30m) earlier is 2026-10-21T22:30:00Z.
    at_bound = [_wf("build-matrix", status="in_progress", created_at="2026-10-21T22:30:00Z")]
    state_eq, desc_eq = gate.decide(
        [_check("REUSE lint")], at_bound, "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW
    )
    assert state_eq == "pending"
    assert desc_eq == "waiting on 1: build-matrix"

    over_bound = [_wf("build-matrix", status="in_progress", created_at="2026-10-21T22:29:59Z")]
    state_gt, desc_gt = gate.decide(
        [_check("REUSE lint")], over_bound, "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW
    )
    assert state_gt == "failure"
    assert desc_gt == f"deadline of {BOUND}s passed; still running: build-matrix"


def test_decide_never_finishing_build_fails_at_bound():
    stuck = [
        _wf("build-matrix", status="queued", conclusion=None, created_at="2026-10-21T20:00:00Z")
    ]
    state, desc = gate.decide(
        [_check("REUSE lint")], stuck, "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "failure"
    assert desc == "deadline of 19800s passed; still running: build-matrix"


def test_decide_excluded_workflow_path_and_superseded_workflow_run_ignored():
    wfs = [
        _wf(
            "Required Checks Aggregator",
            path=AGGREGATOR_PATH,
            status="in_progress",
            created_at="2026-10-21T10:00:00Z",
            run_id=99,
        ),
        _wf("ci", ".github/workflows/ci.yml", status="in_progress", run_id=10),
        _wf("ci", ".github/workflows/ci.yml", status="completed", run_id=11),
    ]
    state, desc = gate.decide(
        [_check("REUSE lint")], wfs, "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "success"
    assert desc == "1 check runs passed"


def test_decide_ignored_check_names_and_self_are_excluded():
    checks = [
        _check("Evaluate required checks", None, status="in_progress", run_id=1),
        _check("required-checks", "failure", status="completed", run_id=2),
        _check("REUSE lint", "success", status="completed", run_id=3),
    ]
    state, desc = gate.decide(
        checks, [_wf()], "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "success"
    assert desc == "1 check runs passed"


def test_decide_no_other_check_run_is_failure():
    checks = [
        _check("Evaluate required checks", None, status="in_progress", run_id=1),
        _check("required-checks", None, status="in_progress", run_id=2),
    ]
    state, desc = gate.decide(
        checks, [_wf()], "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "failure"
    assert "no check run besides 'required-checks'" in desc


def test_decide_workflow_runs_completed_but_non_actions_check_pending():
    checks = [
        _check("REUSE lint", "success", status="completed", run_id=1),
        _check("external/cla", None, status="in_progress", run_id=2),
    ]
    state, desc = gate.decide(
        checks, [_wf()], "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "pending"
    assert desc == "waiting on 1: external/cla"


def _write_gh_stub(
    tmp_path: Path,
    wf_lines: str,
    check_lines: str,
    statuses_lines: str,
    fail_fetch: bool,
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (tmp_path / "wf.jsonl").write_text(wf_lines, encoding="utf-8")
    (tmp_path / "checks.jsonl").write_text(check_lines, encoding="utf-8")
    (tmp_path / "statuses.jsonl").write_text(statuses_lines, encoding="utf-8")
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"root = pathlib.Path({str(tmp_path)!r})\n"
        'with (root / "gh_calls.jsonl").open("a", encoding="utf-8") as fh:\n'
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "args = ' '.join(sys.argv[1:])\n"
        f"if {fail_fetch!r} and ('actions/runs' in args or 'check-runs' in args):\n"
        "    sys.stderr.write('simulated API outage\\n')\n"
        "    sys.exit(1)\n"
        "if '-X POST' in args:\n"
        "    print('{}')\n"
        "elif 'actions/runs' in args:\n"
        '    sys.stdout.write((root / "wf.jsonl").read_text(encoding="utf-8"))\n'
        "elif 'check-runs' in args:\n"
        '    sys.stdout.write((root / "checks.jsonl").read_text(encoding="utf-8"))\n'
        "elif '/statuses' in args:\n"
        '    sys.stdout.write((root / "statuses.jsonl").read_text(encoding="utf-8"))\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir


def _run_once_with_stub(
    tmp_path: Path,
    *,
    wf_lines: str,
    check_lines: str,
    statuses_lines: str = "",
    fail_fetch: bool = False,
    extra_args: list[str] | None = None,
) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
    """Run required_checks_gate.py --once with a fake gh that logs every invocation's argv."""
    bin_dir = _write_gh_stub(tmp_path, wf_lines, check_lines, statuses_lines, fail_fetch)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    cmd = [
        sys.executable,
        str(GATE_SCRIPT),
        "--once",
        "--post-status",
        "--repo",
        "o/r",
        "--ref",
        "deadbeef",
        "--self",
        "required-checks",
        "--bound",
        str(BOUND),
        "--now",
        NOW_ISO,
        "--exclude-workflow",
        AGGREGATOR_PATH,
        "--ignore-check",
        "Evaluate required checks",
        "--ignore-check",
        "required-checks",
        "--allow-skip",
        "Semgrep SAST",
        "--target-url",
        "https://github.com/o/r/actions/runs/42",
        *(extra_args or []),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    log_file = tmp_path / "gh_calls.jsonl"
    calls = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return res, calls


@pytest.mark.parametrize(
    ("check_conclusion", "expected_state"),
    [("success", "success"), ("failure", "failure")],
)
def test_once_post_status_posts_expected_state_and_exits_zero(
    tmp_path: Path, check_conclusion: str, expected_state: str
):
    wf_line = json.dumps(_wf("ci", ".github/workflows/ci.yml")) + "\n"
    long_name = "Compile Kernel " + ("x" * 160)
    check_line = json.dumps(_check(long_name, check_conclusion)) + "\n"
    res, calls = _run_once_with_stub(tmp_path, wf_lines=wf_line, check_lines=check_line)
    assert res.returncode == 0, res.stderr
    post_calls = [c for c in calls if "POST" in c]
    assert len(post_calls) == 1
    post = post_calls[0]
    assert "repos/o/r/statuses/deadbeef" in post
    assert f"state={expected_state}" in post
    assert "context=required-checks" in post
    assert "target_url=https://github.com/o/r/actions/runs/42" in post
    desc_arg = next(arg for arg in post if arg.startswith("description="))
    desc_val = desc_arg.split("=", 1)[1]
    assert len(desc_val) <= 140


def test_once_fetch_error_posts_pending_and_exits_one(tmp_path: Path):
    res, calls = _run_once_with_stub(tmp_path, wf_lines="", check_lines="", fail_fetch=True)
    assert res.returncode == 1
    assert "::error::evaluation failed:" in res.stderr
    post_calls = [c for c in calls if "POST" in c]
    assert len(post_calls) == 1
    post = post_calls[0]
    assert "state=pending" in post
    assert "context=required-checks" in post


def test_once_only_if_pending_skips_settled_and_evaluates_pending(tmp_path: Path):
    wf_line = json.dumps(_wf("ci", ".github/workflows/ci.yml")) + "\n"
    check_line = json.dumps(_check("REUSE lint", "success")) + "\n"
    settled_lines = (
        '{"id": 1, "context": "required-checks", "state": "pending"}\n'
        '{"id": 2, "context": "required-checks", "state": "success"}\n'
    )
    res_settled, calls_settled = _run_once_with_stub(
        tmp_path,
        wf_lines=wf_line,
        check_lines=check_line,
        statuses_lines=settled_lines,
        extra_args=["--only-if-pending"],
    )
    assert res_settled.returncode == 0
    assert "already settled" in res_settled.stdout
    assert [c for c in calls_settled if "POST" in c] == []

    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    pending_lines = '{"id": 3, "context": "required-checks", "state": "pending"}\n'
    res_pending, calls_pending = _run_once_with_stub(
        pending_dir,
        wf_lines=wf_line,
        check_lines=check_line,
        statuses_lines=pending_lines,
        extra_args=["--only-if-pending"],
    )
    assert res_pending.returncode == 0
    assert [c for c in calls_pending if "POST" in c] != []


def test_superseded_suites():
    wfs = [
        _wf("ci", ".github/workflows/ci.yml", run_id=10, check_suite_id=100),
        _wf("ci", ".github/workflows/ci.yml", run_id=11, check_suite_id=101),
        _wf("build-matrix", ".github/workflows/build-matrix.yml", run_id=20, check_suite_id=200),
        _wf("standalone", path="", run_id=30, check_suite_id=300),
        _wf("standalone", path="", run_id=31, check_suite_id=301),
        _wf("no-suite", path="no-suite.yml", run_id=40, check_suite_id=None),
        _wf("no-suite", path="no-suite.yml", run_id=41, check_suite_id=401),
        _wf("aggregator", path=AGGREGATOR_PATH, run_id=50, check_suite_id=500),
        _wf("aggregator", path=AGGREGATOR_PATH, run_id=51, check_suite_id=501),
    ]
    assert gate.superseded_suites(wfs) == {100, 300, 500}


def test_scheduled_suites():
    wfs = [
        _wf("push-run", event="push", check_suite_id=100),
        _wf("pr-run", event="pull_request", check_suite_id=101),
        _wf("downstream-wf", event="workflow_run", check_suite_id=200),
        _wf("scheduled-wf", event="schedule", check_suite_id=201),
        _wf("no-suite-downstream", event="workflow_run", check_suite_id=None),
    ]
    assert gate.scheduled_suites(wfs) == {200, 201}


def test_decide_positive_downstream_skipped_build_matrix_passes():
    """Bug (b): workflow_run/schedule job skips when matrix is empty; gate passes."""
    wf_nvidia = _wf(
        "nvidia-open-module",
        path=".github/workflows/nvidia-open-module.yml",
        event="workflow_run",
        conclusion="success",
        check_suite_id=500,
        run_id=50,
    )
    wf_ci = _wf("ci", ".github/workflows/ci.yml", run_id=60, check_suite_id=600)
    checks = [
        _check("Lint & Pytest Quality Gates", "success", run_id=1, check_suite=600),
        _check(
            "Build NVIDIA Open Module (${{ matrix.stream }})",
            "skipped",
            run_id=2,
            check_suite=500,
        ),
    ]
    state, desc = gate.decide(
        checks, [wf_nvidia, wf_ci], "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "success"
    assert desc == "2 check runs passed"


def test_decide_negative_planted_skipped_check_in_push_event_fails():
    """Planted (b): skipped check in a push event suite must still fail."""
    wf_nvidia = _wf(
        "nvidia-open-module",
        path=".github/workflows/nvidia-open-module.yml",
        event="push",
        conclusion="success",
        check_suite_id=500,
        run_id=50,
    )
    wf_ci = _wf("ci", ".github/workflows/ci.yml", run_id=60, check_suite_id=600)
    checks = [
        _check("Lint & Pytest Quality Gates", "success", run_id=1, check_suite=600),
        _check(
            "Build NVIDIA Open Module (${{ matrix.stream }})",
            "skipped",
            run_id=2,
            check_suite=500,
        ),
    ]
    state, desc = gate.decide(
        checks, [wf_nvidia, wf_ci], "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "failure"
    assert "Build NVIDIA Open Module (${{ matrix.stream }}): skipped" in desc


def test_decide_negative_downstream_workflow_failed_with_skipped_check():
    """Bug (b) negative: workflow_run failing still fails even with skipped check."""
    wf_nvidia = _wf(
        "nvidia-open-module",
        path=".github/workflows/nvidia-open-module.yml",
        event="workflow_run",
        conclusion="failure",
        check_suite_id=500,
        run_id=50,
    )
    wf_ci = _wf("ci", ".github/workflows/ci.yml", run_id=60, check_suite_id=600)
    checks = [
        _check("Lint & Pytest Quality Gates", "success", run_id=1, check_suite=600),
        _check(
            "Build NVIDIA Open Module (${{ matrix.stream }})",
            "skipped",
            run_id=2,
            check_suite=500,
        ),
    ]
    state, desc = gate.decide(
        checks, [wf_nvidia, wf_ci], "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "failure"
    assert "nvidia-open-module: failure" in desc


def test_decide_positive_superseded_workflow_run_failing_check_ignored():
    """Hardening (a): check runs from superseded workflow runs do not fail the gate."""
    wf_old = _wf("ci", ".github/workflows/ci.yml", run_id=10, check_suite_id=100)
    wf_new = _wf("ci", ".github/workflows/ci.yml", run_id=11, check_suite_id=101)
    checks = [
        _check("Old Job", "failure", run_id=1, check_suite=100),
        _check("New Job", "success", run_id=2, check_suite=101),
    ]
    state, desc = gate.decide(
        checks, [wf_old, wf_new], "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "success"
    assert desc == "1 check runs passed"


def test_decide_negative_planted_failing_check_in_newest_suite_fails():
    """Planted (a): failing check in newest run's suite fails."""
    wf_old = _wf("ci", ".github/workflows/ci.yml", run_id=10, check_suite_id=100)
    wf_new = _wf("ci", ".github/workflows/ci.yml", run_id=11, check_suite_id=101)
    checks = [
        _check("Old Job", "success", run_id=1, check_suite=100),
        _check("New Job", "failure", run_id=2, check_suite=101),
    ]
    state, desc = gate.decide(
        checks, [wf_old, wf_new], "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "failure"
    assert "New Job: failure" in desc


def test_decide_negative_planted_failing_check_without_check_suite_fails():
    """Planted (a): failing check with no check_suite key continues to fail."""
    wf = _wf("ci", ".github/workflows/ci.yml", run_id=10, check_suite_id=100)
    checks = [
        _check("Lint & Pytest Quality Gates", "success", run_id=1, check_suite=100),
        _check("Legacy Failing Check", "failure", run_id=2),
    ]
    state, desc = gate.decide(
        checks, [wf], "required-checks", ALLOW_SKIP, EXCLUDED, BOUND, NOW, IGNORED
    )
    assert state == "failure"
    assert "Legacy Failing Check: failure" in desc
