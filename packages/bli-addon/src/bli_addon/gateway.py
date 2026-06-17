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


def _require_gui_for_undo(verb: str) -> None:
    """undo/redo は GUI 前提（--background では undo スタックが不定・研究 §E7）。"""
    if bpy.app.background:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"{verb} には GUI が必要です（--background では undo/redo は機能しません）",
        )


def _step_undo_stack(op: Any, steps: int) -> int:
    """undo/redo operator を steps 回適用し、実際に適用できた段数を返す（スタック端で頭打ち）。

    bare 呼び出しで GUI では context override 不要（§E7・両版確認済み）。スタック端では `FINISHED`
    以外（CANCELLED）になる版と RuntimeError を投げる版の両方を「これ以上進めない＝端」として
    break で正規化し、INTERNAL 化を避ける（§6e）。
    """
    applied = 0
    for _ in range(steps):
        try:
            result = op()
        except RuntimeError:  # スタック端で raise する版も端として扱う（未捕捉→INTERNAL を防ぐ）
            break
        if "FINISHED" in result:
            applied += 1
        else:  # CANCELLED 等＝これ以上戻せない/進められない（スタック端）
            break
    return applied


def undo_steps(steps: int) -> int:
    """グローバル undo スタックを steps 段戻す。実際に適用できた段数を返す（GUI 必須・§E7）。"""
    _require_gui_for_undo("undo")
    return _step_undo_stack(bpy.ops.ed.undo, steps)


def redo_steps(steps: int) -> int:
    """グローバル undo スタックを steps 段進める（やり直す）。実際に適用できた段数を返す（GUI 必須）。"""
    _require_gui_for_undo("redo")
    return _step_undo_stack(bpy.ops.ed.redo, steps)


def scene_state_fingerprint() -> str:
    """シーン全体の粗いフィンガープリント（undo/redo の状態変化検証用）。

    全オブジェクトの name/type と matrix_world（丸め）をハッシュする。transform/add/delete の変化は
    捉えるが mesh データ内部の編集（bevel/merge 等）までは見ない（undo の粗い drift 指標・v1）。
    そのため matrix を変えない undo（mesh 内部編集のみの巻き戻し）では前後で同一値になり得る。
    読み取り前に view_layer.update() で matrix を最新化する。
    """
    bpy.context.view_layer.update()
    items = [
        {
            "name": o.name,
            "type": o.type,
            "matrix": [round(v, 6) for row in o.matrix_world for v in row],
        }
        for o in sorted(bpy.data.objects, key=lambda x: x.name)
    ]
    return _digest16({"objects": items})


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
    return {
        "scene": scene.name,
        "object_count": len(bpy.data.objects),
        "objects": [object_summary(o) for o in scene.objects],
        # unit_settings の要約は print-setup と同一窓口（_unit_settings_dict）で SSOT 化する
        # （scene-info と print-setup で単位表現がドリフトしないように。設計レビュー P2）。
        "unit_settings": _unit_settings_dict(scene.unit_settings),
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
# boolean / decimate は bmesh.ops に相当が無い（スパイク E3 で両版 False を確認）。既存の
# add_modifier（型別プロパティ設定 + 非対応型の RuntimeError→E_PRECONDITION 変換）と apply_modifier
# （run_operator 経由・生 bpy.ops は gateway のみ＝AST guard 準拠）を**再利用**して mesh へ焼き込む。
# 共有 mesh の単一ユーザ化ガードは呼び出し側（ops._guard_shared_mesh）が行う。結果は T7.2 と同じ
# `{<param>, delta, stats}`（stats=編集後 / delta=符号付き増減）。
#
# boolean/decimate は対象を**空/退化 mesh にし得る**（INTERSECT で非重複・decimate ratio→0 等）。
# success は operator 完了を表し幾何的健全性は保証しない（呼び出し側は stats/delta で確認できる）。


def _add_then_apply(obj: Any, mod_type: str, message: str | None, **props: Any) -> None:
    """モディファイアを追加して即適用する（add_modifier+apply_modifier を再利用・アトミック）。

    apply が失敗したら追加したモディファイアを撤去してから再送出する（中途状態でゴミの
    モディファイアを残さない）。undo 境界は apply（run_operator）が1つだけ作る（add は無 undo）。
    """
    summary = add_modifier(obj, mod_type, **props)  # message なし＝add では undo push しない
    name = summary["name"]
    try:
        apply_modifier(obj, name, message=message)
    except BaseException:
        leftover = obj.modifiers.get(name)
        if leftover is not None:
            obj.modifiers.remove(leftover)
        raise


def mesh_decimate(obj: Any, *, ratio: float, message: str | None = None) -> dict[str, Any]:
    """DECIMATE モディファイア（COLLAPSE・ratio）を追加して即適用し、ポリゴンを削減する。

    bmesh に decimate 相当が無いため modifier 経由（add+apply）にフォールバックする（研究 §E3・
    decimate_type 既定は両版とも COLLAPSE）。ratio=1.0 は無削減（delta 0）だが modifier_apply は
    mesh を焼き直す（破壊的書き込み＝共有 mesh は単一ユーザ化が必要）。
    """
    before = mesh_stats(obj)
    _add_then_apply(obj, "DECIMATE", message, ratio=ratio)
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
    _add_then_apply(obj, "BOOLEAN", message, operation=operation, operand=operand)
    after = mesh_stats(obj)
    return {
        "operation": operation,
        "with_object": operand.name,
        "delta": stats_delta(before, after),
        "stats": after,
    }


# ---- 直立補正（M8 T8.2 / straighten・シナリオ2）----
#
# メソッド: reset（回転を identity に）/ world-align（指定 local 軸を world up へ最小回転で合わせる）
# / pca（頂点分布の最大分散軸を up へ）/ floor（up 方向の最下点を接地）/ angle（world 軸まわりに
# 指定角回転）/ align-vector（from_dir を to_dir へ最小回転）/ reference（参照 obj の軸方向へ合わせる）。
# floor 以外は **object 回転のみ**変更（mesh 非破壊・共有 mesh でも安全）。floor は平行移動のみ。
# pca は mesh 頂点が必要。angle/align-vector/reference は基準指定（エージェント算出の補正を安全に適用・
# transform 迂回の解消・実地フィードバック #4）。`--bake-rotation` の mesh 焼き込みは呼び出し側（ops）が
# apply_transform 経路（共有ガード付き）で行う。研究 §E4 で 5.0.1/4.4.3 確認済み。
# matrix_world は読み取り前に view_layer.update() で最新化する（background での stale 対策・§E4）。

_AXIS_VECTORS: dict[str, tuple[float, float, float]] = {
    "+X": (1.0, 0.0, 0.0),
    "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0),
    "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0),
    "-Z": (0.0, 0.0, -1.0),
}
_LOCAL_AXIS_LETTERS = ("X", "Y", "Z")
_LOCAL_AXIS_UNIT: dict[str, tuple[float, float, float]] = {
    "X": (1.0, 0.0, 0.0),
    "Y": (0.0, 1.0, 0.0),
    "Z": (0.0, 0.0, 1.0),
}
# pca: この値以下の最大固有値（分散）は主成分を決められない（点が一致/直線退化）。
_PCA_MIN_VARIANCE = 1e-12
# pca: 原点→重心 の射影がこの閾値以下なら符号が不定（中心対称）→ 正準符号でtie-break。
_PCA_SIGN_EPS = 1e-9
# rotation_difference が anti-parallel（真逆）で軸不定になる閾値。
_ANTIPARALLEL_EPS = 1e-9


def require_geometry(obj: Any) -> None:
    """bbox を持たない型（EMPTY/LIGHT/CAMERA 等）は E_PRECONDITION で弾く（floor 用）。

    require_mesh/require_material_support と同じ流儀。world_bbox は退化（全隅同一）を None で
    返すので、それを接地不能の判定に使う（番号分岐せず値で判定）。
    """
    if world_bbox(obj) is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"接地補正にはジオメトリ（bbox）が必要です（type={obj.type}）",
        )


def _reset_rotation(obj: Any) -> None:
    """rotation_mode に依らず回転を identity にする（QUATERNION/AXIS_ANGLE 対応）。"""
    rmode = obj.rotation_mode
    if rmode == "QUATERNION":
        obj.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    elif rmode == "AXIS_ANGLE":
        obj.rotation_axis_angle = (0.0, 0.0, 0.0, 1.0)  # angle=0 → identity（軸は任意）
    else:
        obj.rotation_euler = (0.0, 0.0, 0.0)


def _local_axis_world(obj: Any, signed_axis: str) -> Any:
    """signed local 軸（"+Z"/"-X" 等）の world 方向（正規化・scale 除去）を返す。"""
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    sign = -1.0 if signed_axis[0] == "-" else 1.0
    base = Vector(_LOCAL_AXIS_UNIT[signed_axis[-1]]) * sign
    return (obj.matrix_world.to_quaternion() @ base).normalized()


def _apply_world_rotation(obj: Any, delta_quat: Any) -> None:
    """delta_quat を world 回転へ前合成し、原点・スケール不変で書き戻す（§E4）。

    decompose→LocRotScale で loc/scale を保ったまま回転だけ差し替える。親付きでも matrix_world
    setter が親逆行列を考慮するため world 空間で正しく整列する。
    """
    from mathutils import Matrix  # type: ignore  # lazy: bpy 依存

    loc, rot, scale = obj.matrix_world.decompose()
    obj.matrix_world = Matrix.LocRotScale(loc, delta_quat @ rot, scale)


def _rotation_to(cur: Any, target: Any) -> Any:
    """cur を target へ重ねる最小回転 quaternion（anti-parallel を決定的に扱う）。

    `Vector.rotation_difference` は cur と target が **真逆**のとき軸が不定（垂直な任意軸まわり
    180°）で版/数値依存に揺れる。整列軸（cur→target）は乗るが直交2軸（見た目の向き）が
    非決定になり golden/fingerprint がぶれる。anti-parallel を検出したら target に直交する
    **固定の**軸まわり 180° を返して決定化する。
    """
    from mathutils import Quaternion, Vector  # type: ignore  # lazy: bpy 依存

    if cur.dot(target) < -1.0 + _ANTIPARALLEL_EPS:
        # target と平行でない決定的な基準ベクトルとの外積で垂直軸を作る。
        ref = Vector((1.0, 0.0, 0.0)) if abs(target.x) < 0.9 else Vector((0.0, 1.0, 0.0))
        perp = target.cross(ref).normalized()
        return Quaternion(perp, math.pi)
    return cur.rotation_difference(target)


def _min_up_projection(obj: Any, up: Any) -> float:
    """bbox 8隅を up 方向へ射影した最小値（floor の接地量と min_up 報告の単一窓口・DRY）。"""
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    return min((obj.matrix_world @ Vector(c)).dot(up) for c in obj.bound_box)


def _world_align(obj: Any, up: Any, axis: str | None) -> str:
    """指定（または up に最も近い）local 軸を up へ最小回転で合わせ、合わせた signed 軸を返す。

    axis 指定時はその local 軸（± のうち up に近い向き）。省略時は ±X/±Y/±Z の6方向から up に
    最も近い signed 軸を自動選択する（spec『最も近い主軸を合わせる』）。
    """
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    if axis is not None:
        wd = (obj.matrix_world.to_quaternion() @ Vector(_LOCAL_AXIS_UNIT[axis])).normalized()
        sign = "+"
        if wd.dot(up) < 0.0:  # 反対向きの方が近ければ符号反転
            wd = -wd
            sign = "-"
        cur, chosen = wd, sign + axis
    else:
        best: tuple[float, Any, str] | None = None
        for letter in _LOCAL_AXIS_LETTERS:
            base = (
                obj.matrix_world.to_quaternion() @ Vector(_LOCAL_AXIS_UNIT[letter])
            ).normalized()
            for sign, scalar in (("+", 1.0), ("-", -1.0)):
                wd = base * scalar
                d = wd.dot(up)
                if best is None or d > best[0]:
                    best = (d, wd, sign + letter)
        if best is None:  # 3軸×2符号で必ず確定（防御・-O でも安全に）
            raise _op_error(ErrorCode.E_PRECONDITION, "world-align の軸を決定できません")
        _, cur, chosen = best
    _apply_world_rotation(obj, _rotation_to(cur, up))
    return chosen


def _principal_axis(obj: Any, *, up: Any = None, up_hint: str = "auto") -> tuple[Any, list[float]]:
    """world 空間頂点分布の最大分散軸（principal）と固有値（昇順）を返す。

    共分散（対称 3x3）を numpy.linalg.eigh で分解し最大固有値の固有ベクトルを主成分とする
    （numpy は Blender 同梱・§E4）。PCA は符号不定なので符号を一意化する:
    - `up_hint="auto"`（既定）: **原点→重心 方向**に揃える（重心が偏る側を + に・決定的）。
    - `up_hint="current"`: 主成分のうち **up に近い向き**を + にする（principal·up>=0）。ベースが重い
      スキャン物体で重心が下に寄り「下」を + と誤判定→上下反転する問題を防ぐ（実地フィードバック #5）。
    分散が無い（点が一致/退化）場合は E_PRECONDITION。
    """
    import numpy as np  # type: ignore  # lazy: Blender 同梱（§E4）
    from mathutils import Vector  # type: ignore

    mw = obj.matrix_world
    verts = [mw @ v.co for v in obj.data.vertices]
    n = len(verts)
    if n < 2:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"pca には2頂点以上が必要です（頂点数={n}）",
        )
    cx = sum(v.x for v in verts) / n
    cy = sum(v.y for v in verts) / n
    cz = sum(v.z for v in verts) / n
    sxx = syy = szz = sxy = sxz = syz = 0.0
    for v in verts:
        dx, dy, dz = v.x - cx, v.y - cy, v.z - cz
        sxx += dx * dx
        syy += dy * dy
        szz += dz * dz
        sxy += dx * dy
        sxz += dx * dz
        syz += dy * dz
    cov = np.array([[sxx, sxy, sxz], [sxy, syy, syz], [sxz, syz, szz]]) / n
    eigvals, eigvecs = np.linalg.eigh(cov)  # 昇順固有値・正規直交固有ベクトル
    if float(eigvals[2]) <= _PCA_MIN_VARIANCE:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            "頂点分布に広がりが無く主成分を決定できません（pca には立体的な mesh が必要）",
        )
    principal = Vector((float(eigvecs[0, 2]), float(eigvecs[1, 2]), float(eigvecs[2, 2])))
    if up_hint == "current" and up is not None:
        # 現在の up に近い向きを + にする（principal·up>=0）→ up へ最小回転で合わせ反転を防ぐ。
        # principal⊥up（傾き≈90°）の退化は重心方向で tie-break して決定性を保つ。ここでの d は
        # 正規化ベクトル同士の内積（射影距離ではない）。_PCA_SIGN_EPS(1e-9) 流用は「ほぼ真の直交
        # （≈90°）」だけを退化扱いにする閾値として機能する（値域は異なるが両者とも ≈0 判定）。
        d = principal.dot(up)
        near_perp = abs(d) <= _PCA_SIGN_EPS
        centroid_below = (Vector((cx, cy, cz)) - mw.translation).dot(principal) < 0.0
        if d < -_PCA_SIGN_EPS or (near_perp and centroid_below):
            principal = -principal
    else:
        # auto: 原点→重心 方向に揃える（重心が偏る側を +）。重心が原点に一致（中心対称・射影 ≈ 0）
        # の退化時は符号が不定になるため、主成分の最大成分を正にする正準符号で tie-break する
        # （決定的・5.0/4.4 同値を保つ）。
        offset = (Vector((cx, cy, cz)) - mw.translation).dot(principal)
        if offset < -_PCA_SIGN_EPS:
            principal = -principal
        elif abs(offset) <= _PCA_SIGN_EPS:
            comps = (principal.x, principal.y, principal.z)
            dominant = max(range(3), key=lambda i: abs(comps[i]))
            if comps[dominant] < 0.0:
                principal = -principal
    return principal.normalized(), [round(float(x), 8) for x in eigvals]


def _floor(obj: Any, up: Any) -> list[float]:
    """up 方向の最下点を up=0 平面へ接地する（平行移動のみ）。適用した world 移動量を返す。"""
    from mathutils import Matrix  # type: ignore

    shift = -_min_up_projection(obj, up) * up
    obj.matrix_world = Matrix.Translation(shift) @ obj.matrix_world
    return [round(shift.x, 6), round(shift.y, 6), round(shift.z, 6)]


def _angle_rotate(obj: Any, axis: str, degrees: float) -> dict[str, Any]:
    """world 軸 axis（X/Y/Z）まわりに degrees 度回転する delta を前合成する（基準指定・#4）。

    エージェントが算出した補正回転を straighten 経由で安全に適用する method。符号は degrees に
    含む（X/Y/Z は無符号）。_apply_world_rotation で原点・スケール不変・親付きでも正しく整列する。
    """
    from mathutils import Quaternion, Vector  # type: ignore  # lazy: bpy 依存

    # X/Y/Z の単位ベクトルは world/local 共通なので _LOCAL_AXIS_UNIT を流用（ここでは world 軸）。
    world_axis = Vector(_LOCAL_AXIS_UNIT[axis])
    _apply_world_rotation(obj, Quaternion(world_axis, math.radians(degrees)))
    return {"axis": axis, "degrees": round(float(degrees), 6)}


def _align_vector(obj: Any, from_dir: Any, to_dir: Any) -> dict[str, Any]:
    """from_dir(world) を to_dir(world) へ重ねる最小回転を前合成する（基準指定・#4 の本命）。

    エージェントが計測した「現在の向き」→「目標の向き」を直接渡せる。同一メッシュ内の支柱など
    別オブジェクト化できない基準でも、向きを数値で与えれば straighten の作法（dry-run/bake/共有
    ガード）で安全に適用できる。anti-parallel は _rotation_to が決定化する。
    """
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    src = Vector(from_dir).normalized()
    dst = Vector(to_dir).normalized()
    delta = _rotation_to(src, dst)
    _apply_world_rotation(obj, delta)
    after = (delta @ src).normalized()
    # from→to のなす角（入力ベクトル由来）。anti-parallel 決定化後の実回転量とは概念的に別だが
    # 通常ケースでは一致する。呼び出し側が補正の妥当性を即チェックできる目安として返す。
    angle = math.degrees(math.acos(max(-1.0, min(1.0, src.dot(dst)))))
    return {
        "from_dir": [round(v, 6) for v in src],
        "to_dir": [round(v, 6) for v in dst],
        "from_world_after": [round(v, 6) for v in after],
        "angle_deg": round(angle, 4),
    }


def _reference_align(obj: Any, ref_obj: Any, ref_axis: str, axis: str | None) -> dict[str, Any]:
    """参照 obj の ref_axis(signed local)の world 方向へ、対象の axis(local)を合わせる（#4）。

    world-align の「合わせる目標」を world up から **参照オブジェクトの軸方向** へ差し替えただけ
    （_world_align をそのまま再利用）。ガイド用の別オブジェクトの向きに揃えたい場合に使う。
    axis 省略時は対象の最近 signed local 軸を自動選択（world-align と同じ挙動）。
    """
    target_dir = _local_axis_world(ref_obj, ref_axis)
    chosen = _world_align(obj, target_dir, axis)
    return {
        "reference": ref_obj.name,
        "ref_axis": ref_axis,
        "reference_world": [round(v, 6) for v in target_dir],
        "axis": chosen,
    }


def _snapshot_transform(obj: Any) -> dict[str, Any]:
    """transform チャンネルの完全スナップショット（dry-run の厳密復元用）。

    補正は matrix_world / 回転チャンネル経由で loc/rot/scale を書き換える。全表現（euler/
    quaternion/axis_angle）と mode/loc/scale を raw 値で控え、restore で厳密に戻す（matrix_world
    の再代入だと decompose の微小ドリフトが乗るため raw チャンネルを使う）。
    """
    return {
        "mode": obj.rotation_mode,
        "location": tuple(obj.location),
        "rotation_euler": tuple(obj.rotation_euler),
        "rotation_quaternion": tuple(obj.rotation_quaternion),
        "rotation_axis_angle": tuple(obj.rotation_axis_angle),
        "scale": tuple(obj.scale),
    }


def _restore_transform(obj: Any, snap: dict[str, Any]) -> None:
    """_snapshot_transform の状態へ厳密に戻す（全チャンネルを raw 値で復元）。"""
    obj.location = snap["location"]
    obj.rotation_euler = snap["rotation_euler"]
    obj.rotation_quaternion = snap["rotation_quaternion"]
    obj.rotation_axis_angle = snap["rotation_axis_angle"]
    obj.scale = snap["scale"]
    obj.rotation_mode = snap["mode"]


def straighten_object(
    obj: Any,
    *,
    method: str,
    up_axis: str = "+Z",
    axis: str | None = None,
    up_hint: str = "auto",
    degrees: float | None = None,
    from_dir: Any = None,
    to_dir: Any = None,
    reference_obj: Any = None,
    ref_axis: str | None = None,
    message: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """直立補正を実行し、補正結果（回転/接地/整列の golden 値）を返す。

    reset/world-align/pca/angle/align-vector/reference は object 回転のみ・floor は平行移動のみ
    変更する（mesh 非破壊）。`--bake-rotation` の mesh 焼き込みは呼び出し側（ops）が apply_transform
    経路で行う。matrix_world は読み取り前に view_layer.update() で最新化する（§E4 の stale 対策）。
    `dry_run=True` は適用→レポート読取→**厳密復元**で、副作用なく計画値を返す（push_undo もしない・
    実地フィードバック #2）。pca は `up_hint` で符号決定を切り替え、`tilt_from_up_deg`（up からの
    傾き角・符号非依存の鋭角）を併せて返す（#5/#6）。angle/align-vector/reference はエージェントが
    算出した補正を straighten 経由で安全に適用する基準指定 method（transform 迂回の解消・#4）。
    """
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    bpy.context.view_layer.update()  # §E4: rotation 直接設定後の stale を避ける
    up = Vector(_AXIS_VECTORS[up_axis])
    data: dict[str, Any] = {"name": obj.name, "method": method, "up_axis": up_axis}
    snap = _snapshot_transform(obj) if dry_run else None

    if method == "reset":
        _reset_rotation(obj)
    elif method == "world-align":
        data["axis"] = _world_align(obj, up, axis)
    elif method == "pca":
        principal, eigvals = _principal_axis(obj, up=up, up_hint=up_hint)
        delta = _rotation_to(principal, up)
        _apply_world_rotation(obj, delta)
        data["eigenvalues"] = eigvals
        data["principal_world"] = [round(v, 6) for v in principal]
        data["principal_world_after"] = [round(v, 6) for v in (delta @ principal).normalized()]
        # up からの傾き角（鋭角・符号非依存）。呼び出し側が補正の妥当性を即チェックできる。
        data["tilt_from_up_deg"] = round(
            math.degrees(math.acos(min(1.0, abs(principal.dot(up))))), 4
        )
    elif method == "angle":
        if axis is None or degrees is None:  # ops が必須を保証するが gateway も防御（型も絞る）
            raise _op_error(ErrorCode.E_PRECONDITION, "angle には axis と degrees が必要です")
        data.update(_angle_rotate(obj, axis, degrees))
    elif method == "align-vector":
        if from_dir is None:  # ops が必須を保証するが gateway も防御（INTERNAL 化を避ける・§6e）
            raise _op_error(ErrorCode.E_PRECONDITION, "align-vector には from_dir が必要です")
        # to_dir 省略時は up（「現在の向きを up へ立てる」が既定・#4）。
        data.update(_align_vector(obj, from_dir, to_dir if to_dir is not None else tuple(up)))
    elif method == "reference":
        if reference_obj is None or ref_axis is None:  # 同上（ops 保証 + gateway 防御）
            raise _op_error(ErrorCode.E_PRECONDITION, "reference には参照オブジェクトが必要です")
        data.update(_reference_align(obj, reference_obj, ref_axis, axis))
    elif method == "floor":
        data["floor_offset"] = _floor(obj, up)
    else:  # method は ENUM 検証済みのため到達不能（新 method の分岐漏れ検出の防御）。
        raise _op_error(ErrorCode.E_PRECONDITION, f"未対応の straighten method: {method}")

    bpy.context.view_layer.update()  # 補正後の matrix_world を確定

    data["rotation_euler_deg"] = _rotation_euler_deg(obj)
    if method in ("world-align", "reference"):
        # 合わせた軸の world 方向（world-align は ≈ up / reference は ≈ reference_world・DoD の整列 golden）
        data["aligned_world"] = [round(v, 6) for v in _local_axis_world(obj, data["axis"])]
    if world_bbox(obj) is not None:  # up 方向の最下点（bbox があれば常時・floor 後は ≈0）
        data["min_up"] = round(_min_up_projection(obj, up), 6)
    data["dry_run"] = dry_run

    if (
        snap is not None
    ):  # dry_run のときのみ snapshot を取る → 厳密復元（副作用なし・push_undo もしない）
        _restore_transform(obj, snap)
        bpy.context.view_layer.update()
    elif message:
        push_undo(message)
    return data


# ---- 3Dプリンタ対応（M8 T8.3 / print-setup・シナリオ3）----
#
# print-setup はシーンの **表示単位**（unit_settings.system/length_unit）を mm/m に設定する。
# length_unit は表示専用で geometry（dimensions）を再スケールしない＝**非破壊**（研究 §E5）。
# mesh データを触らないため共有 mesh ガード不要。実寸の export スケールは print-export（T8.5）が
# scale_length/単位から一本で算出する方針（global_scale 一本化）。

_UNIT_LENGTH = {"mm": "MILLIMETERS", "m": "METERS"}


def require_scene(name: str | None) -> Any:
    """シーンを解決する（name=完全名 / 省略=active）。無ければ E_TARGET_NOT_FOUND。"""
    if name is None:
        return bpy.context.scene
    scene = bpy.data.scenes.get(name)
    if scene is None:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"シーンが見つかりません: {name}",
            category=ErrorCategory.USER_INPUT,
        )
    return scene


def _unit_settings_dict(us: Any) -> dict[str, Any]:
    """unit_settings の要約（system / scale_length / length_unit）。"""
    return {
        "system": us.system,
        "scale_length": round(us.scale_length, 8),
        "length_unit": us.length_unit,
    }


def set_print_units(
    unit: str, *, scene_name: str | None = None, message: str | None = None
) -> dict[str, Any]:
    """シーンの表示単位を mm/m に設定する（system=METRIC + length_unit・geometry 非破壊）。

    length_unit は表示専用で頂点/寸法を再スケールしない（研究 §E5）。changed は設定前後で
    system/length_unit が変わったか（冪等性の指標・既に mm なら False）。
    """
    scene = require_scene(scene_name)
    us = scene.unit_settings
    before = (us.system, us.length_unit)
    us.system = "METRIC"
    us.length_unit = _UNIT_LENGTH[unit]
    changed = (us.system, us.length_unit) != before
    if message:
        push_undo(message)
    return {
        "scene": scene.name,
        "unit": unit,
        "unit_settings": _unit_settings_dict(us),
        "changed": changed,
    }


def unit_settings_fingerprint(unit_settings: dict[str, Any]) -> str:
    """単位設定の決定的フィンガープリント（print-setup の drift 検証用）。"""
    return _digest16(unit_settings)


# ---- print3d 能力検出（M8 T8.4 / thin/intersect は print3d 依存・研究 §E6）----
#
# print3d Toolbox は両版とも実体なし（§E6）。manifold/normals/degenerate は bmesh 自前で計算する
# （print3d 非依存・bmesh_ops.mesh_check）。thin（薄壁）/ intersect（自己交差）のみ print3d 依存で、
# 不在時は ops 側が CAPABILITY_UNAVAILABLE を返す。将来 Extensions で導入された場合のみ True になる。

_PRINT3D_ENABLE_CANDIDATES = (
    "object_print3d_utils",
    "print3d_toolbox",
    "bl_ext.blender_org.print3d_toolbox",
)
_PRINT3D_CHECK_OP = "mesh.print3d_check_all"


def print3d_available() -> bool:
    """print3d Toolbox の能力を検出する（未導入なら enable 試行→不可なら False）。

    operator が既に実在すれば True。無ければ候補 module を `addon_utils.enable` で試行し、
    実在判定（`get_rna_type`）し直す。§E6 でこの環境（5.0.1/4.4.3）では module 自体が無く常に False。
    """
    from . import capability  # lazy: operator_real（bpy 依存）

    if capability.operator_real(_PRINT3D_CHECK_OP):
        return True
    import addon_utils  # type: ignore  # lazy: bpy 依存

    for mod in _PRINT3D_ENABLE_CANDIDATES:
        try:
            addon_utils.enable(mod, default_set=False, persistent=False)
        except Exception:
            continue
        if capability.operator_real(_PRINT3D_CHECK_OP):
            return True
    return False


# ---- 3Dプリンタ出力（M8 T8.5 / print-export・シナリオ3 / 研究 §E8）----
#
# STL は `wm.stl_export`（M0.5/§E8 確定・両版同一引数）。対象1個だけを選択して
# export_selected_objects=True で対象限定し、world 空間でジオメトリを焼いて出力する。
# スケールは `global_scale` 一本化（use_scene_unit=False 固定で scale_length を出力へ反映させない
# ＝1000倍ずれ防止）。選択/active は save→restore で非破壊（print-export は mutates=False）。
# 3MF は両版とも export operator が実体なし（§E8）→ resolve_export_operator が None を返し、呼び出し側
# （ops）が CAPABILITY_UNAVAILABLE + STL hint へ縮退する（黙って STL に差し替えない）。


def _resolve_op(operator_path: str) -> Any:
    """'ns.name' 文字列を bpy.ops の operator callable へ解決する（export/import 共用・dotロジック単一化）。"""
    ns, _, name = operator_path.partition(".")
    return getattr(getattr(bpy.ops, ns), name)


def resolve_export_operator(fmt: str) -> str | None:
    """`export.<fmt>` の実在 export operator を能力検出で解決する（無ければ None）。

    解決ロジックは `CapabilityRegistry.resolve`（RESOLVERS 候補表＝spec §9 OperatorResolver の単一窓口・
    M0.5 確定）へ委譲する（候補ループを二重実装しない）。stl は `wm.stl_export`、3mf は候補
    `export_mesh.3mf` が両版とも stub のため None（§E8）。
    """
    from . import capability  # lazy: bpy 依存

    return capability.CapabilityRegistry().resolve(f"export.{fmt}")


def resolve_import_operator(fmt: str) -> str | None:
    """`import.<fmt>` の実在 import operator を能力検出で解決する（無ければ None）。

    export と対称に `CapabilityRegistry.resolve`（RESOLVERS 候補表）へ委譲する。FBX import の唯一の
    版差（5.0=`wm.fbx_import` / 4.4=`import_scene.fbx`）は RESOLVERS の候補優先順で吸収する（§E9）。
    3mf は候補 `import_mesh.3mf` が両版とも stub のため None（§E8）。
    """
    from . import capability  # lazy: bpy 依存

    return capability.CapabilityRegistry().resolve(f"import.{fmt}")


def import_generic(fmt: str, operator_path: str, path: str) -> list[dict[str, str]]:
    """多形式 import（前後 diff で取込特定・§E9）。取込オブジェクトの {name, type} 要約を返す。

    import 前後の `bpy.data.objects` 名集合の差分で取り込んだオブジェクトを特定する（名前衝突時に
    Blender が `.001` 等へリネームするため、集合差が唯一信頼できる方式）。生 operator は run_operator
    経由（AST guard 緑）。シーンを変える破壊的操作なので message を渡して undo 境界を作る。
    """
    before = {o.name for o in bpy.data.objects}
    op = _resolve_op(operator_path)
    try:
        run_operator(op, filepath=path, message=f"import-{fmt}")
    except JsonRpcError:
        raise  # run_operator が既に業務エラー（E_OPERATOR/E_PRECONDITION）へ写像済み＝そのまま伝播
    except Exception as e:
        # glTF importer 等は Python 実装で、壊れた入力に RuntimeError 以外（KeyError/struct.error/
        # JSONDecodeError 等）を投げ得る。run_operator の RuntimeError 限定 catch を漏れて INTERNAL
        # 化するのを防ぎ、入力起因のエラーとして E_OPERATOR に写像する（§6e: USER 起因を INTERNAL に
        # しない）。run_operator 由来の JsonRpcError は上で再送出済みなので、ここは operator 内部例外のみ。
        raise _op_error(
            ErrorCode.E_OPERATOR,
            f"import に失敗しました（ファイル内容/形式を確認してください）: {type(e).__name__}: {e}",
        ) from e
    imported = [o for o in bpy.data.objects if o.name not in before]
    return [{"name": o.name, "type": o.type} for o in sorted(imported, key=lambda x: x.name)]


def current_filepath() -> str:
    """現在開いている .blend のパス（未保存は空文字・save の --path 省略時の解決に使う）。"""
    return bpy.data.filepath


def save_blend(path: str, *, backup: bool) -> None:
    """現在のシーンを .blend に保存する（wm.save_as_mainfile・研究 §E10）。

    backup=True なら上書き時に `<name>.blend1` を残す。Blender の native backup は preferences
    `save_version`（既定 1）依存のため、決定的に制御するよう **`save_version` を一時上書き
    （1 if backup else 0）→ try/finally で restore** する（preference 非汚染・backup naming は
    Blender 標準の `<name>.blend1`）。check_existing=False で既存上書き可。message なし＝undo 不要。
    注: save_version はプロセスグローバル設定。この一時上書きはサーバがリクエストを逐次処理する
    （save と他コマンドが同時に走らない＝同時接続は SESSION_BUSY で fail-fast）前提で安全。
    """
    prefs = bpy.context.preferences.filepaths
    saved_version = prefs.save_version
    try:
        prefs.save_version = 1 if backup else 0
        run_operator(bpy.ops.wm.save_as_mainfile, filepath=path, check_existing=False)
    finally:
        prefs.save_version = saved_version


def _select_only(obj: Any) -> tuple[list[Any], Any]:
    """obj だけを選択し active にする（単体専用・`_select_set([obj])` への薄い委譲）。

    `wm.stl_export(export_selected_objects=True)` は **永続化された view layer の選択フラグ**を見るため、
    run_operator の `temp_override(selected_objects=[obj])` だけでは対象を絞れない（§E8）。実選択を
    一時的に書き換え `_restore_selection` で厳密に戻す（mutates=False を保つ）。選択ロジックの真実は
    `_select_set` に一本化し、print-export(単体) と export(多形式) で二重実装が drift しないようにする。
    """
    return _select_set([obj])


def _restore_selection(saved_selected: list[Any], saved_active: Any) -> None:
    """_select_only/_select_set で退避した選択/active を厳密に復元する（非破壊）。"""
    view_layer = bpy.context.view_layer
    for o in view_layer.objects:
        o.select_set(False)
    for o in saved_selected:
        try:
            o.select_set(True)
        except RuntimeError:  # 復元中に view layer から消えた等は無視（best-effort 復元）
            pass
    view_layer.objects.active = saved_active


def export_stl(
    obj: Any,
    path: str,
    *,
    ascii_format: bool = False,
    global_scale: float = 1.0,
    apply_modifiers: bool = True,
) -> dict[str, Any]:
    """対象 obj 1個を STL で書き出す（wm.stl_export・world 焼き・global_scale 一本化）。

    対象だけを選択して export_selected_objects=True で対象限定し、選択は save→restore で非破壊に
    戻す。use_scene_unit=False 固定で scale_length を出力へ反映させず、スケールは global_scale のみで
    支配する（§E8・1000倍ずれ防止）。check_existing=False で既存ファイルを上書き可能にする。
    返すのは export パラメータ + 検証用の scale_length（ファイル統計は呼び出し側 ops が付与）。
    """
    saved_selected, saved_active = _select_only(obj)
    try:
        run_operator(
            bpy.ops.wm.stl_export,
            obj,
            filepath=path,
            export_selected_objects=True,
            ascii_format=ascii_format,
            global_scale=global_scale,
            use_scene_unit=False,
            apply_modifiers=apply_modifiers,
            check_existing=False,
        )
    finally:
        _restore_selection(saved_selected, saved_active)
    return {
        "format": "stl",
        "ascii": ascii_format,
        "global_scale": round(float(global_scale), 8),
        "apply_modifiers": apply_modifiers,
        # scale_length は検証専用（出力には use_scene_unit=False で未反映）。1000倍ずれ設定の検知材料。
        "scale_length": round(bpy.context.scene.unit_settings.scale_length, 8),
    }


# ---- 多形式 export（M9 T9.1・print-export の STL 限定を一般化・研究 §E9）----
#
# 形式 -> selection 制御 param 名（§E9 実機確定・5.0/4.4 同一）。stl/obj は export_selected_objects、
# gltf/fbx は use_selection。これが「print-export(STL 単体)を多形式へ広げる」核（形式別引数マップ）。
_EXPORT_SELECTION_PARAM: dict[str, str] = {
    "stl": "export_selected_objects",
    "obj": "export_selected_objects",
    "gltf": "use_selection",
    "fbx": "use_selection",
}


def require_targets(selector: str, *, regex: bool = False) -> list[Any]:
    """対象を1つ以上に解決する。0件はエラー（複数は許容＝export 等の集合操作向け・require_single の緩和版）。"""
    found = resolve_targets(selector, regex=regex)
    if not found:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"対象が見つかりません: {selector}",
            category=ErrorCategory.USER_INPUT,
        )
    return found


def current_selection() -> list[Any]:
    """アクティブ view layer で現在選択されているオブジェクト群（export --use-selection 用）。"""
    return [o for o in bpy.context.view_layer.objects if o.select_get()]


def _select_set(objs: list[Any]) -> tuple[list[Any], Any]:
    """objs 群だけを選択し先頭を active にする。元の (selected, active) を返す（restore 用）。

    export_selected_objects/use_selection は永続化された view layer の選択フラグを見るため
    temp_override では絞れない（§E8・_select_only と同理由）。これは _select_only の複数対象版。
    対象がアクティブ view layer に無ければ E_PRECONDITION（INTERNAL 回避）。
    """
    view_layer = bpy.context.view_layer
    vl_names = {o.name for o in view_layer.objects}
    missing = [o.name for o in objs if o.name not in vl_names]
    if missing:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"対象がアクティブ view layer にありません（export 不可）: {', '.join(missing[:5])}",
        )
    saved_selected = [o for o in view_layer.objects if o.select_get()]
    saved_active = view_layer.objects.active
    for o in view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    view_layer.objects.active = objs[0]
    return saved_selected, saved_active


def export_generic(
    fmt: str, operator_path: str, path: str, *, select_objs: list[Any] | None
) -> dict[str, Any]:
    """多形式 export（print-export の STL 限定を一般化・§E9）。

    select_objs=None はシーン全体（selection param=False）/ list は対象のみ（対象を選択して param=True・
    選択は save→restore で非破壊に戻す）。生 operator は run_operator 経由（AST guard 緑）。scale は
    素通し（global_scale 等は渡さない＝既定 1.0・print-export が 3D プリント用 scale 窓口・gltf は
    scale param 自体が無い）。選択は context override にも全集合を載せる（stl/obj は永続選択を、
    gltf/fbx が override を読む場合も全対象が渡るよう belt-and-suspenders）。
    glTF は **GLB 単一固定**（`export_format` の有効値は両版とも ('GLB','GLTF_SEPARATE') のみ＝
    GLTF_EMBEDDED は存在しない・実機確認済み。SEPARATE は .bin 分離で sha256/size が崩れるため不採用）。
    .glb 拡張子の要求は ops 側で bpy 到達前に検証する。
    """
    op = _resolve_op(operator_path)
    sel_param = _EXPORT_SELECTION_PARAM[fmt]
    kwargs: dict[str, Any] = {"filepath": path, "check_existing": False}
    if fmt == "gltf":
        kwargs["export_format"] = "GLB"

    if select_objs is None:
        kwargs[sel_param] = False
        run_operator(op, **kwargs)
        exported = None
    else:
        saved_selected, saved_active = _select_set(select_objs)
        kwargs[sel_param] = True
        extra = {
            "active_object": select_objs[0],
            "object": select_objs[0],
            "selected_objects": list(select_objs),
            "selected_editable_objects": list(select_objs),
        }
        try:
            run_operator(op, extra_override=extra, **kwargs)
        finally:
            _restore_selection(saved_selected, saved_active)
        exported = sorted(o.name for o in select_objs)
    return {
        "format": fmt,
        "operator": operator_path,
        "use_selection": select_objs is not None,
        "exported_objects": exported,
    }


# ---- 状態キャプチャ（実地フィードバック #1 / Spike V で両版確認）----
#
# viewport = gpu offscreen draw_view3d（UI なし・解像度指定可）/ screen = screenshot_area で
# ビューポート領域そのまま / render = カメラからレンダ。screen/viewport は GUI 必須（--background
# では window/area が無く E_PRECONDITION）。生 bpy.ops は run_operator 経由（AST guard）。


def _find_view3d() -> tuple[Any, Any, Any, Any]:
    """最初の VIEW_3D エリアの (window, area, region(WINDOW), space) を返す（無ければ全 None）。"""
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                return win, area, region, area.spaces.active
    return None, None, None, None


def capture_viewport(path: str, width: int, height: int) -> dict[str, Any]:
    """ビューポート相当を gpu offscreen で描画し PNG 保存（UI なし・解像度指定可）。"""
    if bpy.app.background:
        raise _op_error(
            ErrorCode.E_PRECONDITION, "viewport キャプチャには GUI が必要です（--background 不可）"
        )
    _win, area, region, space = _find_view3d()
    if area is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            "3Dビューポートが見つかりません（GUI に VIEW_3D を開いてください）",
        )

    import gpu  # type: ignore  # lazy: bpy 依存（GUI GPU コンテキスト）
    import numpy as np  # type: ignore  # lazy: Blender 同梱（§E4）

    rv3d = space.region_3d
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        with offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.05, 0.05, 0.05, 1.0))
            offscreen.draw_view3d(
                bpy.context.scene,
                bpy.context.view_layer,
                space,
                region,
                rv3d.view_matrix,
                rv3d.window_matrix,
                do_color_management=True,
            )
            buffer = fb.read_color(0, 0, width, height, 4, 0, "UBYTE")
    finally:
        offscreen.free()
    buffer.dimensions = width * height * 4
    arr = np.asarray(buffer, dtype=np.float32) / 255.0
    img = bpy.data.images.new("bli_capture_tmp", width, height, alpha=True)
    try:
        img.pixels.foreach_set(arr.ravel())
        img.filepath_raw = path
        img.file_format = "PNG"
        try:
            img.save()
        except RuntimeError as e:  # 保存失敗は INTERNAL でなく業務エラーへ
            raise _op_error(ErrorCode.E_OPERATOR, f"画像の保存に失敗しました: {e}") from e
    finally:
        bpy.data.images.remove(img)  # 一時 datablock を残さない（例外時も）
    return {"width": width, "height": height}


def capture_screen(path: str) -> dict[str, Any]:
    """ビューポート領域そのまま（シェーディング/ギズモ込み）を screenshot_area で PNG 保存。"""
    if bpy.app.background:
        raise _op_error(
            ErrorCode.E_PRECONDITION, "screen キャプチャには GUI が必要です（--background 不可）"
        )
    win, area, region, _space = _find_view3d()
    if area is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            "3Dビューポートが見つかりません（GUI に VIEW_3D を開いてください）",
        )
    run_operator(
        bpy.ops.screen.screenshot_area,
        extra_override={"window": win, "area": area, "region": region},
        filepath=path,
    )
    return {"width": area.width, "height": area.height}


def capture_render(
    path: str, width: int, height: int, camera_name: str | None = None
) -> dict[str, Any]:
    """シーンカメラからレンダして PNG 保存（render 設定は save/restore で非破壊）。"""
    scene = bpy.context.scene
    if camera_name is not None:
        cam = bpy.data.objects.get(camera_name)
        if cam is None or cam.type != "CAMERA":
            raise _op_error(
                ErrorCode.E_TARGET_NOT_FOUND,
                f"カメラが見つかりません: {camera_name}",
                category=ErrorCategory.USER_INPUT,
            )
    else:
        cam = scene.camera
    if cam is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            "render にはカメラが必要です（--camera 指定、またはシーンに active camera を設定）",
        )
    r = scene.render
    saved = (
        r.filepath,
        r.image_settings.file_format,
        r.resolution_x,
        r.resolution_y,
        r.resolution_percentage,
        scene.camera,
    )
    try:
        scene.camera = cam
        r.filepath = path
        r.image_settings.file_format = "PNG"
        r.resolution_x = width
        r.resolution_y = height
        r.resolution_percentage = 100
        run_operator(bpy.ops.render.render, write_still=True)
    finally:
        (
            r.filepath,
            r.image_settings.file_format,
            r.resolution_x,
            r.resolution_y,
            r.resolution_percentage,
            scene.camera,
        ) = saved
    return {"width": width, "height": height, "camera": cam.name}
