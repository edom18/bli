"""コマンドハンドラ（M2 スケルトン: ping/echo）。bpy 非依存。

M3 で bpy ディスパッチ（bpy.app.timers 経由の実行）に差し替える。
ここでは walking skeleton の疎通用に最小実装する。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bli_core.errors import RPC_METHOD_NOT_FOUND
from bli_core.protocol import JsonRpcError


@dataclass
class ServerInfo:
    blender_version: str
    schema_hash: str
    capabilities: list[str] = field(default_factory=list)


Handler = Callable[[dict[str, Any], ServerInfo], dict[str, Any]]


def _ping(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    return {
        "success": True,
        "operation": "ping",
        "data": {
            "blender_version": info.blender_version,
            "schema_hash": info.schema_hash,
            "capabilities": info.capabilities,
        },
    }


def _echo(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    return {"success": True, "operation": "echo", "data": {"echo": params}}


HANDLERS: dict[str, Handler] = {
    "ping": _ping,
    "echo": _echo,
}


def dispatch(method: str, params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """method を解決して実行する。未知の method は METHOD_NOT_FOUND。"""
    handler = HANDLERS.get(method)
    if handler is None:
        raise JsonRpcError(RPC_METHOD_NOT_FOUND, f"method not found: {method}")
    return handler(params, info)
