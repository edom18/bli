"""BpyGateway: bpy への唯一の接点（M3 / research.md 論点2 + 付録B）。

このファイル名 `gateway.py` は AST guard の許可対象（run_operator ラッパを置く）。
operator は必ず run_operator 経由で呼ぶ: temp_override + poll 先行 +
`'FINISHED' in result` 判定 + 最小 undo_push。
"""

from __future__ import annotations

import hashlib
import json
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
