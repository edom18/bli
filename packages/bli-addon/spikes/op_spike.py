"""run_operator/temp_override/undo_push 実機確認（T0.5.4 / research.md 論点2）。

`blender --background --python op_spike.py` で実行。
origin_set 等を temp_override 経由で実行し、poll/FINISHED/undo_push を確認する。
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
    print("=== BLI_OP_SPIKE_BEGIN ===")
    print("version", bpy.app.version_string)

    # クリーンシーンに cube を1つ用意（bpy.data 直接は面倒なので primitive を temp_override 無しで）
    report("primitive_cube_add", lambda: bpy.ops.mesh.primitive_cube_add())
    obj = bpy.context.active_object or (bpy.data.objects[0] if bpy.data.objects else None)
    print("active_object", getattr(obj, "name", None), "mode", getattr(obj, "mode", None))

    # cube を移動してから origin_set を試す
    if obj:
        obj.location = (1.0, 2.0, 3.0)

    # poll の事前評価
    def poll_origin():
        return bpy.ops.object.origin_set.poll()

    report("origin_set.poll()", poll_origin)

    # temp_override で origin_set（ORIGIN_GEOMETRY, MEDIAN）
    def run_origin():
        override = {"active_object": obj, "selected_objects": [obj], "object": obj}
        with bpy.context.temp_override(**override):
            res = bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")
        return {
            "result": sorted(res),
            "finished": "FINISHED" in res,
            "loc": tuple(obj.matrix_world.translation),
        }

    report("origin_set via temp_override", run_origin)

    # 直接行列計算で原点を world 指定へ（フォールバック手段の確認）
    def direct_origin_to_world():
        import mathutils  # type: ignore

        target = mathutils.Vector((0.0, 0.0, 0.0))
        delta = obj.matrix_world.translation - target
        obj.data.transform(mathutils.Matrix.Translation(delta))
        obj.matrix_world.translation = target
        return {"loc": tuple(obj.matrix_world.translation)}

    report("direct origin->world(0,0,0)", direct_origin_to_world)

    # undo_push の挙動
    def push():
        bpy.ops.ed.undo_push(message="bli: spike")
        return "pushed"

    report("ed.undo_push(message=)", push)

    # transform_apply の引数確認（isolate_users は 5.0 新）
    def apply_rot():
        override = {"active_object": obj, "selected_objects": [obj], "object": obj}
        with bpy.context.temp_override(**override):
            res = bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        return {"result": sorted(res)}

    report("transform_apply(rotation=True)", apply_rot)

    print("=== BLI_OP_SPIKE_END ===")


if __name__ == "__main__":
    main()
