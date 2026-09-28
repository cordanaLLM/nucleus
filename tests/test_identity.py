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

PRE_MOVE_IDENTITY = re.compile(
    r"github\.com/lusoris/|lusoris\.github\.io|ghcr\.io/lusoris|lusoris-kernel-forge",
    re.IGNORECASE,
)

# Files that name a pre-move identity on purpose: release history, the Accepted
# and therefore immutable ADR-0004, the ADR that supersedes its registry path,
# and this test, which spells the patterns out.
HISTORICAL_RECORDS = frozenset(
    {
        "CHANGELOG.md",
        "docs/adr/0004-native-debian-and-uki-dual-packaging.md",
        "docs/adr/0006-oci-registry-namespace.md",
        "tests/test_identity.py",
    }
)

# The canonical identity each identity-bearing file must carry. The owner in
# REPO_OWNER is a bare word the pattern above cannot catch without matching the
# maintainer handle and the -lusoris1 localversion, so it is pinned here.
CANONICAL_IDENTITIES = {
    "scripts/setup-remote-repository.sh": 'REPO_OWNER="cordanaLLM"',
    "mkdocs.yml": "site_url: https://cordanallm.github.io/nucleus/",
    "docker/Dockerfile.builder": (
        'org.opencontainers.image.source="https://github.com/cordanaLLM/nucleus"'
    ),
    "docs/packaging.md": "oras push ghcr.io/cordanallm/nucleus/kernels/",
    "docs/adr/0006-oci-registry-namespace.md": "`ghcr.io/cordanallm/nucleus/kernels`",
}


def _tracked_files() -> list[str]:
    """Return the repository's tracked paths; fail if git cannot list them."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return [name for name in result.stdout.decode("utf-8").split("\0") if name]


def test_no_pre_move_identity_in_tracked_files() -> None:
    """No tracked file outside the historical records names a pre-move identity."""
    tracked = _tracked_files()
    assert tracked, "git ls-files returned no tracked files"
    violations = []
    for name in tracked:
        path = REPO_ROOT / name
        if name in HISTORICAL_RECORDS or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            if PRE_MOVE_IDENTITY.search(line):
                violations.append(f"{name}:{number}: {line.strip()}")
    assert not violations, "Pre-move identities found:\n" + "\n".join(violations)


def test_identity_bearing_files_name_the_canonical_identity() -> None:
    """The repository, site, source label and registry path name cordanaLLM/nucleus."""
    for name, expected in CANONICAL_IDENTITIES.items():
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert expected in text, f"{name} does not contain {expected!r}"
