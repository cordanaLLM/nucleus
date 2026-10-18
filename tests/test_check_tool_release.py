# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for scripts/check_tool_release.py."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_tool_release import check_tag_syntax

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_tool_release.py"


def _run(args: list[str], repo_root: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT)] + args
    cwd = repo_root or REPO_ROOT
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=120)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)


def _write_release_files(
    repo: Path, version: str, manifest_version: str, changelog_version: str, package_name: str
) -> None:
    (repo / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (repo / ".release-please-manifest.json").write_text(
        json.dumps({".": manifest_version}, indent=2) + "\n", encoding="utf-8"
    )
    (repo / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{changelog_version}](https://example.com) (2026-10-18)\n\n"
        "### Features\n* feature\n",
        encoding="utf-8",
    )
    package = {
        "release-type": "simple",
        "package-name": package_name,
        "changelog-path": "CHANGELOG.md",
        "bump-minor-pre-major": True,
        "include-component-in-tag": True,
        "version-file": "VERSION",
    }
    (repo / "release-please-config.json").write_text(
        json.dumps({"packages": {".": package}}, indent=2) + "\n", encoding="utf-8"
    )
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        REPO_ROOT / "scripts" / "resolve_release_tag.py", scripts_dir / "resolve_release_tag.py"
    )
    shutil.copy2(REPO_ROOT / "versions.json", repo / "versions.json")


def init_repo(
    tmp_path: Path,
    version: str = "0.4.0",
    manifest_version: str = "0.4.0",
    changelog_version: str = "0.4.0",
    package_name: str = "nucleus",
) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test Committer")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "tag.gpgSign", "false")
    _write_release_files(repo, version, manifest_version, changelog_version, package_name)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"chore: release {version}")
    _git(repo, "tag", f"{package_name}-v{version}")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def _make_release_json(
    path: Path,
    tag: str = "nucleus-v0.4.0",
    is_draft: bool = False,
    is_prerelease: bool = False,
    target_commitish: str = "main",
    assets: list[dict[str, object]] | None = None,
) -> Path:
    data = {
        "tagName": tag,
        "isDraft": is_draft,
        "isPrerelease": is_prerelease,
        "targetCommitish": target_commitish,
        "assets": assets if assets is not None else [],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_positive_real_shaped_tag_passes(tmp_path: Path):
    repo = init_repo(tmp_path)
    result = _run(["--tag", "nucleus-v0.4.0", "--repo-root", str(repo)])
    assert result.returncode == 0, result.stderr


def test_positive_with_valid_release_json_passes(tmp_path: Path):
    repo = init_repo(tmp_path)
    release_json = _make_release_json(tmp_path / "release.json", tag="nucleus-v0.4.0")
    result = _run(
        [
            "--tag",
            "nucleus-v0.4.0",
            "--repo-root",
            str(repo),
            "--release-json",
            str(release_json),
        ]
    )
    assert result.returncode == 0, result.stderr


def test_refuse_version_mismatch(tmp_path: Path):
    repo = init_repo(tmp_path, version="0.3.0")
    # Tag it as nucleus-v0.4.0 despite VERSION holding 0.3.0
    subprocess.run(
        ["git", "tag", "-f", "nucleus-v0.4.0"], cwd=repo, check=True, capture_output=True
    )
    result = _run(["--tag", "nucleus-v0.4.0", "--repo-root", str(repo)])
    assert result.returncode == 1
    assert "VERSION mismatch" in result.stderr


def test_refuse_manifest_mismatch(tmp_path: Path):
    repo = init_repo(tmp_path, manifest_version="0.3.0")
    result = _run(["--tag", "nucleus-v0.4.0", "--repo-root", str(repo)])
    assert result.returncode == 1
    assert "manifest mismatch" in result.stderr


def test_refuse_changelog_heading_mismatch(tmp_path: Path):
    repo = init_repo(tmp_path, changelog_version="0.3.0")
    result = _run(["--tag", "nucleus-v0.4.0", "--repo-root", str(repo)])
    assert result.returncode == 1
    assert "CHANGELOG heading mismatch" in result.stderr


def test_refuse_tag_not_on_origin_main(tmp_path: Path):
    repo = init_repo(tmp_path)
    # Move origin/main backwards to an orphaned or parent commit
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "feature on main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "side-branch", "HEAD~1"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "feature on side branch"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-f", "nucleus-v0.4.0"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    result = _run(["--tag", "nucleus-v0.4.0", "--repo-root", str(repo)])
    assert result.returncode == 1
    assert "tag not on origin/main" in result.stderr


def test_refuse_malformed_tag(tmp_path: Path):
    repo = init_repo(tmp_path)
    result = _run(["--tag", "nucleus-v0.4", "--repo-root", str(repo)])
    assert result.returncode == 1
    assert "malformed tag" in result.stderr


def test_refuse_kernel_shaped_tag(tmp_path: Path):
    repo = init_repo(tmp_path)
    result = _run(["--tag", "v7.2.10-mainstream-lusoris1", "--repo-root", str(repo)])
    assert result.returncode == 1
    assert "resolve_release_tag" in result.stderr


def test_refuse_release_json_draft(tmp_path: Path):
    repo = init_repo(tmp_path)
    release_json = _make_release_json(tmp_path / "release.json", is_draft=True)
    result = _run(
        [
            "--tag",
            "nucleus-v0.4.0",
            "--repo-root",
            str(repo),
            "--release-json",
            str(release_json),
        ]
    )
    assert result.returncode == 1
    assert "release json draft" in result.stderr


def test_refuse_release_json_prerelease(tmp_path: Path):
    repo = init_repo(tmp_path)
    release_json = _make_release_json(tmp_path / "release.json", is_prerelease=True)
    result = _run(
        [
            "--tag",
            "nucleus-v0.4.0",
            "--repo-root",
            str(repo),
            "--release-json",
            str(release_json),
        ]
    )
    assert result.returncode == 1
    assert "prerelease" in result.stderr


def test_refuse_release_json_non_empty_assets(tmp_path: Path):
    repo = init_repo(tmp_path)
    release_json = _make_release_json(
        tmp_path / "release.json",
        assets=[{"name": "linux-image-7.2.10-lusoris1-mainstream_amd64.deb"}],
    )
    result = _run(
        [
            "--tag",
            "nucleus-v0.4.0",
            "--repo-root",
            str(repo),
            "--release-json",
            str(release_json),
        ]
    )
    assert result.returncode == 1
    assert "non-empty assets" in result.stderr


def test_refuse_release_json_wrong_tag_name(tmp_path: Path):
    repo = init_repo(tmp_path)
    release_json = _make_release_json(tmp_path / "release.json", tag="nucleus-v0.3.0")
    result = _run(
        [
            "--tag",
            "nucleus-v0.4.0",
            "--repo-root",
            str(repo),
            "--release-json",
            str(release_json),
        ]
    )
    assert result.returncode == 1
    assert "wrong tagName" in result.stderr


def test_refuse_release_json_target_commitish_different_commit(tmp_path: Path):
    repo = init_repo(tmp_path)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "advance main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    # targetCommitish 'main' now points to a newer commit than tag nucleus-v0.4.0
    release_json = _make_release_json(tmp_path / "release.json", target_commitish="main")
    result = _run(
        [
            "--tag",
            "nucleus-v0.4.0",
            "--repo-root",
            str(repo),
            "--release-json",
            str(release_json),
        ]
    )
    assert result.returncode == 1
    assert "targetCommitish different commit" in result.stderr


def test_boundary_zero_and_large_numbers_pass():
    ver, err = check_tag_syntax("nucleus-v0.0.0", "nucleus")
    assert err is None
    assert ver == "0.0.0"

    ver, err = check_tag_syntax("nucleus-v12345.67890.999999", "nucleus")
    assert err is None
    assert ver == "12345.67890.999999"


@pytest.mark.parametrize(
    "invalid_tag",
    [
        "nucleus-v01.2.3",
        "nucleus-v1.02.3",
        "nucleus-v1.2.03",
        "nucleus-v0.0.01",
    ],
)
def test_boundary_leading_zero_refused(invalid_tag: str):
    ver, err = check_tag_syntax(invalid_tag, "nucleus")
    assert ver is None
    assert err is not None
    assert "malformed tag" in err


def test_real_repo_tag_nucleus_v0_4_0():
    present = [
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        ).returncode
        == 0
        for ref in ("nucleus-v0.4.0", "origin/main")
    ]
    if not all(present):
        pytest.skip("tag nucleus-v0.4.0 or origin/main is not in this checkout")

    result = _run(["--tag", "nucleus-v0.4.0", "--repo-root", str(REPO_ROOT)])
    assert result.returncode == 0, result.stderr


def test_refuse_a_resolver_that_fails_without_refusing(tmp_path: Path):
    repo = init_repo(tmp_path)
    resolver = repo / "scripts" / "resolve_release_tag.py"
    resolver.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
    result = _run(["--tag", "nucleus-v0.4.0", "--repo-root", str(repo)])
    assert result.returncode == 1
    assert "not a refusal" in result.stderr
