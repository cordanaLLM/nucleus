#!/usr/bin/env python3
# Copyright 2026 The Lusoris Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Verify downstream kernel requirement documents against the kconfig this forge declares.

A requirement is an ``aegis.p01-nucleus.kernel-requirement.v1`` document. cordanaLLM/Aegis-OS
owns that contract: its normative definition is the Rust crate ``crates/aegis-fabrica-defs``
(``src/kernel.rs``, ``src/field.rs``, ``src/payload.rs``), and this parser follows it field
for field, with one deliberate divergence on ``required-by`` (see ``_REQUIRED_BY``).
versions.json ``downstream.requirements`` declares where each document lives and which
streams its consumer is bound to.

The policy is docs/adr/0007-document-driven-kernel-requirements.md:

- every bound stream must hold the document on every architecture it lists: the stream's
  release is at least ``abi.minimum-release`` and every feature is in exactly its state;
- the other streams are evaluated and reported, and never gate;
- ``abi.target-release`` is recorded and never checked;
- ``abi.module-abi`` names the release of a built kernel, and none exists before issue
  #18, so a document that sets it fails closed.

The evidence level is ``declared``: the kconfig fragments as scripts/merge-config.sh merges
them. ``make olddefconfig`` can still drop a symbol whose dependencies are unmet; issue #18
adds the ``resolved`` level, fed to ``stream_result`` from the built ``.config``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "aegis.p01-nucleus.kernel-requirement.v1"
REPORT_SCHEMA = "nucleus.kernel-requirement-report.v1"
EVIDENCE_LEVEL = "declared"

# Bounds of the owner definition, Aegis-OS crates/aegis-fabrica-defs.
MAX_PAYLOAD_BYTES = 16_384  # payload.rs MAX_PAYLOAD_BYTES, checked before parsing
MAX_FEATURES = 64  # kernel.rs MAX_FEATURES
MAX_ARCHITECTURES = 4  # kernel.rs MAX_ARCHITECTURES
MAX_FIELD_BYTES = 128  # field.rs MAX_FIELD_BYTES: correlation-id, required-by
MAX_RELEASE_BYTES = 64  # field.rs MAX_RELEASE_BYTES
MAX_SYMBOL_BYTES = 64  # field.rs MAX_SYMBOL_BYTES
MAX_SIGNATURE_CHARS = 256  # field.rs MAX_SIGNATURE_CHARS
MAX_RELEASE_COMPONENTS = 8  # field.rs MAX_RELEASE_COMPONENTS
_U64_MAX = (1 << 64) - 1

# kernel.rs Architecture: mkosi's spellings, mapped to the names versions.json builds.
ARCHITECTURES = {"x86-64": "x86_64", "arm64": "arm64"}
# kernel.rs RequiredState.
STATES = ("built-in", "module", "present", "absent")
# kernel.rs ProbeSource::path. kernel-config reads the configuration itself; the other four
# read a running kernel, so here they are checked through their symbol, deferred to boot.
PROBES = {
    "kernel-config": "/proc/config.gz",
    "lsm-list": "/sys/kernel/security/lsm",
    "btf-vmlinux": "/sys/kernel/btf/vmlinux",
    "powercap": "/sys/class/powercap",
    "iommu-groups": "/sys/kernel/iommu_groups",
}

# field.rs encodings, applied with fullmatch after a byte-length check.
_CORRELATION_ID = re.compile(r"[A-Za-z0-9._:-]+")
_SYMBOL = re.compile(r"CONFIG_[A-Z0-9_]+")
_RELEASE = re.compile(r"[0-9][0-9A-Za-z._+-]*")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SIGNATURE = re.compile(r"(?:[0-9a-f]{2})+")
_REVISION = re.compile(r"[0-9a-f]{40}")
# The one deliberate divergence from field.rs RequirementId, an open question to Aegis-OS
# (docs/adr/0007): Aegis demands a REQ- prefix, and imago's live document names its flavors
# (FLAVOR-BASE, FLAVOR-K8S-NODE). Accepted here: an upper-case identifier of [A-Z0-9-].
_REQUIRED_BY = re.compile(r"[A-Z][A-Z0-9-]*")

_DOCUMENT_KEYS = frozenset(
    {"schema", "correlation-id", "architectures", "abi", "features", "artifact"}
)
_DOCUMENT_REQUIRED = ("schema", "correlation-id", "architectures", "abi", "features")
_ABI_KEYS = frozenset({"minimum-release", "target-release", "module-abi"})
_FEATURE_KEYS = frozenset({"symbol", "state", "probe", "required-by"})
_ARTIFACT_KEYS = frozenset({"digest", "signature"})

# The fragment grammar, shared with MERGE_AWK in scripts/merge-config.sh.
_ASSIGNMENT = re.compile(r"(CONFIG_[A-Za-z0-9_]+)=(.*)")
_UNSET = re.compile(r"# (CONFIG_[A-Za-z0-9_]+) is not set")
_OBSERVED_STATE = {"y": "built-in", "m": "module", "n": "not set"}


class RequirementError(ValueError):
    """A refused requirement document, and the Aegis ``KernelError`` kind it maps to.

    Kinds: TooLong, Malformed, UnknownVersion, NoFeatures, NoArchitectures, TooMany,
    DuplicateSymbol and InvertedRelease as in kernel.rs; DigestMismatch and
    CorrelationMismatch are this forge's checks of what a dispatch claimed.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


class KconfigError(ValueError):
    """A kconfig fragment or .config line outside the grammar merge-config.sh accepts."""


@dataclass(frozen=True)
class Feature:
    """One requirement row: a symbol, the state it must be in, and how it is probed."""

    symbol: str
    state: str
    probe: str
    required_by: str


@dataclass(frozen=True)
class Abi:
    """The release floor, and the two fields that are recorded rather than gating."""

    minimum_release: str
    target_release: str | None = None
    module_abi: str | None = None


@dataclass(frozen=True)
class Artifact:
    """The digest and signature a conforming artifact is expected to carry. Echoed only."""

    digest: str
    signature: str | None = None


@dataclass(frozen=True)
class Requirement:
    """A decoded, validated ``aegis.p01-nucleus.kernel-requirement.v1`` document."""

    correlation_id: str
    architectures: tuple[str, ...]
    abi: Abi
    features: tuple[Feature, ...]
    artifact: Artifact | None = None


@dataclass(frozen=True)
class FeatureCheck:
    """One feature against one configuration: the value observed and whether it satisfies."""

    feature: Feature
    observed: str | None
    satisfied: bool


@dataclass(frozen=True)
class ArchitectureResult:
    """One listed architecture. ``arch`` is None when this forge builds no such kernel."""

    token: str
    arch: str | None
    checks: tuple[FeatureCheck, ...] = ()

    @property
    def satisfied(self) -> bool:
        return self.arch is not None and all(check.satisfied for check in self.checks)


@dataclass(frozen=True)
class StreamResult:
    """How one stream fares: its release against the floor and every listed architecture."""

    stream: str
    version: str
    bound: bool
    release_ok: bool
    architectures: tuple[ArchitectureResult, ...]

    @property
    def satisfied(self) -> bool:
        return self.release_ok and all(arch.satisfied for arch in self.architectures)


@dataclass(frozen=True)
class Verdict:
    """The decision over the bound streams: which held the document, and every failure."""

    satisfied: bool
    held: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Source:
    """Where a document came from, as recorded in the report."""

    label: str
    repository: str
    path: str
    ref: str | None
    sha256: str | None


@dataclass(frozen=True)
class Outcome:
    """The result for one document: PASS, FAIL, REJECTED (refused) or ERROR (unreadable)."""

    source: Source
    status: str
    requirement: Requirement | None = None
    bound: tuple[str, ...] = ()
    results: tuple[StreamResult, ...] = ()
    verdict: Verdict | None = None
    error_kind: str | None = None
    error: str | None = None


def _shown(value: object) -> str:
    """A value as an error message may echo it: repr, truncated."""
    text = repr(value)
    return text if len(text) <= 80 else f"{text[:77]}..."


# --- decoding: the order of kernel.rs KernelRequirement::decode ------------------------------


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    obj: dict[str, object] = {}
    for key, value in pairs:
        if key in obj:
            raise RequirementError("Malformed", f"duplicate field {_shown(key)}")
        obj[key] = value
    return obj


def _no_constant(name: str) -> object:
    raise RequirementError("Malformed", f"{name} is not a JSON value")


def _decode_json(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8")
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_no_constant)
    except RequirementError:
        raise
    except (ValueError, RecursionError) as exc:
        raise RequirementError("Malformed", f"not a JSON document: {exc}") from exc


def _check_version(doc: object) -> dict[str, object]:
    """UnknownVersion for another contract version, kept apart from Malformed (payload.rs)."""
    if not isinstance(doc, dict):
        raise RequirementError("Malformed", "the document is not a JSON object")
    declared = doc.get("schema")
    if isinstance(declared, str) and declared != SCHEMA:
        raise RequirementError(
            "UnknownVersion", f"the document claims {_shown(declared)}, not {SCHEMA!r}"
        )
    if declared != SCHEMA:
        raise RequirementError("Malformed", f"schema: must be {SCHEMA!r}")
    return doc


def _object(
    value: object, allowed: frozenset[str], required: Sequence[str], where: str
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RequirementError("Malformed", f"{where}: must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        names = ", ".join(_shown(key) for key in unknown)
        raise RequirementError("Malformed", f"{where}: unknown field(s) {names}")
    missing = [key for key in required if key not in value]
    if missing:
        raise RequirementError("Malformed", f"{where}: missing field(s) {', '.join(missing)}")
    return value


def _bounded(value: object, pattern: re.Pattern[str], limit: int, where: str, shape: str) -> str:
    """A field.rs string: non-empty, within its byte bound, then within its shape."""
    if not isinstance(value, str):
        raise RequirementError("Malformed", f"{where}: must be a string")
    if not value:
        raise RequirementError("Malformed", f"{where}: an empty value is not admissible")
    size = len(value.encode("utf-8", "surrogatepass"))
    if size > limit:
        raise RequirementError("Malformed", f"{where}: {size} bytes exceeds the bound of {limit}")
    if pattern.fullmatch(value) is None:
        raise RequirementError("Malformed", f"{where}: {_shown(value)} is not {shape}")
    return value


def _optional(
    value: object, pattern: re.Pattern[str], limit: int, where: str, shape: str
) -> str | None:
    """An optional field: absent and null both read as not set, as serde's Option does."""
    return None if value is None else _bounded(value, pattern, limit, where, shape)


def _release(value: object, where: str, optional: bool = False) -> str | None:
    shape = "a release starting with a version digit, of [0-9A-Za-z._+-]"
    if optional:
        return _optional(value, _RELEASE, MAX_RELEASE_BYTES, where, shape)
    return _bounded(value, _RELEASE, MAX_RELEASE_BYTES, where, shape)


def _abi(value: object) -> Abi:
    abi = _object(value, _ABI_KEYS, ("minimum-release",), "abi")
    return Abi(
        minimum_release=_release(abi["minimum-release"], "abi.minimum-release"),
        target_release=_release(abi.get("target-release"), "abi.target-release", optional=True),
        module_abi=_release(abi.get("module-abi"), "abi.module-abi", optional=True),
    )


def _architectures(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RequirementError("Malformed", "architectures: must be a list")
    for index, token in enumerate(value):
        if not isinstance(token, str) or token not in ARCHITECTURES:
            accepted = ", ".join(ARCHITECTURES)
            raise RequirementError(
                "Malformed", f"architectures[{index}]: {_shown(token)} is not one of {accepted}"
            )
    return tuple(value)


def _feature(value: object, where: str) -> Feature:
    row = _object(value, _FEATURE_KEYS, ("symbol", "state", "probe", "required-by"), where)
    symbol = _bounded(
        row["symbol"], _SYMBOL, MAX_SYMBOL_BYTES, f"{where}.symbol", "a CONFIG_ symbol"
    )
    state, probe = row["state"], row["probe"]
    if not isinstance(state, str) or state not in STATES:
        raise RequirementError(
            "Malformed", f"{where}.state: {_shown(state)} is not one of {', '.join(STATES)}"
        )
    if not isinstance(probe, str) or probe not in PROBES:
        raise RequirementError(
            "Malformed", f"{where}.probe: {_shown(probe)} is not one of {', '.join(PROBES)}"
        )
    required_by = _bounded(
        row["required-by"],
        _REQUIRED_BY,
        MAX_FIELD_BYTES,
        f"{where}.required-by",
        "an upper-case requirement identifier of [A-Z0-9-]",
    )
    return Feature(symbol, state, probe, required_by)


def _features(value: object) -> tuple[Feature, ...]:
    if not isinstance(value, list):
        raise RequirementError("Malformed", "features: must be a list")
    return tuple(_feature(row, f"features[{index}]") for index, row in enumerate(value))


def _artifact(value: object) -> Artifact | None:
    if value is None:
        return None
    artifact = _object(value, _ARTIFACT_KEYS, ("digest",), "artifact")
    digest = _bounded(
        artifact["digest"], _DIGEST, 64, "artifact.digest", "a lower-case sha256 digest"
    )
    signature = _optional(
        artifact.get("signature"),
        _SIGNATURE,
        MAX_SIGNATURE_CHARS,
        "artifact.signature",
        "an even number of lower-case hexadecimal digits",
    )
    return Artifact(digest, signature)


def _validate(requirement: Requirement) -> None:
    """kernel.rs KernelRequirement::validate: the invariants the field types cannot express."""
    if not requirement.features:
        raise RequirementError(
            "NoFeatures", "the document lists no required feature; an empty requirement is refused"
        )
    if not requirement.architectures:
        raise RequirementError("NoArchitectures", "the document accepts no architecture")
    if len(requirement.features) > MAX_FEATURES:
        raise RequirementError("TooMany", f"the document carries more than {MAX_FEATURES} features")
    if len(requirement.architectures) > MAX_ARCHITECTURES:
        raise RequirementError(
            "TooMany", f"the document carries more than {MAX_ARCHITECTURES} architectures"
        )
    seen: set[str] = set()
    for feature in requirement.features:
        if feature.symbol in seen:
            raise RequirementError(
                "DuplicateSymbol", f"the document requires {feature.symbol} more than once"
            )
        seen.add(feature.symbol)
    abi = requirement.abi
    if abi.target_release is not None and not at_least(abi.target_release, abi.minimum_release):
        raise RequirementError(
            "InvertedRelease",
            f"target-release {abi.target_release} is older than minimum-release "
            f"{abi.minimum_release}",
        )


def parse_requirement(raw: bytes, expected_sha256: str | None = None) -> Requirement:
    """Decode and validate one document, refusing it with a RequirementError.

    The order is the owner's: the byte bound before anything is parsed, then the contract
    version, every field (unknown fields are refused at every level), then the invariants.
    A dispatched digest is proven right after the byte bound, before the JSON is read.
    A repeated architecture is accepted, as the owner accepts it, and evaluated once.
    """
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise RequirementError(
            "TooLong", f"the document is {len(raw)} bytes, past the bound of {MAX_PAYLOAD_BYTES}"
        )
    if expected_sha256 is not None:
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_sha256:
            raise RequirementError(
                "DigestMismatch", f"dispatched sha256 {expected_sha256}, fetched {actual}"
            )
    doc = _object(_check_version(_decode_json(raw)), _DOCUMENT_KEYS, _DOCUMENT_REQUIRED, "document")
    requirement = Requirement(
        correlation_id=_bounded(
            doc["correlation-id"],
            _CORRELATION_ID,
            MAX_FIELD_BYTES,
            "correlation-id",
            "made of [A-Za-z0-9._:-]",
        ),
        architectures=_architectures(doc["architectures"]),
        abi=_abi(doc["abi"]),
        features=_features(doc["features"]),
        artifact=_artifact(doc.get("artifact")),
    )
    _validate(requirement)
    return requirement


# --- releases and states ---------------------------------------------------------------------


def release_components(release: str) -> tuple[int, ...]:
    """The leading dotted numeric components of a release (field.rs numeric_components).

    7.2.4-1-cachyos reads (7, 2, 4), 7.3-rc2 reads (7, 3) and 7.2-rt reads (7, 2). The scan
    stops at the first character that is neither an ASCII digit nor a dot after a digit,
    and is bounded by MAX_RELEASE_BYTES characters and MAX_RELEASE_COMPONENTS components.
    """
    out: list[int] = []
    current, started = 0, False
    for char in release[:MAX_RELEASE_BYTES]:
        if "0" <= char <= "9":
            current = min(current * 10 + ord(char) - ord("0"), _U64_MAX)
            started = True
            continue
        if char != "." or not started or len(out) >= MAX_RELEASE_COMPONENTS:
            break
        out.append(current)
        current, started = 0, False
    if started and len(out) < MAX_RELEASE_COMPONENTS:
        out.append(current)
    return tuple(out)


def at_least(release: str, floor: str) -> bool:
    """Whether release is floor or newer (field.rs KernelRelease::at_least).

    A missing component reads as zero and a suffix is not ordered: 7.3 is newer than 7.2.4
    and not newer than 7.3.1, and 7.3-rc2 reads as 7.3, so it meets a 7.3.0 floor.
    """
    mine, theirs = release_components(release), release_components(floor)
    for index in range(MAX_RELEASE_COMPONENTS):
        left = mine[index] if index < len(mine) else 0
        right = theirs[index] if index < len(theirs) else 0
        if left != right:
            return left > right
    return True


def state_satisfied(required: str, observed: str | None) -> bool:
    """Whether an observed value meets a required state (kernel.rs satisfied_by).

    ``observed`` is what a configuration records: y, m, n (a ``# CONFIG_X is not set`` line
    reads as n), another value for a non-tristate symbol, or None when nothing records the
    symbol. None satisfies nothing, absent included: an unrecorded symbol is an unanswered
    question, not a measured absence. A value that is not y, m or n satisfies nothing.
    """
    state = None if observed is None else _OBSERVED_STATE.get(observed)
    if state is None:
        return False
    if required == "present":
        return state != "not set"
    if required == "absent":
        return state == "not set"
    return state == required


def describe_observed(observed: str | None) -> str:
    if observed is None:
        return "unrecorded"
    return "=n (not set)" if observed == "n" else f"={observed}"


# --- configurations: the merge scripts/merge-config.sh performs ------------------------------


def read_config(path: Path) -> dict[str, str]:
    """Symbols a kconfig fragment or a .config records, the last line for a symbol winning.

    After trimming spaces, tabs and carriage returns, a line is blank, ``CONFIG_X=value``,
    the Kconfig unset form ``# CONFIG_X is not set`` (read as n), or another ``#`` comment.
    Any other line raises KconfigError, as scripts/merge-config.sh refuses it.
    """
    values: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip(" \t\r")
        assignment = _ASSIGNMENT.fullmatch(line)
        if assignment:
            values[assignment.group(1)] = assignment.group(2)
            continue
        unset = _UNSET.fullmatch(line)
        if unset:
            values[unset.group(1)] = "n"
        elif line and not line.startswith("#"):
            raise KconfigError(f"{path}:{number}: not an assignment, an unset or a comment: {line}")
    return values


def fragment_layers(kconfig_dir: Path, arch: str, stream: str) -> list[Path]:
    """The fragments merge-config.sh merges for arch and stream, in merge order."""
    layers = [kconfig_dir / "security-hardened.config", kconfig_dir / f"{arch}.config"]
    stream_fragment = kconfig_dir / "streams" / f"{stream}.config"
    if stream_fragment.is_file():
        layers.append(stream_fragment)
    return layers


def declared_config(kconfig_dir: Path, arch: str, stream: str) -> dict[str, str]:
    """The configuration the fragments declare for arch and stream: evidence level declared."""
    merged: dict[str, str] = {}
    for layer in fragment_layers(kconfig_dir, arch, stream):
        merged.update(read_config(layer))
    return merged


# --- evaluation and decision -----------------------------------------------------------------


def check_features(
    features: Sequence[Feature], config: Mapping[str, str]
) -> tuple[FeatureCheck, ...]:
    """Every feature against one configuration, by exact symbol and exact state.

    Lookup is by the exact symbol, so CONFIG_KVM_GUEST never answers for CONFIG_KVM, and a
    module request configured =y is unmet rather than over-satisfied.
    """
    checks = []
    for feature in features:
        observed = config.get(feature.symbol)
        checks.append(FeatureCheck(feature, observed, state_satisfied(feature.state, observed)))
    return tuple(checks)


def stream_result(
    requirement: Requirement,
    stream: str,
    version: str,
    bound: bool,
    configs: Mapping[str, Mapping[str, str]],
) -> StreamResult:
    """Evaluate one stream on every architecture the document lists.

    ``configs`` maps each architecture this forge builds (x86_64, not x86-64) to the
    configuration that stands as evidence for it: declared_config today, the resolved
    .config of a real build once issue #18 lands. A listed architecture with no entry is
    one this forge does not build, and fails. A repeated architecture is evaluated once.
    """
    architectures = []
    for token in dict.fromkeys(requirement.architectures):
        arch = ARCHITECTURES[token]
        config = configs.get(arch)
        if config is None:
            architectures.append(ArchitectureResult(token, None))
            continue
        checks = check_features(requirement.features, config)
        architectures.append(ArchitectureResult(token, arch, checks))
    release_ok = at_least(version, requirement.abi.minimum_release)
    return StreamResult(stream, version, bound, release_ok, tuple(architectures))


def evaluate_declared(
    requirement: Requirement,
    versions: Mapping[str, object],
    kconfig_dir: Path,
    bound_streams: Sequence[str],
) -> tuple[StreamResult, ...]:
    """Every stream versions.json publishes, against the fragments declared for it."""
    built = [ARCHITECTURES[token] for token in dict.fromkeys(requirement.architectures)]
    built = [arch for arch in built if arch in versions["architectures"]]
    results = []
    for name, stream in versions["streams"].items():
        configs = {arch: declared_config(kconfig_dir, arch, name) for arch in built}
        bound = name in bound_streams
        results.append(stream_result(requirement, name, stream["version"], bound, configs))
    return tuple(results)


def stream_failures(correlation_id: str, minimum: str, result: StreamResult) -> list[str]:
    """Why one stream does not hold a document, one line per failure."""
    where = f"{correlation_id}: {result.stream}"
    out = []
    if not result.release_ok:
        out.append(f"{where}: release {result.version} is below minimum-release {minimum}")
    for arch in result.architectures:
        if arch.arch is None:
            out.append(f"{where} {arch.token}: this forge builds no {arch.token} kernel")
            continue
        for check in arch.checks:
            if check.satisfied:
                continue
            feature = check.feature
            out.append(
                f"{where} {arch.arch}: {feature.symbol} (required-by {feature.required_by}) "
                f"requires {feature.state}, observed {describe_observed(check.observed)}"
            )
    return out


def decide(requirement: Requirement, results: Sequence[StreamResult]) -> Verdict:
    """Every bound stream must hold the document, or it fails (docs/adr/0007).

    A bound stream holds when its release is at least minimum-release and every feature is
    in its required state on every listed architecture. Unbound streams never gate, and
    target-release is never consulted. module-abi fails closed: it names the release of a
    built kernel, and none exists before issue #18. ``held`` names the bound streams that
    passed; every reason carries the correlation-id, the stream, and for a feature the
    architecture, symbol, required-by, required state and observed value.
    """
    correlation_id = requirement.correlation_id
    bound = [result for result in results if result.bound]
    reasons: list[str] = []
    if not bound:
        reasons.append(f"{correlation_id}: no stream is bound to this requirement")
    module_abi = requirement.abi.module_abi
    if module_abi is not None:
        reasons.append(
            f"{correlation_id}: abi.module-abi {module_abi} names the release of a built "
            "kernel; none exists before issue #18, so it cannot be verified"
        )
    for result in bound:
        reasons.extend(stream_failures(correlation_id, requirement.abi.minimum_release, result))
    held = () if module_abi is not None else tuple(r.stream for r in bound if r.satisfied)
    return Verdict(not reasons, held, tuple(reasons))


def verify_document(
    source: Source,
    raw: bytes,
    bound_streams: Sequence[str],
    versions: Mapping[str, object],
    kconfig_dir: Path,
    expected_sha256: str | None = None,
    expected_correlation_id: str | None = None,
) -> Outcome:
    """Parse, evaluate and decide one document; a refused document is REJECTED, not raised."""
    try:
        requirement = parse_requirement(raw, expected_sha256)
        if (
            expected_correlation_id is not None
            and requirement.correlation_id != expected_correlation_id
        ):
            raise RequirementError(
                "CorrelationMismatch",
                f"the dispatch names correlation-id {_shown(expected_correlation_id)}, the "
                f"document {requirement.correlation_id!r}",
            )
    except RequirementError as exc:
        return Outcome(source, "REJECTED", error_kind=exc.kind, error=exc.message)
    results = evaluate_declared(requirement, versions, kconfig_dir, bound_streams)
    verdict = decide(requirement, results)
    status = "PASS" if verdict.satisfied else "FAIL"
    return Outcome(source, status, requirement, tuple(bound_streams), results, verdict)


# --- report ------------------------------------------------------------------------------------

POLICY = {
    "binding": "versions.json downstream.requirements[].streams",
    "bound_streams": "every bound stream must hold the document; the others are information",
    "architectures": "every architecture the document lists must pass",
    "minimum_release": "leading dotted numeric components, zero-padded, suffix ignored",
    "target_release": "recorded, never checked",
    "module_abi": "fails closed until a built kernel exists (issue #18)",
    "runtime_probes": "checked through their kconfig symbol; the probe is deferred to boot",
}


def _probe_json(probe: str) -> dict[str, str]:
    if probe == "kernel-config":
        check = "kconfig symbol"
    else:
        check = "kconfig symbol; probe deferred to boot"
    return {"kind": probe, "path": PROBES[probe], "check": check}


def _check_json(check: FeatureCheck) -> dict[str, object]:
    feature = check.feature
    return {
        "symbol": feature.symbol,
        "required_by": feature.required_by,
        "required": feature.state,
        "observed": check.observed,
        "satisfied": check.satisfied,
        "probe": _probe_json(feature.probe),
    }


def _stream_json(result: StreamResult) -> dict[str, object]:
    return {
        "stream": result.stream,
        "version": result.version,
        "bound": result.bound,
        "release_ok": result.release_ok,
        "satisfied": result.satisfied,
        "architectures": [
            {
                "token": arch.token,
                "arch": arch.arch,
                "satisfied": arch.satisfied,
                "features": [_check_json(check) for check in arch.checks],
            }
            for arch in result.architectures
        ],
    }


def _requirement_json(requirement: Requirement) -> dict[str, object]:
    artifact = None
    if requirement.artifact is not None:
        artifact = {
            "digest": requirement.artifact.digest,
            "signature": requirement.artifact.signature,
            "verified": False,
        }
    return {
        "correlation_id": requirement.correlation_id,
        "architectures": list(requirement.architectures),
        "abi": {
            "minimum_release": requirement.abi.minimum_release,
            "target_release": requirement.abi.target_release,
            "module_abi": requirement.abi.module_abi,
        },
        "artifact": artifact,
    }


def outcome_json(outcome: Outcome) -> dict[str, object]:
    source = outcome.source
    doc: dict[str, object] = {
        "label": source.label,
        "status": outcome.status,
        "source": {
            "label": source.label,
            "repository": source.repository,
            "path": source.path,
            "ref": source.ref,
            "sha256": source.sha256,
        },
    }
    if outcome.error_kind is not None:
        doc["rejection"] = {"kind": outcome.error_kind, "message": outcome.error}
    if outcome.requirement is not None and outcome.verdict is not None:
        doc.update(_requirement_json(outcome.requirement))
        doc["bound_streams"] = list(outcome.bound)
        doc["held"] = list(outcome.verdict.held)
        doc["reasons"] = list(outcome.verdict.reasons)
        doc["streams"] = [_stream_json(result) for result in outcome.results]
    return doc


def report_json(outcomes: Sequence[Outcome], revision: str | None) -> dict[str, object]:
    """The nucleus.kernel-requirement-report.v1 record: internal, not a contract."""
    passed = all(outcome.status == "PASS" for outcome in outcomes)
    return {
        "schema": REPORT_SCHEMA,
        "nucleus_revision": revision,
        "evidence_level": EVIDENCE_LEVEL,
        "policy": POLICY,
        "verdict": "PASS" if passed else "FAIL",
        "documents": [outcome_json(outcome) for outcome in outcomes],
    }


def _probe_note(probe: str) -> str:
    return probe if probe == "kernel-config" else f"{probe}, probe deferred to boot"


def _check_line(check: FeatureCheck) -> str:
    feature = check.feature
    mark = "ok   " if check.satisfied else "unmet"
    return (
        f"      {mark} {feature.symbol} (required-by {feature.required_by}): requires "
        f"{feature.state}, observed {describe_observed(check.observed)} "
        f"[{_probe_note(feature.probe)}]"
    )


def _stream_lines(result: StreamResult, minimum: str) -> list[str]:
    role = "bound" if result.bound else "not bound, information only"
    release = "meets" if result.release_ok else "is below"
    lines = [
        f"  {result.stream} {result.version} ({role}): release {release} minimum-release {minimum}"
    ]
    for arch in result.architectures:
        if arch.arch is None:
            lines.append(f"    {arch.token}: this forge builds no such kernel")
            continue
        met = sum(check.satisfied for check in arch.checks)
        lines.append(f"    {arch.arch}: {met}/{len(arch.checks)} features in the required state")
        shown = arch.checks if result.bound else [c for c in arch.checks if not c.satisfied]
        lines.extend(_check_line(check) for check in shown)
    return lines


def _requirement_lines(requirement: Requirement) -> list[str]:
    abi = requirement.abi
    target = abi.target_release or "none"
    lines = [
        f"  requirement {requirement.correlation_id}: {len(requirement.features)} features for "
        f"{', '.join(requirement.architectures)}; minimum-release {abi.minimum_release}; "
        f"target-release {target} (recorded, not checked)"
    ]
    if abi.module_abi is not None:
        lines.append(f"  module-abi {abi.module_abi}: cannot be verified before issue #18")
    if requirement.artifact is None:
        lines.append("  artifact expectation: none")
    else:
        signature = requirement.artifact.signature or "none"
        lines.append(
            f"  artifact expectation: digest {requirement.artifact.digest}, signature "
            f"{signature} (recorded, not verified: no artifact exists before issue #18)"
        )
    return lines


def outcome_lines(outcome: Outcome) -> list[str]:
    """The human report for one document."""
    source = outcome.source
    ref = source.ref or "an unrecorded revision"
    digest = source.sha256 or "unread"
    lines = [
        f"== {source.label} ==",
        f"  source: {source.repository} {source.path} at {ref} (sha256 {digest})",
    ]
    if outcome.requirement is None or outcome.verdict is None:
        lines.append(f"  {outcome.status} ({outcome.error_kind}): {outcome.error}")
        return lines
    lines.extend(_requirement_lines(outcome.requirement))
    verdict = outcome.verdict
    held = ", ".join(verdict.held) or "no stream"
    lines.append(f"  {outcome.status}: held by {held}; bound: {', '.join(outcome.bound) or '-'}")
    lines.extend(f"    - {reason}" for reason in verdict.reasons)
    for result in outcome.results:
        lines.extend(_stream_lines(result, outcome.requirement.abi.minimum_release))
    return lines


def header_lines(revision: str | None) -> list[str]:
    return [
        f"kernel requirement report ({REPORT_SCHEMA})",
        f"nucleus revision: {revision or 'unrecorded'}",
        f"evidence level: {EVIDENCE_LEVEL} (kconfig fragments as scripts/merge-config.sh merges "
        "them; olddefconfig resolution arrives with issue #18)",
        "policy: every bound stream must hold the document on every listed architecture; "
        "unbound streams are information only",
    ]


# --- command line ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    documents: dict[str, str]
    digests: dict[str, str]
    refs: dict[str, str]
    correlation_ids: dict[str, str]
    rows: dict[str, dict[str, object]]
    versions: dict[str, object]
    kconfig_dir: Path
    revision: str | None
    report_json: Path | None


def _pairs(values: Sequence[str], flag: str, pattern: re.Pattern[str] | None) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for value in values:
        label, sep, rest = value.partition("=")
        if not sep or not label or not rest:
            raise ValueError(f"{flag} expects LABEL=VALUE, got {_shown(value)}")
        if label in pairs:
            raise ValueError(f"{flag} names {label} twice")
        if pattern is not None and pattern.fullmatch(rest) is None:
            raise ValueError(f"{flag} {label}: {_shown(rest)} is not in the expected form")
        pairs[label] = rest
    return pairs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    add = parser.add_argument
    add(
        "--requirement",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="a document to verify; LABEL is its versions.json downstream.requirements label",
    )
    add(
        "--ref",
        action="append",
        default=[],
        metavar="LABEL=SHA",
        help="the commit the document was read at, recorded in the report",
    )
    add(
        "--sha256",
        action="append",
        default=[],
        metavar="LABEL=HEX",
        help="a dispatched digest, proven before the document is parsed",
    )
    add(
        "--correlation-id",
        action="append",
        default=[],
        metavar="LABEL=ID",
        help="a dispatched correlation id, which must equal the document's",
    )
    add("--kconfig-dir", type=Path, default=Path("kconfig"))
    add("--versions", type=Path, default=Path("versions.json"))
    add("--nucleus-revision", metavar="SHA", help="the nucleus commit being verified")
    add("--report-json", type=Path, metavar="PATH", help="write the JSON report here")
    return parser


def _options(args: argparse.Namespace) -> Options:
    versions = json.loads(args.versions.read_text(encoding="utf-8"))
    rows = {row["label"]: row for row in versions["downstream"]["requirements"]}
    documents = _pairs(args.requirement, "--requirement", None)
    options = Options(
        documents=documents,
        digests=_pairs(args.sha256, "--sha256", _DIGEST),
        refs=_pairs(args.ref, "--ref", _REVISION),
        correlation_ids=_pairs(args.correlation_id, "--correlation-id", _CORRELATION_ID),
        rows=rows,
        versions=versions,
        kconfig_dir=args.kconfig_dir,
        revision=args.nucleus_revision,
        report_json=args.report_json,
    )
    if args.nucleus_revision is not None and _REVISION.fullmatch(args.nucleus_revision) is None:
        raise ValueError("--nucleus-revision must be a full 40-character commit")
    for label in documents:
        if label not in rows:
            raise ValueError(f"{label} is not a versions.json downstream.requirements label")
        unknown = sorted(set(rows[label]["streams"]) - set(versions["streams"]))
        if unknown:
            raise ValueError(
                f"{label} is bound to streams versions.json does not publish: {unknown}"
            )
    for flag, pairs in (("--ref", options.refs), ("--sha256", options.digests)):
        for label in pairs:
            if label not in documents:
                raise ValueError(f"{flag} names {label}, which no --requirement names")
    for label in options.correlation_ids:
        if label not in documents:
            raise ValueError(f"--correlation-id names {label}, which no --requirement names")
    return options


def _run_document(label: str, options: Options) -> Outcome:
    """One document, isolated: an unreadable file or kconfig is ERROR, the rest continue."""
    row = options.rows[label]
    repository, path = str(row["repository"]), str(row["path"])
    try:
        raw = Path(options.documents[label]).read_bytes()
    except OSError as exc:
        source = Source(label, repository, path, options.refs.get(label), None)
        return Outcome(source, "ERROR", error_kind="Unreadable", error=str(exc))
    digest = hashlib.sha256(raw).hexdigest()
    source = Source(label, repository, path, options.refs.get(label), digest)
    try:
        return verify_document(
            source,
            raw,
            tuple(row["streams"]),
            options.versions,
            options.kconfig_dir,
            options.digests.get(label),
            options.correlation_ids.get(label),
        )
    except (KconfigError, OSError) as exc:
        return Outcome(source, "ERROR", error_kind=type(exc).__name__, error=str(exc))


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        options = _options(args)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    outcomes = [_run_document(label, options) for label in options.documents]
    lines = header_lines(options.revision)
    for outcome in outcomes:
        lines.extend(outcome_lines(outcome))
    print("\n".join(lines))
    if options.report_json is not None:
        report = report_json(outcomes, options.revision)
        options.report_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if all(outcome.status == "PASS" for outcome in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
