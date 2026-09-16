"""Test kernel configuration fragments in nucleus."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

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
