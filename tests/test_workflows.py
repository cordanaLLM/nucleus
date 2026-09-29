"""Test suite for GitHub Actions workflows validation and least-privilege permissions."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
BUILD_IMAGE = re.compile(r"ubuntu:26\.04(@sha256:[0-9a-f]{64})?")


def test_workflows_count():
    """Ensure all 12 production CI/CD workflows exist."""
    workflows = list(WORKFLOWS_DIR.glob("*.yml"))
    assert len(workflows) == 12, f"Expected exactly 12 workflows, found {len(workflows)}"


def test_workflows_least_privilege_permissions():
    """Ensure all workflows declare explicit top-level least-privilege permissions."""
    for wf in WORKFLOWS_DIR.glob("*.yml"):
        content = wf.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        assert "permissions" in parsed, f"Workflow {wf.name} missing top-level permissions block"
        perms = parsed["permissions"]
        assert isinstance(perms, dict) or perms == "read-all", (
            f"Workflow {wf.name} permissions must be restricted"
        )
        if isinstance(perms, dict) and "contents" in perms:
            assert perms["contents"] in ("read", "write"), (
                f"Workflow {wf.name} invalid contents permission"
            )


def test_publish_release_ships_kernel_artifact_manifest():
    """Ensure publish-release.yml generates, uploads, and announces the imago kernel artifact manifest."""
    workflow = WORKFLOWS_DIR / "publish-release.yml"
    parsed = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    steps = parsed["jobs"]["publish"]["steps"]
    names = [step.get("name", "") for step in steps]

    build = names.index("Build and Boot the Tagged Stream")
    gate = names.index("Gate the Kernel Artifacts")
    sign = names.index("Checksum and Sign the Release")
    manifest = names.index("Generate Kernel Artifact Manifest")
    publish = names.index("Publish to GitHub Release")
    assert build < gate < sign < manifest < publish, "gate before checksums, manifest after signing"
    assert "imago.nucleus.kernel-artifact.v1" in steps[manifest]["run"]
    assert 'digest(out / f"kernel-{stream}-x86_64.config")' in steps[manifest]["run"]

    files = steps[publish]["with"]["files"]
    for pattern in ("output/*.deb", "output/vmlinuz-*", "output/*.manifest.json", "output/*.config", "output/SHA256SUMS*"):
        assert pattern in files, f"release upload must include {pattern}"

    dispatch = steps[names.index("Dispatch Downstream Notification to imago")]
    payload = dispatch["with"]["client-payload"]
    for key in ("stream", "version", "tag"):
        assert f'"{key}"' in payload, f"downstream payload must carry {key}"


def test_publish_release_builds_gates_and_records_what_it_built():
    """Issue #18: a real build of the tagged stream, gated, with the measured release in the manifest."""
    steps, names = _publish_release_steps()
    build = steps[names.index("Build and Boot the Tagged Stream")]
    assert "./scripts/build_kernel.sh" in build["run"] and "--arch=x86_64" in build["run"]
    assert '--revision="${REVISION}"' in build["run"]
    assert build["env"]["REVISION"] == "${{ steps.meta.outputs.rev }}"
    assert BUILD_IMAGE.fullmatch(build["env"]["BUILD_IMAGE"]) and '"${BUILD_IMAGE}"' in build["run"]
    assert "install-build-toolchain.sh --with-qemu" in build["run"]
    assert "-e GITHUB_TOKEN" not in build["run"] and "ACTIONS_ID_TOKEN" not in build["run"]
    gate = steps[names.index("Gate the Kernel Artifacts")]
    assert "scripts/check_kernel_artifacts.py --dir=output" in gate["run"]
    assert "--cross" not in gate["run"], "the release builds natively and must ship its headers package"
    sign = steps[names.index("Checksum and Sign the Release")]["run"]
    assert sign.index("publish_release.sh") < sign.index("cosign sign-blob")
    assert "OUTPUT_DIR=output" in sign and "|| true" not in sign
    manifest = steps[names.index("Generate Kernel Artifact Manifest")]
    assert manifest["env"]["KERNELRELEASE"] == "${{ steps.gate.outputs.kernelrelease }}"
    assert manifest["env"]["REVISION"] == "${{ steps.rev.outputs.revision }}"
    assert '"release": os.environ["KERNELRELEASE"]' in manifest["run"]
    assert '"revision": os.environ["REVISION"]' in manifest["run"]
    assert "GITHUB_SHA" not in manifest["run"]
    text = (WORKFLOWS_DIR / "publish-release.yml").read_text(encoding="utf-8")
    assert "-lusoris1" not in text.replace("v<version>-<stream>-lusoris1", ""), "no hardcoded revision"
    assert "/dev/urandom" not in text
    checkout = steps[names.index("Checkout Repository")]
    assert checkout["with"]["persist-credentials"] is False


def _build_matrix():
    return yaml.safe_load((WORKFLOWS_DIR / "build-matrix.yml").read_text(encoding="utf-8"))["jobs"]["build"]


def test_build_matrix_compiles_twelve_legs_with_least_privilege():
    job = _build_matrix()
    assert job["permissions"] == {"contents": "read"}
    matrix = job["strategy"]["matrix"]
    assert len(matrix["stream"]) * len(matrix["arch"]) == 12
    runners = {entry["arch"]: entry["runner"] for entry in matrix["include"]}
    assert runners == {"x86_64": "ubuntu-24.04", "arm64": "ubuntu-24.04-arm", "riscv64": "ubuntu-24.04"}
    assert job["runs-on"] == "${{ matrix.runner }}"
    assert BUILD_IMAGE.fullmatch(job["container"]["image"])
    assert job["timeout-minutes"] == "${{ matrix.timeout }}"
    for entry in matrix["include"]:
        assert 30 <= entry["timeout"] <= 240 and 8 <= entry["disk_gib"] <= 30, entry
    for step in job["steps"]:
        assert "${{ matrix" not in step.get("run", ""), step.get("name")
        assert "|| true" not in step.get("run", ""), step.get("name")


def test_build_matrix_gates_boots_and_checksums_real_packages():
    steps = {step.get("name", ""): step for step in _build_matrix()["steps"]}
    order = [step.get("name", "") for step in _build_matrix()["steps"]]
    assert "install-build-toolchain.sh" in steps["Install the Build Toolchain"]["run"]
    probe = steps["Probe Free Disk Space"]
    assert "probe-build-space.sh --need-gib=" in probe["run"]
    assert probe["env"]["NEED_GIB"] == "${{ matrix.disk_gib }}"
    build = steps["Compile Kernel Package"]["run"]
    assert "./scripts/build_kernel.sh" in build and "/usr/lib/ccache" in build
    boot = steps["Boot the Kernel to Userspace"]
    assert boot["if"].startswith("matrix.arch == 'x86_64'")
    assert "scripts/boot_smoke.py" in boot["run"]
    stage = steps["Stage Artifacts and Checksums"]["run"]
    assert 'OUTPUT_DIR="output/${STREAM}-${ARCH}" ./scripts/publish_release.sh' in stage
    assert order.index("Compile Kernel Package") < order.index("Boot the Kernel to Userspace")
    assert order.index("Boot the Kernel to Userspace") < order.index("Stage Artifacts and Checksums")
    restore = steps["Restore the Compiler Cache"]
    save = steps["Save the Compiler Cache"]
    assert restore["uses"].startswith("actions/cache/restore@") and save["uses"].startswith("actions/cache/save@")
    assert "${{ steps.build.outputs.config_sha256 }}" in save["with"]["key"]
    assert "${{ matrix.stream }}-${{ matrix.arch }}" in save["with"]["key"]


def test_build_matrix_compiler_cache_is_restored_by_prefix_and_saved_under_a_new_key():
    """The save key must never equal an existing entry, or the cache is frozen at its first save."""
    steps = {step.get("name", ""): step for step in _build_matrix()["steps"]}
    restore, save = steps["Restore the Compiler Cache"]["with"], steps["Save the Compiler Cache"]["with"]
    prefix = "ccache-${{ matrix.stream }}-${{ matrix.arch }}-"
    assert restore["restore-keys"].strip() == prefix
    assert save["key"].startswith(prefix) and restore["key"].startswith(prefix)
    assert "${{ github.run_id }}-${{ github.run_attempt }}" in save["key"]
    assert "hashFiles" not in restore["key"], "the restore key and the save key use one scheme"


def test_publish_release_container_cannot_rewrite_what_the_host_runs_next():
    """The checkout is read-only in the build container; only output/, records/ and work dirs are not."""
    steps, names = _publish_release_steps()
    run = steps[names.index("Build and Boot the Tagged Stream")]["run"]
    mounts = re.findall(r'-v "?([^"\s)]+)"?', run)
    assert '${GITHUB_WORKSPACE}:/forge:ro' in mounts
    writable = [m for m in mounts if not m.endswith(":ro")]
    assert sorted(writable) == sorted([
        "${GITHUB_WORKSPACE}/output:/forge/output",
        "${GITHUB_WORKSPACE}/records:/forge/records",
        "${RUNNER_TEMP}/nucleus-build:/work/temp",
        "/mnt/nucleus-build:/work/mnt",
    ]), writable
    assert "/var/run/docker.sock" not in run and "--privileged" not in run


def test_publish_release_boots_the_kernel_before_anything_is_signed():
    steps, names = _publish_release_steps()
    run = steps[names.index("Build and Boot the Tagged Stream")]["run"]
    assert run.index("./scripts/build_kernel.sh") < run.index("python3 scripts/boot_smoke.py")
    assert '--log="records/boot-${STREAM}-x86_64.log"' in run
    assert 'exit "${status}"' in run and "|| status=$?" in run
    assert names.index("Build and Boot the Tagged Stream") < names.index("Checksum and Sign the Release")
    keep = steps[names.index("Keep the Build Record and Boot Log")]
    assert keep["if"] == "always()" and keep["with"]["path"] == "records/"


def test_every_ubuntu_26_04_container_is_pinned_to_one_digest():
    """A release must compile in the userland the matrix proved; a tag can move, a digest cannot."""
    found: dict[str, list[str]] = {}
    for wf in sorted(WORKFLOWS_DIR.glob("*.yml")):
        for line in wf.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#"):
                continue
            for match in BUILD_IMAGE.finditer(line):
                found.setdefault(match.group(0), []).append(wf.name)
    assert len(found) == 1, found
    image, users = next(iter(found.items()))
    assert "@sha256:" in image
    for name in ("build-matrix.yml", "publish-release.yml", "ci.yml", "verify-requirements.yml"):
        assert name in users, name


def test_no_run_block_interpolates_event_data():
    """Event data reaches a script through env:, never through ${{ }} inside a run: block."""
    event_data = re.compile(r"\$\{\{[^}]*github\.(event|head_ref)[^}]*\}\}")
    for wf in WORKFLOWS_DIR.glob("*.yml"):
        parsed = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for job in parsed["jobs"].values():
            for step in job.get("steps", []):
                assert not event_data.search(step.get("run", "")), f"{wf.name}: {step.get('name')}"


def test_verify_requirements_fetches_pinned_documents_and_keeps_the_report():
    """The dispatch payload goes to the fetch script through env; the report is kept."""
    parsed = yaml.safe_load((WORKFLOWS_DIR / "verify-requirements.yml").read_text(encoding="utf-8"))
    steps = {step.get("name", ""): step for step in parsed["jobs"]["verify"]["steps"]}
    fetch = steps["Fetch Requirement Documents"]
    assert "scripts/fetch-kernel-requirements.sh" in fetch["run"]
    for key in ("PAYLOAD_SOURCE", "PAYLOAD_REF", "PAYLOAD_SHA256", "PAYLOAD_CORRELATION_ID"):
        assert key in fetch["env"], key
    verify_step = steps["Verify Requirements Against the Declared KConfig"]
    verify = verify_step["run"]
    assert "--report-json=" in verify and "--nucleus-revision=" in verify
    assert steps["Upload Verification Report"]["if"] == "always()"
    # The verifier is piped through tee: without pipefail its exit status is lost and the gate
    # reports green. An explicit bash runs with -eo pipefail; the step also sets it itself.
    assert parsed["jobs"]["verify"]["defaults"]["run"]["shell"] == "bash"
    assert verify_step.get("shell", "bash") == "bash"
    assert "| tee" in verify and "set -euo pipefail" in verify


def test_verify_requirements_never_cancels_a_pending_dispatch():
    """A group holds one pending run; each dispatch gets its own, so none replaces another."""
    parsed = yaml.safe_load((WORKFLOWS_DIR / "verify-requirements.yml").read_text(encoding="utf-8"))
    concurrency = parsed["concurrency"]
    group = " ".join(concurrency["group"].split())
    assert "github.event_name == 'repository_dispatch'" in group
    assert "github.run_id" in group
    assert concurrency["cancel-in-progress"] is False


def _publish_release_steps():
    parsed = yaml.safe_load((WORKFLOWS_DIR / "publish-release.yml").read_text(encoding="utf-8"))
    steps = parsed["jobs"]["publish"]["steps"]
    return steps, [step.get("name", "") for step in steps]


def test_publish_release_resolves_tags_without_fallback():
    """A kernel tag must resolve to exactly one versions.json stream; there is no default."""
    steps, names = _publish_release_steps()
    resolve = steps[names.index("Resolve Stream and Version")]
    assert resolve["id"] == "meta"
    assert "scripts/resolve_release_tag.py" in resolve["run"]
    assert '--github-output "${GITHUB_OUTPUT}"' in resolve["run"]
    assert "python3 -c" not in resolve["run"], "the tag grammar lives in scripts/resolve_release_tag.py"
    assert "${{" not in resolve["run"], "the tag reaches the resolver through env, never inline"


def test_publish_release_tag_and_ref_cannot_diverge():
    """A dispatched run must start from the tag it releases, and the release names that tag."""
    steps, names = _publish_release_steps()
    resolve = steps[names.index("Resolve Stream and Version")]
    assert '--ref "${GITHUB_REF}"' in resolve["run"], "the resolver must compare the run's ref with the tag"
    release = steps[names.index("Publish to GitHub Release")]
    assert release["with"]["tag_name"] == "${{ steps.meta.outputs.release_tag }}"


def _guard_step():
    steps, names = _publish_release_steps()
    return steps, names, steps[names.index("Require KERNEL_FORGE_TOKEN for the Downstream Dispatch")]


def _run_guard(token_set: str) -> subprocess.CompletedProcess:
    """Execute the guard's run block the way the runner does, with only the boolean in its env."""
    _, _, guard = _guard_step()
    env = {"PATH": os.environ["PATH"], "KERNEL_FORGE_TOKEN_SET": token_set}
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-c", guard["run"]],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_publish_release_dispatch_requires_kernel_forge_token():
    """GITHUB_TOKEN cannot dispatch to another repository, so nothing falls back to it."""
    steps, names, guard = _guard_step()
    dispatch = steps[names.index("Dispatch Downstream Notification to imago")]
    assert dispatch["with"]["token"] == "${{ secrets.KERNEL_FORGE_TOKEN }}"
    assert guard["env"] == {"KERNEL_FORGE_TOKEN_SET": "${{ secrets.KERNEL_FORGE_TOKEN != '' }}"}
    assert "${{" not in guard["run"], "the guard reads its env, never an inline expression"
    payload = dispatch["with"]["client-payload"]
    assert "steps.meta.outputs.release_version" in payload
    assert "steps.meta.outputs.release_tag" in payload


def test_publish_release_stops_before_building_or_publishing_without_the_token():
    """A missing credential must publish nothing: the guard runs before every other step."""
    _, names, _ = _guard_step()
    guard = names.index("Require KERNEL_FORGE_TOKEN for the Downstream Dispatch")
    for later in ("Build and Boot the Tagged Stream", "Publish to GitHub Release"):
        assert guard < names.index(later), f"the guard must run before {later!r}"


def test_publish_release_secret_is_read_only_by_the_dispatch_step():
    """ADR-0005: the token value reaches the dispatch step alone; the guard sees a boolean."""
    steps, names, _ = _guard_step()
    dispatch = names.index("Dispatch Downstream Notification to imago")
    text = (WORKFLOWS_DIR / "publish-release.yml").read_text(encoding="utf-8")
    plain = re.findall(r"secrets\.KERNEL_FORGE_TOKEN(?! != '')", text)
    assert len(plain) == 1 and "secrets.KERNEL_FORGE_TOKEN" in str(steps[dispatch])
    assert "secrets.GITHUB_TOKEN" not in text, "there is no fallback to GITHUB_TOKEN"


def test_publish_release_guard_refuses_without_the_token():
    """The guard's run block exits 1 with an annotation naming the secret when it is not set."""
    for unset in ("false", ""):
        result = _run_guard(unset)
        assert result.returncode == 1, (unset, result.stdout, result.stderr)
        assert result.stderr.startswith("::error title=Missing secret KERNEL_FORGE_TOKEN::")
        assert "GITHUB_TOKEN cannot reach it" in result.stderr
        assert result.stdout == ""


def test_publish_release_guard_passes_with_the_token():
    """With the secret set the guard is silent and exits 0."""
    result = _run_guard("true")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_verify_requirements_resolves_every_leg_from_its_verified_source():
    """The resolved level: plan, fetch and verify, resolve with the survival check, verify."""
    parsed = yaml.safe_load((WORKFLOWS_DIR / "verify-requirements.yml").read_text(encoding="utf-8"))
    jobs = parsed["jobs"]
    assert "--plan=" in {s.get("name"): s for s in jobs["verify"]["steps"]}[
        "Plan the Resolved Evidence"
    ]["run"]
    resolve = jobs["resolve"]
    assert resolve["needs"] == "verify" and BUILD_IMAGE.fullmatch(resolve["container"]["image"])
    assert resolve["strategy"]["matrix"] == "${{ fromJSON(needs.verify.outputs.plan) }}"
    steps = {step.get("name", ""): step for step in resolve["steps"]}
    install = steps["Install the Resolution Toolchain"]["run"]
    for package in ("dwarves", "gpgv", "gcc-aarch64-linux-gnu", "gcc-riscv64-linux-gnu"):
        assert package in install, package
    fetch = steps["Fetch and Verify the Kernel Source"]
    assert "scripts/fetch-kernel-source.sh" in fetch["run"] and "STREAM" in fetch["env"]
    # The download is cached in --work and verified again on reuse; the tree is never cached.
    restore, save = steps["Restore the Source Download"], steps["Save the Source Download"]
    assert restore["uses"].startswith("actions/cache/restore@")
    assert save["uses"].startswith("actions/cache/save@")
    assert restore["with"]["path"] == save["with"]["path"] == ".kernel-source-work"
    assert f"--work={restore['with']['path']}" in fetch["run"]
    assert '--dest="${RUNNER_TEMP}/linux-${STREAM}"' in fetch["run"]
    resolve_step = steps["Resolve the Configuration"]
    assert "--source-tree=" in resolve_step["run"] and "ARCHES" in resolve_step["env"]
    for step in resolve["steps"]:
        assert "${{ matrix" not in step.get("run", ""), step.get("name")
    verify = {s.get("name", ""): s for s in jobs["verify-resolved"]["steps"]}
    assert "--resolved-config=" in verify["Verify Requirements Against the Resolved KConfig"]["run"]
    assert jobs["verify-resolved"]["needs"] == ["verify", "resolve"]
    for job in jobs.values():
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False, "no job pushes"


def test_required_aggregator_contract():
    """Ensure required-aggregator.yml defines the required-checks job."""
    aggregator = WORKFLOWS_DIR / "required-aggregator.yml"
    assert aggregator.exists(), "required-aggregator.yml must exist"
    content = aggregator.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    assert "jobs" in parsed
    assert "required-checks" in parsed["jobs"], "required-aggregator must define job 'required-checks'"


def test_actionlint_passes():
    """Ensure all workflows pass actionlint with zero errors."""
    actionlint_bin = shutil.which("actionlint")
    if not actionlint_bin:
        for candidate in [
            Path("/usr/local/bin/actionlint"),
            Path("/usr/bin/actionlint"),
            Path.home() / "go" / "bin" / "actionlint",
        ]:
            if candidate.exists() and candidate.is_file():
                actionlint_bin = str(candidate)
                break
    if not actionlint_bin:
        pytest.skip("actionlint binary not found in PATH or standard locations")

    workflows = list(WORKFLOWS_DIR.glob("*.yml"))
    cmd = [actionlint_bin] + [str(w) for w in workflows]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"actionlint failed on workflows:\n{res.stdout}\n{res.stderr}"
