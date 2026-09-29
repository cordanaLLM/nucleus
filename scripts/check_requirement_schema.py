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
"""Check kernel requirement documents against the JSON Schema their contract owner publishes.

cordanaLLM/Aegis-OS owns the ``aegis.p01-nucleus.kernel-requirement.v1`` contract and publishes
``build/kernel-requirement.schema.json`` (JSON Schema 2020-12, generated from its Rust types).
scripts/verify_kernel_requirement.py hand-ports the owner's decoder. This script reads a
document a second way, through the schema, so that a change on the owner's side that drifts from
the port shows up as a disagreement instead of as silence (docs/adr/0010).

versions.json ``downstream.requirement_schema`` pins the schema by repository, path, commit and
sha256, and names the copy vendored for the offline tests. Two subcommands:

``fetch``  read the schema at the pinned commit and write it, refusing any bytes whose sha256 is
           not the pinned one.
``check``  refuse a schema whose sha256 is not the pinned one, then read every document twice:
           validate it against the schema, and decode it with the verifier's parser. The result
           is one of four verdicts per document; only ``accepted by both`` passes:

           - accepted by both
           - refused by both (the document is invalid)
           - the verifier accepts what the schema refuses: drift, or a rule the port invented
           - the schema accepts what the verifier refuses: named with the verifier's refusal
             kind, and explained when the kind is one of SCHEMA_GAPS

The verifier stays stdlib-only, and downstream gates run it with ``python3 -I``; this script is
the only place jsonschema is imported, and only nucleus's own workflow and tests run it.

A pattern is matched as ECMA-262 defines it, which is the regular-expression dialect of JSON
Schema. Python's ``$`` also matches before a trailing newline, so an unmodified validator accepts
a ``correlation-id`` of ``"abc\\n"`` that the schema, and the owner's decoder, refuse.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_kernel_requirement as vkr  # noqa: E402
from jsonschema import Draft202012Validator, ValidationError, validators  # noqa: E402

ACCEPTED_BY_BOTH = "accepted by both"
REFUSED_BY_BOTH = "refused by both"
VERIFIER_ONLY = "the verifier accepts what the schema refuses"
SCHEMA_ONLY = "the schema accepts what the verifier refuses"

# Invariants the owner's decoder enforces (kernel.rs, payload.rs) that JSON Schema cannot state.
# The verifier follows the decoder (docs/adr/0007, section 2, "same"), so a document that breaks
# one is refused by the verifier and accepted by the schema. Nothing else may disagree.
SCHEMA_GAPS: Mapping[str, str] = {
    "TooLong": "a JSON Schema states no byte bound for the whole document",
    "DuplicateSymbol": "a JSON Schema cannot require one property of an array's items to be unique",
    "InvertedRelease": "a JSON Schema cannot order two release strings",
}

_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LABEL = re.compile(r"[a-z0-9][a-z0-9-]*")
_PIN_FIELDS = ("repository", "path", "commit", "sha256", "vendored")
_MESSAGE_LIMIT = 240
# The verifier refuses a document past 16384 bytes before it parses one. The schema has no such
# bound, so this check reads far more than that (the corpus of tests/test_requirement_schema.py
# holds a document just past it) and refuses to read a document that is no plausible requirement.
_READ_LIMIT = 1 << 20


class SchemaPinError(ValueError):
    """The pinned schema could not be read, or is not the pinned one."""


@dataclass(frozen=True)
class Pin:
    """versions.json downstream.requirement_schema."""

    repository: str
    path: str
    commit: str
    sha256: str
    vendored: str


@dataclass(frozen=True)
class Comparison:
    """One document read through the schema and through the verifier's parser."""

    label: str
    schema_errors: tuple[str, ...]
    refusal: vkr.RequirementError | None

    @property
    def verdict(self) -> str:
        if not self.schema_errors:
            return ACCEPTED_BY_BOTH if self.refusal is None else SCHEMA_ONLY
        return REFUSED_BY_BOTH if self.refusal is not None else VERIFIER_ONLY

    @property
    def agrees(self) -> bool:
        return self.verdict in (ACCEPTED_BY_BOTH, REFUSED_BY_BOTH)

    @property
    def gap(self) -> str | None:
        """Why the schema cannot see the verifier's refusal, when the refusal kind is a gap."""
        if self.verdict != SCHEMA_ONLY or self.refusal is None:
            return None
        return SCHEMA_GAPS.get(self.refusal.kind)

    @property
    def unexplained(self) -> bool:
        """A disagreement that no entry of SCHEMA_GAPS accounts for."""
        return not self.agrees and self.gap is None


def _short(text: str) -> str:
    """A message as a log line may carry it: bounded, on one line."""
    flat = " ".join(text.split())
    return flat if len(flat) <= _MESSAGE_LIMIT else f"{flat[: _MESSAGE_LIMIT - 3]}..."


# --- the pin -----------------------------------------------------------------------------------


def load_pin(versions: Mapping[str, object]) -> Pin:
    """The pin, refused unless every field has the shape the fetch and the tests rely on."""
    try:
        entry = versions["downstream"]["requirement_schema"]
        pin = Pin(**{field: entry[field] for field in _PIN_FIELDS})
    except (KeyError, TypeError) as exc:
        raise SchemaPinError(f"versions.json downstream.requirement_schema: {exc!r}") from exc
    if _COMMIT.fullmatch(pin.commit) is None:
        raise SchemaPinError(f"pinned commit {_short(pin.commit)!r} is not a full commit SHA")
    if _SHA256.fullmatch(pin.sha256) is None:
        raise SchemaPinError(f"pinned sha256 {_short(pin.sha256)!r} is not a SHA-256 digest")
    return pin


def prove_digest(raw: bytes, pin: Pin, what: str) -> None:
    actual = hashlib.sha256(raw).hexdigest()
    if actual != pin.sha256:
        raise SchemaPinError(f"sha256 {actual} is not the pinned {pin.sha256}: {what}")


def fetch_schema(pin: Pin) -> bytes:
    """The schema at the pinned commit, proven against the pinned digest."""
    endpoint = f"repos/{pin.repository}/contents/{pin.path}?ref={pin.commit}"
    argv = ["gh", "api", "-H", "Accept: application/vnd.github.raw+json", endpoint]
    try:
        completed = subprocess.run(argv, capture_output=True, check=False)
    except OSError as exc:
        raise SchemaPinError(f"could not run gh: {exc}") from exc
    if completed.returncode != 0:
        detail = _short(completed.stderr.decode("utf-8", "replace"))
        raise SchemaPinError(
            f"could not fetch {pin.repository}/{pin.path} at {pin.commit}: {detail}"
        )
    prove_digest(completed.stdout, pin, f"{pin.repository}/{pin.path} at {pin.commit}")
    return completed.stdout


def write_atomically(raw: bytes, target: Path) -> None:
    """The file appears whole or not at all, so a refused schema leaves nothing behind."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=target.parent, prefix=".schema-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(raw)
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


# --- validation --------------------------------------------------------------------------------


def ecma_pattern(pattern: str) -> str:
    r"""A JSON Schema pattern as a Python pattern with ECMA-262 anchors.

    ECMA-262 without the multiline flag lets ``$`` match only at the end of the input; Python
    also lets it match before a final newline. ``\Z`` is Python's end of input. Every pattern
    of the owner's schema is ``^...$`` with no escape sequence (tests/test_requirement_schema.py
    holds that), so this is the whole difference between the two dialects.
    """
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        return pattern[:-1] + r"\Z"
    return pattern


def _pattern(
    validator, pattern: str, instance: object, schema: object
) -> Iterator[ValidationError]:
    if validator.is_type(instance, "string") and re.search(ecma_pattern(pattern), instance) is None:
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


_Validator = validators.extend(Draft202012Validator, {"pattern": _pattern})


def make_validator(schema: Mapping[str, object]) -> Draft202012Validator:
    """A draft 2020-12 validator whose patterns follow ECMA-262; the schema is checked first."""
    Draft202012Validator.check_schema(schema)
    return _Validator(schema)


def _no_repeat(pairs: list[tuple[str, object]]) -> dict[str, object]:
    obj: dict[str, object] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate field {key!r}")
        obj[key] = value
    return obj


def _no_constant(name: str) -> object:
    raise ValueError(f"{name} is not a JSON value")


def load_instance(raw: bytes) -> object:
    """A document as a JSON value. A repeated field or NaN is not one, as the owner's decoder holds."""
    return json.loads(
        raw.decode("utf-8"), object_pairs_hook=_no_repeat, parse_constant=_no_constant
    )


def schema_errors(validator: Draft202012Validator, raw: bytes) -> tuple[str, ...]:
    """Every way the document breaks the schema, one line each; empty when it is valid."""
    if len(raw) > _READ_LIMIT:
        return (f"the document is {len(raw)} bytes, past the {_READ_LIMIT} this check reads",)
    try:
        instance = load_instance(raw)
    except (ValueError, RecursionError) as exc:
        return (_short(f"not a JSON document: {exc}"),)
    errors = sorted(
        validator.iter_errors(instance), key=lambda e: [str(p) for p in e.absolute_path]
    )
    return tuple(
        _short(f"{'/'.join(str(p) for p in e.absolute_path) or '<document>'}: {e.message}")
        for e in errors
    )


def verifier_refusal(raw: bytes) -> vkr.RequirementError | None:
    """The verifier's decoder on the document: its refusal, or None when it accepts."""
    try:
        vkr.parse_requirement(raw)
    except vkr.RequirementError as exc:
        return exc
    return None


def compare(validator: Draft202012Validator, label: str, raw: bytes) -> Comparison:
    return Comparison(label, schema_errors(validator, raw), verifier_refusal(raw))


def comparison_line(result: Comparison) -> str:
    """The one line the report carries for a document."""
    refused = _short(str(result.refusal)) if result.refusal is not None else ""
    first = result.schema_errors[0] if result.schema_errors else ""
    more = f" (and {len(result.schema_errors) - 1} more)" if len(result.schema_errors) > 1 else ""
    label = result.label
    if result.verdict == ACCEPTED_BY_BOTH:
        return f"==> {label}: valid against the schema, accepted by the verifier"
    if result.verdict == REFUSED_BY_BOTH:
        return f"==> {label}: REFUSED by both. schema: {first}{more} | verifier: {refused}"
    if result.verdict == VERIFIER_ONLY:
        return f"==> {label}: MISMATCH, the verifier accepts what the schema refuses. schema: {first}{more}"
    note = result.gap or "the schema states this rule and the verifier does not"
    return f"==> {label}: MISMATCH, the schema accepts what the verifier refuses. verifier: {refused} | {note}"


# --- command line ------------------------------------------------------------------------------


def _documents(values: Sequence[str]) -> dict[str, Path]:
    documents: dict[str, Path] = {}
    for value in values:
        label, sep, path = value.partition("=")
        if not sep or _LABEL.fullmatch(label) is None or not path:
            raise ValueError(f"--requirement {_short(value)!r} is not LABEL=PATH")
        if label in documents:
            raise ValueError(f"--requirement names {label!r} twice")
        documents[label] = Path(path)
    if not documents:
        raise ValueError("no --requirement given: nothing to check")
    return documents


def run_check(versions_path: Path, schema_path: Path | None, requirements: Sequence[str]) -> int:
    versions = json.loads(versions_path.read_text(encoding="utf-8"))
    pin = load_pin(versions)
    documents = _documents(requirements)
    path = schema_path or versions_path.resolve().parent / pin.vendored
    raw_schema = path.read_bytes()
    prove_digest(raw_schema, pin, str(path))
    validator = make_validator(json.loads(raw_schema))
    print(f"==> schema: {pin.repository}/{pin.path} at {pin.commit}, sha256 {pin.sha256}")
    results = [compare(validator, label, doc.read_bytes()) for label, doc in documents.items()]
    for result in results:
        print(comparison_line(result))
    return 0 if all(r.verdict == ACCEPTED_BY_BOTH for r in results) else 1


def run_fetch(versions_path: Path, out: Path) -> int:
    pin = load_pin(json.loads(versions_path.read_text(encoding="utf-8")))
    write_atomically(fetch_schema(pin), out)
    print(f"==> schema: {pin.repository}/{pin.path} at {pin.commit}, sha256 {pin.sha256} -> {out}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="fetch the schema at the pinned commit")
    fetch.add_argument("--versions", type=Path, default=Path("versions.json"))
    fetch.add_argument("--out", type=Path, required=True)
    check = commands.add_parser("check", help="validate documents and compare with the verifier")
    check.add_argument("--versions", type=Path, default=Path("versions.json"))
    check.add_argument("--schema", type=Path, help="schema file [default: the vendored copy]")
    check.add_argument("--requirement", action="append", default=[], metavar="LABEL=PATH")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "fetch":
            return run_fetch(args.versions, args.out)
        return run_check(args.versions, args.schema, args.requirement)
    except (SchemaPinError, ValueError, OSError, KeyError) as exc:
        print(f"::error::{_short(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
