"""Tests for scripts/verify_kernel_requirement.py, the downstream kernel requirement gate.

Fixtures under tests/fixtures/kernel-requirement are byte-identical copies of the live
documents: imago.json is cordanaLLM/imago kernel/requirement.json at 16f964b4dafa, and
aegis-os.json is cordanaLLM/Aegis-OS build/kernel-requirement.json at 54c710c (unchanged
since 5148ab2). The workflow verifies the live documents; these pin the parser, the
evaluation and the policy of docs/adr/0007.

Tests marked "Aegis" port a vector of Aegis-OS crates/aegis-fabrica-defs/tests/
kernel_requirement.rs, the contract owner's own tests. Stream names and versions in the
decide() tests are synthetic; the tests that touch real streams read them from versions.json.
"""

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "kernel-requirement"
KCONFIG = REPO_ROOT / "kconfig"
VERSIONS = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
ROWS = {row["label"]: row for row in VERSIONS["downstream"]["requirements"]}
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import verify_kernel_requirement as vkr  # noqa: E402
import versions_query as vq  # noqa: E402

CID = "test:decide-0001"


def _fixture(name: str = "aegis-os.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _raw(doc: dict) -> bytes:
    return json.dumps(doc).encode("utf-8")


def _altered(field: str, value: object, name: str = "aegis-os.json") -> bytes:
    """The fixture with one top-level field replaced (Aegis: altered)."""
    doc = _fixture(name)
    doc[field] = value
    return _raw(doc)


def _row(symbol: str, state: str = "built-in", required_by: str = "REQ-P07-01") -> dict:
    return {"symbol": symbol, "state": state, "probe": "kernel-config", "required-by": required_by}


def _refusal(raw: bytes) -> vkr.RequirementError:
    with pytest.raises(vkr.RequirementError) as info:
        vkr.parse_requirement(raw)
    return info.value


def _feature(symbol: str, state: str = "built-in", probe: str = "kernel-config") -> vkr.Feature:
    return vkr.Feature(symbol, state, probe, "REQ-TEST-01")


def _requirement(*features: vkr.Feature, **abi: str) -> vkr.Requirement:
    architectures = abi.pop("architectures", ("x86-64",))
    return vkr.Requirement(
        correlation_id=CID,
        architectures=tuple(architectures),
        abi=vkr.Abi(
            minimum_release=abi.get("minimum", "6.12"),
            target_release=abi.get("target"),
            module_abi=abi.get("module_abi"),
        ),
        features=features,
    )


def _stream(req: vkr.Requirement, name: str, version: str, bound: bool, **configs: dict):
    """One stream's result; configs are keyed by the architecture this forge builds."""
    return vkr.stream_result(req, name, version, bound, configs)


# --- the documents both consumers publish ------------------------------------------------


@pytest.mark.parametrize("name", ["imago.json", "aegis-os.json"])
def test_live_documents_decode(name):
    requirement = vkr.parse_requirement((FIXTURES / name).read_bytes())
    assert requirement.correlation_id
    assert requirement.architectures == ("x86-64",)


@pytest.mark.parametrize("label", sorted(ROWS))
def test_live_documents_pass_on_the_streams_versions_json_binds(label):
    raw = (FIXTURES / f"{label}.json").read_bytes()
    row = ROWS[label]
    source = vkr.Source(label, row["repository"], row["path"], None, None)
    outcome = vkr.verify_document(source, raw, row["streams"], VERSIONS, KCONFIG)
    assert outcome.status == "PASS", outcome.verdict.reasons
    assert set(outcome.verdict.held) == set(row["streams"])


def test_a_bound_stream_records_the_state_of_every_symbol():
    """Aegis-OS#151 positive: every feature satisfied, and each symbol's state recorded."""
    raw = (FIXTURES / "aegis-os.json").read_bytes()
    row = ROWS["aegis-os"]
    source = vkr.Source("aegis-os", row["repository"], row["path"], None, None)
    outcome = vkr.verify_document(source, raw, row["streams"], VERSIONS, KCONFIG)
    report = vkr.outcome_json(outcome)
    for stream in (s for s in report["streams"] if s["bound"]):
        (arch,) = stream["architectures"]
        symbols = [feature["symbol"] for feature in arch["features"]]
        assert symbols == [f["symbol"] for f in _fixture()["features"]]
        assert all(f["observed"] in ("y", "m") and f["satisfied"] for f in arch["features"])


# --- decoding follows the owner (Aegis-OS crates/aegis-fabrica-defs) ---------------------


def test_the_reviewed_requirement_decodes_as_the_owner_reads_it():
    """Aegis: the_reviewed_requirement_lists_the_features_p06_p07_and_p13_need."""
    requirement = vkr.parse_requirement((FIXTURES / "aegis-os.json").read_bytes())
    assert requirement.abi == vkr.Abi("6.12", "7.3", None)
    symbols = {feature.symbol for feature in requirement.features}
    for expected in ("CONFIG_PREEMPT_RT", "CONFIG_BPF_LSM", "CONFIG_DEBUG_INFO_BTF", "CONFIG_KVM"):
        assert expected in symbols


def test_an_artifact_expectation_is_accepted_and_echoed_unverified():
    """Aegis: a_requirement_with_an_artifact_expectation_round_trips."""
    raw = _altered("artifact", {"digest": "a" * 64, "signature": "beef"})
    requirement = vkr.parse_requirement(raw)
    assert requirement.artifact == vkr.Artifact("a" * 64, "beef")
    echoed = vkr._requirement_json(requirement)["artifact"]
    assert echoed == {"digest": "a" * 64, "signature": "beef", "verified": False}


def test_an_abi_with_only_a_minimum_release_decodes():
    """Aegis: an_abi_with_only_a_minimum_release_decodes."""
    requirement = vkr.parse_requirement(_altered("abi", {"minimum-release": "6.12"}))
    assert requirement.abi == vkr.Abi("6.12", None, None)


def test_null_optional_fields_read_as_absent():
    doc = _fixture()
    doc["abi"] = {"minimum-release": "6.12", "target-release": None, "module-abi": None}
    doc["artifact"] = None
    requirement = vkr.parse_requirement(_raw(doc))
    assert requirement.abi == vkr.Abi("6.12", None, None)
    assert requirement.artifact is None
    raw = _altered("artifact", {"digest": "b" * 64, "signature": None})
    assert vkr.parse_requirement(raw).artifact == vkr.Artifact("b" * 64, None)


def test_every_state_and_a_colon_in_the_correlation_id_decode():
    features = [_row(f"CONFIG_S{index}", state) for index, state in enumerate(vkr.STATES)]
    doc = _fixture()
    doc.update({"features": features, "correlation-id": "aegis:m18.kernel_requirement-1"})
    requirement = vkr.parse_requirement(_raw(doc))
    assert [feature.state for feature in requirement.features] == list(vkr.STATES)
    assert requirement.correlation_id == "aegis:m18.kernel_requirement-1"


def test_a_repeated_architecture_is_accepted_like_the_owner_and_evaluated_once():
    """The owner refuses a repeated symbol, not a repeated architecture (kernel.rs:604-640)."""
    requirement = vkr.parse_requirement(_altered("architectures", ["x86-64"] * 4))
    result = _stream(requirement, "alpha", "7.2", True, x86_64={})
    assert [arch.token for arch in result.architectures] == ["x86-64"]


def test_required_by_accepts_imago_flavor_ids_the_one_divergence():
    """Aegis demands REQ-; imago's live document uses FLAVOR-* (docs/adr/0007, open question)."""
    doc = _fixture()
    doc["features"] = [_row("CONFIG_A", required_by="FLAVOR-BASE"), _row("CONFIG_B")]
    assert vkr.parse_requirement(_raw(doc)).features[0].required_by == "FLAVOR-BASE"
    for refused in ("flavor-base", "-REQ", "REQ_P07", "REQ-", "R" * 129):
        doc["features"] = [_row("CONFIG_A", required_by=refused)]
        assert _refusal(_raw(doc)).kind == "Malformed", refused


def test_the_feature_and_architecture_bounds_are_exact():
    """Aegis: the_feature_and_architecture_bounds_are_exact."""
    rows = [_row(f"CONFIG_BOUND_{index}", "present") for index in range(vkr.MAX_FEATURES)]
    assert len(vkr.parse_requirement(_altered("features", rows)).features) == vkr.MAX_FEATURES
    over = _altered("features", [*rows, _row("CONFIG_ONE_TOO_MANY", "present")])
    assert _refusal(over).kind == "TooMany"
    arches = ["arm64"] * vkr.MAX_ARCHITECTURES
    assert vkr.parse_requirement(_altered("architectures", arches))
    assert _refusal(_altered("architectures", [*arches, "arm64"])).kind == "TooMany"


def test_the_byte_bound_is_exact_and_checked_before_parsing():
    """Aegis: a_payload_past_the_byte_bound_is_refused_before_it_is_parsed."""
    assert _refusal(b"x" * (vkr.MAX_PAYLOAD_BYTES + 1)).kind == "TooLong"
    raw = (FIXTURES / "aegis-os.json").read_bytes()
    padded = raw + b" " * (vkr.MAX_PAYLOAD_BYTES - len(raw))
    assert len(padded) == vkr.MAX_PAYLOAD_BYTES
    assert vkr.parse_requirement(padded)


# --- refusals, by the kind the owner names -----------------------------------------------


@pytest.mark.parametrize("architecture", ["riscv64", "x86_64", "X86-64", ""])
def test_an_architecture_outside_the_owner_enum_is_refused(architecture):
    """Aegis: an_unknown_architecture_is_refused (x86_64 and riscv64 do not decode)."""
    assert _refusal(_altered("architectures", [architecture])).kind == "Malformed"


def test_another_contract_version_is_refused_as_an_unknown_version():
    """Aegis: a_payload_naming_another_contract_version_is_refused_as_an_unknown_version."""
    refused = _refusal(_altered("schema", "aegis.p01-nucleus.kernel-requirement.v2"))
    assert refused.kind == "UnknownVersion"
    assert "kernel-requirement.v2" in refused.message
    assert vkr.SCHEMA in refused.message


def test_a_missing_or_unreadable_schema_is_malformed_not_an_unknown_version():
    doc = _fixture()
    del doc["schema"]
    assert _refusal(_raw(doc)).kind == "Malformed"
    assert _refusal(_altered("schema", 1)).kind == "Malformed"
    assert _refusal(b"[]").kind == "Malformed"


def _repeated(field: str, schema: str) -> bytes:
    """The fixture under ``schema``, with ``field`` of the top-level object written twice."""
    doc = _fixture()
    doc["schema"] = schema
    text = json.dumps(doc)
    head, sep, tail = text.partition(f'"{field}": ')
    assert sep, field
    return f'{head}"{field}": {json.dumps(doc[field])}, {sep}{tail}'.encode()


def test_another_version_with_a_repeated_field_is_still_an_unknown_version():
    """payload.rs declared_schema: the lenient peek skips every field but schema, repeats too."""
    foreign = "aegis.p01-nucleus.kernel-requirement.v2"
    for field in ("correlation-id", "abi"):
        assert _refusal(_repeated(field, foreign)).kind == "UnknownVersion", field
        assert _refusal(_repeated(field, vkr.SCHEMA)).kind == "Malformed", field
    nested = _fixture()
    nested["schema"] = foreign
    raw = json.dumps(nested).replace('"abi": {', '"abi": {"target-release": "7.3", ', 1)
    assert _refusal(raw.encode()).kind == "UnknownVersion"
    # A repeated schema claims no version: the owner's peek refuses it as malformed.
    assert _refusal(_repeated("schema", foreign)).kind == "Malformed"


def test_the_array_form_is_refused_where_the_owner_decodes_it():
    """A known divergence, refused here: serde's derived visit_seq takes the fields in order."""
    assert _refusal(_raw(list(_fixture().values()))).kind == "Malformed"
    assert _refusal(b'["aegis.p01-nucleus.kernel-requirement.v2"]').kind == "Malformed"


def test_a_signature_without_a_digest_is_refused():
    """Aegis: a_signature_without_a_digest_is_refused."""
    assert _refusal(_altered("artifact", {"signature": "beef"})).kind == "Malformed"


def test_a_repeated_symbol_is_refused():
    """Aegis: a_repeated_symbol_is_refused."""
    doc = _fixture()
    doc["features"].append(doc["features"][0])
    refused = _refusal(_raw(doc))
    assert refused.kind == "DuplicateSymbol"
    assert doc["features"][0]["symbol"] in refused.message


def test_a_target_release_older_than_the_minimum_is_refused():
    """Aegis: a_target_release_older_than_the_minimum_is_refused."""
    refused = _refusal(_altered("abi", {"minimum-release": "7.3", "target-release": "6.12"}))
    assert refused.kind == "InvertedRelease"
    assert "7.3" in refused.message and "6.12" in refused.message


@pytest.mark.parametrize(
    ("where", "value"),
    [
        ("top", {"patches": []}),
        ("abi", {"ceiling": "7.0"}),
        ("feature", {"note": "x"}),
        ("artifact", {"algorithm": "sha256"}),
    ],
)
def test_an_unknown_field_is_refused_at_every_level(where, value):
    """Aegis: an_unknown_field_is_refused_rather_than_ignored (deny_unknown_fields)."""
    doc = _fixture()
    if where == "top":
        target = doc
    elif where == "abi":
        target = doc["abi"]
    elif where == "feature":
        target = doc["features"][0]
    else:
        target = doc["artifact"] = {"digest": "c" * 64}
    target.update(value)
    assert _refusal(_raw(doc)).kind == "Malformed"


def test_an_empty_requirement_list_is_refused_explicitly():
    """Aegis: an_empty_requirement_list_is_refused_explicitly (Aegis-OS#50 boundary)."""
    assert _refusal(_altered("features", [])).kind == "NoFeatures"
    assert _refusal(_altered("architectures", [])).kind == "NoArchitectures"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "KVM"),
        ("symbol", "CONFIG_"),
        ("symbol", "CONFIG_kvm"),
        ("symbol", "CONFIG_KVM\n"),
        ("symbol", "CONFIG_" + "A" * 58),
        ("state", "yes"),
        ("state", ["built-in"]),
        ("state", {"built-in": True}),
        ("probe", "dmesg"),
        ("probe", 5),
        ("required-by", ""),
    ],
)
def test_a_malformed_feature_row_is_refused_not_crashing(field, value):
    doc = _fixture()
    doc["features"][0][field] = value
    assert _refusal(_raw(doc)).kind == "Malformed"


def test_the_symbol_bound_admits_exactly_64_bytes():
    doc = _fixture()
    doc["features"][0]["symbol"] = "CONFIG_" + "A" * 57
    assert len(vkr.parse_requirement(_raw(doc)).features[0].symbol) == vkr.MAX_SYMBOL_BYTES


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("correlation-id", "has spaces"),
        ("correlation-id", ""),
        ("correlation-id", "c" * 129),
        ("abi", {"minimum-release": "six"}),
        ("abi", {"minimum-release": "v6.12"}),
        ("abi", {"minimum-release": "6.12 "}),
        ("abi", {"minimum-release": "6" * 65}),
        ("abi", {"minimum-release": "6.12", "target-release": ""}),
        ("abi", {"minimum-release": "6.12", "module-abi": 7}),
        ("artifact", {"digest": "A" * 64}),
        ("artifact", {"digest": "a" * 63}),
        ("artifact", {"digest": "a" * 64, "signature": "abc"}),
        ("artifact", {"digest": "a" * 64, "signature": "B0"}),
        ("artifact", {"digest": "a" * 64, "signature": ""}),
        ("artifact", {"digest": "a" * 64, "signature": "ab" * 129}),
    ],
)
def test_field_encodings_are_refused_like_the_owner(field, value):
    """field.rs: every field is non-empty, bounded and in its shape, or the document is refused."""
    assert _refusal(_altered(field, value)).kind == "Malformed"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema": "a", "schema": "aegis.p01-nucleus.kernel-requirement.v1"}',
        b'{"schema": NaN}',
        (FIXTURES / "imago.json").read_bytes() + b"{}",
        b"\xff\xfe",
        b"[" * 5000 + b"]" * 5000,
    ],
    ids=["duplicate-key", "nan", "trailing-data", "not-utf8", "nesting"],
)
def test_input_the_owner_decoder_refuses_is_malformed(raw):
    assert _refusal(raw).kind == "Malformed"


def test_a_dispatched_digest_is_proven_before_the_bytes_are_parsed():
    raw = (FIXTURES / "imago.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert vkr.parse_requirement(raw, digest)
    with pytest.raises(vkr.RequirementError) as info:
        vkr.parse_requirement(b"not json at all", digest)
    assert info.value.kind == "DigestMismatch"


# --- releases: field.rs KernelRelease::at_least ------------------------------------------


@pytest.mark.parametrize(
    ("release", "floor", "expected"),
    [
        ("7.3", "7.2.9", True),
        ("7.3", "7.3.1", False),
        ("7.3.1", "7.3", True),
        ("7.3-rc2", "7.3.0", True),
        ("7.3.0", "7.3-rc2", True),
        ("6.6-rt", "6.6", True),
        ("6.12", "6.12", True),
        ("6.11.99", "6.12", False),
        ("6.9.4-1-cachyos", "6.9.4", True),
        ("5.15.10", "5.15.11", False),
    ],
)
def test_at_least_compares_like_the_owner(release, floor, expected):
    """Leading numeric components, missing ones zero, suffix ignored: 7.3-rc2 reads 7.3."""
    assert vkr.at_least(release, floor) is expected


@pytest.mark.parametrize(
    ("release", "components"),
    [
        ("6.9.4-1-cachyos", (6, 9, 4)),
        ("7.3-rc2", (7, 3)),
        ("7..3", (7,)),
        ("1.2.3.4.5.6.7.8.9", (1, 2, 3, 4, 5, 6, 7, 8)),
        ("9" * 30, ((1 << 64) - 1,)),
    ],
)
def test_release_components_are_bounded_like_the_owner(release, components):
    assert vkr.release_components(release) == components


@pytest.mark.parametrize("stream", sorted(VERSIONS["streams"]))
def test_every_published_stream_version_has_a_comparable_release(stream):
    assert vkr.release_components(VERSIONS["streams"][stream]["version"])


# --- states: kernel.rs RequiredState::satisfied_by ---------------------------------------


@pytest.mark.parametrize(
    ("required", "admits"),
    [
        ("built-in", {"y"}),
        ("module", {"m"}),
        ("present", {"y", "m"}),
        ("absent", {"n"}),
    ],
)
def test_each_required_state_is_satisfied_by_exactly_the_states_it_names(required, admits):
    """Aegis: each_required_state_is_satisfied_by_exactly_the_states_it_names."""
    for observed in ("y", "m", "n", "512", '"bpf"', None):
        assert vkr.state_satisfied(required, observed) is (observed in admits), observed


def test_kvm_guest_does_not_answer_for_kvm():
    (check,) = vkr.check_features([_feature("CONFIG_KVM", "module")], {"CONFIG_KVM_GUEST": "y"})
    assert (check.observed, check.satisfied) == (None, False)


# --- configurations: the merge scripts/merge-config.sh performs --------------------------


def _kconfig(tmp_path: Path, **fragments: str) -> Path:
    for name, body in fragments.items():
        path = tmp_path / (name.replace("__", "/") + ".config")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def test_merge_order_is_baseline_then_architecture_then_stream(tmp_path):
    kdir = _kconfig(
        tmp_path,
        **{
            "security-hardened": "CONFIG_A=y\nCONFIG_B=y\n",
            "x86_64": "CONFIG_B=m\nCONFIG_C=y\n",
            "streams__alpha": "CONFIG_C=m\n",
        },
    )
    merged = vkr.declared_config(kdir, "x86_64", "alpha")
    assert merged == {"CONFIG_A": "y", "CONFIG_B": "m", "CONFIG_C": "m"}
    assert vkr.declared_config(kdir, "x86_64", "beta")["CONFIG_C"] == "y"


def test_an_unset_in_a_later_fragment_overrides_an_earlier_assignment(tmp_path):
    kdir = _kconfig(
        tmp_path,
        **{"security-hardened": "CONFIG_A=y\n", "x86_64": "  # CONFIG_A is not set  \n"},
    )
    assert vkr.declared_config(kdir, "x86_64", "alpha") == {"CONFIG_A": "n"}


def test_only_the_kconfig_unset_form_unsets(tmp_path):
    """Kconfig reads "#CONFIG_A is not set" (no space) as a comment, and so does the merge."""
    kdir = _kconfig(
        tmp_path, **{"security-hardened": "CONFIG_A=y\n", "x86_64": "#CONFIG_A is not set\n"}
    )
    assert vkr.declared_config(kdir, "x86_64", "alpha") == {"CONFIG_A": "y"}


def test_a_line_outside_the_fragment_grammar_is_refused(tmp_path):
    kdir = _kconfig(tmp_path, **{"security-hardened": "", "x86_64": "CONFIG-A=y\n"})
    with pytest.raises(vkr.KconfigError, match="x86_64.config:1"):
        vkr.declared_config(kdir, "x86_64", "alpha")


_BASH = shutil.which("bash")
_needs_bash = pytest.mark.skipif(_BASH is None, reason="bash runs scripts/merge-config.sh")


def _merge(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_BASH, "scripts/merge-config.sh", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@_needs_bash
@pytest.mark.parametrize("stream", sorted(VERSIONS["streams"]))
@pytest.mark.parametrize("arch", VERSIONS["architectures"])
def test_declared_config_matches_merge_config_sh(tmp_path, arch, stream):
    out = tmp_path / "merged.config"
    result = _merge(f"--arch={arch}", f"--stream={stream}", f"--output={out}")
    assert result.returncode == 0, result.stderr
    assert vkr.read_config(out) == vkr.declared_config(KCONFIG, arch, stream)


@_needs_bash
def test_merge_config_output_is_reproducible(tmp_path):
    first, second = tmp_path / "a.config", tmp_path / "b.config"
    for out in (first, second):
        assert _merge("--arch=x86_64", "--stream=realtime", f"--output={out}").returncode == 0
    assert first.read_bytes() == second.read_bytes()
    assert "Timestamp" not in first.read_text(encoding="utf-8")


@_needs_bash
def test_merge_config_keeps_unsets_and_refuses_what_is_not_kconfig(tmp_path):
    extra, out = tmp_path / "extra.config", tmp_path / "merged.config"
    extra.write_text("# CONFIG_KVM is not set\n", encoding="utf-8")
    assert _merge("--arch=x86_64", f"--output={out}", str(extra)).returncode == 0
    assert "# CONFIG_KVM is not set" in out.read_text(encoding="utf-8").splitlines()
    extra.write_text("CONFIG-KVM=m\n", encoding="utf-8")
    out.unlink()
    result = _merge("--arch=x86_64", f"--output={out}", str(extra))
    assert result.returncode != 0 and "CONFIG-KVM=m" in result.stderr
    assert not out.exists()


# --- decide(): the policy of docs/adr/0007 ------------------------------------------------

KVM_MODULE = _feature("CONFIG_KVM", "module")
BTF = _feature("CONFIG_DEBUG_INFO_BTF", "built-in", "btf-vmlinux")
MET = {"CONFIG_KVM": "m", "CONFIG_DEBUG_INFO_BTF": "y"}


def test_mixed_streams_pass_when_every_bound_stream_holds():
    req = _requirement(KVM_MODULE, BTF)
    results = [
        _stream(req, "alpha", "7.2", True, x86_64=MET),
        _stream(req, "beta", "7.2", False, x86_64={"CONFIG_KVM": "m"}),
    ]
    verdict = vkr.decide(req, results)
    assert (verdict.satisfied, verdict.held, verdict.reasons) == (True, ("alpha",), ())


def test_one_failing_bound_stream_fails_the_document_and_is_named():
    req = _requirement(KVM_MODULE, BTF)
    results = [
        _stream(req, "alpha", "7.2", True, x86_64=MET),
        _stream(req, "beta", "7.2", True, x86_64={"CONFIG_KVM": "m"}),
    ]
    verdict = vkr.decide(req, results)
    assert not verdict.satisfied
    assert verdict.held == ("alpha",)
    (reason,) = verdict.reasons
    assert reason.startswith(f"{CID}: beta x86_64: CONFIG_DEBUG_INFO_BTF")


def test_a_bound_stream_below_the_floor_fails():
    req = _requirement(KVM_MODULE, minimum="6.12")
    verdict = vkr.decide(req, [_stream(req, "alpha", "6.11.9", True, x86_64=MET)])
    assert not verdict.satisfied and verdict.held == ()
    assert verdict.reasons == (f"{CID}: alpha: release 6.11.9 is below minimum-release 6.12",)


def test_a_release_exactly_at_the_floor_is_accepted():
    """Aegis-OS#150 boundary: exactly abi.minimum-release is accepted, below it refused."""
    req = _requirement(KVM_MODULE, minimum="6.12")
    assert vkr.decide(req, [_stream(req, "alpha", "6.12", True, x86_64=MET)]).satisfied
    assert not vkr.decide(req, [_stream(req, "alpha", "6.11", True, x86_64=MET)]).satisfied


def test_a_release_candidate_meets_the_floor_of_its_release():
    req = _requirement(KVM_MODULE, minimum="7.3.0")
    assert vkr.decide(req, [_stream(req, "alpha", "7.3-rc2", True, x86_64=MET)]).satisfied


def test_target_release_never_filters_a_stream():
    req = _requirement(KVM_MODULE, minimum="6.12", target="7.3")
    results = [
        _stream(req, "older", "6.18", True, x86_64=MET),
        _stream(req, "newer", "7.4", True, x86_64=MET),
    ]
    assert vkr.decide(req, results).held == ("older", "newer")


def test_a_module_is_satisfied_by_m_and_built_in_is_not():
    """Aegis-OS#151 boundary: module is satisfied by m; built-in is not satisfied by m."""
    req = _requirement(KVM_MODULE, _feature("CONFIG_POWERCAP"))
    config = {"CONFIG_KVM": "m", "CONFIG_POWERCAP": "m"}
    (reason,) = vkr.decide(req, [_stream(req, "alpha", "7.2", True, x86_64=config)]).reasons
    assert "CONFIG_POWERCAP (required-by REQ-TEST-01) requires built-in, observed =m" in reason


def test_a_required_symbol_set_to_n_is_rejected_with_the_correlation_id():
    """Aegis-OS#151 negative: the correlation-id and the symbol are named."""
    req = _requirement(KVM_MODULE)
    verdict = vkr.decide(req, [_stream(req, "alpha", "7.2", True, x86_64={"CONFIG_KVM": "n"})])
    assert not verdict.satisfied
    assert verdict.reasons == (
        f"{CID}: alpha x86_64: CONFIG_KVM (required-by REQ-TEST-01) requires module, "
        "observed =n (not set)",
    )


def test_absent_is_satisfied_only_by_an_explicit_unset():
    req = _requirement(_feature("CONFIG_PREEMPT_RT", "absent"))
    for config, expected in (
        ({"CONFIG_PREEMPT_RT": "n"}, True),
        ({}, False),
        ({"CONFIG_PREEMPT_RT": "y"}, False),
    ):
        verdict = vkr.decide(req, [_stream(req, "alpha", "7.2", True, x86_64=config)])
        assert verdict.satisfied is expected, config


def test_every_listed_architecture_must_pass():
    req = _requirement(KVM_MODULE, architectures=("x86-64", "arm64"))
    result = _stream(req, "alpha", "7.2", True, x86_64=MET, arm64={})
    verdict = vkr.decide(req, [result])
    assert (verdict.satisfied, verdict.held) == (False, ())
    (reason,) = verdict.reasons
    assert reason.startswith(f"{CID}: alpha arm64: CONFIG_KVM")


def test_an_architecture_this_forge_does_not_build_fails():
    req = _requirement(KVM_MODULE, architectures=("arm64",))
    (reason,) = vkr.decide(req, [_stream(req, "alpha", "7.2", True, x86_64=MET)]).reasons
    assert reason == f"{CID}: alpha arm64: this forge builds no arm64 kernel"


def test_a_module_abi_fails_closed_before_a_kernel_is_built():
    req = _requirement(KVM_MODULE, module_abi="6.9.4-1-cachyos")
    verdict = vkr.decide(req, [_stream(req, "alpha", "6.9.4", True, x86_64=MET)])
    assert (verdict.satisfied, verdict.held) == (False, ())
    assert "module-abi 6.9.4-1-cachyos" in verdict.reasons[0] and "#18" in verdict.reasons[0]


def test_a_document_no_stream_is_bound_to_fails():
    req = _requirement(KVM_MODULE)
    verdict = vkr.decide(req, [_stream(req, "alpha", "7.2", False, x86_64=MET)])
    assert not verdict.satisfied and verdict.held == ()


# --- versions.json: the per-consumer binding ----------------------------------------------


def test_every_requirement_binds_streams_versions_json_publishes():
    labels = [row["label"] for row in VERSIONS["downstream"]["requirements"]]
    assert len(labels) == len(set(labels)), "requirement labels must be unique"
    for row in ROWS.values():
        assert row["streams"], row["label"]
        assert set(row["streams"]) <= set(VERSIONS["streams"]), row["label"]


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("streams", None),
        ("streams", []),
        ("streams", ["realtime", "realtime"]),
        ("path", "../kernel-requirement.json"),
        ("path", "/build/kernel-requirement.json"),
        ("path", "build//kernel-requirement.json"),
        ("path", "build/../kernel-requirement.json"),
    ],
)
def test_the_schema_refuses_an_unbound_row_or_a_climbing_path(change, value):
    schema = json.loads((REPO_ROOT / "versions.schema.json").read_text(encoding="utf-8"))
    versions = copy.deepcopy(VERSIONS)
    row = versions["downstream"]["requirements"][0]
    if value is None:
        del row[change]
    else:
        row[change] = value
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(versions, schema)


# --- the command line: every document is reported, one refusal fails the run --------------


ANCHORS = [f"--versions={REPO_ROOT / 'versions.json'}", f"--kconfig-dir={KCONFIG}"]


def _cli(tmp_path: Path, *extra: str, imago: bytes | None = None, aegis: bytes | None = None):
    docs = {"imago": imago, "aegis-os": aegis}
    args = list(ANCHORS)
    for label, raw in docs.items():
        path = tmp_path / f"{label}.json"
        path.write_bytes(raw if raw is not None else (FIXTURES / f"{label}.json").read_bytes())
        args.append(f"--requirement={label}={path}")
    return vkr.main([*args, *extra])


def test_main_reports_every_document_and_fails_when_one_is_refused(tmp_path, capsys):
    report = tmp_path / "report.json"
    code = _cli(tmp_path, f"--report-json={report}", aegis=_altered("features", []))
    out = capsys.readouterr().out
    assert code == 1
    assert "== imago ==" in out and "PASS: held by" in out
    assert "== aegis-os ==" in out and "REJECTED (NoFeatures)" in out
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["schema"] == vkr.REPORT_SCHEMA and data["verdict"] == "FAIL"
    assert [doc["status"] for doc in data["documents"]] == ["PASS", "REJECTED"]


def test_main_passes_on_the_live_documents_and_records_their_source(tmp_path, capsys):
    report = tmp_path / "report.json"
    revision = "0" * 40
    code = _cli(
        tmp_path,
        f"--report-json={report}",
        f"--ref=imago={'a' * 40}",
        f"--nucleus-revision={revision}",
    )
    assert code == 0, capsys.readouterr().out
    data = json.loads(report.read_text(encoding="utf-8"))
    assert (data["nucleus_revision"], data["evidence_level"]) == (revision, "declared")
    source = data["documents"][0]["source"]
    assert source == {
        "label": "imago",
        "repository": ROWS["imago"]["repository"],
        "path": ROWS["imago"]["path"],
        "ref": "a" * 40,
        "sha256": hashlib.sha256((FIXTURES / "imago.json").read_bytes()).hexdigest(),
    }
    lsm = [
        feature
        for stream in data["documents"][1]["streams"]
        for arch in stream["architectures"]
        for feature in arch["features"]
        if feature["symbol"] == "CONFIG_BPF_LSM"
    ]
    assert lsm and all(f["probe"]["check"].endswith("probe deferred to boot") for f in lsm)


def test_main_proves_the_dispatched_digest_and_correlation_id(tmp_path, capsys):
    code = _cli(tmp_path, f"--sha256=imago={'0' * 64}", "--correlation-id=aegis-os=other-0001")
    out = capsys.readouterr().out
    assert code == 1
    assert "REJECTED (DigestMismatch)" in out and "REJECTED (CorrelationMismatch)" in out


def test_main_passes_a_dispatch_that_names_the_document_it_sent(tmp_path, capsys):
    """The success path of a dispatch: the digest and the correlation id both match."""
    raw = (FIXTURES / "imago.json").read_bytes()
    correlation_id = json.loads(raw)["correlation-id"]
    report = tmp_path / "report.json"
    code = _cli(
        tmp_path,
        f"--sha256=imago={hashlib.sha256(raw).hexdigest()}",
        f"--correlation-id=imago={correlation_id}",
        f"--report-json={report}",
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert f"requirement {correlation_id}:" in out
    assert "REJECTED" not in out and out.count("PASS: held by") == 2
    data = json.loads(report.read_text(encoding="utf-8"))
    assert [doc["status"] for doc in data["documents"]] == ["PASS", "PASS"]


def test_main_reports_an_unreadable_document_and_continues(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    aegis = FIXTURES / "aegis-os.json"
    code = vkr.main([*ANCHORS, f"--requirement=imago={missing}", f"--requirement=aegis-os={aegis}"])
    out = capsys.readouterr().out
    assert code == 1
    assert "ERROR (Unreadable)" in out and "PASS: held by realtime" in out


def test_main_refuses_a_binding_to_a_stream_versions_json_does_not_publish(tmp_path):
    versions = copy.deepcopy(VERSIONS)
    versions["downstream"]["requirements"][0]["streams"].append("nightly")
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(versions), encoding="utf-8")
    label = versions["downstream"]["requirements"][0]["label"]
    with pytest.raises(SystemExit) as info:
        vkr.main([f"--versions={path}", f"--requirement={label}=x.json"])
    assert info.value.code == 2


@pytest.mark.parametrize(
    "args",
    [
        ["--requirement=unknown=x.json"],
        ["--requirement=imago=x.json", "--sha256=imago=ABC"],
        ["--requirement=imago=x.json", "--ref=aegis-os=" + "a" * 40],
        ["--requirement=imago=x.json", "--requirement=imago=y.json"],
    ],
)
def test_main_refuses_arguments_versions_json_does_not_back(args):
    with pytest.raises(SystemExit) as info:
        vkr.main([*ANCHORS, *args])
    assert info.value.code == 2


# --- the resolved evidence level (docs/adr/0008) ----------------------------------------------
# A resolved .config is what scripts/merge-config.sh --source-tree writes after olddefconfig.
# Here it is the declared configuration, edited the way olddefconfig can edit it.


def _aegis_outcome(resolved):
    raw = (FIXTURES / "aegis-os.json").read_bytes()
    row = ROWS["aegis-os"]
    source = vkr.Source("aegis-os", row["repository"], row["path"], None, None)
    return vkr.verify_document(source, raw, row["streams"], VERSIONS, KCONFIG, resolved=resolved)


def _declared(stream: str, arch: str = "x86_64") -> dict:
    return vkr.declared_config(KCONFIG, arch, stream)


def test_a_bound_stream_is_judged_on_its_resolved_config_and_the_rest_on_declared():
    outcome = _aegis_outcome({("realtime", "x86_64"): _declared("realtime")})
    assert outcome.status == "PASS", outcome.verdict
    levels = {result.stream: result.evidence for result in outcome.results}
    assert levels["realtime"] == "resolved"
    assert {level for stream, level in levels.items() if stream != "realtime"} == {"declared"}


def test_a_symbol_olddefconfig_dropped_fails_the_resolved_level():
    resolved = _declared("realtime")
    del resolved["CONFIG_PREEMPT_RT"]
    outcome = _aegis_outcome({("realtime", "x86_64"): resolved})
    assert outcome.status == "FAIL"
    assert any(
        "realtime x86_64: CONFIG_PREEMPT_RT" in reason and "observed unrecorded" in reason
        for reason in outcome.verdict.reasons
    ), outcome.verdict.reasons


def test_a_bound_pair_without_a_resolved_config_is_an_error_not_a_verdict():
    outcome = _aegis_outcome({("mainstream", "x86_64"): _declared("mainstream")})
    assert (outcome.status, outcome.error_kind) == ("ERROR", "MissingEvidence")
    assert outcome.error == "no resolved configuration for realtime:x86_64"


def _resolved_file(tmp_path: Path, stream: str, arch: str = "x86_64", header=None) -> Path:
    """A resolved .config as Kconfig writes it: its header, then the declared symbols."""
    if header is None:
        kernel_arch = VERSIONS["architectures"][arch]["kernel_arch"]
        header = (kernel_arch, vq.kernelversion(VERSIONS["streams"][stream]["version"]))
    path = tmp_path / f"kernel-{stream}-{arch}.config"
    lines = ["#", "# Automatically generated file; DO NOT EDIT."]
    lines.append(f"# Linux/{header[0]} {header[1]} Kernel Configuration")
    lines.append("#")
    lines.extend(f"{key}={value}" for key, value in _declared(stream, arch).items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_main_reports_the_resolved_level(tmp_path, capsys):
    configs = []
    for stream in VERSIONS["streams"]:
        path = _resolved_file(tmp_path, stream)
        configs.append(f"--resolved-config={stream}:x86_64={path}")
    report = tmp_path / "report.json"
    code = _cli(tmp_path, *configs, f"--report-json={report}")
    out = capsys.readouterr().out
    assert code == 0, out
    assert "evidence level: resolved" in out
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["evidence_level"] == "resolved"
    imago = data["documents"][0]["streams"]
    assert {stream["evidence"] for stream in imago} == {"resolved"}


@pytest.mark.parametrize(
    "value",
    ["realtime-x86_64=x.config", "nightly:x86_64=x.config", "realtime:mips=x.config"],
)
def test_main_refuses_a_resolved_config_versions_json_does_not_build(tmp_path, value):
    with pytest.raises(SystemExit) as info:
        _cli(tmp_path, f"--resolved-config={value}")
    assert info.value.code == 2


def test_main_accepts_resolved_configs_the_documents_do_not_read(tmp_path, capsys):
    """Every leg is resolved; a leg no document binds or lists is accepted and not consulted."""
    configs = [
        f"--resolved-config={stream}:{arch}={_resolved_file(tmp_path, stream, arch)}"
        for stream in VERSIONS["streams"]
        for arch in VERSIONS["architectures"]
    ]
    code = _cli(tmp_path, *configs)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "evidence level: resolved" in out


def _release(stream: str) -> str:
    return vq.kernelversion(VERSIONS["streams"][stream]["version"])


@pytest.mark.parametrize(
    ("key", "named"),
    [
        ("mainstream:x86_64", ("x86_64", "lts")),
        ("lts:x86_64", ("arm64", "lts")),
        ("lts:riscv64", ("riscv64", "lts")),
    ],
    ids=["another-release", "another-architecture", "the-name-not-the-make-arch"],
)
def test_main_refuses_a_resolved_config_whose_header_names_another_leg(
    tmp_path, capsys, key, named
):
    """The label is what the caller says; the Kconfig header is what the file is."""
    stream, arch = key.split(":")
    header = (named[0], _release(named[1]))
    path = _resolved_file(tmp_path, stream, arch, header=header)
    with pytest.raises(SystemExit) as info:
        _cli(tmp_path, f"--resolved-config={key}={path}")
    assert info.value.code == 2
    assert f"is a Linux/{header[0]} {header[1]} configuration" in capsys.readouterr().err


def test_main_refuses_a_resolved_config_without_a_kconfig_header(tmp_path, capsys):
    """A declared merge is not a resolved configuration, whatever it is labelled."""
    path = tmp_path / "declared.config"
    body = "".join(f"{key}={value}\n" for key, value in _declared("lts").items())
    path.write_text("# Generated by nucleus scripts/merge-config.sh\n" + body, encoding="utf-8")
    with pytest.raises(SystemExit) as info:
        _cli(tmp_path, f"--resolved-config=lts:x86_64={path}")
    assert info.value.code == 2
    assert "Kernel Configuration' header" in capsys.readouterr().err


def _plan(tmp_path: Path, **documents) -> dict:
    tmp_path.mkdir(exist_ok=True)
    plan = tmp_path / "plan.json"
    assert _cli(tmp_path, f"--plan={plan}", **documents) == 0
    return json.loads(plan.read_text(encoding="utf-8"))


def test_the_plan_is_every_stream_on_every_architecture(tmp_path, capsys):
    """Resolving is also the survival check, so no leg is left out, bound or listed or not."""
    arches = " ".join(VERSIONS["architectures"])
    expected = [{"stream": stream, "arches": arches} for stream in VERSIONS["streams"]]
    assert _plan(tmp_path) == {"include": expected}
    assert len(expected) * len(VERSIONS["architectures"]) == 12


def test_the_plan_does_not_depend_on_the_documents(tmp_path, capsys):
    """A refused document narrows nothing: the declared run has already failed on it."""
    refused = _plan(tmp_path / "refused", imago=_altered("features", [], name="imago.json"))
    assert refused == _plan(tmp_path / "fixtures")
