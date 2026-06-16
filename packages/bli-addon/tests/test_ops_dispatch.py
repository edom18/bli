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
        {"op": "boolean", "operation": "UNION", "with_object": "Cube2"},
        {"op": "decimate", "ratio": 0.5},
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


# ---- M7 T7.3 mesh（boolean / decimate）の param 検証（bpy 不要）----


def test_mesh_boolean_missing_operation_invalid_params():
    # boolean は operation 必須（with_object はあっても）→ bpy 到達前に USER_INPUT
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "boolean", "targets": "Cube", "with_object": "Cube2"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_boolean_missing_with_invalid_params():
    # boolean は with_object 必須 → bpy 到達前に USER_INPUT
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "boolean", "targets": "Cube", "operation": "UNION"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_boolean_bad_operation_invalid_params():
    # operation は ENUM。範囲外は schema 検証で INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "mesh",
            {"op": "boolean", "targets": "Cube", "operation": "BOGUS", "with_object": "Cube2"},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mesh_boolean_wrong_op_param_invalid_params():
    # ratio は decimate 専用。boolean に渡すと silent ignore せず弾く（cross-op leak ガード）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "mesh",
            {
                "op": "boolean",
                "targets": "Cube",
                "operation": "UNION",
                "with_object": "Cube2",
                "ratio": 0.5,
            },
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_decimate_missing_ratio_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "decimate", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_mesh_decimate_ratio_out_of_range_invalid_params():
    # ratio は 0..1。範囲外は bpy 到達前に弾く（silent クランプ回避・modifier DECIMATE と同様）。
    for bad in (-0.1, 1.5):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("mesh", {"op": "decimate", "targets": "Cube", "ratio": bad}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, bad
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", bad


def test_mesh_decimate_nonfinite_ratio_server_rejected():
    # FLOAT（ratio）の nan/inf もサーバ側（schema）で弾く。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mesh", {"op": "decimate", "targets": "Cube", "ratio": float("inf")}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mesh_decimate_wrong_op_param_invalid_params():
    # operation は boolean 専用。decimate に渡すと弾く（cross-op leak ガード）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "mesh",
            {"op": "decimate", "targets": "Cube", "ratio": 0.5, "operation": "UNION"},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


# ---- M8 T8.2 straighten（直立補正）の param 検証（bpy 不要）----


def test_straighten_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("straighten", {"method": "reset"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_straighten_missing_method_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("straighten", {"targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_straighten_bad_method_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("straighten", {"targets": "Cube", "method": "bogus"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_straighten_bad_up_axis_invalid_params():
    # up_axis は ENUM。範囲外は schema 検証で INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "straighten", {"targets": "Cube", "method": "world-align", "up_axis": "UP"}, INFO
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_straighten_bad_axis_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("straighten", {"targets": "Cube", "method": "world-align", "axis": "W"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_straighten_axis_on_non_world_align_invalid_params():
    # axis は world-align 専用。reset/pca/floor に渡すと silent ignore せず弾く（bpy 到達前）。
    for method in ("reset", "pca", "floor"):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("straighten", {"targets": "Cube", "method": method, "axis": "Z"}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, method
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", method


def test_straighten_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("straighten", {"targets": "Cube", "method": "reset", "bogus": 1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_straighten_up_hint_on_non_pca_invalid_params():
    # up_hint は pca 専用。reset/world-align/floor に渡すと silent ignore せず弾く（bpy 到達前・#5）。
    for method in ("reset", "world-align", "floor"):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch(
                "straighten", {"targets": "Cube", "method": method, "up_hint": "current"}, INFO
            )
        assert ei.value.code == RPC_INVALID_PARAMS, method
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", method


def test_straighten_bad_up_hint_invalid_params():
    # up_hint は ENUM(auto|current)。範囲外は schema 検証で INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("straighten", {"targets": "Cube", "method": "pca", "up_hint": "bogus"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_straighten_dry_run_with_bake_invalid_params():
    # dry-run（書き込まない）と bake（mesh 焼き込み）は矛盾 → silent ignore せず弾く（bpy 到達前・#2）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "straighten",
            {"targets": "Cube", "method": "pca", "dry_run": True, "bake_rotation": True},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_straighten_valid_params_reach_bpy():
    # 妥当な params（各 method・up_hint/dry_run/bake/make_single_user knob）は USER_INPUT で弾かれず
    # 検証を通過し、bpy の遅延 import まで到達する（bpy 不在の pytest では ModuleNotFoundError）。退行ガード。
    cases = [
        {"method": "reset"},
        {"method": "world-align", "up_axis": "+Z"},
        {"method": "world-align", "axis": "Z"},
        {"method": "pca", "up_axis": "-Y"},
        {"method": "pca", "up_hint": "current"},  # #5: up_hint は pca で受理
        {"method": "pca", "dry_run": True},  # #2: dry-run は受理
        {"method": "floor", "bake_rotation": True, "make_single_user": True},
        # 基準指定 method（#4）。axis は angle/reference でも受理される。
        {"method": "angle", "axis": "X", "degrees": 5.0},
        {"method": "angle", "axis": "Z", "degrees": -90.0, "bake_rotation": True},
        {"method": "align-vector", "from_dir": [0.1, 0.0, 0.99]},  # to_dir 省略=up へ
        {"method": "align-vector", "from_dir": [1.0, 0.0, 0.0], "to_dir": [0.0, 0.0, 1.0]},
        {"method": "align-vector", "from_dir": [0.0, 0.0, 1.0], "dry_run": True},
        {"method": "reference", "reference": "Guide"},
        {"method": "reference", "reference": "Guide", "axis": "Z", "ref_axis": "+Y"},
    ]
    for extra in cases:
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("straighten", {"targets": "Cube", **extra}, INFO)


# ---- 実地FB #4 基準指定 method（angle / align-vector / reference）の param 検証（bpy 不要）----


def test_straighten_angle_requires_axis_and_degrees():
    # angle は axis（回転軸）と degrees（角度）が必須。欠けは USER_INPUT（bpy 到達前）。
    for extra in ({"degrees": 5.0}, {"axis": "X"}, {}):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("straighten", {"targets": "Cube", "method": "angle", **extra}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, extra
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", extra


def test_straighten_degrees_on_non_angle_invalid_params():
    # degrees は angle 専用。他 method に渡すと silent ignore せず弾く（§6e）。
    for method in ("reset", "world-align", "pca", "floor"):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("straighten", {"targets": "Cube", "method": method, "degrees": 5.0}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, method
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", method


def test_straighten_align_vector_requires_from_dir():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("straighten", {"targets": "Cube", "method": "align-vector"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_straighten_align_vector_zero_vector_invalid_params():
    # ゼロベクトルは正規化が不定 → bpy 到達前に弾く（from_dir / to_dir 双方）。
    for extra in (
        {"from_dir": [0.0, 0.0, 0.0]},
        {"from_dir": [1.0, 0.0, 0.0], "to_dir": [0.0, 0.0, 0.0]},
    ):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("straighten", {"targets": "Cube", "method": "align-vector", **extra}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, extra
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", extra


def test_straighten_from_to_dir_on_non_align_vector_invalid_params():
    # from_dir/to_dir は align-vector 専用。他 method に渡すと弾く（§6e）。
    for key in ("from_dir", "to_dir"):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch(
                "straighten",
                {"targets": "Cube", "method": "world-align", key: [1.0, 0.0, 0.0]},
                INFO,
            )
        assert ei.value.code == RPC_INVALID_PARAMS, key
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", key


def test_straighten_reference_requires_reference():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("straighten", {"targets": "Cube", "method": "reference"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_straighten_reference_params_on_non_reference_invalid_params():
    # reference/ref_axis は reference 専用。他 method に渡すと弾く（§6e）。
    for extra in ({"reference": "Guide"}, {"ref_axis": "+Y"}):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("straighten", {"targets": "Cube", "method": "pca", **extra}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, extra
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", extra


def test_straighten_bad_ref_axis_invalid_params():
    # ref_axis は ENUM。範囲外は schema 検証で INVALID_PARAMS。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "straighten",
            {"targets": "Cube", "method": "reference", "reference": "Guide", "ref_axis": "W"},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_straighten_nonfinite_degrees_server_rejected():
    # degrees(FLOAT) の nan/inf は CLI 非経由でもサーバ側（schema SSOT）で弾く（§6e）。
    for bad in (float("inf"), float("nan"), float("-inf")):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch(
                "straighten",
                {"targets": "Cube", "method": "angle", "axis": "Z", "degrees": bad},
                INFO,
            )
        assert ei.value.code == RPC_INVALID_PARAMS, bad
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", bad


def test_straighten_nonfinite_from_dir_server_rejected():
    # from_dir(VEC3) の nan/inf も同じ防御線（schema._check_type の有限性）で弾く。
    for bad in (float("inf"), float("nan"), float("-inf")):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch(
                "straighten",
                {"targets": "Cube", "method": "align-vector", "from_dir": [bad, 0.0, 1.0]},
                INFO,
            )
        assert ei.value.code == RPC_INVALID_PARAMS, bad
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", bad


# ---- 実地FB #1 capture（状態キャプチャ）の param 検証（bpy 不要）----


def test_capture_bad_source_invalid_params():
    # source は ENUM(viewport|screen|render)。範囲外は schema 検証で INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("capture", {"source": "bogus"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_capture_camera_on_non_render_invalid_params():
    # camera は render 専用。viewport/screen に渡すと silent ignore せず弾く（bpy 到達前・USER_INPUT）。
    for source in ("viewport", "screen"):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("capture", {"source": source, "camera": "Camera"}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, source
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", source


def test_capture_dims_on_screen_invalid_params():
    # width/height は screen では領域サイズ固定のため不可（bpy 到達前に弾く）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("capture", {"source": "screen", "width": 640}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_capture_dim_out_of_range_invalid_params():
    # 解像度は暴走防止の範囲を bpy 到達前に弾く（上限超・下限割れ・0 いずれも USER_INPUT）。
    for bad in (99999, 0, 8, -1):  # CAPTURE_MAX_DIM 超 / 0 / CAPTURE_MIN_DIM 未満 / 負値
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("capture", {"source": "viewport", "width": bad}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, bad
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", bad


def test_capture_valid_params_reach_bpy():
    # 妥当な params は USER_INPUT で弾かれず検証を通過し bpy の遅延 import まで到達する（退行ガード）。
    cases = [
        {"source": "viewport"},
        {"source": "viewport", "width": 320, "height": 240},
        {"source": "screen"},
        {"source": "render", "camera": "Camera"},
    ]
    for params in cases:
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("capture", params, INFO)


# ---- M8 T8.3 print-setup（単位設定）の param 検証（bpy 不要）----


def test_print_setup_bad_unit_invalid_params():
    # unit は ENUM(mm|m)。範囲外は schema 検証で INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-setup", {"unit": "inch"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_setup_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-setup", {"unit": "mm", "bogus": 1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_setup_bad_scene_type_invalid_params():
    # scene は STR。非文字列は型エラーで INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-setup", {"unit": "mm", "scene": 123}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_setup_valid_params_reach_bpy():
    # unit 省略（既定 mm）/ mm / m / scene 指定の妥当な params は検証を通過し bpy 遅延 import まで
    # 到達する（bpy 不在の pytest では ModuleNotFoundError）。退行ガード。
    for params in ({}, {"unit": "mm"}, {"unit": "m"}, {"unit": "mm", "scene": "Scene"}):
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("print-setup", params, INFO)


# ---- M8 T8.4 print-check / print-repair の param 検証（bpy 不要）----


def test_print_check_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-check", {}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_check_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-check", {"targets": "Cube", "bogus": 1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_check_min_thickness_without_thin_invalid_params():
    # min_thickness は thin 専用。thin 無しで渡したら弾く（bpy 到達前に USER_INPUT）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-check", {"targets": "Cube", "min_thickness": 1.0}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_print_check_nonfinite_min_thickness_server_rejected():
    # FLOAT（min_thickness）の nan/inf もサーバ側（schema）で弾く。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "print-check", {"targets": "Cube", "thin": True, "min_thickness": float("inf")}, INFO
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_check_valid_params_reach_bpy():
    # 妥当な params（カテゴリ flag・thin/min_thickness 含む）は検証を通過し bpy 遅延 import まで
    # 到達する（CAPABILITY 判定は bpy 必須＝smoke で検証）。退行ガード。
    for params in (
        {},
        {"manifold": True},
        {"normals": True, "degenerate": True},
        {"thin": True, "min_thickness": 0.5},
        {"intersect": True},
    ):
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("print-check", {"targets": "Cube", **params}, INFO)


def test_print_repair_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-repair", {}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_repair_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-repair", {"targets": "Cube", "bogus": 1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_repair_all_false_invalid_params():
    # 明示的に全 false（生成クライアントの既定埋め）は「修復なし」として弾く（apply-transform と同流儀）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "print-repair",
            {
                "targets": "Cube",
                "make_manifold": False,
                "recalc_normals": False,
                "remove_degenerate": False,
            },
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_print_repair_bad_bool_type_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-repair", {"targets": "Cube", "make_manifold": "yes"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_repair_valid_params_reach_bpy():
    # 全省略（=全修復）/ 個別指定 / make_single_user knob は検証を通過し bpy 遅延 import まで到達。
    for params in (
        {},
        {"make_manifold": True},
        {"recalc_normals": True, "remove_degenerate": True},
        {"make_manifold": True, "make_single_user": True},
    ):
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("print-repair", {"targets": "Cube", **params}, INFO)
