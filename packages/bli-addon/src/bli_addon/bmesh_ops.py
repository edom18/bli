"""bmesh ベースのメッシュ編集（M7 T7.1 / bmesh 一次・object モードのまま編集）。

gateway.py と同じ bpy 接点レイヤ。ここは `bpy.ops` を使わず `bmesh.ops` のみを使うため
context 非依存で、edit mode トグルが不要（CLI/headless に最適）。共通フロー:

    bmesh.new() → bm.from_mesh(obj.data) → bmesh.ops.<op>(bm, ...)
    → bm.to_mesh(obj.data) → bm.free() → obj.data.update()

NEXT-M7 §2 のスパイクで 5.0.1 / 4.4.3 とも確認済み（OBJECT モードのまま mesh データを編集可・
remove_doubles は戻り値 None のため merged は頂点数 before/after で算出する）。

破壊的（mesh データを直接書き換える）。共有 mesh の単一ユーザ化ガードは呼び出し側（ops）が行う。
"""

from __future__ import annotations

from typing import Any

import bmesh  # type: ignore

from .gateway import mesh_stats, push_undo


def _face_normals(mesh: Any) -> list[tuple[float, float, float]]:
    """面法線を index 順に丸めて取得する（before/after 比較で flipped を数えるため）。"""
    return [
        (round(p.normal.x, 4), round(p.normal.y, 4), round(p.normal.z, 4)) for p in mesh.polygons
    ]


def _flipped_count(
    before: list[tuple[float, float, float]], after: list[tuple[float, float, float]]
) -> int:
    """前後で法線の向きが反転した面数（dot < 0）。recalc は面を増減しないので index 対応。"""
    n = 0
    for a, b in zip(before, after, strict=True):
        if a[0] * b[0] + a[1] * b[1] + a[2] * b[2] < 0:
            n += 1
    return n


def recalc_normals(obj: Any, *, inside: bool = False, message: str | None = None) -> dict[str, Any]:
    """面法線を一貫化（巻き順を修正）する。inside=True は内向きへ反転する。

    結果は `flipped`（この操作で向きが変わった面数）= 不整合だった面数の指標。clean な
    cube を outward で recalc すると flipped=0、1 面だけ不整合なら flipped=1、clean を
    inside にすると全面反転で flipped=面数。
    """
    before = _face_normals(obj.data)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        if inside:
            bmesh.ops.reverse_faces(bm, faces=bm.faces)
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    after = _face_normals(obj.data)
    if message:
        push_undo(message)
    return {
        "faces": len(after),
        "flipped": _flipped_count(before, after),
        "inside": inside,
        "stats": mesh_stats(obj),
    }


def merge_by_distance(obj: Any, *, distance: float, message: str | None = None) -> dict[str, Any]:
    """distance 以下の頂点をマージする（remove_doubles）。

    remove_doubles の戻り値は None のため、マージ数は頂点数の before/after 差で求める。
    """
    before = len(obj.data.vertices)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=distance)
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    after = len(obj.data.vertices)
    if message:
        push_undo(message)
    return {
        "merged": before - after,
        "before": before,
        "after": after,
        "distance": distance,
        "stats": mesh_stats(obj),
    }
