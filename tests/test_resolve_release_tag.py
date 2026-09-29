"""Tests for scripts/resolve_release_tag.py and the release tag namespaces.

A kernel release tag is v<version>-<stream>-lusoris<N>. Every expectation here is
derived from versions.json and the release-please configuration, so a kernel
version bump never needs a test edit.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "resolve_release_tag.py"
VERSIONS = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
STREAMS = {name: entry["version"] for name, entry in VERSIONS["streams"].items()}
FIRST = sorted(STREAMS)[0]


def _load_resolver():
    spec = importlib.util.spec_from_file_location("resolve_release_tag", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


resolver = _load_resolver()


def _tag(version: str, stream: str, rev: object = 1) -> str:
    return f"v{version}-{stream}-lusoris{rev}"


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    base = {key: value for key, value in os.environ.items() if key != "GITHUB_ACTIONS"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={**base, **(env or {})},
        check=False,
    )


def _expected_outputs(stream: str, rev: int = 1) -> str:
    version = STREAMS[stream]
    return (
        f"stream={stream}\nversion={version}\nrev={rev}\n"
        f"release_tag={_tag(version, stream, rev)}\nrelease_version={version}-lusoris{rev}\n"
    )


def test_load_streams_reads_versions_json():
    assert resolver.load_streams(REPO_ROOT / "versions.json") == STREAMS


@pytest.mark.parametrize("stream", sorted(STREAMS))
def test_each_stream_tag_resolves_to_that_stream(stream):
    version = STREAMS[stream]
    resolution = resolver.resolve(_tag(version, stream), STREAMS)
    assert (resolution.stream, resolution.version, resolution.rev) == (stream, version, 1)
    assert resolution.release_tag == _tag(version, stream)
    assert resolution.release_version == f"{version}-lusoris1"


@pytest.mark.parametrize("rev", [2, 3, 12])
def test_revision_above_one_is_refused_for_now(rev):
    """Only revision 1 is released until a second revision of a release is wanted."""
    with pytest.raises(resolver.TagError, match=f"revision '{rev}'.*only revision 1"):
        resolver.resolve(_tag(STREAMS[FIRST], FIRST, rev), STREAMS)


def test_the_release_builds_the_revision_the_tag_names():
    """The build takes the revision from the resolver, so relaxing it needs no other change."""
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "publish-release.yml").read_text(encoding="utf-8")
    )
    build = {step.get("name"): step for step in workflow["jobs"]["publish"]["steps"]}[
        "Build the Tagged Stream"
    ]
    assert build["env"]["REVISION"] == "${{ steps.meta.outputs.rev }}"
    assert '--revision="${REVISION}"' in build["run"]
    assert "-lusoris1" not in (REPO_ROOT / "scripts" / "build_kernel.sh").read_text(encoding="utf-8")
    assert resolver.SUPPORTED_REVISION == "1"


def test_streams_sharing_a_version_resolve_by_name():
    """Two streams may carry one upstream version; the tag's stream decides."""
    shared = dict.fromkeys(STREAMS, STREAMS[FIRST])
    for name in shared:
        assert resolver.resolve(_tag(STREAMS[FIRST], name), shared).stream == name


NEGATIVE_TAGS = [
    pytest.param("v0.2.0", id="repository-version-tag"),
    pytest.param("nucleus-v0.2.0", id="repository-component-tag"),
    pytest.param(_tag(STREAMS[FIRST], "nosuchstream"), id="unknown-stream"),
    pytest.param(f"v{STREAMS[FIRST]}-{FIRST}", id="missing-lusoris-revision"),
    pytest.param(_tag(STREAMS[FIRST], FIRST, 0), id="revision-zero"),
    pytest.param(_tag(STREAMS[FIRST], FIRST, "01"), id="revision-leading-zero"),
    pytest.param(_tag(STREAMS[FIRST], FIRST, 2), id="revision-two"),
    pytest.param(_tag(STREAMS[FIRST], FIRST, "1x"), id="revision-trailing-text"),
    pytest.param(f"v{STREAMS[FIRST]}-lusoris1", id="stream-omitted"),
    pytest.param(f"{STREAMS[FIRST]}-{FIRST}-lusoris1", id="missing-v-prefix"),
    pytest.param(f"refs/tags/{_tag(STREAMS[FIRST], FIRST)}", id="full-ref"),
    pytest.param(_tag(STREAMS[FIRST], FIRST) + "\n", id="trailing-newline"),
    pytest.param(_tag(STREAMS[FIRST], FIRST.upper()), id="stream-in-upper-case"),
    pytest.param("", id="empty"),
]


@pytest.mark.parametrize("tag", NEGATIVE_TAGS)
def test_tags_outside_the_grammar_are_refused(tag):
    with pytest.raises(resolver.TagError):
        resolver.resolve(tag, STREAMS)


def test_version_of_another_stream_is_refused():
    pairs = [(a, b) for a in STREAMS for b in STREAMS if STREAMS[a] != STREAMS[b]]
    if not pairs:
        pytest.skip("every stream carries the same version")
    owner, named = pairs[0]
    with pytest.raises(resolver.TagError, match=f"stream {named} is at version"):
        resolver.resolve(_tag(STREAMS[owner], named), STREAMS)


@pytest.mark.parametrize("stream", sorted(STREAMS))
def test_version_extended_by_a_digit_is_refused(stream):
    """7.2.40 is not 7.2.4: a stream version must match whole, not as a prefix."""
    longer = f"{STREAMS[stream]}0"
    assert longer not in STREAMS.values()
    with pytest.raises(resolver.TagError, match="does not match versions.json"):
        resolver.resolve(_tag(longer, stream), STREAMS)
    with pytest.raises(resolver.TagError):
        resolver.resolve(f"v{longer}-lusoris1", STREAMS)


@pytest.mark.parametrize("stream", sorted(STREAMS))
def test_version_cut_short_is_refused(stream):
    with pytest.raises(resolver.TagError, match="does not match versions.json"):
        resolver.resolve(_tag(STREAMS[stream][:-1], stream), STREAMS)


def test_a_tag_matching_two_streams_is_refused():
    ambiguous = {"a-b": "1", "b": "1-a"}
    with pytest.raises(resolver.TagError, match="more than one stream"):
        resolver.resolve("v1-a-b-lusoris1", ambiguous)


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"streams": {}},
        {"streams": {"mainline": {"tag": "v1"}}},
        {"streams": {"Main Line": {"version": "1.0"}}},
        {"streams": {"mainline": {"version": "1.0\nstream=injected"}}},
    ],
    ids=["no-streams-key", "empty-streams", "missing-version", "bad-stream-name", "bad-version"],
)
def test_malformed_versions_json_is_refused(tmp_path, document):
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(resolver.TagError):
        resolver.load_streams(path)


def test_cli_appends_outputs_to_github_output(tmp_path):
    output = tmp_path / "github_output"
    output.write_text("earlier=kept\n", encoding="utf-8")
    result = _run_cli("--tag", _tag(STREAMS[FIRST], FIRST), "--github-output", str(output))
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert output.read_text(encoding="utf-8") == "earlier=kept\n" + _expected_outputs(FIRST)


def test_cli_prints_outputs_without_github_output():
    result = _run_cli("--tag", _tag(STREAMS[FIRST], FIRST))
    assert result.returncode == 0, result.stderr
    assert result.stdout == _expected_outputs(FIRST)


def test_cli_refusal_writes_no_output(tmp_path):
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    result = _run_cli("--tag", "v0.2.0", "--github-output", str(output))
    assert result.returncode == 1
    assert "v0.2.0" in result.stderr and "lusoris<N>" in result.stderr
    assert output.read_text(encoding="utf-8") == ""


def test_cli_refusal_is_an_actions_annotation_in_actions():
    result = _run_cli("--tag", "v0.2.0", env={"GITHUB_ACTIONS": "true"})
    assert result.returncode == 1
    assert result.stderr.startswith("::error title=Kernel release tag refused::")


def test_cli_reads_the_versions_option(tmp_path):
    versions = tmp_path / "versions.json"
    versions.write_text(
        json.dumps({"streams": {"canary": {"version": "9.9-rc9"}}}), encoding="utf-8"
    )
    result = _run_cli("--tag", "v9.9-rc9-canary-lusoris1", "--versions", str(versions))
    assert result.returncode == 0, result.stderr
    assert (
        "stream=canary\n" in result.stdout and "release_version=9.9-rc9-lusoris1\n" in result.stdout
    )


def test_cli_accepts_the_run_started_from_the_tag(tmp_path):
    tag = _tag(STREAMS[FIRST], FIRST)
    output = tmp_path / "github_output"
    result = _run_cli("--tag", tag, "--ref", f"refs/tags/{tag}", "--github-output", str(output))
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == _expected_outputs(FIRST)


@pytest.mark.parametrize(
    "ref",
    [
        pytest.param("refs/heads/main", id="branch"),
        pytest.param("refs/tags/v0.2.0", id="another-tag"),
        pytest.param(f"refs/tags/{_tag(STREAMS[FIRST], FIRST)}x", id="longer-tag"),
        pytest.param(_tag(STREAMS[FIRST], FIRST), id="short-name-not-a-ref"),
        pytest.param("", id="empty"),
    ],
)
def test_cli_refuses_a_run_started_from_another_ref(tmp_path, ref):
    """A dispatch of tag X from ref Y would sign and release under Y, not under the tag X names."""
    tag = _tag(STREAMS[FIRST], FIRST)
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    result = _run_cli("--tag", tag, "--ref", ref, "--github-output", str(output))
    assert result.returncode == 1
    assert f"refs/tags/{tag}" in result.stderr and f"--ref {tag}" in result.stderr
    assert output.read_text(encoding="utf-8") == ""


def test_cli_without_ref_skips_the_ref_check():
    assert _run_cli("--tag", _tag(STREAMS[FIRST], FIRST)).returncode == 0


def _release_please_package() -> dict:
    config = json.loads((REPO_ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    return config["packages"]["."]


def test_repository_release_tags_never_reach_the_kernel_trigger():
    """release-please tags nucleus-v<X.Y.Z>; publish-release triggers on kernel tags only."""
    package = _release_please_package()
    assert package["include-component-in-tag"] is True
    component = package.get("component") or package["package-name"]
    manifest = json.loads((REPO_ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))
    repository_tag = f"{component}-v{manifest['.']}"

    workflow_path = REPO_ROOT / ".github" / "workflows" / "publish-release.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    patterns = triggers["push"]["tags"]
    assert patterns, "publish-release must trigger on kernel release tags"
    for pattern in patterns:
        assert not fnmatch.fnmatchcase(repository_tag, pattern), (repository_tag, pattern)
    with pytest.raises(resolver.TagError):
        resolver.resolve(repository_tag, STREAMS)


def test_version_file_follows_repository_releases():
    """release-please writes VERSION through version-file; it must carry the manifest version."""
    package = _release_please_package()
    assert package.get("version-file") == "VERSION"
    for extra in package.get("extra-files", []):
        path = extra if isinstance(extra, str) else extra.get("path")
        assert path not in ("VERSION", "versions.json"), f"{path} is not an extra-file"
    manifest = json.loads((REPO_ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))
    assert (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip() == manifest["."]
