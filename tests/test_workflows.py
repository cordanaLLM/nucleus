"""Test suite for GitHub Actions workflows validation and least-privilege permissions."""

import json
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
    """Ensure all 14 production CI/CD workflows exist."""
    workflows = list(WORKFLOWS_DIR.glob("*.yml"))
    assert len(workflows) == 14, f"Expected exactly 14 workflows, found {len(workflows)}"


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
    for pattern in (
        "output/*.deb",
        "output/vmlinuz-*",
        "output/*.manifest.json",
        "output/*.config",
        "output/SHA256SUMS*",
    ):
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
    assert "--cross" not in gate["run"], (
        "the release builds natively and must ship its headers package"
    )
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
    assert "-lusoris1" not in text, "no hardcoded revision"
    assert "/dev/urandom" not in text
    checkout = steps[names.index("Checkout Repository")]
    assert checkout["with"]["persist-credentials"] is False


def _build_matrix():
    return yaml.safe_load((WORKFLOWS_DIR / "build-matrix.yml").read_text(encoding="utf-8"))["jobs"][
        "build"
    ]


SCRIPT_REF = re.compile(r"(?:\bscripts/|\$\{SCRIPT_DIR\}/)([A-Za-z0-9_.-]+)")
PY_IMPORT = re.compile(r"^(?:from|import) ([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
BUILD_INPUTS = {"kconfig/**", "keys/**", "versions.json", "docker/Dockerfile.builder"}


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
    assert {"scripts/build_kernel.sh", "scripts/verify_kernel_requirement.py"} <= scripts
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


def test_required_checks_waits_longer_than_the_longest_build_leg():
    legs = max(entry["timeout"] for entry in _build_matrix()["strategy"]["matrix"]["include"])
    job = yaml.safe_load((WORKFLOWS_DIR / "required-aggregator.yml").read_text(encoding="utf-8"))[
        "jobs"
    ]["required-checks"]
    run = job["steps"][-1]["run"]
    wait = int(re.search(r"--timeout (\d+)", run)[1])
    assert wait > legs * 60, f"the gate gives up at {wait} s, before a {legs}-minute leg ends"
    assert job["timeout-minutes"] * 60 > wait, "the job is killed before the gate's own timeout"


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
    assert boot["if"].startswith("matrix.arch == 'x86_64'")
    assert "scripts/boot_smoke.py" in boot["run"]
    stage = steps["Stage Artifacts and Checksums"]["run"]
    assert 'OUTPUT_DIR="output/${STREAM}-${ARCH}" ./scripts/publish_release.sh' in stage
    assert order.index("Compile Kernel Package") < order.index("Boot the Kernel to Userspace")
    assert order.index("Boot the Kernel to Userspace") < order.index(
        "Stage Artifacts and Checksums"
    )
    restore = steps["Restore the Compiler Cache"]
    save = steps["Save the Compiler Cache"]
    assert restore["uses"].startswith("actions/cache/restore@") and save["uses"].startswith(
        "actions/cache/save@"
    )
    assert "${{ steps.build.outputs.config_sha256 }}" in save["with"]["key"]
    assert "${{ matrix.stream }}-${{ matrix.arch }}" in save["with"]["key"]


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
    steps, names = _publish_release_steps()
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
    steps, names = _publish_release_steps()
    run = steps[names.index("Build and Boot the Tagged Stream")]["run"]
    assert run.index("./scripts/build_kernel.sh") < run.index("python3 scripts/boot_smoke.py")
    assert '--log="records/boot-${STREAM}-x86_64.log"' in run
    assert 'exit "${status}"' in run and "|| status=$?" in run
    assert names.index("Build and Boot the Tagged Stream") < names.index(
        "Checksum and Sign the Release"
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
    assert "python3 -c" not in resolve["run"], (
        "the tag grammar lives in scripts/resolve_release_tag.py"
    )
    assert "${{" not in resolve["run"], "the tag reaches the resolver through env, never inline"


def test_publish_release_tag_and_ref_cannot_diverge():
    """A dispatched run must start from the tag it releases, and the release names that tag."""
    steps, names = _publish_release_steps()
    resolve = steps[names.index("Resolve Stream and Version")]
    assert '--ref "${GITHUB_REF}"' in resolve["run"], (
        "the resolver must compare the run's ref with the tag"
    )
    release = steps[names.index("Publish to GitHub Release")]
    assert release["with"]["tag_name"] == "${{ steps.meta.outputs.release_tag }}"


def _guard_step():
    steps, names = _publish_release_steps()
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
    """Ensure one always() job in security-scans.yml needs every scan job."""
    jobs, gate = _security_gate_job()
    assert str(gate["if"]).strip() == "always()", "the gate must report on every run"
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
    pin = parsed["env"]["PRAETOR_COMMIT"]
    assert re.fullmatch(r"[0-9a-f]{40}", pin), f"PRAETOR_COMMIT must be a full commit SHA: {pin}"
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
    assert checkout["with"]["ref"] == "${{ env.PRAETOR_COMMIT }}"

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
    assert 'test "$(git -C praetor-src rev-parse HEAD)" = "${PRAETOR_COMMIT}"' in commands[0]


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
    result = _run_metadata_gate(tmp_path, **{**RELEASE_PR, **override})
    assert result.returncode == 1, result.stdout
    assert "waived" not in result.stdout
    assert "missing an assigned milestone" in result.stdout + result.stderr


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
