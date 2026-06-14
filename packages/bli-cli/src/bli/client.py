"""TCP クライアント。connection.json/token を解決し HELLO→RPC を行う。spec §5。"""

from __future__ import annotations

import json
import os
import socket
import uuid
from typing import Any

from bli_core import protocol as proto
from bli_core import runtime


class ConnectError(Exception):
    """接続/ハンドシェイク不能（終了コード 3 相当）。"""


class RpcRemoteError(Exception):
    """サーバが error レスポンスを返した（業務エラー）。"""

    def __init__(self, error: dict[str, Any]) -> None:
        self.error = error
        super().__init__(error.get("message", "rpc error"))


def _env_port() -> int | None:
    v = os.environ.get("BLI_PORT")
    return int(v) if v else None


def load_connection(port_override: int | None = None) -> tuple[str, int, str, dict[str, Any]]:
    """接続情報を解決する。優先順: flag > env > connection.json > 既定。"""
    info: dict[str, Any] = {}
    cp = runtime.connection_path()
    if cp.exists():
        try:
            info = json.loads(cp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            info = {}
    host = info.get("host", runtime.DEFAULT_HOST)
    port = port_override or _env_port() or info.get("port") or runtime.DEFAULT_PORT
    token = ""
    tp = runtime.token_path()
    if tp.exists():
        try:
            token = tp.read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
    token = os.environ.get("BLI_TOKEN", token)
    return host, int(port), token, info


def call(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    port: int | None = None,
    request_id: str | None = None,
    timeout: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """HELLO→RPC を1往復し (result, hello_ok) を返す。

    接続不能 → ConnectError、サーバ error → RpcRemoteError。
    timeout 未指定時は CLIENT_READ_TIMEOUT（サーバ DISPATCH_TIMEOUT より長い）を使い、
    サーバが返す retryable な TIMEOUT 応答を取りこぼさないようにする。
    """
    if timeout is None:
        timeout = runtime.CLIENT_READ_TIMEOUT
    host, port_, token, _ = load_connection(port)
    request_id = request_id or str(uuid.uuid4())
    try:
        sock = socket.create_connection((host, port_), timeout=timeout)
    except OSError as e:
        raise ConnectError(f"接続不能 {host}:{port_}: {e}（アドオンが起動していない可能性）") from e

    try:
        sock.settimeout(timeout)
        recv = sock.recv
        sock.sendall(proto.encode_frame(proto.build_hello(token)))
        hello = proto.read_frame(recv)
        if hello.get("type") != "hello-ok":
            if "error" in hello:
                raise RpcRemoteError(hello["error"])
            raise ConnectError("ハンドシェイク失敗（hello-ok ではない応答）")

        sock.sendall(proto.encode_frame(proto.build_request(method, request_id, params or {})))
        resp = proto.read_frame(recv)
        if "error" in resp and resp["error"] is not None:
            raise RpcRemoteError(resp["error"])
        return resp.get("result", {}), hello
    except (ConnectionError, OSError) as e:
        raise ConnectError(f"通信エラー: {e}") from e
    finally:
        try:
            sock.close()
        except OSError:
            pass
