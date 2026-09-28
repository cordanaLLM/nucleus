# Copyright 2026 The Lusoris Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Repository, site and registry identities name cordanaLLM/nucleus.

The repository moved from the maintainer's user account to the cordanaLLM
organization. A tracked file that still names the old repository, site or
registry sends the first build or publish that follows it to the wrong
identity (issue #28). ADR-0006 decides the registry path.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The pre-move identities: the old owner's repositories, site and registry
# namespace, the nucleus repository in owner/name form, and the repository's
# pre-move name. They are built from parts, so this file does not spell them
# out and needs no exemption from its own scan.
_OLD_OWNER = "lusoris"
PRE_MOVE_IDENTITY = re.compile(
    "|".join(
        (
            re.escape(f"github.com/{_OLD_OWNER}/"),
            re.escape(f"{_OLD_OWNER}.github.io"),
            re.escape(f"ghcr.io/{_OLD_OWNER}"),
            rf"(?<![\w/]){_OLD_OWNER}/nucleus\b",
            re.escape(f"{_OLD_OWNER}-kernel-forge"),
        )
    ),
    re.IGNORECASE,
)

# Files that name a pre-move identity on purpose, with the number of lines that
# do. The count keeps the exemption exact: one more such line in these files
# fails the scan as it would anywhere else. ADR-0004 is Accepted and therefore
# immutable; ADR-0006 names the path it supersedes and the patterns scanned for.
HISTORICAL_MENTIONS = {
    "docs/adr/0004-native-debian-and-uki-dual-packaging.md": 2,
    "docs/adr/0006-oci-registry-namespace.md": 4,
}

# Release history. release-please writes it from commit subjects, which quote
# pre-move names such as the one the repository was bootstrapped under, so any
# fixed count would fail the release pull request that adds such an entry.
UNSCANNED = frozenset({"CHANGELOG.md"})

SITE_URL = "https://cordanallm.github.io/nucleus/"
REPOSITORY_URL = "https://github.com/cordanaLLM/nucleus"


def _shell_assignment(name: str) -> str:
    """Return a pattern capturing every value a shell script assigns to name."""
    keyword = r"(?:(?:export|readonly|local|declare(?:\s+-\w+)*)\s+)?"
    return rf"^\s*{keyword}{name}=[\"']?([^\"'\s]*)"


SETUP_SCRIPT = "scripts/setup-remote-repository.sh"

# Each identity-bearing setting: its file, a label, a pattern capturing every
# value the file gives it, and the one value allowed. All captured values are
# compared, not only the presence of the canonical one: a second REPO_OWNER
# assignment below the canonical line would pass a presence check and still
# make the setup script create the repository under another owner.
CANONICAL_VALUES = (
    (SETUP_SCRIPT, "REPO_OWNER", _shell_assignment("REPO_OWNER"), "cordanaLLM"),
    (SETUP_SCRIPT, "REPO_NAME", _shell_assignment("REPO_NAME"), "nucleus"),
    (SETUP_SCRIPT, "FULL_REPO", _shell_assignment("FULL_REPO"), "${REPO_OWNER}/${REPO_NAME}"),
    (SETUP_SCRIPT, "--homepage", r"--homepage[=\s]+[\"']?([^\"'\s]+)", SITE_URL),
    ("mkdocs.yml", "site_url", r"^site_url:\s*[\"']?([^\"'\s]+)", SITE_URL),
    ("mkdocs.yml", "repo_url", r"^repo_url:\s*[\"']?([^\"'\s]+)", REPOSITORY_URL),
    ("mkdocs.yml", "repo_name", r"^repo_name:\s*[\"']?([^\"'\s]+)", "cordanaLLM/nucleus"),
    (
        "docker/Dockerfile.builder",
        "org.opencontainers.image.source",
        r"org\.opencontainers\.image\.source=[\"']?([^\"'\s]+)",
        REPOSITORY_URL,
    ),
    (
        "docs/packaging.md",
        "every ghcr.io reference, cut after its third path segment",
        r"ghcr\.io/([\w.-]+(?:/[\w.-]+){0,2})",
        "cordanallm/nucleus/kernels",
    ),
    (
        "docs/adr/0006-oci-registry-namespace.md",
        "the decided path",
        r"^UKI OCI artifacts are published under `ghcr\.io/([^`]+)`",
        "cordanallm/nucleus/kernels",
    ),
)


def _tracked_files() -> list[str]:
    """Return the repository's tracked paths; fail if git cannot list them."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return [name for name in result.stdout.decode("utf-8").split("\0") if name]


def _pre_move_lines(text: str) -> list[str]:
    """Return "number: line" for every line of text that names a pre-move identity."""
    return [
        f"{number}: {line.strip()}"
        for number, line in enumerate(text.splitlines(), start=1)
        if PRE_MOVE_IDENTITY.search(line)
    ]


def test_no_pre_move_identity_in_tracked_files() -> None:
    """No tracked file names a pre-move identity beyond its counted historical lines."""
    tracked = _tracked_files()
    assert tracked, "git ls-files returned no tracked files"
    missing = sorted(set(HISTORICAL_MENTIONS) - set(tracked))
    assert not missing, f"Historical records are no longer tracked: {missing}"
    violations = []
    for name in tracked:
        path = REPO_ROOT / name
        if name in UNSCANNED or not path.is_file():
            continue
        hits = _pre_move_lines(path.read_text(encoding="utf-8", errors="ignore"))
        expected = HISTORICAL_MENTIONS.get(name, 0)
        if len(hits) != expected:
            violations.append(f"{name}: {len(hits)} lines, {expected} expected")
            violations.extend(f"  {name}:{hit}" for hit in hits)
    assert not violations, "Pre-move identities found:\n" + "\n".join(violations)


def test_identity_bearing_files_name_the_canonical_identity() -> None:
    """Each identity-bearing setting holds the canonical value and no other."""
    mismatches = []
    for name, label, pattern, expected in CANONICAL_VALUES:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        found = sorted(set(re.findall(pattern, text, re.MULTILINE)))
        if found != [expected]:
            mismatches.append(f"{name} ({label}): found {found}, expected [{expected!r}]")
    assert not mismatches, "Identity settings differ:\n" + "\n".join(mismatches)
