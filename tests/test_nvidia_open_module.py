"""Test suite for NVIDIA Open GPU kernel module compilation and CI/CD workflow."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-nvidia-open-module.sh"
WORKFLOW_FILE = REPO_ROOT / ".github" / "workflows" / "nvidia-open-module.yml"
VERSIONS_JSON = REPO_ROOT / "versions.json"
NVIDIA_OPEN = json.loads(VERSIONS_JSON.read_text(encoding="utf-8"))["modules"]["nvidia_open"]

needs_dpkg = pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not installed")


def _make_fake_headers_deb(out: Path, kr: str, version: str = "1.0.0") -> Path:
    """Build a minimal valid linux-headers deb package with dpkg-deb."""
    pkg_dir = out / f"pkg-linux-headers-{kr}"
    debian = pkg_dir / "DEBIAN"
    debian.mkdir(parents=True, exist_ok=True)
    control = (
        f"Package: linux-headers-{kr}\n"
        f"Version: {version}\n"
        "Architecture: amd64\n"
        "Maintainer: test <test@example.com>\n"
        "Description: test kernel headers package\n"
    )
    (debian / "control").write_text(control, encoding="utf-8")
    headers_tree = pkg_dir / "usr" / "src" / f"linux-headers-{kr}"
    headers_tree.mkdir(parents=True, exist_ok=True)
    (headers_tree / "Makefile").write_text("# mock kernel headers makefile\n", encoding="utf-8")

    deb_path = out / f"linux-headers-{kr}_{version}_amd64.deb"
    subprocess.run(
        [
            "dpkg-deb",
            "-Zgzip",
            "--root-owner-group",
            "--build",
            str(pkg_dir),
            str(deb_path),
        ],
        check=True,
        capture_output=True,
    )
    return deb_path


def _make_fake_non_headers_deb(
    out: Path, name: str = "wrong-package", version: str = "1.0.0"
) -> Path:
    """Build a deb package whose Package field does not start with linux-headers-."""
    pkg_dir = out / f"pkg-{name}"
    debian = pkg_dir / "DEBIAN"
    debian.mkdir(parents=True, exist_ok=True)
    control = (
        f"Package: {name}\n"
        f"Version: {version}\n"
        "Architecture: amd64\n"
        "Maintainer: test <test@example.com>\n"
        "Description: test non-headers package\n"
    )
    (debian / "control").write_text(control, encoding="utf-8")
    deb_path = out / f"{name}_{version}_amd64.deb"
    subprocess.run(
        [
            "dpkg-deb",
            "-Zgzip",
            "--root-owner-group",
            "--build",
            str(pkg_dir),
            str(deb_path),
        ],
        check=True,
        capture_output=True,
    )
    return deb_path


def _make_tarball_with_hash(
    out: Path, filename: str = "mock-nvidia.tar.gz"
) -> tuple[Path, str, str]:
    """Create a git archive tarball, as GitHub serves one, and return (path, sha256, commit)."""
    tar_path = out / filename
    src_dir = out / "src_content"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "README.md").write_text("mock nvidia open\n", encoding="utf-8")
    # Several MiB of incompressible data, so the archive outlasts a reader that stops early.
    (src_dir / "blob.bin").write_bytes(os.urandom(8 * 1024 * 1024))
    git = ["git", "-C", str(src_dir), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    git_env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (
        ["init", "-q"],
        ["add", "README.md", "blob.bin"],
        ["commit", "-q", "--no-gpg-sign", "-m", "mock"],
    ):
        subprocess.run([*git, *args], check=True, capture_output=True, env=git_env, timeout=60)
    commit = subprocess.run(
        [*git, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=git_env,
        timeout=60,
    ).stdout.strip()
    subprocess.run(
        [
            *git,
            "archive",
            "--format=tar.gz",
            "--prefix=open-gpu-kernel-modules/",
            f"--output={tar_path}",
            "HEAD",
        ],
        check=True,
        capture_output=True,
        env=git_env,
        timeout=60,
    )
    sha256 = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    return tar_path, sha256, commit


def _pinned_manifest(out: Path, sha256: str, commit: str) -> Path:
    """Write a copy of versions.json whose nvidia_open pin names the mock archive."""
    manifest = json.loads(VERSIONS_JSON.read_text(encoding="utf-8"))
    manifest["modules"]["nvidia_open"]["source"]["sha256"] = sha256
    manifest["modules"]["nvidia_open"]["source"]["commit"] = commit
    path = out / "versions.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_manifest_nvidia_open_entry_matches_schema():
    """Ensure versions.json modules.nvidia_open matches versions.schema.json."""
    manifest = json.loads(VERSIONS_JSON.read_text(encoding="utf-8"))
    schema = json.loads((REPO_ROOT / "versions.schema.json").read_text(encoding="utf-8"))

    jsonschema.validate(instance=manifest, schema=schema)

    assert "modules" in manifest, "versions.json must declare 'modules'"
    assert "nvidia_open" in manifest["modules"], "modules must declare 'nvidia_open'"

    nv = manifest["modules"]["nvidia_open"]
    assert nv["version"] == "615.78.08"
    assert nv["streams"] == ["bleeding", "mainstream"]
    assert nv["architectures"] == ["x86_64"]

    source = nv["source"]
    assert source["kind"] == "tarball"
    assert (
        source["url"]
        == "https://github.com/NVIDIA/open-gpu-kernel-modules/archive/refs/tags/615.78.08.tar.gz"
    )
    assert source["sha256"] == "eac0ecd52facab28ad2963cf7e03ae8d2289d596ad590c703b5f72d65899e2bf"
    assert source["commit"] == "1a82fbfda8f321b056eb3568850f9316c5e465c5"


def test_nvidia_open_workflow_shape():
    """Ensure nvidia-open-module.yml conforms to repository contracts and shapes."""
    assert WORKFLOW_FILE.is_file(), f"{WORKFLOW_FILE.name} must exist"
    parsed = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))

    # Permissions
    assert parsed.get("permissions") == {"contents": "read"}

    # Triggers
    on_triggers = parsed.get("on", parsed.get(True, {}))
    assert "workflow_dispatch" in on_triggers
    assert "schedule" in on_triggers
    assert "pull_request" in on_triggers
    assert "push" in on_triggers

    # Filtered paths
    expected_paths = [
        "versions.json",
        "versions.schema.json",
        "scripts/check-nvidia-open-module.sh",
        ".github/workflows/nvidia-open-module.yml",
    ]
    assert sorted(on_triggers["pull_request"]["paths"]) == sorted(expected_paths)
    assert sorted(on_triggers["push"]["paths"]) == sorted(expected_paths)
    assert on_triggers["push"]["branches"] == ["main"]


def test_nvidia_open_workflow_builds_in_the_release_image():
    """The build leg runs in the container image release-build.yml builds kernels in."""
    parsed = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))
    release_build_workflow = REPO_ROOT / ".github" / "workflows" / "release-build.yml"
    build_parsed = yaml.safe_load(release_build_workflow.read_text(encoding="utf-8"))
    build_step = next(
        s
        for s in build_parsed["jobs"]["build"]["steps"]
        if s.get("name") == "Build and Boot the Tagged Stream"
    )
    expected_image = build_step["env"]["BUILD_IMAGE"]

    build_job = parsed["jobs"]["build"]
    actual_image = build_job["container"]["image"]
    assert actual_image == expected_image, (
        f"Image digest mismatch: {actual_image} != {expected_image}"
    )


def test_nvidia_open_workflow_takes_its_legs_and_signer_from_the_repository():
    """The matrix comes from versions.json and each leg verifies the release signature."""
    parsed = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))
    build_job = parsed["jobs"]["build"]

    # Matrix is dynamically computed from versions.json, not hardcoded
    assert build_job["strategy"]["matrix"] == "${{ fromJSON(needs.plan.outputs.matrix) }}"
    build_job_text = yaml.safe_dump(build_job)
    for stream in ("lts", "mainstream", "realtime", "bleeding"):
        assert f"stream: {stream}" not in build_job_text
        assert f"- {stream}" not in build_job_text

    # Cosign identity and issuer
    steps = build_job["steps"]
    build_mod_step = next(
        s for s in steps if s.get("name") == "Download Release Assets and Build Module"
    )
    run_script = build_mod_step["run"]
    assert "cosign verify-blob" in run_script
    assert "scripts/release_signer_identity.py" in run_script
    assert '--certificate-oidc-issuer "https://token.actions.githubusercontent.com"' in run_script

    # No hardcoded NVIDIA URL in workflow or script
    assert "github.com/NVIDIA" not in WORKFLOW_FILE.read_text(encoding="utf-8")
    assert "github.com/NVIDIA" not in SCRIPT.read_text(encoding="utf-8")


def test_nvidia_open_workflow_names_no_repository_and_bounds_every_job():
    """Ensure a fork runs the workflow against its own releases, and no job or download can hang."""
    text = WORKFLOW_FILE.read_text(encoding="utf-8")
    assert "cordanaLLM/nucleus" not in text, "the release lookup must use GITHUB_REPOSITORY"
    assert 'os.environ["GITHUB_REPOSITORY"]' in text
    parsed = yaml.safe_load(text)
    for name, job in parsed["jobs"].items():
        assert isinstance(job.get("timeout-minutes"), int), f"job {name} has no timeout-minutes"
    assert "urlopen(req, timeout=" in text
    assert "urlopen(down_req, timeout=" in text


@needs_dpkg
def test_check_nvidia_open_module_print_plan(tmp_path: Path):
    """Ensure --print-plan states url, sha, KR, legs and exits 0."""
    kr = "7.3.0-rc7-lusoris1-bleeding"
    deb = _make_fake_headers_deb(tmp_path, kr)

    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            "--print-plan",
            f"--versions={VERSIONS_JSON}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"--print-plan failed:\n{res.stdout}\n{res.stderr}"
    assert f"url: {NVIDIA_OPEN['source']['url']}" in res.stdout
    assert f"sha256: {NVIDIA_OPEN['source']['sha256']}" in res.stdout
    assert f"kernelrelease: {kr}" in res.stdout
    assert "bleeding" in res.stdout
    assert "mainstream" in res.stdout


@needs_dpkg
def test_check_nvidia_open_module_refuses_tampered_sha256(tmp_path: Path):
    """Ensure tampered tarball fails sha256 check and exits non-zero."""
    kr = "7.3.0-rc7-lusoris1-bleeding"
    deb = _make_fake_headers_deb(tmp_path, kr)
    tampered_tarball = tmp_path / "tampered.tar.gz"
    tampered_tarball.write_text("not-authentic-nvidia-tarball\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()

    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            f"--tarball={tampered_tarball}",
            f"--work={work}",
            f"--versions={VERSIONS_JSON}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0, "Tampered sha256 must exit non-zero"
    assert "sha256" in res.stderr.lower(), f"Expected sha256 refusal in stderr:\n{res.stderr}"


@needs_dpkg
def test_check_nvidia_open_module_refuses_missing_headers_deb(tmp_path: Path):
    """Ensure missing headers deb package is refused."""
    nonexistent = tmp_path / "nonexistent-headers.deb"
    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={nonexistent}",
            "--print-plan",
            f"--versions={VERSIONS_JSON}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "headers deb missing" in res.stderr.lower()


@needs_dpkg
def test_check_nvidia_open_module_refuses_empty_headers_deb(tmp_path: Path):
    """Ensure zero-byte headers deb package is refused."""
    empty_deb = tmp_path / "empty-headers.deb"
    empty_deb.touch()
    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={empty_deb}",
            "--print-plan",
            f"--versions={VERSIONS_JSON}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "headers deb empty" in res.stderr.lower()


@needs_dpkg
def test_check_nvidia_open_module_refuses_unknown_kernel_release(tmp_path: Path):
    """Ensure deb package with unknown/foreign Package name is refused."""
    deb = _make_fake_non_headers_deb(tmp_path, "foreign-library")
    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            "--print-plan",
            f"--versions={VERSIONS_JSON}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "kernel release unknown" in res.stderr.lower()


@needs_dpkg
def test_check_nvidia_open_module_refuses_unknown_tag(tmp_path: Path):
    """Ensure version/tag not equal to the pin is refused."""
    kr = "7.3.0-rc7-lusoris1-bleeding"
    deb = _make_fake_headers_deb(tmp_path, kr)
    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            "--tag=999.00.00",
            "--print-plan",
            f"--versions={VERSIONS_JSON}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "unknown tag" in res.stderr.lower()
    assert "615.78.08" in res.stderr


@needs_dpkg
def test_check_nvidia_open_module_refuses_missing_nvidia_drm_ko(tmp_path: Path):
    """Ensure build that omits nvidia-drm.ko is refused."""
    kr = "7.3.0-rc7-lusoris1-bleeding"
    deb = _make_fake_headers_deb(tmp_path, kr)
    tarball, sha256, commit = _make_tarball_with_hash(tmp_path)

    custom_manifest_path = _pinned_manifest(tmp_path, sha256, commit)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Stub make: generates only nvidia.ko, nvidia-uvm.ko, nvidia-modeset.ko (missing nvidia-drm.ko)
    make_stub = bin_dir / "make"
    make_script = """#!/usr/bin/env bash
set -eu
mkdir -p kernel-open
touch kernel-open/nvidia.ko
touch kernel-open/nvidia-uvm.ko
touch kernel-open/nvidia-modeset.ko
exit 0
"""
    make_stub.write_text(make_script, encoding="utf-8")
    make_stub.chmod(0o755)

    # Stub modinfo: returns valid vermagic
    modinfo_stub = bin_dir / "modinfo"
    modinfo_script = f"""#!/usr/bin/env bash
echo "{kr} SMP preempt mod_unload"
exit 0
"""
    modinfo_stub.write_text(modinfo_script, encoding="utf-8")
    modinfo_stub.chmod(0o755)

    work = tmp_path / "work"
    work.mkdir()

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            f"--tarball={tarball}",
            f"--work={work}",
            f"--versions={custom_manifest_path}",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "nvidia-drm.ko" in res.stderr


@needs_dpkg
@pytest.mark.parametrize(
    "built_release",
    ["6.18.56-lusoris1-lts", "7.3.0-rc7-lusoris1-bleeding2"],
    ids=["another-release", "release-with-a-suffix"],
)
def test_check_nvidia_open_module_refuses_vermagic_mismatch(tmp_path: Path, built_release: str):
    """Ensure vermagic of another kernel release, or one the target release only prefixes, is refused."""
    kr = "7.3.0-rc7-lusoris1-bleeding"
    deb = _make_fake_headers_deb(tmp_path, kr)
    tarball, sha256, commit = _make_tarball_with_hash(tmp_path)

    custom_manifest_path = _pinned_manifest(tmp_path, sha256, commit)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Stub make: generates all 4 modules
    make_stub = bin_dir / "make"
    make_script = """#!/usr/bin/env bash
set -eu
mkdir -p kernel-open
touch kernel-open/nvidia.ko
touch kernel-open/nvidia-uvm.ko
touch kernel-open/nvidia-modeset.ko
touch kernel-open/nvidia-drm.ko
exit 0
"""
    make_stub.write_text(make_script, encoding="utf-8")
    make_stub.chmod(0o755)

    # Stub modinfo: returns vermagic for a different kernel release
    modinfo_stub = bin_dir / "modinfo"
    modinfo_script = f"""#!/usr/bin/env bash
echo "{built_release} SMP preempt mod_unload"
exit 0
"""
    modinfo_stub.write_text(modinfo_script, encoding="utf-8")
    modinfo_stub.chmod(0o755)

    work = tmp_path / "work"
    work.mkdir()

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            f"--tarball={tarball}",
            f"--work={work}",
            f"--versions={custom_manifest_path}",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert "vermagic" in res.stderr.lower()


@needs_dpkg
def test_check_nvidia_open_module_resolves_a_relative_work_directory(tmp_path: Path):
    """Ensure relative --work directory is made absolute properly."""
    deb = _make_fake_headers_deb(tmp_path, "7.2.10-lusoris1-mainstream")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_stub = bin_dir / "curl"
    curl_stub.write_text("#!/usr/bin/env bash\nexit 22\n", encoding="utf-8")
    curl_stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            "--work=nvidia-work-mainstream",
            f"--versions={VERSIONS_JSON}",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert f"==> Work directory {tmp_path / 'nvidia-work-mainstream'}" in res.stdout


@needs_dpkg
def test_check_nvidia_open_module_happy_path_mock(tmp_path: Path):
    """Ensure happy path executes build, validates all 4 modules, and logs module paths."""
    kr = "7.2.10-lusoris1-mainstream"
    deb = _make_fake_headers_deb(tmp_path, kr)
    tarball, sha256, commit = _make_tarball_with_hash(tmp_path)

    custom_manifest_path = _pinned_manifest(tmp_path, sha256, commit)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    # Stub make: generates all 4 modules
    make_stub = bin_dir / "make"
    make_script = """#!/usr/bin/env bash
set -eu
mkdir -p kernel-open
touch kernel-open/nvidia.ko
touch kernel-open/nvidia-uvm.ko
touch kernel-open/nvidia-modeset.ko
touch kernel-open/nvidia-drm.ko
exit 0
"""
    make_stub.write_text(make_script, encoding="utf-8")
    make_stub.chmod(0o755)

    # Stub modinfo: returns valid vermagic matching kr
    modinfo_stub = bin_dir / "modinfo"
    modinfo_script = f"""#!/usr/bin/env bash
echo "{kr} SMP preempt mod_unload"
exit 0
"""
    modinfo_stub.write_text(modinfo_script, encoding="utf-8")
    modinfo_stub.chmod(0o755)

    work = tmp_path / "work"
    work.mkdir()

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            f"--tarball={tarball}",
            f"--work={work}",
            f"--versions={custom_manifest_path}",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Happy path build failed:\n{res.stdout}\n{res.stderr}"
    for module in ("nvidia", "nvidia-uvm", "nvidia-modeset", "nvidia-drm"):
        assert f"built module: {work}/src/kernel-open/{module}.ko" in res.stdout
    assert f"nvidia-open 615.78.08 built against {kr}: all 4 modules verified" in res.stdout


@needs_dpkg
def test_check_nvidia_open_module_refuses_an_archive_of_another_commit(tmp_path: Path):
    """An archive whose sha256 is pinned but whose embedded commit is not the pinned one is refused."""
    kr = "7.2.10-lusoris1-mainstream"
    deb = _make_fake_headers_deb(tmp_path, kr)
    tarball, sha256, _ = _make_tarball_with_hash(tmp_path)
    custom_manifest = json.loads(VERSIONS_JSON.read_text(encoding="utf-8"))
    custom_manifest["modules"]["nvidia_open"]["source"]["sha256"] = sha256
    custom_manifest_path = tmp_path / "versions.json"
    custom_manifest_path.write_text(json.dumps(custom_manifest), encoding="utf-8")
    work = tmp_path / "work"
    res = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            f"--headers-deb={deb}",
            f"--tarball={tarball}",
            f"--work={work}",
            f"--versions={custom_manifest_path}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert res.returncode != 0
    assert "commit mismatch" in res.stderr
    assert NVIDIA_OPEN["source"]["commit"] in res.stderr
