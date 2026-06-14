"""BpyGateway: bpy への唯一の接点（M3 / research.md 論点2 + 付録B）。

このファイル名 `gateway.py` は AST guard の許可対象（run_operator ラッパを置く）。
operator は必ず run_operator 経由で呼ぶ: temp_override + poll 先行 +
`'FINISHED' in result` 判定 + 最小 undo_push。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

import bpy  # type: ignore

from bli_core.errors import (
    RPC_BUSINESS_ERROR,
    ErrorCategory,
    ErrorCode,
    make_error,
)
from bli_core.protocol import JsonRpcError


def _op_error(
    kind: str, symptom: str, *, category: str = ErrorCategory.PRECONDITION
) -> JsonRpcError:
    eo = make_error(kind, category=category, retryable=False, symptom=symptom)
    return JsonRpcError(RPC_BUSINESS_ERROR, kind, eo)


def _override_for(obj: Any, extra: dict[str, Any] | None) -> dict[str, Any]:
    ov: dict[str, Any] = {}
    if obj is not None:
        ov["active_object"] = obj
        ov["object"] = obj
        ov["selected_objects"] = [obj]
    if extra:
        ov.update(extra)
    return ov


def run_operator(
    op: Any,
    obj: Any = None,
    *,
    message: str | None = None,
    extra_override: dict[str, Any] | None = None,
    **kwargs: Any,
) -> set[str]:
    """operator を temp_override 下で実行する（poll 先行 / FINISHED 判定 / undo_push）。"""
    override = _override_for(obj, extra_override)
    try:
        with bpy.context.temp_override(**override):
            if not op.poll():
                raise _op_error(ErrorCode.E_PRECONDITION, "poll() False（前提条件未達）")
            result = op(**kwargs)
    except RuntimeError as e:
        raise _op_error(ErrorCode.E_OPERATOR, f"operator 実行時エラー: {e}") from e
    if "FINISHED" not in result:
        raise _op_error(ErrorCode.E_OPERATOR, f"operator が完了しませんでした: {sorted(result)}")
    if message:
        with bpy.context.temp_override(**override):
            bpy.ops.ed.undo_push(message=message)
    return result


def push_undo(message: str) -> None:
    """operator を介さない直接変更後の Undo 境界を作る。"""
    bpy.ops.ed.undo_push(message=message)


# ---- オブジェクト解決・情報 ----


def resolve_targets(selector: str, *, regex: bool = False) -> list[Any]:
    """selector からオブジェクト群を解決する（完全名 > regex）。"""
    objs = bpy.data.objects
    if not regex:
        obj = objs.get(selector)
        if obj is not None:
            return [obj]
    pattern = re.compile(selector)
    return [o for o in objs if pattern.search(o.name)]


def require_single(selector: str, *, regex: bool = False) -> Any:
    """対象を1つに解決する。0件/複数はエラー。"""
    found = resolve_targets(selector, regex=regex)
    if not found:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"対象が見つかりません: {selector}",
            category=ErrorCategory.USER_INPUT,
        )
    if len(found) > 1:
        names = ", ".join(o.name for o in found[:5])
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"対象が複数該当します（1つに絞ってください）: {names}",
            category=ErrorCategory.USER_INPUT,
        )
    return found[0]


def world_bbox(obj: Any) -> dict[str, list[float]] | None:
    """オブジェクトの **ワールド空間** 軸並行バウンディングボックス（min/max/size）。

    `obj.bound_box`（object 空間の8隅）を matrix_world で変換し、各軸 min/max を取る。
    既定 Cube（size=2・原点）なら min=[-1,-1,-1] max=[1,1,1] size=[2,2,2]。

    非ジオメトリ（EMPTY/LIGHT/CAMERA 等）や bound_box 未提供の文脈では Blender が
    8隅すべて同一値を返す（5.0/4.4 とも全 (0,0,0) / 版により全 (-1,-1,-1) の番兵）。
    その退化（全隅同一）を検出して **None** を返し、偽の零サイズ bbox を出さない
    （番号分岐せず値で判定。Codex P2 指摘）。
    """
    raw = [tuple(c) for c in obj.bound_box]
    if len(set(raw)) <= 1:  # 全隅同一 = ジオメトリ無し / bound_box 未提供
        return None

    from mathutils import Vector  # type: ignore  # lazy: bpy 依存を閉じる

    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    mn = [min(xs), min(ys), min(zs)]
    mx = [max(xs), max(ys), max(zs)]
    return {
        "min": [round(v, 6) for v in mn],
        "max": [round(v, 6) for v in mx],
        "size": [round(mx[i] - mn[i], 6) for i in range(3)],
    }


def object_summary(obj: Any) -> dict[str, Any]:
    """オブジェクトの要約（info 系の共通項）。"""
    loc = obj.matrix_world.translation
    dims = obj.dimensions
    data = {
        "name": obj.name,
        "type": obj.type,
        "location": [round(loc.x, 6), round(loc.y, 6), round(loc.z, 6)],
        "dimensions": [round(dims.x, 6), round(dims.y, 6), round(dims.z, 6)],
        "rotation_euler_deg": [round(a * 57.2957795, 4) for a in obj.rotation_euler],
        "scale": [round(s, 6) for s in obj.scale],
        "bbox": world_bbox(obj),
    }
    if obj.type == "MESH" and obj.data is not None:
        data["vertices"] = len(obj.data.vertices)
        data["polygons"] = len(obj.data.polygons)
        data["mesh_users"] = obj.data.users
    data["modifiers"] = [m.name for m in obj.modifiers]
    data["materials"] = (
        [m.name for m in obj.data.materials] if getattr(obj.data, "materials", None) else []
    )
    return data


def scene_summary(depth: int = 1) -> dict[str, Any]:
    """シーン要約（オブジェクト一覧 + 単位）。"""
    scene = bpy.context.scene
    us = scene.unit_settings
    return {
        "scene": scene.name,
        "object_count": len(bpy.data.objects),
        "objects": [object_summary(o) for o in scene.objects],
        "unit_settings": {
            "system": us.system,
            "scale_length": round(us.scale_length, 8),
            "length_unit": us.length_unit,
        },
    }


def list_objects(type_filter: str | None = None, regex: str | None = None) -> list[dict[str, Any]]:
    """シーン内オブジェクトを type/regex でフィルタして軽量サマリ一覧を返す。

    type は大小無視で `obj.type` と完全一致照合（freeform: 版差・将来型に強い）。
    regex は名前への部分一致。重い object_summary は使わず name/type/location のみ。
    """
    pattern = None
    if regex:
        try:
            pattern = re.compile(regex)
        except re.error as e:
            raise _op_error(
                ErrorCode.E_PRECONDITION,
                f"正規表現が不正です: {e}",
                category=ErrorCategory.USER_INPUT,
            ) from e
    want_type = type_filter.upper() if type_filter else None

    out: list[dict[str, Any]] = []
    for o in bpy.context.scene.objects:
        if want_type is not None and o.type != want_type:
            continue
        if pattern is not None and not pattern.search(o.name):
            continue
        loc = o.matrix_world.translation
        out.append(
            {
                "name": o.name,
                "type": o.type,
                "location": [round(loc.x, 6), round(loc.y, 6), round(loc.z, 6)],
            }
        )
    return out


def object_fingerprint(obj: Any) -> str:
    """オブジェクト状態の決定的フィンガープリント（verified 用の短ハッシュ）。"""
    blob = json.dumps(object_summary(obj), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---- モード / 単一ユーザ化 ----


def current_mode() -> str:
    """現在の編集モード文字列（'OBJECT' / 'EDIT_MESH' など）。"""
    return bpy.context.mode


def mesh_user_count(obj: Any) -> int:
    """オブジェクトの mesh データ共有数（users）。データ無しは 0。"""
    data = obj.data
    return data.users if data is not None else 0


def make_single_user_mesh(obj: Any) -> None:
    """共有 mesh をコピーして単一ユーザ化する（他オブジェクトへの波及を防ぐ）。"""
    if obj.data is not None:
        obj.data = obj.data.copy()


# ---- 原点操作（origin_set は operator 経由 / world は直接行列）----


def origin_set(
    obj: Any, *, origin_type: str, center: str = "MEDIAN", message: str | None = None
) -> None:
    """object.origin_set を run_operator 経由で実行する（type/center は M0.5 確定値）。"""
    kwargs: dict[str, Any] = {"type": origin_type}
    if origin_type == "ORIGIN_GEOMETRY":
        kwargs["center"] = center
    run_operator(bpy.ops.object.origin_set, obj, message=message, **kwargs)


def set_origin_world(obj: Any, x: float, y: float, z: float) -> list[float]:
    """原点をワールド座標 (x,y,z) へ移す（直接行列フォールバック / 付録B）。

    メッシュを逆方向に動かして見た目を固定したまま、object 原点だけを移す。
    回転・スケールがあっても matrix.to_3x3() の逆で局所オフセットに変換して整合させる。
    """
    from mathutils import Matrix, Vector  # type: ignore  # lazy: bpy 依存を閉じる

    if not hasattr(obj.data, "transform"):
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"world 原点指定は mesh/curve のみ対応（type={obj.type}）",
        )
    mat = obj.matrix_world
    new_origin = Vector((x, y, z))
    diff_local = mat.to_3x3().inverted() @ (new_origin - mat.translation)
    obj.data.transform(Matrix.Translation(-diff_local))
    mat.translation = new_origin
    loc = obj.matrix_world.translation
    return [round(loc.x, 6), round(loc.y, 6), round(loc.z, 6)]


# ---- 汎用編集（transform / apply-transform / select / M6 T6.1）----


def transform_object(
    obj: Any,
    *,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
    mode: str = "set",
    message: str | None = None,
) -> dict[str, Any]:
    """オブジェクトの loc/rot/scale を set または delta で変更する（直接プロパティ・op不要）。

    rotation は度入力 → ラジアン。delta: location/rotation は加算、scale は成分ごとの乗算。
    """
    if mode == "delta":
        if location is not None:
            cur = obj.location
            obj.location = (cur[0] + location[0], cur[1] + location[1], cur[2] + location[2])
        if rotation is not None:
            r = obj.rotation_euler
            obj.rotation_euler = (
                r[0] + math.radians(rotation[0]),
                r[1] + math.radians(rotation[1]),
                r[2] + math.radians(rotation[2]),
            )
        if scale is not None:
            s = obj.scale
            obj.scale = (s[0] * scale[0], s[1] * scale[1], s[2] * scale[2])
    else:  # set
        if location is not None:
            obj.location = tuple(location)
        if rotation is not None:
            obj.rotation_euler = tuple(math.radians(a) for a in rotation)
        if scale is not None:
            obj.scale = tuple(scale)
    if message:
        push_undo(message)
    return object_summary(obj)


def apply_transform(
    obj: Any,
    *,
    location: bool,
    rotation: bool,
    scale: bool,
    message: str | None = None,
) -> dict[str, Any]:
    """transform を mesh データへ適用する（operator 経由）。

    共有 mesh は `isolate_users=True` で自動的に単一ユーザ化してから適用する
    （他オブジェクトへの波及を防ぐ。M0.5 で 4.4/5.0 とも存在を確認）。
    """
    run_operator(
        bpy.ops.object.transform_apply,
        obj,
        message=message,
        location=location,
        rotation=rotation,
        scale=scale,
        isolate_users=True,
    )
    return object_summary(obj)


def select_objects(
    targets: str,
    *,
    type_filter: str | None = None,
    active: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """targets(name|regex) を選択し active を設定する（select_set / active 直接設定・op不要）。"""
    matched = resolve_targets(targets)  # 完全名 > regex
    if type_filter is not None:
        want = type_filter.upper()
        matched = [o for o in matched if o.type == want]
    if not matched:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"対象が見つかりません: {targets}",
            category=ErrorCategory.USER_INPUT,
        )

    view_layer = bpy.context.view_layer
    for o in view_layer.objects:
        o.select_set(False)
    for o in matched:
        o.select_set(True)

    if active is not None:
        active_obj = next((o for o in matched if o.name == active), None)
        if active_obj is None:
            raise _op_error(
                ErrorCode.E_PRECONDITION,
                f"--active の対象が選択集合にありません: {active}",
                category=ErrorCategory.USER_INPUT,
            )
    else:
        active_obj = matched[0]
    view_layer.objects.active = active_obj

    if message:
        push_undo(message)
    return {
        "selected": [o.name for o in matched],
        "count": len(matched),
        "active": active_obj.name,
    }
