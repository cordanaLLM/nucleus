"""scripts/hindsight_mcp_server.py: redaction, the offline buffer, the upstream calls, the protocol.

The server is wired into `.agents/mcp_config.json` and redacts private addresses and workstation
paths before anything leaves the machine. These tests are hermetic: a stub HTTP server on the
loopback interface stands in for Hindsight, a closed port for an unreachable one, and the buffer
lives in a temporary directory.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "hindsight_mcp_server.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import hindsight_mcp_server as hms  # noqa: E402

PRIVATE = "reached 10.1.2.3 and 192.168.0.9 from /home/planted-user/src"


class Upstream:
    """A stub Hindsight: answers from `routes` and records every request it receives."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], tuple[int, object]] = {}
        self.requests: list[tuple[str, str, object]] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                upstream.requests.append((self.command, self.path, body))
                status, reply = upstream.routes.get((self.command, self.path), (404, {}))
                raw = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _answer

            def log_message(self, *_args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def buffer(tmp_path, monkeypatch):
    monkeypatch.setattr(hms, "LOCAL_CACHE_DIR", tmp_path / "memory")
    monkeypatch.setattr(hms, "LOCAL_CACHE_FILE", tmp_path / "memory" / "buffer.json")
    monkeypatch.setattr(hms, "REQUEST_TIMEOUT", 5)
    return tmp_path / "memory" / "buffer.json"


@pytest.fixture
def upstream(buffer, monkeypatch):
    stub = Upstream()
    monkeypatch.setattr(hms, "HINDSIGHT_URL", stub.url)
    yield stub
    stub.close()


@pytest.fixture
def offline(buffer, monkeypatch):
    monkeypatch.setattr(hms, "HINDSIGHT_URL", _closed_port_url())
    return buffer


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10.0.0.1", "[REDACTED-RFC1918-IP]"),
        ("192.168.178.20", "[REDACTED-RFC1918-IP]"),
        ("172.16.0.1 172.31.255.1", "[REDACTED-RFC1918-IP] [REDACTED-RFC1918-IP]"),
        ("172.15.0.1 172.32.0.1 192.0.2.7", "172.15.0.1 172.32.0.1 192.0.2.7"),
        ("/home/planted-user/x", "[REDACTED-USER-PATH]/x"),
        ("/Users/planted/x", "[REDACTED-USER-PATH]/x"),
        ("C:\\Users\\planted\\x", "[REDACTED-USER-PATH]\\x"),
        ("/srv/kernel.example.com", "/srv/kernel.example.com"),
    ],
)
def test_private_addresses_and_workstation_paths_are_redacted(text, expected):
    assert hms.sanitize_privacy(text) == expected


def test_retain_sends_only_redacted_text_upstream(upstream):
    upstream.routes[("POST", "/v1/default/banks/nucleus/retain")] = (200, {"document_id": "d1"})
    reply = hms.tool_hindsight_retain(PRIVATE, context="seen on 172.20.0.4")
    assert "Retained fact in bank 'nucleus' (document_id: d1)" in reply
    [(_, _, body)] = upstream.requests
    sent = json.dumps(body)
    assert "10.1.2.3" not in sent and "192.168" not in sent and "planted-user" not in sent
    assert "172.20" not in sent and "[REDACTED-RFC1918-IP]" in sent


def test_retain_refuses_empty_content_and_sends_nothing(upstream):
    assert hms.tool_hindsight_retain("   ").startswith("Error: Memory content cannot be empty")
    assert upstream.requests == []


def test_an_unreachable_upstream_buffers_the_redacted_fact(offline):
    reply = hms.tool_hindsight_retain(PRIVATE, bank_id="b1")
    assert "[Hindsight Offline Buffer]" in reply and "pending_sync: true" in reply
    [entry] = json.loads(offline.read_text(encoding="utf-8"))
    assert entry["bank_id"] == "b1" and entry["pending_sync"] is True
    assert "10.1.2.3" not in entry["content"] and "planted-user" not in entry["content"]


def test_an_upstream_error_status_buffers_the_fact(upstream, buffer):
    upstream.routes[("POST", "/v1/default/banks/nucleus/retain")] = (500, {"error": "down"})
    assert "[Hindsight Offline Buffer]" in hms.tool_hindsight_retain("a fact")
    assert len(json.loads(buffer.read_text(encoding="utf-8"))) == 1


def test_a_later_retain_flushes_only_its_own_banks_buffered_facts(buffer, monkeypatch):
    monkeypatch.setattr(hms, "HINDSIGHT_URL", _closed_port_url())
    hms.tool_hindsight_retain("fact for a/b", bank_id="a/b")
    hms.tool_hindsight_retain("fact for other", bank_id="other")
    stub = Upstream()
    try:
        monkeypatch.setattr(hms, "HINDSIGHT_URL", stub.url)
        stub.routes[("POST", "/v1/default/banks/a%2Fb/retain")] = (200, {"id": "x"})
        stub.routes[("POST", "/v1/default/banks/other/retain")] = (200, {"id": "y"})
        hms.tool_hindsight_retain("new fact for a/b", bank_id="a/b")
        sent = [(path, body["content"]) for _, path, body in stub.requests]
    finally:
        stub.close()
    assert sent == [
        ("/v1/default/banks/a%2Fb/retain", "new fact for a/b"),
        ("/v1/default/banks/a%2Fb/retain", "fact for a/b"),
    ]
    pending = {e["bank_id"]: e["pending_sync"] for e in json.loads(buffer.read_text("utf-8"))}
    assert pending == {"a/b": False, "other": True}


@pytest.mark.parametrize(("top_k", "k"), [(5, 5), (0, 1), (99, 20)])
def test_recall_asks_upstream_for_a_bounded_number_and_lists_the_results(upstream, top_k, k):
    upstream.routes[("POST", "/v1/default/banks/nucleus/recall")] = (
        200,
        {"results": [{"text": "first", "score": 0.9}, {"content": "second", "score": 0.5}]},
    )
    reply = hms.tool_hindsight_recall(f"where did {PRIVATE}", top_k=top_k)
    [(_, _, body)] = upstream.requests
    assert body["k"] == k and "10.1.2.3" not in body["query"]
    assert reply.splitlines() == [
        "Found 2 memories in bank 'nucleus':",
        "1. first (score: 0.9)",
        "2. second (score: 0.5)",
    ]


def test_recall_offline_searches_only_the_banks_buffered_facts(offline):
    hms.tool_hindsight_retain("kernel forge fact", bank_id="b1")
    hms.tool_hindsight_retain("kernel forge elsewhere", bank_id="b2")
    reply = hms.tool_hindsight_recall("FORGE", bank_id="b1")
    assert "Found 1 locally buffered facts in 'b1'" in reply
    assert "kernel forge fact" in reply and "elsewhere" not in reply
    assert "No matching buffered facts" in hms.tool_hindsight_recall("absent", bank_id="b1")


def test_reflect_status_and_banks_read_the_upstream_answer(upstream, buffer):
    upstream.routes[("POST", "/v1/default/banks/nucleus/reflect")] = (200, {"operation_id": "op"})
    upstream.routes[("GET", "/v1/default/banks")] = (200, {"banks": [{"bank_id": "n"}, "m"]})
    assert "(operation: op)" in hms.tool_hindsight_reflect()
    status = hms.tool_hindsight_status()
    assert "ONLINE" in status and "Active Banks: 2" in status
    assert hms.tool_hindsight_list_banks().splitlines() == [
        "Available Hindsight memory banks (2):",
        "- n",
        "- m",
    ]


def test_reflect_status_and_banks_say_when_upstream_is_unreachable(offline):
    assert "Reflection skipped (HTTP 0)" in hms.tool_hindsight_reflect()
    assert "OFFLINE / DISCONNECTED (HTTP 0)" in hms.tool_hindsight_status()
    assert hms.tool_hindsight_list_banks().startswith("Unable to list banks")


def test_a_url_that_is_not_http_is_refused(monkeypatch):
    monkeypatch.setattr(hms, "HINDSIGHT_URL", "file:///etc")
    with pytest.raises(ValueError, match="Prohibited URL scheme"):
        hms.http_api("GET", "/v1/default/banks")


def test_an_https_url_is_spoken_to_over_tls(monkeypatch):
    opened = []

    class Refuse:
        def __init__(self, host, port, timeout):
            opened.append((type(self).__name__, host, port))
            raise OSError("planted: no network in this test")

    monkeypatch.setattr(hms.http.client, "HTTPSConnection", type("HTTPS", (Refuse,), {}))
    monkeypatch.setattr(hms.http.client, "HTTPConnection", type("HTTP", (Refuse,), {}))
    monkeypatch.setattr(hms, "HINDSIGHT_URL", "https://memory.example.com")
    status, body = hms.http_api("GET", "/v1/default/banks")
    assert opened == [("HTTPS", "memory.example.com", 443)]
    assert status == 0 and "planted" in body["error"]


def test_every_listed_tool_has_a_handler(offline):
    for tool in hms.get_tool_definitions():
        assert not hms.handle_tool_call(tool["name"], {"query": "q", "content": "c"}).startswith(
            "Error: Unknown tool"
        ), tool["name"]
    assert hms.handle_tool_call("planted_tool", {}) == "Error: Unknown tool 'planted_tool'"


def test_the_stdio_loop_speaks_json_rpc(tmp_path):
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "planted"}},
        {"jsonrpc": "2.0", "id": 5, "method": "planted/method"},
    ]
    stdin = "\n".join(json.dumps(line) for line in lines) + "\n\nnot json\n"
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin", "HINDSIGHT_URL": _closed_port_url()},
    )
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [reply.get("id") for reply in replies] == [1, 2, 3, 4, 5, None]
    assert replies[0]["result"]["serverInfo"]["name"] == "lusoris-hindsight-mcp"
    assert replies[1]["result"] == {}
    names = {tool["name"] for tool in replies[2]["result"]["tools"]}
    assert names == {
        "hindsight_recall",
        "hindsight_retain",
        "hindsight_reflect",
        "hindsight_status",
        "hindsight_list_banks",
    }
    assert replies[3]["result"]["content"][0]["text"] == "Error: Unknown tool 'planted'"
    assert replies[4]["error"]["code"] == -32601
    assert replies[5]["error"]["code"] == -32700
