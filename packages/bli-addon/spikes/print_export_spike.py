"""print-export（STL/3MF 出力）実機確認（M8 T8.5 着手前スパイク / NEXT-M8 §2.3）。

`blender --background --python print_export_spike.py` で実行（5.0.1 / 4.4.3 両版）。

確認事項（research §[要実機検証] line 175 を消化）:
- `wm.stl_export` / `wm.stl_import` の **正確な引数集合**（名前・既定値）。特に
  ascii / selection / global_scale / use_scene_unit / apply_modifiers / forward_axis / up_axis。
- 3MF operator（`export_mesh.3mf` / `import_mesh.3mf`）の可否と `io_mesh_3mf` の enable 経路。
- `global_scale` と `use_scene_unit`（scale_length 適用）が座標へ与える効果（1000倍ずれ防止の根拠）。
- 既定 cube の STL 往復（binary/ascii）でファイル生成・サイズ・再 import 頂点数。
- selection（export_selected_objects）の挙動。
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


def ensure_cube():
    cube = bpy.data.objects.get("Cube")
    if cube is None:
        bpy.ops.mesh.primitive_cube_add(size=2.0)  # spike のみ
        cube = bpy.context.active_object
        cube.name = "Cube"
    return cube


def select_only(obj):
    """obj だけを選択して active にする（background でも view layer 経由で可）。"""
    vl = bpy.context.view_layer
    for o in vl.objects:
        o.select_set(False)
    obj.select_set(True)
    vl.objects.active = obj


def stl_vertex_count(path):
    """import_scene せず、binary STL の三角形数からおおよその頂点数を読む（健全性確認用）。

    binary STL: 80 byte header + uint32 三角形数。ascii はここでは None。
    """
    with open(path, "rb") as f:
        head = f.read(84)
    if len(head) < 84:
        return None
    # ascii STL は "solid" で始まる（が binary も偶然始まり得るので緩い指標）。
    import struct

    tri = struct.unpack("<I", head[80:84])[0]
    expected_size = 84 + tri * 50
    actual = os.path.getsize(path)
    return {"triangles": tri, "binary_size_match": expected_size == actual, "file_size": actual}


def main():
    print("=== BLI_PRINT_EXPORT_SPIKE_BEGIN ===")
    print("version", bpy.app.version_string)
    print("background", bpy.app.background)

    # --- operator 実在 + 引数集合 ---
    report("op_real wm.stl_export", lambda: op_real("wm.stl_export"))
    report("op_real wm.stl_import", lambda: op_real("wm.stl_import"))
    report("op_real export_mesh.stl (旧)", lambda: op_real("export_mesh.stl"))
    if op_real("wm.stl_export"):
        report("rna wm.stl_export", lambda: rna_props("wm.stl_export"))
    if op_real("wm.stl_import"):
        report("rna wm.stl_import", lambda: rna_props("wm.stl_import"))

    # --- 3MF 可否 + enable 経路 ---
    report("op_real export_mesh.3mf", lambda: op_real("export_mesh.3mf"))
    report("op_real import_mesh.3mf", lambda: op_real("import_mesh.3mf"))

    def try_enable_3mf():
        import addon_utils  # type: ignore

        results = {}
        for mod in (
            "io_mesh_3mf",
            "io_scene_3mf",
            "bl_ext.blender_org.io_mesh_3mf",
            "bl_ext.blender_org.3mf_io",
        ):
            try:
                addon_utils.enable(mod, default_set=False, persistent=False)
                results[mod] = op_real("export_mesh.3mf") or op_real("import_mesh.3mf")
            except Exception as e:
                results[mod] = f"{type(e).__name__}"
        return results

    report("addon_utils.enable 3mf candidates", try_enable_3mf)

    # --- STL 往復（binary / ascii）---
    cube = ensure_cube()
    select_only(cube)
    tmpdir = tempfile.mkdtemp(prefix="bli_export_spike_")

    def export_binary():
        path = os.path.join(tmpdir, "cube_bin.stl")
        bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True, ascii_format=False)
        return {"path": path, "exists": os.path.exists(path), "info": stl_vertex_count(path)}

    report("export binary (selected)", export_binary)

    def export_ascii():
        path = os.path.join(tmpdir, "cube_ascii.stl")
        bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True, ascii_format=True)
        with open(path, "rb") as f:
            head = f.read(5)
        return {
            "path": path,
            "exists": os.path.exists(path),
            "size": os.path.getsize(path),
            "starts_solid": head == b"solid",
        }

    report("export ascii (selected)", export_ascii)

    # --- selection なし（全シーン）でも書けるか ---
    def export_no_selection():
        path = os.path.join(tmpdir, "cube_all.stl")
        bpy.ops.wm.stl_export(filepath=path, export_selected_objects=False)
        return {"exists": os.path.exists(path), "info": stl_vertex_count(path)}

    report("export export_selected_objects=False", export_no_selection)

    # --- global_scale の効果（座標倍率）---
    def export_scaled():
        path1 = os.path.join(tmpdir, "cube_s1.stl")
        path2 = os.path.join(tmpdir, "cube_s2.stl")
        bpy.ops.wm.stl_export(filepath=path1, export_selected_objects=True, global_scale=1.0)
        bpy.ops.wm.stl_export(filepath=path2, export_selected_objects=True, global_scale=2.0)
        # 再 import して dimensions を比較（global_scale=2 で 2倍になるはず）。
        before = {o.name for o in bpy.data.objects}
        bpy.ops.wm.stl_import(filepath=path2)
        imported = [o for o in bpy.data.objects if o.name not in before]
        dims = [round(d, 4) for d in imported[0].dimensions] if imported else None
        for o in imported:
            bpy.data.objects.remove(o, do_unlink=True)
        return {"global_scale2_imported_dims": dims, "expected": "[4,4,4] (元 cube=2)"}

    report("global_scale=2 効果", export_scaled)

    # --- use_scene_unit（scale_length 適用）の効果 ---
    def export_scene_unit():
        us = bpy.context.scene.unit_settings
        saved = us.scale_length
        us.scale_length = 0.001  # mm 相当（1 Blender unit = 0.001 m）
        path = os.path.join(tmpdir, "cube_su.stl")
        try:
            bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True, use_scene_unit=True)
            before = {o.name for o in bpy.data.objects}
            bpy.ops.wm.stl_import(filepath=path)
            imported = [o for o in bpy.data.objects if o.name not in before]
            dims = [round(d, 6) for d in imported[0].dimensions] if imported else None
            for o in imported:
                bpy.data.objects.remove(o, do_unlink=True)
        finally:
            us.scale_length = saved
        return {"use_scene_unit_dims(scale_length=0.001)": dims, "note": "scale_length 適用で縮む?"}

    report("use_scene_unit 効果", export_scene_unit)

    print("=== BLI_PRINT_EXPORT_SPIKE_END ===")


if __name__ == "__main__":
    main()
