"""scripts/verify-reproducibility.sh compares two real build directories, or gives no verdict.

Its dry run used to hash two identical strings it had just written and print "Byte-level
identical builds verified", and `make verify-reproducibility` ran only that dry run, so the
check could not fail. A dry run now states the comparison and says no comparison ran; the real
comparison names every entry that differs and refuses inputs that would compare nothing.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "verify-reproducibility.sh"
BUILD = {
    "linux-image-7.2.8-lusoris1-mainstream_7.2.8-lusoris1_amd64.deb": b"image\n",
    "linux-libc-dev_7.2.8-lusoris1_amd64.deb": b"libc-dev\n",
    "boot/vmlinuz-7.2.8-lusoris1-mainstream": b"\x00kernel\xff",
}


def _run(*args: str, path_prefix: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False
    )


def _build(root: Path, files: dict[str, bytes] = BUILD) -> Path:
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    return root


@pytest.fixture
def builds(tmp_path):
    return _build(tmp_path / "a"), _build(tmp_path / "b")


def _compare(a: Path, b: Path, **kwargs) -> subprocess.CompletedProcess:
    return _run(f"--dir-a={a}", f"--dir-b={b}", **kwargs)


def test_identical_builds_pass(builds):
    result = _compare(*builds)
    assert result.returncode == 0, result.stderr
    assert "byte-identical" in result.stdout
    assert "Comparing 3 entries with 3" in result.stdout


def test_one_differing_byte_fails_and_is_named(builds):
    a, b = builds
    (b / "boot/vmlinuz-7.2.8-lusoris1-mainstream").write_bytes(b"\x00kernel\xfe")
    result = _compare(a, b)
    assert result.returncode == 1
    assert "./boot/vmlinuz-7.2.8-lusoris1-mainstream" in result.stderr
    assert "byte-identical" not in result.stdout


@pytest.mark.parametrize("side", ["a", "b"])
def test_a_file_on_one_side_only_fails_and_is_named(builds, side):
    a, b = builds
    ((a, b)[side == "b"] / "extra.deb").write_bytes(b"extra\n")
    result = _compare(a, b)
    assert result.returncode == 1
    assert ("<" if side == "a" else ">") + " ./extra.deb" in result.stderr


def test_a_renamed_file_with_the_same_content_fails(builds):
    a, b = builds
    (b / "linux-libc-dev_7.2.8-lusoris1_amd64.deb").rename(b / "linux-libc-dev.deb")
    result = _compare(a, b)
    assert result.returncode == 1
    assert "./linux-libc-dev.deb" in result.stderr


def test_a_differing_link_target_fails(builds):
    a, b = builds
    (a / "vmlinuz").symlink_to("boot/vmlinuz-7.2.8-lusoris1-mainstream")
    (b / "vmlinuz").symlink_to("linux-libc-dev_7.2.8-lusoris1_amd64.deb")
    result = _compare(a, b)
    assert result.returncode == 1
    assert "./vmlinuz" in result.stderr


@pytest.mark.parametrize(
    ("make_b", "message"),
    [
        pytest.param(lambda tmp, a: tmp / "absent", "is not a directory", id="missing"),
        pytest.param(
            lambda tmp, a: (tmp / "empty").mkdir() or tmp / "empty", "holds no files", id="empty"
        ),
        pytest.param(lambda tmp, a: a, "same directory", id="same-directory"),
        pytest.param(
            lambda tmp, a: tmp / "." / "a", "same directory", id="same-directory-other-spelling"
        ),
    ],
)
def test_inputs_that_compare_nothing_are_refused(tmp_path, make_b, message):
    a = _build(tmp_path / "a")
    result = _compare(a, make_b(tmp_path, a))
    assert result.returncode == 1
    assert message in result.stderr
    assert "byte-identical" not in result.stdout


def test_two_empty_directories_are_refused(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    result = _compare(tmp_path / "a", tmp_path / "b")
    assert result.returncode == 1
    assert "holds no files" in result.stderr


@pytest.mark.parametrize("args", [(), ("--dir-a=x",), ("--dir-b=x",)])
def test_a_missing_directory_argument_is_refused(args):
    result = _run(*args)
    assert result.returncode == 1
    assert "both --dir-a and --dir-b are required" in result.stderr


def test_an_unknown_argument_is_refused():
    assert _run("--verify").returncode == 1


def test_the_dry_run_compares_nothing_and_gives_no_verdict(builds):
    a, b = builds
    (b / "extra.deb").write_bytes(b"differs\n")
    result = _run(f"--dir-a={a}", f"--dir-b={b}", "--dry-run")
    assert result.returncode == 0
    assert "No comparison ran" in result.stdout
    for verdict in ("identical", "verified", "passed", "differ"):
        assert verdict not in result.stdout.lower() + result.stderr.lower()


def _stub_diffoscope(tmp_path: Path, status: int) -> Path:
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    stub = stub_dir / "diffoscope"
    stub.write_text(f'#!/bin/sh\necho diffoscope-ran "$@"\nexit {status}\n', encoding="utf-8")
    stub.chmod(0o755)
    return stub_dir


@pytest.mark.parametrize("status", [1, 2])
def test_diffoscope_explains_a_difference_and_its_failure_is_reported(builds, tmp_path, status):
    a, b = builds
    (b / "extra.deb").write_bytes(b"extra\n")
    result = _compare(a, b, path_prefix=_stub_diffoscope(tmp_path, status))
    assert result.returncode == 1
    assert "diffoscope-ran" in result.stdout
    assert ("diffoscope failed with status 2" in result.stderr) == (status == 2)


def test_diffoscope_does_not_run_on_identical_builds(builds, tmp_path):
    result = _compare(*builds, path_prefix=_stub_diffoscope(tmp_path, 0))
    assert result.returncode == 0
    assert "diffoscope-ran" not in result.stdout


def test_make_target_compares_the_directories_it_is_given(builds):
    a, b = builds
    (b / "extra.deb").write_bytes(b"extra\n")
    make = [
        "make",
        "-s",
        "-C",
        str(REPO_ROOT),
        "verify-reproducibility",
        f"DIR_A={a}",
        f"DIR_B={b}",
    ]
    assert (
        subprocess.run([*make, "DRY_RUN=false"], capture_output=True, check=False).returncode != 0
    )
    dry = subprocess.run(make, capture_output=True, text=True, check=False)
    assert dry.returncode == 0
    assert "No comparison ran" in dry.stdout
