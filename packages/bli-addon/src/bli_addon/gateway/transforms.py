"""BpyGateway 原点操作・汎用編集（transform/apply-transform/select）（gateway/ 分割 P2-4）。

元 gateway.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

import math
from typing import Any

import bpy  # type: ignore

from bli_core.errors import ErrorCategory, ErrorCode

from .core import _op_error, push_undo, run_operator
from .objects import _EULER_MODES, _regex_match_hint, object_summary, resolve_targets

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


def _write_rotation(obj: Any, rotation_deg: list[float], mode: str) -> None:
    """要求 Euler(度) を obj.rotation_mode に合わせて反映する（QUATERNION/AXIS_ANGLE 対応）。

    Euler モードは従来どおり rotation_euler を set/加算する。非 Euler モードでは native
    フィールド（quaternion / axis_angle）へ書き込み、見た目の向きを実際に変える
    （Codex P2: euler のみ書いて silent fail する問題を解消）。delta は quaternion 合成。
    """
    rmode = obj.rotation_mode
    if rmode in _EULER_MODES:
        if mode == "delta":
            r = obj.rotation_euler
            obj.rotation_euler = (
                r[0] + math.radians(rotation_deg[0]),
                r[1] + math.radians(rotation_deg[1]),
                r[2] + math.radians(rotation_deg[2]),
            )
        else:
            obj.rotation_euler = tuple(math.radians(a) for a in rotation_deg)
        return

    from mathutils import Euler, Quaternion, Vector  # type: ignore  # lazy: bpy 依存

    req_q = Euler([math.radians(a) for a in rotation_deg], "XYZ").to_quaternion()
    if rmode == "QUATERNION":
        cur = obj.rotation_quaternion
        obj.rotation_quaternion = (cur @ req_q) if mode == "delta" else req_q
    else:  # AXIS_ANGLE
        if mode == "delta":
            aa = obj.rotation_axis_angle  # (angle, x, y, z)
            new_q = Quaternion(Vector((aa[1], aa[2], aa[3])), aa[0]) @ req_q
        else:
            new_q = req_q
        axis, angle = new_q.to_axis_angle()
        obj.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)


def _write_location(obj: Any, location: list[float], mode: str) -> None:
    """location を **world 空間** で設定/相対移動する（Codex P2）。

    obj.location は親ローカルだが object_summary は matrix_world.translation（world）を
    報告する。親付きでも要求/報告/見た目が一致するよう、matrix_world 経由で世界座標を
    書き込む（Blender が親逆行列を考慮してローカルへ反映する）。
    """
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    mw = obj.matrix_world.copy()
    if mode == "delta":
        t = mw.translation
        mw.translation = Vector((t.x + location[0], t.y + location[1], t.z + location[2]))
    else:
        mw.translation = Vector((float(location[0]), float(location[1]), float(location[2])))
    obj.matrix_world = mw


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

    location は world 空間（親付きでも report と一致）。rotation は度入力 → ラジアンで
    rotation_mode の native 表現へ反映（QUATERNION/AXIS_ANGLE でも有効）。delta は
    location/rotation 加算（rotation は mode に応じ加算/quaternion 合成）、scale は乗算。
    location を先に適用し、その後 rotation/scale を local で上書きする（原点位置は不変）。
    """
    if location is not None:
        _write_location(obj, location, mode)
    if rotation is not None:
        _write_rotation(obj, rotation, mode)
    if scale is not None:
        if mode == "delta":
            s = obj.scale
            obj.scale = (s[0] * scale[0], s[1] * scale[1], s[2] * scale[2])
        else:
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

    共有 mesh の単一ユーザ化は呼び出し側（ops._guard_shared_mesh）が --make-single-user
    明示時のみ行う。ここでは黙って分離しない（spec §破壊防止）。焼き込み先データを持たない
    型（EMPTY/LIGHT/CAMERA）は事前に弾き、分かりやすい precondition エラーを返す。
    """
    if obj.data is None or not hasattr(obj.data, "transform"):
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"transform 適用は mesh/curve 等のデータを持つ型のみ対応（type={obj.type}）",
        )
    run_operator(
        bpy.ops.object.transform_apply,
        obj,
        message=message,
        location=location,
        rotation=rotation,
        scale=scale,
    )
    return object_summary(obj)


def select_objects(
    targets: str,
    *,
    regex: bool = False,
    type_filter: str | None = None,
    active: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """targets を選択し active を設定する（select_set / active 直接設定・op不要）。"""
    # 選択は view layer 操作。グローバル解決後に **アクティブ view layer 内**へ絞る。
    # 別シーン/除外コレクションの object を弾いてから状態を変更する（Codex P2: 状態を汚さない）。
    view_layer = bpy.context.view_layer
    vl_names = {o.name for o in view_layer.objects}
    matched = [o for o in resolve_targets(targets, regex=regex) if o.name in vl_names]
    if type_filter is not None:
        want = type_filter.upper()
        matched = [o for o in matched if o.type == want]
    if not matched:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"対象が見つかりません（アクティブ view layer 内）: {targets}"
            f"{_regex_match_hint(targets, regex=regex)}",
            category=ErrorCategory.USER_INPUT,
        )

    # active は選択状態を変更する前に解決・検証する（失敗時に状態を汚さない。Codex P2）。
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

    for o in view_layer.objects:
        o.select_set(False)
    for o in matched:
        o.select_set(True)
    view_layer.objects.active = active_obj

    if message:
        push_undo(message)
    # selected は fingerprint（sorted）と並びを揃え、解決順（版/履歴依存）に依らず決定的にする。
    return {
        "selected": sorted(o.name for o in matched),
        "count": len(matched),
        "active": active_obj.name,
    }
