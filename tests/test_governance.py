"""Automated verification suite for repository governance and project standards.

Validates presence of core governance contracts (AGENTS.md, LICENSE, README.md, etc.),
semantic versioning format in VERSION, linter and scanner configurations,
and required CI/CD workflows.
Complies with NASA/JPL Power of 10: short functions (<= 60 lines), checked assertions.
"""

from pathlib import Path
import os
import re
import subprocess

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


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
        """Verify LICENSE file contains Apache 2.0 terms and README/mkdocs state copyright."""
        license_path = REPO_ROOT / "LICENSE"
        assert license_path.exists(), "LICENSE file is missing"
        content = license_path.read_text(encoding="utf-8")
        assert "Apache License" in content, "Missing Apache License terms in LICENSE"
        assert "Version 2.0" in content, "Missing Version 2.0 declaration in LICENSE"

        readme_content = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "Lusoris Authors" in readme_content, "Missing Lusoris copyright in README.md"

        mkdocs_content = (REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        assert "The Lusoris Authors" in mkdocs_content, "Missing Lusoris copyright in mkdocs.yml"

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
        assert audit_script.stat().st_mode & 0o111, "scripts/audit-repository-health.sh not executable"

