"""Tests for the owner's JSON Schema pin and its cross-check with the verifier (docs/adr/0010).

cordanaLLM/Aegis-OS publishes build/kernel-requirement.schema.json. versions.json pins it by
commit and sha256 (``downstream.requirement_schema``), and tests/fixtures/kernel-requirement
carries the pinned bytes. scripts/check_requirement_schema.py reads each document through the
schema and through the verifier's parser and reports where the two disagree; these tests hold
the pin, the acceptance of issue #38, and a corpus of documents that must not split them.

This file needs jsonschema and pytest only: verify-requirements.yml runs it without PyYAML.
"""

import hashlib
import itertools
import json
import os
import re
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "kernel-requirement"
VERSIONS = json.loads((REPO_ROOT / "versions.json").read_text(encoding="utf-8"))
PIN = VERSIONS["downstream"]["requirement_schema"]
SCHEMA_PATH = REPO_ROOT / PIN["vendored"]
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_requirement_schema as crs  # noqa: E402
import verify_kernel_requirement as vkr  # noqa: E402

VALIDATOR = crs.make_validator(SCHEMA)
FIXTURE_NAMES = ("aegis-os.json", "imago.json")


def _fixture(name: str = "aegis-os.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _raw(doc: object) -> bytes:
    return json.dumps(doc).encode("utf-8")


def _row(symbol: str, state: str = "built-in", required_by: str = "REQ-P07-01") -> dict:
    return {"symbol": symbol, "state": state, "probe": "kernel-config", "required-by": required_by}


def _features(count: int) -> list[dict]:
    return [_row(f"CONFIG_F{index}") for index in range(count)]


def _compare(raw: bytes, label: str = "case") -> crs.Comparison:
    return crs.compare(VALIDATOR, label, raw)


# --- the pin ------------------------------------------------------------------------------------


def test_the_pin_names_the_owner_and_the_file_vendored_here():
    rows = {row["label"]: row for row in VERSIONS["downstream"]["requirements"]}
    assert PIN["repository"] == rows["aegis-os"]["repository"], "the owner publishes the schema"
    assert re.fullmatch(r"build/[a-z-]+\.schema\.json", PIN["path"]), PIN["path"]
    assert re.fullmatch(r"[0-9a-f]{40}", PIN["commit"]) and re.fullmatch(
        r"[0-9a-f]{64}", PIN["sha256"]
    )
    assert PIN["vendored"].startswith("tests/fixtures/kernel-requirement/")
    assert SCHEMA_PATH.is_file()


def test_the_vendored_schema_is_the_pinned_file():
    assert hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest() == PIN["sha256"]


def test_only_versions_json_records_the_pinned_commit_and_digest():
    """The pin has one home; a second copy is a second place to forget when the pin moves."""
    suffixes = {".py", ".sh", ".yml", ".yaml", ".md", ".json", ".toml", ".txt", ".cfg"}
    skipped = {".git", ".claude", "__pycache__", ".pytest_cache", "site", "output"}
    holders = []
    for path in REPO_ROOT.rglob("*"):
        parts = path.relative_to(REPO_ROOT).parts
        if not path.is_file() or path.suffix not in suffixes or skipped & set(parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if PIN["commit"] in text or PIN["sha256"] in text:
            holders.append("/".join(parts))
    assert holders == ["versions.json"], holders


def test_the_vendored_schema_is_a_valid_2020_12_schema():
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def _walk(node: object):
    """Every mapping and list nested in the schema."""
    yield node
    for child in (
        node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()
    ):
        yield from _walk(child)


def test_every_pattern_is_anchored_and_uses_no_escape():
    """crs.ecma_pattern turns the closing anchor into \\Z; that is the whole dialect difference."""
    patterns = [n["pattern"] for n in _walk(SCHEMA) if isinstance(n, dict) and "pattern" in n]
    assert len(patterns) >= 8, "the schema states its field encodings as patterns"
    for pattern in patterns:
        assert pattern.startswith("^") and pattern.endswith("$"), pattern
        assert "\\" not in pattern, pattern
        assert crs.ecma_pattern(pattern) == pattern[:-1] + "\\Z"


def test_the_schema_states_the_names_and_bounds_the_verifier_holds():
    """The schema and the port say the same thing about every name and bound; drift shows here."""
    top, defs = SCHEMA["properties"], SCHEMA["$defs"]
    assert set(top) == vkr._DOCUMENT_KEYS and set(SCHEMA["required"]) == set(vkr._DOCUMENT_REQUIRED)
    assert set(defs["KernelAbi"]["properties"]) == vkr._ABI_KEYS
    assert set(defs["FeatureRequirement"]["properties"]) == vkr._FEATURE_KEYS
    assert set(defs["ArtifactExpectation"]["properties"]) == vkr._ARTIFACT_KEYS
    assert (top["architectures"]["minItems"], top["architectures"]["maxItems"]) == (
        1,
        vkr.MAX_ARCHITECTURES,
    )
    assert (top["features"]["minItems"], top["features"]["maxItems"]) == (1, vkr.MAX_FEATURES)
    assert top["correlation-id"]["maxLength"] == vkr.MAX_FIELD_BYTES
    row = defs["FeatureRequirement"]["properties"]
    assert row["required-by"]["maxLength"] == vkr.MAX_FIELD_BYTES
    assert row["symbol"]["maxLength"] == vkr.MAX_SYMBOL_BYTES
    for name in ("minimum-release", "target-release", "module-abi"):
        assert defs["KernelAbi"]["properties"][name]["maxLength"] == vkr.MAX_RELEASE_BYTES
    assert defs["ArtifactExpectation"]["properties"]["signature"]["maxLength"] == (
        vkr.MAX_SIGNATURE_CHARS
    )


def _constants(definition: dict) -> set[str]:
    return {option["const"] for option in definition["oneOf"]}


def test_the_schema_enumerates_the_values_the_verifier_accepts():
    defs = SCHEMA["$defs"]
    assert _constants(defs["Architecture"]) == set(vkr.ARCHITECTURES)
    assert _constants(defs["RequiredState"]) == set(vkr.STATES)
    assert _constants(defs["ProbeSource"]) == set(vkr.PROBES)
    assert _constants(defs["KernelRequirementVersion"]) == {vkr.SCHEMA}


def test_every_object_in_the_schema_is_closed():
    objects = [n for n in _walk(SCHEMA) if isinstance(n, dict) and n.get("type") == "object"]
    assert len(objects) == 4, "the document, the abi, a feature and the artifact expectation"
    assert all(o["additionalProperties"] is False for o in objects)


# --- the documents both consumers publish -------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_the_committed_fixtures_validate_and_the_verifier_accepts_them(name):
    """Issue #38 positive."""
    result = _compare((FIXTURES / name).read_bytes(), name)
    assert result.schema_errors == () and result.refusal is None
    assert result.verdict == crs.ACCEPTED_BY_BOTH


def test_imagos_flavor_identifiers_validate():
    """Aegis decision D103 made required-by generic: imago's FLAVOR-* ids are valid."""
    flavors = [f["required-by"] for f in _fixture("imago.json")["features"]]
    assert any(name.startswith("FLAVOR-") for name in flavors), flavors
    assert VALIDATOR.is_valid(_fixture("imago.json"))


# --- issue #38 acceptance: negative and boundary -----------------------------------------------


def _isolated_verifier_report(tmp_path: Path, doc: dict) -> dict:
    """The verifier's JSON report, run the way a downstream gate runs it (python3 -I)."""
    document = tmp_path / "requirement.json"
    document.write_text(json.dumps(doc), encoding="utf-8")
    report = tmp_path / "report.json"
    subprocess.run(
        [
            sys.executable,
            "-I",
            "scripts/verify_kernel_requirement.py",
            f"--requirement=aegis-os={document}",
            f"--report-json={report}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(report.read_text(encoding="utf-8"))["documents"][0]


def test_an_unknown_top_level_field_fails_the_schema_and_is_refused_as_malformed(tmp_path):
    doc = _fixture()
    doc["unexpected-field"] = True
    errors = crs.schema_errors(VALIDATOR, _raw(doc))
    assert errors and "unexpected-field" in errors[0]
    refused = _isolated_verifier_report(tmp_path, doc)
    assert refused["status"] == "REJECTED"
    assert refused["rejection"]["kind"] == "Malformed"
    assert "unexpected-field" in refused["rejection"]["message"]


@pytest.mark.parametrize(("count", "valid"), [(1, True), (63, True), (64, True), (65, False)])
def test_the_feature_bound_is_the_same_for_the_schema_and_the_verifier(count, valid):
    """Issue #38 boundary: 64 entries are valid for both, 65 are invalid for both."""
    doc = _fixture()
    doc["features"] = _features(count)
    result = _compare(_raw(doc))
    assert (result.schema_errors == ()) is valid
    assert (result.refusal is None) is valid
    assert result.verdict == (crs.ACCEPTED_BY_BOTH if valid else crs.REFUSED_BY_BOTH)
    if not valid:
        assert result.refusal.kind == "TooMany"


# --- the corpus: schema and verifier must not disagree ------------------------------------------

FULL = {
    "schema": vkr.SCHEMA,
    "correlation-id": "corpus-full-0001",
    "architectures": ["x86-64", "arm64"],
    "abi": {"minimum-release": "6.12", "target-release": "7.3", "module-abi": "7.3.0-lusoris1"},
    "features": [
        _row("CONFIG_KVM", "module", "FLAVOR-BASE"),
        _row("CONFIG_PREEMPT_RT", "built-in", "REQ-P07-01"),
        _row("CONFIG_DEBUG_INFO_BTF", "present", "REQ-P06-05"),
        _row("CONFIG_NUCLEUS_GONE", "absent", "FLAVOR-K8S-NODE"),
    ],
    "artifact": {"digest": "a" * 64, "signature": "ab" * 8},
}

RELEASES = [
    ("plain", "6.12"),
    ("one-digit", "6"),
    ("suffix", "6.12.0-rc1+x_y"),
    ("64-chars", "6" + "1" * 63),
    ("65-chars", "6" + "1" * 64),
    ("empty", ""),
    ("letter-first", "v6"),
    ("dot-first", ".6"),
    ("space", "6 12"),
    ("newline", "6.12\n"),
    ("non-ascii", "6.é"),
    ("integer", 6),
    ("list", ["6"]),
    ("object", {}),
]
SYMBOLS = [
    ("plain", "CONFIG_A"),
    ("digits", "CONFIG_9"),
    ("64-bytes", "CONFIG_" + "A" * 57),
    ("65-bytes", "CONFIG_" + "A" * 58),
    ("bare-prefix", "CONFIG_"),
    ("lower-prefix", "config_a"),
    ("lower-body", "CONFIG_a"),
    ("dash", "CONFIG_A-B"),
    ("newline", "CONFIG_A\n"),
    ("no-prefix", "A"),
    ("empty", ""),
    ("integer", 1),
    ("null", None),
]
REQUIRED_BY = [
    ("requirement", "REQ-P07-01"),
    ("flavor", "FLAVOR-BASE"),
    ("short", "A"),
    ("three", "ABC"),
    ("four", "ABCD"),
    ("five", "ABCDE"),
    ("req-and-one", "REQ-1"),
    ("req-and-letter", "REQ-A"),
    ("bare-req-prefix", "REQ-"),
    ("req-dash-dash", "REQ--"),
    ("req-no-dash", "REQ"),
    ("req-letter", "REQX"),
    ("req-digit", "REQ1"),
    ("r", "R"),
    ("re", "RE"),
    ("lower", "req-1"),
    ("digit-first", "1ABC"),
    ("dash-first", "-A"),
    ("underscore", "FLAVOR_BASE"),
    ("space", "A B"),
    ("newline", "REQ-1\n"),
    ("128-chars", "A" * 128),
    ("129-chars", "A" * 129),
    ("non-ascii", "É"),
    ("empty", ""),
    ("integer", 1),
    ("null", None),
]
CORRELATION_IDS = [
    ("plain", "corpus-0001"),
    ("charset", "a.b:c_d-e"),
    ("one", "a"),
    ("128-chars", "a" * 128),
    ("129-chars", "a" * 129),
    ("empty", ""),
    ("space", "a b"),
    ("slash", "a/b"),
    ("newline", "abc\n"),
    ("non-ascii", "é"),
    ("integer", 1),
    ("null", None),
    ("list", []),
]
STATES = [
    *[(state, state) for state in vkr.STATES],
    ("short", "y"),
    ("cased", "Built-In"),
    ("empty", ""),
    ("null", None),
    ("integer", 1),
    ("list", ["built-in"]),
]
PROBES = [
    *[(probe, probe) for probe in vkr.PROBES],
    ("unknown", "cmdline"),
    ("cased", "Kernel-Config"),
    ("empty", ""),
    ("null", None),
]
DIGESTS = [
    ("64-hex", "a" * 64),
    ("63-hex", "a" * 63),
    ("65-hex", "a" * 65),
    ("upper-case", "A" * 64),
    ("not-hex", "g" * 64),
    ("newline", "a" * 64 + "\n"),
    ("empty", ""),
    ("null", None),
    ("integer", 1),
]
SIGNATURES = [
    ("pair", "ab"),
    ("256-chars", "ab" * 128),
    ("258-chars", "ab" * 129),
    ("odd", "abc"),
    ("upper-case", "AB"),
    ("not-hex", "zz"),
    ("newline", "ab\n"),
    ("empty", ""),
    ("null", None),
    ("integer", 1),
]
ARCHITECTURE_LISTS = [
    ("x86-64", ["x86-64"]),
    ("arm64", ["arm64"]),
    ("both", ["x86-64", "arm64"]),
    ("four", ["x86-64"] * 4),
    ("five", ["x86-64"] * 5),
    ("four-mixed", ["arm64", "x86-64", "arm64", "x86-64"]),
    ("empty", []),
    ("underscore", ["x86_64"]),
    ("riscv64", ["riscv64"]),
    ("cased", ["X86-64"]),
    ("string", "x86-64"),
    ("null-item", ["x86-64", None]),
    ("integer-item", [1]),
    ("object", {}),
    ("null", None),
]


def _put(doc: dict, path: tuple, value: object) -> dict:
    """A copy of FULL with the value at the path replaced (a dict path key is created)."""
    copy = json.loads(json.dumps(doc))
    node = copy
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
    return copy


def _drop(doc: dict, path: tuple) -> dict:
    copy = json.loads(json.dumps(doc))
    node = copy
    for step in path[:-1]:
        node = node[step]
    del node[path[-1]]
    return copy


def _padded(doc: dict, size: int) -> bytes:
    """The document as JSON, with trailing spaces up to exactly this many bytes."""
    raw = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    assert len(raw) <= size
    return raw + b" " * (size - len(raw))


def _field_cases() -> list[tuple[str, bytes]]:
    """Every field set to boundary and wrong values, one at a time."""
    cases: list[tuple[str, bytes]] = []
    abi = {"minimum-release": "1"}
    tables = [
        (("correlation-id",), CORRELATION_IDS, FULL),
        (("architectures",), ARCHITECTURE_LISTS, FULL),
        (("features", 0, "symbol"), SYMBOLS, FULL),
        (("features", 0, "state"), STATES, FULL),
        (("features", 0, "probe"), PROBES, FULL),
        (("features", 0, "required-by"), REQUIRED_BY, FULL),
        (("artifact", "digest"), DIGESTS, FULL),
        (("artifact", "signature"), SIGNATURES, FULL),
        (("abi", "minimum-release"), RELEASES + [("null", None)], {**FULL, "abi": abi}),
        (("abi", "target-release"), RELEASES + [("null", None)], {**FULL, "abi": abi}),
        (("abi", "module-abi"), RELEASES + [("null", None)], {**FULL, "abi": abi}),
    ]
    for path, values, base in tables:
        for label, value in values:
            name = f"{'/'.join(map(str, path))}: {label}"
            cases.append((name, _raw(_put(base, path, value))))
    return cases


def _shape_cases() -> list[tuple[str, bytes]]:
    cases: list[tuple[str, bytes]] = []
    for name in FIXTURE_NAMES:
        cases.append((f"fixture {name}", (FIXTURES / name).read_bytes()))
    cases.append(("full document", _raw(FULL)))
    cases.append(
        (
            "minimal document",
            _raw(
                _drop(
                    _drop(_drop(FULL, ("artifact",)), ("abi", "target-release")),
                    ("abi", "module-abi"),
                )
            ),
        )
    )
    required = [("schema",), ("correlation-id",), ("architectures",), ("abi",), ("features",)]
    required += [("abi", "minimum-release"), ("artifact", "digest")]
    required += [("features", 0, key) for key in ("symbol", "state", "probe", "required-by")]
    for path in required:
        cases.append((f"missing {'/'.join(map(str, path))}", _raw(_drop(FULL, path))))
    optional = [("artifact",), ("abi", "target-release"), ("abi", "module-abi")]
    optional += [("artifact", "signature")]
    for path in optional:
        cases.append((f"omitted {'/'.join(map(str, path))}", _raw(_drop(FULL, path))))
    for path in [(), ("abi",), ("features", 0), ("artifact",)]:
        name = f"unknown field at /{'/'.join(map(str, path))}"
        cases.append((name, _raw(_put(FULL, (*path, "extra"), 1))))
    cases.append(("json-schema keyword as field", _raw(_put(FULL, ("$schema",), "x"))))
    for label, value in [
        ("another version", "aegis.p01-nucleus.kernel-requirement.v2"),
        ("cased version", vkr.SCHEMA.upper()),
        ("empty", ""),
        ("null", None),
        ("integer", 1),
    ]:
        cases.append((f"schema: {label}", _raw(_put(FULL, ("schema",), value))))
    for label, value in [
        ("empty object", {}),
        ("null", None),
        ("list", []),
        ("string", "x"),
        ("array of fields", [FULL["abi"]]),
    ]:
        cases.append((f"abi: {label}", _raw(_put(FULL, ("abi",), value))))
    for label, value in [("empty object", {}), ("list", []), ("string", "x"), ("integer", 1)]:
        cases.append((f"artifact: {label}", _raw(_put(FULL, ("artifact",), value))))
    cases.append(
        ("artifact: signature without digest", _raw(_put(FULL, ("artifact",), {"signature": "ab"})))
    )
    for label, value in [("null", None), ("string", "x"), ("object", {}), ("integer", 1)]:
        cases.append((f"features: {label}", _raw(_put(FULL, ("features",), value))))
    for label, value in [("null", None), ("empty", {}), ("list", []), ("string", "x")]:
        cases.append((f"feature row: {label}", _raw(_put(FULL, ("features",), [value]))))
    for count in (0, 1, 2, 63, 64, 65, 100):
        cases.append((f"{count} features", _raw(_put(FULL, ("features",), _features(count)))))
    for label, value in [
        ("null", None),
        ("string", "x"),
        ("integer", 1),
        ("true", True),
        ("list", []),
    ]:
        cases.append((f"document is {label}", _raw(value)))
    array_form = [
        FULL["schema"],
        FULL["correlation-id"],
        FULL["architectures"],
        FULL["abi"],
        FULL["features"],
    ]
    cases.append(("document as an array of its fields", _raw(array_form)))
    return cases


def _rule_cases() -> list[tuple[str, bytes]]:
    """Rules the decoder enforces beyond field shape: where the two may legitimately differ."""
    cases: list[tuple[str, bytes]] = []
    repeated = [_row("CONFIG_A"), _row("CONFIG_A", "module")]
    cases.append(("repeated symbol", _raw(_put(FULL, ("features",), repeated))))
    ends = _features(64)
    ends[63] = ends[0]
    cases.append(("repeated symbol at both ends of 64", _raw(_put(FULL, ("features",), ends))))
    for label, target in [
        ("older", "6.11"),
        ("equal", "6.12"),
        ("newer", "6.13"),
        ("rc", "6.12-rc1"),
    ]:
        cases.append(
            (f"target-release {label}", _raw(_put(FULL, ("abi", "target-release"), target)))
        )
    cases.append(
        ("repeated architecture", _raw(_put(FULL, ("architectures",), ["arm64", "arm64"])))
    )
    long_rows = [_row(f"CONFIG_{index:0>57}"[:64], required_by="A" * 128) for index in range(64)]
    oversize = _put(FULL, ("features",), long_rows)
    cases.append(("64 long rows, past the byte bound", _raw(oversize)))
    cases.append(("exactly 16384 bytes", _padded(FULL, vkr.MAX_PAYLOAD_BYTES)))
    cases.append(("16385 bytes", _padded(FULL, vkr.MAX_PAYLOAD_BYTES + 1)))
    return cases


def _text_cases() -> list[tuple[str, bytes]]:
    """Documents that are not one JSON value, or that JSON parsers read differently."""
    text = json.dumps(FULL)
    cases = [
        ("empty input", b""),
        ("whitespace only", b"  \n"),
        ("open brace", b"{"),
        ("empty object", b"{}"),
        ("truncated", text[:-3].encode("utf-8")),
        ("trailing garbage", (text + " x").encode("utf-8")),
        ("trailing newline", (text + "\n").encode("utf-8")),
        ("pretty printed", json.dumps(FULL, indent=2).encode("utf-8")),
        ("byte order mark", b"\xef\xbb\xbf" + text.encode("utf-8")),
        ("not utf-8", b"\xff\xfe{}"),
        (
            "repeated top-level field",
            text.replace("{", '{"correlation-id": "x", ', 1).encode("utf-8"),
        ),
        (
            "repeated abi field",
            text.replace('"abi": {', '"abi": {"minimum-release": "1", ', 1).encode("utf-8"),
        ),
        (
            "repeated schema with another version",
            text.replace("{", '{"schema": "other", ', 1).encode("utf-8"),
        ),
        ("NaN as a release", text.replace('"7.3"', "NaN", 1).encode("utf-8")),
        ("Infinity as a release", text.replace('"7.3"', "Infinity", 1).encode("utf-8")),
        ("negative Infinity as a release", text.replace('"7.3"', "-Infinity", 1).encode("utf-8")),
        (
            "escaped unicode in a symbol",
            text.replace("CONFIG_KVM", "CONFIG_K\\u0056M", 1).encode("utf-8"),
        ),
        (
            "lone surrogate in a symbol",
            text.replace("CONFIG_KVM", "CONFIG_\\ud800", 1).encode("utf-8"),
        ),
    ]
    return cases


def _corpus() -> list[tuple[str, bytes]]:
    return _shape_cases() + _field_cases() + _rule_cases() + _text_cases()


CORPUS = _corpus()


def _results() -> list[crs.Comparison]:
    return [_compare(raw, name) for name, raw in CORPUS]


def test_the_corpus_is_large_and_two_sided():
    """A corpus that only holds refusals, or only acceptances, cannot show a disagreement."""
    results = _results()
    assert len({name for name, _ in CORPUS}) == len(CORPUS), "case names are unique"
    assert len(results) >= 200
    verdicts = [result.verdict for result in results]
    assert verdicts.count(crs.ACCEPTED_BY_BOTH) >= 40
    assert verdicts.count(crs.REFUSED_BY_BOTH) >= 150


def test_the_schema_and_the_verifier_agree_on_every_document():
    """Fails on any disagreement, and lists each case. The exceptions are SCHEMA_GAPS alone."""
    unexplained = [
        f"{result.label}: {crs.comparison_line(result)}"
        for result in _results()
        if result.unexplained
    ]
    assert not unexplained, f"{len(unexplained)} disagreement(s):\n" + "\n".join(unexplained)


def test_each_documented_gap_is_a_real_disagreement_and_nothing_else_is():
    """An exception that no case exercises would outlive its reason, so each must be observed."""
    seen = {result.refusal.kind for result in _results() if result.verdict == crs.SCHEMA_ONLY}
    assert seen == set(crs.SCHEMA_GAPS), f"observed {sorted(seen)}"
    assert not [r.label for r in _results() if r.verdict == crs.VERIFIER_ONLY]


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("repeated symbol", "DuplicateSymbol"),
        ("target-release older", "InvertedRelease"),
        ("64 long rows, past the byte bound", "TooLong"),
        ("16385 bytes", "TooLong"),
    ],
)
def test_the_rules_json_schema_cannot_state_are_refused_by_the_verifier_alone(name, kind):
    result = _compare(dict(CORPUS)[name], name)
    assert result.verdict == crs.SCHEMA_ONLY and result.refusal.kind == kind
    assert result.gap == crs.SCHEMA_GAPS[kind]


def test_the_byte_bound_is_inclusive():
    assert _compare(dict(CORPUS)["exactly 16384 bytes"]).verdict == crs.ACCEPTED_BY_BOTH


def test_a_repeated_field_is_no_json_value_for_the_schema_either():
    result = _compare(dict(CORPUS)["repeated top-level field"])
    assert result.verdict == crs.REFUSED_BY_BOTH
    assert "duplicate field" in result.schema_errors[0]
    assert result.refusal.kind == "Malformed"


def test_a_document_no_requirement_could_be_is_not_read_at_all():
    """The verifier stops at 16384 bytes before parsing; the schema check stops at a mebibyte."""
    result = _compare(b" " * (1 << 20) + b"{}")
    assert result.verdict == crs.REFUSED_BY_BOTH
    assert "past the 1048576 this check reads" in result.schema_errors[0]
    assert result.refusal.kind == "TooLong"


def test_the_array_form_is_refused_by_both():
    """Aegis-OS#167 closed the array form at the owner; ADR-0007 recorded it as a divergence."""
    result = _compare(dict(CORPUS)["document as an array of its fields"])
    assert result.verdict == crs.REFUSED_BY_BOTH and result.refusal.kind == "Malformed"


# --- the cross-check has teeth: it sees drift in both directions --------------------------------


def _drifted(mutate) -> jsonschema.Draft202012Validator:
    schema = json.loads(json.dumps(SCHEMA))
    mutate(schema)
    return crs.make_validator(schema)


def test_a_schema_that_admits_less_than_the_verifier_is_reported():
    """The owner narrows features to 63: the verifier still accepts 64."""
    validator = _drifted(lambda s: s["properties"]["features"].update(maxItems=63))
    doc = _fixture()
    doc["features"] = _features(64)
    result = crs.compare(validator, "drift", _raw(doc))
    assert result.verdict == crs.VERIFIER_ONLY and result.unexplained
    assert "MISMATCH, the verifier accepts what the schema refuses" in crs.comparison_line(result)


def test_a_schema_that_admits_more_than_the_verifier_is_reported():
    """The owner opens the document to extra fields: the verifier still refuses them."""
    validator = _drifted(lambda s: s.update(additionalProperties=True))
    doc = _fixture()
    doc["unexpected-field"] = True
    result = crs.compare(validator, "drift", _raw(doc))
    assert result.verdict == crs.SCHEMA_ONLY and result.unexplained
    assert "MISMATCH, the schema accepts what the verifier refuses" in crs.comparison_line(result)


def test_a_trailing_newline_is_refused_as_ecma_262_refuses_it():
    """Python's $ also matches before a final newline; the schema's dialect does not."""
    doc = _fixture()
    doc["correlation-id"] = "abc\n"
    result = _compare(_raw(doc))
    assert result.verdict == crs.REFUSED_BY_BOTH
    assert "does not match" in result.schema_errors[0]


def test_required_by_means_the_same_to_the_schema_and_the_verifier():
    """Every string over a small alphabet, up to six characters, including the REQ- edge cases."""
    schema_pattern = re.compile(
        crs.ecma_pattern(
            SCHEMA["$defs"]["FeatureRequirement"]["properties"]["required-by"]["pattern"]
        )
    )
    disagreements = []
    for length in range(7):
        for chars in itertools.product("REQAZ0-a", repeat=length):
            text = "".join(chars)
            schema_says = schema_pattern.search(text) is not None
            verifier_says = bool(text) and vkr._REQUIRED_BY.fullmatch(text) is not None
            if schema_says != verifier_says:
                disagreements.append(text)
    assert not disagreements, disagreements[:10]


# --- scripts/check_requirement_schema.py --------------------------------------------------------


def _check(
    tmp_path: Path, *arguments: str, versions: Path | None = None
) -> subprocess.CompletedProcess:
    argv = [sys.executable, "scripts/check_requirement_schema.py", "check", *arguments]
    if versions is not None:
        argv.append(f"--versions={versions}")
    return subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def _documents(*names: str) -> list[str]:
    return [f"--requirement={name.removesuffix('.json')}={FIXTURES / name}" for name in names]


def test_check_passes_the_committed_fixtures_against_the_vendored_schema(tmp_path):
    result = _check(tmp_path, *_documents(*FIXTURE_NAMES))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"sha256 {PIN['sha256']}" in result.stdout
    assert "==> imago: valid against the schema, accepted by the verifier" in result.stdout
    assert "==> aegis-os: valid against the schema, accepted by the verifier" in result.stdout


def test_check_fails_a_document_both_readings_refuse(tmp_path):
    doc = _fixture()
    doc["unexpected-field"] = True
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    result = _check(tmp_path, f"--requirement=bad={path}", *_documents("imago.json"))
    assert result.returncode == 1
    assert "==> bad: REFUSED by both." in result.stdout
    assert "unexpected-field" in result.stdout and "Malformed" in result.stdout
    assert "==> imago: valid against the schema" in result.stdout, (
        "the other document is still read"
    )


def test_check_names_the_rule_a_schema_cannot_state(tmp_path):
    doc = _fixture()
    doc["features"].append(doc["features"][0])
    path = tmp_path / "repeated.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    result = _check(tmp_path, f"--requirement=repeated={path}")
    assert result.returncode == 1
    assert "MISMATCH, the schema accepts what the verifier refuses" in result.stdout
    assert "DuplicateSymbol" in result.stdout
    assert crs.SCHEMA_GAPS["DuplicateSymbol"] in result.stdout


def _repinned(tmp_path: Path, schema: dict) -> tuple[Path, Path]:
    """A versions.json that pins a changed schema, and that schema on disk."""
    raw = json.dumps(schema).encode("utf-8")
    schema_path = tmp_path / "schema.json"
    schema_path.write_bytes(raw)
    versions = json.loads(json.dumps(VERSIONS))
    versions["downstream"]["requirement_schema"]["sha256"] = hashlib.sha256(raw).hexdigest()
    versions_path = tmp_path / "versions.json"
    versions_path.write_text(json.dumps(versions), encoding="utf-8")
    return versions_path, schema_path


def test_check_reports_a_mismatch_in_each_direction(tmp_path):
    narrower = json.loads(json.dumps(SCHEMA))
    narrower["properties"]["architectures"]["maxItems"] = 1
    versions, schema = _repinned(tmp_path, narrower)
    doc = _fixture()
    doc["architectures"] = ["x86-64", "arm64"]
    both = tmp_path / "two-architectures.json"
    both.write_text(json.dumps(doc), encoding="utf-8")
    result = _check(tmp_path, f"--schema={schema}", f"--requirement=two={both}", versions=versions)
    assert result.returncode == 1
    assert "MISMATCH, the verifier accepts what the schema refuses" in result.stdout
    assert "architectures: " in result.stdout

    wider = json.loads(json.dumps(SCHEMA))
    wider["additionalProperties"] = True
    versions, schema = _repinned(tmp_path, wider)
    doc = _fixture()
    doc["unexpected-field"] = True
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps(doc), encoding="utf-8")
    result = _check(
        tmp_path, f"--schema={schema}", f"--requirement=extra={extra}", versions=versions
    )
    assert result.returncode == 1
    assert "MISMATCH, the schema accepts what the verifier refuses" in result.stdout
    assert "the schema states this rule and the verifier does not" in result.stdout


def test_check_refuses_a_schema_that_is_not_the_pinned_file(tmp_path):
    other = tmp_path / "other.schema.json"
    other.write_text(json.dumps({**SCHEMA, "title": "Other"}), encoding="utf-8")
    result = _check(tmp_path, f"--schema={other}", *_documents("aegis-os.json"))
    assert result.returncode == 1
    assert result.stderr.startswith("::error::")
    assert f"not the pinned {PIN['sha256']}" in result.stderr
    assert "==> aegis-os" not in result.stdout, "no document is read against an unpinned schema"


@pytest.mark.parametrize(
    "arguments", [[], ["--requirement=noequals"], ["--requirement=Bad=x.json"]]
)
def test_check_refuses_arguments_that_name_no_document(tmp_path, arguments):
    result = _check(tmp_path, *arguments)
    assert result.returncode == 1 and result.stderr.startswith("::error::")


def test_check_refuses_the_same_label_twice(tmp_path):
    result = _check(tmp_path, *_documents("imago.json"), *_documents("imago.json"))
    assert result.returncode == 1 and "twice" in result.stderr


def test_the_script_leaves_no_bytecode_where_it_runs(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    for name in (
        "check_requirement_schema.py",
        "verify_kernel_requirement.py",
        "versions_query.py",
    ):
        (checkout / "scripts" / name).write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
    (checkout / "versions.json").write_bytes((REPO_ROOT / "versions.json").read_bytes())
    vendored = checkout / PIN["vendored"]
    vendored.parent.mkdir(parents=True)
    vendored.write_bytes(SCHEMA_PATH.read_bytes())
    completed = subprocess.run(
        [sys.executable, "scripts/check_requirement_schema.py", "check", *_documents("imago.json")],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
        env={k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"},
    )
    assert completed.returncode == 0, completed.stderr
    assert not list(checkout.rglob("__pycache__"))


# --- fetching the schema at the pinned commit ---------------------------------------------------

GH_STUB = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import os, sys
    with open(os.environ["GH_STUB_LOG"], "a", encoding="utf-8") as log:
        log.write(" ".join(sys.argv[1:]) + "\\n")
    if os.environ.get("GH_STUB_FAIL"):
        sys.stderr.write("HTTP 404: Not Found\\n")
        sys.exit(1)
    with open(os.environ["GH_STUB_BODY"], "rb") as handle:
        sys.stdout.buffer.write(handle.read())
    """
)


def _fetch(
    tmp_path: Path, body: bytes, fail: bool = False
) -> tuple[subprocess.CompletedProcess, Path]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "gh"
    stub.write_text(GH_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    served = tmp_path / "served.json"
    served.write_bytes(body)
    out = tmp_path / "requirements" / "schema.json"
    environment = {
        **os.environ,
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "GH_STUB_LOG": str(tmp_path / "gh.log"),
        "GH_STUB_BODY": str(served),
    }
    if fail:
        environment["GH_STUB_FAIL"] = "1"
    completed = subprocess.run(
        [sys.executable, "scripts/check_requirement_schema.py", "fetch", f"--out={out}"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, out


def test_fetch_reads_the_pinned_commit_and_writes_the_proven_bytes(tmp_path):
    result, out = _fetch(tmp_path, SCHEMA_PATH.read_bytes())
    assert result.returncode == 0, result.stderr
    assert out.read_bytes() == SCHEMA_PATH.read_bytes()
    log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert f"repos/{PIN['repository']}/contents/{PIN['path']}?ref={PIN['commit']}" in log
    assert "Accept: application/vnd.github.raw+json" in log


def test_fetch_refuses_bytes_that_are_not_the_pinned_digest_and_writes_nothing(tmp_path):
    result, out = _fetch(tmp_path, SCHEMA_PATH.read_bytes() + b" ")
    assert result.returncode == 1
    assert (
        result.stderr.startswith("::error::") and f"not the pinned {PIN['sha256']}" in result.stderr
    )
    assert not out.exists() and not list(out.parent.glob(".schema-*")), "no partial file"


def test_fetch_refuses_when_gh_fails(tmp_path):
    result, out = _fetch(tmp_path, b"", fail=True)
    assert result.returncode == 1
    assert "could not fetch" in result.stderr and PIN["commit"] in result.stderr
    assert not out.exists()


def test_a_pin_with_a_malformed_commit_or_digest_is_refused():
    for field, bad in (("commit", "main"), ("commit", "E" * 40), ("sha256", "abc")):
        versions = json.loads(json.dumps(VERSIONS))
        versions["downstream"]["requirement_schema"][field] = bad
        with pytest.raises(crs.SchemaPinError):
            crs.load_pin(versions)
    with pytest.raises(crs.SchemaPinError):
        crs.load_pin({"downstream": {}})
