"""ドメインハンドラ + dispatch ルータ（M3）。spec §6 / methods.md / 付録B。

bpy 系コマンド（scene-info/object-info/set-origin）を `gateway` 経由で実行する。
それ以外（ping/echo 等）は `handlers.dispatch` に委譲する。

- param 検証はサーバ側でも行う（`bli_core.schema.validate_from_dict` → INVALID_PARAMS）。
- required_mode を実行直前に検証する（自動遷移はしない → E_MODE_MISMATCH）。
- `gateway`/`bpy` は **遅延 import**（pytest では bpy が無いため、検証パスだけ到達可能）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bli_core.commands import Command, get_command, load_definitions
from bli_core.errors import (
    RPC_BUSINESS_ERROR,
    RPC_INVALID_PARAMS,
    RPC_METHOD_NOT_FOUND,
    ErrorCategory,
    ErrorCode,
    make_error,
)
from bli_core.protocol import JsonRpcError
from bli_core.schema import validate_from_dict
from bli_core.types import Mode

from . import handlers
from .handlers import ServerInfo

# ---- 共通ヘルパ ----


def _command(name: str) -> Command:
    load_definitions()
    cmd = get_command(name)
    if cmd is None:  # 定義漏れ（コードバグ）
        raise JsonRpcError(RPC_METHOD_NOT_FOUND, f"method not found: {name}")
    return cmd


def _validate(cmd: Command, params: dict[str, Any]) -> None:
    """params を SSOT スキーマで検証する。不正なら INVALID_PARAMS。"""
    errors = validate_from_dict(cmd, params)
    if errors:
        raise JsonRpcError(RPC_INVALID_PARAMS, ErrorCode.INVALID_PARAMS, errors[0])


def _check_mode(cmd: Command, current: str) -> None:
    """required_mode を検証する。不一致は自動遷移せず E_MODE_MISMATCH。"""
    req = cmd.required_mode
    if req is Mode.ANY:
        return
    ok = (req is Mode.OBJECT and current == "OBJECT") or (
        req is Mode.EDIT and current.startswith("EDIT")
    )
    if not ok:
        raise JsonRpcError(
            RPC_BUSINESS_ERROR,
            ErrorCode.E_MODE_MISMATCH,
            make_error(
                ErrorCode.E_MODE_MISMATCH,
                category=ErrorCategory.PRECONDITION,
                retryable=False,
                symptom=f"必要モード {req.value}（現在 {current}）",
                remediation=f"{req.value} モードに切り替えてください（自動遷移はしません）",
            ),
        )


def _ok(
    operation: str,
    data: dict[str, Any],
    *,
    verified: bool = True,
    fingerprint: str | None = None,
    output_ref: str | None = None,
) -> dict[str, Any]:
    """成功レスポンス（data-model §2.5 のエンベロープ）。"""
    return {
        "success": True,
        "operation": operation,
        "verified": verified,
        "fingerprint": fingerprint,
        "output_ref": output_ref,
        "data": data,
    }


# ---- ハンドラ（bpy 系）----


def _scene_info(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("scene-info")
    _validate(cmd, params)
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    data = gateway.scene_summary(int(params.get("depth", 1)))
    return _ok("scene-info", data)


def _object_info(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("object-info")
    _validate(cmd, params)
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]))
    return _ok(
        "object-info", gateway.object_summary(obj), fingerprint=gateway.object_fingerprint(obj)
    )


def _set_origin(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("set-origin")
    _validate(cmd, params)
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]))
    to = str(params["to"])

    # 共有 mesh は明示許可（make_single_user）が無い限り拒否する。
    if gateway.mesh_user_count(obj) >= 2:
        if not bool(params.get("make_single_user", False)):
            raise JsonRpcError(
                RPC_BUSINESS_ERROR,
                ErrorCode.E_PRECONDITION,
                make_error(
                    ErrorCode.E_PRECONDITION,
                    category=ErrorCategory.PRECONDITION,
                    retryable=False,
                    symptom=f"共有 mesh（users={gateway.mesh_user_count(obj)}）です",
                    remediation="--make-single-user を付けて単一ユーザ化を許可してください",
                ),
            )
        gateway.make_single_user_mesh(obj)

    if to == "geometry":
        center = "BOUNDS" if params.get("center") == "bounds" else "MEDIAN"
        gateway.origin_set(
            obj, origin_type="ORIGIN_GEOMETRY", center=center, message="set-origin geometry"
        )
    elif to == "cursor":
        gateway.origin_set(obj, origin_type="ORIGIN_CURSOR", message="set-origin cursor")
    else:  # world（直接行列）
        x = float(params.get("x") or 0.0)
        y = float(params.get("y") or 0.0)
        z = float(params.get("z") or 0.0)
        gateway.set_origin_world(obj, x, y, z)
        gateway.push_undo("set-origin world")

    data = {
        "name": obj.name,
        "to": to,
        "origin_world": gateway.object_summary(obj)["location"],
    }
    return _ok("set-origin", data, fingerprint=gateway.object_fingerprint(obj))


_BPY_HANDLERS: dict[str, Callable[[dict[str, Any], ServerInfo], dict[str, Any]]] = {
    "scene-info": _scene_info,
    "object-info": _object_info,
    "set-origin": _set_origin,
}


def dispatch(method: str, params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """bpy 系は専用ハンドラ、その他は handlers.dispatch に委譲する。"""
    fn = _BPY_HANDLERS.get(method)
    if fn is not None:
        return fn(params, info)
    return handlers.dispatch(method, params, info)
