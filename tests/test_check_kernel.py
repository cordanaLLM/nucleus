# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Tests for single-kernel requirement checking (scripts/verify_kernel_requirement.py).

Ports and extends the Aegis-OS test suite (tools/kernel_requirement_check.py)
to verify that nucleus serves as the single source of truth for checking a single
kernel configuration against an aegis.p01-nucleus.kernel-requirement.v1 payload.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import verify_kernel_requirement as vkr  # noqa: E402

AEGIS_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "kernel-requirement" / "aegis-os.json"
VECTORS_DIR = REPO_ROOT / "tests" / "vectors" / "kernel-check"


def _sample_requirement(
    *features: vkr.Feature,
    correlation_id: str = "test:check-kernel-01",
    architectures: tuple[str, ...] = ("x86-64",),
    minimum_release: str = "6.12",
    module_abi: str | None = None,
) -> vkr.Requirement:
    """Create a synthetic Requirement for testing."""
    return vkr.Requirement(
        correlation_id=correlation_id,
        architectures=architectures,
        abi=vkr.Abi(minimum_release=minimum_release, module_abi=module_abi),
        features=features,
    )


def _conforming_x86_config() -> str:
    """A standard conforming x86_64 configuration text."""
    return (
        "# Generated test config\n"
        "CONFIG_X86_64=y\n"
        "CONFIG_BPF_SYSCALL=y\n"
        "CONFIG_OVERLAY_FS=m\n"
        "# CONFIG_DEBUG_INFO is not set\n"
    )


# --- parse_config_text and read_config --------------------------------------------------------


def test_parse_config_text_assignments_and_unset_lines():
    """Positive: assignments and unset lines are read into symbols."""
    text = 'CONFIG_A=y\nCONFIG_B=m\n# CONFIG_C is not set\nCONFIG_D="x"\n'
    found = vkr.parse_config_text(text, "test")
    assert found == {
        "CONFIG_A": "y",
        "CONFIG_B": "m",
        "CONFIG_C": "n",
        "CONFIG_D": '"x"',
    }


def test_parse_config_text_later_line_wins():
    """Boundary: last assignment for a symbol overrides earlier ones (Kconfig merge rule)."""
    text = "CONFIG_A=y\nCONFIG_A=m\n"
    found = vkr.parse_config_text(text, "test")
    assert found == {"CONFIG_A": "m"}


def test_parse_config_text_prose_comments_skipped():
    """Positive: comments and header lines are not treated as symbols."""
    text = "# Linux/x86 7.2.8 Kernel Configuration\n# Just a comment\n"
    assert vkr.parse_config_text(text, "test") == {}


def test_parse_config_text_oversized_bytes_refused():
    """Negative: configuration exceeding MAX_CONFIG_BYTES is refused with KconfigError."""
    huge_text = "x" * (vkr.MAX_CONFIG_BYTES + 1)
    with pytest.raises(vkr.KconfigError, match="configuration exceeds .* bytes"):
        vkr.parse_config_text(huge_text, "oversized_bytes")


def test_parse_config_text_oversized_lines_refused():
    """Negative: configuration exceeding MAX_CONFIG_LINES is refused with KconfigError."""
    many_lines = "\n" * (vkr.MAX_CONFIG_LINES + 1)
    with pytest.raises(vkr.KconfigError, match="configuration exceeds .* lines"):
        vkr.parse_config_text(many_lines, "oversized_lines")


def test_parse_config_text_ungrammatical_line_refused():
    """Negative: line outside Kconfig grammar is refused with KconfigError."""
    bad_text = "CONFIG_A=y\nNOT_A_VALID_LINE\n"
    with pytest.raises(vkr.KconfigError, match="not an assignment, an unset or a comment"):
        vkr.parse_config_text(bad_text, "bad_syntax")


def test_read_config_reads_file(tmp_path):
    """Positive: read_config delegates to parse_config_text on a file path."""
    cfg_file = tmp_path / "test.config"
    cfg_file.write_text("CONFIG_FOO=y\n# CONFIG_BAR is not set\n", encoding="utf-8")
    assert vkr.read_config(cfg_file) == {"CONFIG_FOO": "y", "CONFIG_BAR": "n"}


def test_parse_config_text_splits_on_newline_only():
    """Boundary: a form feed or line separator inside a line does not start a new line.

    read_config split on "\\n" before parse_config_text existed; str.splitlines would also
    split on \\x0c, \\x0b, \\x1c and \\u2028, and read a value's tail as a line of its own.
    """
    text = "CONFIG_A=y\x0cjunk\nCONFIG_B=m \n"
    assert vkr.parse_config_text(text, "ff") == {"CONFIG_A": "y\x0cjunk", "CONFIG_B": "m "}


# --- state_satisfied and describe_observed ----------------------------------------------------


def test_state_satisfied_rules():
    """Positive: four RequiredState rules satisfied by their respective readings."""
    assert vkr.state_satisfied("built-in", "y") is True
    assert vkr.state_satisfied("module", "m") is True
    assert vkr.state_satisfied("present", "y") is True
    assert vkr.state_satisfied("present", "m") is True
    assert vkr.state_satisfied("absent", "n") is True


def test_module_and_builtin_do_not_interchange():
    """Boundary: m does not satisfy built-in, and y does not satisfy module."""
    assert vkr.state_satisfied("built-in", "m") is False
    assert vkr.state_satisfied("module", "y") is False


def test_unobserved_satisfies_nothing():
    """Negative: unrecorded symbol satisfies no state, absent included."""
    for state in vkr.STATES:
        assert vkr.state_satisfied(state, None) is False


def test_non_tristate_satisfies_nothing():
    """Negative: non-tristate values do not satisfy boolean/tristate requirements."""
    for state in vkr.STATES:
        assert vkr.state_satisfied(state, '"y"') is False
        assert vkr.state_satisfied(state, "42") is False


def test_describe_observed_formatting():
    """Positive: describe_observed produces standard human strings."""
    assert vkr.describe_observed(None) == "unrecorded"
    assert vkr.describe_observed("n") == "=n (not set)"
    assert vkr.describe_observed("y") == "=y"
    assert vkr.describe_observed("m") == "=m"
    assert vkr.describe_observed('"bar"') == '="bar"'


# --- config_architecture ----------------------------------------------------------------------


def test_config_architecture_detection():
    """Positive: mkosi architecture spellings detected from =y symbols."""
    assert vkr.config_architecture({"CONFIG_X86_64": "y"}) == "x86-64"
    assert vkr.config_architecture({"CONFIG_ARM64": "y"}) == "arm64"
    assert vkr.config_architecture({}) is None
    assert vkr.config_architecture({"CONFIG_X86_64": "m"}) is None


# --- parse_requirement ------------------------------------------------------------------------


def test_parse_requirement_fixture_accepted():
    """Positive: the canonical aegis-os.json fixture parses without error."""
    req = vkr.parse_requirement(AEGIS_FIXTURE.read_bytes())
    assert req.correlation_id == "aegis-m18-kernel-requirement-0001"
    assert req.architectures == ("x86-64",)
    assert req.abi.minimum_release == "6.12"


def test_parse_requirement_unknown_key_refused():
    """Negative: unknown keys in document payload are refused (deny_unknown_fields)."""
    raw = json.loads(AEGIS_FIXTURE.read_text(encoding="utf-8"))
    raw["unknown_extra_field"] = "bad"
    with pytest.raises(vkr.RequirementError, match="unknown field"):
        vkr.parse_requirement(json.dumps(raw).encode("utf-8"))


# --- at_least and release floor ---------------------------------------------------------------


def test_release_at_least_boundary():
    """Boundary: release matching minimum exactly or with release suffix is admitted."""
    assert vkr.at_least("6.12", "6.12") is True
    assert vkr.at_least("6.12.0-lusoris1", "6.12") is True


def test_release_at_least_below_refused():
    """Negative: release below minimum floor is refused."""
    assert vkr.at_least("6.11.999", "6.12") is False
    assert vkr.at_least("6.11", "6.12") is False


def test_release_components_numeric_comparison():
    """Boundary: numeric component ordering distinguishes 6.100 from 6.12."""
    assert vkr.at_least("6.100", "6.12") is True
    assert vkr.release_components("7.2.8-lusoris1-realtime") == (7, 2, 8)


# --- check_kernel decision logic --------------------------------------------------------------


def test_check_kernel_conforming_satisfied():
    """Positive: a fully conforming configuration produces satisfied=True and no reasons."""
    req = _sample_requirement(
        vkr.Feature("CONFIG_BPF_SYSCALL", "built-in", "kernel-config", "REQ-01"),
        vkr.Feature("CONFIG_OVERLAY_FS", "module", "kernel-config", "REQ-01"),
        vkr.Feature("CONFIG_DEBUG_INFO", "absent", "kernel-config", "REQ-01"),
    )
    cfg = vkr.parse_config_text(_conforming_x86_config(), "conforming")
    verdict = vkr.check_kernel(req, cfg, release="7.2.10")
    assert verdict.satisfied is True
    assert verdict.reasons == ()
    assert len(verdict.checks) == 3
    assert all(c.satisfied for c in verdict.checks)


def test_check_kernel_unmet_feature_reasons():
    """Negative: unmet feature reasons name correlation-id, symbol, required-by, state, observed."""
    req = _sample_requirement(
        vkr.Feature("CONFIG_BPF_SYSCALL", "built-in", "kernel-config", "REQ-BPF"),
        correlation_id="test:cid-unmet",
    )
    cfg = vkr.parse_config_text("CONFIG_X86_64=y\nCONFIG_BPF_SYSCALL=m\n", "unmet")
    verdict = vkr.check_kernel(req, cfg, release="7.2.10")
    assert verdict.satisfied is False
    assert len(verdict.reasons) == 1
    reason = verdict.reasons[0]
    assert reason.startswith("test:cid-unmet: ")
    assert "CONFIG_BPF_SYSCALL" in reason
    assert "(required-by REQ-BPF)" in reason
    assert "requires built-in" in reason
    assert "observed =m" in reason


def test_check_kernel_unrecorded_feature_fails():
    """Negative: missing symbol in config is unrecorded and fails."""
    req = _sample_requirement(
        vkr.Feature("CONFIG_MISSING", "built-in", "kernel-config", "REQ-01"),
        correlation_id="test:cid-missing",
    )
    cfg = vkr.parse_config_text("CONFIG_X86_64=y\n", "missing")
    verdict = vkr.check_kernel(req, cfg, release="7.2.10")
    assert verdict.satisfied is False
    assert any("CONFIG_MISSING" in r and "unrecorded" in r for r in verdict.reasons)


def test_check_kernel_absent_unrecorded_fails():
    """Negative: a feature required absent fails when unrecorded in config."""
    req = _sample_requirement(
        vkr.Feature("CONFIG_DEBUG_INFO", "absent", "kernel-config", "REQ-01"),
        correlation_id="test:cid-absent-unrecorded",
    )
    cfg = vkr.parse_config_text("CONFIG_X86_64=y\n", "cfg")
    verdict = vkr.check_kernel(req, cfg, release="7.2.10")
    assert verdict.satisfied is False
    assert any("CONFIG_DEBUG_INFO" in r and "unrecorded" in r for r in verdict.reasons)


def test_check_kernel_architecture_mismatch():
    """Negative: configuration architecture not matching requirement generates reason."""
    req = _sample_requirement(
        vkr.Feature("CONFIG_BPF_SYSCALL", "built-in", "kernel-config", "REQ-01"),
        correlation_id="test:cid-arch",
        architectures=("x86-64",),
    )
    cfg = vkr.parse_config_text("CONFIG_ARM64=y\nCONFIG_BPF_SYSCALL=y\n", "arm_cfg")
    verdict = vkr.check_kernel(req, cfg, release="7.2.10")
    assert verdict.satisfied is False
    expected = "test:cid-arch: architecture x86-64 is listed and the configuration is for arm64"
    assert expected in verdict.reasons


def test_check_kernel_no_listed_architecture():
    """Negative: configuration without any architecture symbol names 'no listed architecture'."""
    req = _sample_requirement(
        vkr.Feature("CONFIG_BPF_SYSCALL", "built-in", "kernel-config", "REQ-01"),
        correlation_id="test:cid-no-arch",
        architectures=("x86-64",),
    )
    cfg = vkr.parse_config_text("CONFIG_BPF_SYSCALL=y\n", "no_arch")
    verdict = vkr.check_kernel(req, cfg, release="7.2.10")
    assert verdict.satisfied is False
    expected = (
        "test:cid-no-arch: architecture x86-64 is listed and the configuration is for "
        "no listed architecture"
    )
    assert expected in verdict.reasons


def test_check_kernel_release_floor_failure():
    """Negative: kernel release below minimum floor generates rejection."""
    req = _sample_requirement(
        vkr.Feature("CONFIG_BPF_SYSCALL", "built-in", "kernel-config", "REQ-01"),
        correlation_id="test:cid-floor",
        minimum_release="6.12",
    )
    cfg = vkr.parse_config_text("CONFIG_X86_64=y\nCONFIG_BPF_SYSCALL=y\n", "floor_cfg")
    verdict = vkr.check_kernel(req, cfg, release="6.11.999")
    assert verdict.satisfied is False
    expected = "test:cid-floor: kernel release 6.11.999 is below abi.minimum-release 6.12"
    assert expected in verdict.reasons


def test_check_kernel_module_abi_exact_match():
    """Positive: release matching module-abi exactly passes."""
    req = _sample_requirement(
        vkr.Feature("CONFIG_BPF_SYSCALL", "built-in", "kernel-config", "REQ-01"),
        module_abi="7.2.8-lusoris1-realtime",
    )
    cfg = vkr.parse_config_text("CONFIG_X86_64=y\nCONFIG_BPF_SYSCALL=y\n", "cfg")
    verdict = vkr.check_kernel(req, cfg, release="7.2.8-lusoris1-realtime")
    assert verdict.satisfied is True


def test_check_kernel_module_abi_mismatch_fails():
    """Negative: release differing from module-abi is rejected."""
    req = _sample_requirement(
        vkr.Feature("CONFIG_BPF_SYSCALL", "built-in", "kernel-config", "REQ-01"),
        correlation_id="test:cid-abi",
        module_abi="7.2.8-lusoris1-realtime",
    )
    cfg = vkr.parse_config_text("CONFIG_X86_64=y\nCONFIG_BPF_SYSCALL=y\n", "cfg")
    verdict = vkr.check_kernel(req, cfg, release="7.2.8-wrong-abi")
    assert verdict.satisfied is False
    expected = (
        "test:cid-abi: kernel release 7.2.8-wrong-abi is not abi.module-abi 7.2.8-lusoris1-realtime"
    )
    assert expected in verdict.reasons


def test_check_kernel_release_none_skips_abi_checks():
    """Boundary: when release is None, neither minimum-release nor module-abi is checked."""
    req = _sample_requirement(
        vkr.Feature("CONFIG_BPF_SYSCALL", "built-in", "kernel-config", "REQ-01"),
        minimum_release="6.12",
        module_abi="7.2.8-lusoris1-realtime",
    )
    cfg = vkr.parse_config_text("CONFIG_X86_64=y\nCONFIG_BPF_SYSCALL=y\n", "cfg")
    verdict = vkr.check_kernel(req, cfg, release=None)
    assert verdict.satisfied is True
    assert verdict.reasons == ()


# --- CLI tests --------------------------------------------------------------------------------


def test_cli_exit_zero_conforming(tmp_path, capsys):
    """Positive: valid requirement and conforming configuration exit 0 and print PASS."""
    req_path = tmp_path / "req.json"
    req_path.write_bytes(AEGIS_FIXTURE.read_bytes())
    # Create conforming config for aegis requirements
    req = vkr.parse_requirement(AEGIS_FIXTURE.read_bytes())
    lines = ["CONFIG_X86_64=y"]
    for f in req.features:
        if f.state == "absent":
            lines.append(f"# {f.symbol} is not set")
        else:
            lines.append(f"{f.symbol}={'m' if f.state == 'module' else 'y'}")
    cfg_path = tmp_path / "kernel.config"
    cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    code = vkr.main(
        [
            "--kernel-config",
            str(cfg_path),
            "--requirement-file",
            str(req_path),
            "--kernel-release",
            "7.2.10",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "release: 7.2.10" in captured.out
    assert f"PASS: {req.correlation_id}: {len(req.features)} features satisfied" in captured.out


def test_cli_exit_one_unsatisfied(tmp_path, capsys):
    """Negative: unsatisfied configuration exits 1, prints rejections and FAIL."""
    req_path = tmp_path / "req.json"
    req_path.write_bytes(AEGIS_FIXTURE.read_bytes())
    cfg_path = tmp_path / "kernel.config"
    # Bare config missing required symbols
    cfg_path.write_text("CONFIG_X86_64=y\n", encoding="utf-8")

    code = vkr.main(
        [
            "--kernel-config",
            str(cfg_path),
            "--requirement-file",
            str(req_path),
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "release: not given; minimum-release and module-abi not checked" in captured.out
    assert "rejected: " in captured.out
    assert "FAIL: aegis-m18-kernel-requirement-0001:" in captured.out


def test_cli_exit_two_unreadable_file(tmp_path, capsys):
    """Negative: non-existent requirement file causes exit 2."""
    code = vkr.main(
        [
            "--kernel-config",
            str(tmp_path / "nonexistent.config"),
            "--requirement-file",
            str(tmp_path / "nonexistent.json"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "FAIL:" in captured.out


def test_cli_exit_two_invalid_requirement_json(tmp_path, capsys):
    """Negative: invalid JSON or refused requirement causes exit 2."""
    req_path = tmp_path / "broken.json"
    req_path.write_text("{not valid json", encoding="utf-8")
    cfg_path = tmp_path / "kernel.config"
    cfg_path.write_text("CONFIG_X86_64=y\n", encoding="utf-8")

    code = vkr.main(
        [
            "--kernel-config",
            str(cfg_path),
            "--requirement-file",
            str(req_path),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "FAIL:" in captured.out


def test_cli_exit_two_invalid_config_syntax(tmp_path, capsys):
    """Negative: ungrammatical kernel configuration causes exit 2."""
    req_path = tmp_path / "req.json"
    req_path.write_bytes(AEGIS_FIXTURE.read_bytes())
    cfg_path = tmp_path / "broken.config"
    cfg_path.write_text("THIS IS NOT KCONFIG\n", encoding="utf-8")

    code = vkr.main(
        [
            "--kernel-config",
            str(cfg_path),
            "--requirement-file",
            str(req_path),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "FAIL:" in captured.out


def test_cli_report_json(tmp_path):
    """Positive: --report-json emits valid nucleus.kernel-check-report.v1 document."""
    req_path = tmp_path / "req.json"
    req_path.write_bytes(AEGIS_FIXTURE.read_bytes())
    cfg_path = tmp_path / "kernel.config"
    cfg_path.write_text("CONFIG_X86_64=y\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    code = vkr.main(
        [
            "--kernel-config",
            str(cfg_path),
            "--requirement-file",
            str(req_path),
            "--kernel-release",
            "7.2.10",
            "--report-json",
            str(report_path),
        ]
    )
    assert code == 1
    assert report_path.is_file()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["schema"] == "nucleus.kernel-check-report.v1"
    assert data["correlation-id"] == "aegis-m18-kernel-requirement-0001"
    assert data["release"] == "7.2.10"
    assert data["satisfied"] is False
    assert isinstance(data["checks"], list)
    assert len(data["checks"]) > 0
    assert isinstance(data["reasons"], list)
    assert len(data["reasons"]) > 0


def test_cli_mutually_exclusive_flags():
    """Negative: mutual exclusion errors on invalid flag combinations."""
    with pytest.raises(SystemExit):
        vkr.main(["--kernel-config", "foo.config", "--requirement", "label=req.json"])
    with pytest.raises(SystemExit):
        vkr.main(["--kernel-config", "foo.config"])  # missing --requirement-file


# --- Conformance vectors runner ---------------------------------------------------------------


def test_shared_conformance_vectors():
    """Positive, negative and boundary conformance vectors validation."""
    vector_files = sorted(VECTORS_DIR.glob("*.json"))
    assert len(vector_files) >= 8, f"Expected at least 8 vectors, found {len(vector_files)}"
    for vec_path in vector_files:
        payload = json.loads(vec_path.read_text(encoding="utf-8"))
        req_bytes = json.dumps(payload["requirement"]).encode("utf-8")
        req = vkr.parse_requirement(req_bytes)
        cfg = vkr.parse_config_text(payload["config"], vec_path.name)
        verdict = vkr.check_kernel(req, cfg, release=payload.get("release"))
        assert verdict.satisfied == payload["satisfied"], (
            f"Vector {vec_path.name}: expected satisfied={payload['satisfied']}, "
            f"got {verdict.satisfied}, reasons: {verdict.reasons}"
        )
        for expected_str in payload.get("reasons_contain", []):
            assert any(expected_str in r for r in verdict.reasons), (
                f"Vector {vec_path.name}: expected substring {expected_str!r} in reasons: "
                f"{verdict.reasons}"
            )
