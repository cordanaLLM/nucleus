"""Test manifest integrity and Single Source of Truth for lusoris-kernel-forge."""

import json
from pathlib import Path
import pytest
import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent

@pytest.fixture
def manifest_data():
    manifest_path = REPO_ROOT / "versions.json"
    assert manifest_path.exists(), "versions.json must exist"
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)

@pytest.fixture
def schema_data():
    schema_path = REPO_ROOT / "versions.schema.json"
    assert schema_path.exists(), "versions.schema.json must exist"
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)

def test_manifest_schema_validation(manifest_data, schema_data):
    """Validate versions.json conforms to versions.schema.json."""
    jsonschema.validate(instance=manifest_data, schema=schema_data)

def test_manifest_streams(manifest_data):
    """Ensure all 4 release streams exist with valid metadata."""
    streams = manifest_data.get("streams", {})
    expected = ["bleeding", "mainstream", "lts", "realtime"]
    for s in expected:
        assert s in streams, f"Stream {s} must exist in versions.json"
        assert "version" in streams[s]
        assert "tag" in streams[s]
        assert "tarball_url" in streams[s]
        assert streams[s]["tarball_url"].startswith("http")

def test_live_kernel_versions(manifest_data):
    """Verify live kernel version assertions (Late 2026 releases)."""
    streams = manifest_data["streams"]
    assert streams["bleeding"]["version"] == "7.3-rc2"
    assert streams["mainstream"]["version"] == "7.2.4"
    assert streams["lts"]["version"] == "6.18.50"
    assert streams["realtime"]["version"] == "7.2-rt"

def test_supported_architectures(manifest_data):
    """Verify support for x86_64, arm64, and riscv64."""
    archs = manifest_data.get("architectures", [])
    assert "x86_64" in archs
    assert "arm64" in archs
    assert "riscv64" in archs
