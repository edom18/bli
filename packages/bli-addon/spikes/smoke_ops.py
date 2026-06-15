"""M3 ops スタックを Blender 埋め込み Python 上で疎通させるスモーク。

`blender --background --python smoke_ops.py` で実行。

--background では `bpy.app.timers` が発火しないため、本スモークは
**メインスレッドで Dispatcher.pump ループ + 別スレッドで client** という構成で
GUI 常駐の挙動を近似する（HANDOFF §6.5 / research 付録C 準拠）。

検証（golden）:
  ping → scene-info → object-info(Cube) [dims + world bbox]
  → set-origin world (1,0,0)        : 直接行列フォールバック（geometry 固定）
  → object-info(Cube)               : location==[1,0,0], dims 不変
  → set-origin geometry median      : operator 経路。原点が幾何中心(=world原点)へ戻る
  → object-info(Cube)               : location≈[0,0,0]
  → request-status (M4)             : 既知IDの決着回収 / 未知IDは known=False
  → list-objects (M5)               : type=MESH は ["Cube"] のみ / regex フィルタ
  → scene-info output_ref (M5)      : 閾値を下げて退避を強制 → sha256 検証で読み戻し
  → transform (M6)                  : set location / set rotation(deg) / delta scale(乗算)
  → apply-transform (M6)            : scale をベイク（scale→1 / dims×2）
  → select (M6)                     : Cube を選択し active に
  → transform 複合/非Euler/親付き    : loc+rot 同時・QUATERNION・world location（set/delta）
  → apply-transform ガード          : 非mesh / 共有mesh（--make-single-user）/ --targets限定
  → select ガード                    : 不正active で状態保持 / 不正regex は USER_INPUT
  → duplicate (M6 T6.2)             : count=2 offset=(3,0,0) → 新規2個・world位置 +3/+6 累積
  → duplicate linked/親付き          : linked で mesh_users +1 / 親付き Child の world offset 整合
  → delete (M6 T6.2)                : 複製を削除 → シーンから消える・backup返却・元Cubeは健在
  → delete ガード                    : 存在しない名は E_TARGET_NOT_FOUND（USER_INPUT）
  → material (M6 T6.3)              : create-and-assign（Base Color 往復・slot.link 報告）/ list / assign
  → material 共有ガード              : 共有mesh DATA slot は --make-single-user 必須（兄弟へ波及せず /
                                       解決失敗時は分離しない / OBJECT リンク slot はガード不要 / Codex P2）
  → material ガード                  : 存在しないmat=E_TARGET_NOT_FOUND / 非mesh=E_PRECONDITION /
                                       --color on assign=INVALID_PARAMS
  → modifier (M6 T6.4)              : add(5種)/list/remove → apply(mesh焼き込み)
  → modifier 共有ガード              : apply は共有mesh で --make-single-user 必須（兄弟へ波及せず）
  → mesh (M7 T7.1)                  : recalc-normals（flipped 統計・法線込み fingerprint）/
                                       merge-by-distance（重複頂点 9→8）/ 非mesh・共有ガード
  → mesh (M7 T7.2)                  : extrude（8→16v）/ bevel（seg1 → 24v）/ inset（→32v）/
                                       op別必須・範囲ガード
  → mesh (M7 T7.3)                  : boolean（modifier 経由・UNION/INTERSECT/DIFFERENCE の world bbox）/
                                       decimate（ico 80f→40f・ratio=1.0 no-op）/ 相手ガード/共有ガード
"""

import os
import sys
import tempfile
import threading
import time
import traceback

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
for pkg in ("bli-core", "bli-cli", "bli-addon"):
    sys.path.insert(0, os.path.join(ROOT, "packages", pkg, "src"))

os.environ["BLI_STATE_DIR"] = tempfile.mkdtemp(prefix="bli-ops-smoke-")

import bpy  # type: ignore  # noqa: E402

from bli import client  # noqa: E402
from bli_addon import ops  # noqa: E402
from bli_addon import server as srv_mod  # noqa: E402
from bli_addon.capability import CapabilityRegistry  # noqa: E402
from bli_addon.dispatcher import Dispatcher  # noqa: E402
from bli_core import runtime  # noqa: E402
from bli_core.commands import load_definitions  # noqa: E402
from bli_core.schema import schema_hash  # noqa: E402


def approx(a, b, tol=1e-4):
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=False))


def ensure_cube():
    cube = bpy.data.objects.get("Cube")
    if cube is None:
        bpy.ops.mesh.primitive_cube_add(size=2.0)  # spike のみ（AST guard 対象外）
        cube = bpy.context.active_object
        cube.name = "Cube"
    return cube


def ensure_quaternion_empty():
    """QUATERNION モードの Empty を用意（非 Euler 回転の検証用・メインスレッドで呼ぶ）。

    Empty にするのは MESH フィルタ（list-objects）の golden を壊さないため。
    """
    obj = bpy.data.objects.get("QRot")
    if obj is None:
        obj = bpy.data.objects.new("QRot", None)  # data=None → EMPTY
        bpy.context.scene.collection.objects.link(obj)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)  # identity
    return obj


def ensure_parented():
    """親(offset)に子を付けた Empty ペアを用意（world 空間 transform の検証用）。

    親を (10,0,0) に置き、子のローカル原点(0,0,0)＝world(10,0,0) から始める。
    EMPTY なので list-objects(MESH) golden は壊さない。メインスレッドで呼ぶ。
    """
    parent = bpy.data.objects.get("Parent")
    if parent is None:
        parent = bpy.data.objects.new("Parent", None)
        bpy.context.scene.collection.objects.link(parent)
    parent.location = (10.0, 0.0, 0.0)
    child = bpy.data.objects.get("Child")
    if child is None:
        child = bpy.data.objects.new("Child", None)
        bpy.context.scene.collection.objects.link(child)
    child.parent = parent
    child.location = (0.0, 0.0, 0.0)
    return parent, child


def ensure_shared_mesh():
    """同一 mesh を共有する2つの MESH object（ShA/ShB）を用意（共有 mesh ガード検証用）。"""
    mesh = bpy.data.meshes.get("ShMesh")
    if mesh is None:
        mesh = bpy.data.meshes.new("ShMesh")
        mesh.from_pydata([(0.0, 0.0, 0.0)], [], [])  # 単一頂点で十分
    for name in ("ShA", "ShB"):
        if name not in bpy.data.objects:
            o = bpy.data.objects.new(name, mesh)
            bpy.context.scene.collection.objects.link(o)
    return bpy.data.objects["ShA"], bpy.data.objects["ShB"]


def ensure_object_linked_shared():
    """OBJECT リンク slot を持つ共有 mesh ペア（OLnkA/OLnkB）を用意（Codex P2 検証用）。

    OLnkMesh を OLnkA/OLnkB で共有し、OLnkA の active slot を OBJECT リンクにする。
    OBJECT リンク slot への assign が共有 mesh を触らず make_single_user 不要なことの検証用。
    """
    mesh = bpy.data.meshes.get("OLnkMesh")
    if mesh is None:
        mesh = bpy.data.meshes.new("OLnkMesh")
        mesh.from_pydata([(0.0, 0.0, 0.0)], [], [])
    base = bpy.data.materials.get("OLnkBase") or bpy.data.materials.new("OLnkBase")
    if len(mesh.materials) == 0:
        mesh.materials.append(base)  # DATA slot 0
    for name in ("OLnkA", "OLnkB"):
        if name not in bpy.data.objects:
            o = bpy.data.objects.new(name, mesh)
            bpy.context.scene.collection.objects.link(o)
    a = bpy.data.objects["OLnkA"]
    a.material_slots[0].link = "OBJECT"  # object 限定スロットにする
    a.material_slots[0].material = base
    if "OLnkNew" not in bpy.data.materials:
        bpy.data.materials.new("OLnkNew")  # assign 用の別マテリアル
    return a, bpy.data.objects["OLnkB"]


def ensure_mesh_fixtures():
    """M7 mesh 編集の検証用オブジェクトを用意（メインスレッドで生成・bmesh は spike 内で可）。

    - MeshFlip: cube の1面だけ巻き順を反転（recalc で flipped=1 → 一貫化）。
    - MeshDbl : cube に重複頂点を1つ追加（9頂点・merge-by-distance で 9→8）。
    - MeshShA/MeshShB: 同一 cube mesh を共有（共有 mesh ガード検証用・mesh_users=2）。
    - MeshExtrude/MeshBevel/MeshInset: clean cube（T7.2 の extrude/bevel/inset golden 用）。
    いずれも MESH なので list-objects(MESH) golden（block 9）に加える。
    """
    import bmesh as _bmesh  # spike 内（AST guard 対象外）

    def _fresh_cube_obj(name):
        o = bpy.data.objects.get(name)
        if o is not None:
            return o
        bpy.ops.mesh.primitive_cube_add(size=2.0)  # spike のみ（AST guard 対象外）
        o = bpy.context.active_object
        o.name = name
        return o

    # MeshFlip: 1 面の巻き順を反転して不整合を作る
    flip = _fresh_cube_obj("MeshFlip")
    if len(flip.data.polygons) == 6:
        bm = _bmesh.new()
        bm.from_mesh(flip.data)
        bm.faces.ensure_lookup_table()
        _bmesh.ops.reverse_faces(bm, faces=[bm.faces[0]])
        bm.to_mesh(flip.data)
        bm.free()
        flip.data.update()

    # MeshDbl: 既存頂点に重なる重複頂点を1つ追加（8→9）
    dbl = _fresh_cube_obj("MeshDbl")
    if len(dbl.data.vertices) == 8:
        bm = _bmesh.new()
        bm.from_mesh(dbl.data)
        bm.verts.ensure_lookup_table()
        bm.verts.new(bm.verts[0].co.copy())
        bm.to_mesh(dbl.data)
        bm.free()
        dbl.data.update()

    # MeshShA/MeshShB: 同一 mesh を共有（cube・面ありで recalc/merge が意味を持つ）
    sha = _fresh_cube_obj("MeshShA")
    if "MeshShB" not in bpy.data.objects:
        shb = bpy.data.objects.new("MeshShB", sha.data)  # 同一 mesh を共有
        bpy.context.scene.collection.objects.link(shb)

    # T7.2: 各 op を clean cube で検証（exact count golden）。
    # MeshExtrude は scale=2（world≠local）で extrude offset の world→local 変換を検証する。
    ext = _fresh_cube_obj("MeshExtrude")
    ext.scale = (2.0, 2.0, 2.0)
    _fresh_cube_obj("MeshBevel")
    _fresh_cube_obj("MeshInset")

    # T7.3: decimate は ico_sphere（決定的トポロジ・両版一致 80f→40f）/ boolean は重なる2 cube。
    # MeshBoolA/C/D を MeshBoolB（x+1 平行移動）に対して INTERSECT/DIFFERENCE/UNION する
    # （operand B は read-only で3回再利用・world bbox が幾何的に決定的＝solver 非依存の golden）。
    if "MeshDecimate" not in bpy.data.objects:
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2)  # spike のみ
        ico = bpy.context.active_object
        ico.name = "MeshDecimate"
    _fresh_cube_obj("MeshDecimate1")  # decimate ratio=1.0（無削減）検証用の clean cube
    for name in ("MeshBoolA", "MeshBoolC", "MeshBoolD"):
        _fresh_cube_obj(name).location = (0.0, 0.0, 0.0)  # 原点（A/C/D は同一配置の独立 fixture）
    _fresh_cube_obj("MeshBoolB").location = (
        1.0,
        0.0,
        0.0,
    )  # A/C/D と x で半分重なる（重なり x[0,1]）

    # MeshShC/MeshShD: 同一 cube mesh を共有（boolean/decimate の共有ガード検証用・mesh_users=2）。
    shc = _fresh_cube_obj("MeshShC")
    if "MeshShD" not in bpy.data.objects:
        shd = bpy.data.objects.new("MeshShD", shc.data)  # 同一 mesh を共有
        bpy.context.scene.collection.objects.link(shd)
    return flip, dbl, sha


def call_retry(method, params=None, request_id=None, attempts=40):
    """SESSION_BUSY（接続クローズ直後のロック解放待ち）を少し待って再試行する。"""
    last = None
    for _ in range(attempts):
        try:
            return client.call(method, params, request_id=request_id)
        except client.RpcRemoteError as e:
            if e.error.get("message") == "SESSION_BUSY":
                last = e
                time.sleep(0.02)
                continue
            raise
    raise last


def run_calls():
    # 1) ping
    _result, hello = call_retry("ping")
    assert hello["type"] == "hello-ok", hello
    assert hello["blender_version"] == bpy.app.version_string
    print("ping_ok", hello["blender_version"])

    # 2) scene-info
    si, _ = call_retry("scene-info", {"depth": 1})
    names = [o["name"] for o in si["data"]["objects"]]
    assert "Cube" in names, names
    print("scene_info_ok", si["data"]["object_count"], names)

    # 3) object-info Cube（M5: world bbox の golden を追加）
    oi, _ = call_retry("object-info", {"targets": "Cube"})
    dims = oi["data"]["dimensions"]
    assert approx(dims, [2.0, 2.0, 2.0]), dims
    bbox = oi["data"]["bbox"]
    assert approx(bbox["min"], [-1.0, -1.0, -1.0]), bbox
    assert approx(bbox["max"], [1.0, 1.0, 1.0]), bbox
    assert approx(bbox["size"], [2.0, 2.0, 2.0]), bbox
    print("object_info_ok dims=", dims, "bbox=", bbox, "fp=", oi.get("fingerprint"))

    # 4) set-origin world (1,0,0): 直接行列。geometry は固定。
    so, _ = call_retry("set-origin", {"targets": "Cube", "to": "world", "x": 1.0})
    assert so["operation"] == "set-origin"
    assert approx(so["data"]["origin_world"], [1.0, 0.0, 0.0]), so["data"]
    print("set_origin_world_ok", so["data"]["origin_world"])

    # 5) object-info: 原点は (1,0,0) へ、寸法は不変（見た目固定）
    oi2, _ = call_retry("object-info", {"targets": "Cube"})
    assert approx(oi2["data"]["location"], [1.0, 0.0, 0.0]), oi2["data"]["location"]
    assert approx(oi2["data"]["dimensions"], [2.0, 2.0, 2.0]), oi2["data"]["dimensions"]
    print("after_world location=", oi2["data"]["location"], "dims=", oi2["data"]["dimensions"])

    # 6) set-origin geometry median: operator 経路。幾何中心(=world原点)へ戻る。
    so2, _ = call_retry("set-origin", {"targets": "Cube", "to": "geometry", "center": "median"})
    assert approx(so2["data"]["origin_world"], [0.0, 0.0, 0.0]), so2["data"]
    print("set_origin_geometry_ok", so2["data"]["origin_world"])

    # 7) object-info: location ≈ 0
    oi3, _ = call_retry("object-info", {"targets": "Cube"})
    assert approx(oi3["data"]["location"], [0.0, 0.0, 0.0]), oi3["data"]["location"]
    print("after_geometry location=", oi3["data"]["location"])

    # 8) request-status（M4）: 固定IDで set-origin を確定させ、後追いで決着を回収
    rid = "smoke-fixed-id"
    call_retry("set-origin", {"targets": "Cube", "to": "geometry"}, request_id=rid)
    rs, _ = call_retry("request-status", {"id": rid})
    assert rs["data"]["known"] is True, rs["data"]
    assert rs["data"]["state"] == "DONE", rs["data"]
    rs2, _ = call_retry("request-status", {"id": "never-seen-id"})
    assert rs2["data"]["known"] is False, rs2["data"]
    print(
        "request_status_ok known/state=",
        rs["data"]["state"],
        "unknown_known=",
        rs2["data"]["known"],
    )

    # 9) list-objects（M5）: type=MESH は Cube のみ（大小無視）/ regex フィルタ
    lo, _ = call_retry("list-objects", {"type": "mesh"})
    lo_names = [o["name"] for o in lo["data"]["objects"]]
    # ShA/ShB は共有mesh検証用 / OLnkA/OLnkB は OBJECT リンク slot 検証用 /
    # MeshFlip/MeshDbl/MeshShA/MeshShB は M7 mesh 編集検証用（いずれも MESH）。
    assert set(lo_names) == {
        "Cube",
        "ShA",
        "ShB",
        "OLnkA",
        "OLnkB",
        "MeshFlip",
        "MeshDbl",
        "MeshShA",
        "MeshShB",
        "MeshExtrude",
        "MeshBevel",
        "MeshInset",
        "MeshDecimate",
        "MeshDecimate1",
        "MeshBoolA",
        "MeshBoolB",
        "MeshBoolC",
        "MeshBoolD",
        "MeshShC",
        "MeshShD",
    }, lo_names
    assert lo["data"]["count"] == 20, lo["data"]
    lo2, _ = call_retry("list-objects", {"regex": "^Cu"})
    assert [o["name"] for o in lo2["data"]["objects"]] == ["Cube"], lo2["data"]
    print("list_objects_ok mesh=", sorted(lo_names))

    # 非ジオメトリ（Light）の object-info: bbox は None（Codex P2: 偽の零サイズを出さない）
    oi_light, _ = call_retry("object-info", {"targets": "Light"})
    assert oi_light["data"]["bbox"] is None, oi_light["data"].get("bbox")
    print("nongeometry_bbox_none_ok Light")

    # 10) scene-info output_ref 退避（M5）: 閾値を一時的に下げて shared-fs 退避を強制し、
    #     退避ファイルを sha256 検証付きで読み戻す（往復）。
    import bli_core.output_ref as outref

    saved_threshold = outref.INLINE_THRESHOLD
    outref.INLINE_THRESHOLD = 50
    try:
        si2, _ = call_retry("scene-info", {"depth": 1})
    finally:
        outref.INLINE_THRESHOLD = saved_threshold
    ref = si2["output_ref"]
    assert si2["data"] is None, si2
    assert ref is not None and ref["transport"] == "shared-fs", ref
    restored = outref.load_verified(ref)
    assert any(o["name"] == "Cube" for o in restored["objects"]), restored
    print("scene_info_offload_ok size=", ref["size"], "sha256=", ref["sha256"][:12])

    # 11) transform / apply-transform / select（M6 T6.1）
    tr, _ = call_retry("transform", {"targets": "Cube", "location": [5.0, 0.0, 0.0], "mode": "set"})
    assert approx(tr["data"]["location"], [5.0, 0.0, 0.0]), tr["data"]
    trr, _ = call_retry(
        "transform", {"targets": "Cube", "rotation": [0.0, 0.0, 90.0], "mode": "set"}
    )
    assert approx(trr["data"]["rotation_euler_deg"], [0.0, 0.0, 90.0]), trr["data"]
    trd, _ = call_retry("transform", {"targets": "Cube", "scale": [2.0, 2.0, 2.0], "mode": "delta"})
    assert approx(trd["data"]["scale"], [2.0, 2.0, 2.0]), trd["data"]
    print("transform_ok loc/rot/scale=", trd["data"]["location"], trd["data"]["scale"])

    # apply-transform scale: scale→[1,1,1] / dims は2倍（[4,4,4]）にベイク
    ap, _ = call_retry("apply-transform", {"targets": "Cube", "scale": True})
    assert approx(ap["data"]["scale"], [1.0, 1.0, 1.0]), ap["data"]
    assert approx(ap["data"]["dimensions"], [4.0, 4.0, 4.0]), ap["data"]
    print("apply_transform_ok scale/dims=", ap["data"]["scale"], ap["data"]["dimensions"])

    # select: Cube を選択し active に
    sel, _ = call_retry("select", {"targets": "Cube"})
    assert sel["data"]["selected"] == ["Cube"], sel["data"]
    assert sel["data"]["active"] == "Cube", sel["data"]
    # select は fingerprint を返す（Codex P2: 契約どおり drift 検証可能に）
    assert sel.get("fingerprint") and len(sel["fingerprint"]) == 16, sel
    print(
        "select_ok",
        sel["data"]["selected"],
        "active=",
        sel["data"]["active"],
        "fp=",
        sel["fingerprint"],
    )

    # 不正な --active: エラーになり、直前の選択状態は変わらない（Codex P2: 検証→変更）
    try:
        call_retry("select", {"targets": "Cube", "active": "NoSuchObj"})
        raise AssertionError("bad --active should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    assert bpy.data.objects["Cube"].select_get(), "選択状態が失敗時に保持されていない"
    print("select_bad_active_ok state-preserved")

    # 不正な正規表現 targets: USER_INPUT エラーにする（INTERNAL にしない・Codex P2）
    try:
        call_retry("object-info", {"targets": "["})
        raise AssertionError("malformed regex should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
        assert e.error.get("data", {}).get("category") == "USER_INPUT", e.error
    print("bad_regex_target_ok user-input-error")

    # 非 Euler（QUATERNION）モードでも rotation が native 表現に反映される（Codex P2）。
    # 報告 euler は rotation_quaternion から導出するため、quaternion が変わらなければ 0 のまま。
    tq, _ = call_retry(
        "transform", {"targets": "QRot", "rotation": [0.0, 0.0, 90.0], "mode": "set"}
    )
    assert approx(tq["data"]["rotation_euler_deg"], [0.0, 0.0, 90.0]), tq["data"]
    print("transform_quaternion_mode_ok", tq["data"]["rotation_euler_deg"])

    # 親付き Child（親は world(10,0,0)）: transform --location は world 空間で反映され、
    # report（matrix_world）と一致する（Codex P2: 親ローカルにしない）。
    tc, _ = call_retry(
        "transform", {"targets": "Child", "location": [0.0, 0.0, 0.0], "mode": "set"}
    )
    assert approx(tc["data"]["location"], [0.0, 0.0, 0.0]), tc["data"]
    tcd, _ = call_retry(
        "transform", {"targets": "Child", "location": [2.0, 0.0, 0.0], "mode": "delta"}
    )
    assert approx(tcd["data"]["location"], [2.0, 0.0, 0.0]), tcd["data"]
    print("transform_world_location_ok set/delta=", tc["data"]["location"], tcd["data"]["location"])

    # apply-transform は現在の選択でなく --targets だけに作用する（Codex P1）。
    # 別オブジェクト(Parent)を選択状態にしてから Cube に apply-transform する。
    call_retry("select", {"targets": "Parent"})
    call_retry("transform", {"targets": "Cube", "scale": [3.0, 3.0, 3.0], "mode": "set"})
    apc, _ = call_retry("apply-transform", {"targets": "Cube", "scale": True})
    assert approx(apc["data"]["scale"], [1.0, 1.0, 1.0]), apc["data"]
    assert apc["data"]["name"] == "Cube", apc["data"]
    print("apply_transform_targets_only_ok scale=", apc["data"]["scale"])

    # 複合指定（location + rotation 同時・親付き Child）でも location は world で確定する
    # （レビュー P2: location を先に書いて後続 rot/scale で並進がずれない順序）。
    tcr, _ = call_retry(
        "transform",
        {
            "targets": "Child",
            "location": [1.0, 2.0, 3.0],
            "rotation": [0.0, 0.0, 45.0],
            "mode": "set",
        },
    )
    assert approx(tcr["data"]["location"], [1.0, 2.0, 3.0]), tcr["data"]
    assert approx(tcr["data"]["rotation_euler_deg"], [0.0, 0.0, 45.0]), tcr["data"]
    print("transform_combined_ok", tcr["data"]["location"], tcr["data"]["rotation_euler_deg"])

    # apply-transform を mesh 以外（QRot=EMPTY）に → 分かりやすい E_PRECONDITION（レビュー P2）
    try:
        call_retry("apply-transform", {"targets": "QRot", "scale": True})
        raise AssertionError("apply on empty should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    print("apply_nonmesh_guard_ok")

    # 共有 mesh の apply-transform: --make-single-user 無しは拒否、明示時のみ単一化して適用
    # （レビュー P1: set-origin と同じ安全モデルに統一）。
    try:
        call_retry("apply-transform", {"targets": "ShA", "scale": True})
        raise AssertionError("shared-mesh apply should be blocked")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
        assert e.error.get("data", {}).get("category") == "PRECONDITION", e.error
    aps, _ = call_retry(
        "apply-transform", {"targets": "ShA", "scale": True, "make_single_user": True}
    )
    assert aps["data"]["name"] == "ShA", aps["data"]
    print("apply_shared_mesh_guard_ok")

    # 12) duplicate（M6 T6.2）: Cube を count=2 offset=(3,0,0) で複製。
    #     i 番目の複製は (i+1)*offset を world 空間で累積する。
    ci, _ = call_retry("object-info", {"targets": "Cube"})
    cube_x = ci["data"]["location"][0]
    dup, _ = call_retry("duplicate", {"targets": "Cube", "count": 2, "offset": [3.0, 0.0, 0.0]})
    created = dup["data"]["created"]
    assert len(created) == 2, dup["data"]
    assert dup["data"]["source"] == "Cube", dup["data"]
    assert "Cube" not in created, created  # 新規名（Cube.001 等）
    assert dup.get("fingerprint") and len(dup["fingerprint"]) == 16, dup
    for i, name in enumerate(created):
        di, _ = call_retry("object-info", {"targets": name})
        assert approx([di["data"]["location"][0]], [cube_x + 3.0 * (i + 1)]), (
            name,
            di["data"]["location"],
        )
    print("duplicate_ok created=", created, "base_x=", cube_x)

    # 13) duplicate linked（M6 T6.2）: data を共有 → mesh_users が +1（非linkedは独立で不変）。
    lci, _ = call_retry("object-info", {"targets": "Cube"})
    base_users = lci["data"]["mesh_users"]
    ld, _ = call_retry("duplicate", {"targets": "Cube", "linked": True, "count": 1})
    lname = ld["data"]["created"][0]
    loi, _ = call_retry("object-info", {"targets": lname})
    assert loi["data"]["mesh_users"] == base_users + 1, (base_users, loi["data"]["mesh_users"])
    print("duplicate_linked_ok", lname, "mesh_users", base_users, "->", loi["data"]["mesh_users"])

    # 14) duplicate 親付き（M6 T6.2）: 親付き Child の複製でも world offset が正しく累積する
    #     （レビュー P2: 複製直後の未評価 matrix_world でなく元 obj の評価済み行列を基準に）。
    pci, _ = call_retry("object-info", {"targets": "Child"})
    cwx, cwy, cwz = pci["data"]["location"]
    pd, _ = call_retry("duplicate", {"targets": "Child", "count": 1, "offset": [0.0, 0.0, 5.0]})
    pchild = pd["data"]["created"][0]
    pdi, _ = call_retry("object-info", {"targets": pchild})
    assert approx(pdi["data"]["location"], [cwx, cwy, cwz + 5.0]), (
        pchild,
        pdi["data"]["location"],
    )
    print("duplicate_parented_ok", pchild, pdi["data"]["location"])

    # 15) delete（M6 T6.2）: 複製した独立コピーを削除 → シーンから消え、backup が返る。
    victim = created[0]
    de, _ = call_retry("delete", {"targets": victim})
    assert de["data"]["deleted"] == victim, de["data"]
    assert de["data"]["backup"]["name"] == victim, de["data"]
    assert de["data"]["backup"]["type"] == "MESH", de["data"]
    assert de.get("fingerprint") and len(de["fingerprint"]) == 16, de
    # 消失は scene-info の名前集合で厳密に確認（regex フォールバック偽陽性を避ける。レビュー P3）。
    si_after, _ = call_retry("scene-info", {"depth": 1})
    after_names = {o["name"] for o in si_after["data"]["objects"]}
    assert victim not in after_names, after_names
    assert "Cube" in after_names, after_names  # 元 Cube は健在（delete は --targets のみ）
    print("delete_ok deleted=", victim)

    # 存在しない名の delete → E_TARGET_NOT_FOUND（USER_INPUT）。状態は汚さない。
    try:
        call_retry("delete", {"targets": "NoSuchObjToDelete"})
        raise AssertionError("delete of missing should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_TARGET_NOT_FOUND", e.error
        assert e.error.get("data", {}).get("category") == "USER_INPUT", e.error
    print("delete_missing_guard_ok")

    # 16) material（M6 T6.3 / Codex + 設計レビュー対応）: 共有 mesh ガード + slot.link 尊重 + Base Color。
    # Cube は step 13 の linked 複製（lname）と mesh を共有 → DATA slot への assign/create は
    # --make-single-user 無しで E_PRECONDITION（兄弟への波及を防ぐ。Codex P2-A）。
    red = [0.8, 0.1, 0.2, 1.0]
    try:
        call_retry(
            "material", {"action": "create", "targets": "Cube", "name": "SmRed", "color": red}
        )
        raise AssertionError("material on shared mesh should require make_single_user")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
        assert e.error.get("data", {}).get("category") == "PRECONDITION", e.error
    # ガードは create_material より前に走るため、失敗時にマテリアルを生成しない（orphan なし）。
    assert bpy.data.materials.get("SmRed") is None, "failed create should not leak a material"
    print("material_shared_guard_ok")

    # 既存マテリアルの assign も共有 mesh の DATA slot 書き込みは --make-single-user 必須。
    cl, _ = call_retry("material", {"action": "list", "targets": "Cube"})
    existing_mat = cl["data"]["materials"][0]["name"]  # 既定 Cube の "Material"（DATA slot）
    try:
        call_retry("material", {"action": "assign", "targets": "Cube", "name": existing_mat})
        raise AssertionError("assign on shared DATA slot should require make_single_user")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    print("material_assign_shared_guard_ok", existing_mat)

    # 存在しないマテリアルの assign + --make-single-user: 解決失敗時に mesh を分離しない
    # （Codex P2: side-effect before failure 回避。失敗後も mesh_users は不変）。
    cu, _ = call_retry("object-info", {"targets": "Cube"})
    assert cu["data"]["mesh_users"] == 2, cu["data"]  # linked 兄弟と共有
    try:
        call_retry(
            "material",
            {
                "action": "assign",
                "targets": "Cube",
                "name": "NoSuchMatXYZ",
                "make_single_user": True,
            },
        )
        raise AssertionError("assign of missing material should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_TARGET_NOT_FOUND", e.error
    cu2, _ = call_retry("object-info", {"targets": "Cube"})
    assert cu2["data"]["mesh_users"] == 2, cu2["data"]  # 失敗時に単一ユーザ化していない
    print("material_assign_missing_no_sideeffect_ok")

    sib, _ = call_retry("material", {"action": "list", "targets": lname})
    sib_before = [m["name"] for m in sib["data"]["materials"]]  # 波及していないこと確認用

    # --make-single-user 付きで create-and-assign → Cube を単一ユーザ化して付与。
    before, _ = call_retry("material", {"action": "list", "targets": "Cube"})
    n_before = len(before["data"]["materials"])
    mc, _ = call_retry(
        "material",
        {
            "action": "create",
            "targets": "Cube",
            "name": "SmRed",
            "color": red,
            "make_single_user": True,
        },
    )
    assert mc["data"]["action"] == "create", mc["data"]
    created_mat = mc["data"]["material"]  # "SmRed"（衝突時は自動採番）
    assert mc.get("fingerprint") and len(mc["fingerprint"]) == 16, mc
    ml, _ = call_retry("material", {"action": "list", "targets": "Cube"})
    expected_n = n_before if n_before >= 1 else 1  # active 置換でスロット数は不変
    assert len(ml["data"]["materials"]) == expected_n, (n_before, ml["data"]["materials"])
    slot_entry = ml["data"]["materials"][mc["data"]["slot"]]
    assert slot_entry["name"] == created_mat, (slot_entry, created_mat)
    assert slot_entry["link"] == "DATA", slot_entry  # 既定リンク（Codex P2-B: link を報告）
    assert len(slot_entry["base_color"]) == 4, slot_entry  # RGBA 4要素（version 退化検出）
    assert approx(slot_entry["base_color"], red), slot_entry
    print("material_create_ok", created_mat, "slot", mc["data"]["slot"], slot_entry["base_color"])

    # fingerprint は同一状態で決定的（drift 検証の前提）。同じ list を2回引いて一致を確認。
    fa, _ = call_retry("material", {"action": "list", "targets": "Cube"})
    fb, _ = call_retry("material", {"action": "list", "targets": "Cube"})
    assert fa["fingerprint"] == fb["fingerprint"], (fa["fingerprint"], fb["fingerprint"])
    print("material_fingerprint_deterministic_ok", fa["fingerprint"])

    # 単一ユーザ化したので linked 兄弟（lname）は波及していない（P2-A の核心）。
    sib2, _ = call_retry("material", {"action": "list", "targets": lname})
    sib_after = [m["name"] for m in sib2["data"]["materials"]]
    assert sib_after == sib_before, (sib_before, sib_after)
    assert created_mat not in sib_after, sib_after
    print("material_sibling_unaffected_ok", sib_before)

    # 空スロットの共有オブジェクト（ShA/ShB が ShMesh 共有）への create も --make-single-user 必須。
    sa, _ = call_retry(
        "material",
        {
            "action": "create",
            "targets": "ShA",
            "name": "ShGreen",
            "color": [0.0, 1.0, 0.0, 1.0],
            "make_single_user": True,
        },
    )
    assert sa["data"]["slot"] == 0, sa["data"]  # 空スロット → append で slot 0
    sal, _ = call_retry("material", {"action": "list", "targets": "ShA"})
    assert sal["data"]["materials"][0]["name"] == sa["data"]["material"], sal["data"]
    sbl, _ = call_retry("material", {"action": "list", "targets": "ShB"})
    assert sbl["data"]["materials"] == [], sbl["data"]  # ShB は ShMesh 保持で波及なし
    print("material_empty_append_ok", sa["data"]["material"], "ShB_unaffected")

    # Cube は単一ユーザ化済み → assign は make_single_user 不要。別 create→既存 SmRed に戻す。
    call_retry(
        "material",
        {"action": "create", "targets": "Cube", "name": "SmBlue", "color": [0.1, 0.2, 0.8, 1.0]},
    )
    ma, _ = call_retry("material", {"action": "assign", "targets": "Cube", "name": created_mat})
    assert ma["data"]["material"] == created_mat, ma["data"]
    active_slot = ma["data"]["slot"]
    ml2, _ = call_retry("material", {"action": "list", "targets": "Cube"})
    assert ml2["data"]["materials"][active_slot]["name"] == created_mat, ml2["data"]
    print("material_assign_ok", created_mat, "slot", active_slot)

    # OBJECT リンク slot への assign は共有 mesh を触らない → make_single_user 不要・分離なし
    # （Codex P2: OBJECT リンク slot は object 限定書き込みなので共有ガードを掛けない）。
    # OLnkA/OLnkB は OLnkMesh 共有・OLnkA の slot0 は OBJECT リンク。
    oa, _ = call_retry("material", {"action": "assign", "targets": "OLnkA", "name": "OLnkNew"})
    assert oa["data"]["material"] == "OLnkNew", oa["data"]
    ola, _ = call_retry("material", {"action": "list", "targets": "OLnkA"})
    assert ola["data"]["materials"][0]["name"] == "OLnkNew", ola["data"]
    assert ola["data"]["materials"][0]["link"] == "OBJECT", ola["data"]
    olb, _ = call_retry("material", {"action": "list", "targets": "OLnkB"})
    assert olb["data"]["materials"][0]["name"] == "OLnkBase", olb["data"]  # DATA slot は波及なし
    oio, _ = call_retry("object-info", {"targets": "OLnkA"})
    assert oio["data"]["mesh_users"] == 2, oio["data"]  # 共有のまま（分離していない）
    print("material_object_linked_no_guard_ok")

    # 存在しないマテリアルの assign → E_TARGET_NOT_FOUND（USER_INPUT）。
    try:
        call_retry("material", {"action": "assign", "targets": "Cube", "name": "NoSuchMat"})
        raise AssertionError("assign missing material should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_TARGET_NOT_FOUND", e.error
        assert e.error.get("data", {}).get("category") == "USER_INPUT", e.error
    print("material_assign_missing_ok")

    # 非対応型（QRot=EMPTY）への material → E_PRECONDITION。
    try:
        call_retry("material", {"action": "list", "targets": "QRot"})
        raise AssertionError("material on empty should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    print("material_nonmesh_guard_ok")

    # --color を assign で渡す → INVALID_PARAMS（create 専用・silent ignore しない）。
    try:
        call_retry(
            "material",
            {"action": "assign", "targets": "Cube", "name": created_mat, "color": red},
        )
        raise AssertionError("color on assign should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "INVALID_PARAMS", e.error
    print("material_color_on_assign_ok")

    # 17) modifier（M6 T6.4）: add（5種）→ list → remove → apply（共有ガード）。
    # Cube は material 段で単一ユーザ化済み（mesh_users=1）。add/remove/list はオブジェクト単位で
    # 共有ガード不要。BOOLEAN の相手は ShB（参照のみ・apply はしない）。
    ci, _ = call_retry("object-info", {"targets": "Cube"})
    assert ci["data"]["mesh_users"] == 1, ci["data"]  # material 段で単一ユーザ化済み
    call_retry("modifier", {"action": "add", "targets": "Cube", "type": "MIRROR", "axis": "X"})
    call_retry("modifier", {"action": "add", "targets": "Cube", "type": "SUBSURF", "levels": 2})
    call_retry(
        "modifier", {"action": "add", "targets": "Cube", "type": "SOLIDIFY", "thickness": 0.1}
    )
    call_retry("modifier", {"action": "add", "targets": "Cube", "type": "DECIMATE", "ratio": 0.5})
    ab, _ = call_retry(
        "modifier",
        {
            "action": "add",
            "targets": "Cube",
            "type": "BOOLEAN",
            "operation": "DIFFERENCE",
            "with_object": "ShB",
        },
    )
    assert ab["data"]["modifier"]["type"] == "BOOLEAN", ab["data"]
    assert ab["data"]["modifier"]["object"] == "ShB", ab["data"]
    lm, _ = call_retry("modifier", {"action": "list", "targets": "Cube"})
    types = [m["type"] for m in lm["data"]["modifiers"]]
    assert types == ["MIRROR", "SUBSURF", "SOLIDIFY", "DECIMATE", "BOOLEAN"], types
    print("modifier_add_list_ok", types)

    # remove（SUBSURF を名前で削除）
    sub_name = next(m["name"] for m in lm["data"]["modifiers"] if m["type"] == "SUBSURF")
    call_retry("modifier", {"action": "remove", "targets": "Cube", "name": sub_name})
    lm2, _ = call_retry("modifier", {"action": "list", "targets": "Cube"})
    assert sub_name not in [m["name"] for m in lm2["data"]["modifiers"]], lm2["data"]
    print("modifier_remove_ok", sub_name)

    # remove 存在しない名 → E_TARGET_NOT_FOUND（USER_INPUT）
    try:
        call_retry("modifier", {"action": "remove", "targets": "Cube", "name": "NoSuchMod"})
        raise AssertionError("remove missing modifier should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_TARGET_NOT_FOUND", e.error
        assert e.error.get("data", {}).get("category") == "USER_INPUT", e.error
    print("modifier_remove_missing_ok")

    # apply（先頭 MIRROR を Cube=単一ユーザに焼き込み・ガード不要）→ スタックから消え、
    # かつ **mesh が実際に変わる**（MIRROR で頂点が増える＝remove と区別できる）。
    vbefore = call_retry("object-info", {"targets": "Cube"})[0]["data"]["vertices"]
    mir_name = next(m["name"] for m in lm2["data"]["modifiers"] if m["type"] == "MIRROR")
    ap, _ = call_retry("modifier", {"action": "apply", "targets": "Cube", "name": mir_name})
    assert mir_name not in [m["name"] for m in ap["data"]["modifiers"]], ap["data"]
    vafter = call_retry("object-info", {"targets": "Cube"})[0]["data"]["vertices"]
    assert vafter > vbefore, (vbefore, vafter)  # MIRROR を焼き込んだので頂点が増える
    print("modifier_apply_ok", mir_name, "verts", vbefore, "->", vafter)

    # 非対応型（QRot=EMPTY）への add → E_PRECONDITION（INTERNAL にしない）。
    try:
        call_retry("modifier", {"action": "add", "targets": "QRot", "type": "MIRROR", "axis": "X"})
        raise AssertionError("modifier on non-mesh should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    print("modifier_nonmesh_guard_ok")

    # BOOLEAN の相手が非mesh（QRot）/ 自分自身 → USER_INPUT（INTERNAL にしない）。
    for bad_with in ("QRot", "Cube"):
        try:
            call_retry(
                "modifier",
                {
                    "action": "add",
                    "targets": "Cube",
                    "type": "BOOLEAN",
                    "operation": "DIFFERENCE",
                    "with_object": bad_with,
                },
            )
            raise AssertionError("bad boolean operand should error")
        except client.RpcRemoteError as e:
            assert e.error.get("message") == "INVALID_PARAMS", (bad_with, e.error)
    print("modifier_boolean_operand_guard_ok")

    # apply 共有ガード: OLnkA/OLnkB は OLnkMesh 共有（mesh_users=2）。MIRROR を add して
    # apply を --make-single-user 無しで実行 → E_PRECONDITION（apply-transform と同じ）。
    call_retry("modifier", {"action": "add", "targets": "OLnkA", "type": "MIRROR", "axis": "X"})
    olm, _ = call_retry("modifier", {"action": "list", "targets": "OLnkA"})
    olmir = next(m["name"] for m in olm["data"]["modifiers"] if m["type"] == "MIRROR")
    try:
        call_retry("modifier", {"action": "apply", "targets": "OLnkA", "name": olmir})
        raise AssertionError("apply on shared mesh should require make_single_user")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    oa, _ = call_retry("object-info", {"targets": "OLnkA"})
    assert oa["data"]["mesh_users"] == 2, oa["data"]  # 失敗時に単一ユーザ化していない
    # --make-single-user 付きで apply → 成功し OLnkB は波及しない。
    call_retry(
        "modifier",
        {"action": "apply", "targets": "OLnkA", "name": olmir, "make_single_user": True},
    )
    oa2, _ = call_retry("object-info", {"targets": "OLnkA"})
    assert oa2["data"]["mesh_users"] == 1, oa2["data"]  # 単一ユーザ化された
    print("modifier_apply_guard_ok")

    # 18) mesh（M7 T7.1）: recalc-normals（flipped 統計・法線込み fingerprint）/ merge / ガード。
    # recalc: MeshFlip は1面が不整合 → outward recalc で flipped=1（一貫化）。
    mr, _ = call_retry("mesh", {"op": "recalc-normals", "targets": "MeshFlip"})
    assert mr["data"]["op"] == "recalc-normals", mr["data"]
    assert mr["data"]["faces"] == 6, mr["data"]
    assert mr["data"]["flipped"] == 1, mr["data"]  # 1 面だけ反転していた
    fp_clean = mr["fingerprint"]
    # もう一度 recalc → 既に一貫しているので flipped=0・fingerprint 不変（決定的）。
    mr2, _ = call_retry("mesh", {"op": "recalc-normals", "targets": "MeshFlip"})
    assert mr2["data"]["flipped"] == 0, mr2["data"]
    assert mr2["fingerprint"] == fp_clean, (mr2["fingerprint"], fp_clean)
    # inside=True → 全面反転（flipped=6）。法線が変わるので頂点数不変でも fingerprint が変わる
    # （mesh_fingerprint は法線込み＝object_fingerprint では検出できない recalc を検出する）。
    mr3, _ = call_retry("mesh", {"op": "recalc-normals", "targets": "MeshFlip", "inside": True})
    assert mr3["data"]["flipped"] == 6, mr3["data"]
    assert mr3["data"]["inside"] is True, mr3["data"]
    assert mr3["fingerprint"] != fp_clean, mr3["fingerprint"]
    print("mesh_recalc_ok clean_fp=", fp_clean, "inside_fp=", mr3["fingerprint"])

    # merge-by-distance: MeshDbl は重複頂点1つ（9頂点）→ merged=1（9→8）。
    mm, _ = call_retry("mesh", {"op": "merge-by-distance", "targets": "MeshDbl"})
    assert mm["data"]["op"] == "merge-by-distance", mm["data"]
    assert mm["data"]["before"] == 9 and mm["data"]["after"] == 8, mm["data"]
    assert mm["data"]["merged"] == 1, mm["data"]
    assert mm["data"]["stats"]["vertices"] == 8, mm["data"]["stats"]
    print(
        "mesh_merge_ok merged=",
        mm["data"]["merged"],
        "verts",
        mm["data"]["before"],
        "->",
        mm["data"]["after"],
    )
    # 明示 --distance が remove_doubles(dist=...) に届くことを確認: MeshDbl は今 clean cube（8頂点）。
    # 大きい distance で離れた頂点も collapse する（既定 0.0001 では起きない＝param が効いている）。
    mm2, _ = call_retry("mesh", {"op": "merge-by-distance", "targets": "MeshDbl", "distance": 3.0})
    assert mm2["data"]["distance"] == 3.0, mm2["data"]
    assert mm2["data"]["after"] < 8, mm2["data"]  # 大距離で頂点が大きく減る（param 反映）
    print("mesh_merge_distance_param_ok after=", mm2["data"]["after"])

    # 非 mesh 型（QRot=EMPTY）→ E_PRECONDITION（INTERNAL にしない・modifier/material と同様）。
    try:
        call_retry("mesh", {"op": "recalc-normals", "targets": "QRot"})
        raise AssertionError("mesh edit on non-mesh should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    print("mesh_nonmesh_guard_ok")

    # 共有 mesh ガード: MeshShA/MeshShB は同一 mesh 共有（mesh_users=2）。破壊的 mesh 編集は
    # --make-single-user 無しで E_PRECONDITION。失敗時に単一ユーザ化しない。
    try:
        call_retry("mesh", {"op": "merge-by-distance", "targets": "MeshShA"})
        raise AssertionError("mesh edit on shared mesh should require make_single_user")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    msa0, _ = call_retry("object-info", {"targets": "MeshShA"})
    assert msa0["data"]["mesh_users"] == 2, msa0["data"]  # 失敗時に単一ユーザ化していない
    # --make-single-user 付き → 成功し単一ユーザ化（MeshShB は元 mesh を保持＝波及せず）。
    call_retry("mesh", {"op": "recalc-normals", "targets": "MeshShA", "make_single_user": True})
    msa, _ = call_retry("object-info", {"targets": "MeshShA"})
    assert msa["data"]["mesh_users"] == 1, msa["data"]  # 単一ユーザ化された
    print("mesh_shared_guard_ok")

    # 19) mesh（M7 T7.2）: extrude / bevel / inset の exact count golden（clean cube・spike 一致）。
    # extrude: 先に recalc で clean cube の mesh_fingerprint を取り、extrude で変わることを確認。
    fp0, _ = call_retry("mesh", {"op": "recalc-normals", "targets": "MeshExtrude"})
    ex, _ = call_retry(
        "mesh", {"op": "extrude", "targets": "MeshExtrude", "offset": [0.0, 0.0, 1.0]}
    )
    assert ex["data"]["op"] == "extrude", ex["data"]
    assert ex["data"]["stats"] == {"vertices": 16, "edges": 24, "polygons": 12}, ex["data"]["stats"]
    assert ex["data"]["delta"]["vertices"] == 8, ex["data"]["delta"]
    assert ex["fingerprint"] != fp0["fingerprint"], (ex["fingerprint"], fp0["fingerprint"])
    # world 空間 offset 検証: MeshExtrude は scale=2。world (0,0,1) 押し出し → 新シェル top の
    # world z = 3（local 空間誤実装なら 4 になる）。object-info の world bbox で確認。
    exi, _ = call_retry("object-info", {"targets": "MeshExtrude"})
    assert approx([exi["data"]["bbox"]["max"][2]], [3.0], tol=1e-3), exi["data"]["bbox"]
    print("mesh_extrude_ok", ex["data"]["stats"], "world_max_z=", exi["data"]["bbox"]["max"][2])

    bv, _ = call_retry("mesh", {"op": "bevel", "targets": "MeshBevel", "width": 0.2})
    assert bv["data"]["op"] == "bevel" and bv["data"]["segments"] == 1, bv["data"]
    assert bv["data"]["stats"] == {"vertices": 24, "edges": 48, "polygons": 26}, bv["data"]["stats"]
    assert bv["data"]["delta"]["vertices"] == 16, bv["data"]["delta"]
    print("mesh_bevel_ok", bv["data"]["stats"])

    ins, _ = call_retry("mesh", {"op": "inset", "targets": "MeshInset", "thickness": 0.2})
    assert ins["data"]["op"] == "inset", ins["data"]
    assert ins["data"]["stats"] == {"vertices": 32, "edges": 60, "polygons": 30}, ins["data"][
        "stats"
    ]
    assert ins["data"]["delta"]["vertices"] == 24, ins["data"]["delta"]
    print("mesh_inset_ok", ins["data"]["stats"])

    # op 別必須/範囲ガード（実機でも USER_INPUT で弾けること）。
    for bad in (
        {"op": "extrude", "targets": "MeshExtrude"},  # offset 欠落
        {"op": "bevel", "targets": "MeshBevel"},  # width 欠落
        {"op": "bevel", "targets": "MeshBevel", "width": 0.1, "segments": 1000},  # segments 過大
        {"op": "inset", "targets": "MeshInset"},  # thickness 欠落
        {"op": "inset", "targets": "MeshInset", "thickness": -1.0},  # 負の厚み
    ):
        try:
            call_retry("mesh", bad)
            raise AssertionError(f"expected USER_INPUT for {bad}")
        except client.RpcRemoteError as e:
            assert e.error.get("message") == "INVALID_PARAMS", (bad, e.error)
            assert e.error.get("data", {}).get("category") == "USER_INPUT", (bad, e.error)
    print("mesh_t72_guard_ok")

    # 20) mesh（M7 T7.3）: boolean（modifier 経由・world bbox 幾何 golden）/ decimate（削減）。
    # 結果キーは入力と対称な `with_object`（入力 --with → with_object・出力も with_object）。
    # INTERSECT: A[-1,1]^3 ∩ B[0,2]（x方向） → world x[0,1]・y/z は [-1,1] 不変（solver 非依存）。
    bi, _ = call_retry(
        "mesh",
        {
            "op": "boolean",
            "targets": "MeshBoolA",
            "operation": "INTERSECT",
            "with_object": "MeshBoolB",
        },
    )
    assert bi["data"]["op"] == "boolean", bi["data"]
    assert bi["data"]["operation"] == "INTERSECT", bi["data"]
    assert bi["data"]["with_object"] == "MeshBoolB", bi["data"]
    bia, _ = call_retry("object-info", {"targets": "MeshBoolA"})
    assert approx(bia["data"]["bbox"]["min"], [0.0, -1.0, -1.0], tol=1e-3), bia["data"]["bbox"]
    assert approx(bia["data"]["bbox"]["max"], [1.0, 1.0, 1.0], tol=1e-3), bia["data"]["bbox"]
    print("mesh_boolean_intersect_ok bbox=", bia["data"]["bbox"])

    # DIFFERENCE: A[-1,1] - B[0,2] → world x[-1,0]（operation param が実際に効くことの確認）。
    bd, _ = call_retry(
        "mesh",
        {
            "op": "boolean",
            "targets": "MeshBoolC",
            "operation": "DIFFERENCE",
            "with_object": "MeshBoolB",
        },
    )
    assert bd["data"]["operation"] == "DIFFERENCE", bd["data"]
    bdc, _ = call_retry("object-info", {"targets": "MeshBoolC"})
    assert approx(bdc["data"]["bbox"]["min"], [-1.0, -1.0, -1.0], tol=1e-3), bdc["data"]["bbox"]
    assert approx(bdc["data"]["bbox"]["max"], [0.0, 1.0, 1.0], tol=1e-3), bdc["data"]["bbox"]
    # MeshBoolB は operand（read-only）→ 不変のまま（焼き込まれていない）。
    bob, _ = call_retry("object-info", {"targets": "MeshBoolB"})
    assert approx(bob["data"]["bbox"]["max"], [2.0, 1.0, 1.0], tol=1e-3), bob["data"]["bbox"]
    print("mesh_boolean_difference_ok bbox=", bdc["data"]["bbox"])

    # UNION: A[-1,1] ∪ B[0,2] → world x[-1,2]（縮小方向の INTERSECT/DIFFERENCE と逆＝拡大方向）。
    bu, _ = call_retry(
        "mesh",
        {"op": "boolean", "targets": "MeshBoolD", "operation": "UNION", "with_object": "MeshBoolB"},
    )
    assert bu["data"]["operation"] == "UNION", bu["data"]
    assert bu["data"]["delta"]["vertices"] == 8, bu["data"]["delta"]  # 8→16（research §E3）
    bud, _ = call_retry("object-info", {"targets": "MeshBoolD"})
    assert approx(bud["data"]["bbox"]["min"], [-1.0, -1.0, -1.0], tol=1e-3), bud["data"]["bbox"]
    assert approx(bud["data"]["bbox"]["max"], [2.0, 1.0, 1.0], tol=1e-3), bud["data"]["bbox"]
    print("mesh_boolean_union_ok bbox=", bud["data"]["bbox"])

    # decimate: ico_sphere(subdiv=2) は 80f → ratio=0.5 で 40f（両版一致・delta 負）。
    dc, _ = call_retry("mesh", {"op": "decimate", "targets": "MeshDecimate", "ratio": 0.5})
    assert dc["data"]["op"] == "decimate" and dc["data"]["ratio"] == 0.5, dc["data"]
    assert dc["data"]["stats"]["polygons"] == 40, dc["data"]["stats"]
    assert dc["data"]["delta"]["polygons"] == -40, dc["data"]["delta"]  # 符号付き削減
    print("mesh_decimate_ok", dc["data"]["stats"], "delta_f=", dc["data"]["delta"]["polygons"])

    # decimate ratio=1.0 は無削減（delta 0）だが modifier_apply は mesh を焼き直す（破壊的書き込み）。
    dc1, _ = call_retry("mesh", {"op": "decimate", "targets": "MeshDecimate1", "ratio": 1.0})
    assert dc1["data"]["delta"] == {"vertices": 0, "edges": 0, "polygons": 0}, dc1["data"]["delta"]
    print("mesh_decimate_noop_ok delta=", dc1["data"]["delta"])

    # boolean の相手検証（実機でも USER_INPUT）: 自己参照 / 非mesh 相手 / 存在しない相手。
    for bad, want in (
        (
            {
                "op": "boolean",
                "targets": "MeshBoolB",
                "operation": "UNION",
                "with_object": "MeshBoolB",
            },
            "INVALID_PARAMS",
        ),
        (
            {"op": "boolean", "targets": "MeshBoolB", "operation": "UNION", "with_object": "QRot"},
            "INVALID_PARAMS",
        ),
        (
            {
                "op": "boolean",
                "targets": "MeshBoolB",
                "operation": "UNION",
                "with_object": "NoSuchObj",
            },
            "E_TARGET_NOT_FOUND",
        ),
    ):
        try:
            call_retry("mesh", bad)
            raise AssertionError(f"expected {want} for {bad}")
        except client.RpcRemoteError as e:
            assert e.error.get("message") == want, (bad, e.error)
            assert e.error.get("data", {}).get("category") == "USER_INPUT", (bad, e.error)

    # 共有 mesh ガード（boolean/decimate も modifier_apply 前に効く＝多ユーザ mesh は焼けない）。
    # MeshShC/MeshShD は同一 mesh 共有（mesh_users=2）。--make-single-user 無しは E_PRECONDITION。
    try:
        call_retry("mesh", {"op": "decimate", "targets": "MeshShC", "ratio": 0.5})
        raise AssertionError("decimate on shared mesh should require make_single_user")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    shc0, _ = call_retry("object-info", {"targets": "MeshShC"})
    assert shc0["data"]["mesh_users"] == 2, shc0["data"]  # 失敗時に単一ユーザ化していない
    # --make-single-user 付き → 成功し単一ユーザ化（MeshShD は元 mesh を保持＝波及せず）。
    call_retry(
        "mesh", {"op": "decimate", "targets": "MeshShC", "ratio": 0.5, "make_single_user": True}
    )
    shc1, _ = call_retry("object-info", {"targets": "MeshShC"})
    assert shc1["data"]["mesh_users"] == 1, shc1["data"]  # 単一ユーザ化された
    shd, _ = call_retry("object-info", {"targets": "MeshShD"})
    assert shd["data"]["polygons"] == 6, shd["data"]  # operand 共有元は不変（cube のまま）
    print("mesh_t73_guard_ok")


def main():
    print("=== BLI_OPS_SMOKE_BEGIN ===")
    print("python", sys.version.split()[0], "blender", bpy.app.version_string)
    ensure_cube()
    ensure_quaternion_empty()  # 非 Euler 回転モード検証用（メインスレッドで生成）
    ensure_parented()  # world 空間 transform 検証用（メインスレッドで生成）
    ensure_shared_mesh()  # 共有 mesh ガード検証用（メインスレッドで生成）
    ensure_object_linked_shared()  # OBJECT リンク slot ガードスキップ検証用（Codex P2）
    ensure_mesh_fixtures()  # M7 mesh 編集（flip/double/共有）検証用（メインスレッドで生成）

    dispatcher = Dispatcher()  # background では timer を使わず手動 pump

    def executor(method, params, info, settle):
        return dispatcher.submit(
            lambda: ops.dispatch(method, params, info),
            timeout=runtime.DISPATCH_TIMEOUT,
            settle=settle,
        )

    srv_mod.start(
        blender_version=bpy.app.version_string,
        schema_hash=schema_hash(load_definitions()),
        capabilities=CapabilityRegistry().list_capabilities(),
        host="127.0.0.1",
        port=0,
        handler=executor,
    )

    state = {}

    def worker():
        try:
            run_calls()
            state["ok"] = True
        except BaseException as e:  # スモーク: 全例外を回収して報告する
            state["error"] = "".join(traceback.format_exception(type(e), e, e.__traceback__))

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    deadline = time.time() + 30.0
    while t.is_alive() and time.time() < deadline:
        dispatcher.pump()
        time.sleep(0.005)
    dispatcher.pump()  # 最後の job を drain
    t.join(timeout=2.0)

    srv_mod.stop()

    if state.get("ok"):
        print("OPS SMOKE OK")
    else:
        print("OPS SMOKE FAIL")
        print(state.get("error", "worker did not finish in time"))
    print("=== BLI_OPS_SMOKE_END ===")


if __name__ == "__main__":
    main()
