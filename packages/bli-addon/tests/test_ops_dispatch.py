"""ops.dispatch のルーティング + サーバ側 param 検証のユニット（L1/L3）。

bpy を必要としない経路のみを検証する:
- 非 bpy メソッド（ping/echo/unknown）の委譲。
- bpy 系ハンドラの **param 検証は bpy import より前**に走るため、
  不正 params は bpy 無しで INVALID_PARAMS を返せる。
"""

from __future__ import annotations

import pytest

from bli_addon import ops
from bli_addon.handlers import ServerInfo
from bli_core.errors import RPC_INVALID_PARAMS, RPC_METHOD_NOT_FOUND, ErrorCode
from bli_core.protocol import JsonRpcError

INFO = ServerInfo("5.0.1-test", "deadbeef", ["wm.stl_export"])


def test_routes_ping_to_handlers():
    result = ops.dispatch("ping", {}, INFO)
    assert result["success"] is True
    assert result["operation"] == "ping"
    assert result["data"]["blender_version"] == "5.0.1-test"


def test_routes_echo_to_handlers():
    result = ops.dispatch("echo", {"k": "値"}, INFO)
    assert result["data"]["echo"] == {"k": "値"}


def test_unknown_method_not_found():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("does-not-exist", {}, INFO)
    assert ei.value.code == RPC_METHOD_NOT_FOUND


def test_set_origin_missing_required_invalid_params():
    # targets/to が無い → bpy に到達する前に INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("set-origin", {}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.message == ErrorCode.INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_set_origin_bad_enum_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("set-origin", {"targets": "Cube", "to": "bogus"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_object_info_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("object-info", {}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_scene_info_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("scene-info", {"nope": 1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
