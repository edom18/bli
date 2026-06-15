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
        # transform_apply 等は selected_editable_objects を反復する。現在の選択が
        # --targets と異なる場合に無関係なオブジェクトを巻き込まないよう、対象だけに絞る
        # （Codex P1）。読み取り専用の派生コンテキストだが temp_override で上書き可能。
        ov["selected_editable_objects"] = [obj]
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
    """selector からオブジェクト群を解決する（完全名 > regex）。

    完全名に一致しない場合は regex 照合。不正な正規表現は INTERNAL ではなく
    USER_INPUT エラーにする（共有リゾルバなので targets を取る全コマンドに効く。Codex P2）。
    """
    objs = bpy.data.objects
    if not regex:
        obj = objs.get(selector)
        if obj is not None:
            return [obj]
    try:
        pattern = re.compile(selector)
    except re.error as e:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"正規表現が不正です: {selector!r}: {e}",
            category=ErrorCategory.USER_INPUT,
        ) from e
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


_RAD2DEG = 57.2957795

_EULER_MODES = frozenset({"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"})


def _rotation_euler_deg(obj: Any) -> list[float]:
    """rotation_mode に依らず実効回転を Euler(度) で返す（QUATERNION/AXIS_ANGLE 対応）。

    Euler モードは従来式（`a * 57.2957795`）をそのまま使い fingerprint を不変に保つ。
    非 Euler は native 表現から Euler へ変換して報告する（Codex P2: 報告と実体の整合）。
    """
    rmode = obj.rotation_mode
    if rmode == "QUATERNION":
        euler = obj.rotation_quaternion.to_euler()
    elif rmode == "AXIS_ANGLE":
        from mathutils import Quaternion, Vector  # type: ignore  # lazy: bpy 依存

        aa = obj.rotation_axis_angle  # (angle, x, y, z)
        euler = Quaternion(Vector((aa[1], aa[2], aa[3])), aa[0]).to_euler()
    else:
        euler = obj.rotation_euler
    return [round(a * _RAD2DEG, 4) for a in euler]


def object_summary(obj: Any) -> dict[str, Any]:
    """オブジェクトの要約（info 系の共通項）。"""
    loc = obj.matrix_world.translation
    dims = obj.dimensions
    data = {
        "name": obj.name,
        "type": obj.type,
        "location": [round(loc.x, 6), round(loc.y, 6), round(loc.z, 6)],
        "dimensions": [round(dims.x, 6), round(dims.y, 6), round(dims.z, 6)],
        "rotation_euler_deg": _rotation_euler_deg(obj),
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


def _digest16(payload: dict[str, Any]) -> str:
    """JSON 化可能な状態の決定的 16 桁ハッシュ（verified 用の短ハッシュ）。"""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def object_fingerprint(obj: Any) -> str:
    """オブジェクト状態の決定的フィンガープリント（verified 用の短ハッシュ）。"""
    return _digest16(object_summary(obj))


def selection_fingerprint(selected: list[str], active: str) -> str:
    """選択集合 + active の決定的フィンガープリント（select の drift 検証用）。

    順序非依存にするため selected は sort してからハッシュする。
    """
    return _digest16({"selected": sorted(selected), "active": active})


def names_fingerprint(names: list[str]) -> str:
    """オブジェクト名集合の決定的フィンガープリント（duplicate の drift 検証用）。

    順序非依存にするため sort してからハッシュする。
    """
    return _digest16({"names": sorted(names)})


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
    type_filter: str | None = None,
    active: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """targets(name|regex) を選択し active を設定する（select_set / active 直接設定・op不要）。"""
    # 選択は view layer 操作。グローバル解決後に **アクティブ view layer 内**へ絞る。
    # 別シーン/除外コレクションの object を弾いてから状態を変更する（Codex P2: 状態を汚さない）。
    view_layer = bpy.context.view_layer
    vl_names = {o.name for o in view_layer.objects}
    matched = [o for o in resolve_targets(targets) if o.name in vl_names]  # 完全名 > regex
    if type_filter is not None:
        want = type_filter.upper()
        matched = [o for o in matched if o.type == want]
    if not matched:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"対象が見つかりません（アクティブ view layer 内）: {targets}",
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


# ---- 複製 / 削除（M6 T6.2 / 生 bpy.ops 不要・bpy.data 直接操作）----


def duplicate_object(
    obj: Any,
    *,
    linked: bool = False,
    count: int = 1,
    offset: list[float] | None = None,
    message: str | None = None,
) -> list[str]:
    """obj を count 回複製し、生成オブジェクト名の一覧を返す（op 不要・bpy.data 直接）。

    linked=False（既定）はデータ（mesh 等）も複製して独立させる。linked=True は
    obj.data を共有する（軽量だが片方の編集が両方に波及する）。offset は **world 空間**
    のオフセットで、i 番目（0始まり）の複製を (i+1)*offset だけ累積して world 位置へ置く
    （T6.1 の location と一貫）。

    offset の基準は **元 obj の評価済み matrix_world**（複製直後の new.matrix_world は
    depsgraph 未評価で誤値になり得るため、信頼できる単一基準を使う）。これにより親付き
    obj の複製でも world 位置が正しく確定する。各複製は元 object が属する全 collection に
    link する（属さない場合はシーンの既定 collection にフォールバックし、view layer から
    見えなくなる不整合を防ぐ）。
    """
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存を閉じる

    collections = list(obj.users_collection) or [bpy.context.scene.collection]
    base = obj.matrix_world.copy()  # 評価済みの信頼できる基準（親付きでも world）
    bt = base.translation
    created: list[str] = []
    for i in range(count):
        new = obj.copy()
        if not linked and obj.data is not None:
            new.data = obj.data.copy()
        for coll in collections:
            coll.objects.link(new)
        if offset is not None:
            factor = i + 1
            mw = base.copy()
            mw.translation = Vector(
                (
                    bt.x + offset[0] * factor,
                    bt.y + offset[1] * factor,
                    bt.z + offset[2] * factor,
                )
            )
            new.matrix_world = mw
        created.append(new.name)
    if message:
        push_undo(message)
    return created


def delete_object(obj: Any, *, message: str | None = None) -> None:
    """obj をシーン/データから削除する（do_unlink=True・op 不要・bpy.data 直接）。

    共有 mesh（users>=2）でも安全: object だけを除去し、データは他の利用者が残れば保持される
    （NEXT-M6 §4-B）。削除前のサマリ取得は呼び出し側（ops）が行う。
    """
    bpy.data.objects.remove(obj, do_unlink=True)
    if message:
        push_undo(message)


# ---- マテリアル（M6 T6.3 / 生 bpy.ops 不要・bpy.data 直接。M0.5 スパイクで 5.0/4.4 確認済み）----


def _principled(mat: Any) -> Any:
    """マテリアルの Principled BSDF ノードを返す（無ければ None）。"""
    if not mat.use_nodes or mat.node_tree is None:
        return None
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def _base_color(mat: Any) -> list[float] | None:
    """マテリアルの Base Color（RGBA）を返す（取得不可は None）。"""
    if mat is None:
        return None
    bsdf = _principled(mat)
    if bsdf is not None:
        bc = bsdf.inputs.get("Base Color")
        if bc is not None:
            return [round(v, 6) for v in bc.default_value]
    return [round(v, 6) for v in mat.diffuse_color]


def require_material_support(obj: Any) -> None:
    """materials を持てない型（EMPTY/LIGHT/CAMERA 等）は E_PRECONDITION で弾く。"""
    if obj.data is None or not hasattr(obj.data, "materials"):
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"マテリアル操作は mesh/curve 等のデータを持つ型のみ対応（type={obj.type}）",
        )


def find_material(name: str) -> Any | None:
    """名前でマテリアルを解決する（完全一致・無ければ None）。"""
    return bpy.data.materials.get(name)


def require_material(name: str) -> Any:
    """名前でマテリアルを解決する。無ければ E_TARGET_NOT_FOUND（require_single と同じ流儀）。

    対象未発見エラーの生成を gateway に集約する（ops は薄く保つ）。
    """
    mat = bpy.data.materials.get(name)
    if mat is None:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"マテリアルが見つかりません: {name}（既存名を指定するか create で作成）",
            category=ErrorCategory.USER_INPUT,
        )
    return mat


def create_material(name: str, color: list[float] | None) -> Any:
    """新規マテリアルを作る（use_nodes + Principled Base Color + diffuse_color）。

    name は既存と衝突すると Blender が name.001 等に自動採番する（戻り値の mat.name が真）。
    color(RGBA) 指定時は Principled の Base Color とビューポート表示色の双方へ反映する。
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    if color is not None:
        rgba = (float(color[0]), float(color[1]), float(color[2]), float(color[3]))
        bsdf = _principled(mat)
        if bsdf is not None:
            bc = bsdf.inputs.get("Base Color")
            if bc is not None:
                bc.default_value = rgba
        mat.diffuse_color = rgba
    return mat


def _target_slot_index(obj: Any) -> int | None:
    """assign/create が書き込むスロット index を返す（None = 空スロットで append が必要）。

    `material_write_touches_mesh_data`（ガード判定）と `assign_material`（実書き込み）が
    **同一の書き込み先**を見るための単一窓口。両者が別々に active_material_index をクランプして
    ズレると「ガードが見る slot」と「実際に書く slot」が食い違い、共有 mesh への意図しない波及を
    招くため、ここに集約する（設計レビュー P2）。
    """
    mats = obj.data.materials
    if len(mats) == 0:
        return None
    idx = obj.active_material_index
    if idx < 0 or idx >= len(mats):
        idx = 0
    return idx


def material_write_touches_mesh_data(obj: Any) -> bool:
    """assign/create の付与がメッシュデータ（共有され得る）を書き換えるか判定する（Codex P2）。

    空スロット（append で DATA slot を新設）か、書き込み先スロットが DATA リンクなら True。
    OBJECT リンクなら object 限定の書き込みで共有 mesh を触らないため False（共有ガード不要・
    --make-single-user による不要な分離も避ける）。書き込み先は `_target_slot_index` で
    assign_material と一致させる。
    """
    idx = _target_slot_index(obj)
    if idx is None:
        return True  # append は DATA slot を作る（共有 mesh に波及し得る）
    return obj.material_slots[idx].link == "DATA"


def assign_material(obj: Any, mat: Any) -> int:
    """mat を obj に付与する（空スロットなら append・あれば書き込み先スロットを置換）。

    付与したスロット index を返す（判断: active 置換・空なら追加。複数スロット運用は後続）。
    書き込みは `material_slots[idx].material` 経由で **slot.link を尊重**する（OBJECT リンクの
    slot では object 側、DATA リンクでは mesh データ側へ正しく反映する。Codex P2-B）。共有 mesh
    の DATA slot 置換が兄弟へ波及する件は呼び出し側（ops._guard_shared_mesh）が単一ユーザ化で防ぐ。
    書き込み先 index は `_target_slot_index`（material_write_touches_mesh_data と共有）で決める。
    """
    idx = _target_slot_index(obj)
    if idx is None:
        obj.data.materials.append(mat)  # 新規スロット作成は data 経由（DATA リンクで生成される）
        return 0
    obj.material_slots[idx].material = mat
    return idx


def list_object_materials(obj: Any) -> list[dict[str, Any]]:
    """obj のマテリアルスロット一覧（slot index / name / link / base_color）を返す。

    実効スロット（slot.link 尊重）を `material_slots` 経由で読む。OBJECT リンクの slot では
    object 側のマテリアルを報告する（data.materials を直接見ると乖離する。Codex P2-B）。
    """
    out: list[dict[str, Any]] = []
    for i, slot in enumerate(obj.material_slots):
        mat = slot.material
        out.append(
            {
                "slot": i,
                "name": mat.name if mat is not None else None,
                "link": slot.link,
                "base_color": _base_color(mat),
            }
        )
    return out


def material_fingerprint(obj: Any) -> str:
    """obj のマテリアル状態の決定的フィンガープリント（material の drift 検証用）。"""
    return _digest16({"name": obj.name, "materials": list_object_materials(obj)})


# ---- モディファイア（M6 T6.4 / add/remove/list は bpy.data 直接・apply は run_operator）----
#
# modifier は **オブジェクト単位**（obj.modifiers）。add/remove/list は mesh データを触らないため
# 共有 mesh ガード不要。**apply のみ** mesh へ焼き込む（apply-transform と同様にガードが要る）。

_MIRROR_AXES = ("X", "Y", "Z")

# モディファイアを持てるオブジェクト型（これ以外は E_PRECONDITION。非対応型への
# obj.modifiers.new() は生 RuntimeError になり INTERNAL 誤分類されるのを防ぐ）。
_MODIFIER_OBJECT_TYPES = frozenset(
    {"MESH", "CURVE", "SURFACE", "FONT", "LATTICE", "VOLUME", "GREASEPENCIL", "POINTCLOUD"}
)


def require_modifier_support(obj: Any) -> None:
    """モディファイアを持てない型（EMPTY/LIGHT/CAMERA 等）は E_PRECONDITION で弾く。

    material の require_material_support と同じ流儀。USER_INPUT 的な型ミスを INTERNAL に
    しないための前提検証（個別 modifier×型 の細かな非対応は add_modifier 側で捕捉する）。
    """
    if obj.type not in _MODIFIER_OBJECT_TYPES:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"モディファイアを持てない型です（type={obj.type}）",
        )


def _modifier_summary(mod: Any) -> dict[str, Any]:
    """モディファイア1件の要約（name/type + 種類別の主要プロパティ）。"""
    data: dict[str, Any] = {"name": mod.name, "type": mod.type}
    t = mod.type
    if t == "MIRROR":
        data["axes"] = [ax for ax, on in zip(_MIRROR_AXES, mod.use_axis, strict=True) if on]
    elif t == "SUBSURF":
        data["levels"] = mod.levels
    elif t == "SOLIDIFY":
        data["thickness"] = round(mod.thickness, 6)
    elif t == "DECIMATE":
        data["ratio"] = round(mod.ratio, 6)
    elif t == "BOOLEAN":
        data["operation"] = mod.operation
        data["object"] = mod.object.name if mod.object is not None else None
    return data


def require_modifier(obj: Any, name: str) -> Any:
    """名前でモディファイアを解決する。無ければ E_TARGET_NOT_FOUND。"""
    mod = obj.modifiers.get(name)
    if mod is None:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"モディファイアが見つかりません: {name}",
            category=ErrorCategory.USER_INPUT,
        )
    return mod


def add_modifier(
    obj: Any,
    mod_type: str,
    *,
    name: str | None = None,
    axis: str | None = None,
    levels: int | None = None,
    thickness: float | None = None,
    ratio: float | None = None,
    operation: str | None = None,
    operand: Any = None,
    message: str | None = None,
) -> dict[str, Any]:
    """obj にモディファイアを追加し、要約を返す（op 不要・obj.modifiers 直接）。

    name 省略時は Blender 既定名（type 名）。種類別の主要プロパティのみ設定する（最小）。
    対象型がこの modifier を受け付けない場合（生 RuntimeError）は E_PRECONDITION に変換する。
    """
    try:
        mod = obj.modifiers.new(name or mod_type.title(), mod_type)
    except RuntimeError as e:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"この型にこのモディファイアは追加できません（type={obj.type}, modifier={mod_type}）: {e}",
        ) from e
    if mod_type == "MIRROR" and axis is not None:
        for i, ax in enumerate(_MIRROR_AXES):
            mod.use_axis[i] = ax == axis
    elif mod_type == "SUBSURF" and levels is not None:
        mod.levels = levels
        mod.render_levels = levels
    elif mod_type == "SOLIDIFY" and thickness is not None:
        mod.thickness = thickness
    elif mod_type == "DECIMATE" and ratio is not None:
        mod.ratio = ratio
    elif mod_type == "BOOLEAN":
        if operation is not None:
            mod.operation = operation
        if operand is not None:
            mod.object = operand
    if message:
        push_undo(message)
    return _modifier_summary(mod)


def remove_modifier(obj: Any, name: str, *, message: str | None = None) -> None:
    """名前でモディファイアを削除する（無効名は E_TARGET_NOT_FOUND・op 不要）。"""
    mod = require_modifier(obj, name)
    obj.modifiers.remove(mod)
    if message:
        push_undo(message)


def list_modifiers(obj: Any) -> list[dict[str, Any]]:
    """obj のモディファイアスタックを順に要約する（スタック順は意味があるので保持）。"""
    return [_modifier_summary(m) for m in obj.modifiers]


def apply_modifier(obj: Any, name: str, *, message: str | None = None) -> dict[str, Any]:
    """モディファイアを mesh データへ適用する（operator 経由・破壊的）。

    無効名の事前検証・共有 mesh ガードは呼び出し側（ops）が apply 前に行う。
    """
    run_operator(bpy.ops.object.modifier_apply, obj, message=message, modifier=name)
    return {"applied": name, "modifiers": list_modifiers(obj)}


def modifiers_fingerprint(obj: Any) -> str:
    """obj のモディファイアスタック状態の決定的フィンガープリント（drift 検証用）。

    型別の主要プロパティ込み（list_modifiers）でハッシュするため、名前のみの
    object_fingerprint より param 変化に敏感。add/remove/list の drift 検証に使う。
    """
    return _digest16({"name": obj.name, "modifiers": list_modifiers(obj)})


# ---- メッシュ編集の前提/統計/fingerprint（M7 T7.1 / bmesh 操作は bmesh_ops.py）----


def require_mesh(obj: Any) -> None:
    """mesh 型でない（EMPTY/CURVE/LIGHT 等）対象は E_PRECONDITION で弾く。

    require_modifier_support/require_material_support と同じ流儀。USER_INPUT 的な型ミスを
    INTERNAL にしないための前提検証（bmesh 編集は mesh データを直接書き換えるため）。
    """
    if obj.type != "MESH" or obj.data is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"メッシュ編集は mesh 型のみ対応です（type={obj.type}）",
        )


def mesh_stats(obj: Any) -> dict[str, int]:
    """mesh データの頂点/辺/面数（編集結果の before/after 報告に使う）。"""
    me = obj.data
    return {
        "vertices": len(me.vertices),
        "edges": len(me.edges),
        "polygons": len(me.polygons),
    }


def mesh_fingerprint(obj: Any) -> str:
    """mesh データの幾何の決定的フィンガープリント（mesh 編集の drift 検証用）。

    頂点/辺/面数に加え、面法線（巻き順の本質）を取り込む。これにより、頂点数が
    変わらない recalc-normals（法線の向きだけが変わる）でも drift を検出できる
    （object_fingerprint は object_summary 由来で頂点数しか見ず recalc を検出できない。§6e）。
    法線は丸めてハッシュするので 5.0/4.4 で軸整列メッシュは同値になる。
    `+ 0.0` で符号付きゼロを正規化する（`f"{-0.0:.4f}"` は `"-0.0000"` となり `"0.0000"` と
    文字列が変わる＝法線反転で生じる -0.0 が版間で fingerprint をぶらすのを防ぐ）。
    """
    norms = hashlib.sha256()
    for poly in obj.data.polygons:
        n = poly.normal
        norms.update(f"{n.x + 0.0:.4f},{n.y + 0.0:.4f},{n.z + 0.0:.4f};".encode())
    # 幾何カウントは mesh_stats を単一の真実とする（重複定義のドリフトを防ぐ）。
    return _digest16({**mesh_stats(obj), "normals": norms.hexdigest()[:16]})


def stats_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """before→after の頂点/辺/面の増減（符号付き＝追加は正・削減は負）。

    extrude/bevel/inset（追加）も decimate/boolean（削減もあり得る）も同じ符号付き表現で
    返せるよう中立な「delta」にする（mesh_stats を持つ gateway に集約し bmesh_ops と共有）。
    """
    return {k: after[k] - before[k] for k in after}


# ---- メッシュ編集 heavy（M7 T7.3 / bmesh に boolean/decimate が無く modifier add+apply 経由）----
#
# boolean / decimate は bmesh.ops に相当が無い（スパイク E3 で両版 False を確認）。モディファイアを
# 追加して run_operator(modifier_apply) で mesh へ焼き込む（生 bpy.ops は gateway のみ＝AST guard 準拠）。
# 共有 mesh の単一ユーザ化ガードは呼び出し側（ops._guard_shared_mesh）が行う。結果は T7.2 と同じ
# `{<param>, delta, stats}`（stats=編集後 / delta=符号付き増減）。


def mesh_decimate(obj: Any, *, ratio: float, message: str | None = None) -> dict[str, Any]:
    """DECIMATE モディファイア（COLLAPSE・ratio）を追加して即適用し、ポリゴンを削減する。

    bmesh に decimate 相当が無いため modifier 経由（add+apply）にフォールバックする（研究 §E3）。
    ratio=1.0 は無削減（delta 0）だが modifier_apply は mesh を焼き直す（破壊的書き込み）。
    """
    before = mesh_stats(obj)
    mod = obj.modifiers.new("BLI_Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    run_operator(bpy.ops.object.modifier_apply, obj, message=message, modifier=mod.name)
    after = mesh_stats(obj)
    return {"ratio": ratio, "delta": stats_delta(before, after), "stats": after}


def mesh_boolean(
    obj: Any, operand: Any, *, operation: str, message: str | None = None
) -> dict[str, Any]:
    """BOOLEAN モディファイア（operand を相手・operation）を追加して即適用する。

    bmesh に boolean が無いため modifier 経由（add+apply・solver は既定 EXACT）。operand の
    **world 位置は Blender が両者の matrix_world から解決**するため手動 world→local 変換は不要
    （extrude と異なる。研究 §E3）。operand 自体は read-only（編集されない）。
    """
    before = mesh_stats(obj)
    mod = obj.modifiers.new("BLI_Boolean", "BOOLEAN")
    mod.operation = operation
    mod.object = operand
    run_operator(bpy.ops.object.modifier_apply, obj, message=message, modifier=mod.name)
    after = mesh_stats(obj)
    return {
        "operation": operation,
        "with": operand.name,
        "delta": stats_delta(before, after),
        "stats": after,
    }
