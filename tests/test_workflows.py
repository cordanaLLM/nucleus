"""Test suite for GitHub Actions workflows validation and least-privilege permissions."""

import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
# The first step of every job a pull request starts (HISS-18); it runs only on a draft.
DRAFT_STEP = "Stop on a draft pull request"
BUILD_IMAGE = re.compile(r"ubuntu:26\.04(@sha256:[0-9a-f]{64})?")
RULESET = REPO_ROOT / ".github" / "rulesets" / "main.json"

# Job conditions that hold on every run that is not cancelled, whitespace removed. The
# selection below mirrors how praetorctl picks required checks (praetor docs/adoption.md,
# "Which jobs the ruleset requires") for the workflow shapes this repository uses.
EVERY_RUN_CONDITIONS = {"always()", "!cancelled()", "success()||failure()", "failure()||success()"}


def test_workflows_count():
    """Ensure all 18 production CI/CD workflows exist."""
    workflows = list(WORKFLOWS_DIR.glob("*.yml"))
    assert len(workflows) == 18, f"Expected exactly 18 workflows, found {len(workflows)}"


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


def _release_build_steps():
    parsed = yaml.safe_load((WORKFLOWS_DIR / "release-build.yml").read_text(encoding="utf-8"))
    steps = parsed["jobs"]["build"]["steps"]
    return steps, [step.get("name", "") for step in steps]


def test_publish_release_ships_kernel_artifact_manifest():
    """Ensure publish-release.yml and release-build.yml generate, upload, and announce the imago kernel artifact manifest."""
    build_steps, build_names = _release_build_steps()
    build = build_names.index("Build and Boot the Tagged Stream")
    gate = build_names.index("Gate the Kernel Artifacts")
    sign = build_names.index("Sign and Verify the Release")
    manifest = build_names.index("Generate Kernel Artifact Manifest")
    upload = build_names.index("Upload Release Artifacts")
    assert build < gate < sign < manifest < upload, "gate before checksums, manifest after signing"
    run = build_steps[manifest]["run"]
    assert "python3 scripts/kernel_artifact_manifest.py --dir=output" in run
    bound = {
        "--stream": "STREAM",
        "--version": "VERSION",
        "--kernelrelease": "KERNELRELEASE",
        "--tag": "RELEASE_TAG",
        "--revision": "REVISION",
        "--repository": "GITHUB_REPOSITORY",
        "--signer-identity": "SIGNER_IDENTITY",
    }
    for option, variable in bound.items():
        assert f'{option}="${{{variable}}}"' in run, f"the manifest step must pass {option}"

    publish_wf = WORKFLOWS_DIR / "publish-release.yml"
    publish_parsed = yaml.safe_load(publish_wf.read_text(encoding="utf-8"))
    publish_steps = publish_parsed["jobs"]["publish"]["steps"]
    publish_names = [step.get("name", "") for step in publish_steps]
    publish = publish_names.index("Publish to GitHub Release")

    files = publish_steps[publish]["with"]["files"]
    for pattern in (
        "output/*.deb",
        "output/vmlinuz-*",
        "output/*.manifest.json",
        "output/*.config",
        "output/SHA256SUMS*",
        "output/*.provenance.sigstore.json",
    ):
        assert pattern in files, f"release upload must include {pattern}"

    dispatch = publish_steps[publish_names.index("Dispatch Downstream Notification to imago")]
    payload = dispatch["with"]["client-payload"]
    for key in ("stream", "version", "tag"):
        assert f'"{key}"' in payload, f"downstream payload must carry {key}"


def test_publish_release_builds_gates_and_records_what_it_built():
    """Issue #18: a real build of the tagged stream, gated, with the measured release in the manifest."""
    steps, names = _release_build_steps()
    build = steps[names.index("Build and Boot the Tagged Stream")]
    assert "./scripts/build_kernel.sh" in build["run"] and "--arch=x86_64" in build["run"]
    assert '--revision="${REVISION}"' in build["run"]
    assert build["env"]["REVISION"] == "${{ steps.meta.outputs.rev }}"
    assert BUILD_IMAGE.fullmatch(build["env"]["BUILD_IMAGE"]) and '"${BUILD_IMAGE}"' in build["run"]
    assert "install-build-toolchain.sh --with-qemu" in build["run"]
    assert "-e GITHUB_TOKEN" not in build["run"] and "ACTIONS_ID_TOKEN" not in build["run"]
    gate = steps[names.index("Gate the Kernel Artifacts")]
    assert "scripts/check_kernel_artifacts.py --dir=output" in gate["run"]
    assert "--cross" not in gate["run"], (
        "the release builds natively and must ship its headers package"
    )
    assert (
        names.index("Write SBOMs and Checksum the Release")
        < names.index("Attest Build Provenance")
        < names.index("Sign and Verify the Release")
    )
    checksum_run = steps[names.index("Write SBOMs and Checksum the Release")]["run"]
    assert (
        "publish_release.sh" in checksum_run
        and "OUTPUT_DIR=output" in checksum_run
        and "|| true" not in checksum_run
    )
    sign_run = steps[names.index("Sign and Verify the Release")]["run"]
    assert "cosign sign-blob" in sign_run
    manifest = steps[names.index("Generate Kernel Artifact Manifest")]
    assert manifest["env"]["KERNELRELEASE"] == "${{ steps.gate.outputs.kernelrelease }}"
    assert manifest["env"]["REVISION"] == "${{ steps.rev.outputs.revision }}"
    assert '--kernelrelease="${KERNELRELEASE}"' in manifest["run"]
    assert '--revision="${REVISION}"' in manifest["run"]
    assert "GITHUB_SHA" not in manifest["run"]
    text = (WORKFLOWS_DIR / "release-build.yml").read_text(encoding="utf-8")
    assert "-lusoris1" not in text, "no hardcoded revision"
    assert "/dev/urandom" not in text
    checkout = steps[names.index("Checkout Repository")]
    assert checkout["with"]["persist-credentials"] is False


def test_publish_release_refuses_existing_release():
    """release-build.yml refuses to run for a tag whose release already exists (gap row C14-2)."""
    steps, names = _release_build_steps()
    step_name = "Refuse Existing Release"
    assert step_name in names, f"'{step_name}' step missing from release-build.yml"
    refuse_idx = names.index(step_name)
    meta_idx = names.index("Resolve Stream and Version")
    build_idx = names.index("Build and Boot the Tagged Stream")
    assert meta_idx < refuse_idx < build_idx, (
        f"'{step_name}' must run after 'Resolve Stream and Version' and before 'Build and Boot the Tagged Stream'"
    )
    step = steps[refuse_idx]
    assert "./scripts/refuse_existing_release.sh" in step["run"]
    assert step["env"]["RELEASE_TAG"] == "${{ steps.meta.outputs.release_tag }}"
    assert step["env"]["REPO"] == "${{ github.repository }}"
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"


def _build_matrix():
    return yaml.safe_load((WORKFLOWS_DIR / "build-matrix.yml").read_text(encoding="utf-8"))["jobs"][
        "build"
    ]


SCRIPT_REF = re.compile(r"(?:\bscripts/|\$\{SCRIPT_DIR\}/)([A-Za-z0-9_.-]+)")
PY_IMPORT = re.compile(r"^(?:from|import) ([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
BUILD_INPUTS = {
    "kconfig/**",
    "keys/**",
    "patches/**",
    "versions.json",
    "versions.schema.json",
    "docker/Dockerfile.builder",
}


def _build_path_scripts() -> set[str]:
    """Every repository script build-matrix.yml runs, and every script those run or import.

    Shell and workflow lines that are comments are skipped; a Python file counts its imports.
    """
    pending = [WORKFLOWS_DIR / "build-matrix.yml"]
    found: set[str] = set()
    for _ in range(64):
        if not pending:
            return found
        source = pending.pop()
        text = source.read_text(encoding="utf-8")
        if source.suffix == ".py":
            names = {f"{module}.py" for module in PY_IMPORT.findall(text)}
        else:
            code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
            names = set(SCRIPT_REF.findall("\n".join(code)))
        for name in sorted(names):
            path = REPO_ROOT / "scripts" / name
            if path.is_file() and f"scripts/{name}" not in found:
                found.add(f"scripts/{name}")
                pending.append(path)
    raise AssertionError("the script graph did not close within 64 files")


def _on(workflow: str) -> dict:
    loaded = yaml.safe_load((WORKFLOWS_DIR / workflow).read_text(encoding="utf-8"))
    return loaded[True]  # YAML 1.1 reads the bare key `on` as true


def test_build_matrix_compiles_every_pull_request_that_touches_the_build_path():
    on = _on("build-matrix.yml")
    paths = set(on["pull_request"]["paths"])
    scripts = _build_path_scripts()
    assert {
        "scripts/build_kernel.sh",
        "scripts/verify_kernel_requirement.py",
        "scripts/module_stream_matrix.py",
        "scripts/check-openzfs-module.sh",
        "scripts/check-nvidia-open-module.sh",
    } <= scripts
    expected = scripts | BUILD_INPUTS | {".github/workflows/build-matrix.yml"}
    assert paths == expected, (
        f"missing: {sorted(expected - paths)}, not on the build path: {sorted(paths - expected)}"
    )
    assert on["workflow_dispatch"]["inputs"]["dry_run"]["default"] is False
    assert "schedule" in on
    loaded = yaml.safe_load((WORKFLOWS_DIR / "build-matrix.yml").read_text(encoding="utf-8"))
    assert (
        loaded["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    )


def test_build_matrix_leaves_hosted_runners_to_other_repositories():
    """The kernel legs share the organisation's hosted runners: at most half run at once."""
    strategy = _build_matrix()["strategy"]
    legs = len(strategy["matrix"]["stream"]) * len(strategy["matrix"]["arch"])
    cap = strategy.get("max-parallel")
    assert isinstance(cap, int) and 0 < cap <= legs // 2, f"max-parallel {cap} for {legs} legs"


def test_build_matrix_runs_one_build_per_branch():
    """A pull request run and a dispatch on its branch share one group, so they never overlap."""
    group = yaml.safe_load((WORKFLOWS_DIR / "build-matrix.yml").read_text(encoding="utf-8"))[
        "concurrency"
    ]["group"]
    assert group == "build-matrix-${{ github.head_ref || github.ref_name }}"


def test_required_checks_waits_longer_than_every_build_wave():
    strategy = _build_matrix()["strategy"]
    count = len(strategy["matrix"]["stream"]) * len(strategy["matrix"]["arch"])
    waves = (count + strategy["max-parallel"] - 1) // strategy["max-parallel"]
    legs = waves * max(entry["timeout"] for entry in strategy["matrix"]["include"])
    job = yaml.safe_load((WORKFLOWS_DIR / "required-aggregator.yml").read_text(encoding="utf-8"))[
        "jobs"
    ]["required-checks"]
    run = job["steps"][-1]["run"]
    wait = int(re.search(r"--timeout (\d+)", run)[1])
    assert wait > legs * 60, (
        f"the gate gives up at {wait} s, before {waves} waves ({legs} minutes) end"
    )
    assert job["timeout-minutes"] * 60 > wait, "the job is killed before the gate's own timeout"


def _measured_spans() -> tuple[list[int], int]:
    """Measured pull request build-matrix spans and the hosted runner job limit, in minutes."""
    fixture = json.loads(
        (REPO_ROOT / "tests" / "data" / "required-checks-spans.json").read_text(encoding="utf-8")
    )
    return [entry["span_seconds"] for entry in fixture["runs"]], fixture["hosted_job_limit_minutes"]


def _deadline_problems(
    wait: int, job_minutes: int, spans: list[int], limit_minutes: int
) -> list[str]:
    """Pure check of aggregator wait and job timeout against spans and runner limits."""
    problems: list[str] = []
    if wait < math.ceil(max(spans) * 1.25):
        problems.append("deadline below measured worst span + 25%")
    if job_minutes * 60 <= wait:
        problems.append("job killed before the gate's deadline")
    if job_minutes > limit_minutes:
        problems.append("job bound over the hosted runner limit")
    return problems


def test_required_checks_deadline_covers_every_measured_build_span():
    """Aggregator timeout must cover all measured PR build-matrix spans with headroom."""
    job = yaml.safe_load((WORKFLOWS_DIR / "required-aggregator.yml").read_text(encoding="utf-8"))[
        "jobs"
    ]["required-checks"]
    run = job["steps"][-1]["run"]
    wait = int(re.search(r"--timeout (\d+)", run)[1])
    job_minutes = job["timeout-minutes"]
    spans, limit_minutes = _measured_spans()
    assert _deadline_problems(wait, job_minutes, spans, limit_minutes) == []


@pytest.mark.parametrize(
    ("wait", "job_minutes", "expected_message"),
    [
        (11400, 200, "deadline below measured worst span + 25%"),
        (19800, 330, "job killed before the gate's deadline"),
        (19800, 370, "job bound over the hosted runner limit"),
    ],
)
def test_deadline_problems_planted_refusals(wait: int, job_minutes: int, expected_message: str):
    """Planted invalid deadlines must be refused with the exact expected diagnostic."""
    spans, limit_minutes = _measured_spans()
    assert _deadline_problems(wait, job_minutes, spans, limit_minutes) == [expected_message]


def test_deadline_problems_boundary():
    """Wait at exactly ceil(max * 1.25) passes; one second less triggers rule a."""
    spans, limit_minutes = _measured_spans()
    boundary_wait = math.ceil(max(spans) * 1.25)
    job_minutes = boundary_wait // 60 + 1
    assert _deadline_problems(boundary_wait, job_minutes, spans, limit_minutes) == []
    assert _deadline_problems(boundary_wait - 1, job_minutes, spans, limit_minutes) == [
        "deadline below measured worst span + 25%"
    ]


def _container_run_shells() -> dict[tuple[str, str, str], str | None]:
    """The shell each run step of each container job resolves to, keyed by workflow, job, step."""
    shells: dict[tuple[str, str, str], str | None] = {}
    for wf in sorted(WORKFLOWS_DIR.glob("*.yml")):
        parsed = yaml.safe_load(wf.read_text(encoding="utf-8"))
        default = parsed.get("defaults", {}).get("run", {}).get("shell")
        for job_id, job in parsed["jobs"].items():
            if "container" not in job:
                continue
            job_default = job.get("defaults", {}).get("run", {}).get("shell", default)
            for index, step in enumerate(job["steps"]):
                if "run" in step:
                    key = (wf.name, job_id, step.get("name", f"step {index}"))
                    shells[key] = step.get("shell", job_default)
    return shells


def test_every_run_step_in_a_container_names_its_shell():
    """Issue #16: a container job's default shell is sh, which parses no `[[` and no array.

    A step that leaves the shell to the default ran under dash in the build container and failed
    on bash syntax on every architecture, so each one names its shell, and the kernel build,
    which all twelve legs run, names bash.
    """
    shells = _container_run_shells()
    unnamed = [key for key, shell in shells.items() if shell is None]
    assert not unnamed, f"run steps that leave the shell to the container default: {unnamed}"
    build = {
        key: shell for key, shell in shells.items() if key[:2] == ("build-matrix.yml", "build")
    }
    assert build and set(build.values()) == {"bash"}, build


def test_build_matrix_compiles_twelve_legs_with_least_privilege():
    job = _build_matrix()
    assert job["permissions"] == {"contents": "read"}
    matrix = job["strategy"]["matrix"]
    assert len(matrix["stream"]) * len(matrix["arch"]) == 12
    runners = {entry["arch"]: entry["runner"] for entry in matrix["include"]}
    assert runners == {
        "x86_64": "ubuntu-24.04",
        "arm64": "ubuntu-24.04-arm",
        "riscv64": "ubuntu-24.04",
    }
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
    assert boot["if"] == "env.DRY_RUN != 'true'"
    assert "scripts/boot_smoke.py" in boot["run"]
    assert '--arch="${ARCH}"' in boot["run"]
    other_release = steps["Refuse a console of another release"]
    assert other_release["if"] == "env.DRY_RUN != 'true'"
    assert '--judge-log="${log}" --kernelrelease="${KERNELRELEASE}"' in other_release["run"]
    assert '--kernelrelease="${KERNELRELEASE}-planted"' in other_release["run"]
    assert 'log="records/boot-${STREAM}-${ARCH}.log"' in other_release["run"]
    assert "records/boot-${STREAM}-${ARCH}.log" in boot["run"]
    assert order.index("Boot the Kernel to Userspace") < order.index(
        "Refuse a console of another release"
    )
    refuse_silent = steps["Refuse a boot whose console stays silent"]
    assert refuse_silent["if"] == "matrix.arch != 'x86_64' && env.DRY_RUN != 'true'"
    assert "--console=" in refuse_silent["run"]
    stage = steps["Stage Artifacts and Checksums"]["run"]
    assert 'OUTPUT_DIR="output/${STREAM}-${ARCH}" ./scripts/publish_release.sh' in stage
    assert order.index("Compile Kernel Package") < order.index("Boot the Kernel to Userspace")
    assert order.index("Boot the Kernel to Userspace") < order.index(
        "Refuse a boot whose console stays silent"
    )
    assert order.index("Refuse a boot whose console stays silent") < order.index(
        "Stage Artifacts and Checksums"
    )
    restore = steps["Restore the Compiler Cache"]
    save = steps["Save the Compiler Cache"]
    assert restore["uses"].startswith("actions/cache/restore@") and save["uses"].startswith(
        "actions/cache/save@"
    )
    assert "${{ steps.build.outputs.config_sha256 }}" in save["with"]["key"]
    assert "${{ matrix.stream }}-${{ matrix.arch }}" in save["with"]["key"]


def test_build_matrix_builds_and_refuses_ukis_as_build_checks():
    steps = {step.get("name", ""): step for step in _build_matrix()["steps"]}
    order = [step.get("name", "") for step in _build_matrix()["steps"]]

    # Positive UKI synthesis step
    assert "Build UKI from Nucleus Kernel" in steps
    uki_step = steps["Build UKI from Nucleus Kernel"]
    assert uki_step["if"] == "matrix.arch != 'riscv64' && env.DRY_RUN != 'true'"
    uki_run = uki_step["run"]
    assert "scripts/boot_smoke.py" in uki_run and "--write-initramfs=" in uki_run
    assert "scripts/package-uki.sh" in uki_run
    assert "--output-dir=output/" not in uki_run
    assert '--output-dir="output/' not in uki_run
    assert "${RUNNER_TEMP}" in uki_run

    # Negative UKI step
    assert "Refuse a UKI when vmlinuz is not a kernel" in steps
    neg_step = steps["Refuse a UKI when vmlinuz is not a kernel"]
    assert neg_step["if"] == "matrix.arch != 'riscv64' && env.DRY_RUN != 'true'"
    neg_run = neg_step["run"]
    assert "scripts/package-uki.sh" in neg_run
    assert "--output-dir=" in neg_run
    assert "--output-dir=output/" not in neg_run
    assert "find " in neg_run and "*.efi" in neg_run.lower()

    # Ordering: after boot, before staging artifacts
    assert order.index("Boot the Kernel to Userspace") < order.index(
        "Build UKI from Nucleus Kernel"
    )
    assert order.index("Build UKI from Nucleus Kernel") < order.index(
        "Refuse a UKI when vmlinuz is not a kernel"
    )
    assert order.index("Refuse a UKI when vmlinuz is not a kernel") < order.index(
        "Stage Artifacts and Checksums"
    )


def test_build_matrix_compiler_cache_is_restored_by_prefix_and_saved_under_a_new_key():
    """The save key must never equal an existing entry, or the cache is frozen at its first save."""
    steps = {step.get("name", ""): step for step in _build_matrix()["steps"]}
    restore, save = (
        steps["Restore the Compiler Cache"]["with"],
        steps["Save the Compiler Cache"]["with"],
    )
    prefix = "ccache-${{ matrix.stream }}-${{ matrix.arch }}-"
    assert restore["restore-keys"].strip() == prefix
    assert save["key"].startswith(prefix) and restore["key"].startswith(prefix)
    assert "${{ github.run_id }}-${{ github.run_attempt }}" in save["key"]
    assert "hashFiles" not in restore["key"], "the restore key and the save key use one scheme"


def test_publish_release_container_cannot_rewrite_what_the_host_runs_next():
    """The checkout is read-only in the build container; only output/, records/ and work dirs are not."""
    steps, names = _release_build_steps()
    run = steps[names.index("Build and Boot the Tagged Stream")]["run"]
    mounts = re.findall(r'-v "?([^"\s)]+)"?', run)
    assert "${GITHUB_WORKSPACE}:/forge:ro" in mounts
    writable = [m for m in mounts if not m.endswith(":ro")]
    assert sorted(writable) == sorted(
        [
            "${GITHUB_WORKSPACE}/output:/forge/output",
            "${GITHUB_WORKSPACE}/records:/forge/records",
            "${RUNNER_TEMP}/nucleus-build:/work/temp",
            "/mnt/nucleus-build:/work/mnt",
        ]
    ), writable
    assert "/var/run/docker.sock" not in run and "--privileged" not in run


def test_publish_release_boots_the_kernel_before_anything_is_signed():
    steps, names = _release_build_steps()
    run = steps[names.index("Build and Boot the Tagged Stream")]["run"]
    assert run.index("./scripts/build_kernel.sh") < run.index("python3 scripts/boot_smoke.py")
    assert '--log="records/boot-${STREAM}-x86_64.log"' in run
    assert 'exit "${status}"' in run and "|| status=$?" in run
    assert names.index("Build and Boot the Tagged Stream") < names.index(
        "Sign and Verify the Release"
    )
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
    for name in ("build-matrix.yml", "release-build.yml", "ci.yml", "verify-requirements.yml"):
        assert name in users, name


def test_renovate_custom_manager_matches_workflow_image_env_pins():
    """Renovate customManager regexes match workflow *_IMAGE: env pins and extract image, tag, and digest."""
    renovate = json.loads((REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
    manager = next(
        m
        for m in renovate.get("customManagers", [])
        if m.get("customType") == "regex"
        and m.get("datasourceTemplate") == "docker"
        and any("_IMAGE:" in pat for pat in m.get("matchStrings", []))
    )
    assert manager.get("managerFilePatterns") == ["/^\\.github\\/workflows\\/.*\\.ya?ml$/"]

    patterns = [
        re.compile(re.sub(r"\?<([a-zA-Z0-9_]+)>", r"?P<\1>", pat))
        for pat in manager.get("matchStrings", [])
    ]
    assert patterns

    matched_lines = 0
    for wf in sorted(WORKFLOWS_DIR.glob("*.yml")):
        for line in wf.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "_IMAGE: " not in line:
                continue
            matched = False
            for pat in patterns:
                m = pat.search(line)
                if m:
                    matched = True
                    assert m.group("depName") == "ubuntu", f"unexpected depName in {wf.name}"
                    assert m.group("currentValue") == "26.04", (
                        f"unexpected currentValue in {wf.name}"
                    )
                    digest = m.group("currentDigest")
                    assert re.fullmatch(r"sha256:[a-f0-9]{64}", digest), (
                        f"invalid digest in {wf.name}"
                    )
                    break
            assert matched, f"No matchString in customManager matched: {line!r} in {wf.name}"
            matched_lines += 1

    assert matched_lines >= 3, (
        f"Expected at least 3 _IMAGE: lines in workflows, got {matched_lines}"
    )

    # Negative case: a line without a digest does not match
    no_digest_line = "BUILD_IMAGE: ubuntu:26.04"
    assert not any(pat.search(no_digest_line) for pat in patterns)


def test_no_run_block_interpolates_event_data():
    """Event data reaches a script through env:, never through ${{ }} inside a run: block."""
    event_data = re.compile(r"\$\{\{[^}]*github\.(event|head_ref)[^}]*\}\}")
    for wf in WORKFLOWS_DIR.glob("*.yml"):
        parsed = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for job in parsed["jobs"].values():
            for step in job.get("steps", []):
                assert not event_data.search(step.get("run", "")), f"{wf.name}: {step.get('name')}"


LOCK_FILE = REPO_ROOT / "requirements-test.txt"
PIP_INSTALL = re.compile(r"\bpip[\d.]*\s+install\b")
SHELL = r"(?:\S*/)?(?:ba|z|da|a)?sh\b"
DOWNLOAD_INTO_SHELL = re.compile(
    rf"\b(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?{SHELL}"
    rf"|\b(?:{SHELL}|eval|source)[^\n]*(?:\$\(|<\(|`)\s*(?:curl|wget)\b"
)
# What Renovate's pip-compile manager accepts in the command a uv lock file names in its
# header (renovatebot/renovate, lib/modules/manager/pip-compile/common.ts): an allow-list,
# and every option that takes a value must be written as --option=value.
RENOVATE_UV_FLAGS = {"--generate-hashes", "--universal", "--no-strip-extras"}
RENOVATE_UV_VALUE_OPTIONS = {"--python-version", "--output-file"}


def _run_lines(workflow_text: str) -> list[str]:
    """Every logical line of every run: block of a workflow, continuations joined."""
    lines: list[str] = []
    for job in yaml.safe_load(workflow_text)["jobs"].values():
        for step in job.get("steps", []):
            lines += step.get("run", "").replace("\\\n", " ").splitlines()
    return [line.strip() for line in lines if line.strip()]


def _unhashed_pip_installs(workflow_text: str) -> list[str]:
    """pip install lines that do not install a requirements file under --require-hashes."""
    return [
        line
        for line in _run_lines(workflow_text)
        if PIP_INSTALL.search(line)
        and not ("--require-hashes" in line and re.search(r"(?:\s-r\s|--requirement[\s=])", line))
    ]


def _downloads_into_shell(workflow_text: str) -> list[str]:
    """Lines that pipe or substitute a download into a shell."""
    return [line for line in _run_lines(workflow_text) if DOWNLOAD_INTO_SHELL.search(line)]


def _unhashed_requirements(lock_text: str) -> list[str]:
    """Lines of a requirements file that carry no --hash=."""
    lines = lock_text.replace("\\\n", " ").splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#") and "--hash=" not in line
    ]


def _lock_command(lock_text: str) -> str:
    """The command a lock file names in its header, as uv writes it."""
    match = re.match(r"(?:#.*\n)+?#    (uv pip compile .*)\n", lock_text)
    assert match, "the lock file must start with uv's header and its command"
    return match.group(1)


def _renovate_refusals(command: str) -> list[str]:
    """Arguments of a uv pip compile command that Renovate's pip-compile manager refuses."""
    refused = []
    for arg in command.split()[3:]:
        option = arg.split("=", 1)[0]
        if not arg.startswith("-") or arg in RENOVATE_UV_FLAGS:
            continue
        if not (option in RENOVATE_UV_VALUE_OPTIONS and "=" in arg):
            refused.append(arg)
    return refused


def _step(run: str) -> str:
    """A one-job workflow whose only step runs the given script."""
    return yaml.safe_dump({"jobs": {"job": {"steps": [{"run": run}]}}})


def test_every_workflow_pip_install_requires_hashes():
    """Every pip install in a workflow installs a requirements file with --require-hashes."""
    for wf in sorted(WORKFLOWS_DIR.glob("*.yml")):
        assert not _unhashed_pip_installs(wf.read_text(encoding="utf-8")), wf.name


@pytest.mark.parametrize(
    "run",
    [
        "pip install --no-cache-dir -r requirements-test.txt",
        "pip install --no-cache-dir jsonschema pytest",
        "pip install --require-hashes jsonschema",
        "python3 -m pip install -r requirements.txt",
        "sudo pip3 install mkdocs-material",
        "set -e\nmake lint && pip install \\\n  pytest",
    ],
)
def test_an_unhashed_pip_install_is_refused(run: str):
    """The check refuses each planted install, in a single-line run: as in a script."""
    assert _unhashed_pip_installs(_step(run))
    if "\n" not in run:
        assert _unhashed_pip_installs(f"jobs:\n  job:\n    steps:\n      - run: {run}\n")


def test_no_workflow_downloads_into_a_shell():
    """No workflow executes what curl or wget returned; a download is verified first."""
    for wf in sorted(WORKFLOWS_DIR.glob("*.yml")):
        assert not _downloads_into_shell(wf.read_text(encoding="utf-8")), wf.name


@pytest.mark.parametrize(
    "run",
    [
        'bash -c "$(curl -sSfL https://example.com/main/scripts/download-actionlint.bash)"',
        'sh -c "$(curl -sSfL https://example.com/install.sh)"',
        "curl -sSfL https://example.com/install.sh | bash",
        "curl -sSfL https://example.com/install.sh | sudo bash",
        "curl -sSfL https://example.com/install.sh \\\n  | sh",
        "wget -qO- https://example.com/install.sh | sh",
        'eval "$(curl -sSfL https://example.com/init)"',
        "bash <(curl -sSfL https://example.com/install.sh)",
        "source <(curl -sSfL https://example.com/env)",
    ],
)
def test_a_download_into_a_shell_is_refused(run: str):
    """The check refuses each planted download that reaches a shell unverified."""
    assert _downloads_into_shell(_step(run))


def test_a_verified_download_is_not_refused():
    """A download to a file that sha256sum checks is the accepted form."""
    run = 'curl -sSfL -o tool.tar.gz https://example.com/tool.tar.gz\necho "0  tool.tar.gz" | sha256sum -c'
    assert not _downloads_into_shell(_step(run))
    assert not _unhashed_pip_installs(
        _step("pip install --require-hashes -r requirements-test.txt")
    )


def test_the_lock_file_pins_every_requirement_by_hash():
    """requirements-test.txt carries a hash for every requirement it names."""
    assert not _unhashed_requirements(LOCK_FILE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "lock",
    [
        "jsonschema>=4.20.0\npytest>=7.4.0\n",
        "pytest==9.1.1\n",
        "attrs==26.1.0 \\\n    --hash=sha256:00\npytest==9.1.1\n",
        "-r other.txt\n",
    ],
)
def test_a_requirement_without_a_hash_is_refused(lock: str):
    """The check refuses each planted lock file with a line that has no hash."""
    assert _unhashed_requirements(lock)


def test_the_lock_command_is_one_renovate_can_rerun():
    """Renovate reruns the header's command, so it must parse it; the docs name the same one."""
    command = _lock_command(LOCK_FILE.read_text(encoding="utf-8"))
    assert not _renovate_refusals(command), command
    assert "--generate-hashes" in command.split()
    assert "--output-file=requirements-test.txt" in command.split()
    assert command.split()[-1] == "requirements-test.in"
    for doc in ("CONTRIBUTING.md", "docs/community/contributing.md"):
        assert command in (REPO_ROOT / doc).read_text(encoding="utf-8"), doc
    renovate = json.loads((REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
    patterns = renovate["pip-compile"]["managerFilePatterns"]
    assert any(re.search(pattern.strip("/"), LOCK_FILE.name) for pattern in patterns), patterns


@pytest.mark.parametrize(
    "command",
    [
        "uv pip compile --generate-hashes --python-version 3.12 requirements-test.in -o requirements-test.txt",
        "uv pip compile --python-version=3.12 -o requirements-test.txt requirements-test.in",
        "uv pip compile --no-header --output-file=requirements-test.txt requirements-test.in",
        "uv pip compile --output-file requirements-test.txt requirements-test.in",
    ],
)
def test_a_lock_command_renovate_cannot_parse_is_refused(command: str):
    """The first case is the command the lock file was first compiled with."""
    assert _renovate_refusals(command)


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
    # Only a pull request run is cancelled when superseded; a dispatch run never is.
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def _publish_release_steps():
    parsed = yaml.safe_load((WORKFLOWS_DIR / "publish-release.yml").read_text(encoding="utf-8"))
    steps = parsed["jobs"]["publish"]["steps"]
    return steps, [step.get("name", "") for step in steps]


def test_publish_release_resolves_tags_without_fallback():
    """A kernel tag must resolve to exactly one versions.json stream; there is no default."""
    steps, names = _release_build_steps()
    resolve = steps[names.index("Resolve Stream and Version")]
    assert resolve["id"] == "meta"
    assert "scripts/resolve_release_tag.py" in resolve["run"]
    assert '--github-output "${GITHUB_OUTPUT}"' in resolve["run"]
    assert "python3 -c" not in resolve["run"], (
        "the tag grammar lives in scripts/resolve_release_tag.py"
    )
    assert "${{" not in resolve["run"], "the tag reaches the resolver through env, never inline"


def test_publish_release_tag_and_ref_cannot_diverge():
    """A dispatched run must start from the tag it releases, and the release names that tag."""
    build_steps, build_names = _release_build_steps()
    resolve = build_steps[build_names.index("Resolve Stream and Version")]
    assert '--ref "${GITHUB_REF}"' in resolve["run"], (
        "the resolver must compare the run's ref with the tag"
    )
    publish_steps, publish_names = _publish_release_steps()
    release = publish_steps[publish_names.index("Publish to GitHub Release")]
    assert release["with"]["tag_name"] == "${{ needs.build.outputs.release_tag }}"


def _guard_step():
    parsed = yaml.safe_load((WORKFLOWS_DIR / "publish-release.yml").read_text(encoding="utf-8"))
    steps = parsed["jobs"]["preflight"]["steps"]
    names = [step.get("name", "") for step in steps]
    return (
        steps,
        names,
        steps[names.index("Require KERNEL_FORGE_TOKEN for the Downstream Dispatch")],
    )


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
    _, _, guard = _guard_step()
    publish_steps, publish_names = _publish_release_steps()
    dispatch = publish_steps[publish_names.index("Dispatch Downstream Notification to imago")]
    assert dispatch["with"]["token"] == "${{ secrets.KERNEL_FORGE_TOKEN }}"
    assert guard["env"] == {"KERNEL_FORGE_TOKEN_SET": "${{ secrets.KERNEL_FORGE_TOKEN != '' }}"}
    assert "${{" not in guard["run"], "the guard reads its env, never an inline expression"
    payload = dispatch["with"]["client-payload"]
    assert "needs.build.outputs.release_version" in payload
    assert "needs.build.outputs.release_tag" in payload


def test_publish_release_stops_before_building_or_publishing_without_the_token():
    """A missing credential must publish nothing: preflight runs before build and publish."""
    parsed = yaml.safe_load((WORKFLOWS_DIR / "publish-release.yml").read_text(encoding="utf-8"))
    jobs = parsed["jobs"]
    assert "preflight" in jobs
    assert "build" in jobs and jobs["build"].get("needs") == "preflight"
    assert "publish" in jobs and jobs["publish"].get("needs") == "build"


def test_publish_release_secret_is_read_only_by_the_dispatch_step():
    """ADR-0005: the token value reaches the dispatch step alone; the guard sees a boolean."""
    publish_steps, publish_names = _publish_release_steps()
    dispatch = publish_names.index("Dispatch Downstream Notification to imago")
    text = (WORKFLOWS_DIR / "publish-release.yml").read_text(encoding="utf-8")
    plain = re.findall(r"secrets\.KERNEL_FORGE_TOKEN(?! != '')", text)
    assert len(plain) == 1 and "secrets.KERNEL_FORGE_TOKEN" in str(publish_steps[dispatch])
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
    assert (
        "--plan="
        in {s.get("name"): s for s in jobs["verify"]["steps"]}["Plan the Resolved Evidence"]["run"]
    )
    resolve = jobs["resolve"]
    assert resolve["needs"] == "verify" and BUILD_IMAGE.fullmatch(resolve["container"]["image"])
    # The plan is the matrix; each leg reports as "Resolve KConfig (<stream>)"
    # (docs/repository-governance.md, sections 4 and 6).
    assert resolve["strategy"]["matrix"] == "${{ fromJSON(needs.verify.outputs.plan) }}"
    assert resolve["name"] == "Resolve KConfig (${{ matrix.stream }})"
    assert "matrix.leg" not in str(resolve)
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
    apply_patches = steps["Apply the Carried Patches"]
    assert (
        "scripts/check_patches_apply.py" in apply_patches["run"]
        and "--apply" in apply_patches["run"]
        and "STREAM" in apply_patches["env"]
    )
    step_names = [s.get("name", "") for s in resolve["steps"]]
    assert step_names.index("Apply the Carried Patches") < step_names.index(
        "Resolve the Configuration"
    )
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
    assert "required-checks" in parsed["jobs"], (
        "required-aggregator must define job 'required-checks'"
    )


def _security_gate_job():
    workflow = WORKFLOWS_DIR / "security-scans.yml"
    parsed = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    return parsed["jobs"], parsed["jobs"]["security-gate"]


def test_security_scans_aggregate_contract():
    """Ensure one !cancelled() job in security-scans.yml needs every scan job."""
    jobs, gate = _security_gate_job()
    assert str(gate["if"]).strip() == "${{ !cancelled() }}", (
        "the gate must report on every run that a newer push did not cancel"
    )
    assert "continue-on-error" not in gate, "an advisory gate can never fail"
    scans = sorted(name for name in jobs if name != "security-gate")
    assert sorted(gate["needs"]) == scans, "the gate must need every other job of the workflow"
    env = gate["steps"][-1]["env"]
    for job in scans:
        assert env[f"{job.upper()}_RESULT"] == f"${{{{ needs.{job}.result }}}}"


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        (
            {"changes": "success", "semgrep": "success", "trivy": "success", "gitleaks": "success"},
            0,
        ),
        (
            {"changes": "success", "semgrep": "skipped", "trivy": "skipped", "gitleaks": "success"},
            0,
        ),
        (
            {"changes": "success", "semgrep": "failure", "trivy": "skipped", "gitleaks": "success"},
            1,
        ),
        (
            {
                "changes": "success",
                "semgrep": "skipped",
                "trivy": "cancelled",
                "gitleaks": "success",
            },
            1,
        ),
        (
            {"changes": "failure", "semgrep": "skipped", "trivy": "skipped", "gitleaks": "success"},
            1,
        ),
        (
            {"changes": "success", "semgrep": "success", "trivy": "success", "gitleaks": "failure"},
            1,
        ),
        (
            {
                "changes": "cancelled",
                "semgrep": "skipped",
                "trivy": "skipped",
                "gitleaks": "cancelled",
            },
            1,
        ),
    ],
)
def test_security_gate_fails_on_failed_or_cancelled_scans(results, expected):
    """Run the gate's own script against each combination of scan results."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not found in PATH")
    _, gate = _security_gate_job()
    # A pull request on which the change detector found no code path: the only case in which
    # Semgrep and Trivy may skip (tests/test_gates_refuse_skipped.py covers the others).
    env = {"PATH": "/usr/bin:/bin", "EVENT_NAME": "pull_request", "CODE_CHANGED": "false"}
    for job, result in results.items():
        env[f"{job.upper()}_RESULT"] = result
    res = subprocess.run(
        [bash, "-c", gate["steps"][-1]["run"]], env=env, capture_output=True, text=True
    )
    assert res.returncode == expected, f"{results}: {res.stdout}{res.stderr}"


def _runs_on_every_pull_request_to_main(parsed):
    """Whether a workflow triggers on every pull request that targets main."""
    on = parsed.get("on", parsed.get(True))  # PyYAML reads the bare key `on` as True.
    if isinstance(on, str):
        return on == "pull_request"
    if isinstance(on, list):
        return "pull_request" in on
    if not isinstance(on, dict) or "pull_request" not in on:
        return False
    trigger = on["pull_request"]
    if not isinstance(trigger, dict):
        return True
    if "paths" in trigger or "paths-ignore" in trigger:
        return False
    branches = trigger.get("branches")
    return branches is None or "main" in branches


def _job_contexts(job_id, job):
    """The check names a job reports on every run, expanding matrix include legs."""
    condition = "".join(str(job.get("if", "")).replace("${{", "").replace("}}", "").split())
    if condition and condition not in EVERY_RUN_CONDITIONS:
        return []
    if str(job.get("continue-on-error", False)).lower() != "false":
        return []
    name = job.get("name", job_id)
    if "${{" not in name:
        return [name]
    legs = ((job.get("strategy") or {}).get("matrix") or {}).get("include") or []
    contexts = []
    for leg in legs:
        expanded = name
        for key, value in leg.items():
            expanded = re.sub(
                r"\$\{\{\s*matrix\." + re.escape(key) + r"\s*\}\}", str(value), expanded
            )
        contexts.append(expanded)
    return contexts


def _checks_reported_on_every_pull_request():
    reported = set()
    for workflow in sorted(WORKFLOWS_DIR.glob("*.yml")):
        parsed = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        if not _runs_on_every_pull_request_to_main(parsed):
            continue
        for job_id, job in parsed["jobs"].items():
            reported.update(_job_contexts(job_id, job))
    return reported


def test_ruleset_requires_exactly_the_checks_every_pull_request_reports():
    """A required check that no pull request reports would block every merge.

    .github/rulesets/main.json is rendered by `make ruleset`; re-render it whenever a
    workflow that runs on pull requests gains, loses or renames a job. This test compares
    the required check names only. The review counts, signature and history rules, and the
    review-mode override are checked by the `praetorctl sync` step of the governance job,
    which fails when the file differs from the policy `.standards.yaml` declares.
    """
    ruleset = json.loads(RULESET.read_text(encoding="utf-8"))
    rules = [rule for rule in ruleset["rules"] if rule["type"] == "required_status_checks"]
    assert len(rules) == 1, "the ruleset must carry one required_status_checks rule"
    required = [check["context"] for check in rules[0]["parameters"]["required_status_checks"]]
    reported = _checks_reported_on_every_pull_request()
    never_reported = sorted(set(required) - reported)
    assert not never_reported, f"required checks no pull request reports: {never_reported}"
    not_required = sorted(reported - set(required))
    assert not not_required, f"ruleset is stale, run `make ruleset`; not required: {not_required}"


def test_praetor_pin_is_a_full_commit_and_governance_job_verifies_it():
    """The praetor pin is one full commit SHA, and CI checks the vendored catalog against it.

    `plan` passes when `.standards.lock` is missing, so the job also runs
    `praetorctl sync --catalog-root praetor-src`, which fails on a missing lock and on a
    committed ruleset that differs from the declared policy. It must stay local: `--remote`
    would write to GitHub from a pull request job.
    """
    parsed = yaml.safe_load((WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8"))
    pin = parsed["env"]["PRAETOR_REF"]
    assert re.fullmatch(r"[0-9a-f]{40}", pin), f"PRAETOR_REF must be a full commit SHA: {pin}"
    assert (REPO_ROOT / ".standards.lock").is_file(), ".standards.lock must be committed"

    steps = parsed["jobs"]["governance"]["steps"]
    for step in steps:
        uses = step.get("uses", "")
        if uses.startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False, (
                f"checkout keeps credentials: {step}"
            )
    checkout = next(
        step for step in steps if step.get("with", {}).get("repository") == "cordanaLLM/praetor"
    )
    assert checkout["with"]["ref"] == "${{ env.PRAETOR_REF }}"

    commands = [
        step["run"].strip() for step in steps if "run" in step and step.get("name") != DRAFT_STEP
    ]
    assert not [command for command in commands if "--remote" in command], (
        "governance job must stay local"
    )
    order = [
        "praetorctl plan --catalog-root praetor-src",
        "praetorctl sync --catalog-root praetor-src",
        "rm -rf praetor-src",
        "praetorctl plan",
        "praetorctl compile-context --verify",
    ]
    positions = [commands.index(command) for command in order]
    assert positions == sorted(positions), f"governance steps out of order: {commands}"
    assert 'test "$(git -C praetor-src rev-parse HEAD)" = "${PRAETOR_REF}"' in commands[0]


def test_hook_engine_finds_the_ci_pin():
    """Git hook engine.sh declared_pins finds the PRAETOR_REF declared in ci.yml."""
    engine_sh = REPO_ROOT / ".config" / "lefthook" / "engine.sh"
    assert engine_sh.is_file(), f"missing engine script at {engine_sh}"
    engine_content = engine_sh.read_text(encoding="utf-8")

    pipeline = (
        "tr -d '\\r' <\"$file\" | "
        "sed -n -e 's/^[[:space:]]*\\(PRAETOR_REF\\)[[:space:]]*[:=][[:space:]]*/\\1=/p' "
        "-e 's/^[[:space:]]*\\(PRAETOR_COMMIT\\)[[:space:]]*[:=][[:space:]]*/\\1=/p' | "
        "sed -e 's/[[:space:]]*#.*$//' "
        '-e "s/=[\\"\']/=/" '
        '-e "s/[\\"\'][[:space:]]*\\$//" '
        "-e 's/[[:space:]]*$//'"
    )
    assert pipeline in engine_content, (
        f"literal pipeline string from engine.sh not found in {engine_sh}"
    )

    found = []
    for wf in sorted(WORKFLOWS_DIR.glob("*.yml")):
        cmd = f'file="{wf}"; {pipeline}'
        res = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, check=True)
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        found.extend(line.split("=", 1)[1] for line in lines if line.startswith("PRAETOR_REF="))

    distinct = sorted(set(found))
    assert len(distinct) == 1, (
        f"expected exactly one distinct PRAETOR_REF value across workflows, found {distinct}"
    )

    parsed = yaml.safe_load((WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8"))
    expected_pin = parsed["env"].get("PRAETOR_REF")
    assert distinct[0] == expected_pin, (
        f"hook pipeline found {distinct[0]!r}, expected ci.yml PRAETOR_REF {expected_pin!r}"
    )


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


def _run_metadata_gate(
    tmp_path: Path | None = None, **overrides: str
) -> subprocess.CompletedProcess:
    """Run the metadata gate's script as the runner does, with a complete, valid pull request.

    With `tmp_path`, `gh` on PATH is a stub that fails, so the gate's live milestone and label
    queries cannot reach the forge and the test judges the payload alone.
    """
    parsed = yaml.safe_load((WORKFLOWS_DIR / "pr-project-gate.yml").read_text(encoding="utf-8"))
    step = next(
        s
        for s in parsed["jobs"]["validate-metadata"]["steps"]
        if s.get("name") == "Verify PR Milestone, Issue Reference and Labels"
    )
    env = {
        "PATH": os.environ["PATH"],
        "PR_NUMBER": "1",
        "PR_TITLE": "fix(ci): an ordinary change",
        "PR_BODY": "Fixes #39",
        "PR_MILESTONE": "v1.0.0-rc1",
        "PR_LABELS": '["area/ci-cd"]',
        "PR_AUTHOR": "lusoris",
        "PR_HEAD_REF": "fix/example",
    }
    env.update(overrides)
    if tmp_path is not None:
        stub = tmp_path / "gh"
        stub.write_text(
            "#!/bin/sh\necho 'gh: no forge in this test' >&2\nexit 1\n", encoding="utf-8"
        )
        stub.chmod(0o755)
        env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-c", step["run"]],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_metadata_gate_passes_a_pull_request_without_skip_markers():
    """A title and body that name no skip marker pass, so ordinary pull requests are unaffected."""
    result = _run_metadata_gate()
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "field,text",
    [
        ("PR_BODY", "Fixes #39\n\nThe ruleset cannot see a commit marked [skip ci]."),
        ("PR_BODY", "Fixes #39 [CI SKIP]"),
        ("PR_BODY", "Fixes #39 [No Ci]"),
        ("PR_BODY", "Fixes #39 [skip actions]"),
        ("PR_BODY", "Fixes #39 [actions skip]"),
        ("PR_BODY", "Fixes #39\n\nskip-checks: true"),
        ("PR_TITLE", "docs: explain [skip ci]"),
    ],
)
def test_metadata_gate_refuses_a_skip_marker(field, text):
    """A squash merge would carry the marker into main's commit message and skip every workflow."""
    result = _run_metadata_gate(**{field: text})
    assert result.returncode == 1
    assert "skip marker" in result.stdout + result.stderr


def test_release_waiver_does_not_exempt_a_skip_marker():
    """The release automation waiver covers metadata only; a skip marker is refused for it too."""
    result = _run_metadata_gate(
        PR_AUTHOR="github-actions[bot]",
        PR_HEAD_REF="release-please--branches--main--components--nucleus",
        PR_BODY="release notes [skip ci]",
    )
    assert result.returncode == 1
    assert "skip marker" in result.stdout + result.stderr


_LANDED_SHA = "751868804a1b2c3d4e5f60718293a4b5c6d7e8f9"


def _landed_pull(**overrides) -> dict:
    """A merged pull request as `GET /repos/{repo}/commits/{sha}/pulls` returns it."""
    pull = {
        "number": 228,
        "title": "feat(boot): load scx_simple under QEMU",
        "body": "Closes #215\n\nSecond paragraph.",
        "milestone": {"title": "0.5.0"},
        "labels": [{"name": "kind/feature"}, {"name": "area/ci-cd"}],
        "user": {"login": "lusoris"},
        "head": {"ref": "feat/boot-smoke-scx-simple"},
        "base": {"ref": "main"},
        "merged_at": "2026-10-19T00:55:00Z",
        "merge_commit_sha": _LANDED_SHA,
    }
    pull.update(overrides)
    return pull


def _resolve_landed_pull(tmp_path: Path, pulls: list) -> tuple[subprocess.CompletedProcess, dict]:
    """Run the push-to-main resolution step with `gh` stubbed to return `pulls`; parse its outputs."""
    parsed = yaml.safe_load((WORKFLOWS_DIR / "pr-project-gate.yml").read_text(encoding="utf-8"))
    step = next(s for s in parsed["jobs"]["validate-metadata"]["steps"] if s.get("id") == "merged")
    fixture = tmp_path / "pulls.json"
    fixture.write_text(json.dumps(pulls), encoding="utf-8")
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "gh"
    stub.write_text(f"#!/bin/sh\ncat '{fixture}'\n", encoding="utf-8")
    stub.chmod(0o755)
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    env = {
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "REPO": "owner/nucleus",
        "SHA": _LANDED_SHA,
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-c", step["run"]],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    values: dict[str, str] = {}
    lines = output.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            key, delim = line.split("<<", 1)
            end = lines.index(delim, index + 1)
            values[key] = "\n".join(lines[index + 1 : end])
            index = end + 1
            continue
        key, value = line.split("=", 1)
        values[key] = value
        index += 1
    return result, values


def test_metadata_gate_runs_on_a_push_to_main():
    """The ruleset requires the check, so main's head must carry it, not only pull requests."""
    parsed = yaml.safe_load((WORKFLOWS_DIR / "pr-project-gate.yml").read_text(encoding="utf-8"))
    on = parsed.get("on", parsed.get(True, {}))
    assert on["push"] == {"branches": ["main"]}
    steps = parsed["jobs"]["validate-metadata"]["steps"]
    resolve = next(s for s in steps if s.get("id") == "merged")
    assert resolve["if"] == "github.event_name == 'push'"
    verify = next(
        s for s in steps if s.get("name") == "Verify PR Milestone, Issue Reference and Labels"
    )
    assert steps.index(resolve) < steps.index(verify)
    for key in (
        "PR_NUMBER",
        "PR_TITLE",
        "PR_BODY",
        "PR_MILESTONE",
        "PR_LABELS",
        "PR_AUTHOR",
        "PR_HEAD_REF",
    ):
        assert "steps.merged.outputs." in verify["env"][key], key


def test_push_gate_checks_the_pull_request_that_landed_the_commit(tmp_path):
    """The landed pull request's metadata feeds the same checks a pull request event runs."""
    result, values = _resolve_landed_pull(tmp_path, [_landed_pull()])
    assert result.returncode == 0, result.stdout + result.stderr
    assert values["number"] == "228"
    assert values["body"] == "Closes #215\n\nSecond paragraph."
    assert json.loads(values["labels"]) == ["kind/feature", "area/ci-cd"]
    gate = _run_metadata_gate(
        tmp_path,
        PR_NUMBER=values["number"],
        PR_TITLE=values["title"],
        PR_BODY=values["body"],
        PR_MILESTONE=values["milestone"],
        PR_LABELS=values["labels"],
        PR_AUTHOR=values["author"],
        PR_HEAD_REF=values["head_ref"],
    )
    assert gate.returncode == 0, gate.stdout + gate.stderr


def test_push_gate_refuses_a_landed_pull_request_without_metadata(tmp_path):
    """A landed pull request without an issue reference fails on main as it would have on the pull request."""
    result, values = _resolve_landed_pull(tmp_path, [_landed_pull(body="No reference here.")])
    assert result.returncode == 0, result.stdout + result.stderr
    gate = _run_metadata_gate(
        tmp_path,
        PR_NUMBER=values["number"],
        PR_TITLE=values["title"],
        PR_BODY=values["body"],
        PR_MILESTONE=values["milestone"],
        PR_LABELS=values["labels"],
        PR_AUTHOR=values["author"],
        PR_HEAD_REF=values["head_ref"],
    )
    assert gate.returncode == 1
    assert "must reference a tracked Issue" in gate.stdout + gate.stderr


@pytest.mark.parametrize(
    "pulls",
    [
        [],
        [_landed_pull(merged_at=None)],
        [_landed_pull(merge_commit_sha="0" * 40)],
        [_landed_pull(base={"ref": "feat/other"})],
        [_landed_pull(), _landed_pull(number=229)],
    ],
    ids=["none", "unmerged", "other-merge-commit", "other-base", "ambiguous"],
)
def test_push_gate_refuses_a_commit_no_merged_pull_request_landed(tmp_path, pulls):
    """A direct push, or a commit that only passed through a pull request, has no metadata to check."""
    result, _ = _resolve_landed_pull(tmp_path, pulls)
    assert result.returncode == 1
    assert "No merged pull request landed" in result.stdout + result.stderr


def test_package_lifecycle_workflow_contract():
    """Verify package-lifecycle.yml structure, triggers, permissions, cosign identity, and container execution."""
    workflow_path = WORKFLOWS_DIR / "package-lifecycle.yml"
    parsed = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    # Triggers
    on = parsed.get("on", parsed.get(True, {}))
    assert "pull_request" in on, "pull_request trigger required"
    assert on["pull_request"]["paths"] == [
        "scripts/package_lifecycle.py",
        ".github/workflows/package-lifecycle.yml",
    ]
    assert "schedule" in on, "schedule trigger required"
    cron = on["schedule"][0]["cron"]
    assert cron != "0 2 * * 0", "cron must be distinct from build-matrix.yml"
    assert "workflow_dispatch" in on, "workflow_dispatch trigger required"

    # Least privilege permissions
    assert parsed.get("permissions") == {"contents": "read"}
    for job_id, job in parsed.get("jobs", {}).items():
        assert job.get("permissions") == {"contents": "read"}, (
            f"job {job_id} must restrict permissions"
        )

    # Matrix derivation: plan job outputs matrix from versions.json
    plan_steps = parsed["jobs"]["plan"]["steps"]
    plan_run = "\n".join(s.get("run", "") for s in plan_steps)
    assert "versions.json" in plan_run
    assert "matrix=" in plan_run

    # Lifecycle job: cosign verification, sha256sum -c before docker run, image digest, NUCLEUS_DISPOSABLE_ROOT
    lifecycle_steps = parsed["jobs"]["lifecycle"]["steps"]
    step_runs = [s.get("run", "") for s in lifecycle_steps]
    combined_run = "\n".join(step_runs)

    # cosign identity string
    assert "cosign verify-blob" in combined_run
    assert '--certificate-identity "${identity}"' in combined_run
    assert "scripts/release_signer_identity.py" in combined_run
    assert "cordanaLLM/nucleus" not in workflow_path.read_text(encoding="utf-8"), (
        "a fork verifies its own releases"
    )
    assert "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in combined_run


def test_package_lifecycle_runs_verified_assets_in_the_build_image():
    """package-lifecycle.yml checks SHA256SUMS before docker run, in build-matrix's image."""
    parsed = yaml.safe_load((WORKFLOWS_DIR / "package-lifecycle.yml").read_text(encoding="utf-8"))
    lifecycle_steps = parsed["jobs"]["lifecycle"]["steps"]
    combined_run = "\n".join(s.get("run", "") for s in lifecycle_steps)

    # sha256sum -c before docker run
    sha256_index = combined_run.index("sha256sum -c SHA256SUMS")
    docker_index = combined_run.index("docker run")
    assert sha256_index < docker_index, "sha256sum -c must run before docker run"

    # image digest equals build-matrix's
    parsed_bm = yaml.safe_load((WORKFLOWS_DIR / "build-matrix.yml").read_text(encoding="utf-8"))
    bm_image = parsed_bm["jobs"]["build"]["container"]["image"]
    run_step = next(
        s
        for s in lifecycle_steps
        if s.get("name") == "Run Package Lifecycle in Disposable Container"
    )
    assert run_step["env"]["BUILD_IMAGE"] == bm_image

    # NUCLEUS_DISPOSABLE_ROOT passed
    assert "-e NUCLEUS_DISPOSABLE_ROOT=1" in combined_run
    # The arguments reach the script as "$@", never word-split into the command string.
    assert 'package_lifecycle.py "$@"' in combined_run
    assert '"${cmd_args[@]}"' in combined_run and "${cmd_args[*]}" not in combined_run


RELEASE_PR = {
    "PR_AUTHOR": "github-actions[bot]",
    "PR_HEAD_REF": "release-please--branches--main--components--nucleus",
    "PR_TITLE": "chore(main): release nucleus 0.3.1",
    "PR_BODY": "Generated changelog without an issue reference.",
    "PR_MILESTONE": "",
    "PR_LABELS": '["autorelease: pending"]',
}


def test_release_waiver_passes_the_release_please_pull_request(tmp_path):
    """Issue #24, positive: release-please's own pull request merges, and the log says why."""
    result = _run_metadata_gate(tmp_path, **RELEASE_PR)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Metadata gates waived" in result.stdout
    assert "github-actions[bot] on release-please--branches--main" in result.stdout


@pytest.mark.parametrize(
    "override",
    [
        pytest.param({"PR_AUTHOR": "lusoris"}, id="human-on-release-branch-with-label"),
        pytest.param({"PR_HEAD_REF": "fix/example"}, id="bot-off-release-branch"),
        pytest.param(
            {"PR_HEAD_REF": "x-release-please--branches--main"}, id="bot-prefix-not-anchored"
        ),
        pytest.param({"PR_AUTHOR": "github-actions"}, id="not-the-bot-account"),
    ],
)
def test_release_waiver_needs_the_bot_and_its_branch(tmp_path, override):
    """Issue #24, boundary: the label alone, or the bot off its branch, is not waived."""
    # The forge answers that the pull request has no milestone, so the gate reaches its own
    # milestone check instead of stopping on a failed query.
    stub = tmp_path / "gh"
    stub.write_text("#!/bin/sh\necho null\n", encoding="utf-8")
    stub.chmod(0o755)
    path = f"{tmp_path}{os.pathsep}{os.environ['PATH']}"
    result = _run_metadata_gate(None, **{**RELEASE_PR, **override, "PATH": path})
    assert result.returncode == 1, result.stdout
    assert "waived" not in result.stdout
    assert "missing an assigned milestone" in result.stdout + result.stderr


RENOVATE_PR = {
    "PR_AUTHOR": "renovate[bot]",
    "PR_HEAD_REF": "renovate/github-actions",
    "PR_TITLE": "chore(deps): update github-actions",
    "PR_BODY": "This PR contains the following updates: no issue reference.",
    "PR_MILESTONE": "",
    "PR_LABELS": '["dependencies"]',
}


def test_dependency_waiver_passes_the_renovate_pull_request(tmp_path):
    """Positive: Renovate's own update pull request (#229) merges, and the log says why."""
    result = _run_metadata_gate(tmp_path, **RENOVATE_PR)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Metadata gates waived" in result.stdout
    assert "renovate[bot] on renovate/github-actions" in result.stdout


@pytest.mark.parametrize(
    "override",
    [
        pytest.param({"PR_AUTHOR": "lusoris"}, id="human-on-renovate-branch"),
        pytest.param({"PR_AUTHOR": "renovate"}, id="not-the-bot-account"),
        pytest.param({"PR_AUTHOR": "github-actions[bot]"}, id="other-bot-on-renovate-branch"),
        pytest.param({"PR_HEAD_REF": "fix/example"}, id="bot-off-renovate-branch"),
        pytest.param({"PR_HEAD_REF": "x-renovate/github-actions"}, id="prefix-not-anchored"),
        pytest.param({"PR_HEAD_REF": "renovate"}, id="prefix-without-slash"),
    ],
)
def test_dependency_waiver_needs_the_bot_and_its_branch(tmp_path, override):
    """Negative and boundary: an impostor author, or the bot off its branch, is not waived."""
    stub = tmp_path / "gh"
    stub.write_text("#!/bin/sh\necho null\n", encoding="utf-8")
    stub.chmod(0o755)
    path = f"{tmp_path}{os.pathsep}{os.environ['PATH']}"
    result = _run_metadata_gate(None, **{**RENOVATE_PR, **override, "PATH": path})
    assert result.returncode == 1, result.stdout
    assert "waived" not in result.stdout
    assert "missing an assigned milestone" in result.stdout + result.stderr


def test_dependency_waiver_does_not_exempt_a_skip_marker():
    """A Renovate pull request with a skip marker is refused before the waiver."""
    result = _run_metadata_gate(**{**RENOVATE_PR, "PR_TITLE": "chore(deps): bump [skip ci]"})
    assert result.returncode == 1, result.stdout
    assert "skip marker" in result.stdout + result.stderr


def test_container_jobs_install_envsubst_before_cosign_installer():
    """sigstore/cosign-installer v4 runs envsubst on its install-dir input; the bare
    ubuntu:26.04 image has none (exit 127 on #233), so a container job installs
    gettext-base in a step before the installer."""
    checked = 0
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in parsed.get("jobs", {}).items():
            steps = job.get("steps", [])
            uses = [
                i for i, s in enumerate(steps) if "sigstore/cosign-installer" in s.get("uses", "")
            ]
            if not uses or "container" not in job:
                continue
            checked += 1
            before = " ".join(s.get("run", "") for s in steps[: uses[0]])
            assert re.search(r"\bgettext-base\b", before), f"{path.name}:{name}"
    assert checked >= 2


def test_every_pull_request_job_stops_on_a_draft():
    """A draft runs no gate (HISS-18): each job a pull request starts begins with the draft step,
    and the trigger listens for ready_for_review so the gate runs once the draft is marked ready."""
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        # PyYAML reads an unquoted key "on" as a boolean.
        triggers = parsed.get(True, parsed.get("on"))
        if not isinstance(triggers, dict) or "pull_request" not in triggers:
            continue
        types = (triggers["pull_request"] or {}).get("types", [])
        assert {"opened", "synchronize", "reopened", "ready_for_review"} <= set(types), path.name
        for name, job in parsed["jobs"].items():
            first = job["steps"][0]
            assert first["name"] == DRAFT_STEP, f"{path.name}: {name}"
            assert first["if"] == "github.event.pull_request.draft == true", f"{path.name}: {name}"
            assert first["run"].rstrip().endswith("exit 1"), f"{path.name}: {name}"


def test_publish_release_wires_make_latest_and_prerelease_outputs():
    """Issue #115: publish-release.yml configures make_latest and prerelease from build outputs."""
    publish_parsed = yaml.safe_load(
        (WORKFLOWS_DIR / "publish-release.yml").read_text(encoding="utf-8")
    )
    publish_steps = publish_parsed["jobs"]["publish"]["steps"]
    publish_names = [step.get("name", "") for step in publish_steps]
    release = publish_steps[publish_names.index("Publish to GitHub Release")]
    assert release["with"]["make_latest"] == "${{ needs.build.outputs.make_latest }}"
    assert release["with"]["prerelease"] == "${{ needs.build.outputs.prerelease == 'true' }}"


def test_release_build_outputs_make_latest_and_prerelease():
    """Issue #115: release-build.yml exposes make_latest and prerelease at workflow and job levels."""
    build_parsed = yaml.safe_load((WORKFLOWS_DIR / "release-build.yml").read_text(encoding="utf-8"))
    on_triggers = build_parsed.get("on", build_parsed.get(True, {}))
    call_outputs = on_triggers["workflow_call"]["outputs"]
    assert call_outputs["make_latest"]["value"] == "${{ jobs.build.outputs.make_latest }}"
    assert call_outputs["prerelease"]["value"] == "${{ jobs.build.outputs.prerelease }}"

    job_outputs = build_parsed["jobs"]["build"]["outputs"]
    assert job_outputs["make_latest"] == "${{ steps.meta.outputs.make_latest }}"
    assert job_outputs["prerelease"] == "${{ steps.meta.outputs.prerelease }}"


def test_release_please_marks_tool_release_not_latest():
    """Issue #115: release-please.yml unsets latest on created tool releases."""
    rp_parsed = yaml.safe_load((WORKFLOWS_DIR / "release-please.yml").read_text(encoding="utf-8"))
    steps = rp_parsed["jobs"]["release-please"]["steps"]
    names = [s.get("name", "") for s in steps]
    rp_index = names.index("Run Release Please")
    assert "Mark Tool Release as Not Latest" in names
    edit_step = steps[names.index("Mark Tool Release as Not Latest")]
    assert names.index("Mark Tool Release as Not Latest") > rp_index
    assert edit_step["if"] == "${{ steps.release.outputs.release_created }}"
    assert "gh release edit" in edit_step["run"]
    assert "--latest=false" in edit_step["run"]
    assert edit_step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert edit_step["env"]["TAG_NAME"] == "${{ steps.release.outputs.tag_name }}"


def test_release_please_verifies_tool_release():
    """release-please.yml declares verify-tool-release job and workflow_dispatch tag input."""
    rp_parsed = yaml.safe_load((WORKFLOWS_DIR / "release-please.yml").read_text(encoding="utf-8"))

    on_triggers = rp_parsed.get("on", rp_parsed.get(True, {}))
    assert "workflow_dispatch" in on_triggers
    dispatch_inputs = on_triggers["workflow_dispatch"].get("inputs", {})
    assert "tag" in dispatch_inputs

    jobs = rp_parsed["jobs"]
    assert "verify-tool-release" in jobs
    job = jobs["verify-tool-release"]

    assert job["needs"] == "release-please"
    assert job["if"] == "needs.release-please.outputs.release_created == 'true' || inputs.tag != ''"
    assert job["permissions"] == {"contents": "read"}

    steps = job["steps"]
    checkout = steps[0]
    assert "actions/checkout" in checkout["uses"]
    assert checkout["with"]["persist-credentials"] is False

    verify_step = steps[1]
    assert "python3 scripts/check_tool_release.py" in verify_step["run"]


# praetor-docs.yml is installed and locked by praetor (renovate.json); its group is praetor's.
CONCURRENCY_OWNED_ELSEWHERE = {"praetor-docs.yml"}
CANCEL_ON_PULL_REQUEST = "${{ github.event_name == 'pull_request' }}"


def test_pull_request_workflows_cancel_superseded_runs():
    """A push to a pull request cancels its previous head's runs; a run for main is never cancelled."""
    missing = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        if path.name in CONCURRENCY_OWNED_ELSEWHERE:
            continue
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        on = workflow.get(True, workflow.get("on"))
        if not isinstance(on, dict) or "pull_request" not in on:
            continue
        concurrency = workflow.get("concurrency") or {}
        group = " ".join(str(concurrency.get("group", "")).split())
        cancel = concurrency.get("cancel-in-progress")
        # True is safe only where every event other than a pull request gets a group of its own.
        if not group or not (
            cancel == CANCEL_ON_PULL_REQUEST or (cancel is True and "github.run_id" in group)
        ):
            missing.append(path.name)
    assert missing == [], (
        f"pull_request workflows that keep superseded runs or cancel runs for main: {missing}"
    )


def _reproducibility_job() -> dict:
    return yaml.safe_load((WORKFLOWS_DIR / "build-matrix.yml").read_text(encoding="utf-8"))["jobs"][
        "reproducibility"
    ]


def test_reproducibility_builds_one_leg_twice_with_one_key_and_compares():
    """Issue #77: two builds of one tag, compared byte for byte, are the reproducibility gate."""
    job = _reproducibility_job()
    assert job["permissions"] == {"contents": "read"}
    assert BUILD_IMAGE.fullmatch(job["container"]["image"])
    assert job["steps"][0]["name"] == DRAFT_STEP
    steps = {step.get("name", ""): step for step in job["steps"]}
    build = steps["Build Twice and Compare"]["run"]
    assert "for side in a b; do" in build and 'rm -rf "${WORK_DIR}"' in build
    assert '--module-key="${KEY}"' in build and '--output-dir="repro/${side}"' in build
    assert "./scripts/verify-reproducibility.sh --dir-a=repro/a --dir-b=repro/b\n" in build
    assert "ccache" not in build and "--dry-run" not in build and "|| true" not in build
    delete = steps["Delete the Module Signing Key"]
    assert delete["if"].startswith("always()") and 'rm -rf "${KEY_DIR}"' in delete["run"]
    names = [step.get("name", "") for step in job["steps"]]
    assert names.index("Build Twice and Compare") < names.index("Delete the Module Signing Key")


def test_required_checks_waits_longer_than_the_reproducibility_job():
    run = yaml.safe_load((WORKFLOWS_DIR / "required-aggregator.yml").read_text(encoding="utf-8"))[
        "jobs"
    ]["required-checks"]["steps"][-1]["run"]
    wait = int(re.search(r"--timeout (\d+)", run)[1])
    assert wait > _reproducibility_job()["timeout-minutes"] * 60


def test_x86_64_boots_prove_bbr3_and_sched_ext_from_the_manifest_binding():
    step_list = _build_matrix()["steps"]
    names = [step.get("name", "") for step in step_list]
    steps = {step.get("name", ""): step for step in step_list}
    sched_build = steps["Build the sched_ext Scheduler"]
    assert sched_build["if"] == "matrix.arch == 'x86_64' && env.DRY_RUN != 'true'"
    assert sched_build["shell"] == "bash"
    assert sched_build["env"]["WORK_DIR"] == "${{ steps.disk.outputs.work_dir }}"
    assert "scripts/build_sched_ext_scheduler.sh" in sched_build["run"]
    assert '--source-tree="${WORK_DIR}/linux-${STREAM}"' in sched_build["run"]
    assert '--vmlinux="${WORK_DIR}/${STREAM}-${ARCH}/kbuild/vmlinux"' in sched_build["run"]
    assert '--output="records/scx_simple-${STREAM}-${ARCH}"' in sched_build["run"]
    # The streams that load a scheduler come from versions.json too: lts 6.18's scx_simple does
    # not compile against its own kernel, so a hardcoded "every x86_64 leg" went red there.
    assert sched_build["id"] == "scheduler"
    assert (
        'bound="$(python3 scripts/versions_query.py scheduler-streams simple)"'
        in sched_build["run"]
    )
    assert "|| echo" not in sched_build["run"]
    assert 'echo "scheduler=${scheduler}" >> "${GITHUB_OUTPUT}"' in sched_build["run"]
    sched_if = sched_build["run"].index('if [[ "${scheduler}" == "yes" ]]')
    assert sched_if < sched_build["run"].index("scripts/build_sched_ext_scheduler.sh")
    assert (
        names.index("Compile Kernel Package")
        < names.index("Build the sched_ext Scheduler")
        < names.index("Boot the Kernel to Userspace")
    )
    boot = steps["Boot the Kernel to Userspace"]
    judge = steps["Refuse a console of another release"]
    assert boot["id"] == "boot" and boot["shell"] == judge["shell"] == "bash"
    run = boot["run"]
    # The binding is read from versions.json; a failed read must fail the step, never read as
    # "not bound" (an `|| echo no` turned an IndentationError into a silently skipped check).
    assert 'bound="$(python3 scripts/versions_query.py series-streams bbr3)"' in run
    assert "|| echo" not in run and "python3 -c" not in run
    assert "kernel/net/ipv4/tcp_bbr3.ko" in run
    assert "--require-congestion=bbr --require-congestion=bbr3" in run
    assert "--require-sched-ext" in run
    assert boot["env"]["SCHEDULER"] == judge["env"]["SCHEDULER"]
    assert boot["env"]["SCHEDULER"] == "${{ steps.scheduler.outputs.scheduler }}"
    bound_if = run.index('if [[ "${SCHEDULER}" == "yes" ]]')
    assert bound_if < run.index('--scheduler="records/scx_simple-${STREAM}-${ARCH}"')
    assert bound_if < run.index("--require-scheduler=simple")
    assert run.index("--require-sched-ext") < bound_if
    assert 'echo "bbr3=${bbr3}" >> "${GITHUB_OUTPUT}"' in run
    assert judge["env"]["BBR3"] == "${{ steps.boot.outputs.bbr3 }}"
    judge_if = judge["run"].index('if [[ "${SCHEDULER}" == "yes" ]]')
    assert judge_if < judge["run"].index("--require-scheduler=simple")
    assert "--require-congestion=planted-cc" in judge["run"]
    assert "--require-scheduler=planted-sched" in judge["run"]


MODULE_JOBS = [
    (
        "openzfs-module",
        "Build OpenZFS Module (${{ matrix.stream }})",
        "openzfs",
        "check-openzfs-module.sh",
    ),
    (
        "nvidia-open-module",
        "Build NVIDIA Open Module (${{ matrix.stream }})",
        "nvidia_open",
        "check-nvidia-open-module.sh",
    ),
]


def _build_matrix_jobs():
    return yaml.safe_load((WORKFLOWS_DIR / "build-matrix.yml").read_text(encoding="utf-8"))["jobs"]


@pytest.mark.parametrize(("job_id", "expected_name", "mod_key", "script_name"), MODULE_JOBS)
def test_build_matrix_builds_modules_from_same_sha_headers(
    job_id, expected_name, mod_key, script_name
):
    """PR module checks run in build-matrix.yml against the x86_64 headers artifact from build."""
    jobs = _build_matrix_jobs()
    assert "plan-modules" in jobs
    job = jobs[job_id]
    assert job["name"] == expected_name
    assert sorted(job["needs"]) == ["build", "plan-modules"]
    assert job["permissions"] == {"contents": "read"}
    assert job["steps"][0]["name"] == DRAFT_STEP
    assert job["strategy"]["matrix"] == f"${{{{ fromJSON(needs.plan-modules.outputs.{mod_key}) }}}}"
    assert job["env"]["DRY_RUN"] == "${{ github.event.inputs.dry_run == 'true' }}"
    build_step = job["steps"][-1]
    assert build_step["if"] == "env.DRY_RUN != 'true'"
    assert f"./scripts/{script_name}" in build_step["run"]


def _run_module_step(run: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ["PATH"], "STREAM": "mainstream"}
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", run],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(("job_id", "expected_name", "mod_key", "script_name"), MODULE_JOBS)
def test_build_matrix_module_step_requires_exactly_one_headers_deb(
    tmp_path: Path, job_id, expected_name, mod_key, script_name
):
    """The module step refuses 0 and 2 headers packages and runs the check on exactly 1."""
    run = _build_matrix_jobs()[job_id]["steps"][-1]["run"]
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    stub = scripts_dir / script_name
    stub.write_text('#!/usr/bin/env bash\necho "invoked: $*"\n', encoding="utf-8")
    stub.chmod(0o755)
    out_dir = tmp_path / "output" / "mainstream-x86_64"
    out_dir.mkdir(parents=True)

    res_zero = _run_module_step(run, tmp_path)
    assert res_zero.returncode == 1
    assert "found 0: none" in res_zero.stderr

    deb1 = out_dir / "linux-headers-7.2.10-lusoris1-mainstream_7.2.10-lusoris1_amd64.deb"
    deb1.write_text("deb1", encoding="utf-8")
    res_one = _run_module_step(run, tmp_path)
    assert res_one.returncode == 0, res_one.stderr
    assert f"--headers-deb=output/mainstream-x86_64/{deb1.name}" in res_one.stdout

    deb2 = out_dir / "linux-headers-7.2.11-lusoris1-mainstream_7.2.11-lusoris1_amd64.deb"
    deb2.write_text("deb2", encoding="utf-8")
    res_two = _run_module_step(run, tmp_path)
    assert res_two.returncode == 1
    assert "found 2:" in res_two.stderr
    assert deb1.name in res_two.stderr and deb2.name in res_two.stderr


KERNEL_SLOT_PATTERN = re.compile(
    r"nucleus-kernel-slot-\$\{\{ fromJSON\('(\[[0-9,]+\])'\)\[strategy\.job-index\] \}\}"
)
EXPECTED_KERNEL_SLOT_TABLE_STR = "[0,1,2,3,4,5,0,1,2,3,4,5,0,1,2,3,4,5,0,1,2,3,4,5]"

KERNEL_POOL_JOBS = [
    ("build-matrix.yml", "build"),
    ("build-matrix.yml", "reproducibility"),
    ("build-matrix.yml", "openzfs-module"),
    ("build-matrix.yml", "nvidia-open-module"),
    ("nvidia-open-module.yml", "build"),
    ("openzfs-module.yml", "build"),
    ("package-lifecycle.yml", "lifecycle"),
    ("verify-requirements.yml", "resolve"),
    ("release-build.yml", "build"),
]


def _kernel_slot_ok(job: dict) -> bool:
    conc = job.get("concurrency")
    if not isinstance(conc, dict):
        return False
    if conc.get("queue") != "max":
        return False
    if conc.get("cancel-in-progress") is not False:
        return False
    group = conc.get("group", "")
    m = KERNEL_SLOT_PATTERN.fullmatch(group)
    if not m:
        return False
    table_str = m.group(1)
    if table_str != EXPECTED_KERNEL_SLOT_TABLE_STR:
        return False
    if set(json.loads(table_str)) != set(range(6)):
        return False
    return True


def _load_job(wf_filename: str, job_id: str) -> dict:
    wf = yaml.safe_load((WORKFLOWS_DIR / wf_filename).read_text(encoding="utf-8"))
    assert job_id in wf["jobs"], f"{job_id} not found in {wf_filename}"
    return wf["jobs"][job_id]


def test_kernel_jobs_share_six_repository_wide_slots():
    """Every kernel and module job takes one of the six repository-wide slots."""
    for wf_filename, job_id in KERNEL_POOL_JOBS:
        assert _kernel_slot_ok(_load_job(wf_filename, job_id)), f"{wf_filename} job {job_id}"
    build = _load_job("build-matrix.yml", "build")
    legs = len(build["strategy"]["matrix"]["stream"]) * len(build["strategy"]["matrix"]["arch"])
    assert len(json.loads(EXPECTED_KERNEL_SLOT_TABLE_STR)) >= legs


def test_kernel_slot_check_refuses_mutations():
    """A slot that cancels, keeps no queue or uses another table is refused."""
    build = _load_job("build-matrix.yml", "build")
    mutations = (
        ("queue", "single"),
        ("cancel-in-progress", True),
        ("group", build["concurrency"]["group"].replace("5]", "6]", 1)),
    )
    for key, value in mutations:
        bad = json.loads(json.dumps(build))
        bad["concurrency"][key] = value
        assert not _kernel_slot_ok(bad), key
    del build["concurrency"]
    assert not _kernel_slot_ok(build)


def _compiles(job: dict) -> bool:
    for step in job.get("steps", []):
        run = step.get("run", "") if isinstance(step, dict) else ""
        if "check-openzfs-module.sh" in run or "check-nvidia-open-module.sh" in run:
            return True
        lines = [line for line in run.splitlines() if "build_kernel.sh" in line]
        if any("--dry-run" not in line for line in lines):
            return True
    return False


def test_every_compiling_job_is_in_the_kernel_pool():
    """A job that compiles a kernel or a module outside the pool is refused."""
    for wf_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
        for job_id, job in wf.get("jobs", {}).items():
            if isinstance(job, dict) and _compiles(job):
                assert (wf_path.name, job_id) in KERNEL_POOL_JOBS, f"{wf_path.name} job {job_id}"
    assert _compiles({"steps": [{"run": "scripts/build_kernel.sh --stream lts"}]})
    assert not _compiles({"steps": [{"run": "scripts/build_kernel.sh --dry-run"}]})


def _scan_queue_ok(job: dict) -> bool:
    return job.get("concurrency") == {
        "group": "nucleus-scan-${{ github.job }}",
        "queue": "max",
        "cancel-in-progress": False,
    }


def test_security_scanners_queue_per_scanner():
    """Security scanners declare dedicated repository-wide FIFO queues per scanner."""
    for job_id in ("semgrep", "trivy", "gitleaks"):
        assert _scan_queue_ok(_load_job("security-scans.yml", job_id)), job_id
    good = _load_job("security-scans.yml", "semgrep")
    for key, value in (("queue", "single"), ("cancel-in-progress", True), ("group", "scan")):
        bad = json.loads(json.dumps(good))
        bad["concurrency"][key] = value
        assert not _scan_queue_ok(bad), key


def test_actionlint_ignores_queue_only_where_used():
    """actionlint config ignores queue error only on workflows that declare queue."""
    config_path = REPO_ROOT / ".github" / "actionlint.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    paths = config.get("paths", {})
    assert paths, "actionlint.yaml missing paths block"

    covered_workflows = set()
    for pat, cfg in paths.items():
        assert cfg.get("ignore") == ['unexpected key "queue" for "concurrency" section'], (
            f"unexpected ignore pattern for path {pat}"
        )
        m = re.fullmatch(r"\*\*/\.github/workflows/\{([^}]+)\}\.yml", pat)
        if m:
            for name in m.group(1).split(","):
                covered_workflows.add(f"{name.strip()}.yml")
        else:
            covered_workflows.add(Path(pat).name)

    workflows_with_queue = {
        wf.name for wf in WORKFLOWS_DIR.glob("*.yml") if "queue:" in wf.read_text(encoding="utf-8")
    }
    assert workflows_with_queue == covered_workflows, (
        f"Mismatch between workflows using queue ({workflows_with_queue}) and covered ({covered_workflows})"
    )
