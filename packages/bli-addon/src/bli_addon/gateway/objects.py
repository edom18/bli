"""BpyGateway オブジェクト解決/情報・モード/単一ユーザ化・複製/削除（gateway/ 分割 P2-4）。

元 gateway.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

import re
from typing import Any

import bpy  # type: ignore

from bli_core.errors import ErrorCategory, ErrorCode

from .core import _digest16, _op_error, _unit_settings_dict, push_undo
from .materials import list_object_materials

# ---- オブジェクト解決・情報 ----


def resolve_targets(selector: str, *, regex: bool = False) -> list[Any]:
    """selector からオブジェクト群を解決する（既定は完全名一致のみ・regex=True で正規表現照合）。

    かつては完全名の不一致時に暗黙で regex 照合へフォールバックしていたが、Blender の既定命名
    `Cube.001` は `.`（regex の任意一文字）を含むため、typo した targets が別オブジェクトへ静かに
    誤マッチし得る（delete/apply 等の破壊系で実害）。明示 opt-in（--regex）へ分離した
    （設計レビュー 2026-07-11 B2）。不正な正規表現は INTERNAL ではなく USER_INPUT エラーにする
    （共有リゾルバなので targets を取る全コマンドに効く。Codex P2）。
    """
    objs = bpy.data.objects
    if not regex:
        obj = objs.get(selector)
        return [obj] if obj is not None else []
    try:
        pattern = re.compile(selector)
    except re.error as e:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"正規表現が不正です: {selector!r}: {e}",
            category=ErrorCategory.USER_INPUT,
        ) from e
    return [o for o in objs if pattern.search(o.name)]


def _regex_match_hint(selector: str, *, regex: bool) -> str:
    """完全名一致 0 件時の移行ヒント。regex として解釈すると N 件当たるなら --regex を案内する。

    暗黙フォールバック廃止（B2）でこれまで regex 頼みだった呼び出しが 0 件になるため、
    E_TARGET_NOT_FOUND の症状文に脱出手段を添える（regex 指定済み・不正 regex・0 件なら空文字）。
    """
    if regex:
        return ""
    try:
        pattern = re.compile(selector)
    except re.error:
        return ""
    n = sum(1 for o in bpy.data.objects if pattern.search(o.name))
    return (
        f"（正規表現として解釈すると {n} 件に一致します。--regex を指定してください）" if n else ""
    )


def require_single(selector: str, *, regex: bool = False) -> Any:
    """対象を1つに解決する。0件/複数はエラー。"""
    found = resolve_targets(selector, regex=regex)
    if not found:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"対象が見つかりません: {selector}{_regex_match_hint(selector, regex=regex)}",
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
    # material --action list と同じ窓口（list_object_materials＝material_slots・slot.link 尊重）で
    # 読む。data.materials 直読みは OBJECT リンク slot で実効マテリアルと乖離し、object-info と
    # material 一覧が食い違う（設計レビュー 2026-07-11 B3）。空スロットは None で報告する。
    data["materials"] = [m["name"] for m in list_object_materials(obj)]
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


def state_fingerprint(payload: dict[str, Any]) -> str:
    """呼び出し側が既に構築済みの状態 dict をそのままハッシュする（drift 検証用の軽量指標）。

    add/mode のように「変更の本質が単一オブジェクトの object_summary に収まらない」結果
    （add は新規オブジェクトが増える／mode はオブジェクトを変えずモードだけ変わる）を、
    obj 参照を持ち回さず result dict から直接指標化するための汎用ヘルパ。
    """
    return _digest16(payload)
