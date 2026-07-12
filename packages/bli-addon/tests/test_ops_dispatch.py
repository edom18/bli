"""ops.dispatch のルーティング + サーバ側 param 検証のユニット（L1/L3）。

bpy を必要としない経路のみを検証する:
- 非 bpy メソッド（ping/echo/unknown）の委譲。
- bpy 系ハンドラの **param 検証は bpy import より前**に走るため、
  不正 params は bpy 無しで INVALID_PARAMS を返せる。
"""

from __future__ import annotations

import pytest

from bli_addon import ops, session_state
from bli_addon.handlers import ServerInfo
from bli_core.errors import (
    RPC_BUSINESS_ERROR,
    RPC_INVALID_PARAMS,
    RPC_METHOD_NOT_FOUND,
    ErrorCode,
)
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


def test_modifier_add_unknown_type_with_flag_invalid_params():
    # type は P2-3 で STR 化＝実在検証はサーバ（rna 能力検出・bpy 必要）。bpy 到達前に弾けるのは
    # 「未知 type に専用フラグ」の組み合わせ（--props を案内する）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "modifier", {"action": "add", "targets": "Cube", "type": "BOGUS", "levels": 2}, INFO
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert "--props" in ei.value.data.remediation


def test_modifier_props_bad_json_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "modifier",
            {"action": "add", "targets": "Cube", "type": "BEVEL", "props": "{width:0.1}"},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_modifier_props_non_object_invalid_params():
    # 配列/スカラの JSON は弾く（key:value のオブジェクトのみ）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "modifier",
            {"action": "add", "targets": "Cube", "type": "BEVEL", "props": "[1,2]"},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_modifier_props_only_for_add_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "modifier",
            {"action": "list", "targets": "Cube", "props": '{"width":0.1}'},
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_modifier_props_conflicts_with_dedicated_flag_invalid_params():
    # 専用フラグと --props の併用は曖昧（同一プロパティの二重指定）＝弾く。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "modifier",
            {
                "action": "add",
                "targets": "Cube",
                "type": "SUBSURF",
                "levels": 2,
                "props": '{"levels":3}',
            },
            INFO,
        )
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


# ---- P1-2: E_MODE_MISMATCH の remediation が具体的な復帰コマンドを案内する ----


def test_check_mode_mismatch_remediation_points_to_mode_command():
    # mode コマンド新設（U9対策）に伴い、remediation は「OBJECT モードに切り替えてください」という
    # 趣旨の文言だけでなく、具体的な復帰コマンド（bli mode --to object）を案内するようにした。
    from bli_core.commands import get_command, load_definitions

    load_definitions()
    cmd = get_command("select")  # required_mode=Mode.OBJECT の代表コマンド
    with pytest.raises(JsonRpcError) as ei:
        ops._check_mode(cmd, "EDIT_MESH")
    assert ei.value.message == ErrorCode.E_MODE_MISMATCH
    assert "bli mode --to object" in ei.value.data.remediation


# ---- P1-2 add（生成）の param 検証（bpy 不要）----


def test_add_missing_type_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("add", {}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_add_bad_type_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("add", {"type": "sphere"}, INFO)  # 正しくは uv-sphere/ico-sphere
    assert ei.value.code == RPC_INVALID_PARAMS


def test_add_light_type_on_non_light_invalid_params():
    # light_type は type=light 専用（presence-sensitive）。他 type に渡すと弾く（bpy 到達前）。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("add", {"type": "cube", "light_type": "SUN"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_add_bad_light_type_enum_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("add", {"type": "light", "light_type": "BOGUS"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_add_bad_location_vec3_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("add", {"type": "cube", "location": [1, 2]}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_add_valid_params_reach_bpy():
    # 妥当な params は USER_INPUT で弾かれず bpy の遅延 import まで到達する（退行ガード）。
    cases = [
        {"type": "cube"},
        {"type": "cube", "name": "Barrel", "location": [1.0, 2.0, 3.0]},
        {"type": "cylinder", "rotation": [0.0, 0.0, 45.0], "scale": [1.0, 1.0, 2.0]},
        {"type": "light", "light_type": "SUN"},
        {"type": "empty"},
        {"type": "camera"},
        {"type": "text"},
    ]
    for params in cases:
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("add", params, INFO)


# ---- P1-2 mode（モード切替）の param 検証（bpy 不要）----


def test_mode_missing_to_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mode", {}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mode_bad_to_enum_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("mode", {"to": "bogus"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_mode_valid_params_reach_bpy():
    # targets 省略（現在の active を対象）でも到達できる＝ U9（Edit モード放置）復帰の要件。
    for params in (
        {"to": "object"},
        {"to": "edit", "targets": "Cube"},
        {"to": "sculpt"},
        {"to": "vertex-paint"},
        {"to": "weight-paint", "targets": "^Cube", "regex": True},
    ):
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("mode", params, INFO)


# ---- P1-2 rename の param 検証（bpy 不要）----


def test_rename_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("rename", {"name": "Barrel"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_rename_missing_name_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("rename", {"targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_rename_valid_params_reach_bpy():
    for params in (
        {"targets": "Cube", "name": "Barrel"},
        {"targets": "Cube", "name": "Barrel", "with_data": True},
    ):
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("rename", params, INFO)


# ---- P1-2 parent の param 検証（bpy 不要・--to/--clear 排他）----


def test_parent_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("parent", {"to": "Empty"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_parent_neither_to_nor_clear_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("parent", {"targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_parent_both_to_and_clear_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("parent", {"targets": "Cube", "to": "Empty", "clear": True}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_parent_valid_params_reach_bpy():
    for params in (
        {"targets": "Cube", "to": "Empty"},
        {"targets": "Cube", "clear": True},
        {"targets": "Cube", "to": "Empty", "keep_transform": False},
        {"targets": "^Cube", "regex": True, "clear": True},
    ):
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("parent", params, INFO)


# ---- P1-2 collection の param 検証（bpy 不要・action 別必須）----


def test_collection_missing_action_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("collection", {"name": "Props"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_collection_bad_action_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("collection", {"action": "bogus", "name": "Props"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_collection_create_missing_name_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("collection", {"action": "create"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_collection_list_does_not_require_name():
    # list は name 不要（presence-sensitive で他 action のみ必須）→ bpy まで到達する。
    with pytest.raises(ModuleNotFoundError):
        ops.dispatch("collection", {"action": "list"}, INFO)


def test_collection_move_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("collection", {"action": "move", "name": "Props"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_collection_link_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("collection", {"action": "link", "name": "Props"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_collection_unlink_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("collection", {"action": "unlink", "name": "Props"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_collection_create_with_targets_invalid_params():
    # targets は move/link/unlink 専用。create/list に渡すと silent ignore せず弾く。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("collection", {"action": "create", "name": "Props", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_collection_list_with_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("collection", {"action": "list", "targets": "Cube"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_collection_valid_params_reach_bpy():
    for params in (
        {"action": "create", "name": "Props"},
        {"action": "move", "name": "Props", "targets": "Cube"},
        {"action": "link", "name": "Props", "targets": "^Cube", "regex": True},
        {"action": "unlink", "name": "Props", "targets": "Cube"},
        {"action": "list"},
    ):
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("collection", params, INFO)


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


# ---- 実地FB #3 undo / redo（状態操作）の param 検証（bpy 不要）----


def test_undo_redo_default_steps_reach_bpy():
    # steps 省略（既定 1）/ 範囲内は検証を通過し、gateway の遅延 import まで到達する。
    for method in ("undo", "redo"):
        for params in ({}, {"steps": 1}, {"steps": 100}):
            with pytest.raises(ModuleNotFoundError):
                ops.dispatch(method, params, INFO)


def test_undo_redo_steps_below_min_invalid_params():
    # steps<1 は無音 no-op になるため USER_INPUT で弾く（bpy 到達前）。
    for method in ("undo", "redo"):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch(method, {"steps": 0}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, method
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", method


def test_undo_redo_steps_above_max_invalid_params():
    # 暴走防止: 上限超過も bpy 到達前に弾く（runtime.MAX_UNDO_STEPS）。
    for method in ("undo", "redo"):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch(method, {"steps": 10_000}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, method
        assert ei.value.data is not None
        assert ei.value.data.category == "USER_INPUT", method


def test_undo_redo_bad_steps_type_invalid_params():
    # steps は INT。非整数は schema 型エラーで INVALID_PARAMS（bool は int 扱いしない）。
    for method in ("undo", "redo"):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch(method, {"steps": "x"}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, method


def test_undo_redo_unknown_param_invalid_params():
    for method in ("undo", "redo"):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch(method, {"bogus": 1}, INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, method


# ---- M8 T8.5 print-export（STL 出力）の param 検証（bpy 不要）----


def _export_params(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {"targets": "Cube", "format": "stl", "path": "out.stl"}
    base.update(extra)
    return base


def test_print_export_missing_targets_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-export", {"format": "stl", "path": "out.stl"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_export_missing_format_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-export", {"targets": "Cube", "path": "out.stl"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_export_missing_path_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-export", {"targets": "Cube", "format": "stl"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_export_bad_format_invalid_params():
    # format は ENUM(stl|3mf)。範囲外（obj 等）は schema 検証で INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-export", _export_params(format="obj"), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_export_empty_path_invalid_params():
    # 空/空白のみの path は無音失敗を避けるため bpy 到達前に USER_INPUT で弾く。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-export", _export_params(path="   "), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_print_export_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-export", _export_params(bogus=1), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_export_bad_scale_type_invalid_params():
    # scale は FLOAT。文字列は型エラーで INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("print-export", _export_params(scale="x"), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_print_export_nonfinite_scale_server_rejected():
    # FLOAT（scale）の nan/inf もサーバ側（schema SSOT）で弾く（CLI 非経由 RPC 防御）。
    for bad in (float("inf"), float("nan"), float("-inf")):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("print-export", _export_params(scale=bad), INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, bad


def test_print_export_non_positive_scale_invalid_params():
    # scale<=0 は退化（0＝原点に潰れる）/ 反転（負＝法線裏返り）で壊れた STL になるため bpy 到達前に弾く。
    for bad in (0.0, -1.0, -0.5):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("print-export", _export_params(scale=bad), INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, bad
        assert ei.value.data is not None, bad
        assert ei.value.data.category == "USER_INPUT", bad


def test_print_export_bad_bool_type_invalid_params():
    # ascii / apply_modifiers は BOOL。非真偽値は型エラーで INVALID_PARAMS
    for key in ("ascii", "apply_modifiers"):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("print-export", _export_params(**{key: "yes"}), INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, key


def test_print_export_valid_params_reach_bpy():
    # 妥当な params（stl/3mf・ascii・scale・apply_modifiers）は検証を通過し gateway の遅延 import まで
    # 到達する（3mf の CAPABILITY 判定・実出力は bpy 必須＝smoke で検証）。退行ガード。
    for extra in (
        {},
        {"format": "3mf"},
        {"ascii": True},
        {"scale": 1000.0},
        {"apply_modifiers": False},
    ):
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("print-export", _export_params(**extra), INFO)


# ---- M9 T9.1 export（多形式 export）の param 検証（bpy 不要）----


def _generic_export_params(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {"format": "stl", "path": "out.stl"}
    base.update(extra)
    return base


def test_export_missing_format_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("export", {"path": "out.stl"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_export_missing_path_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("export", {"format": "stl"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_export_bad_format_invalid_params():
    # format は ENUM(obj|fbx|gltf|stl|3mf)。範囲外（ply 等）は schema 検証で INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("export", _generic_export_params(format="ply"), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_export_empty_path_invalid_params():
    # 空/空白のみの path は無音失敗を避けるため bpy 到達前に USER_INPUT で弾く。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("export", _generic_export_params(path="   "), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_export_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("export", _generic_export_params(bogus=1), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_export_bad_use_selection_type_invalid_params():
    # use_selection は BOOL。非真偽値は型エラーで INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("export", _generic_export_params(use_selection="yes"), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_export_empty_targets_invalid_params():
    # 空/空白のみの --targets は空 regex で全マッチ＝シーン全体に化けるため bpy 到達前に USER_INPUT で弾く。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("export", _generic_export_params(targets="  "), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_export_gltf_non_glb_extension_invalid_params():
    # gltf は GLB 単一固定（.glb 必須）。.gltf 等は無効 enum→INTERNAL を避け bpy 到達前に USER_INPUT。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("export", _generic_export_params(format="gltf", path="out.gltf"), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_export_valid_params_reach_bpy():
    # 妥当な params（各形式・targets/use_selection/シーン全体・両指定）は検証を通過し gateway の遅延
    # import まで到達する（能力解決/実出力は bpy 必須＝smoke で検証）。3mf も gateway 到達後に CAPABILITY。
    for extra in (
        {},  # シーン全体（targets/use_selection 省略）
        {"targets": "Cube"},
        {"use_selection": True},
        {"targets": "Cube", "use_selection": True},  # 両指定（targets 優先・use_selection 無視）
        {"format": "obj"},
        {"format": "gltf", "path": "out.glb"},
        {"format": "fbx", "path": "out.fbx"},
        {"format": "3mf", "path": "out.3mf"},
        # P1-3: fbx 専用オプション（全指定）も検証を通過し gateway まで到達する。
        {
            "format": "fbx",
            "path": "out.fbx",
            "axis_forward": "-Z",
            "axis_up": "Y",
            "scale": 2.0,
            "apply_unit_scale": True,
            "embed_textures": True,
        },
    ):
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("export", _generic_export_params(**extra), INFO)


# ---- P1-3: export の fbx 専用オプション（axis/scale/apply_unit_scale/embed_textures）検証 ----


def test_export_fbx_only_param_rejected_for_other_format():
    # axis_forward/axis_up/scale/apply_unit_scale/embed_textures は format=fbx 専用
    # （presence-sensitive・他 format への指定は silent ignore せず INVALID_PARAMS）。
    for extra in (
        {"axis_forward": "-Z"},
        {"axis_up": "Y"},
        {"scale": 2.0},
        {"apply_unit_scale": True},
        {"embed_textures": True},
    ):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("export", _generic_export_params(format="stl", **extra), INFO)
        assert ei.value.code == RPC_INVALID_PARAMS, extra
        assert ei.value.data is not None, extra
        assert ei.value.data.category == "USER_INPUT", extra


def test_export_fbx_bad_axis_enum_invalid_params():
    # axis_forward/axis_up は ENUM(X|Y|Z|-X|-Y|-Z)。範囲外は schema 検証で INVALID_PARAMS。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch(
            "export",
            _generic_export_params(format="fbx", path="out.fbx", axis_forward="UP"),
            INFO,
        )
    assert ei.value.code == RPC_INVALID_PARAMS


def test_export_fbx_non_positive_scale_invalid_params():
    # fbx の --scale も 0/負値は退化/反転で不正な FBX になるため bpy 到達前に弾く（print-export と同流儀）。
    for bad in (0.0, -1.0, -0.5):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch(
                "export", _generic_export_params(format="fbx", path="out.fbx", scale=bad), INFO
            )
        assert ei.value.code == RPC_INVALID_PARAMS, bad
        assert ei.value.data is not None, bad
        assert ei.value.data.category == "USER_INPUT", bad


def test_export_fbx_bad_bool_type_invalid_params():
    # apply_unit_scale / embed_textures は BOOL。非真偽値は型エラーで INVALID_PARAMS
    for key in ("apply_unit_scale", "embed_textures"):
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch(
                "export",
                _generic_export_params(format="fbx", path="out.fbx", **{key: "yes"}),
                INFO,
            )
        assert ei.value.code == RPC_INVALID_PARAMS, key


# ---- M9 T9.2 import（多形式 import）の param 検証（bpy 不要）----


def _import_params(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {"format": "stl", "path": "in.stl"}
    base.update(extra)
    return base


def test_import_missing_format_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("import", {"path": "in.stl"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_import_missing_path_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("import", {"format": "stl"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_import_bad_format_invalid_params():
    # format は ENUM(obj|fbx|gltf|stl|3mf)。範囲外（ply 等）は schema 検証で INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("import", _import_params(format="ply"), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_import_empty_path_invalid_params():
    # 空/空白のみの path は bpy 到達前に USER_INPUT で弾く。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("import", _import_params(path="   "), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_import_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("import", _import_params(bogus=1), INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_import_valid_params_reach_bpy():
    # 妥当な params（各形式）は検証を通過し gateway の遅延 import まで到達する（能力解決/実取込は bpy
    # 必須＝smoke で検証）。3mf も gateway 到達後に CAPABILITY、ファイル存在チェックも bpy import 後。
    for extra in (
        {},
        {"format": "obj", "path": "in.obj"},
        {"format": "gltf", "path": "in.glb"},
        {"format": "fbx", "path": "in.fbx"},
        {"format": "3mf", "path": "in.3mf"},
    ):
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("import", _import_params(**extra), INFO)


# ---- M9 T9.3 save（.blend 保存）の param 検証（bpy 不要）----


def test_save_non_blend_extension_invalid_params():
    # --path は .blend 必須（backup naming/上書き安全のため）。.txt 等は bpy 到達前に USER_INPUT。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("save", {"path": "out.txt"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_save_empty_path_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("save", {"path": "   "}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_save_bad_backup_type_invalid_params():
    # backup は BOOL。非真偽値は型エラーで INVALID_PARAMS
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("save", {"backup": "yes"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_save_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("save", {"bogus": 1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_save_valid_params_reach_bpy():
    # 妥当な params（path 省略=現在ファイル / .blend 指定 / backup トグル）は検証を通過し gateway の
    # 遅延 import まで到達する（保存/未保存判定/backup は bpy 必須＝smoke で検証）。
    for extra in (
        {},  # path 省略（現在ファイルへ・未保存判定は bpy 後）
        {"path": "out.blend"},
        {"path": "out.blend", "backup": False},
    ):
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("save", extra, INFO)


# ---- M9 T9.4 open（.blend を開く）の param 検証 + 未保存ガード（bpy 不要）----


def test_open_missing_path_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("open", {}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_open_empty_path_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("open", {"path": "   "}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_open_non_blend_extension_invalid_params():
    # --path は .blend 必須（save と対称）。.txt 等は bpy 到達前に USER_INPUT。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("open", {"path": "scene.txt"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_open_bad_force_type_invalid_params():
    # force は BOOL。非真偽値は型エラーで INVALID_PARAMS。
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("open", {"path": "scene.blend", "force": "yes"}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_open_unknown_param_invalid_params():
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("open", {"path": "scene.blend", "bogus": 1}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS


def test_open_missing_file_invalid_params(tmp_path):
    # 実在しない .blend は bpy 到達前に USER_INPUT（abspath 後に isfile 判定）。
    session_state.reset()
    missing = str(tmp_path / "nope.blend")
    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("open", {"path": missing}, INFO)
    assert ei.value.code == RPC_INVALID_PARAMS
    assert ei.value.data is not None
    assert ei.value.data.category == "USER_INPUT"


def test_open_unsaved_changes_precondition(tmp_path):
    # 未保存変更あり + --force なし → bpy 到達前に E_PRECONDITION（シーン破壊を防ぐ・§E11）。
    f = tmp_path / "scene.blend"
    f.write_bytes(b"BLENDER-dummy")  # 実在すればよい（中身検証は bpy・smoke）
    session_state.reset()
    session_state.mark_modified()
    try:
        with pytest.raises(JsonRpcError) as ei:
            ops.dispatch("open", {"path": str(f)}, INFO)
        assert ei.value.code == RPC_BUSINESS_ERROR
        assert ei.value.message == ErrorCode.E_PRECONDITION
        assert ei.value.data is not None
        assert ei.value.data.category == "PRECONDITION"
    finally:
        session_state.reset()


def test_open_unsaved_changes_force_reaches_bpy(tmp_path):
    # 未保存変更あり + --force → ガードを通過し gateway（bpy）まで到達する。
    f = tmp_path / "scene.blend"
    f.write_bytes(b"BLENDER-dummy")
    session_state.reset()
    session_state.mark_modified()
    try:
        with pytest.raises(ModuleNotFoundError):
            ops.dispatch("open", {"path": str(f), "force": True}, INFO)
    finally:
        session_state.reset()


def test_open_clean_reaches_bpy(tmp_path):
    # 変更なし（clean）+ 実在ファイル → ガードなしで gateway（bpy）まで到達する。
    f = tmp_path / "scene.blend"
    f.write_bytes(b"BLENDER-dummy")
    session_state.reset()
    with pytest.raises(ModuleNotFoundError):
        ops.dispatch("open", {"path": str(f)}, INFO)
