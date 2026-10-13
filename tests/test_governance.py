"""Automated verification suite for repository governance and project standards.

Validates presence of core governance contracts (AGENTS.md, LICENSE, README.md, etc.),
semantic versioning format in VERSION, linter and scanner configurations,
and required CI/CD workflows.
Complies with NASA/JPL Power of 10: short functions (<= 60 lines), checked assertions.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
EUPL_TITLE = "EUROPEAN UNION PUBLIC LICENCE v. 1.2"
APACHE_TEXT = "Apache License\nVersion 2.0, January 2004\n"


def licence_problems(text: str) -> list[str]:
    """Name what keeps a text from being the EUPL-1.2 licence text."""
    problems = []
    if not text.lstrip().startswith(EUPL_TITLE):
        problems.append(f"does not start with '{EUPL_TITLE}'")
    if "Apache License" in text:
        problems.append("holds Apache License terms")
    return problems


MILESTONE_KEYS = {"number", "title", "state", "due_on", "description"}
EPIC_KEYS = {"id", "issue", "title", "state", "milestone", "labels", "cadence", "description"}
FORGE_STATES = ("open", "closed")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object and refuse a key that is written twice."""
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"repeated key {repeated}")
    return dict(pairs)


def planning_problems(milestones_text: str, epics_text: str) -> list[str]:
    """Name what keeps the two planning files from being one forge mirror."""
    try:
        milestones = json.loads(milestones_text, object_pairs_hook=_unique_keys)["milestones"]
        epics = json.loads(epics_text, object_pairs_hook=_unique_keys)["epics"]
    except (ValueError, KeyError) as error:
        return [f"unreadable: {error}"]
    problems = []
    for row in milestones:
        if set(row) != MILESTONE_KEYS:
            problems.append(f"milestone {row.get('title')}: keys {sorted(row)}")
        elif row["state"] not in FORGE_STATES:
            problems.append(f"milestone {row['title']}: state '{row['state']}'")
    titles = [row.get("title") for row in milestones]
    for row in epics:
        if set(row) != EPIC_KEYS:
            problems.append(f"epic {row.get('id')}: keys {sorted(row)}")
            continue
        if row["state"] not in FORGE_STATES:
            problems.append(f"epic {row['id']}: state '{row['state']}'")
        if row["id"] != f"EPIC-{row['issue']:02d}":
            problems.append(f"epic {row['id']}: issue {row['issue']}")
        if row["milestone"] not in titles:
            problems.append(
                f"epic {row['id']}: milestone '{row['milestone']}' is not in milestones.json"
            )
        if "epic" not in row["labels"]:
            problems.append(f"epic {row['id']}: lacks the label 'epic'")
    for name, values in (
        ("milestone title", titles),
        ("epic id", [row.get("id") for row in epics]),
    ):
        if len(values) != len(set(values)):
            problems.append(f"{name} listed twice")
    return problems


class TestGovernanceIntegrity:
    def test_core_files_exist(self) -> None:
        """Verify all core configuration, governance, and manifest files exist."""
        required_files = [
            REPO_ROOT / "AGENTS.md",
            REPO_ROOT / "README.md",
            REPO_ROOT / "LICENSE",
            REPO_ROOT / "VERSION",
            REPO_ROOT / "SECURITY.md",
            REPO_ROOT / "CONTRIBUTING.md",
            REPO_ROOT / "CODE_OF_CONDUCT.md",
            REPO_ROOT / "GOVERNANCE.md",
            REPO_ROOT / "SUPPORT.md",
            REPO_ROOT / "MAINTAINERS.md",
            REPO_ROOT / "CHANGELOG.md",
            REPO_ROOT / "mkdocs.yml",
            REPO_ROOT / "Makefile",
            REPO_ROOT / "versions.json",
            REPO_ROOT / "versions.schema.json",
            REPO_ROOT / ".pre-commit-config.yaml",
            REPO_ROOT / ".yamllint.yml",
            REPO_ROOT / ".gitleaks.toml",
            REPO_ROOT / ".markdownlint.json",
            REPO_ROOT / ".codespellrc",
            REPO_ROOT / "release-please-config.json",
            REPO_ROOT / ".release-please-manifest.json",
            REPO_ROOT / "renovate.json",
            REPO_ROOT / "requirements-test.txt",
            REPO_ROOT / ".github" / "epics.json",
            REPO_ROOT / ".github" / "milestones.json",
            REPO_ROOT / ".github" / "CODEOWNERS",
            REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
            REPO_ROOT / "docs" / "principles.md",
            REPO_ROOT / "docs" / "onboarding.md",
            REPO_ROOT / "docs" / "streams.md",
            REPO_ROOT / "docs" / "patches.md",
            REPO_ROOT / "docs" / "packaging.md",
            REPO_ROOT / "docs" / "hardware" / "architectures.md",
            REPO_ROOT / "docs" / "hardware" / "amd-rocm.md",
            REPO_ROOT / "docs" / "hardware" / "intel-xe2.md",
            REPO_ROOT / "docs" / "hardware" / "nvidia-blackwell.md",
            REPO_ROOT / "docs" / "security" / "kspp-baseline.md",
            REPO_ROOT / "docs" / "security" / "cis-l2.md",
            REPO_ROOT / "docs" / "security" / "ebpf-sched-ext.md",
            REPO_ROOT / "docs" / "adr" / "README.md",
            REPO_ROOT / "scripts" / "audit-repository-health.sh",
        ]
        for file_path in required_files:
            assert file_path.exists(), f"Missing required file: {file_path}"
            assert file_path.stat().st_size > 0, f"File is empty: {file_path}"

    def test_version_file_semver(self) -> None:
        """Verify VERSION file adheres strictly to semantic versioning (X.Y.Z)."""
        version_path = REPO_ROOT / "VERSION"
        assert version_path.exists(), "VERSION file is missing"
        version_str = version_path.read_text(encoding="utf-8").strip()
        assert re.match(r"^\d+\.\d+\.\d+$", version_str), (
            f"VERSION '{version_str}' does not conform to semver format (X.Y.Z)"
        )

    def test_license_terms_and_copyright(self) -> None:
        """Verify LICENSE is the EUPL-1.2 text and README/mkdocs state licence and copyright."""
        license_path = REPO_ROOT / "LICENSE"
        assert license_path.exists(), "LICENSE file is missing"
        content = license_path.read_text(encoding="utf-8")
        assert licence_problems(content) == [], "LICENSE is not the EUPL-1.2 text"
        stored = (REPO_ROOT / "LICENSES" / "EUPL-1.2.txt").read_text(encoding="utf-8")
        assert content == stored, "LICENSE differs from LICENSES/EUPL-1.2.txt"

        readme_content = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "Lusoris Authors" in readme_content, "Missing Lusoris copyright in README.md"

        assert "EUPL-1.2" in readme_content, "README.md does not name the licence"
        assert "Apache%202.0" not in readme_content, "README.md still shows the Apache badge"

        mkdocs_content = (REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        assert "The Lusoris Authors" in mkdocs_content, "Missing Lusoris copyright in mkdocs.yml"
        assert "EUPL-1.2" in mkdocs_content, "mkdocs.yml does not name the licence"

    def test_license_check_refuses_another_licence_text(self) -> None:
        """Verify the licence check names an Apache text, an empty text and a text with both."""
        assert licence_problems(APACHE_TEXT) == [
            f"does not start with '{EUPL_TITLE}'",
            "holds Apache License terms",
        ]
        assert licence_problems("") == [f"does not start with '{EUPL_TITLE}'"]
        assert licence_problems(EUPL_TITLE + "\n" + APACHE_TEXT) == ["holds Apache License terms"]

    def test_planning_files_mirror_shape(self) -> None:
        """Verify milestones.json and epics.json carry forge fields and agree with each other."""
        milestones = (REPO_ROOT / ".github" / "milestones.json").read_text(encoding="utf-8")
        epics = (REPO_ROOT / ".github" / "epics.json").read_text(encoding="utf-8")
        assert planning_problems(milestones, epics) == []

    def test_planning_check_refuses_broken_mirrors(self) -> None:
        """Verify the planning check names a foreign state, an unknown milestone and a repeated key."""
        milestone = {
            "number": 1,
            "title": "one",
            "state": "open",
            "due_on": None,
            "description": "d",
        }
        epic = {"id": "EPIC-01", "issue": 1, "title": "t", "state": "open", "milestone": "one"}
        epic.update({"labels": ["epic"], "cadence": "Monthly", "description": "d"})

        def problems(milestone_row: dict, epic_row: dict) -> list[str]:
            return planning_problems(
                json.dumps({"milestones": [milestone_row]}), json.dumps({"epics": [epic_row]})
            )

        assert problems(milestone, epic) == []
        assert problems(milestone, {**epic, "state": "active"}) == ["epic EPIC-01: state 'active'"]
        assert problems(milestone, {**epic, "milestone": "two"}) == [
            "epic EPIC-01: milestone 'two' is not in milestones.json"
        ]
        assert problems(milestone, {**epic, "issue": 2}) == ["epic EPIC-01: issue 2"]
        assert problems(milestone, {**epic, "labels": ["kind/feature"]}) == [
            "epic EPIC-01: lacks the label 'epic'"
        ]
        assert problems({**milestone, "state": "active"}, epic) == ["milestone one: state 'active'"]
        without_due = {key: value for key, value in milestone.items() if key != "due_on"}
        assert problems(without_due, epic) == [
            "milestone one: keys ['description', 'number', 'state', 'title']"
        ]
        repeated = '{"milestones": [], "milestones": []}'
        assert planning_problems(repeated, '{"epics": []}') == [
            "unreadable: repeated key ['milestones']"
        ]

    def test_agent_directives_principles_citation(self) -> None:
        """Verify AGENTS.md references principles.md contract."""
        agents_md = REPO_ROOT / "AGENTS.md"
        assert agents_md.exists()
        agents_content = agents_md.read_text(encoding="utf-8")
        assert "docs/principles.md" in agents_content, "AGENTS.md must cite docs/principles.md"

    def test_required_github_workflows_present(self) -> None:
        """Verify all 12 mandatory GitHub Actions CI/CD workflows exist."""
        required_workflows = [
            "ci.yml",
            "required-aggregator.yml",
            "pr-project-gate.yml",
            "security-scans.yml",
            "scorecard.yml",
            "codeql.yml",
            "pages.yml",
            "release-please.yml",
            "supply-chain.yml",
            "build-matrix.yml",
            "publish-release.yml",
            "verify-requirements.yml",
        ]
        for wf_name in required_workflows:
            wf_file = WORKFLOWS_DIR / wf_name
            assert wf_file.exists(), f"Mandatory workflow missing: {wf_name}"
            assert wf_file.stat().st_size > 0, f"Workflow is empty: {wf_name}"

    def test_audit_repository_health_script_executable(self) -> None:
        """Verify audit-repository-health.sh is present and executable."""
        audit_script = REPO_ROOT / "scripts" / "audit-repository-health.sh"
        assert audit_script.exists(), "scripts/audit-repository-health.sh missing"
        assert audit_script.stat().st_mode & 0o111, (
            "scripts/audit-repository-health.sh not executable"
        )
