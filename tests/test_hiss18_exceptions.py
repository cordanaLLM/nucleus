""".standards.yaml: a HISS-18 exception that says no job is required holds against the ruleset.

The engine's HISS-18 check reports a job that needs a job held back on a draft, because GitHub
reports the skipped job as successful and a required check accepts that. An exception whose
reason states that no job of its workflow is a required check rests on that claim; this suite
reads the job names of each such workflow and refuses one the branch ruleset requires.
"""

import json
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
STANDARDS = REPO_ROOT / ".standards.yaml"
RULESET = REPO_ROOT / ".github" / "rulesets" / "main.json"
CLAIM = "no job of this workflow is a required check"


def required_contexts(ruleset):
    """Every status check context the ruleset requires."""
    return {
        check["context"]
        for rule in ruleset["rules"]
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    }


def job_names(workflow):
    """Each job's check name; a matrix name keeps the text before its first expression and its
    opening parenthesis."""
    names = set()
    for key, job in workflow["jobs"].items():
        name = str(job.get("name", key))
        names.add(name.split("${{", 1)[0].rstrip(" ("))
    return names


def claimed_workflows(standards):
    """Workflow paths of the HISS-18 exceptions whose reason makes the claim."""
    return [
        entry["path"]
        for entry in standards["exceptions"]
        if entry["rule"] == "HISS-18" and CLAIM in " ".join(entry["reason"].split())
    ]


def required_jobs(workflow, contexts):
    """Job names of a workflow that a required context names or begins with."""
    return sorted(
        name
        for name in job_names(workflow)
        for context in contexts
        if context == name or (name and context.startswith(name + " ("))
    )


def load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_claim_is_made_by_the_two_matrix_workflows():
    claimed = claimed_workflows(load_yaml(STANDARDS))
    assert sorted(claimed) == [
        ".github/workflows/build-matrix.yml",
        ".github/workflows/package-lifecycle.yml",
    ]


def test_no_claimed_workflow_has_a_required_job():
    contexts = required_contexts(json.loads(RULESET.read_text(encoding="utf-8")))
    for path in claimed_workflows(load_yaml(STANDARDS)):
        assert required_jobs(load_yaml(REPO_ROOT / path), contexts) == [], path


@pytest.mark.parametrize(
    "context",
    [
        "Build NVIDIA Open Module (bleeding)",
        "Build OpenZFS Module (mainstream)",
        "Plan Out-of-Tree Module Build Matrices",
    ],
)
def test_a_planted_required_job_is_refused(context):
    workflow = load_yaml(REPO_ROOT / ".github" / "workflows" / "build-matrix.yml")
    assert required_jobs(workflow, {"Praetor Governance", context}) != []


def test_a_job_whose_name_only_shares_a_prefix_is_not_required():
    workflow = {"jobs": {"build": {"name": "Build"}}}
    assert required_jobs(workflow, {"Build Kernel (lts / x86_64)"}) == []
