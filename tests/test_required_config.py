"""kconfig/required-config.json and scripts/check_required_config.py.

The list names what the documentation claims each stream builds; the checker refuses a
configuration in which a claimed option is off. Every leg's declared fragments must meet the
list, the list must cover every line of kconfig/k8s-node.config, and each way a configuration
or the list can be wrong must be refused.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KCONFIG = REPO_ROOT / "kconfig"
REQUIRED = KCONFIG / "required-config.json"
SCRIPT = REPO_ROOT / "scripts" / "check_required_config.py"
VERSIONS = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_required_config as crc  # noqa: E402
import verify_kernel_requirement as vkr  # noqa: E402

LEGS = [(s, a) for s in VERSIONS["streams"] for a in VERSIONS["architectures"]]


def _run(config: Path, stream: str, arch: str, required: Path = REQUIRED):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(config),
            "--stream",
            stream,
            "--arch",
            arch,
            "--required",
            str(required),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _declared(tmp_path: Path, stream: str, arch: str, drop: str | None = None) -> Path:
    config = vkr.declared_config(KCONFIG, arch, stream)
    if drop is not None:
        config.pop(drop)
    path = tmp_path / f"{stream}-{arch}.config"
    path.write_text("".join(f"{k}={v}\n" for k, v in sorted(config.items())), encoding="utf-8")
    return path


@pytest.mark.parametrize(("stream", "arch"), LEGS)
def test_every_leg_declares_what_is_claimed_for_it(tmp_path, stream, arch):
    result = _run(_declared(tmp_path, stream, arch), stream, arch)
    assert result.returncode == 0, result.stderr


def test_the_list_covers_every_line_of_the_k8s_node_fragment():
    claims = {c.ident: c for c in crc.load_claims(REQUIRED)}
    fragment = vkr.read_config(KCONFIG / "k8s-node.config")
    legacy = claims["k8s-node-iptables-legacy"]
    assert set(fragment) == set(claims["k8s-node"].symbols)
    assert not (set(claims["k8s-node"].symbols) & set(legacy.symbols))
    assert claims["k8s-node"].streams == tuple(VERSIONS["streams"])
    assert claims["k8s-node"].architectures == tuple(VERSIONS["architectures"])
    assert legacy.streams == ("bleeding", "mainstream", "lts")
    assert legacy.architectures == tuple(VERSIONS["architectures"])
    bbr3 = claims["bbr3"]
    for stream in ("bleeding", "mainstream", "lts"):
        stream_cfg = vkr.read_config(KCONFIG / "streams" / f"{stream}.config")
        carried = set(bbr3.symbols) if stream in bbr3.streams else set()
        assert set(stream_cfg) == set(legacy.symbols) | carried
    realtime = vkr.read_config(KCONFIG / "streams" / "realtime.config")
    for symbol in legacy.symbols:
        assert symbol not in realtime


def test_xe2_is_claimed_on_every_stream_and_rocm_on_mainstream():
    claims = {c.ident: c for c in crc.load_claims(REQUIRED)}
    xe2 = claims["xe2"]
    assert xe2.streams == tuple(VERSIONS["streams"]) and xe2.architectures == ("x86_64",)
    assert xe2.symbols["CONFIG_DRM_XE"] == "built"
    assert xe2.symbols["CONFIG_INTEL_MEI"] == "built"
    assert xe2.symbols["CONFIG_INTEL_MEI_ME"] == "built"
    assert xe2.symbols["CONFIG_INTEL_MEI_GSC"] == "built"
    assert xe2.symbols["CONFIG_INTEL_MEI_GSC_PROXY"] == "built"
    assert xe2.symbols["CONFIG_INTEL_MEI_LB"] == "built"
    assert xe2.symbols["CONFIG_MTD_INTEL_DG"] == "built"
    amd = claims["amd-rocm"]
    assert amd.streams == ("mainstream",) and amd.architectures == ("x86_64",)
    assert amd.symbols["CONFIG_DRM_AMDGPU"] == "built"
    assert amd.symbols["CONFIG_HSA_AMD"] == "y"


@pytest.mark.parametrize("stream", VERSIONS["streams"])
def test_every_x86_64_leg_declares_the_xe2_claim(tmp_path, stream):
    claims = {c.ident: c for c in crc.load_claims(REQUIRED)}
    xe2 = claims["xe2"]
    assert stream in xe2.streams
    assert "x86_64" in xe2.architectures
    config = vkr.read_config(_declared(tmp_path, stream, "x86_64"))
    assert not crc.misses([xe2], config, stream, "x86_64")


@pytest.mark.parametrize(
    ("stream", "arch", "drop"),
    [
        ("mainstream", "x86_64", "CONFIG_DRM_XE"),
        ("lts", "x86_64", "CONFIG_DRM_XE"),
        ("realtime", "x86_64", "CONFIG_INTEL_MEI_GSC"),
        ("mainstream", "x86_64", "CONFIG_NETFILTER_XTABLES_LEGACY"),
        ("lts", "arm64", "CONFIG_OVERLAY_FS"),
        ("bleeding", "riscv64", "CONFIG_NF_TABLES"),
        ("realtime", "x86_64", "CONFIG_PREEMPT_RT"),
        ("bleeding", "riscv64", "CONFIG_SCHED_CLASS_EXT"),
        ("lts", "x86_64", "CONFIG_SCHED_CLASS_EXT"),
        ("mainstream", "x86_64", "CONFIG_TCP_CONG_BBR3"),
        ("realtime", "arm64", "CONFIG_TCP_CONG_BBR3"),
    ],
)
def test_a_claimed_option_that_is_off_is_refused(tmp_path, stream, arch, drop):
    result = _run(_declared(tmp_path, stream, arch, drop=drop), stream, arch)
    assert result.returncode == 1
    assert f"{drop} unrecorded" in result.stderr


def test_the_bbr3_claim_covers_exactly_the_streams_the_series_is_carried_for():
    doc = json.loads(REQUIRED.read_text(encoding="utf-8"))
    claim = next(c for c in doc["claims"] if c["id"] == "bbr3")
    versions = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
    assert sorted(claim["streams"]) == sorted(versions["patches"]["bbr3"]["streams"])
    assert claim["symbols"] == {"CONFIG_TCP_CONG_BBR3": "built"}


def test_realtime_refuses_legacy_iptables_claim_if_planted(tmp_path):
    def plant_realtime(doc):
        for claim in doc["claims"]:
            if claim["id"] == "k8s-node-iptables-legacy":
                claim["streams"] = ["bleeding", "mainstream", "lts", "realtime"]
                break

    config = _declared(tmp_path, "realtime", "x86_64")
    result = _run(config, "realtime", "x86_64", required=_edit_list(tmp_path, plant_realtime))
    assert result.returncode == 1
    assert "CONFIG_NETFILTER_XTABLES_LEGACY unrecorded" in result.stderr


def test_a_module_where_built_in_is_claimed_is_refused(tmp_path):
    path = _declared(tmp_path, "realtime", "arm64")
    text = path.read_text(encoding="utf-8").replace("CONFIG_PREEMPT_RT=y", "CONFIG_PREEMPT_RT=m")
    path.write_text(text, encoding="utf-8")
    result = _run(path, "realtime", "arm64")
    assert result.returncode == 1
    assert "CONFIG_PREEMPT_RT=m, needs =y" in result.stderr


@pytest.mark.parametrize(
    ("stream", "arch", "symbol"),
    [
        ("mainstream", "x86_64", "CONFIG_DRM_XE"),
        ("lts", "x86_64", "CONFIG_DRM_XE"),
    ],
)
def test_an_unset_line_does_not_meet_a_claim(tmp_path, stream, arch, symbol):
    path = _declared(tmp_path, stream, arch)
    text = path.read_text(encoding="utf-8").replace(f"{symbol}=m", f"# {symbol} is not set")
    path.write_text(text, encoding="utf-8")
    result = _run(path, stream, arch)
    assert result.returncode == 1
    assert f"{symbol} is not set" in result.stderr


def _edit_list(tmp_path: Path, edit) -> Path:
    document = json.loads(REQUIRED.read_text(encoding="utf-8"))
    edit(document)
    path = tmp_path / "required.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.update(schema="other"),
        lambda d: d.update(claims=[]),
        lambda d: d["claims"][0].update(streams=["stable"]),
        lambda d: d["claims"][0].update(architectures=["mips"]),
        lambda d: d["claims"][0]["symbols"].update(CONFIG_VETH="module"),
        lambda d: d["claims"][0]["symbols"].update(VETH="built"),
        lambda d: d["claims"][0].update(extra=1),
        lambda d: d["claims"].append(dict(d["claims"][0])),
    ],
    ids=["schema", "empty", "stream", "arch", "value", "symbol", "key", "duplicate"],
)
def test_a_malformed_list_is_refused(tmp_path, edit):
    config = _declared(tmp_path, "mainstream", "x86_64")
    result = _run(config, "mainstream", "x86_64", required=_edit_list(tmp_path, edit))
    assert result.returncode == 2
    assert result.stderr.startswith("required-config:")


def test_a_leg_no_claim_covers_is_refused(tmp_path):
    config = _declared(tmp_path, "mainstream", "x86_64")
    result = _run(config, "stable", "x86_64")
    assert result.returncode == 2


def test_merge_config_runs_the_check_after_the_survival_check():
    script = (REPO_ROOT / "scripts" / "merge-config.sh").read_text(encoding="utf-8")
    survival = script.index("python3 scripts/kconfig_survival.py --config")
    required = script.index("python3 scripts/check_required_config.py --config")
    copy = script.index('cp "${BUILD_DIR}/.config" "${OUTPUT_FILE}"')
    assert survival < required < copy
