"""The two aggregate gates pass a skipped job only when a change detector excluded it.

required-aggregator.yml used to accept ``skipped`` for every check run, and the Security Scan
Gate accepted ``skipped`` for all four of its needs, including the change detector and the
Gitleaks history scan, which run on every event and must never skip.
"""

import os
import subprocess
import sys
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


def _security_gate(event: str, code: str, **results: str) -> subprocess.CompletedProcess:
    job = _workflow("security-scans.yml")["jobs"]["security-gate"]
    step = job["steps"][0]
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
    step = _workflow("security-scans.yml")["jobs"]["security-gate"]["steps"][0]
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


def _main_with_stub(tmp_path, lines: str, *extra: str) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "runs.jsonl").write_text(lines, encoding="utf-8")
    stub = bin_dir / "gh"
    stub.write_text(f"#!/usr/bin/env bash\ncat {tmp_path / 'runs.jsonl'}\n", encoding="utf-8")
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
    result = _main_with_stub(tmp_path, lines, "--interval", "1", "--timeout", "0")
    assert result.returncode == 1
    assert "pending" in result.stderr


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
