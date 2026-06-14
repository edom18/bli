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


def test_list_objects_unknown_param_invalid_params():
    # type/regex は任意だが、未知 param は bpy 到達前に INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("list-objects", {"bogus": 1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_list_objects_bad_type_invalid_params():
    # type は STR。非文字列は型エラーで INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("list-objects", {"type": 123}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_transform_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("transform", {}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_transform_bad_mode_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("transform", {"targets": "Cube", "mode": "bogus"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_transform_bad_vec3_invalid_params():
    # location は3要素必須（VEC3）
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("transform", {"targets": "Cube", "location": [1, 2]}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_select_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("select", {}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_transform_no_channels_invalid_params():
    # location/rotation/scale すべて省略は無音 no-op になるため USER_INPUT で弾く（bpy 到達前）
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("transform", {"targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_apply_transform_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("apply-transform", {"targets": "Cube", "bogus": 1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_apply_transform_all_false_invalid_params():
    # 明示的に全 false（生成クライアントの既定埋め）は「適用なし」として弾く（Codex P2）。
    # キー有無で判定するため bpy 到達前に INVALID_PARAMS。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "apply-transform",
            {"targets": "Cube", "location": False, "rotation": False, "scale": False},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_duplicate_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("duplicate", {}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_duplicate_bad_count_type_invalid_params():
    # count は INT。非整数は型エラーで INVALID_PARAMS（bool は int 扱いしない）
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("duplicate", {"targets": "Cube", "count": "x"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_duplicate_count_below_min_invalid_params():
    # count<1 は無音 no-op になるため USER_INPUT で弾く（bpy 到達前）
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("duplicate", {"targets": "Cube", "count": 0}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_duplicate_count_above_max_invalid_params():
    # 暴走防止: 上限超過も bpy 到達前に弾く
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("duplicate", {"targets": "Cube", "count": 100000}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_duplicate_bad_offset_invalid_params():
    # offset は VEC3（3要素）。要素不足は型エラーで INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("duplicate", {"targets": "Cube", "offset": [1, 2]}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_delete_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("delete", {}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_delete_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("delete", {"targets": "Cube", "bogus": 1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_duplicate_linked_bad_type_invalid_params():
    # linked は BOOL。非真偽値は型エラーで INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("duplicate", {"targets": "Cube", "linked": "yes"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_duplicate_nonfinite_offset_server_rejected():
    # CLI 非経由でも nan/inf の offset は **サーバ側**（schema 検証）で弾く（matrix を壊さない）。
    # bpy 到達前に INVALID_PARAMS（USER_INPUT）。
    for bad in (float("inf"), float("nan"), float("-inf")):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("duplicate", {"targets": "Cube", "offset": [bad, 0.0, 0.0]}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, bad
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", bad


def test_set_origin_nonfinite_float_server_rejected():
    # FLOAT パラメータ（set-origin の x）の nan/inf もサーバ側で弾く（同じ防御線）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("set-origin", {"targets": "Cube", "to": "world", "x": float("inf")}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
