"""M9 ファイルI/O（export / import 多形式）実機確認（着手前スパイク / NEXT-M9 §2.1, §2.2）。

`blender --background --python fileio_spike.py` で実行（5.0.1 / 4.4.3 両版）。

確認事項:
- 各 export operator（obj/gltf/fbx/stl）の **正確な引数集合**（名前・既定値）。特に
  selection 制御 param 名（stl=export_selected_objects / obj=export_selected_objects? /
  gltf=use_selection / fbx=use_selection）・scale・filepath/check_existing。
  → OperatorResolver に形式別の引数マップが要るか判断する材料。
- 各 import operator（obj/gltf/fbx/stl）の引数集合 + FBX import 版差
  （wm.fbx_import(5.0) → import_scene.fbx(両対応)）。
- import 前後の bpy.data.objects 差分で「取り込んだオブジェクト」を特定する方式の確認。
- 各形式 export→import 往復で頂点数/bbox 一致（往復 golden の土台）。
- 3mf export/import operator は両版とも stub（実体なし）→ CAPABILITY。

print-export スパイクの rna_props / op_real を流用する。
"""

import os
import tempfile

import bpy  # type: ignore


def report(label, fn):
    try:
        r = fn()
        print(f"[OK] {label}: {r}")
        return r
    except Exception as e:
        print(f"[ERR] {label}: {type(e).__name__}: {e}")
        return None


def op_real(path):
    ns, _, name = path.partition(".")
    group = getattr(bpy.ops, ns, None)
    if group is None or not hasattr(group, name):
        return False
    try:
        getattr(group, name).get_rna_type()
        return True
    except Exception:
        return False


def rna_props(path):
    """operator の引数（name / 既定値）を rna から列挙する。"""
    ns, _, name = path.partition(".")
    rna = getattr(getattr(bpy.ops, ns), name).get_rna_type()
    out = {}
    for prop in rna.properties:
        if prop.identifier == "rna_type":
            continue
        try:
            default = prop.default if hasattr(prop, "default") else None
        except Exception:
            default = "?"
        out[prop.identifier] = f"{prop.type}={default}"
    return out


def selection_props(path):
    """selection 制御らしき param 名だけ抜き出す（use_selection / export_selected_objects 等）。"""
    if not op_real(path):
        return None
    props = rna_props(path)
    return {k: v for k, v in props.items() if "select" in k.lower()}


def scale_props(path):
    """scale 関連 param だけ抜き出す（global_scale / use_scene_unit / *scale*）。"""
    if not op_real(path):
        return None
    props = rna_props(path)
    return {
        k: v
        for k, v in props.items()
        if "scale" in k.lower() or "unit" in k.lower() or k in ("filepath", "check_existing")
    }


def ensure_cube():
    cube = bpy.data.objects.get("Cube")
    if cube is None:
        bpy.ops.mesh.primitive_cube_add(size=2.0)  # spike のみ
        cube = bpy.context.active_object
        cube.name = "Cube"
    return cube


def select_only(obj):
    vl = bpy.context.view_layer
    for o in vl.objects:
        o.select_set(False)
    obj.select_set(True)
    vl.objects.active = obj


def bbox_of(obj):
    """obj の world AABB（min/size）を返す（往復一致の golden 指標）。"""
    import mathutils  # type: ignore

    corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    return {
        "min": [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)],
        "size": [
            round(max(xs) - min(xs), 4),
            round(max(ys) - min(ys), 4),
            round(max(zs) - min(zs), 4),
        ],
    }


def vert_count(obj):
    return (
        len(obj.data.vertices)
        if getattr(obj, "data", None) and hasattr(obj.data, "vertices")
        else None
    )


def diff_import(do_import):
    """import 前後の bpy.data.objects 差分で取込オブジェクトを特定する（M9 import の核）。"""
    before = {o.name for o in bpy.data.objects}
    do_import()
    after = [o for o in bpy.data.objects if o.name not in before]
    return after


def main():
    print("=== BLI_FILEIO_SPIKE_BEGIN ===")
    print("version", bpy.app.version_string)
    print("background", bpy.app.background)

    # --- operator 実在（export/import 各形式 + FBX import 版差 + 3mf stub）---
    for key, path in [
        ("export.stl", "wm.stl_export"),
        ("import.stl", "wm.stl_import"),
        ("export.obj", "wm.obj_export"),
        ("import.obj", "wm.obj_import"),
        ("export.gltf", "export_scene.gltf"),
        ("import.gltf", "import_scene.gltf"),
        ("export.fbx", "export_scene.fbx"),
        ("import.fbx(5.0)", "wm.fbx_import"),
        ("import.fbx(両対応)", "import_scene.fbx"),
        ("export.3mf", "export_mesh.3mf"),
        ("import.3mf", "import_mesh.3mf"),
    ]:
        report(f"op_real {key} = {path}", lambda p=path: op_real(p))

    # --- selection 制御 param 名（形式別引数マップの根拠）---
    print("--- selection param 名 ---")
    report("sel export.stl", lambda: selection_props("wm.stl_export"))
    report("sel export.obj", lambda: selection_props("wm.obj_export"))
    report("sel export.gltf", lambda: selection_props("export_scene.gltf"))
    report("sel export.fbx", lambda: selection_props("export_scene.fbx"))

    # --- scale / filepath / check_existing param（形式別引数マップの根拠）---
    print("--- scale/filepath param ---")
    report("scale export.stl", lambda: scale_props("wm.stl_export"))
    report("scale export.obj", lambda: scale_props("wm.obj_export"))
    report("scale export.gltf", lambda: scale_props("export_scene.gltf"))
    report("scale export.fbx", lambda: scale_props("export_scene.fbx"))

    # --- import 引数集合（filepath/ディレクトリ等）---
    print("--- import filepath param ---")
    report("rna import.stl filepath系", lambda: scale_props("wm.stl_import"))
    report("rna import.obj filepath系", lambda: scale_props("wm.obj_import"))
    report(
        "rna import.fbx(版依存) filepath系",
        lambda: scale_props("wm.fbx_import" if op_real("wm.fbx_import") else "import_scene.fbx"),
    )

    # --- export→import 往復（各形式・頂点数/bbox 一致）---
    cube = ensure_cube()
    select_only(cube)
    base_bbox = bbox_of(cube)
    base_verts = vert_count(cube)
    print("base cube bbox", base_bbox, "verts", base_verts)
    tmpdir = tempfile.mkdtemp(prefix="bli_fileio_spike_")

    def roundtrip_stl():
        select_only(cube)  # import→remove で選択が外れるため往復ごとに再選択
        path = os.path.join(tmpdir, "rt.stl")
        bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True)
        imp = diff_import(lambda: bpy.ops.wm.stl_import(filepath=path))
        out = {"imported": [o.name for o in imp]}
        if imp:
            out["bbox"] = bbox_of(imp[0])
            out["verts"] = vert_count(imp[0])
            for o in imp:
                bpy.data.objects.remove(o, do_unlink=True)
        return out

    report("roundtrip STL", roundtrip_stl)

    def roundtrip_obj():
        select_only(cube)
        path = os.path.join(tmpdir, "rt.obj")
        bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True)
        imp = diff_import(lambda: bpy.ops.wm.obj_import(filepath=path))
        out = {"imported": [o.name for o in imp]}
        if imp:
            out["bbox"] = bbox_of(imp[0])
            out["verts"] = vert_count(imp[0])
            for o in imp:
                bpy.data.objects.remove(o, do_unlink=True)
        return out

    report("roundtrip OBJ", roundtrip_obj)

    def roundtrip_gltf():
        select_only(cube)
        path = os.path.join(tmpdir, "rt.glb")
        bpy.ops.export_scene.gltf(filepath=path, use_selection=True, export_format="GLB")
        imp = diff_import(lambda: bpy.ops.import_scene.gltf(filepath=path))
        out = {"imported": [o.name for o in imp], "types": [o.type for o in imp]}
        meshes = [o for o in imp if o.type == "MESH"]
        if meshes:
            out["bbox"] = bbox_of(meshes[0])
            out["verts"] = vert_count(meshes[0])
        for o in imp:
            bpy.data.objects.remove(o, do_unlink=True)
        return out

    report("roundtrip glTF(GLB)", roundtrip_gltf)

    def roundtrip_fbx():
        select_only(cube)
        path = os.path.join(tmpdir, "rt.fbx")
        bpy.ops.export_scene.fbx(filepath=path, use_selection=True)
        import_op = bpy.ops.wm.fbx_import if op_real("wm.fbx_import") else bpy.ops.import_scene.fbx
        imp = diff_import(lambda: import_op(filepath=path))
        out = {"imported": [o.name for o in imp], "types": [o.type for o in imp]}
        meshes = [o for o in imp if o.type == "MESH"]
        if meshes:
            out["bbox"] = bbox_of(meshes[0])
            out["verts"] = vert_count(meshes[0])
        for o in imp:
            bpy.data.objects.remove(o, do_unlink=True)
        return out

    report("roundtrip FBX", roundtrip_fbx)

    # --- use_selection=False（全シーン export）が書けるか ---
    def export_whole_scene_obj():
        path = os.path.join(tmpdir, "scene.obj")
        bpy.ops.wm.obj_export(filepath=path, export_selected_objects=False)
        return {"exists": os.path.exists(path), "size": os.path.getsize(path)}

    report("OBJ export 全シーン(export_selected_objects=False)", export_whole_scene_obj)

    # --- 3mf enable 経路（両版 stub の確認）---
    def try_enable_3mf():
        import addon_utils  # type: ignore

        results = {}
        for mod in ("io_mesh_3mf", "io_scene_3mf", "bl_ext.blender_org.io_mesh_3mf"):
            try:
                addon_utils.enable(mod, default_set=False, persistent=False)
                results[mod] = op_real("export_mesh.3mf") or op_real("import_mesh.3mf")
            except Exception as e:
                results[mod] = f"{type(e).__name__}"
        return results

    report("addon_utils.enable 3mf candidates", try_enable_3mf)

    print("=== BLI_FILEIO_SPIKE_END ===")


if __name__ == "__main__":
    main()
