"""scripts/kernel_artifact_manifest.py: the manifest imago verifies, and what it refuses to describe.

Each test builds a signed release directory the way publish-release.yml leaves it before the
manifest step (packages, kernel image, configuration, SBOMs, SHA256SUMS and its bundle) and runs
the generator over it. The acceptance criteria are those of issue #12.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "kernel_artifact_manifest.py"

STREAM = "mainstream"
KR = "7.2.8-lusoris1-mainstream"
REVISION = "0123456789abcdef0123456789abcdef01234567"
TAG = "v7.2.8-mainstream-lusoris1"
SIGNER = (
    f"https://github.com/cordanaLLM/nucleus/.github/workflows/publish-release.yml@refs/tags/{TAG}"
)
FILES = {
    f"linux-image-{KR}_7.2.8-lusoris1_amd64.deb": b"image package",
    f"linux-headers-{KR}_7.2.8-lusoris1_amd64.deb": b"headers package, longer",
    "linux-libc-dev_7.2.8-lusoris1_amd64.deb": b"libc-dev",
    f"vmlinuz-{KR}": b"MZ kernel",
    f"kernel-{STREAM}-x86_64.config": b"CONFIG_BPF_LSM=y\n",
    f"kernel-{STREAM}.cdx.json": b"{}",
    f"kernel-{STREAM}.spdx.json": b"{ }",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def release(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    for name, data in FILES.items():
        (out / name).write_bytes(data)
    _write_sums(out, FILES)
    (out / "SHA256SUMS.bundle").write_text('{"planted": "bundle"}', encoding="utf-8")
    return out


def _write_sums(out: Path, files: dict[str, bytes], mode: str = " ") -> None:
    lines = [f"{_sha(data)} {mode}{name}\n" for name, data in sorted(files.items())]
    (out / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def _run(out: Path, *override: str) -> subprocess.CompletedProcess:
    args = {
        "--dir": str(out),
        "--stream": STREAM,
        "--version": "7.2.8-lusoris1",
        "--kernelrelease": KR,
        "--tag": TAG,
        "--revision": REVISION,
        "--repository": "cordanaLLM/nucleus",
        "--signer-identity": SIGNER,
    }
    for item in override:
        key, value = item.split("=", 1)
        args[key] = value
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(f"{k}={v}" for k, v in args.items())],
        capture_output=True,
        text=True,
        check=False,
    )


def _manifest(out: Path) -> dict:
    return json.loads((out / f"kernel-{STREAM}.manifest.json").read_text(encoding="utf-8"))


def test_the_manifest_digests_match_sha256sums_and_the_files(release):
    """Issue #12, positive: a release publishes a manifest whose digests match SHA256SUMS."""
    result = _run(release)
    assert result.returncode == 0, result.stderr
    manifest = _manifest(release)
    listed = {}
    for line in (release / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        listed[name] = digest
    assert {a["name"]: a["sha256"] for a in manifest["artifacts"]} == listed
    for artifact in manifest["artifacts"]:
        data = (release / artifact["name"]).read_bytes()
        assert artifact["sha256"] == _sha(data) and artifact["size"] == len(data)
    assert manifest["checksums"] == {
        "file": "SHA256SUMS",
        "sha256": _sha((release / "SHA256SUMS").read_bytes()),
    }
    config = FILES[f"kernel-{STREAM}-x86_64.config"]
    assert manifest["kernel"] == {"release": KR, "config_digest": "sha256:" + _sha(config)}


def test_the_manifest_has_the_shape_imago_reads(release):
    assert _run(release).returncode == 0
    manifest = _manifest(release)
    assert list(manifest) == [
        "schema",
        "provider",
        "stream",
        "version",
        "kernel",
        "artifacts",
        "checksums",
        "provenance",
    ]
    assert manifest["schema"] == "imago.nucleus.kernel-artifact.v1"
    assert manifest["provider"] == "cordanaLLM/nucleus"
    assert (manifest["stream"], manifest["version"]) == (STREAM, "7.2.8-lusoris1")
    assert manifest["provenance"] == {
        "repository": "cordanaLLM/nucleus",
        "tag": TAG,
        "revision": REVISION,
        "bundle": "SHA256SUMS.bundle",
        "signer_identity": SIGNER,
    }


def test_binary_mode_checksum_lines_are_read(release):
    _write_sums(release, FILES, mode="*")
    result = _run(release)
    assert result.returncode == 0, result.stderr
    assert len(_manifest(release)["artifacts"]) == len(FILES)


def _refused(out: Path, expected: str, *override: str) -> None:
    result = _run(out, *override)
    assert result.returncode == 1, result.stdout
    assert expected in result.stderr
    assert not (out / f"kernel-{STREAM}.manifest.json").exists(), "a refusal writes nothing"


def test_a_release_without_its_configuration_is_refused(release):
    """Issue #12's boundary: no configuration means no config digest, never a stand-in one."""
    name = f"kernel-{STREAM}-x86_64.config"
    (release / name).unlink()
    _write_sums(release, {k: v for k, v in FILES.items() if k != name})
    _refused(release, f"{name} is not in SHA256SUMS: there is no configuration to digest")


def test_a_file_changed_after_checksumming_is_refused(release):
    (release / f"vmlinuz-{KR}").write_bytes(b"MZ another kernel")
    _refused(release, f"vmlinuz-{KR} does not have the digest SHA256SUMS lists for it")


def test_a_listed_file_that_is_missing_is_refused(release):
    (release / "linux-libc-dev_7.2.8-lusoris1_amd64.deb").unlink()
    _refused(release, "linux-libc-dev_7.2.8-lusoris1_amd64.deb is listed in SHA256SUMS but is not")


def test_a_file_that_is_not_listed_is_refused(release):
    (release / "planted.deb").write_bytes(b"unlisted")
    _refused(release, "not listed in SHA256SUMS: planted.deb")


def test_the_provenance_bundle_written_after_checksumming_is_accepted(release):
    """release-build.yml writes the attestation bundle after SHA256SUMS; it is not a stranger."""
    (release / f"kernel-{STREAM}.provenance.sigstore.json").write_bytes(b"{}")
    result = _run(release)
    assert result.returncode == 0, result.stderr


def test_a_provenance_bundle_of_another_stream_is_refused(release):
    (release / "kernel-lts.provenance.sigstore.json").write_bytes(b"{}")
    _refused(release, "not listed in SHA256SUMS: kernel-lts.provenance.sigstore.json")


def test_an_unsigned_release_is_refused(release):
    (release / "SHA256SUMS.bundle").unlink()
    _refused(release, "SHA256SUMS has not been signed")


@pytest.mark.parametrize(
    "line",
    [
        pytest.param(f"{'0' * 64}  ../escape.deb", id="parent-path"),
        pytest.param(f"{'0' * 64}  sub/file.deb", id="sub-path"),
        pytest.param(f"{'0' * 64}  .hidden", id="hidden-name"),
        pytest.param(f"{'0' * 63}  short.deb", id="short-digest"),
        pytest.param("not a checksum line", id="garbage"),
    ],
)
def test_a_line_that_is_not_a_plain_checksum_entry_is_refused(release, line):
    sums = release / "SHA256SUMS"
    sums.write_text(sums.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    _refused(release, "line is not '<64 hex> <file name>'")


def test_a_file_listed_twice_is_refused(release):
    sums = release / "SHA256SUMS"
    first = sums.read_text(encoding="utf-8").splitlines()[0]
    sums.write_text(sums.read_text(encoding="utf-8") + first + "\n", encoding="utf-8")
    _refused(release, "twice")


@pytest.mark.parametrize("content", ["", "\n\n"])
def test_an_empty_checksum_file_is_refused(release, content):
    (release / "SHA256SUMS").write_text(content, encoding="utf-8")
    _refused(release, "lists no artifact")


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ("--revision=abc", "is not a 40-hex commit"),
        ("--stream=Main Stream", "is not a stream name"),
        ("--kernelrelease= ", "--kernelrelease is empty"),
        ("--signer-identity=", "--signer-identity is empty"),
    ],
)
def test_bad_provenance_is_refused(release, override, expected):
    _refused(release, expected, override)
