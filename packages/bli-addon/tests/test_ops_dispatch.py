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


def test_material_missing_action_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("material", {"targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_material_bad_action_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("material", {"action": "bogus", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_material_bad_color_vec4_invalid_params():
    # color は VEC4（4要素）。要素不足は型エラーで INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "material",
            {"action": "create", "targets": "Cube", "name": "M", "color": [1, 2, 3]},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_material_nonfinite_color_server_rejected():
    # VEC4 の nan/inf もサーバ側で弾く（色を壊さない・CLI 非経由 RPC 防御）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "material",
            {"action": "create", "targets": "Cube", "name": "M", "color": [float("inf"), 0, 0, 1]},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_material_missing_targets_invalid_params():
    # list でも対象は必要（bpy 到達前に USER_INPUT）
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("material", {"action": "list"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_material_create_missing_name_invalid_params():
    # create には --name が必要（bpy 到達前に USER_INPUT）
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("material", {"action": "create", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_material_color_on_assign_invalid_params():
    # --color は create 専用。assign/list で渡したら silent ignore せず弾く（bpy 到達前）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "material",
            {"action": "assign", "targets": "Cube", "name": "M", "color": [1, 0, 0, 1]},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_modifier_missing_action_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("modifier", {"targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_modifier_bad_action_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("modifier", {"action": "bogus", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_modifier_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("modifier", {"action": "list"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_modifier_add_missing_type_invalid_params():
    # add には --type が必要（bpy 到達前に USER_INPUT）
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("modifier", {"action": "add", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_modifier_add_bad_type_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("modifier", {"action": "add", "targets": "Cube", "type": "BOGUS"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_modifier_add_boolean_missing_with_invalid_params():
    # BOOLEAN の add には --with が必要（bpy 到達前に USER_INPUT）
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("modifier", {"action": "add", "targets": "Cube", "type": "BOOLEAN"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_modifier_add_wrong_type_param_invalid_params():
    # MIRROR に levels（SUBSURF 用）を渡すと silent ignore せず弾く（bpy 到達前）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "modifier",
            {"action": "add", "targets": "Cube", "type": "MIRROR", "levels": 2},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_modifier_remove_missing_name_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("modifier", {"action": "remove", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_modifier_apply_missing_name_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("modifier", {"action": "apply", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_modifier_list_with_type_param_invalid_params():
    # type 別パラメータは add 専用。list で渡したら弾く（bpy 到達前）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("modifier", {"action": "list", "targets": "Cube", "levels": 2}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_modifier_bad_levels_type_invalid_params():
    # levels は INT。非整数は型エラーで INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "modifier",
            {"action": "add", "targets": "Cube", "type": "SUBSURF", "levels": "x"},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_modifier_nonfinite_thickness_server_rejected():
    # FLOAT（thickness）の nan/inf もサーバ側で弾く（既存の有限性チェック）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "modifier",
            {"action": "add", "targets": "Cube", "type": "SOLIDIFY", "thickness": float("inf")},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_modifier_levels_above_max_invalid_params():
    # SUBSURF levels の上限超過は暴走防止のため bpy 到達前に弾く。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "modifier", {"action": "add", "targets": "Cube", "type": "SUBSURF", "levels": 100}, INFO
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_modifier_ratio_out_of_range_invalid_params():
    # DECIMATE ratio は 0..1。範囲外は bpy 到達前に弾く（silent クランプ回避）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "modifier", {"action": "add", "targets": "Cube", "type": "DECIMATE", "ratio": 5.0}, INFO
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_modifier_apply_with_type_param_invalid_params():
    # type 別パラメータは add 専用。apply で渡したら弾く（bpy 到達前）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "modifier", {"action": "apply", "targets": "Cube", "name": "Mirror", "axis": "X"}, INFO
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


# ---- M7 T7.1 mesh（recalc-normals / merge-by-distance）の param 検証（bpy 不要）----


def test_mesh_missing_op_invalid_params():
    # op（必須）が無い → bpy 到達前に INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mesh_bad_op_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "bogus", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mesh_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "recalc-normals"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mesh_recalc_with_distance_invalid_params():
    # distance は merge-by-distance 専用。recalc に渡すと silent ignore せず弾く（bpy 到達前）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "recalc-normals", "targets": "Cube", "distance": 0.1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_merge_with_inside_invalid_params():
    # inside は recalc-normals 専用。merge に渡すと弾く（bpy 到達前）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "merge-by-distance", "targets": "Cube", "inside": True}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_merge_negative_distance_invalid_params():
    # 負の距離は remove_doubles で未定義。0 以上を要求する（bpy 到達前）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "merge-by-distance", "targets": "Cube", "distance": -1.0}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_nonfinite_distance_server_rejected():
    # FLOAT（distance）の nan/inf もサーバ側で弾く（既存の有限性チェック）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "mesh",
            {"op": "merge-by-distance", "targets": "Cube", "distance": float("inf")},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mesh_bad_distance_type_invalid_params():
    # distance は FLOAT。文字列は型エラーで INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "merge-by-distance", "targets": "Cube", "distance": "x"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mesh_make_single_user_not_rejected_as_op_param():
    # make_single_user は共有ガードの knob であり op 専用 param ではない（_ALL_MESH_OP_PARAMS に
    # 含めない）。各 op で USER_INPUT として弾かれず検証を通過し、bpy/bmesh の遅延 import まで
    # 到達する（bpy 不在の pytest では ModuleNotFoundError）。_ALL_MESH_OP_PARAMS 退行ガード。
    cases = [
        {"op": "recalc-normals"},
        {"op": "merge-by-distance"},
        {"op": "extrude", "offset": [0.0, 0.0, 1.0]},
        {"op": "bevel", "width": 0.1},
        {"op": "inset", "thickness": 0.1},
    ]
    for extra in cases:
        params = {"targets": "Cube", "make_single_user": True, **extra}
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("mesh", params, INFO)


# ---- M7 T7.2 mesh（extrude / bevel / inset）の param 検証（bpy 不要）----


def test_mesh_extrude_missing_offset_invalid_params():
    # extrude は offset 必須（無音 no-op を避ける）→ bpy 到達前に USER_INPUT
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "extrude", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_extrude_bad_offset_type_invalid_params():
    # offset は VEC3（3要素）。要素不足は型エラーで INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "extrude", "targets": "Cube", "offset": [1, 2]}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mesh_extrude_nonfinite_offset_server_rejected():
    # nan/inf の offset はサーバ側（schema）で弾く（mesh を壊さない）
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "mesh", {"op": "extrude", "targets": "Cube", "offset": [float("inf"), 0.0, 0.0]}, INFO
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mesh_extrude_wrong_op_param_invalid_params():
    # width は bevel 専用。extrude に渡すと弾く（bpy 到達前）
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "mesh", {"op": "extrude", "targets": "Cube", "offset": [0, 0, 1], "width": 0.2}, INFO
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_bevel_missing_width_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "bevel", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_bevel_negative_width_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "bevel", "targets": "Cube", "width": -0.1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_bevel_segments_out_of_range_invalid_params():
    # segments 上限超過は暴走防止で bpy 到達前に弾く
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "mesh", {"op": "bevel", "targets": "Cube", "width": 0.2, "segments": 1000}, INFO
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_bevel_segments_below_min_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "bevel", "targets": "Cube", "width": 0.2, "segments": 0}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mesh_inset_missing_thickness_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "inset", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_inset_negative_thickness_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "inset", "targets": "Cube", "thickness": -1.0}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_segments_on_non_bevel_op_invalid_params():
    # segments は bevel 専用。extrude/inset に渡すと silent ignore せず弾く（cross-op leak ガード）。
    for extra in ({"op": "extrude", "offset": [0, 0, 1]}, {"op": "inset", "thickness": 0.2}):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("mesh", {"targets": "Cube", "segments": 3, **extra}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT"


def test_mesh_nonfinite_width_thickness_server_rejected():
    # FLOAT（width/thickness）の nan/inf もサーバ側（schema）で弾く。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "bevel", "targets": "Cube", "width": float("inf")}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "inset", "targets": "Cube", "thickness": float("nan")}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
