"""verify-requirements.yml reads every fetched document through the owner's schema (docs/adr/0010).

The behaviour is in tests/test_requirement_schema.py; this file holds the wiring: the schema is
fetched and proven before any document is read through it, the check runs before the verifier's
verdict, event data stays out of run blocks, and every touched path re-runs the workflow.
"""

from pathlib import Path

import yaml

WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "verify-requirements.yml"
)
PARSED = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
STEPS = PARSED["jobs"]["verify"]["steps"]
NAMES = [step.get("name", "") for step in STEPS]
BY_NAME = {step.get("name", ""): step for step in STEPS}
TRIGGERS = PARSED[True]  # PyYAML reads the key `on` as the boolean True


def test_the_schema_is_fetched_and_checked_before_the_verifier_decides():
    fetched = NAMES.index("Fetch Requirement Documents")
    schema = NAMES.index("Fetch the Owner Schema")
    check = NAMES.index("Validate the Documents Against the Owner Schema")
    verify = NAMES.index("Verify Requirements Against the Declared KConfig")
    plan = NAMES.index("Plan the Resolved Evidence")
    assert fetched < schema < check < verify < plan


def test_the_schema_fetch_takes_its_pin_from_versions_json_and_writes_one_file():
    step = BY_NAME["Fetch the Owner Schema"]
    assert step["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert "scripts/check_requirement_schema.py fetch --out=requirements/schema.json" in step["run"]
    assert "commit" not in step["run"] and "sha256" not in step["run"], "the pin is versions.json's"


def test_the_check_reads_every_fetched_document_and_keeps_its_report():
    step = BY_NAME["Validate the Documents Against the Owner Schema"]
    run = step["run"]
    assert "--schema=requirements/schema.json" in run
    assert "requirements/args" in run and "^--requirement=" in run, "the documents fetched"
    assert "| tee requirements/schema-report.txt" in run
    assert "${{" not in run and "env" not in step, "no event data reaches this step"
    assert "requirements/schema-report.txt" in BY_NAME["Upload Verification Report"]["with"]["path"]


def test_every_run_block_of_the_verifying_jobs_is_strict():
    """A failing schema check must fail its step even through tee."""
    for job in ("verify", "verify-resolved"):
        assert PARSED["jobs"][job]["defaults"]["run"]["shell"] == "bash"
        for step in PARSED["jobs"][job]["steps"]:
            if "run" in step:
                first = step["run"].strip().splitlines()[0]
                assert first == "set -euo pipefail", f"{job}: {step['name']}"


def test_the_suite_that_holds_the_schema_runs_before_its_verdict_is_trusted():
    run = BY_NAME["Test the Verifier"]["run"]
    assert "tests/test_requirement_schema.py" in run
    assert NAMES.index("Test the Verifier") < NAMES.index("Fetch the Owner Schema")


def test_a_change_to_the_check_the_pin_or_the_schema_re_runs_the_workflow():
    for trigger in ("pull_request", "push"):
        paths = TRIGGERS[trigger]["paths"]
        for path in (
            "scripts/check_requirement_schema.py",
            "tests/test_requirement_schema.py",
            "tests/fixtures/kernel-requirement/**",
            "versions.json",
            "versions.schema.json",
        ):
            assert path in paths, f"{trigger}: {path}"
