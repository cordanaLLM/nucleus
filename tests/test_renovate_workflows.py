# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Validation that GitHub release downloads in workflows are managed by Renovate.

Renovate regex customManagers in renovate.json use github-release-attachments to match
pinned release version and digest variable pairs in .github/workflows/*.yml so that
pinned tools receive automated version and sha256 update pull requests together.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
RENOVATE_JSON = REPO_ROOT / "renovate.json"

HEX_DIGEST_64_RE = re.compile(r"^[a-f0-9]{64}$")


def _load_renovate_release_attachment_matchers() -> list[re.Pattern[str]]:
    """Compile customManager matchStrings from renovate.json for github-release-attachments.

    Translates JavaScript RegExp named groups (?<name>...) to Python (?P<name>...).
    Filters strictly by datasourceTemplate in {'github-release-attachments'}.
    """
    assert RENOVATE_JSON.is_file(), f"Missing {RENOVATE_JSON}"
    config = json.loads(RENOVATE_JSON.read_text(encoding="utf-8"))
    matchers: list[re.Pattern[str]] = []

    for manager in config.get("customManagers", []):
        if manager.get("customType") != "regex":
            continue
        if manager.get("datasourceTemplate") in {"github-release-attachments"}:
            for pattern in manager.get("matchStrings", []):
                py_pattern = re.sub(r"\?<([a-zA-Z0-9_]+)>", r"?P<\1>", pattern)
                matchers.append(re.compile(py_pattern))

    return matchers


def _load_renovate_release_attachment_managers() -> list[list[re.Pattern[str]]]:
    """Compile matchers grouped by customManager from renovate.json for github-release-attachments."""
    assert RENOVATE_JSON.is_file(), f"Missing {RENOVATE_JSON}"
    config = json.loads(RENOVATE_JSON.read_text(encoding="utf-8"))
    managers: list[list[re.Pattern[str]]] = []

    for manager in config.get("customManagers", []):
        if manager.get("customType") != "regex":
            continue
        if manager.get("datasourceTemplate") in {"github-release-attachments"}:
            patterns = [
                re.compile(re.sub(r"\?<([a-zA-Z0-9_]+)>", r"?P<\1>", pattern))
                for pattern in manager.get("matchStrings", [])
            ]
            if patterns:
                managers.append(patterns)

    return managers


def test_renovate_custom_manager_exists_for_github_release_attachments():
    """Ensure renovate.json contains regex customManagers for github-release-attachments."""
    matchers = _load_renovate_release_attachment_matchers()
    assert len(matchers) >= 2, (
        f"Expected at least 2 github-release-attachments customManager matchStrings in renovate.json, got {len(matchers)}"
    )


def test_every_workflow_file_with_release_download_is_managed():
    """Assert every workflow containing release downloads is covered by matching Renovate managers.

    Per workflow file, requires the number of distinct release-download URLs
    (occurrences of 'releases/download/') <= number of matching managers (one manager may match a file once).
    """
    managers = _load_renovate_release_attachment_managers()
    assert managers, "No github-release-attachments customManager matchers found in renovate.json"

    workflows = sorted(WORKFLOWS_DIR.glob("*.yml"))
    target_workflows = [
        wf for wf in workflows if "releases/download/" in wf.read_text(encoding="utf-8")
    ]
    assert target_workflows, "Expected at least one workflow containing 'releases/download/'"

    for wf in workflows:
        content = wf.read_text(encoding="utf-8")
        distinct_urls = set(
            re.findall(r"https?://[^\s\"'\\]*releases/download/[^\s\"'\\]*", content)
        )
        download_count = (
            len(distinct_urls) if distinct_urls else content.count("releases/download/")
        )

        # One manager may match a file once
        matching_managers: list[re.Match[str]] = []
        for manager_patterns in managers:
            for pattern in manager_patterns:
                m = pattern.search(content)
                if m:
                    matching_managers.append(m)
                    break

        assert download_count <= len(matching_managers), (
            f"Workflow {wf.name} has {download_count} distinct release-download URL(s) "
            f"but only {len(matching_managers)} matching Renovate manager(s)"
        )

        for match in matching_managers:
            group_dict = match.groupdict()
            assert "currentValue" in group_dict, f"Named group 'currentValue' missing in {wf.name}"
            assert "currentDigest" in group_dict, (
                f"Named group 'currentDigest' missing in {wf.name}"
            )
            assert match.group("currentValue"), f"'currentValue' is empty in {wf.name}"
            assert HEX_DIGEST_64_RE.fullmatch(match.group("currentDigest")), (
                f"'currentDigest' is not a 64-hex string in {wf.name}: {match.group('currentDigest')}"
            )


def test_actionlint_match_yields_version_and_digest():
    """Ensure the customManager regex captures currentValue v1.7.12 and a 64-hex digest from ci.yml."""
    matchers = _load_renovate_release_attachment_matchers()
    ci_workflow = WORKFLOWS_DIR / "ci.yml"
    assert ci_workflow.is_file(), f"Missing {ci_workflow}"
    content = ci_workflow.read_text(encoding="utf-8")

    matching = [m for matcher in matchers if (m := matcher.search(content))]
    assert matching, (
        f"{ci_workflow.name} did not match any github-release-attachments customManager pattern"
    )

    match = matching[0]
    group_dict = match.groupdict()
    assert "currentValue" in group_dict, f"Named group 'currentValue' missing in {group_dict}"
    assert "currentDigest" in group_dict, f"Named group 'currentDigest' missing in {group_dict}"

    assert match.group("currentValue") == "v1.7.12"
    digest = match.group("currentDigest")
    assert HEX_DIGEST_64_RE.fullmatch(digest), (
        f"currentDigest must be a 64-hex sha256 string, got {digest}"
    )
    assert digest == "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"


def test_gitleaks_match_yields_version_and_digest():
    """Ensure the customManager regex captures currentValue v8.30.1 and a 64-hex digest from security-scans.yml."""
    matchers = _load_renovate_release_attachment_matchers()
    sec_workflow = WORKFLOWS_DIR / "security-scans.yml"
    assert sec_workflow.is_file(), f"Missing {sec_workflow}"
    content = sec_workflow.read_text(encoding="utf-8")

    matching = [m for matcher in matchers if (m := matcher.search(content))]
    assert matching, (
        f"{sec_workflow.name} did not match any github-release-attachments customManager pattern"
    )

    match = matching[0]
    group_dict = match.groupdict()
    assert "currentValue" in group_dict, f"Named group 'currentValue' missing in {group_dict}"
    assert "currentDigest" in group_dict, f"Named group 'currentDigest' missing in {group_dict}"

    assert match.group("currentValue") == "v8.30.1"
    digest = match.group("currentDigest")
    assert HEX_DIGEST_64_RE.fullmatch(digest), (
        f"currentDigest must be a 64-hex sha256 string, got {digest}"
    )
    assert digest == "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"


def test_planted_negative_version_without_digest_fails():
    """A workflow text with actionlint_version but no digest line fails the matcher."""
    matchers = _load_renovate_release_attachment_matchers()
    text = (
        "actionlint_version=v1.7.12\n"
        "curl -sSfL -o actionlint.tar.gz https://github.com/rhysd/actionlint/releases/download/${actionlint_version}/actionlint_linux_amd64.tar.gz\n"
    )
    assert not any(matcher.search(text) for matcher in matchers), (
        "Planted negative failed: text without actionlint_sha256 unexpectedly matched"
    )


def test_planted_negative_different_variable_name_fails():
    """A workflow text with a different variable name fails the matcher."""
    matchers = _load_renovate_release_attachment_matchers()
    text = (
        "other_tool_version=v1.7.12\n"
        "other_tool_sha256=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8\n"
    )
    assert not any(matcher.search(text) for matcher in matchers), (
        "Planted negative failed: text with different variable names unexpectedly matched"
    )


@pytest.mark.parametrize(
    "invalid_text",
    [
        "actionlint_version=v1.7.12\n",
        "actionlint_sha256=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8\n",
        "actionlint_ver=v1.7.12\nactionlint_sha256=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8\n",
        "actionlint_version=v1.7.12\nother_sha256=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8\n",
        "actionlint_version=v1.7.12\nactionlint_sha256=8aca8db9\n",
        "gitleaks_version=v8.30.1\n",
        "gitleaks_sha256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb\n",
        "gitleaks_ver=v8.30.1\ngitleaks_sha256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb\n",
        "gitleaks_version=v8.30.1\nother_sha256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb\n",
        "gitleaks_version=v8.30.1\ngitleaks_sha256=551f6fc8\n",
    ],
)
def test_planted_negatives_parametrized(invalid_text: str):
    """Ensure invalid or incomplete variable lines are refused by all matchers."""
    matchers = _load_renovate_release_attachment_matchers()
    assert not any(matcher.search(invalid_text) for matcher in matchers), (
        f"Planted negative failed: invalid text unexpectedly matched: {invalid_text!r}"
    )
