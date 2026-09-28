"""Tests for scripts/fetch-kernel-requirements.sh, the fetch step of verify-requirements.yml.

A stub ``gh`` on PATH answers the two calls the script makes (resolve a default branch,
read a file at a commit) from the fixtures, and logs every call, so the dispatch handling
is tested without the network: which row a dispatch pins, which rows are read at their
default branch, and which payloads are refused.
"""

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "kernel-requirement"
SCHEMA = "aegis.p01-nucleus.kernel-requirement.v1"
HEADS = {"cordanaLLM/imago": "1" * 40, "cordanaLLM/Aegis-OS": "2" * 40}
DISPATCH_REF = "3" * 40

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="the script needs bash")

STUB = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    args = sys.argv[1:]
    with open(os.environ["GH_STUB_LOG"], "a", encoding="utf-8") as log:
        log.write(" ".join(args) + "\\n")
    endpoint = next(arg for arg in args if arg.startswith("repos/"))
    if endpoint.endswith("/commits/HEAD"):
        print(json.loads(os.environ["GH_STUB_HEADS"])[endpoint[6:-13]])
        sys.exit(0)
    files = json.loads(os.environ["GH_STUB_FILES"])
    if endpoint not in files:
        sys.stderr.write("HTTP 404: " + endpoint + "\\n")
        sys.exit(1)
    with open(files[endpoint], "rb") as handle:
        sys.stdout.buffer.write(handle.read())
    """
)


def _files() -> dict[str, str]:
    """Every endpoint the stub serves: each document at its default head and at DISPATCH_REF."""
    served = {}
    for repo, path, name in (
        ("cordanaLLM/imago", "kernel/requirement.json", "imago.json"),
        ("cordanaLLM/Aegis-OS", "build/kernel-requirement.json", "aegis-os.json"),
    ):
        for ref in (HEADS[repo], DISPATCH_REF):
            served[f"repos/{repo}/contents/{path}?ref={ref}"] = str(FIXTURES / name)
    return served


def _run(tmp_path: Path, versions: Path | None = None, **env: str) -> subprocess.CompletedProcess:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "gh"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "GH_STUB_LOG": str(tmp_path / "gh.log"),
        "GH_STUB_HEADS": json.dumps(HEADS),
        "GH_STUB_FILES": json.dumps(_files()),
        **env,
    }
    args = ["bash", "scripts/fetch-kernel-requirements.sh", f"--out={tmp_path / 'out'}"]
    if versions is not None:
        args.append(f"--versions={versions}")
    return subprocess.run(args, cwd=REPO_ROOT, env=environment, capture_output=True, text=True)


def _args(tmp_path: Path) -> list[str]:
    return (tmp_path / "out" / "args").read_text(encoding="utf-8").splitlines()


def _dispatch(**overrides: str) -> dict[str, str]:
    payload = {
        "EVENT_NAME": "repository_dispatch",
        "PAYLOAD_SOURCE": "imago",
        "PAYLOAD_SCHEMA": SCHEMA,
        "PAYLOAD_PATH": "kernel/requirement.json",
        "PAYLOAD_REF": DISPATCH_REF,
        "PAYLOAD_SHA256": "4" * 64,
        "PAYLOAD_CORRELATION_ID": "imago-kernel-requirement-0001",
    }
    payload.update(overrides)
    return payload


def test_a_scheduled_run_reads_every_document_at_its_resolved_default_branch(tmp_path):
    result = _run(tmp_path, EVENT_NAME="schedule")
    assert result.returncode == 0, result.stderr
    out = tmp_path / "out"
    assert _args(tmp_path) == [
        f"--requirement=imago={out / 'imago.json'}",
        f"--ref=imago={HEADS['cordanaLLM/imago']}",
        f"--requirement=aegis-os={out / 'aegis-os.json'}",
        f"--ref=aegis-os={HEADS['cordanaLLM/Aegis-OS']}",
    ]
    assert (out / "imago.json").read_bytes() == (FIXTURES / "imago.json").read_bytes()
    assert (
        "sha256 cee3ec779575543f735b9e242e3b6bc7df36333d23243a25aa6d4d11cb0ef0a8" in result.stdout
    )


def test_a_dispatch_pins_the_row_its_source_names_and_resolves_the_others(tmp_path):
    result = _run(tmp_path, **_dispatch())
    assert result.returncode == 0, result.stderr
    args = _args(tmp_path)
    assert f"--ref=imago={DISPATCH_REF}" in args
    assert f"--sha256=imago={'4' * 64}" in args
    assert "--correlation-id=imago=imago-kernel-requirement-0001" in args
    assert f"--ref=aegis-os={HEADS['cordanaLLM/Aegis-OS']}" in args
    log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert "repos/cordanaLLM/imago/commits/HEAD" not in log, "a pinned row is not re-resolved"


def test_a_second_dispatched_source_is_pinned_not_refused(tmp_path):
    """Two dispatched rows: a dispatch from either pins its own row, whichever is listed first."""
    versions = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
    for row in versions["downstream"]["requirements"]:
        row["dispatched"] = True
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(versions), encoding="utf-8")
    payload = _dispatch(
        PAYLOAD_SOURCE="aegis-os",
        PAYLOAD_PATH="build/kernel-requirement.json",
        PAYLOAD_CORRELATION_ID="aegis-m18-kernel-requirement-0001",
    )
    result = _run(tmp_path, versions=path, **payload)
    assert result.returncode == 0, result.stderr
    args = _args(tmp_path)
    assert f"--ref=aegis-os={DISPATCH_REF}" in args
    assert f"--ref=imago={HEADS['cordanaLLM/imago']}" in args


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"PAYLOAD_SOURCE": "aegis-os"}, "matches no dispatched requirement"),
        ({"PAYLOAD_SOURCE": ""}, "matches no dispatched requirement"),
        ({"PAYLOAD_PATH": "../kernel/requirement.json"}, "is not kernel/requirement.json"),
        ({"PAYLOAD_SCHEMA": "aegis.p01-nucleus.kernel-requirement.v2"}, "dispatched schema"),
        ({"PAYLOAD_REF": "main"}, "is not a full commit SHA"),
        ({"PAYLOAD_SHA256": "A" * 64}, "is not a lower-case SHA-256 digest"),
        ({"PAYLOAD_CORRELATION_ID": ""}, "correlation_id"),
        ({"PAYLOAD_CORRELATION_ID": "has spaces"}, "correlation_id"),
    ],
)
def test_a_dispatch_the_declared_row_does_not_back_is_refused(tmp_path, override, message):
    result = _run(tmp_path, **_dispatch(**override))
    assert result.returncode == 1
    assert "::error::" in result.stderr and message in result.stderr
    assert not (tmp_path / "gh.log").exists(), "nothing is fetched for a refused dispatch"


def test_a_payload_cannot_inject_a_workflow_command(tmp_path):
    result = _run(tmp_path, **_dispatch(PAYLOAD_SOURCE="imago\n::warning::injected"))
    assert result.returncode == 1
    lines = (result.stdout + result.stderr).splitlines()
    assert not any(line.startswith("::warning::") for line in lines)
    assert "imago?::warning::injected" in result.stderr


def test_a_document_that_cannot_be_fetched_stops_the_run(tmp_path):
    heads = dict(HEADS, **{"cordanaLLM/Aegis-OS": "5" * 40})
    result = _run(tmp_path, EVENT_NAME="schedule", GH_STUB_HEADS=json.dumps(heads))
    assert result.returncode == 1
    assert "could not fetch cordanaLLM/Aegis-OS/build/kernel-requirement.json" in result.stderr
