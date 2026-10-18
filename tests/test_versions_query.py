# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for scripts/versions_query.py queries and invariants."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "versions_query.py"
VERSIONS = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))


def _run_query(*args: str, versions: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT)]
    if versions is not None:
        cmd.append(f"--versions={versions}")
    cmd.extend(args)
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def test_list_streams_happy_path():
    result = _run_query("list", "streams")
    assert result.returncode == 0, result.stderr
    streams = result.stdout.strip().splitlines()
    assert streams == list(VERSIONS["streams"].keys())


def test_list_architectures_happy_path():
    result = _run_query("list", "architectures")
    assert result.returncode == 0, result.stderr
    architectures = result.stdout.strip().splitlines()
    assert architectures == list(VERSIONS["architectures"].keys())


def test_openzfs_happy_path():
    result = _run_query("openzfs")
    assert result.returncode == 0, result.stderr
    fields = dict(line.split("=", 1) for line in result.stdout.strip().splitlines() if line)
    zfs = VERSIONS["modules"]["openzfs"]
    source = zfs["source"]
    assert fields["version"] == zfs["version"]
    assert fields["url"] == source["url"]
    assert fields["signature_url"] == source["signature_url"]
    assert fields["sha256"] == source["sha256"]
    assert fields["signers"] == " ".join(source["signers"])
    assert fields["linux_minimum"] == zfs["linux_minimum"]
    assert fields["linux_maximum"] == zfs["linux_maximum"]


def test_openzfs_refuses_manifest_missing_sha256(tmp_path: Path):
    manifest = json.loads(json.dumps(VERSIONS))
    del manifest["modules"]["openzfs"]["source"]["sha256"]
    bad_manifest = tmp_path / "versions.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_query("openzfs", versions=bad_manifest)
    assert result.returncode == 2
    assert "modules.openzfs" in result.stderr
    assert "sha256" in result.stderr


def test_openzfs_refuses_missing_module(tmp_path: Path):
    manifest = json.loads(json.dumps(VERSIONS))
    del manifest["modules"]["openzfs"]
    bad_manifest = tmp_path / "versions.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_query("openzfs", versions=bad_manifest)
    assert result.returncode == 2
    assert "modules.openzfs missing" in result.stderr


def test_nvidia_open_happy_path():
    result = _run_query("nvidia-open")
    assert result.returncode == 0, result.stderr
    fields = dict(line.split("=", 1) for line in result.stdout.strip().splitlines() if line)
    nv = VERSIONS["modules"]["nvidia_open"]
    source = nv["source"]
    assert fields["version"] == nv["version"]
    assert fields["url"] == source["url"]
    assert fields["sha256"] == source["sha256"]
    assert fields["commit"] == source["commit"]
    assert fields["streams"] == " ".join(nv["streams"])
    assert fields["architectures"] == " ".join(nv["architectures"])


def test_nvidia_open_refuses_manifest_missing_sha256(tmp_path: Path):
    manifest = json.loads(json.dumps(VERSIONS))
    del manifest["modules"]["nvidia_open"]["source"]["sha256"]
    bad_manifest = tmp_path / "versions.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_query("nvidia-open", versions=bad_manifest)
    assert result.returncode == 2
    assert "modules.nvidia_open" in result.stderr
    assert "sha256" in result.stderr


def test_nvidia_open_refuses_missing_module(tmp_path: Path):
    manifest = json.loads(json.dumps(VERSIONS))
    del manifest["modules"]["nvidia_open"]
    bad_manifest = tmp_path / "versions.json"
    bad_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_query("nvidia-open", versions=bad_manifest)
    assert result.returncode == 2
    assert "modules.nvidia_open missing" in result.stderr


def test_list_unknown_collection_refused():
    result = _run_query("list", "unknown")
    assert result.returncode == 2
    assert "unknown list collection" in result.stderr


def test_missing_name_for_subcommands_refused():
    for cmd in ("source", "arch", "list", "series-streams", "scheduler-streams"):
        result = _run_query(cmd)
        assert result.returncode == 2
        assert f"query '{cmd}' requires a name argument" in result.stderr


# Inline Python allowed to read versions.json, with the reason. A reader that mirrors the
# script's own code is an allow-list, never a list of unsafe cases.
_INLINE_READERS_ALLOWED = {
    ("audit-repository-health.sh", "versions.schema.json"): "validates the whole document",
}
_INLINE = re.compile(r"""python3\s+-c\s+(['"])(.*?)\1([^\n]*)""", re.DOTALL)
_ASSIGNED = re.compile(
    r"""^(?:readonly\s+|local\s+)?(\w+)=(['"])(.*?)\2""", re.DOTALL | re.MULTILINE
)


def _inline_python(text: str) -> list[str]:
    """Each python3 -c body in a shell script with the arguments on its closing line."""
    bodies = {m.group(1): m.group(3) for m in _ASSIGNED.finditer(text)}
    found = []
    for match in _INLINE.finditer(text):
        code, args = match.group(2), match.group(3)
        variable = re.fullmatch(r"\$\{?(\w+)\}?", code)
        if variable:
            code = bodies.get(variable.group(1), code)
        found.append(code + args)
    return found


def violations_in(name: str, text: str) -> list[str]:
    """Inline Python in a script that names versions.json or the script's VERSIONS path."""
    found = []
    for snippet in _inline_python(text):
        if "versions.json" not in snippet and "VERSIONS}" not in snippet:
            continue
        if any(name == script and marker in snippet for script, marker in _INLINE_READERS_ALLOWED):
            continue
        found.append(f"{name}: {snippet.strip().splitlines()[0][:80]}")
    return found


def test_scripts_read_versions_json_only_through_versions_query():
    """Every shell script reads versions.json through scripts/versions_query.py (#156)."""
    violations = [
        v
        for script in sorted((REPO_ROOT / "scripts").glob("*.sh"))
        for v in violations_in(script.name, script.read_text(encoding="utf-8"))
    ]
    assert not violations, "inline Python reads versions.json:\n" + "\n".join(violations)


def test_the_sweep_refuses_each_inline_read_it_replaced():
    """Planted negatives: the three reads #156 removed, in their original shape."""
    planted = {
        "merge-config.sh": """  python3 -c 'import json, sys; sys.exit(sys.argv[2] not in json.load(open("versions.json"))[sys.argv[1]])' "$1" "$2"\n""",
        "check-openzfs-module.sh": """  local py_code='\nimport json, sys\nwith open(sys.argv[1]) as f:\n    data = json.load(f)\n'\n  output="$(python3 -c "${py_code}" "${VERSIONS}")"\n""",
        "package-uki.sh": """version=$(python3 -c "\nimport json\nwith open('versions.json', 'r') as f:\n    data = json.load(f)\n")\n""",
    }
    for name, text in planted.items():
        assert violations_in(name, text), name


def test_series_streams_lists_the_bound_streams():
    result = _run_query("series-streams", "bbr3")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == VERSIONS["patches"]["bbr3"]["streams"]
    assert "lts" not in result.stdout.split()


def test_series_streams_refuses_an_unknown_series():
    result = _run_query("series-streams", "nope")
    assert result.returncode == 2
    assert "patches.nope: missing" in result.stderr


def test_series_streams_refuses_streams_that_are_not_a_list(tmp_path):
    bad = dict(VERSIONS)
    bad["patches"] = {"bbr3": {"streams": "bleeding"}}
    manifest = tmp_path / "versions.json"
    manifest.write_text(json.dumps(bad), encoding="utf-8")
    result = _run_query("series-streams", "bbr3", versions=manifest)
    assert result.returncode == 2
    assert "patches.bbr3.streams: missing or not a list" in result.stderr


def test_scheduler_streams_lists_the_bound_streams():
    result = _run_query("scheduler-streams", "simple")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == VERSIONS["schedulers"]["simple"]["streams"]
    # linux-6.18.y's scx_simple calls a kernel function 6.18 does not have.
    assert "lts" not in result.stdout.split()
    assert set(result.stdout.split()) <= set(VERSIONS["streams"])


def test_scheduler_streams_refuses_an_unknown_scheduler():
    result = _run_query("scheduler-streams", "nope")
    assert result.returncode == 2
    assert "schedulers.nope: missing" in result.stderr
    assert result.stdout == ""


def test_scheduler_streams_does_not_read_patches(tmp_path):
    bad = dict(VERSIONS)
    bad["schedulers"] = {}
    manifest = tmp_path / "versions.json"
    manifest.write_text(json.dumps(bad), encoding="utf-8")
    result = _run_query("scheduler-streams", "bbr3", versions=manifest)
    assert result.returncode == 2
    assert "schedulers.bbr3: missing" in result.stderr
