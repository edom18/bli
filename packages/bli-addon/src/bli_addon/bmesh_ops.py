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


# ---- 3Dプリント健全性チェック/修復（M8 T8.4 / bmesh 自前・print3d 非依存・研究 §E6）----
#
# print3d Toolbox は両版で実体なし（§E6）。manifold/normals/degenerate は **bmesh 自前**で計算する
# （print3d 非依存で stable）。thin/intersect は print3d 依存のため ops 側で CAPABILITY_UNAVAILABLE。

_DEGENERATE_AREA_EPS = 1e-8  # この面積未満の面を退化面とみなす
_REPAIR_MERGE_DIST = 1e-6  # repair の dissolve_degenerate / remove_doubles の許容距離


def mesh_check(obj: Any) -> dict[str, Any]:
    """bmesh で 3Dプリント健全性をチェックする（manifold/normals/degenerate・読み取り専用）。

    - 非多様体辺: 2面共有でない辺（`not is_manifold`＝boundary/wire/3面以上）。watertight なら 0。
    - 反転法線: 2面共有だが巻き順が逆（`is_manifold and not is_contiguous`）。一貫なら 0。
    - 退化面: 面積が _DEGENERATE_AREA_EPS 未満。
    `is_printable` は致命カテゴリ（非多様体/反転法線/退化面）が全て 0 かの要約（§E6 で両版確認）。
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.normal_update()
        non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
        boundary = sum(1 for e in bm.edges if e.is_boundary)
        wire = sum(1 for e in bm.edges if e.is_wire)
        flipped = sum(1 for e in bm.edges if e.is_manifold and not e.is_contiguous)
        degenerate = sum(1 for f in bm.faces if f.calc_area() < _DEGENERATE_AREA_EPS)
        loose = sum(1 for v in bm.verts if not v.link_edges)
    finally:
        bm.free()
    return {
        "non_manifold_edges": non_manifold,
        "boundary_edges": boundary,
        "wire_edges": wire,
        "flipped_normals": flipped,
        "degenerate_faces": degenerate,
        "loose_verts": loose,
        "is_manifold": non_manifold == 0,
        "normals_consistent": flipped == 0,
        "is_printable": non_manifold == 0 and flipped == 0 and degenerate == 0,
    }


def mesh_repair(
    obj: Any,
    *,
    make_manifold: bool,
    recalc_normals: bool,
    remove_degenerate: bool,
    message: str | None = None,
) -> dict[str, Any]:
    """bmesh で best-effort 修復する（修復前後のチェック差分を返す・**完全修復は非保証**）。

    - remove-degenerate: `dissolve_degenerate`（退化面/辺を除去）。
    - make-manifold: 退化除去 + `remove_doubles`（重複頂点マージ）+ loose 除去 + `holes_fill`
      （boundary の穴埋め）。穴の形状によっては埋めきれない（非保証）。
    - recalc-normals: `recalc_face_normals`（巻き順一貫化）。make-manifold は穴埋め後の整合のため
      末尾で必ず recalc する。各 bmesh.ops は実行時点の bm を都度読む（delete でトポロジが変わるため）。
    """
    before = mesh_check(obj)
    applied: list[str] = []
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        if remove_degenerate or make_manifold:
            bmesh.ops.dissolve_degenerate(bm, dist=_REPAIR_MERGE_DIST, edges=list(bm.edges))
            if remove_degenerate:
                applied.append("remove-degenerate")
        if make_manifold:
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=_REPAIR_MERGE_DIST)
            loose_v = [v for v in bm.verts if not v.link_edges]
            if loose_v:
                bmesh.ops.delete(bm, geom=loose_v, context="VERTS")
            wire_e = [e for e in bm.edges if e.is_wire]
            if wire_e:
                bmesh.ops.delete(bm, geom=wire_e, context="EDGES")
            bmesh.ops.holes_fill(bm, edges=list(bm.edges), sides=0)
            applied.append("make-manifold")
        if recalc_normals or make_manifold:
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
            if recalc_normals:
                applied.append("recalc-normals")
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    if message:
        push_undo(message)
    after = mesh_check(obj)
    return {
        "applied": applied,
        "before": before,
        "after": after,
        # 致命カテゴリの改善数（正＝減った＝改善）。完全修復は保証しない（after で確認）。
        "fixed": {
            "non_manifold_edges": before["non_manifold_edges"] - after["non_manifold_edges"],
            "flipped_normals": before["flipped_normals"] - after["flipped_normals"],
            "degenerate_faces": before["degenerate_faces"] - after["degenerate_faces"],
        },
    }
