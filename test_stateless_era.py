"""Era wire suite for the 2026-07-28 stateless protocol (migration card A.2, T03).

Subprocess-driven, network-free: every case spawns server.py and drives it over its
stdio pipes exactly as a client would. The suite pins the stateless 2026-07-28 wire
contract, including notification correlation and explicit null request ids.
"""
import json
import os
import select
import subprocess

import pytest

# Production interpreter (the live config pins /usr/bin/python3 — REFERENCE recipe 7),
# NOT the pytest runner's venv python.
PROD_PY = "/usr/bin/python3"

# Derived from __file__, never hardcoded: T06's fresh-clone gate must run the CLONED
# repo's server. This file sits at the repo ROOT, so one dirname suffices (do not
# cross-copy scripts/golden_capture.py's double dirname — different file depth).
SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "just-eat.tools.json")

# Deadline helper is mandatory (F9): pytest-timeout is NOT installed in the named
# runner, and readline without a deadline wedges the whole run whenever the legacy
# server stays silent. A timeout reads as a test failure (caller asserts on None).
READ_TIMEOUT = 5


def read_line_with_timeout(f, sec=READ_TIMEOUT):
    """One line from f, or None if nothing arrives within `sec`."""
    ready, _, _ = select.select([f], [], [], sec)
    if not ready:
        return None
    return f.readline()


def spawn():
    """Text-mode pipes for the wire tests; stderr to DEVNULL (server logs never gate)."""
    return subprocess.Popen(
        [PROD_PY, SERVER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True,
    )


def rpc(p, obj):
    """Write one JSON-RPC line, flush, read the next line with a deadline, parse."""
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()
    line = read_line_with_timeout(p.stdout)
    if line is None or line == "":
        return None  # server stayed silent (or closed) — a test failure, asserted below
    return json.loads(line)


def discover_req(rid=1):
    return {"jsonrpc": "2.0", "id": rid, "method": "server/discover",
            "params": {"clientInfo": {"name": "era-suite"}, "protocolVersions": ["2026-07-28"]}}


# ────────────────────────────── era ──────────────────────────────

def test_discover_era_shape():
    p = spawn()
    try:
        resp = rpc(p, discover_req(1))
        assert resp is not None, "server/discover unanswered (legacy server is silent)"
        result = resp["result"]
        assert result["supportedVersions"] == ["2026-07-28"]
        assert "tools" in result["capabilities"]
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"
    finally:
        p.kill(); p.wait()


def test_discover_paramless():
    """§2: the request may arrive with no params at all; must answer either way."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 2, "method": "server/discover"})
        assert resp is not None, "paramless server/discover unanswered"
        result = resp["result"]
        assert result["supportedVersions"] == ["2026-07-28"]
        assert "tools" in result["capabilities"]
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"
    finally:
        p.kill(); p.wait()


def test_initialize_returns_32601_and_never_hangs_or_closes():
    """D2: no initialize branch; the catch-all rejects it and the loop keeps running."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 3, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05",
                                  "capabilities": {}, "clientInfo": {"name": "era-suite"}}})
        assert resp is not None, "initialize hung"
        assert resp["id"] == 3
        assert resp["error"]["code"] == -32601
        # same pipes stay usable: discover must now answer
        resp2 = rpc(p, discover_req(4))
        assert resp2 is not None, "stdout closed or loop dead after initialize rejection"
        assert resp2["id"] == 4
        assert resp2["result"]["supportedVersions"] == ["2026-07-28"]
    finally:
        p.kill(); p.wait()


def test_notifications_initialized_swallowed():
    p = spawn()
    try:
        p.stdin.write(json.dumps({"jsonrpc": "2.0",
                                  "method": "notifications/initialized"}) + "\n")
        p.stdin.flush()
        resp = rpc(p, {"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}})
        assert resp is not None
        assert resp["id"] == 5  # no phantom reply to the notification
        assert "result" in resp
    finally:
        p.kill(); p.wait()


def test_tools_list_triple_and_no_output_schema():
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}})
        assert resp is not None
        result = resp["result"]
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"
        assert len(result["tools"]) == 6
        for tool in result["tools"]:
            assert "outputSchema" not in tool  # §4 trap: would force structuredContent
    finally:
        p.kill(); p.wait()


def test_tools_list_golden_byte_identity():
    """D4 freeze: name+description+inputSchema byte-identical to the committed golden.
    Loads unconditionally (recipe 3 — no skipif), via a __file__-derived path."""
    with open(GOLDEN, encoding="utf-8") as f:
        golden = json.load(f)
    assert golden, "golden must not be empty"
    assert not any("outputSchema" in t for t in golden)  # none today; assert it
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}})
        assert resp is not None
        tools = resp["result"]["tools"]
        assert len(tools) == len(golden)
        for want in golden:
            got = [t for t in tools if t.get("name") == want["name"]]
            assert len(got) == 1, f"tool {want['name']!r} missing or duplicated"
            projection = {"name": got[0]["name"],
                          "description": got[0].get("description"),
                          "inputSchema": got[0]["inputSchema"]}
            want_projection = {"name": want["name"],
                               "description": want["description"],
                               "inputSchema": want["inputSchema"]}
            assert json.dumps(projection, sort_keys=True) == json.dumps(want_projection, sort_keys=True)
    finally:
        p.kill(); p.wait()


def test_tools_call_invalid_postcode_shape():
    """Local short-circuit (verified in T02) — no network on this path."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                       "params": {"name": "search_restaurants",
                                  "arguments": {"postcode": "!!!"}}})
        assert resp is not None
        result = resp["result"]
        assert result["content"][0]["text"] == "Error: invalid UK postcode format"
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"
    finally:
        p.kill(); p.wait()


def test_tools_call_unknown_tool():
    """R3: legacy returns this as plain text, no isError — asserted around, never
    encoded server-side (tool errors are strings here, not {"error": ...} dicts)."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                       "params": {"name": "bogus", "arguments": {}}})
        assert resp is not None
        result = resp["result"]
        assert result["content"][0]["text"] == "Unknown tool: bogus"
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"
    finally:
        p.kill(); p.wait()


def test_tools_call_missing_params_is_32602():
    """§5: invalid-params is checked upfront, never allowed to surface as -32603."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 12, "method": "tools/call"})
        assert resp is not None
        assert resp["id"] == 12
        assert resp["error"]["code"] == -32602
    finally:
        p.kill(); p.wait()


def test_ping_answers_empty():
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 10, "method": "ping", "params": {}})
        assert resp is not None
        assert resp["result"] == {}  # §6: the {} form (lenient EmptyResult tolerates it)
    finally:
        p.kill(); p.wait()


def test_unknown_method_32601():
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 11, "method": "resources/list", "params": {}})
        assert resp is not None, "no catch-all: resources/list left unanswered"
        assert resp["id"] == 11
        assert resp["error"]["code"] == -32601
    finally:
        p.kill(); p.wait()


def test_id_less_unknown_is_silent():
    """§1/§3: a no-id unknown method is a notification — never respond to it."""
    p = spawn()
    try:
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "foo/bar"}) + "\n")
        p.stdin.flush()
        resp = rpc(p, {"jsonrpc": "2.0", "id": 13, "method": "ping", "params": {}})
        assert resp is not None
        assert resp["id"] == 13  # next response is the ping's — no phantom reply to foo/bar
    finally:
        p.kill(); p.wait()


@pytest.mark.parametrize("method, params", [
    ("server/discover", {}),
    ("tools/list", {}),
    ("tools/call", {"name": "bogus", "arguments": {}}),
    ("ping", {}),
])
def test_id_less_known_method_is_silent_and_does_not_displace_next_response(method, params):
    """JSON-RPC notifications stay silent before every known-method dispatch path."""
    p = spawn()
    try:
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        p.stdin.write(json.dumps(notification) + "\n")
        p.stdin.flush()
        resp = rpc(p, {"jsonrpc": "2.0", "id": 17, "method": "ping", "params": {}})
        assert resp is not None
        assert resp["id"] == 17
        assert resp["result"] == {}
    finally:
        p.kill(); p.wait()


@pytest.mark.parametrize("method, params", [
    ("server/discover", {}),
    ("tools/list", {}),
    ("tools/call", {"name": "bogus", "arguments": {}}),
    ("ping", {}),
])
def test_explicit_null_id_receives_response(method, params):
    """An explicitly included JSON null is a request, unlike an absent id member."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": None, "method": method, "params": params})
        assert resp is not None
        assert "id" in resp
        assert resp["id"] is None
        if method == "tools/call":
            assert resp["result"]["content"][0]["text"] == "Unknown tool: bogus"
        else:
            assert "result" in resp
    finally:
        p.kill(); p.wait()


# ────────────────────────── regression ──────────────────────────
# REFERENCE §7 pinned shapes.

def test_garbage_lines_do_not_kill_the_server():
    p = spawn()
    try:
        p.stdin.write("not json\n")
        p.stdin.write("{broken,,,\n")
        p.stdin.write("\n")
        p.stdin.flush()
        resp = rpc(p, discover_req(14))
        assert resp is not None, "server died or stalled after garbage lines"
        assert resp["id"] == 14
        assert resp["result"]["supportedVersions"] == ["2026-07-28"]
        assert p.poll() is None
    finally:
        p.kill(); p.wait()


def test_non_dict_json_lines_do_not_kill_the_server():
    p = spawn()
    try:
        for line in ('5\n', 'null\n', '[1,2]\n', '"str"\n'):
            p.stdin.write(line)
        p.stdin.flush()
        resp = rpc(p, {"jsonrpc": "2.0", "id": 16, "method": "tools/list", "params": {}})
        assert resp is not None, "server died or stalled on non-dict JSON lines"
        assert resp["id"] == 16
        assert "result" in resp
        assert p.poll() is None
    finally:
        p.kill(); p.wait()


def test_null_method_does_not_crash():
    """§1: non-string method must route as unknown-method, not None.startswith crash."""
    p = spawn()
    try:
        resp = rpc(p, {"jsonrpc": "2.0", "id": 15, "method": None})
        assert resp is not None, "server crashed on null method"
        assert resp["id"] == 15
        assert resp["error"]["code"] == -32601
        assert p.poll() is None
    finally:
        p.kill(); p.wait()


def test_binary_garbage_line_does_not_kill_the_server():
    """REFERENCE §7 skeleton (canonical proof: transitous 05ed373), copied verbatim;
    the two raw p.stdout.readline() reads are wrapped in the deadline helper because
    the silent legacy server would otherwise block forever (timeout wrapper, not a
    skeleton deviation; byte-match preserved for the asserts). Pins the §1
    sys.stdin.reconfigure(errors="replace") guard + §7 exit-on-EOF — both arrive in T04."""
    discover_line = json.dumps(discover_req(1))
    tools_list_line = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    p = subprocess.Popen([PROD_PY, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    try:
        p.stdin.write(b"\xff\xfe\x00garbage\n")                        # G
        p.stdin.write(discover_line.encode() + b"\n"); p.stdin.flush()  # D
        raw = read_line_with_timeout(p.stdout)
        assert raw, "server died or stalled on pre-stream invalid UTF-8"
        resp = json.loads(raw)
        assert resp["id"] == 1 and resp["result"]["supportedVersions"] == ["2026-07-28"]
        p.stdin.write(b"\x00\xff\n")                                    # G
        p.stdin.write(tools_list_line.encode() + b"\n"); p.stdin.flush()  # L
        raw2 = read_line_with_timeout(p.stdout)
        assert raw2, "server died on mid-stream invalid UTF-8"
        resp2 = json.loads(raw2)
        assert resp2["id"] == 2 and "result" in resp2
        assert p.poll() is None
        p.stdin.close(); assert p.wait(timeout=5) == 0                  # clean EOF exit
    finally:
        if p.poll() is None: p.kill(); p.wait()


def test_exit_on_stdin_eof_rc0():
    """§7: exit only on EOF, interpreter exit code 0."""
    p = spawn()
    try:
        p.stdin.close()
        assert p.wait(timeout=5) == 0
    finally:
        if p.poll() is None:
            p.kill(); p.wait()
