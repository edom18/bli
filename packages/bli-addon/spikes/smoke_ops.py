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
    # ShA/ShB は共有mesh検証用 / OLnkA/OLnkB は OBJECT リンク slot 検証用（いずれも MESH）。
    assert set(lo_names) == {"Cube", "ShA", "ShB", "OLnkA", "OLnkB"}, lo_names
    assert lo["data"]["count"] == 5, lo["data"]
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

    # 16) material（M6 T6.3 / Codex P2 対応）: 共有 mesh ガード + slot.link 尊重 + Base Color 往復。
    # Cube は step 13 の linked 複製（Cube.003）と mesh を共有 → assign/create は
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
    print("material_shared_guard_ok")

    # 存在しないマテリアルの assign + --make-single-user: 解決失敗時に mesh を分離しない
    # （Codex P2: side-effect before failure 回避。失敗後も mesh_users は不変）。
    cu, _ = call_retry("object-info", {"targets": "Cube"})
    assert cu["data"]["mesh_users"] == 2, cu["data"]  # Cube.003 と共有
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

    sib, _ = call_retry("material", {"action": "list", "targets": "Cube.003"})
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

    # 単一ユーザ化したので linked 兄弟 Cube.003 は波及していない（P2-A の核心）。
    sib2, _ = call_retry("material", {"action": "list", "targets": "Cube.003"})
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


def main():
    print("=== BLI_OPS_SMOKE_BEGIN ===")
    print("python", sys.version.split()[0], "blender", bpy.app.version_string)
    ensure_cube()
    ensure_quaternion_empty()  # 非 Euler 回転モード検証用（メインスレッドで生成）
    ensure_parented()  # world 空間 transform 検証用（メインスレッドで生成）
    ensure_shared_mesh()  # 共有 mesh ガード検証用（メインスレッドで生成）
    ensure_object_linked_shared()  # OBJECT リンク slot ガードスキップ検証用（Codex P2）

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
