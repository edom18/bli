"""print3d 能力検出 + bmesh 自前チェックの実機確認（M8 T8.4 着手前スパイク・最重要 / NEXT-M8 §2.1）。

`blender --background --python print3d_spike.py` で実行（5.0.1 / 4.4.3 両版）。

M0.5 で `mesh.print3d_*` は両版 stub・`enable("print3d_toolbox"/"object_print3d_utils")` は両版 False
だった（research §A）。Extensions 化された 5.0 での正しい module id / enable 経路を特定する。
不可なら print-check/repair は **CAPABILITY_UNAVAILABLE 縮退**で設計し、manifold/normals/degenerate を
**bmesh 自前計算**できる範囲を切り分ける（print3d 非依存で stable に出せる部分）。

確認ポイント:
1. print3d 系 addon module の実在 id（addon_utils.modules 列挙）と enable 可否・operator 実在。
2. bmesh 自前チェックの信号: non-manifold edge（not is_manifold）/ 法線不整合（manifold かつ not
   is_contiguous）/ 退化面（calc_area ≈ 0）。clean cube / 面欠け cube / 面反転 cube / 退化面 mesh で検証。
3. bmesh 自前 repair: recalc_face_normals / dissolve_degenerate / holes_fill / remove_doubles の挙動。
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


def operator_real(path):
    ns, _, name = path.partition(".")
    group = getattr(bpy.ops, ns, None)
    if group is None or not hasattr(group, name):
        return False
    try:
        getattr(group, name).get_rna_type()
        return True
    except Exception:
        return False


# ---- bmesh 自前チェック ----
_DEGEN_EPS = 1e-8


def check_bmesh(obj):
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.normal_update()
        non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
        boundary = sum(1 for e in bm.edges if e.is_boundary)
        wire = sum(1 for e in bm.edges if e.is_wire)
        # 法線不整合: 2面共有（manifold）なのに巻き順が逆 = not is_contiguous。
        non_contiguous = sum(1 for e in bm.edges if e.is_manifold and not e.is_contiguous)
        degenerate_faces = sum(1 for f in bm.faces if f.calc_area() < _DEGEN_EPS)
        loose_verts = sum(1 for v in bm.verts if not v.link_edges)
        return {
            "verts": len(bm.verts),
            "faces": len(bm.faces),
            "non_manifold_edges": non_manifold,
            "boundary_edges": boundary,
            "wire_edges": wire,
            "non_contiguous_edges": non_contiguous,
            "degenerate_faces": degenerate_faces,
            "loose_verts": loose_verts,
        }
    finally:
        bm.free()


def fresh_cube(name):
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=2.0)  # spike のみ
    obj = bpy.context.active_object
    obj.name = name
    return obj


def main():
    print("=== BLI_PRINT3D_SPIKE_BEGIN ===")
    print("version", bpy.app.version_string)

    # --- Part 1: print3d 能力検出 ---
    import addon_utils  # type: ignore

    report(
        "addon modules matching 'print'",
        lambda: sorted(m.__name__ for m in addon_utils.modules() if "print" in m.__name__.lower()),
    )

    print3d_ops = [
        "mesh.print3d_check_all",
        "mesh.print3d_check_solid",
        "mesh.print3d_check_intersections",
        "mesh.print3d_check_degenerate",
        "mesh.print3d_check_distorted",
        "mesh.print3d_clean_non_manifold",
    ]
    report(
        "print3d operators real (before enable)",
        lambda: {op: operator_real(op) for op in print3d_ops},
    )

    # Extensions の命名規約 bl_ext.<repo>.<id> を含む候補で enable を試す。
    candidates = [
        "object_print3d_utils",
        "print3d_toolbox",
        "bl_ext.blender_org.print3d_toolbox",
        "bl_ext.user_default.print3d_toolbox",
        "bl_ext.system.print3d_toolbox",
    ]

    def try_enable(mod):
        try:
            addon_utils.enable(mod, default_set=False, persistent=False)
            return mod in [
                m.__name__ for m in addon_utils.modules() if addon_utils.check(m.__name__)[1]
            ]
        except Exception as e:
            return f"{type(e).__name__}: {e}"

    report("enable attempts", lambda: {mod: try_enable(mod) for mod in candidates})
    report(
        "print3d operators real (after enable)",
        lambda: {op: operator_real(op) for op in print3d_ops},
    )
    report("scene has print_3d prop?", lambda: hasattr(bpy.context.scene, "print_3d"))

    # --- Part 2: bmesh 自前チェック（print3d 非依存・stable に出せる範囲） ---
    # clean cube: 全て 0。
    report("bmesh check: clean cube", lambda: check_bmesh(fresh_cube("P3Clean")))

    # 面欠け cube（1面削除）→ boundary/non-manifold edge = 4。
    def cube_missing_face(name):
        obj = fresh_cube(name)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY")
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return obj

    report("bmesh check: cube missing 1 face", lambda: check_bmesh(cube_missing_face("P3Open")))

    # 面反転 cube（1面の巻き順反転）→ non_contiguous edge = 4。
    def cube_flipped(name):
        obj = fresh_cube(name)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.reverse_faces(bm, faces=[bm.faces[0]])
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return obj

    report("bmesh check: cube 1 flipped face", lambda: check_bmesh(cube_flipped("P3Flip")))

    # 退化面 mesh（2頂点が一致する三角形＝面積0）→ degenerate_faces >= 1。
    def degenerate_mesh(name):
        old = bpy.data.objects.get(name)
        if old is not None:
            bpy.data.objects.remove(old, do_unlink=True)
        me = bpy.data.meshes.new(name + "Mesh")
        me.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 0.0)], [], [(0, 1, 2)])
        me.update()
        obj = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(obj)
        return obj

    report("bmesh check: degenerate triangle", lambda: check_bmesh(degenerate_mesh("P3Degen")))

    # --- Part 2b: bmesh 自前 repair の挙動 ---
    # recalc: 反転 cube を recalc_face_normals で一貫化 → non_contiguous 0。
    def repair_recalc():
        obj = cube_flipped("P3RepairFlip")
        before = check_bmesh(obj)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return {
            "before_nc": before["non_contiguous_edges"],
            "after": check_bmesh(obj)["non_contiguous_edges"],
        }

    report("repair recalc_face_normals", repair_recalc)

    # dissolve_degenerate: 退化面を除去。
    def repair_degenerate():
        obj = degenerate_mesh("P3RepairDegen")
        before = check_bmesh(obj)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return {
            "before_degen": before["degenerate_faces"],
            "after": check_bmesh(obj)["degenerate_faces"],
        }

    report("repair dissolve_degenerate", repair_degenerate)

    # holes_fill: 面欠け cube の穴を埋めて manifold 化 → non_manifold 0。
    def repair_holes():
        obj = cube_missing_face("P3RepairOpen")
        before = check_bmesh(obj)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.holes_fill(bm, edges=bm.edges, sides=0)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return {
            "before_nm": before["non_manifold_edges"],
            "after": check_bmesh(obj)["non_manifold_edges"],
        }

    report("repair holes_fill", repair_holes)

    print("=== BLI_PRINT3D_SPIKE_END ===")


if __name__ == "__main__":
    main()
