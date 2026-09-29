"""Tests for scripts/notify_downstream.sh, the manual downstream dispatch.

The script must send the payload of publish-release.yml for a kernel release tag the
resolver accepts, and it must not report success for a dispatch it did not send. A
stand-in `gh` records its arguments, so these tests run offline and show whether a
dispatch was attempted.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "notify_downstream.sh"
VERSIONS = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
STREAMS = {name: entry["version"] for name, entry in VERSIONS["streams"].items()}
STREAM = sorted(STREAMS)[0]
VERSION = STREAMS[STREAM]
TAG = f"v{VERSION}-{STREAM}-lusoris1"
RELEASE_VERSION = f"{VERSION}-lusoris1"

FAKE_GH = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"${FAKE_GH_LOG}"
"""


@pytest.fixture
def gh_log(tmp_path):
    """Put a recording `gh` first in PATH and return the log it appends to."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stand_in = bindir / "gh"
    stand_in.write_text(FAKE_GH, encoding="utf-8")
    stand_in.chmod(0o755)
    log = tmp_path / "gh.log"
    log.write_text("", encoding="utf-8")
    return log


def _run(*args: str, gh_log: Path, tag: str | None = TAG, token: str | None = None):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("RELEASE_TAG", "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_ACTIONS")
    }
    env["PATH"] = f"{gh_log.parent / 'bin'}{os.pathsep}{env['PATH']}"
    env["FAKE_GH_LOG"] = str(gh_log)
    if tag is not None:
        env["RELEASE_TAG"] = tag
    if token is not None:
        env["GITHUB_TOKEN"] = token
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False
    )


def _dispatches(gh_log: Path) -> list[str]:
    return gh_log.read_text(encoding="utf-8").splitlines()


def test_dry_run_prints_the_dispatch_for_a_resolvable_tag(gh_log):
    result = _run(STREAM, RELEASE_VERSION, "true", gh_log=gh_log)
    assert result.returncode == 0, result.stderr
    assert f"Tag:     {TAG}" in result.stdout and "[DRY-RUN]" in result.stdout
    assert _dispatches(gh_log) == []


def test_live_dispatch_sends_stream_version_and_tag(gh_log):
    result = _run(STREAM, RELEASE_VERSION, gh_log=gh_log, token="dummy")
    assert result.returncode == 0, result.stderr
    (call,) = _dispatches(gh_log)
    assert "repos/cordanaLLM/imago/dispatches" in call
    for field in (
        f"client_payload[stream]={STREAM}",
        f"client_payload[version]={RELEASE_VERSION}",
        f"client_payload[tag]={TAG}",
    ):
        assert field in call


def test_a_second_revision_dispatches_its_own_version_and_tag(gh_log):
    """A -lusoris2 release carries -lusoris2 downstream, the way publish-release.yml sends it."""
    tag, version = f"v{VERSION}-{STREAM}-lusoris2", f"{VERSION}-lusoris2"
    result = _run(STREAM, version, gh_log=gh_log, tag=tag, token="dummy")
    assert result.returncode == 0, result.stderr
    (call,) = _dispatches(gh_log)
    assert f"client_payload[version]={version}" in call and f"client_payload[tag]={tag}" in call


def test_live_dispatch_without_a_token_fails_and_sends_nothing(gh_log):
    """A missing credential is an error, not a warning that exits 0."""
    result = _run(STREAM, RELEASE_VERSION, gh_log=gh_log)
    assert result.returncode == 1
    assert "GITHUB_TOKEN is not set" in result.stderr and "Nothing was sent" in result.stderr
    assert _dispatches(gh_log) == []


def test_release_tag_has_no_default(gh_log):
    result = _run(STREAM, RELEASE_VERSION, "true", gh_log=gh_log, tag=None)
    assert result.returncode == 1
    assert "RELEASE_TAG" in result.stderr and "Usage" in result.stderr
    assert _dispatches(gh_log) == []


@pytest.mark.parametrize(
    "tag",
    [
        pytest.param("v0.2.0", id="repository-version-tag"),
        pytest.param(f"v{RELEASE_VERSION}", id="tag-without-a-stream"),
        pytest.param(f"v{VERSION}-{STREAM}-lusoris0", id="revision-zero"),
        pytest.param(f"v{VERSION}-{STREAM}-lusoris02", id="revision-with-a-leading-zero"),
    ],
)
def test_a_tag_the_resolver_refuses_is_refused(gh_log, tag):
    result = _run(STREAM, RELEASE_VERSION, gh_log=gh_log, tag=tag, token="dummy")
    assert result.returncode == 1
    assert "error:" in result.stderr and tag in result.stderr
    assert _dispatches(gh_log) == []


@pytest.mark.parametrize(
    ("stream", "version", "label"),
    [
        pytest.param("nosuchstream", RELEASE_VERSION, "Stream", id="stream"),
        pytest.param(STREAM, VERSION, "Version", id="version-without-revision"),
        pytest.param(STREAM, f"{VERSION}-lusoris2", "Version", id="version-of-another-revision"),
    ],
)
def test_arguments_must_agree_with_the_tag(gh_log, stream, version, label):
    result = _run(stream, version, gh_log=gh_log, token="dummy")
    assert result.returncode == 1
    assert f"Error: {label} " in result.stderr and TAG in result.stderr
    assert _dispatches(gh_log) == []
