"""プロトコル codec のユニット（L1）。"""

from __future__ import annotations

import struct

import pytest

from bli_core import protocol as proto
from bli_core.errors import RPC_INVALID_REQUEST


def _byte_at_a_time_recv(data: bytes):
    """1バイトずつ返す recv（部分読込・連結耐性の検証用）。"""
    state = {"i": 0}

    def recv(n: int) -> bytes:
        i = state["i"]
        chunk = data[i : i + 1]
        state["i"] = i + len(chunk)
        return chunk

    return recv


def test_frame_roundtrip_partial_reads():
    obj = {"jsonrpc": "2.0", "method": "ping", "id": "abc", "params": {"k": "値"}}
    data = proto.encode_frame(obj)
    got = proto.read_frame(_byte_at_a_time_recv(data))
    assert got == obj


def test_read_frame_oversize_header_raises():
    header = struct.pack(">I", proto.MAX_FRAME_BYTES + 1)
    with pytest.raises(proto.FrameTooLarge):
        proto.read_frame(_byte_at_a_time_recv(header))


def test_encode_oversize_raises(monkeypatch):
    monkeypatch.setattr(proto, "MAX_FRAME_BYTES", 8)
    with pytest.raises(proto.FrameTooLarge):
        proto.encode_frame({"x": "0123456789"})


def test_recv_exactly_connection_closed():
    def recv(_n):
        return b""

    with pytest.raises(ConnectionError):
        proto.recv_exactly(recv, 4)


def test_parse_request_valid():
    method, rid, params = proto.parse_request(
        {"jsonrpc": "2.0", "method": "ping", "id": "u1", "params": {"a": 1}}
    )
    assert (method, rid, params) == ("ping", "u1", {"a": 1})


def test_parse_request_rejects_batch():
    with pytest.raises(proto.JsonRpcError) as ei:
        proto.parse_request([{"jsonrpc": "2.0", "method": "ping", "id": "u1"}])
    assert ei.value.code == RPC_INVALID_REQUEST


def test_parse_request_rejects_notification():
    with pytest.raises(proto.JsonRpcError):
        proto.parse_request({"jsonrpc": "2.0", "method": "ping"})  # id 無し


def test_parse_request_rejects_bad_jsonrpc():
    with pytest.raises(proto.JsonRpcError):
        proto.parse_request({"jsonrpc": "1.0", "method": "ping", "id": "u1"})


def test_hello_helpers():
    h = proto.build_hello("tok", client="x")
    assert proto.is_hello(h)
    assert h["protocol_version"] == proto.PROTOCOL_VERSION
    ok = proto.build_hello_ok("5.0.0", "deadbeef", "sess-1", ["wm.stl_export"])
    assert ok["type"] == "hello-ok"
    assert ok["capabilities"] == ["wm.stl_export"]


def test_major():
    assert proto.major("5.0.0") == 5
    assert proto.major("1.2.3") == 1
    assert proto.major("bad") == -1
