"""Test manifest integrity and Single Source of Truth for nucleus."""

import json
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def manifest_data():
    manifest_path = REPO_ROOT / "versions.json"
    assert manifest_path.exists(), "versions.json must exist"
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def schema_data():
    schema_path = REPO_ROOT / "versions.schema.json"
    assert schema_path.exists(), "versions.schema.json must exist"
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


def test_manifest_schema_validation(manifest_data, schema_data):
    """Validate versions.json conforms to versions.schema.json."""
    jsonschema.validate(instance=manifest_data, schema=schema_data)


def test_manifest_streams(manifest_data):
    """All four release streams exist, each with a version, its tag and a source."""
    streams = manifest_data.get("streams", {})
    expected = ["bleeding", "mainstream", "lts", "realtime"]
    for s in expected:
        assert s in streams, f"Stream {s} must exist in versions.json"
        assert streams[s]["tag"] == f"v{streams[s]['version']}", s
        assert "tarball_url" not in streams[s], "tarball_url is replaced by source"
        assert streams[s]["source"]["kind"] in ("tarball", "git-tag"), s


@pytest.mark.parametrize("stream", ["bleeding", "mainstream", "lts", "realtime"])
def test_each_source_names_the_release_its_stream_publishes(manifest_data, stream):
    """The source is the stream's release: the tarball names it, or the tag is the stream tag.

    No version is written here: a bump edits versions.json alone, and this checks that the
    source moved with the version.
    """
    entry = manifest_data["streams"][stream]
    source = entry["source"]
    if source["kind"] == "tarball":
        assert source["url"].endswith(f"/linux-{entry['version']}.tar.xz")
        assert source["signature_url"] == source["url"].removesuffix(".tar.xz") + ".tar.sign"
    else:
        assert source["tag"] == entry["tag"]


def test_every_signer_has_its_public_key_in_keys(manifest_data):
    """scripts/fetch-kernel-source.sh builds its keyring from keys/<FINGERPRINT>.asc."""
    for name, entry in manifest_data["streams"].items():
        for fpr in entry["source"]["signers"]:
            assert (REPO_ROOT / "keys" / f"{fpr}.asc").is_file(), (name, fpr)


def test_a_release_candidate_comes_from_a_signed_tag(manifest_data):
    """kernel.org signs no release-candidate tarball; the only signed path is the tag."""
    for name, entry in manifest_data["streams"].items():
        if "-rc" in entry["version"]:
            assert entry["source"]["kind"] == "git-tag", name


def test_supported_architectures(manifest_data):
    """x86_64, arm64 and riscv64 are built, each with its ARCH, defconfig and toolchain."""
    archs = manifest_data.get("architectures", {})
    assert set(archs) == {"x86_64", "arm64", "riscv64"}
    assert archs["riscv64"]["kernel_arch"] == "riscv"
    for arch, data in archs.items():
        assert data["base_config"].endswith("defconfig"), arch
        assert data["cross_compile"].endswith("-linux-gnu-"), arch
    assert {arch: data["debian_arch"] for arch, data in archs.items()} == {
        "x86_64": "amd64",
        "arm64": "arm64",
        "riscv64": "riscv64",
    }
