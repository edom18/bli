"""プロトコル codec（contracts/protocol.schema.json）。純Python・依存ゼロ。

フレーム: [4byte big-endian uint32 length][UTF-8 JSON body]。
JSON-RPC 2.0 サブセット（通知/バッチ非対応）。HELLO ハンドシェイク。
ソケットに依存せず、bytes I/O コールバックで動作する（テスト容易）。
"""

from __future__ import annotations

import json
import struct
from collections.abc import Callable
from typing import Any

from .errors import (
    RPC_BUSINESS_ERROR,
    RPC_INVALID_REQUEST,
    ErrorObject,
)

PROTOCOL_VERSION = "1.0.0"
MAX_FRAME_BYTES = 16 * 1024 * 1024  # 16 MiB
_HEADER = struct.Struct(">I")


class ProtocolError(Exception):
    """プロトコル違反（フレーミング/サイズ等）。"""


class FrameTooLarge(ProtocolError):
    def __init__(self, size: int) -> None:
        super().__init__(f"frame too large: {size} > {MAX_FRAME_BYTES}")
        self.size = size


class JsonRpcError(Exception):
    """JSON-RPC レベルのエラー。code と任意の ErrorObject を保持。"""

    def __init__(self, code: int, message: str, data: ErrorObject | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# ---- フレーミング ----


def encode_frame(obj: dict[str, Any]) -> bytes:
    """dict を長さ接頭辞付き JSON フレームにエンコードする。"""
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise FrameTooLarge(len(body))
    return _HEADER.pack(len(body)) + body


def recv_exactly(recv: Callable[[int], bytes], n: int) -> bytes:
    """recv(maxbytes)->bytes コールバックから厳密に n バイト読む。

    部分読込・パケット連結に非依存。接続断は ConnectionError。
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed while reading frame")
        buf.extend(chunk)
    return bytes(buf)


def read_frame(recv: Callable[[int], bytes]) -> dict[str, Any]:
    """1 フレームを読み出し dict を返す。"""
    header = recv_exactly(recv, 4)
    (length,) = _HEADER.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise FrameTooLarge(length)
    body = recv_exactly(recv, length)
    return json.loads(body.decode("utf-8"))


# ---- JSON-RPC サブセット ----


def build_request(method: str, id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "id": id, "params": params or {}}


def build_success(id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def build_error(
    id: str | None, code: int, message: str, data: ErrorObject | None = None
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    err["data"] = data.to_dict() if data is not None else None
    return {"jsonrpc": "2.0", "id": id, "error": err}


def parse_request(msg: Any) -> tuple[str, str, dict[str, Any]]:
    """RPC リクエストを検証して (method, id, params) を返す。

    バッチ(list)・通知(id無し)は非対応 → JsonRpcError(-32600)。
    """
    if isinstance(msg, list):
        raise JsonRpcError(RPC_INVALID_REQUEST, "batch is not supported")
    if not isinstance(msg, dict):
        raise JsonRpcError(RPC_INVALID_REQUEST, "request must be an object")
    if msg.get("jsonrpc") != "2.0":
        raise JsonRpcError(RPC_INVALID_REQUEST, "jsonrpc must be '2.0'")
    method = msg.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcError(RPC_INVALID_REQUEST, "method must be a non-empty string")
    rid = msg.get("id")
    if not isinstance(rid, str) or not rid:
        raise JsonRpcError(RPC_INVALID_REQUEST, "id (uuid) is required; notifications unsupported")
    params = msg.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise JsonRpcError(RPC_INVALID_REQUEST, "params must be an object")
    return method, rid, params


def error_response_from(id: str | None, exc: JsonRpcError) -> dict[str, Any]:
    """JsonRpcError から error レスポンス dict を作る。"""
    code = exc.code if exc.code else RPC_BUSINESS_ERROR
    return build_error(id, code, exc.message, exc.data)


# ---- HELLO ハンドシェイク ----


def build_hello(token: str, client: str = "bli-cli") -> dict[str, Any]:
    return {
        "type": "hello",
        "token": token,
        "protocol_version": PROTOCOL_VERSION,
        "client": client,
    }


def build_hello_ok(
    blender_version: str,
    schema_hash: str,
    session_uid: str,
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "hello-ok",
        "protocol_version": PROTOCOL_VERSION,
        "blender_version": blender_version,
        "schema_hash": schema_hash,
        "session_uid": session_uid,
        "capabilities": capabilities or [],
    }


def is_hello(msg: Any) -> bool:
    return isinstance(msg, dict) and msg.get("type") == "hello"


def major(version: str) -> int:
    """SemVer の MAJOR を返す（不正なら -1）。"""
    try:
        return int(version.split(".", 1)[0])
    except (ValueError, AttributeError):
        return -1
