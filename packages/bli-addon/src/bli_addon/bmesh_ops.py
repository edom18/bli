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

from .gateway import mesh_stats, push_undo, stats_delta


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

    結果の `flipped` は **この操作で向きが変わった面数**（操作前後の法線を index 対応で比較。
    methods.md の定義と一致）。inside=False では「不整合だった面数」に一致し、inside=True では
    「元の向きから反転した面数」を表す。例: clean cube を outward recalc → flipped=0 /
    1 面だけ不整合 → flipped=1 / clean を inside → 全面反転で flipped=面数。
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


def extrude(obj: Any, *, offset: list[float], message: str | None = None) -> dict[str, Any]:
    """全 face を region として押し出し、新頂点を offset だけ平行移動する。

    offset は **world 空間** のベクトル（move/duplicate の --offset と一貫）。bmesh は mesh
    ローカル空間で動くため、matrix_world の 3x3 逆行列で world→local 変換してから translate する
    （回転/スケールがあっても world 変位が要求どおりになる。set_origin_world と同じ流儀）。
    extrude_face_region は新ジオメトリを生成するが移動はしないので戻り 'geom' から新頂点を取る。
    """
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    local_offset = obj.matrix_world.to_3x3().inverted() @ Vector(offset)
    before = mesh_stats(obj)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        ret = bmesh.ops.extrude_face_region(bm, geom=list(bm.faces))
        new_verts = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, verts=new_verts, vec=local_offset)
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    after = mesh_stats(obj)
    if message:
        push_undo(message)
    return {"offset": list(offset), "delta": stats_delta(before, after), "stats": after}


def bevel(obj: Any, *, width: float, segments: int, message: str | None = None) -> dict[str, Any]:
    """全 edge をベベルする（affect='EDGES'）。width=オフセット幅（mesh ローカル単位）・segments=分割数。

    width はスカラ量で、非一様スケール下の "world 幅" は定義できないため mesh ローカル単位とする。
    """
    before = mesh_stats(obj)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bmesh.ops.bevel(bm, geom=list(bm.edges), offset=width, segments=segments, affect="EDGES")
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    after = mesh_stats(obj)
    if message:
        push_undo(message)
    return {
        "width": width,
        "segments": segments,
        "delta": stats_delta(before, after),
        "stats": after,
    }


def inset(obj: Any, *, thickness: float, message: str | None = None) -> dict[str, Any]:
    """全 face を個別にインセットする（inset_individual）。

    inset_region は閉じた mesh の全 face 選択だと境界が無く no-op になるため、各面を個別に
    inset する inset_individual を使う（thickness=インセット厚み・mesh ローカル単位のスカラ）。
    """
    before = mesh_stats(obj)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bmesh.ops.inset_individual(bm, faces=list(bm.faces), thickness=thickness)
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    after = mesh_stats(obj)
    if message:
        push_undo(message)
    return {"thickness": thickness, "delta": stats_delta(before, after), "stats": after}
