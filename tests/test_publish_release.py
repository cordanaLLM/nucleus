"""scripts/publish_release.sh writes SHA256SUMS over exactly the published files, or nothing.

Issue #31: it used to run ``sha256sum -- *.deb > SHA256SUMS || true`` in a hardcoded
``output``, so an empty directory produced a zero-byte SHA256SUMS and exit status 0.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "publish_release.sh"
RELEASE = ("linux-image-7.2.8-lusoris1-mainstream_7.2.8-lusoris1_amd64.deb",
           "linux-libc-dev_7.2.8-lusoris1_amd64.deb",
           "vmlinuz-7.2.8-lusoris1-mainstream",
           "kernel-mainstream-x86_64.config",
           "kernel-mainstream.cdx.json",
           "kernel-mainstream.spdx.json")


def _run(out: Path, *args: str, path_prefix: Path | None = None, cwd: Path | None = None):
    env = dict(os.environ, OUTPUT_DIR=str(out))
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=cwd, check=False
    )


def _release(out: Path, names=RELEASE) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(names):
        (out / name).write_bytes(f"artifact {index} {name}\n".encode())


def test_checksums_cover_exactly_the_published_files_in_output_dir(tmp_path):
    out = tmp_path / "release"
    _release(out)
    result = _run(out, "v7.2.8-mainstream-lusoris1", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    lines = (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    listed = {}
    for line in lines:
        digest, name = line.split("  ", 1)
        listed[name] = digest
    assert set(listed) == set(RELEASE)
    for name, digest in listed.items():
        assert digest == hashlib.sha256((out / name).read_bytes()).hexdigest()
    assert not (tmp_path / "output").exists(), "OUTPUT_DIR is honoured, output/ is not touched"


def test_an_empty_directory_is_refused_and_no_checksum_file_is_written(tmp_path):
    out = tmp_path / "release"
    out.mkdir()
    result = _run(out, "v0.0.0")
    assert result.returncode == 1
    assert "nothing to checksum" in result.stderr
    assert list(out.iterdir()) == []


@pytest.mark.parametrize(
    ("names", "message"),
    [
        (("vmlinuz-7.2.8", "kernel-mainstream-x86_64.config"), "no Debian package"),
        ((*RELEASE, "notes.txt"), "notes.txt is not a publishable artifact"),
        ((*RELEASE, "kernel-mainstream.manifest.json"), "is not a publishable artifact"),
    ],
)
def test_a_directory_that_is_not_a_release_is_refused(tmp_path, names, message):
    out = tmp_path / "release"
    _release(out, names)
    result = _run(out, "v0.0.0")
    assert result.returncode == 1
    assert message in result.stderr
    assert not (out / "SHA256SUMS").exists()


def test_a_zero_byte_artifact_is_refused_before_any_checksum(tmp_path):
    out = tmp_path / "release"
    _release(out)
    (out / RELEASE[0]).write_bytes(b"")
    result = _run(out, "v0.0.0")
    assert result.returncode == 1
    assert f"{RELEASE[0]} is empty" in result.stderr
    assert not (out / "SHA256SUMS").exists()


def test_a_directory_entry_that_is_not_a_regular_file_is_refused(tmp_path):
    out = tmp_path / "release"
    _release(out)
    (out / "nested.deb").mkdir()
    result = _run(out, "v0.0.0")
    assert result.returncode == 1
    assert "nested.deb is not a regular file" in result.stderr


def test_an_existing_checksum_file_is_never_overwritten(tmp_path):
    out = tmp_path / "release"
    _release(out)
    (out / "SHA256SUMS").write_text("signed earlier\n", encoding="utf-8")
    result = _run(out, "v0.0.0")
    assert result.returncode == 1
    assert "SHA256SUMS already exists" in result.stderr
    assert (out / "SHA256SUMS").read_text(encoding="utf-8") == "signed earlier\n"


def test_a_sha256sum_failure_is_not_swallowed(tmp_path):
    out = tmp_path / "release"
    _release(out)
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "sha256sum").write_text("#!/bin/sh\necho 'sha256sum: read error' >&2\nexit 1\n")
    (stub / "sha256sum").chmod(0o755)
    result = _run(out, "v0.0.0", path_prefix=stub)
    assert result.returncode == 1
    assert "sha256sum failed; no SHA256SUMS was written" in result.stderr
    assert sorted(p.name for p in out.iterdir()) == sorted(RELEASE), "no partial file is left"


@pytest.mark.parametrize("missing", ["tag", "dir"])
def test_a_missing_tag_or_directory_is_refused(tmp_path, missing):
    out = tmp_path / "release"
    if missing == "tag":
        _release(out)
        result = _run(out)
        assert "usage:" in result.stderr
    else:
        result = _run(out, "v0.0.0")
        assert "does not exist" in result.stderr
    assert result.returncode == 1
    assert not (out / "SHA256SUMS").exists()


def test_no_failure_is_swallowed_in_the_script():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "|| true" not in text
    assert 'OUTPUT_DIR="${OUTPUT_DIR:-output}"' in text
