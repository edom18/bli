"""M2 walking skeleton の E2E（L3）。CLI クライアント ⇄ アドオンサーバ（bpy 不要）。

CLI→HELLO→ping(echo) 疎通、認証失敗、SESSION_BUSY、冪等、未知メソッドを検証する。
"""

from __future__ import annotations

import socket

import pytest

from bli import client
from bli_addon import server as srv_mod
from bli_core import protocol as proto
from bli_core import runtime


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("BLI_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("BLI_TOKEN", raising=False)
    monkeypatch.delenv("BLI_PORT", raising=False)
    s = srv_mod.start(
        blender_version="5.0.1-test",
        schema_hash="deadbeef0123",
        capabilities=["wm.stl_export"],
        host="127.0.0.1",
        port=0,
    )
    try:
        yield s
    finally:
        srv_mod.stop()


def test_ping_e2e(server):
    result, hello = client.call("ping")  # connection.json から解決
    assert hello["type"] == "hello-ok"
    assert hello["blender_version"] == "5.0.1-test"
    assert hello["schema_hash"] == "deadbeef0123"
    assert hello["capabilities"] == ["wm.stl_export"]
    assert result["success"] is True
    assert result["data"]["blender_version"] == "5.0.1-test"


def test_echo_e2e(server):
    result, _ = client.call("echo", {"k": "値", "n": 1})
    assert result["data"]["echo"] == {"k": "値", "n": 1}


def test_idempotent_same_id(server):
    r1, _ = client.call("echo", {"x": 1}, request_id="fixed-id-1")
    r2, _ = client.call("echo", {"x": 999}, request_id="fixed-id-1")
    # 同一 id の再送は再実行されず初回結果を返す
    assert r1 == r2
    assert r2["data"]["echo"] == {"x": 1}


def test_method_not_found(server):
    with pytest.raises(client.RpcRemoteError) as ei:
        client.call("does-not-exist")
    assert "does-not-exist" in str(ei.value)


def test_auth_failed(server, monkeypatch):
    monkeypatch.setenv("BLI_TOKEN", "wrong-token")
    with pytest.raises(client.RpcRemoteError) as ei:
        client.call("ping")
    assert ei.value.error["message"] == "AUTH_FAILED"


def test_session_busy(server):
    host, port, token, _ = client.load_connection()
    holder = socket.create_connection((host, port), timeout=5)
    try:
        holder.sendall(proto.encode_frame(proto.build_hello(token)))
        hello = proto.read_frame(holder.recv)
        assert hello["type"] == "hello-ok"  # セッション確立・保持
        # 2本目はを即拒否
        with pytest.raises(client.RpcRemoteError) as ei:
            client.call("ping")
        assert ei.value.error["message"] == "SESSION_BUSY"
    finally:
        holder.close()


def test_http_like_rejected(server):
    """HTTP 様式（巨大 length 接頭辞）は即切断される。"""
    host, port, _token, _ = client.load_connection()
    sock = socket.create_connection((host, port), timeout=5)
    try:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        # サーバは frame too large 等で即 close。graceful close なら空 recv、
        # Windows では RST(ConnectionReset) になり得るため両方を「切断」として許容。
        sock.settimeout(5)
        try:
            data = sock.recv(16)
        except (ConnectionResetError, ConnectionError):
            data = b""
        assert data == b""  # 応答なしで切断
    finally:
        sock.close()


def test_runtime_files_written(server, tmp_path):
    assert runtime.connection_path().exists()
    assert runtime.token_path().exists()


def test_request_status_unknown_id(server):
    result, _ = client.call("request-status", {"id": "no-such-id"})
    assert result["operation"] == "request-status"
    assert result["data"]["known"] is False
    assert result["data"]["state"] == "UNKNOWN"
    assert result["data"]["result"] is None


def test_request_status_after_completed_request(server):
    # 先に echo を1回確定させ、その id の決着を後追い取得する
    rid = "tracked-echo-id"
    client.call("echo", {"v": 1}, request_id=rid)
    result, _ = client.call("request-status", {"id": rid})
    assert result["data"]["known"] is True
    assert result["data"]["state"] == "DONE"
    # 保存済みレスポンスから元の echo 結果が回収できる
    assert result["data"]["result"]["result"]["data"]["echo"] == {"v": 1}


def test_request_status_missing_id(server):
    with pytest.raises(client.RpcRemoteError) as ei:
        client.call("request-status", {})
    assert ei.value.error["message"] == "INVALID_PARAMS"


def test_request_status_works_while_session_busy(server):
    """別セッションがロック保持中でも request-status は応答できる（spec §7 後追い回収）。

    通常コマンドは SESSION_BUSY のまま。これが Codex P2 指摘の修正点。
    """
    host, port, token, _ = client.load_connection()
    holder = socket.create_connection((host, port), timeout=5)
    try:
        holder.sendall(proto.encode_frame(proto.build_hello(token)))
        assert proto.read_frame(holder.recv)["type"] == "hello-ok"  # holder がセッション保持

        # request-status は lock-free → busy 中でも成功する
        result, _ = client.call("request-status", {"id": "whatever"})
        assert result["operation"] == "request-status"
        assert result["data"]["known"] is False

        # ロックを要する通常コマンドは従来どおり SESSION_BUSY
        with pytest.raises(client.RpcRemoteError) as ei:
            client.call("ping")
        assert ei.value.error["message"] == "SESSION_BUSY"
    finally:
        holder.close()
