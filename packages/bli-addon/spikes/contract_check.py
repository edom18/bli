"""bl_rna 契約テスト（M13 T13.2）。`blender --background --python contract_check.py`（5.0/4.4）。

bli が依存する operator が **実在**し（`get_rna_type()` 成功＝M0.5 の判定法）、bli が渡す
**引数（プロパティ）が存在**することを実機 Blender で検証する。Blender の版差で operator 名や引数が
変わると bli が壊れるため、その回帰を CI（L2 マトリクス）で早期に捕まえる。

pytest では収集しない（bpy 依存）。本番 `capability` モジュールをそのまま使う。
"""

import os
import sys

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
for pkg in ("bli-core", "bli-cli", "bli-addon"):
    sys.path.insert(0, os.path.join(ROOT, "packages", pkg, "src"))

import bpy  # type: ignore  # noqa: E402

from bli_addon.capability import RESOLVERS, CapabilityRegistry, operator_real  # noqa: E402

# bli が依存し、両版で **必ず解決すべき** 能力キー（M0.5 グラウンドトゥルース）。
MUST_RESOLVE = [
    "export.stl",
    "import.stl",
    "export.obj",
    "import.obj",
    "export.gltf",
    "import.gltf",
    "export.fbx",
    "import.fbx",  # 5.0=wm.fbx_import / 4.4=import_scene.fbx（RESOLVERS 優先順で吸収）
    "origin_set",
    "transform_apply",
]

# 標準構成では実体が無いことを期待する能力（要求時は CAPABILITY_UNAVAILABLE で縮退する前提）。
MUST_NOT_RESOLVE = ["export.3mf", "import.3mf"]

# bli が各 operator に渡す引数（プロパティ）。版差で消えると bli が壊れるので存在を要求する。
OPERATOR_ARGS = {
    "wm.stl_export": [
        "filepath",
        "ascii_format",
        "export_selected_objects",
        "global_scale",
        "use_scene_unit",
        "apply_modifiers",
    ],
    "wm.obj_export": ["filepath", "export_selected_objects"],
    "object.origin_set": ["type", "center"],
    "object.transform_apply": ["location", "rotation", "scale", "isolate_users"],
    "export_scene.gltf": ["filepath", "export_format", "use_selection"],
    # axis_forward/axis_up/global_scale/apply_unit_scale/embed_textures/path_mode は P1-3（Unity
    # 取込向け export --format fbx オプション）が依存する。版差で消えると bli の fbx_options が
    # CAPABILITY_UNAVAILABLE へ縮退するだけで INTERNAL 化はしないが、この契約テストで早期検出する。
    "export_scene.fbx": [
        "filepath",
        "use_selection",
        "axis_forward",
        "axis_up",
        "global_scale",
        "apply_unit_scale",
        "embed_textures",
        "path_mode",
    ],
}


def _op_properties(path):
    ns, _, name = path.partition(".")
    return set(getattr(getattr(bpy.ops, ns), name).get_rna_type().properties.keys())


def main():
    print("=== BLI_CONTRACT_BEGIN ===")
    print("version", bpy.app.version_string)
    failures = []
    reg = CapabilityRegistry()

    for key in MUST_RESOLVE:
        resolved = reg.resolve(key)
        if resolved is None:
            failures.append(f"必須能力 {key} が解決できない（候補 {RESOLVERS.get(key)}）")
        else:
            print(f"[OK] {key} -> {resolved}")

    for key in MUST_NOT_RESOLVE:
        resolved = reg.resolve(key)
        if resolved is not None:
            print(f"[NOTE] {key} が解決した（{resolved}）＝この環境では 3mf 実装あり")
        else:
            print(f"[OK] {key} -> なし（CAPABILITY_UNAVAILABLE 縮退の前提どおり）")

    for op, expected in OPERATOR_ARGS.items():
        if not operator_real(op):
            # import.fbx のように版差で別名が正の場合は OPERATOR_ARGS には両版共通のものだけ置く。
            print(f"[SKIP] {op} はこの版に無い（別名が解決されているはず）")
            continue
        props = _op_properties(op)
        missing = [a for a in expected if a not in props]
        if missing:
            failures.append(f"{op} に引数が無い: {missing}")
        else:
            print(f"[OK] args {op}: {expected}")

    print("=== BLI_CONTRACT_END ===")
    if failures:
        print("CONTRACT FAIL")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("CONTRACT OK")


if __name__ == "__main__":
    main()
