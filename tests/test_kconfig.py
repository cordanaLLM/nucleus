"""Test kernel configuration fragments in nucleus."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KCONFIG = REPO_ROOT / "kconfig"
VERSIONS = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import verify_kernel_requirement as vkr  # noqa: E402


def test_kconfig_fragments_exist():
    """Ensure baseline kconfig fragments exist for supported architectures."""
    kconfig_dir = REPO_ROOT / "kconfig"
    assert (kconfig_dir / "x86_64.config").exists()
    assert (kconfig_dir / "arm64.config").exists()
    assert (kconfig_dir / "riscv64.config").exists()
    assert (kconfig_dir / "security-hardened.config").exists()

def test_kconfig_mandatory_options():
    """Ensure essential virtualization and network options are declared."""
    x86_config = (REPO_ROOT / "kconfig" / "x86_64.config").read_text(encoding="utf-8")
    assert "CONFIG_VIRTIO=y" in x86_config
    assert "CONFIG_MQ_IOSCHED_DEADLINE=y" in x86_config
    assert "CONFIG_TCP_CONG_BBR=y" in x86_config
    assert "CONFIG_SCHED_CLASS_EXT=y" in x86_config

def test_security_hardened_kconfig():
    """Ensure KSPP security options are enabled in security-hardened.config."""
    sec_config = (REPO_ROOT / "kconfig" / "security-hardened.config").read_text(encoding="utf-8")
    assert "CONFIG_STACKPROTECTOR_STRONG=y" in sec_config
    assert "CONFIG_STRICT_KERNEL_RWX=y" in sec_config
    assert "CONFIG_RANDOMIZE_BASE=y" in sec_config


# --- declared dependencies of the downstream requirement symbols ---------------------------
# A fragment line survives `make olddefconfig` only when the symbol's Kconfig dependencies
# are met. These pin the dependencies the fragments declare for symbols the requirement
# documents ask for. They check declarations, not resolution: the survival check of
# scripts/merge-config.sh --source-tree, run for every leg by verify-requirements.yml, proves
# that a declared symbol survives (docs/adr/0008).


def test_config_lsm_is_set_once_and_lists_apparmor_and_bpf():
    """A string symbol a base .config already records survives olddefconfig unchanged."""
    lsm = vkr.read_config(KCONFIG / "security-hardened.config")["CONFIG_LSM"]
    order = lsm.strip('"').split(",")
    assert "apparmor" in order and "bpf" in order
    for arch in VERSIONS["architectures"]:
        assert "CONFIG_LSM" not in vkr.read_config(KCONFIG / f"{arch}.config"), arch


def test_the_realtime_stream_declares_expert_for_preempt_rt():
    """PREEMPT_RT depends on EXPERT && ARCH_SUPPORTS_RT (kernel/Kconfig.preempt)."""
    realtime = vkr.read_config(KCONFIG / "streams" / "realtime.config")
    assert realtime["CONFIG_PREEMPT_RT"] == "y"
    assert realtime["CONFIG_EXPERT"] == "y"


def test_btf_is_declared_with_its_chain_wherever_sched_ext_is():
    """SCHED_CLASS_EXT depends on DEBUG_INFO_BTF, which needs DEBUG_INFO and DEBUG_KERNEL."""
    for arch in VERSIONS["architectures"]:
        config = vkr.read_config(KCONFIG / f"{arch}.config")
        if config.get("CONFIG_SCHED_CLASS_EXT") != "y":
            continue
        for symbol in ("BPF_SYSCALL", "BPF_JIT", "DEBUG_KERNEL", "DEBUG_INFO_DWARF5", "DEBUG_INFO_BTF"):
            assert config.get(f"CONFIG_{symbol}") == "y", (arch, symbol)
        assert config.get("CONFIG_DEBUG_INFO_REDUCED") in (None, "n"), arch


def test_proc_config_gz_exists_where_a_requirement_can_probe_it():
    """The kernel-config probe reads /proc/config.gz: IKCONFIG and IKCONFIG_PROC."""
    for arch in vkr.ARCHITECTURES.values():
        config = vkr.read_config(KCONFIG / f"{arch}.config")
        assert config.get("CONFIG_IKCONFIG") == "y", arch
        assert config.get("CONFIG_IKCONFIG_PROC") == "y", arch


def test_bbr_is_declared_with_the_menu_it_sits_in():
    """TCP_CONG_BBR sits inside "if TCP_CONG_ADVANCED"; only the x86_64 defconfig sets it."""
    for arch in VERSIONS["architectures"]:
        config = vkr.read_config(KCONFIG / f"{arch}.config")
        if config.get("CONFIG_TCP_CONG_BBR") in ("y", "m"):
            assert config.get("CONFIG_TCP_CONG_ADVANCED") == "y", arch


def test_a_built_in_virtio_fs_is_declared_with_a_built_in_fuse():
    """VIRTIO_FS depends on FUSE_FS, and a tristate is capped by what it depends on."""
    for arch in VERSIONS["architectures"]:
        config = vkr.read_config(KCONFIG / f"{arch}.config")
        if config.get("CONFIG_VIRTIO_FS") == "y":
            assert config.get("CONFIG_FUSE_FS") == "y", arch


# --- BPF trampolines (docs/adr/0011) ---------------------------------------------------------
TRAMPOLINE_CHAIN = ("FTRACE", "FUNCTION_TRACER", "DYNAMIC_FTRACE", "DYNAMIC_FTRACE_WITH_DIRECT_CALLS")


def _layers() -> list[Path]:
    """Every fragment merge-config.sh can merge: baseline, architectures, streams."""
    layers = [KCONFIG / "security-hardened.config"]
    layers += [KCONFIG / f"{arch}.config" for arch in VERSIONS["architectures"]]
    return layers + sorted((KCONFIG / "streams").glob("*.config"))


def test_the_bpf_trampoline_chain_is_in_the_baseline_and_no_layer_drops_it():
    """BPF LSM, fentry and fexit attach through ftrace direct calls; without them attach fails."""
    baseline = vkr.read_config(KCONFIG / "security-hardened.config")
    for symbol in TRAMPOLINE_CHAIN:
        assert baseline.get(f"CONFIG_{symbol}") == "y", symbol
    for layer in _layers()[1:]:
        config = vkr.read_config(layer)
        for symbol in TRAMPOLINE_CHAIN:
            assert config.get(f"CONFIG_{symbol}") in (None, "y"), (layer.name, symbol)


def test_no_fragment_declares_a_tracer_beyond_the_trampoline_chain():
    """Function tracing is compiled in for the trampolines only; no tracer is added or started."""
    declared = set()
    for layer in _layers():
        declared |= {
            symbol.removeprefix("CONFIG_")
            for symbol, value in vkr.read_config(layer).items()
            if value != "n" and ("TRAC" in symbol or "FTRACE" in symbol)
        }
    assert declared == set(TRAMPOLINE_CHAIN)
