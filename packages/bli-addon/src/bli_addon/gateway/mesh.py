"""BpyGateway メッシュ編集の前提/統計/fingerprint + heavy（boolean/decimate・gateway/ 分割 P2-4）。

元 gateway.py の該当セクションをそのまま移設（挙動変更なし）。bmesh 自体の操作は bmesh_ops.py。
"""

from __future__ import annotations

import hashlib
from typing import Any

from bli_core.errors import ErrorCode

from .core import _digest16, _op_error
from .modifiers import add_modifier, apply_modifier

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
