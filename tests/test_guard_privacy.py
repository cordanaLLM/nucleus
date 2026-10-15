# Copyright 2026 The Lusoris Authors
# SPDX-License-Identifier: EUPL-1.2
"""Behaviour of the pre-tool privacy guard, .agents/hooks-scripts/guard_privacy.py.

The guard used to end the process from inside its scanner and walked the payload
recursively. It now returns the violation to main() and walks iteratively within
explicit bounds. These tests pin what it blocks and what it lets through, as the hook
runs it: JSON on standard input, exit status 1 plus a message on standard error to
block, exit status 0 to allow. Private addresses and home paths are assembled at run
time so this file itself stays free of them.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD = REPO_ROOT / ".agents" / "hooks-scripts" / "guard_privacy.py"

IP_TEN = ".".join(["10", "1", "2", "3"])
IP_192 = ".".join(["192", "168", "7", "9"])
IP_172 = ".".join(["172", "20", "0", "5"])
IP_172_PUBLIC = ".".join(["172", "32", "0", "5"])
HOME_PATH = "/" + "home" + "/dev0/"
MAC_PATH = "/" + "Users" + "/dev1/"
WINDOWS_PATH = "C:" + "\\" + "Users" + "\\" + "dev2" + "\\"

IP_MESSAGE = "SECURITY VIOLATION: Private RFC 1918 IP detected: "
PATH_MESSAGE = "SECURITY VIOLATION: Local workstation path detected: "


def _load_guard():
    spec = importlib.util.spec_from_file_location("guard_privacy", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(payload: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )


def _arguments(value) -> str:
    return json.dumps({"arguments": value})


BLOCKED = [
    ("ten-net address", _arguments({"content": f"gateway {IP_TEN}"}), IP_MESSAGE + IP_TEN),
    ("192.168 address", _arguments({"content": IP_192}), IP_MESSAGE + IP_192),
    ("172.16/12 address", _arguments({"content": IP_172}), IP_MESSAGE + IP_172),
    ("linux home path", _arguments({"content": HOME_PATH}), PATH_MESSAGE + HOME_PATH),
    ("macos home path", _arguments({"content": MAC_PATH}), PATH_MESSAGE + MAC_PATH),
    ("windows home path", _arguments({"content": WINDOWS_PATH}), PATH_MESSAGE + WINDOWS_PATH),
    (
        "address before path in one string",
        _arguments({"content": f"{HOME_PATH} {IP_192}"}),
        IP_MESSAGE + IP_192,
    ),
    (
        "first value in document order wins",
        _arguments({"a": HOME_PATH, "b": IP_TEN}),
        PATH_MESSAGE + HOME_PATH,
    ),
    (
        "nested list inside a dictionary",
        _arguments({"edits": [{"old": "x", "new": [["y", f"via {IP_TEN}"]]}]}),
        IP_MESSAGE + IP_TEN,
    ),
    ("arguments given as a bare string", _arguments(IP_172), IP_MESSAGE + IP_172),
    ("arguments given as a list", _arguments(["ok", [HOME_PATH]]), PATH_MESSAGE + HOME_PATH),
    (
        "violation at the deepest scanned level",
        '{"arguments": ' + "[" * 64 + json.dumps(HOME_PATH) + "]" * 64 + "}",
        PATH_MESSAGE + HOME_PATH,
    ),
]

ALLOWED = [
    ("empty input", ""),
    ("whitespace input", "  \n"),
    ("input that is not JSON", "not json"),
    ("object without arguments", json.dumps({"other": IP_TEN})),
    ("documentation address", _arguments({"content": "target: 192.0.2.1"})),
    ("address outside 172.16/12", _arguments({"content": IP_172_PUBLIC})),
    ("dictionary keys are not scanned", _arguments({HOME_PATH: "ok", IP_TEN: "ok"})),
    ("numbers, booleans and null", _arguments({"n": 10, "b": True, "z": None})),
    (
        "clean value at the deepest scanned level",
        '{"arguments": ' + "[" * 64 + '"ok"' + "]" * 64 + "}",
    ),
]


@pytest.mark.parametrize(("name", "payload", "message"), BLOCKED, ids=[case[0] for case in BLOCKED])
def test_guard_blocks_prohibited_values(name: str, payload: str, message: str) -> None:
    result = _run(payload)
    assert result.returncode == 1, name
    assert result.stderr == message + "\n", name


@pytest.mark.parametrize(("name", "payload"), ALLOWED, ids=[case[0] for case in ALLOWED])
def test_guard_allows_clean_payloads(name: str, payload: str) -> None:
    result = _run(payload)
    assert result.returncode == 0, name
    assert result.stderr == "", name


@pytest.mark.parametrize("payload", ["[1]", "null", "5", '"text"'])
def test_guard_refuses_json_that_is_not_an_object(payload: str) -> None:
    """A payload that parses but is not an object still ends with exit status 1."""
    assert _run(payload).returncode == 1


def test_guard_refuses_payload_nested_beyond_the_depth_bound() -> None:
    payload = '{"arguments": ' + "[" * 65 + '"ok"' + "]" * 65 + "}"
    result = _run(payload)
    assert result.returncode == 1
    assert "nests deeper than 64 levels" in result.stderr


@pytest.mark.parametrize("leaf", [IP_192, "ok"], ids=["private address", "clean value"])
def test_guard_refuses_payload_too_deep_for_the_json_decoder(leaf: str) -> None:
    """A payload this deep never passes unscanned, whichever bound refuses it.

    Where json.loads gives up (RecursionError) depends on the interpreter: since Python 3.14
    the bound is the C stack, so one 3.14 build decodes this depth and another does not. The
    scan's depth bound refuses what the decoder accepts.
    """
    depth = 100_000
    payload = '{"arguments": ' + "[" * depth + json.dumps(leaf) + "]" * depth + "}"
    result = _run(payload)
    assert result.returncode == 1
    assert (
        "nests too deeply to decode" in result.stderr
        or "nests deeper than 64 levels" in result.stderr
    )


def test_guard_refuses_a_payload_the_decoder_gives_up_on(monkeypatch, capsys) -> None:
    """The RecursionError branch refuses on every interpreter, not only where the decoder fails."""
    guard = _load_guard()

    def give_up(_raw: str) -> object:
        raise RecursionError

    monkeypatch.setattr(guard.json, "loads", give_up)
    monkeypatch.setattr(guard.sys, "stdin", io.StringIO('{"arguments": []}'))
    assert guard.main() == 1
    assert "nests too deeply to decode" in capsys.readouterr().err


def test_scanner_returns_the_violation_instead_of_exiting() -> None:
    guard = _load_guard()
    assert guard.inspect_text("clean") is None
    assert guard.inspect_text(IP_TEN) == IP_MESSAGE + IP_TEN
    assert guard.scan_payload_values({"a": ["ok", {"b": HOME_PATH}]}) == PATH_MESSAGE + HOME_PATH
    assert guard.scan_payload_values({"a": ["ok", {"b": "fine"}]}) is None


def test_scanner_refuses_payload_beyond_the_value_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_guard()
    monkeypatch.setattr(guard, "MAX_SCAN_VALUES", 4)
    assert guard.scan_payload_values(["a", "b", "c"]) is None
    refusal = guard.scan_payload_values(["a", "b", "c", "d"])
    assert refusal is not None
    assert "more than 4 values" in refusal
