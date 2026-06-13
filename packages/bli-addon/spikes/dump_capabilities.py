"""能力検出スパイク（T0.5.1 / research.md 論点3）。

`blender --background --python dump_capabilities.py` で実行する。
import/export/print3d の operator 実在・引数・addon module を JSON でダンプする。
出力は spikes/out/capabilities-<version>.json と stdout。
"""

import json
import os
import sys

import bpy  # type: ignore


def op_exists(path):
    ns, _, name = path.partition(".")
    group = getattr(bpy.ops, ns, None)
    if group is None:
        return False
    return hasattr(group, name)


def op_props(path):
    """operator の引数名と型を bl_rna から取得（存在すれば）。"""
    ns, _, name = path.partition(".")
    try:
        op = getattr(getattr(bpy.ops, ns), name)
        rna = op.get_rna_type()
        props = {}
        for pr in rna.properties:
            if pr.identifier == "rna_type":
                continue
            props[pr.identifier] = pr.type  # ENUM/FLOAT/BOOLEAN/STRING ...
        return props
    except Exception as e:
        return {"_error": str(e)}


CANDIDATES = [
    "wm.stl_export",
    "wm.stl_import",
    "export_mesh.stl",
    "import_mesh.stl",
    "wm.obj_export",
    "wm.obj_import",
    "export_scene.obj",
    "import_scene.obj",
    "export_scene.gltf",
    "import_scene.gltf",
    "export_scene.fbx",
    "import_scene.fbx",
    "wm.fbx_import",
    "wm.fbx_export",
    "import_mesh.3mf",
    "export_mesh.3mf",
    "object.origin_set",
    "object.transform_apply",
    "object.select_all",
    "mesh.print3d_check_all",
    "mesh.print3d_check_solid",
    "mesh.print3d_clean_non_manifold",
    "mesh.print3d_check_non_manifold",
]

ADDON_HINTS = ["print3d", "3mf", "io_mesh_3mf", "object_print3d_utils", "print3d_toolbox"]


def list_addons():
    try:
        import addon_utils  # type: ignore

        names = []
        for mod in addon_utils.modules():
            nm = getattr(mod, "__name__", "")
            if any(h in nm.lower() for h in ADDON_HINTS):
                _loaded_default, loaded_state = addon_utils.check(nm)
                names.append({"module": nm, "enabled": bool(loaded_state)})
        return names
    except Exception as e:
        return [{"_error": str(e)}]


def try_enable(module):
    try:
        import addon_utils  # type: ignore

        addon_utils.enable(module, default_set=False)
        return module in {a.module for a in bpy.context.preferences.addons}
    except Exception as e:
        return f"error: {e}"


def main():
    data = {
        "blender_version": list(bpy.app.version),
        "blender_version_string": bpy.app.version_string,
        "python_version": sys.version,
        "operators": {
            p: {"exists": op_exists(p), "props": op_props(p) if op_exists(p) else None}
            for p in CANDIDATES
        },
        "print3d_addons": list_addons(),
        "enable_attempt": {
            "print3d_toolbox": try_enable("print3d_toolbox"),
            "object_print3d_utils": try_enable("object_print3d_utils"),
        },
        "unit_settings": {
            "system": bpy.context.scene.unit_settings.system,
            "scale_length": bpy.context.scene.unit_settings.scale_length,
            "length_unit": bpy.context.scene.unit_settings.length_unit,
        },
    }
    # print3d を有効化後に再チェック
    data["operators_after_enable"] = {
        p: op_exists(p) for p in CANDIDATES if p.startswith("mesh.print3d")
    }

    here = os.path.dirname(__file__)
    outdir = os.path.join(here, "out")
    os.makedirs(outdir, exist_ok=True)
    ver = "-".join(str(x) for x in bpy.app.version[:2])
    outpath = os.path.join(outdir, f"capabilities-{ver}.json")
    with open(outpath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("=== BLI_CAPABILITY_DUMP_BEGIN ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print("=== BLI_CAPABILITY_DUMP_END ===")
    print("written:", outpath)


if __name__ == "__main__":
    main()
