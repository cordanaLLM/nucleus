"""The two aggregate gates pass a skipped job only when a change detector excluded it.

required-aggregator.yml used to accept ``skipped`` for every check run, and the Security Scan
Gate accepted ``skipped`` for all four of its needs, including the change detector and the
Gitleaks history scan, which run on every event and must never skip.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
GATE_SCRIPT = REPO_ROOT / "scripts" / "required_checks_gate.py"
DETECTOR_GATED = {"Semgrep SAST", "Trivy Filesystem CVE Scan"}

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import required_checks_gate as gate  # noqa: E402


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


# Security Scan Gate ------------------------------------------------------------------------


def _security_gate_step() -> dict:
    """The gate's own step, the first after the draft step."""
    steps = _workflow("security-scans.yml")["jobs"]["security-gate"]["steps"]
    assert steps[0]["name"] == "Stop on a draft pull request"
    return steps[1]


def _security_gate(event: str, code: str, **results: str) -> subprocess.CompletedProcess:
    step = _security_gate_step()
    env = {"PATH": os.environ["PATH"], "EVENT_NAME": event, "CODE_CHANGED": code}
    for need in ("changes", "semgrep", "trivy", "gitleaks"):
        env[f"{need.upper()}_RESULT"] = results.get(need, "success")
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_the_security_gate_reads_the_detector_decision():
    step = _security_gate_step()
    assert step["env"]["EVENT_NAME"] == "${{ github.event_name }}"
    assert step["env"]["CODE_CHANGED"] == "${{ needs.changes.outputs.code }}"


@pytest.mark.parametrize(
    "event, code, results",
    [
        ("pull_request", "false", {"gitleaks": "skipped"}),
        ("pull_request", "false", {"changes": "skipped", "semgrep": "skipped", "trivy": "skipped"}),
        ("pull_request", "true", {"semgrep": "skipped"}),
        ("pull_request", "true", {"trivy": "skipped"}),
        ("push", "false", {"semgrep": "skipped", "trivy": "skipped"}),
        ("schedule", "", {"trivy": "skipped"}),
        ("pull_request", "false", {"changes": "failure", "semgrep": "skipped", "trivy": "skipped"}),
        ("pull_request", "true", {"semgrep": "failure"}),
        ("pull_request", "true", {"gitleaks": "cancelled"}),
    ],
)
def test_the_security_gate_refuses_a_skip_no_detector_decided(event, code, results):
    result = _security_gate(event, code, **results)
    assert result.returncode == 1, (event, code, results, result.stdout)
    assert "::error::" in result.stdout


@pytest.mark.parametrize(
    "event, code, results",
    [
        ("pull_request", "true", {}),
        ("push", "false", {}),
        ("pull_request", "false", {"semgrep": "skipped", "trivy": "skipped"}),
    ],
)
def test_the_security_gate_passes_success_and_detector_skips(event, code, results):
    result = _security_gate(event, code, **results)
    assert result.returncode == 0, (event, code, results, result.stdout)


# required-checks ---------------------------------------------------------------------------


def _run(name, conclusion, status="completed", run_id=1):
    return {"id": run_id, "name": name, "status": status, "conclusion": conclusion}


def test_the_newest_run_of_a_check_name_decides():
    rerun = [
        _run("Validate PR Milestone & Metadata", "failure", run_id=1),
        _run("Validate PR Milestone & Metadata", "success", run_id=2),
    ]
    assert gate.evaluate(gate.latest(rerun), "required-checks", set()) == []
    regressed = [
        _run("Validate PR Milestone & Metadata", "success", run_id=3),
        _run("Validate PR Milestone & Metadata", "failure", run_id=4),
    ]
    assert gate.evaluate(gate.latest(regressed), "required-checks", set()) == [
        "Validate PR Milestone & Metadata: failure"
    ]


def test_a_skipped_required_check_fails_the_aggregate():
    runs = [_run("Lint & Pytest Quality Gates", "skipped"), _run("REUSE lint", "success")]
    assert gate.evaluate(runs, "required-checks", DETECTOR_GATED) == [
        "Lint & Pytest Quality Gates: skipped"
    ]


@pytest.mark.parametrize(
    "conclusion", ["failure", "cancelled", "timed_out", "neutral", "action_required", "stale", None]
)
def test_every_conclusion_but_success_fails(conclusion):
    assert gate.evaluate([_run("CodeQL", conclusion)], "required-checks", DETECTOR_GATED)


def test_a_detector_gated_scan_may_be_skipped_but_not_fail():
    skipped = [_run("Semgrep SAST", "skipped"), _run("Trivy Filesystem CVE Scan", "skipped")]
    assert gate.evaluate(skipped, "required-checks", DETECTOR_GATED) == []
    failed = [_run("Semgrep SAST", "failure")]
    assert gate.evaluate(failed, "required-checks", DETECTOR_GATED) == ["Semgrep SAST: failure"]


def test_the_gate_ignores_only_itself():
    runs = [_run("required-checks", None, status="in_progress"), _run("REUSE lint", "success")]
    assert gate.evaluate(runs, "required-checks", set()) == []
    assert gate.pending(runs, "required-checks") == []


def test_no_other_check_run_is_a_failure():
    assert gate.evaluate([_run("required-checks", None, "in_progress")], "required-checks", set())


def _main_with_polls(tmp_path, polls: list[str], *extra: str) -> subprocess.CompletedProcess:
    """Run the gate against a gh stub that answers the n-th poll with polls[n], then the last."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for index, lines in enumerate(polls):
        (tmp_path / f"poll{index}.jsonl").write_text(lines, encoding="utf-8")
    counter = tmp_path / "count"
    counter.write_text("0", encoding="utf-8")
    stub = bin_dir / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"n=$(cat {counter}); echo $((n + 1)) >{counter}\n"
        f'[ "$n" -lt {len(polls)} ] || n={len(polls) - 1}\n'
        f'cat "{tmp_path}/poll$n.jsonl"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    args = [
        sys.executable,
        str(GATE_SCRIPT),
        "--repo",
        "o/r",
        "--ref",
        "abc",
        "--self",
        "required-checks",
    ]
    return subprocess.run([*args, *extra], capture_output=True, text=True, env=env, check=False)


def _main_with_stub(tmp_path, lines: str, *extra: str) -> subprocess.CompletedProcess:
    return _main_with_polls(tmp_path, [lines], "--interval", "0", *extra)


def test_the_script_fails_on_a_skipped_check_and_names_it(tmp_path):
    lines = '{"id":1,"name":"UKI Real ukify Build","status":"completed","conclusion":"skipped"}\n'
    result = _main_with_stub(tmp_path, lines, "--allow-skip", "Semgrep SAST")
    assert result.returncode == 1
    assert "::error::UKI Real ukify Build: skipped" in result.stderr


def test_the_script_passes_when_every_check_succeeded(tmp_path):
    lines = (
        '{"id":1,"name":"REUSE lint","status":"completed","conclusion":"success"}\n'
        '{"id":2,"name":"Semgrep SAST","status":"completed","conclusion":"skipped"}\n'
    )
    result = _main_with_stub(tmp_path, lines, "--allow-skip", "Semgrep SAST")
    assert result.returncode == 0, result.stderr


def test_the_script_fails_when_checks_stay_pending(tmp_path):
    lines = '{"id":1,"name":"REUSE lint","status":"in_progress","conclusion":null}\n'
    result = _main_with_stub(tmp_path, lines, "--timeout", "0")
    assert result.returncode == 1
    assert "pending" in result.stderr


# A forge status left at in_progress after the run finished (BACKLOG 2026-10-11: the Gitleaks
# check run on e685ec1 held the aggregator for its whole 30 minute timeout).
STALE = {"status": "in_progress", "conclusion": "success", "completed_at": "2026-10-11T19:00:00Z"}


@pytest.mark.parametrize(
    ("run", "done"),
    [
        pytest.param({**STALE}, True, id="stale-status-with-conclusion-and-time"),
        pytest.param({**STALE, "conclusion": None}, False, id="no-conclusion"),
        pytest.param({**STALE, "completed_at": None}, False, id="no-completion-time"),
        pytest.param(
            {**STALE, "status": "queued", "conclusion": None, "completed_at": None},
            False,
            id="queued",
        ),
        pytest.param({"status": "completed", "conclusion": "failure"}, True, id="completed"),
    ],
)
def test_a_run_counts_as_finished_by_status_or_by_conclusion_and_time(run, done):
    assert gate.finished({"id": 1, "name": "Gitleaks History Scan", **run}) is done


def test_the_script_does_not_wait_on_a_stale_status(tmp_path):
    lines = (
        '{"id":1,"name":"Gitleaks History Scan","status":"in_progress","conclusion":"success",'
        '"completed_at":"2026-10-11T19:00:00Z"}\n'
    )
    result = _main_with_stub(tmp_path, lines, "--timeout", "5")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_stale_status_still_reports_its_conclusion(tmp_path):
    lines = (
        '{"id":1,"name":"Gitleaks History Scan","status":"in_progress","conclusion":"failure",'
        '"completed_at":"2026-10-11T19:00:00Z"}\n'
    )
    result = _main_with_stub(tmp_path, lines, "--timeout", "5")
    assert result.returncode == 1
    assert "::error::Gitleaks History Scan: failure" in result.stderr


LINT_OK = '{"id":1,"name":"REUSE lint","status":"completed","conclusion":"success"}\n'
GATE_QUEUED = '{"id":2,"name":"Security Scan Gate","status":"queued","conclusion":null}\n'


@pytest.mark.parametrize(("late", "code"), [("failure", 1), ("success", 0)])
def test_a_check_that_registers_after_the_others_finished_is_waited_for(tmp_path, late, code):
    """A job that needs another one appears only once queued; one quiet poll must not pass."""
    gate_done = (
        f'{{"id":2,"name":"Security Scan Gate","status":"completed","conclusion":"{late}"}}\n'
    )
    polls = [LINT_OK, LINT_OK + GATE_QUEUED, LINT_OK + gate_done]
    result = _main_with_polls(tmp_path, polls, "--interval", "0", "--timeout", "5")
    assert result.returncode == code, result.stdout + result.stderr
    assert "Security Scan Gate" in result.stdout


def test_the_aggregator_runs_the_gate_and_allows_only_detector_gated_skips():
    job = _workflow("required-aggregator.yml")["jobs"]["required-checks"]
    text = yaml.safe_dump(job)
    assert "allowed-conclusions" not in text, "no blanket list of accepted conclusions"
    run = next(
        step["run"] for step in job["steps"] if "required_checks_gate.py" in step.get("run", "")
    )
    allowed = {part.split('"')[1] for part in run.split("--allow-skip")[1:]}
    assert allowed == DETECTOR_GATED


def test_the_allowed_skips_are_exactly_the_detector_gated_jobs_the_security_gate_judges():
    jobs = _workflow("security-scans.yml")["jobs"]
    gated = {j["name"] for j in jobs.values() if "needs.changes.outputs" in str(j.get("if", ""))}
    assert gated == DETECTOR_GATED
    names = {key: j["name"] for key, j in jobs.items()}
    assert {names[n] for n in jobs["security-gate"]["needs"]} >= DETECTOR_GATED


# The aggregator of runs 39004397349 and 39004953382 waited on build legs the forge had not
# started; the gate's timeout must be a wall-clock deadline that a slow or hung gh api cannot
# stretch, and the failure must say what it waited for.
PENDING = '{"id":1,"name":"Build Kernel (lts / arm64)","status":"queued","conclusion":null}\n'


def _main_with_gh(tmp_path, body: str, *extra: str) -> tuple[subprocess.CompletedProcess, float]:
    """Run the gate against a gh stub with the given shell body; return the result and seconds."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "poll.jsonl").write_text(PENDING, encoding="utf-8")
    stub = bin_dir / "gh"
    stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    argv = [sys.executable, str(GATE_SCRIPT), "--repo", "o/r", "--ref", "abc",
            "--self", "required-checks", *extra]  # fmt: skip
    start = time.monotonic()
    result = subprocess.run(argv, capture_output=True, text=True, env=env, check=False, timeout=60)
    return result, time.monotonic() - start


def test_a_check_that_never_finishes_fails_at_the_deadline_and_is_named(tmp_path):
    result, seconds = _main_with_gh(
        tmp_path, f'cat "{tmp_path}/poll.jsonl"', "--interval", "1", "--timeout", "3"
    )
    assert result.returncode == 1
    assert "::error::deadline of 3s passed; still pending: Build Kernel (lts / arm64)" in (
        result.stderr
    )
    assert seconds < 6, seconds


def test_slow_polls_count_against_the_deadline(tmp_path):
    """Four polls of two seconds each would take 12 s; the deadline stops the gate at 3 s."""
    result, seconds = _main_with_gh(
        tmp_path, f'sleep 2; cat "{tmp_path}/poll.jsonl"', "--interval", "1", "--timeout", "3"
    )
    assert result.returncode == 1 and "still pending" in result.stderr
    assert seconds < 6, seconds


def test_a_hung_gh_api_call_is_killed_and_the_gate_fails_closed(tmp_path):
    result, seconds = _main_with_gh(tmp_path, "exec sleep 600", "--interval", "1", "--timeout", "3")
    assert result.returncode == 1
    assert "gh api did not answer within" in result.stderr
    assert seconds < 8, seconds


def test_a_failing_gh_api_call_is_retried_then_named(tmp_path):
    result, _ = _main_with_gh(
        tmp_path, 'echo "HTTP 502: Bad Gateway" >&2; exit 1', "--interval", "1", "--timeout", "2"
    )
    assert result.returncode == 1
    assert "gh api failed (exit 1): HTTP 502: Bad Gateway" in result.stderr
    assert result.stdout.count("poll failed") >= 2
