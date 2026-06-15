"""bmesh-on-data API 実機確認（M7 T7.1 着手前スパイク / NEXT-M7 §2）。

`blender --background --python bmesh_spike.py` で実行（5.0.1 / 4.4.3 両版）。

確認したいこと:
- bmesh-on-data フロー（object モードのまま mesh データを編集・context 非依存）:
  bmesh.new() → bm.from_mesh(obj.data) → bmesh.ops.<op>(bm, ...) → bm.to_mesh(obj.data) → bm.free()
- recalc_face_normals(bm, faces=bm.faces) で法線を一貫化（巻き順を修正）。
- reverse_faces(bm, faces=bm.faces) で inside（内向き）化。
- 「flipped 数」= 操作前後で normal の向きが反転した面数（決定的な統計に使えるか）。
- remove_doubles(bm, verts=bm.verts, dist=...) のマージ数（before-after で算出可能か）。
- bm.to_mesh 後の obj.data.update() 要否。
"""

import bmesh  # type: ignore
import bpy  # type: ignore


def report(label, fn):
    try:
        r = fn()
        print(f"[OK] {label}: {r}")
        return r
    except Exception as e:
        print(f"[ERR] {label}: {type(e).__name__}: {e}")
        return None


def fresh_cube(name="SpikeCube"):
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=2.0)  # spike のみ（AST guard 対象外）
    obj = bpy.context.active_object
    obj.name = name
    return obj


def face_normals(obj):
    """obj.data の面法線を index 順に取得（[(x,y,z), ...]）。"""
    return [tuple(round(c, 5) for c in p.normal) for p in obj.data.polygons]


def flipped_count(before, after):
    """前後で normal の向きが反転した面数（dot < 0）。"""
    n = 0
    for a, b in zip(before, after, strict=True):
        dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
        if dot < 0:
            n += 1
    return n


def recalc(obj, inside):
    before = face_normals(obj)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if inside:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    after = face_normals(obj)
    return {"faces": len(after), "flipped": flipped_count(before, after), "inside": inside}


def main():
    print("=== BLI_BMESH_SPIKE_BEGIN ===")
    print("version", bpy.app.version_string)
    print("mode", bpy.context.mode)  # OBJECT のまま編集できるか

    # --- recalc_face_normals ---
    obj = fresh_cube()

    # クリーンな cube（既に outward・一貫）→ recalc しても flipped=0 のはず
    report("recalc clean outward (inside=False)", lambda: recalc(obj, inside=False))

    # 1 面だけ巻き順を反転させて不整合を作る → recalc で 1 面が修正される（flipped=1）
    def flip_one_face():
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.reverse_faces(bm, faces=[bm.faces[0]])
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return "flipped face[0]"

    report("setup: flip one face", flip_one_face)
    report("recalc one-inconsistent (inside=False)", lambda: recalc(obj, inside=False))

    # inside=True: 全面が内向きへ（clean outward 比 6 面反転）
    obj2 = fresh_cube("SpikeCube2")
    report("recalc inside=True (clean)", lambda: recalc(obj2, inside=True))
    # もう一度 inside=True → 既に内向きなので flipped=0
    report("recalc inside=True again", lambda: recalc(obj2, inside=True))
    # inside=False → outward へ戻る（内向き比 6 面反転）
    report("recalc inside=False (was inside)", lambda: recalc(obj2, inside=False))

    # --- remove_doubles ---
    def make_doubled_cube():
        o = fresh_cube("SpikeDouble")
        bm = bmesh.new()
        bm.from_mesh(o.data)
        # 既存頂点に重なる重複頂点を1つ追加（マージ対象を1つ作る）
        bm.verts.ensure_lookup_table()
        v0 = bm.verts[0]
        bm.verts.new(v0.co.copy())
        bm.to_mesh(o.data)
        bm.free()
        o.data.update()
        return o

    od = make_doubled_cube()
    print("doubled verts before", len(od.data.vertices))

    def merge(obj, dist):
        before = len(obj.data.vertices)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        ret = bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        after = len(obj.data.vertices)
        return {
            "before": before,
            "after": after,
            "merged": before - after,
            "ret_keys": sorted(ret.keys()) if isinstance(ret, dict) else type(ret).__name__,
        }

    report("remove_doubles dist=0.001 (1 double)", lambda: merge(od, 0.001))

    # 大きい dist で複数頂点が collapse することも確認
    oc = fresh_cube("SpikeCollapse")
    report("remove_doubles dist=3.0 (collapse cube)", lambda: merge(oc, 3.0))

    # bmesh.ops のシグネチャ確認用（存在チェック）
    report(
        "ops exist",
        lambda: {
            "recalc_face_normals": hasattr(bmesh.ops, "recalc_face_normals"),
            "reverse_faces": hasattr(bmesh.ops, "reverse_faces"),
            "remove_doubles": hasattr(bmesh.ops, "remove_doubles"),
        },
    )

    print("=== BLI_BMESH_SPIKE_END ===")


if __name__ == "__main__":
    main()
