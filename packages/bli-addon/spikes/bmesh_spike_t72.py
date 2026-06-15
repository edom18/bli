"""bmesh extrude/bevel/inset API 実機確認（M7 T7.2 着手前スパイク / NEXT-M7 §2）。

`blender --background --python bmesh_spike_t72.py` で実行（5.0.1 / 4.4.3 両版）。

確認したいこと（bmesh-on-data・object モードのまま）:
- extrude: `bmesh.ops.extrude_face_region(bm, geom=faces)` → 戻り 'geom' から新頂点を取り、
  `bmesh.ops.translate(bm, verts=new_verts, vec=offset)` で移動。before/after の頂点/面数。
- bevel: `bmesh.ops.bevel(bm, geom=edges, offset=W, segments=S, affect='EDGES')` のシグネチャ
  （引数名/必須/版差）。before/after。
- inset: `bmesh.ops.inset_region(bm, faces=faces, thickness=T)` のシグネチャ。
  inset_individual との差。before/after。
- 空 mesh / 全 face 選択の挙動。各 op 後の頂点/面数の増分。
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


def stats(me):
    return {"v": len(me.vertices), "e": len(me.edges), "f": len(me.polygons)}


def main():
    print("=== BLI_BMESH_T72_SPIKE_BEGIN ===")
    print("version", bpy.app.version_string)
    print("mode", bpy.context.mode)

    # --- extrude（全 face を region 抽出 → offset 平行移動）---
    def do_extrude(offset):
        obj = fresh_cube("SpikeExtrude")
        before = stats(obj.data)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        ret = bmesh.ops.extrude_face_region(bm, geom=list(bm.faces))
        new_verts = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, verts=new_verts, vec=offset)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return {
            "before": before,
            "after": stats(obj.data),
            "ret_keys": sorted(ret.keys()) if isinstance(ret, dict) else type(ret).__name__,
            "new_vert_count": len(new_verts),
        }

    report("extrude_face_region + translate (0,0,1)", lambda: do_extrude((0.0, 0.0, 1.0)))

    # --- bevel（全 edge を affect='EDGES'）---
    def do_bevel(offset, segments):
        obj = fresh_cube("SpikeBevel")
        before = stats(obj.data)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        ret = bmesh.ops.bevel(
            bm,
            geom=list(bm.edges),
            offset=offset,
            segments=segments,
            affect="EDGES",
        )
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return {
            "before": before,
            "after": stats(obj.data),
            "ret_keys": sorted(ret.keys()) if isinstance(ret, dict) else type(ret).__name__,
        }

    report("bevel(edges, offset=0.2, segments=1)", lambda: do_bevel(0.2, 1))
    report("bevel(edges, offset=0.3, segments=3)", lambda: do_bevel(0.3, 3))

    # --- inset（全 face を region inset）---
    def do_inset_region(thickness):
        obj = fresh_cube("SpikeInsetR")
        before = stats(obj.data)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        ret = bmesh.ops.inset_region(bm, faces=list(bm.faces), thickness=thickness)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return {
            "before": before,
            "after": stats(obj.data),
            "ret_keys": sorted(ret.keys()) if isinstance(ret, dict) else type(ret).__name__,
        }

    report("inset_region(faces, thickness=0.2)", lambda: do_inset_region(0.2))

    def do_inset_individual(thickness):
        obj = fresh_cube("SpikeInsetI")
        before = stats(obj.data)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.inset_individual(bm, faces=list(bm.faces), thickness=thickness)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return {"before": before, "after": stats(obj.data)}

    report("inset_individual(faces, thickness=0.2)", lambda: do_inset_individual(0.2))

    # --- 空 mesh（頂点のみ・面なし）での各 op の挙動 ---
    def do_empty_mesh_ops():
        me = bpy.data.meshes.new("SpikeEmpty")
        me.from_pydata([(0.0, 0.0, 0.0)], [], [])
        bm = bmesh.new()
        bm.from_mesh(me)
        # face が無い状態で各 op を呼んでクラッシュしないか
        bmesh.ops.extrude_face_region(bm, geom=list(bm.faces))
        bmesh.ops.bevel(bm, geom=list(bm.edges), offset=0.1, segments=1, affect="EDGES")
        bmesh.ops.inset_region(bm, faces=list(bm.faces), thickness=0.1)
        bm.to_mesh(me)
        bm.free()
        return stats(me)

    report("empty-mesh ops (no faces) no-crash", do_empty_mesh_ops)

    # ops 存在確認
    report(
        "ops exist",
        lambda: {
            "extrude_face_region": hasattr(bmesh.ops, "extrude_face_region"),
            "translate": hasattr(bmesh.ops, "translate"),
            "bevel": hasattr(bmesh.ops, "bevel"),
            "inset_region": hasattr(bmesh.ops, "inset_region"),
            "inset_individual": hasattr(bmesh.ops, "inset_individual"),
        },
    )

    print("=== BLI_BMESH_T72_SPIKE_END ===")


if __name__ == "__main__":
    main()
