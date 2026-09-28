"""The survival check: every value a fragment requests must hold in the resolved .config."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import kconfig_survival as ks  # noqa: E402
import versions_query as vq  # noqa: E402

RESOLVED = """\
#
# Automatically generated file; DO NOT EDIT.
# Linux/x86_64 7.2.8 Kernel Configuration
#
CONFIG_CC_VERSION_TEXT="x86_64-linux-gnu-gcc (Ubuntu 15.2.0) 15.2.0"
CONFIG_SMP=y
CONFIG_NR_CPUS=512
CONFIG_VIRTIO_FS=m
CONFIG_LSM="landlock,lockdown,yama,apparmor,bpf"
# CONFIG_DEBUG_INFO_REDUCED is not set
CONFIG_PREEMPT_RT=y
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _check(tmp_path: Path, *fragments: str, resolved: str = RESOLVED) -> list[ks.Casualty]:
    paths = [_write(tmp_path, f"f{index}.config", text) for index, text in enumerate(fragments)]
    config = _write(tmp_path, ".config", resolved)
    return ks.casualties(ks.requested(paths), ks.read_config(config))


def test_every_request_that_holds_survives(tmp_path):
    fragment = (
        "CONFIG_SMP=y\nCONFIG_NR_CPUS=512\nCONFIG_VIRTIO_FS=m\n"
        'CONFIG_LSM="landlock,lockdown,yama,apparmor,bpf"\n'
        "# CONFIG_DEBUG_INFO_REDUCED is not set\n"
    )
    assert _check(tmp_path, fragment) == []


def test_a_dropped_symbol_is_a_casualty_and_reads_unrecorded(tmp_path):
    (lost,) = _check(tmp_path, "CONFIG_SMP=y\nCONFIG_DEFAULT_MQ_DEADLINE=y\n")
    assert (lost.request.symbol, lost.request.value, lost.resolved) == (
        "CONFIG_DEFAULT_MQ_DEADLINE",
        "y",
        None,
    )


def test_a_built_in_request_capped_to_a_module_is_a_casualty(tmp_path):
    (lost,) = _check(tmp_path, "CONFIG_VIRTIO_FS=y\n")
    assert (lost.request.value, lost.resolved) == ("y", "m")


def test_a_changed_number_or_string_is_a_casualty(tmp_path):
    lost = _check(tmp_path, "CONFIG_NR_CPUS=256\n", 'CONFIG_LSM="landlock"\n')
    assert [(c.request.symbol, c.resolved) for c in lost] == [
        ("CONFIG_LSM", '"landlock,lockdown,yama,apparmor,bpf"'),
        ("CONFIG_NR_CPUS", "512"),
    ]


def test_an_unset_request_holds_only_against_an_explicit_unset(tmp_path):
    assert _check(tmp_path, "# CONFIG_DEBUG_INFO_REDUCED is not set\n") == []
    (enabled,) = _check(tmp_path, "# CONFIG_PREEMPT_RT is not set\n")
    assert (enabled.request.value, enabled.resolved) == ("n", "y")
    (unrecorded,) = _check(tmp_path, "# CONFIG_KVM_GUEST is not set\n")
    assert unrecorded.resolved is None


def test_the_last_fragment_wins_and_is_the_one_named(tmp_path):
    (lost,) = _check(tmp_path, "CONFIG_VIRTIO_FS=m\n", "CONFIG_VIRTIO_FS=y\n")
    assert lost.request.fragment.endswith("f1.config")
    assert _check(tmp_path, "CONFIG_VIRTIO_FS=y\n", "CONFIG_VIRTIO_FS=m\n") == []


def test_a_later_unset_overrides_an_earlier_assignment(tmp_path):
    (lost,) = _check(tmp_path, "CONFIG_SMP=y\n", "# CONFIG_SMP is not set\n")
    assert (lost.request.value, lost.resolved) == ("n", "y")


def test_the_report_names_symbol_request_resolution_and_fragment(tmp_path):
    fragment = _write(tmp_path, "arm64.config", "CONFIG_KVM_GUEST=y\nCONFIG_VIRTIO_FS=y\n")
    config = _write(tmp_path, ".config", RESOLVED)
    requests = ks.requested([fragment])
    lines = ks.report_lines(str(config), requests, ks.casualties(requests, ks.read_config(config)))
    assert lines[0].startswith("survival: 2 of 2 requested symbols do not hold")
    assert f"CONFIG_KVM_GUEST: requested =y by {fragment}, resolved unrecorded" in lines[1]
    assert f"CONFIG_VIRTIO_FS: requested =y by {fragment}, resolved =m" in lines[2]


def _run(*args: str) -> subprocess.CompletedProcess:
    script = REPO_ROOT / "scripts" / "kconfig_survival.py"
    return subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True, check=False
    )


def test_the_command_exits_0_1_or_2(tmp_path):
    config = _write(tmp_path, ".config", RESOLVED)
    holds = _write(tmp_path, "holds.config", "CONFIG_SMP=y\n")
    lost = _write(tmp_path, "lost.config", "CONFIG_SMP=m\n")
    garbage = _write(tmp_path, "garbage.config", "CONFIG_SMP y\n")
    ok = _run("--config", str(config), str(holds))
    assert ok.returncode == 0 and "all 1 requested symbols hold" in ok.stdout
    failed = _run("--config", str(config), str(holds), str(lost))
    assert failed.returncode == 1 and "CONFIG_SMP: requested =m" in failed.stderr
    unreadable = _run("--config", str(config), str(garbage))
    assert unreadable.returncode == 2
    missing = _run("--config", str(tmp_path / "missing"), str(holds))
    assert missing.returncode == 2


# --- scripts/merge-config.sh --source-tree, against a stand-in kernel tree -------------------
# The stand-in answers the four make targets the script calls and merges fragments by
# appending them, so the plumbing is tested without a kernel: the real resolution of all
# twelve stream and architecture legs runs in verify-requirements.yml.

VERSIONS = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
MERGE_CONFIG = REPO_ROOT / "scripts" / "merge-config.sh"
STAND_IN_MAKEFILE = """\
kernelversion:
\t@echo {version}
x86_64_defconfig:
\t@echo 'CONFIG_FROM_DEFCONFIG=y' > $(O)/.config
olddefconfig:
\t@if [ -n "$(DROP)" ]; then sed -i '/^$(DROP)=/d' $(O)/.config; fi
kernelrelease:
\t@echo {version}
"""
STAND_IN_MERGE = """\
#!/bin/sh
set -e
[ "$1" = "-m" ] && shift
base="$1"
shift
for fragment in "$@"; do cat "$fragment" >> "$base"; done
"""
TOOLS = ("gcc", "flex", "bison", "bc", "pahole", "x86_64-linux-gnu-gcc", "x86_64-linux-gnu-ld")


@pytest.fixture
def stand_in(tmp_path):
    """A stand-in tree for mainstream, and a PATH on which every required tool resolves."""
    if shutil.which("make") is None:
        pytest.skip("needs make")
    version = vq.kernelversion(VERSIONS["streams"]["mainstream"]["version"])
    tree = tmp_path / "linux"
    (tree / "scripts" / "kconfig").mkdir(parents=True)
    (tree / "Makefile").write_text(STAND_IN_MAKEFILE.format(version=version), encoding="utf-8")
    merge = tree / "scripts" / "kconfig" / "merge_config.sh"
    merge.write_text(STAND_IN_MERGE, encoding="utf-8")
    merge.chmod(0o755)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for tool in TOOLS:
        stub = stubs / tool
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{os.environ['PATH']}:{stubs}"}
    return tree, env, version


def _resolve(tmp_path, tree, env, *extra, stream="mainstream"):
    output = tmp_path / "out" / "kernel.config"
    result = subprocess.run(
        [
            "bash",
            str(MERGE_CONFIG),
            "--arch=x86_64",
            f"--stream={stream}",
            f"--source-tree={tree}",
            f"--build-dir={tmp_path / 'kbuild'}",
            f"--output={output}",
            *extra,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, output


def test_source_tree_mode_writes_the_config_when_every_request_survives(tmp_path, stand_in):
    tree, env, version = stand_in
    result, output = _resolve(tmp_path, tree, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "survival: all" in result.stdout
    assert f"kernelrelease {version}" in result.stdout
    assert output.read_text(encoding="utf-8").startswith("CONFIG_FROM_DEFCONFIG=y\n")


def test_source_tree_mode_refuses_a_request_olddefconfig_dropped(tmp_path, stand_in):
    tree, env, _ = stand_in
    result, output = _resolve(tmp_path, tree, {**env, "DROP": "CONFIG_STRICT_KERNEL_RWX"})
    assert result.returncode != 0
    assert (
        "CONFIG_STRICT_KERNEL_RWX: requested =y by kconfig/security-hardened.config"
        in result.stderr
    )
    assert not output.exists(), "a configuration that lost a request is never written"


def test_source_tree_mode_refuses_a_tree_of_another_release(tmp_path, stand_in):
    tree, env, version = stand_in
    result, output = _resolve(tmp_path, tree, env, stream="lts")
    assert result.returncode != 0
    assert f"is kernel {version}; versions.json names" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    ("extra", "message"),
    [(["--dry-run"], "has no dry run"), (["--stream="], "--source-tree needs --stream")],
)
def test_source_tree_mode_refuses_what_it_cannot_resolve(tmp_path, stand_in, extra, message):
    tree, env, _ = stand_in
    result, _ = _resolve(tmp_path, tree, env, *extra)
    assert result.returncode != 0 and message in result.stderr


def test_source_tree_mode_refuses_a_directory_that_is_no_kernel_tree(tmp_path, stand_in):
    _, env, _ = stand_in
    result, _ = _resolve(tmp_path, tmp_path, env)
    assert result.returncode != 0 and "is not a kernel source tree" in result.stderr
