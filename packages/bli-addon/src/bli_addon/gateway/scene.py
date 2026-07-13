"""BpyGateway シーングラフ生成・モード切替・改名・親子・コレクション（gateway/ 分割 P2-4）。

元 gateway.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

from typing import Any

import bpy  # type: ignore

from bli_core.errors import ErrorCategory, ErrorCode

from .core import _digest16, _op_error, _resolve_op, push_undo, run_operator
from .objects import object_summary, require_single
from .transforms import _write_rotation

# ---- シーングラフ生成 / モード切替 / 改名 / 親子 / コレクション（P1-2・欠落プリミティブ第1弾）----

# add --type -> 生成 operator（'ns.name'）。mesh 系は primitive_*_add、それ以外は object.*_add。
_ADD_TYPE_OPERATORS: dict[str, str] = {
    "cube": "mesh.primitive_cube_add",
    "uv-sphere": "mesh.primitive_uv_sphere_add",
    "ico-sphere": "mesh.primitive_ico_sphere_add",
    "cylinder": "mesh.primitive_cylinder_add",
    "cone": "mesh.primitive_cone_add",
    "plane": "mesh.primitive_plane_add",
    "torus": "mesh.primitive_torus_add",
    "empty": "object.empty_add",
    "light": "object.light_add",
    "camera": "object.camera_add",
    "text": "object.text_add",
}


def add_object(
    add_type: str,
    *,
    name: str | None = None,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
    light_type: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """primitive/empty/light/camera/text を生成する（op 経由・前後名前差分で新規特定）。

    生成オブジェクトの特定は import_generic と同じ「実行前後の bpy.data.objects 名差分」方式
    （active_object 依存より決定的）。差分が1個でなければ INTERNAL でなく E_OPERATOR で報告する。
    operator の実在は capability.operator_real（get_rna_type() 判定）で確認する（hasattr のみでは
    旧名 stub を誤検出する・mistakes-memo）。無ければ CAPABILITY_UNAVAILABLE。location のみ
    operator 引数で渡し、name/rotation/scale は生成後に直接プロパティへ反映する。
    """
    from .. import capability  # lazy: bpy 依存

    operator_path = _ADD_TYPE_OPERATORS[add_type]
    if not capability.operator_real(operator_path):
        raise _op_error(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            f"{add_type} の生成 operator が利用できません: {operator_path}",
            category=ErrorCategory.ENVIRONMENT,
        )
    op = _resolve_op(operator_path)
    kwargs: dict[str, Any] = {}
    if location is not None:
        kwargs["location"] = tuple(float(c) for c in location)
    if add_type == "light":
        kwargs["type"] = light_type or "POINT"

    before = {o.name for o in bpy.data.objects}
    # message は渡さない（作成 operator 単体では undo 境界を作らない）。name/rotation/scale の
    # 反映まで含めて呼び出し側の message で1つの undo 境界にまとめる（下の push_undo）。
    run_operator(op, **kwargs)
    created = [o for o in bpy.data.objects if o.name not in before]
    if len(created) != 1:
        raise _op_error(
            ErrorCode.E_OPERATOR,
            f"生成後の新規オブジェクトが1個ではありません（{len(created)}個）: "
            f"{[o.name for o in created]}",
        )
    obj = created[0]
    if name is not None:
        obj.name = name
    if rotation is not None:
        _write_rotation(obj, rotation, "set")
    if scale is not None:
        obj.scale = tuple(float(s) for s in scale)
    if message:
        push_undo(message)
    return object_summary(obj)


# mode --to -> bpy の mode_set 引数値。
_MODE_TO_BLENDER: dict[str, str] = {
    "object": "OBJECT",
    "edit": "EDIT",
    "sculpt": "SCULPT",
    "vertex-paint": "VERTEX_PAINT",
    "weight-paint": "WEIGHT_PAINT",
}


def set_object_mode(
    to: str, *, targets: str | None = None, regex: bool = False, message: str | None = None
) -> dict[str, Any]:
    """object.mode_set で編集モードを切り替える（targets 省略時は現在の active を対象）。

    active 不在・切替不能型（EMPTY へ edit 等）は run_operator の poll() が False を返し
    E_PRECONDITION に写像される（INTERNAL 化しない・既存 run_operator の契約どおり）。
    """
    blender_mode = _MODE_TO_BLENDER[to]
    view_layer = bpy.context.view_layer
    obj = None
    if targets is not None:
        obj = require_single(targets, regex=regex)
        view_layer.objects.active = obj
    active = view_layer.objects.active
    if active is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            "active オブジェクトがありません（--targets を指定するか先に select してください）",
        )
    from_mode = bpy.context.mode
    run_operator(bpy.ops.object.mode_set, obj, message=message, mode=blender_mode)
    return {"from_mode": from_mode, "to_mode": blender_mode, "active": active.name}


def rename_object(
    obj: Any, name: str, *, with_data: bool = False, message: str | None = None
) -> dict[str, Any]:
    """obj.name / (任意で) obj.data.name を変更する（op 不要・bpy.data 直接）。

    衝突時は Blender が .001 等へ実名を確定する（要求名と実名が異なり得るため両方を返す）。
    """
    old_name = obj.name
    obj.name = name
    new_name = obj.name
    data_renamed = False
    if with_data and obj.data is not None:
        obj.data.name = name
        data_renamed = True
    if message:
        push_undo(message)
    return {"old_name": old_name, "new_name": new_name, "data_renamed": data_renamed}


def _ancestor_names(obj: Any) -> set[str]:
    """obj の祖先チェーンの名前集合（親子循環検出用。既存循環があっても無限ループしない防御込み）。"""
    names: set[str] = set()
    cur = obj.parent
    while cur is not None and cur.name not in names:
        names.add(cur.name)
        cur = cur.parent
    return names


def require_valid_parent(children: list[Any], parent_obj: Any) -> None:
    """親子関係の構造的妥当性を検証する（自己参照/循環は E_PRECONDITION）。

    require_mesh/require_modifier_support と同じ「型/構造の前提違反」流儀
    （USER_INPUT のタイプミスとは区別する）。
    """
    child_names = {c.name for c in children}
    if parent_obj.name in child_names:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"対象自身を親にはできません: {parent_obj.name}",
        )
    cyc = _ancestor_names(parent_obj) & child_names
    if cyc:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"親子関係が循環します（既に子孫にある対象を親にしようとしています）: {sorted(cyc)}",
        )


def parent_set(
    children: list[Any],
    parent_obj: Any,
    *,
    keep_transform: bool = True,
    message: str | None = None,
) -> list[dict[str, Any]]:
    """children の親を parent_obj に設定する（自己参照/循環は事前拒否・op 不要）。

    keep_transform は **world を退避→復元**で保つ（parent_clear と同型・レビュー R1-2）。
    matrix_parent_inverse を新親の逆行列にするだけの方式は、子が既に別の親を持つ
    （matrix_basis ≠ matrix_world となる）付け替えで world 位置が飛ぶ:
    Blender の関係式は matrix_world = parent.matrix_world @ matrix_parent_inverse @ matrix_basis
    のため。matrix_parent_inverse も新親基準に揃えてから matrix_world を書き戻す
    （実機再現 5.0: 旧親を動かした後の付け替えで delta=10 → 本方式で 0 を確認）。
    """
    require_valid_parent(children, parent_obj)
    results: list[dict[str, Any]] = []
    for child in children:
        world = child.matrix_world.copy()
        child.parent = parent_obj
        if keep_transform:
            child.matrix_parent_inverse = parent_obj.matrix_world.inverted()
            child.matrix_world = world
        results.append({"name": child.name, "parent": parent_obj.name})
    if message:
        push_undo(message)
    return results


def parent_clear(
    children: list[Any], *, keep_transform: bool = True, message: str | None = None
) -> list[dict[str, Any]]:
    """children の親子関係を解除する（keep_transform で見た目の world 位置を保持）。"""
    results: list[dict[str, Any]] = []
    for child in children:
        world = child.matrix_world.copy()
        child.parent = None
        if keep_transform:
            child.matrix_world = world
        results.append({"name": child.name, "parent": None})
    if message:
        push_undo(message)
    return results


def parent_fingerprint(results: list[dict[str, Any]]) -> str:
    """親子関係変更結果の決定的フィンガープリント（順序非依存）。"""
    return _digest16({"results": sorted(results, key=lambda r: r["name"])})


def create_collection(name: str, *, message: str | None = None) -> dict[str, Any]:
    """新規 collection を作成しシーンの master collection 直下へ link する。同名は E_PRECONDITION。"""
    if bpy.data.collections.get(name) is not None:
        raise _op_error(ErrorCode.E_PRECONDITION, f"collection が既に存在します: {name}")
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    if message:
        push_undo(message)
    return {"name": col.name}


def require_collection(name: str) -> Any:
    """名前で collection を解決する。無ければ E_TARGET_NOT_FOUND。"""
    col = bpy.data.collections.get(name)
    if col is None:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"collection が見つかりません: {name}",
            category=ErrorCategory.USER_INPUT,
        )
    return col


def _in_collection(obj: Any, collection: Any) -> bool:
    return collection.name in {c.name for c in obj.users_collection}


def move_to_collection(
    objs: list[Any], collection: Any, *, message: str | None = None
) -> list[dict[str, Any]]:
    """objs を所属する全 collection から外し collection のみへ link する（シーンから消えない）。"""
    results: list[dict[str, Any]] = []
    for obj in objs:
        for coll in list(obj.users_collection):
            coll.objects.unlink(obj)
        collection.objects.link(obj)
        results.append({"name": obj.name, "collection": collection.name})
    if message:
        push_undo(message)
    return results


def link_to_collection(
    objs: list[Any], collection: Any, *, message: str | None = None
) -> list[dict[str, Any]]:
    """objs を collection へ追加 link する（既に link 済みは静かに skip し結果で報告）。"""
    results: list[dict[str, Any]] = []
    for obj in objs:
        already = _in_collection(obj, collection)
        if not already:
            collection.objects.link(obj)
        results.append({"name": obj.name, "linked": not already})
    if message:
        push_undo(message)
    return results


def unlink_from_collection(
    objs: list[Any], collection: Any, *, message: str | None = None
) -> list[dict[str, Any]]:
    """objs を collection から外す（部分失敗で状態を汚さないよう全対象を先に検証してから外す）。

    外すと所属 collection が 0 になる対象が1つでもあれば、何も外さず E_PRECONDITION で拒否する
    （view layer から消える事故防止・move の利用を促す）。
    """
    for obj in objs:
        if _in_collection(obj, collection) and len(obj.users_collection) <= 1:
            raise _op_error(
                ErrorCode.E_PRECONDITION,
                f"{obj.name} をこの collection から外すと所属 collection が 0 になります"
                "（move を使ってください）",
            )
    results: list[dict[str, Any]] = []
    for obj in objs:
        member = _in_collection(obj, collection)
        if member:
            collection.objects.unlink(obj)
        results.append({"name": obj.name, "unlinked": member})
    if message:
        push_undo(message)
    return results


def _collection_summary(col: Any) -> dict[str, Any]:
    return {
        "name": col.name,
        "objects": len(col.objects),
        "children": [c.name for c in col.children],
    }


def list_collections() -> list[dict[str, Any]]:
    """scene の master collection 配下（子 collection）を再帰的に一覧する（軽量サマリ）。"""
    out: list[dict[str, Any]] = []

    def _walk(col: Any) -> None:
        out.append(_collection_summary(col))
        for child in col.children:
            _walk(child)

    for top in bpy.context.scene.collection.children:
        _walk(top)
    return out


def collections_fingerprint() -> str:
    """collection 階層状態の決定的フィンガープリント（drift 検証用・順序非依存）。"""
    return _digest16({"collections": sorted(list_collections(), key=lambda c: c["name"])})
