#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Verify a repository release tag (nucleus-vX.Y.Z) the way a consumer would.

Checks that the tag adheres to release-please semantic versioning, matches the
component name declared in release-please-config.json, has consistent VERSION,
manifest, and CHANGELOG contents at the tag commit, is an ancestor of
origin/main, is rejected by scripts/resolve_release_tag.py, and matches release
metadata when a release JSON file is supplied.

Usage:
    python3 scripts/check_tool_release.py --tag nucleus-vX.Y.Z [--repo-root .] [--release-json FILE]

Exit 0 all pass, 1 any refusal, 2 usage.

Complies with NASA/JPL Power of 10: short functions (<= 60 lines), no recursion,
bounded loops, explicit subprocess timeouts, type hints.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_TIMEOUT = 30.0
MAX_CHANGELOG_LINES = 1000
MAX_PACKAGES = 64
# resolve_release_tag.py exits 1 when it refuses a tag; argparse exits 2 on a usage error.
RESOLVER_REFUSED = 1


def run_cmd(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Execute a subprocess command with an explicit timeout and captured output."""
    env = dict(os.environ, LC_ALL="C")
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=args,
            returncode=124,
            stdout="",
            stderr=f"command timed out after {DEFAULT_TIMEOUT}s",
        )


def extract_package_name(repo_root: Path) -> str:
    """Read the package-name from release-please-config.json in repo_root."""
    config_file = repo_root / "release-please-config.json"
    if not config_file.is_file():
        raise ValueError(f"{config_file} not found")
    data = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{config_file} is not a JSON object")
    packages = data.get("packages")
    if isinstance(packages, dict):
        dot_pkg = packages.get(".")
        if isinstance(dot_pkg, dict) and isinstance(dot_pkg.get("package-name"), str):
            return dot_pkg["package-name"]
        for pkg in list(packages.values())[:MAX_PACKAGES]:
            if isinstance(pkg, dict) and isinstance(pkg.get("package-name"), str):
                return pkg["package-name"]
    top_name = data.get("package-name")
    if isinstance(top_name, str):
        return top_name
    raise ValueError(f"no package-name found in {config_file}")


def check_tag_syntax(tag: str, component: str) -> tuple[str | None, str | None]:
    """Verify that tag matches ^<component>-v(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)$."""
    pattern = rf"^{re.escape(component)}-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
    match = re.match(pattern, tag)
    if not match:
        return (
            None,
            f"check_tool_release: tag '{tag}': check 'malformed tag': "
            f"observed '{tag}' does not match pattern '{pattern}'",
        )
    version = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    return version, None


def _check_commit_version(tag: str, expected_version: str, repo_root: Path) -> str | None:
    """Verify VERSION file content at tag commit."""
    res = run_cmd(["git", "show", f"{tag}:VERSION"], cwd=repo_root)
    if res.returncode != 0:
        return (
            f"check_tool_release: tag '{tag}': check 'VERSION mismatch': "
            f"observed git show failed: {res.stderr.strip()}"
        )
    if res.stdout.strip() != expected_version:
        return (
            f"check_tool_release: tag '{tag}': check 'VERSION mismatch': "
            f"observed '{res.stdout.strip()}' != '{expected_version}'"
        )
    return None


def _check_commit_manifest(tag: str, expected_version: str, repo_root: Path) -> str | None:
    """Verify .release-please-manifest.json content at tag commit."""
    res = run_cmd(["git", "show", f"{tag}:.release-please-manifest.json"], cwd=repo_root)
    if res.returncode != 0:
        return (
            f"check_tool_release: tag '{tag}': check 'manifest mismatch': "
            f"observed git show failed: {res.stderr.strip()}"
        )
    try:
        manifest = json.loads(res.stdout)
        val = manifest.get(".") if isinstance(manifest, dict) else None
        if val != expected_version:
            return (
                f"check_tool_release: tag '{tag}': check 'manifest mismatch': "
                f"observed '.' is {val!r} != '{expected_version}'"
            )
    except json.JSONDecodeError as exc:
        return (
            f"check_tool_release: tag '{tag}': check 'manifest mismatch': "
            f"observed invalid JSON: {exc}"
        )
    return None


def _check_commit_changelog(tag: str, expected_version: str, repo_root: Path) -> str | None:
    """Verify first '## ' heading in CHANGELOG.md at tag commit."""
    res = run_cmd(["git", "show", f"{tag}:CHANGELOG.md"], cwd=repo_root)
    if res.returncode != 0:
        return (
            f"check_tool_release: tag '{tag}': check 'CHANGELOG heading mismatch': "
            f"observed git show failed: {res.stderr.strip()}"
        )
    first_heading: str | None = None
    for line in res.stdout.splitlines()[:MAX_CHANGELOG_LINES]:
        if line.startswith("## "):
            first_heading = line
            break
    target = f"[{expected_version}]"
    if first_heading is None:
        return (
            f"check_tool_release: tag '{tag}': check 'CHANGELOG heading mismatch': "
            f"observed no '## ' heading found in CHANGELOG.md"
        )
    if target not in first_heading:
        return (
            f"check_tool_release: tag '{tag}': check 'CHANGELOG heading mismatch': "
            f"observed heading {first_heading!r} does not contain {target!r}"
        )
    return None


def check_commit_contents(tag: str, expected_version: str, repo_root: Path) -> list[str]:
    """Verify VERSION, manifest, and CHANGELOG at the tag commit."""
    failures: list[str] = []
    err = _check_commit_version(tag, expected_version, repo_root)
    if err:
        failures.append(err)
    err = _check_commit_manifest(tag, expected_version, repo_root)
    if err:
        failures.append(err)
    err = _check_commit_changelog(tag, expected_version, repo_root)
    if err:
        failures.append(err)
    return failures


def check_ancestry(tag: str, repo_root: Path) -> str | None:
    """Verify that tag is an ancestor of origin/main."""
    res = run_cmd(["git", "merge-base", "--is-ancestor", tag, "origin/main"], cwd=repo_root)
    if res.returncode != 0:
        return (
            f"check_tool_release: tag '{tag}': check 'tag not on origin/main': "
            f"observed git merge-base --is-ancestor rc={res.returncode}: {res.stderr.strip()}"
        )
    return None


def check_not_kernel_tag(tag: str, repo_root: Path) -> str | None:
    """Verify that resolve_release_tag.py rejects the tag."""
    resolver = repo_root / "scripts" / "resolve_release_tag.py"
    if not resolver.is_file():
        return (
            f"check_tool_release: tag '{tag}': check 'resolve_release_tag': "
            f"observed {resolver} does not exist"
        )
    cmd = [sys.executable, str(resolver), "--tag", tag]
    versions_file = repo_root / "versions.json"
    if versions_file.is_file():
        cmd.extend(["--versions", str(versions_file)])
    res = run_cmd(cmd, cwd=repo_root)
    if res.returncode == 0:
        return (
            f"check_tool_release: tag '{tag}': check 'resolve_release_tag': "
            f"observed tag resolved as kernel release (rc 0)"
        )
    if res.returncode != RESOLVER_REFUSED:
        return (
            f"check_tool_release: tag '{tag}': check 'resolve_release_tag': "
            f"observed rc {res.returncode}, not a refusal (rc {RESOLVER_REFUSED}): "
            f"{res.stderr.strip()}"
        )
    return None


def _check_target_commitish(tag: str, target_commitish: str, repo_root: Path) -> str | None:
    """Verify targetCommitish resolves to the same commit as tag."""
    res_target = run_cmd(["git", "rev-parse", f"{target_commitish}^{{commit}}"], cwd=repo_root)
    res_tag = run_cmd(["git", "rev-parse", f"{tag}^{{commit}}"], cwd=repo_root)
    if res_target.returncode != 0:
        return (
            f"check_tool_release: tag '{tag}': check 'targetCommitish different commit': "
            f"observed cannot resolve targetCommitish {target_commitish!r}: {res_target.stderr.strip()}"
        )
    if res_tag.returncode != 0:
        return (
            f"check_tool_release: tag '{tag}': check 'targetCommitish different commit': "
            f"observed cannot resolve tag {tag!r}: {res_tag.stderr.strip()}"
        )
    c_target = res_target.stdout.strip()
    c_tag = res_tag.stdout.strip()
    if c_target != c_tag:
        return (
            f"check_tool_release: tag '{tag}': check 'targetCommitish different commit': "
            f"observed targetCommitish commit {c_target} != tag commit {c_tag}"
        )
    return None


def _check_release_json_fields(tag: str, data: dict[str, object]) -> list[str]:
    """Verify fields of gh release view JSON data."""
    failures: list[str] = []
    tag_name = data.get("tagName")
    if tag_name != tag:
        failures.append(
            f"check_tool_release: tag '{tag}': check 'wrong tagName': observed tagName={tag_name!r}"
        )

    is_draft = data.get("isDraft")
    if is_draft is not False:
        failures.append(
            f"check_tool_release: tag '{tag}': check 'release json draft': observed isDraft={is_draft!r}"
        )

    is_prerelease = data.get("isPrerelease")
    if is_prerelease is not False:
        failures.append(
            f"check_tool_release: tag '{tag}': check 'prerelease': observed isPrerelease={is_prerelease!r}"
        )

    assets = data.get("assets")
    if not isinstance(assets, list) or len(assets) > 0:
        failures.append(
            f"check_tool_release: tag '{tag}': check 'non-empty assets': "
            f"observed {len(assets) if isinstance(assets, list) else assets!r} assets"
        )
    return failures


def check_release_json(tag: str, release_json: Path, repo_root: Path) -> list[str]:
    """Verify gh release view JSON output fields."""
    if not release_json.is_file():
        return [
            f"check_tool_release: tag '{tag}': check 'release json': observed {release_json} not found"
        ]
    try:
        data = json.loads(release_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [
            f"check_tool_release: tag '{tag}': check 'release json': observed error reading: {exc}"
        ]
    if not isinstance(data, dict):
        return [
            f"check_tool_release: tag '{tag}': check 'release json': observed not a JSON object"
        ]

    failures = _check_release_json_fields(tag, data)
    target_commitish = data.get("targetCommitish")
    if not isinstance(target_commitish, str) or not target_commitish.strip():
        failures.append(
            f"check_tool_release: tag '{tag}': check 'targetCommitish different commit': "
            f"observed targetCommitish={target_commitish!r}"
        )
    else:
        err = _check_target_commitish(tag, target_commitish, repo_root)
        if err:
            failures.append(err)

    return failures


def run_checks(tag: str, repo_root: Path, release_json: Path | None) -> list[str]:
    """Execute all tool release verification checks."""
    failures: list[str] = []
    try:
        component = extract_package_name(repo_root)
    except (OSError, ValueError) as exc:
        failures.append(
            f"check_tool_release: tag '{tag}': check 'release-please-config': observed {exc}"
        )
        component = None

    version: str | None = None
    if component is not None:
        version, err = check_tag_syntax(tag, component)
        if err:
            failures.append(err)

    if version is not None:
        failures.extend(check_commit_contents(tag, version, repo_root))

    err = check_ancestry(tag, repo_root)
    if err:
        failures.append(err)

    err = check_not_kernel_tag(tag, repo_root)
    if err:
        failures.append(err)

    if release_json is not None:
        failures.extend(check_release_json(tag, release_json, repo_root))

    return failures


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Verify a repository release tag (nucleus-vX.Y.Z) the way a consumer would."
    )
    parser.add_argument("--tag", required=True, help="repository release tag, e.g. nucleus-v0.4.0")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="path to repository root (default: current directory)",
    )
    parser.add_argument(
        "--release-json",
        type=Path,
        default=None,
        help="path to gh release view JSON output",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for checking repository tool releases."""
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    failures = run_checks(args.tag, repo_root, args.release_json)
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
