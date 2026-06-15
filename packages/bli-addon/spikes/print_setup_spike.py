"""print-setup（単位設定）実機確認（M8 T8.3 着手前スパイク / NEXT-M8 §2.4）。

`blender --background --python print_setup_spike.py` で実行（5.0.1 / 4.4.3 両版）。

`scene.unit_settings`（system / scale_length / length_unit）で mm/m 表示単位を設定できるか、
length_unit の enum 値が両版で同一か（'MILLIMETERS'/'METERS'）、background でも書けるかを確認する。
また「global_scale 一本化」のため、表示単位を変えても **geometry（dimensions）は不変**であること
（length_unit は表示専用・scale_length が実スケール）を確認する。
"""

import bpy  # type: ignore


def report(label, fn):
    try:
        r = fn()
        print(f"[OK] {label}: {r}")
        return r
    except Exception as e:
        print(f"[ERR] {label}: {type(e).__name__}: {e}")
        return None


def main():
    print("=== BLI_PRINT_SETUP_SPIKE_BEGIN ===")
    print("version", bpy.app.version_string)

    scene = bpy.context.scene
    us = scene.unit_settings

    # 既定値（5.0/4.4 とも METRIC / scale_length=1.0 / METERS のはず）。
    report(
        "default unit_settings",
        lambda: {
            "system": us.system,
            "scale_length": us.scale_length,
            "length_unit": us.length_unit,
        },
    )

    # length_unit の有効 enum（system=METRIC 時）を rna から取得（版間差を確認）。
    def length_unit_enum():
        prop = us.bl_rna.properties["length_unit"]
        return [e.identifier for e in prop.enum_items]

    report("length_unit enum (current system)", length_unit_enum)

    # system の有効 enum。
    def system_enum():
        prop = us.bl_rna.properties["system"]
        return [e.identifier for e in prop.enum_items]

    report("system enum", system_enum)

    # 既定 cube の dimensions（表示単位を変えても不変であることの基準）。
    def ensure_cube():
        cube = bpy.data.objects.get("Cube")
        if cube is None:
            bpy.ops.mesh.primitive_cube_add(size=2.0)  # spike のみ
            cube = bpy.context.active_object
            cube.name = "Cube"
        return cube

    cube = ensure_cube()
    dims_before = [round(d, 6) for d in cube.dimensions]

    # mm に設定（system=METRIC + length_unit=MILLIMETERS）。
    def set_mm():
        us.system = "METRIC"
        us.length_unit = "MILLIMETERS"
        return {"system": us.system, "scale_length": us.scale_length, "length_unit": us.length_unit}

    report("set unit=mm", set_mm)
    report(
        "dimensions unchanged after mm (表示専用・geometry 不変)",
        lambda: {"before": dims_before, "after": [round(d, 6) for d in cube.dimensions]},
    )

    # m に戻す。
    def set_m():
        us.system = "METRIC"
        us.length_unit = "METERS"
        return {"system": us.system, "length_unit": us.length_unit}

    report("set unit=m", set_m)

    print("=== BLI_PRINT_SETUP_SPIKE_END ===")


if __name__ == "__main__":
    main()
