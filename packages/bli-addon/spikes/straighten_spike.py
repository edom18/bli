"""straighten 数学・API 実機確認（M8 T8.2 着手前スパイク / NEXT-M8 §2）。

`blender --background --python straighten_spike.py` で実行（5.0.1 / 4.4.3 両版）。

直立補正の4メソッドを mathutils（+必要なら numpy）で実装できるか、決定的 golden を
作れるかを確認する:
- reset       : 回転をゼロにする（rotation_mode 非依存）。
- world-align : 指定 local 軸（既定は up に最も近い軸）を world up へ最小回転で合わせる。
- pca         : 頂点分布の主成分（最大分散軸）を up へ合わせる。符号は重心の偏りで一意化。
- floor       : up 方向の最下点を up=0 平面へ接地する（平行移動・回転しない）。

確認ポイント:
- mathutils.Matrix.LocRotScale / matrix_world.decompose() / Vector.rotation_difference の有無と挙動。
- numpy（linalg.eigh）が Blender に同梱されているか（pca の固有値分解に使えるか）。
- 各メソッドの golden（補正後 local +Z の world 方向・接地 z）が両版で一致するか。
"""

import math

import bpy  # type: ignore
from mathutils import Matrix, Vector  # type: ignore


def report(label, fn):
    try:
        r = fn()
        print(f"[OK] {label}: {r}")
        return r
    except Exception as e:
        print(f"[ERR] {label}: {type(e).__name__}: {e}")
        return None


AXIS_VEC = {
    "+X": Vector((1.0, 0.0, 0.0)),
    "-X": Vector((-1.0, 0.0, 0.0)),
    "+Y": Vector((0.0, 1.0, 0.0)),
    "-Y": Vector((0.0, -1.0, 0.0)),
    "+Z": Vector((0.0, 0.0, 1.0)),
    "-Z": Vector((0.0, 0.0, -1.0)),
}
LOCAL_AXIS = {
    "X": Vector((1.0, 0.0, 0.0)),
    "Y": Vector((0.0, 1.0, 0.0)),
    "Z": Vector((0.0, 0.0, 1.0)),
}


def fresh_box(name, *, dims=(1.0, 1.0, 4.0), location=(0.0, 0.0, 0.0)):
    """中心原点の直方体（dims = full サイズ）。最大分散軸を作るため Z を長くできる。"""
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)  # spike のみ
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (dims[0], dims[1], dims[2])
    # scale をデータへ焼いて以後の計算を純粋な頂点分布にする
    ov = {
        "active_object": obj,
        "object": obj,
        "selected_objects": [obj],
        "selected_editable_objects": [obj],
    }
    with bpy.context.temp_override(**ov):
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def local_axis_world_dir(obj, axis_unit):
    """local 軸（単位ベクトル）の world 空間方向（正規化・scale 除去）。"""
    return (obj.matrix_world.to_quaternion() @ axis_unit).normalized()


def refresh(obj=None):
    """matrix_world を最新化する（--background では rotation_euler 設定後に必須）。

    GUI 常駐ではユーザ操作で depsgraph が評価済みだが、background かつ bpy.data 直接生成では
    matrix_world が stale（identity）のまま残る。実装側も読み取り前に update する想定。
    """
    bpy.context.view_layer.update()


def main():
    print("=== BLI_STRAIGHTEN_SPIKE_BEGIN ===")
    print("version", bpy.app.version_string)
    print("mode", bpy.context.mode)

    # --- API 有無 ---
    report("Matrix.LocRotScale exists?", lambda: hasattr(Matrix, "LocRotScale"))
    report(
        "Vector.rotation_difference exists?",
        lambda: hasattr(Vector((0, 0, 1)), "rotation_difference"),
    )
    report(
        "matrix_world.decompose works?",
        lambda: [round(v, 4) for v in Matrix.Identity(4).decompose()[1]],  # quaternion
    )

    def numpy_check():
        import numpy as np  # type: ignore

        m = np.array([[2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 5.0]])
        w, _vecs = np.linalg.eigh(m)
        return {"numpy": np.__version__, "eigvals": [round(float(x), 3) for x in w]}

    has_numpy = report("numpy linalg.eigh available?", numpy_check) is not None

    # --- reset: 回転をゼロに ---
    def do_reset():
        obj = fresh_box("StrReset")
        obj.rotation_euler = (math.radians(30), math.radians(20), math.radians(10))
        obj.rotation_euler = (0.0, 0.0, 0.0)  # reset 相当
        return [round(math.degrees(a), 4) for a in obj.rotation_euler]

    report("reset -> euler(deg)", do_reset)

    # --- world-align: tilt した box の local Z を world +Z に合わせる ---
    def world_align(obj, local_axis_name, up_axis_name):
        target = AXIS_VEC[up_axis_name]
        if local_axis_name is not None:
            local_unit = LOCAL_AXIS[local_axis_name]
            cur = local_axis_world_dir(obj, local_unit)
            # 反対向きの方が近ければ符号を反転（local 軸の ± どちらでも近い向きへ）
            if cur.dot(target) < 0:
                cur = -cur
                local_unit = -local_unit
        else:
            # up に最も近い signed local 軸を自動選択
            best = None
            for lu in (LOCAL_AXIS["X"], LOCAL_AXIS["Y"], LOCAL_AXIS["Z"]):
                wd = local_axis_world_dir(obj, lu)
                for sgn in (1.0, -1.0):
                    d = (wd * sgn).dot(target)
                    if best is None or d > best[0]:
                        best = (d, wd * sgn, lu * sgn)
            _, cur, local_unit = best
        delta = cur.rotation_difference(target)
        loc, rot, scale = obj.matrix_world.decompose()
        obj.matrix_world = Matrix.LocRotScale(loc, delta @ rot, scale)
        refresh(obj)
        return local_axis_world_dir(
            obj, local_unit if isinstance(local_unit, Vector) else LOCAL_AXIS[local_axis_name]
        )

    def do_world_align_explicit():
        obj = fresh_box("StrWA1")
        obj.rotation_euler = (math.radians(30), 0.0, 0.0)  # X 周りに 30° tilt
        refresh(obj)  # matrix_world を tilt 反映
        before = local_axis_world_dir(obj, LOCAL_AXIS["Z"])
        print("    (debug) localZ_world before align =", [round(v, 5) for v in before])
        res = world_align(obj, "Z", "+Z")
        # 補正後の local +Z の world 方向は (0,0,1) に一致するはず
        return {
            "localZ_world": [round(v, 5) for v in res],
            "euler": [round(math.degrees(a), 3) for a in obj.rotation_euler],
        }

    report("world-align(axis=Z,up=+Z) tilt30 -> localZ_world≈(0,0,1)", do_world_align_explicit)

    def do_world_align_auto():
        obj = fresh_box("StrWA2", dims=(1.0, 1.0, 1.0))
        # 小さく傾けて「最も近い軸」が Z になる状況（auto 選択）
        obj.rotation_euler = (math.radians(15), math.radians(10), 0.0)
        refresh(obj)
        world_align(obj, None, "+Z")
        z_world = local_axis_world_dir(obj, LOCAL_AXIS["Z"])
        return {"chosen_localZ_world": [round(v, 5) for v in z_world]}

    report("world-align(auto,up=+Z) -> nearest axis to +Z", do_world_align_auto)

    # --- floor: up 方向の最下点を 0 へ ---
    def do_floor():
        obj = fresh_box("StrFloor", dims=(2.0, 2.0, 2.0), location=(0.0, 0.0, 5.0))
        refresh(obj)
        up = AXIS_VEC["+Z"]
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        min_proj = min(c.dot(up) for c in corners)
        obj.matrix_world = Matrix.Translation(-min_proj * up) @ obj.matrix_world
        corners2 = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        return {"min_z_after": round(min(c.z for c in corners2), 6)}

    report("floor(up=+Z) box@z5 -> min_z≈0", do_floor)

    # --- pca: 最大分散軸を +Z に。符号は (centroid-origin)·axis で一意化 ---
    def pca_axis(obj):
        verts = [obj.matrix_world @ v.co for v in obj.data.vertices]
        n = len(verts)
        cx = sum(v.x for v in verts) / n
        cy = sum(v.y for v in verts) / n
        cz = sum(v.z for v in verts) / n
        centroid = Vector((cx, cy, cz))
        # 共分散（対称 3x3）
        sxx = syy = szz = sxy = sxz = syz = 0.0
        for v in verts:
            dx, dy, dz = v.x - cx, v.y - cy, v.z - cz
            sxx += dx * dx
            syy += dy * dy
            szz += dz * dz
            sxy += dx * dy
            sxz += dx * dz
            syz += dy * dz
        if has_numpy:
            import numpy as np  # type: ignore

            cov = np.array([[sxx, sxy, sxz], [sxy, syy, syz], [sxz, syz, szz]]) / n
            w, vecs = np.linalg.eigh(cov)  # 昇順固有値
            principal = Vector((float(vecs[0, 2]), float(vecs[1, 2]), float(vecs[2, 2])))
            eigvals = [round(float(x), 4) for x in w]
        else:
            principal, eigvals = (Vector((0, 0, 1)), None)
        # 符号: 原点→重心 方向に揃える（重心が偏っている側を + に）
        origin = obj.matrix_world.translation
        if (centroid - origin).dot(principal) < 0:
            principal = -principal
        return principal.normalized(), eigvals, centroid

    def do_pca():
        # 非対称な細長い分布: Z 方向に長く、+Z 端に追加頂点を置いて重心を +Z へ偏らせる。
        old = bpy.data.objects.get("StrPCA")
        if old is not None:
            bpy.data.objects.remove(old, do_unlink=True)
        coords = []
        for z in (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 3.0, 3.0):  # +Z 端に重複で重心を偏らせる
            coords.append((0.2, 0.0, z))
            coords.append((-0.2, 0.0, z))
            coords.append((0.0, 0.2, z))
        me = bpy.data.meshes.new("StrPCAMesh")
        me.from_pydata(coords, [], [])
        me.update()
        obj = bpy.data.objects.new("StrPCA", me)
        bpy.context.scene.collection.objects.link(obj)
        # tilt させてから pca で +Z に戻す
        obj.rotation_euler = (math.radians(40), math.radians(15), 0.0)
        refresh(obj)
        axis, eigvals, _ = pca_axis(obj)
        before = [round(v, 5) for v in axis]
        # axis を +Z に合わせる
        target = AXIS_VEC["+Z"]
        delta = axis.rotation_difference(target)
        loc, rot, scale = obj.matrix_world.decompose()
        obj.matrix_world = Matrix.LocRotScale(loc, delta @ rot, scale)
        refresh(obj)
        axis_after, _, _ = pca_axis(obj)
        return {
            "eigvals": eigvals,
            "principal_world_before": before,
            "principal_world_after": [round(v, 5) for v in axis_after],
        }

    report("pca(elongated Z, tilted) -> principal aligned to +Z", do_pca)

    print("=== BLI_STRAIGHTEN_SPIKE_END ===")


if __name__ == "__main__":
    main()
