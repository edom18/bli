"""boolean / decimate 実機確認（M7 T7.3 着手前スパイク / NEXT-M7 §2）。

`blender --background --python bmesh_spike_t73.py` で実行（5.0.1 / 4.4.3 両版）。

bmesh には boolean / decimate 相当の ops が無いため、いずれも **modifier 経由**
（add + apply）にフォールバックする方針を確認する:
- decimate: DECIMATE モディファイア（COLLAPSE・ratio）を追加 → modifier_apply で焼き込み。
  どの fixture が版間で決定的なポリ削減になるか（exact count か不等式か）を見る。
- boolean: BOOLEAN モディファイア（operation + 相手 object）を追加 → modifier_apply。
  重なる2 cube で union/difference/intersect の **world bbox** が幾何的に決まる
  （solver 差に依らない頑健な golden）ことを確認する。
- modifier_apply が OBJECT モード・temp_override で `{'FINISHED'}` になるか。
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


def stats(me):
    return {"v": len(me.vertices), "e": len(me.edges), "f": len(me.polygons)}


def world_bbox(obj):
    from mathutils import Vector  # type: ignore

    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    return {
        "min": [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)],
        "max": [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)],
    }


def fresh_cube(name, *, size=2.0, location=(0.0, 0.0, 0.0)):
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)  # spike のみ
    obj = bpy.context.active_object
    obj.name = name
    return obj


def apply_modifier(obj, mod_name):
    """温存的に modifier_apply を temp_override 下で実行（gateway.run_operator 流儀）。"""
    ov = {
        "active_object": obj,
        "object": obj,
        "selected_objects": [obj],
        "selected_editable_objects": [obj],
    }
    with bpy.context.temp_override(**ov):
        ok = bpy.ops.object.modifier_apply.poll()
        result = bpy.ops.object.modifier_apply(modifier=mod_name)
    return {"poll": ok, "result": sorted(result)}


def main():
    print("=== BLI_BMESH_T73_SPIKE_BEGIN ===")
    print("version", bpy.app.version_string)
    print("mode", bpy.context.mode)

    # bmesh に boolean/decimate 相当が無いことを明示確認（modifier 経由が必要）。
    report(
        "bmesh.ops has boolean/decimate?",
        lambda: {
            "has_boolean": hasattr(bmesh.ops, "boolean"),
            "has_decimate": hasattr(bmesh.ops, "decimate"),
        },
    )

    # --- decimate: 各 fixture で COLLAPSE ratio の削減を見る ---
    def do_decimate(builder, ratio, name):
        obj = builder(name)
        before = stats(obj.data)
        mod = obj.modifiers.new("BLI_Decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        ap = apply_modifier(obj, mod.name)
        obj.data.update()
        return {"before": before, "after": stats(obj.data), "ratio": ratio, **ap}

    # plain cube は三角化後 12 tri。ratio=0.5 の挙動を見る。
    report("decimate cube ratio=0.5", lambda: do_decimate(lambda n: fresh_cube(n), 0.5, "DecCube"))
    report("decimate cube ratio=1.0", lambda: do_decimate(lambda n: fresh_cube(n), 1.0, "DecCube1"))

    # subdivided cube（より多くの面）での決定的削減を見る。
    def subdiv_cube(name, cuts=3):
        obj = fresh_cube(name)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=cuts, use_grid_fill=True)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        return obj

    report(
        "decimate subdiv-cube(cuts=3) ratio=0.5",
        lambda: do_decimate(lambda n: subdiv_cube(n, 3), 0.5, "DecSub"),
    )
    report(
        "decimate subdiv-cube(cuts=3) ratio=0.25",
        lambda: do_decimate(lambda n: subdiv_cube(n, 3), 0.25, "DecSub2"),
    )

    # ico_sphere（決定的トポロジ）での削減（版間一致しやすい候補）。
    def ico_sphere(name, subdiv=2):
        old = bpy.data.objects.get(name)
        if old is not None:
            bpy.data.objects.remove(old, do_unlink=True)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdiv)
        obj = bpy.context.active_object
        obj.name = name
        return obj

    report(
        "decimate ico(subdiv=2) ratio=0.5",
        lambda: do_decimate(lambda n: ico_sphere(n, 2), 0.5, "DecIco"),
    )

    # --- boolean: 重なる2 cube の world bbox（幾何的に決定的）---
    # A: 原点 size2 → [-1,1]^3 / B: x+1 平行移動 size2 → x[0,2] y[-1,1] z[-1,1]。重なり x[0,1]。
    def do_boolean(operation):
        a = fresh_cube("BoolA", location=(0.0, 0.0, 0.0))
        b = fresh_cube("BoolB", location=(1.0, 0.0, 0.0))
        before = stats(a.data)
        mod = a.modifiers.new("BLI_Boolean", "BOOLEAN")
        mod.operation = operation
        mod.object = b
        solver = getattr(mod, "solver", None)
        ap = apply_modifier(a, mod.name)
        a.data.update()
        return {
            "operation": operation,
            "solver": solver,
            "before": before,
            "after": stats(a.data),
            "world_bbox": world_bbox(a),
            **ap,
        }

    report("boolean UNION (A∪B)", lambda: do_boolean("UNION"))
    report("boolean DIFFERENCE (A-B)", lambda: do_boolean("DIFFERENCE"))
    report("boolean INTERSECT (A∩B)", lambda: do_boolean("INTERSECT"))

    # 非重なり（離れた相手）での difference は no-op 的（A 不変）か確認。
    def do_boolean_disjoint():
        a = fresh_cube("BoolDA", location=(0.0, 0.0, 0.0))
        b = fresh_cube("BoolDB", location=(5.0, 0.0, 0.0))
        before = stats(a.data)
        mod = a.modifiers.new("BLI_Boolean", "BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = b
        ap = apply_modifier(a, mod.name)
        a.data.update()
        return {"before": before, "after": stats(a.data), "world_bbox": world_bbox(a), **ap}

    report("boolean DIFFERENCE disjoint (no overlap)", do_boolean_disjoint)

    print("=== BLI_BMESH_T73_SPIKE_END ===")


if __name__ == "__main__":
    main()
