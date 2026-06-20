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
  → straighten (M8 T8.2)            : world-align（localZ→+Z・回転除去）/ auto 最近軸 / reset /
                                       pca（主成分→+Z）/ floor（接地 z→0）/ bake（焼き込み・見た目不変）/
                                       共有 mesh は bake のみガード（非 bake は安全）/ 前提ガード
  → straighten 実地FB (M8 fb #5/#2)  : pca up_hint=current で上下反転防止（auto は重心で下向き誤判定）/
                                       tilt_from_up_deg / dry-run 非破壊（前後不変・計画=実適用）
  → straighten 基準指定 (M8 fb #4)    : angle（world 軸×角度）/ align-vector（from→to・to 省略=up）/
                                       reference（参照 obj の軸方向へ・world up と区別）/ bake / 自己参照ガード
  → print-setup (M8 T8.3)           : 表示単位 mm/m（geometry 非破壊・dims 不変）/ 冪等 changed /
                                       scene-info 反映 / --scene 解決 / 存在しないシーンガード
  → print-check/repair (M8 T8.4)    : bmesh 自前 manifold/normals/degenerate（clean/面欠け/反転/退化）/
                                       thin/intersect は CAPABILITY_UNAVAILABLE / 非mesh ガード /
                                       repair make-manifold/recalc/remove-degenerate / 共有ガード
  → undo/redo 実地FB (M8 fb #3)      : --background は E_PRECONDITION 縮退 / steps 範囲外は INVALID_PARAMS
                                       （実巻き戻しの GUI 検証は undo_spike.py・研究 §E7）
"""

import math
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
from bli_addon import audit, ops, policy, render_state, watchdog  # noqa: E402
from bli_addon import server as srv_mod  # noqa: E402
from bli_addon.capability import CapabilityRegistry  # noqa: E402
from bli_addon.dispatcher import Dispatcher  # noqa: E402
from bli_core import runtime  # noqa: E402
from bli_core.commands import load_definitions  # noqa: E402
from bli_core.schema import schema_hash  # noqa: E402


def approx(a, b, tol=1e-4):
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=False))


def stl_binary_bbox(path):
    """binary STL の全頂点から world AABB (min, max) を読む（print-export の world 焼き/scale 検証用）。"""
    import struct

    with open(path, "rb") as f:
        f.read(80)  # header
        (ntri,) = struct.unpack("<I", f.read(4))
        xs, ys, zs = [], [], []
        for _ in range(ntri):
            f.read(12)  # 面法線
            for _v in range(3):
                x, y, z = struct.unpack("<fff", f.read(12))
                xs.append(x)
                ys.append(y)
                zs.append(z)
            f.read(2)  # attribute byte count
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def obj_text_bbox(path):
    """OBJ テキストの `v` 行から world AABB (min,max) と頂点数を読む（export の world 焼き/選択検証用）。

    export は world 焼き（matrix_world 適用）するため `v` 行は world 座標。worker スレッドから
    bpy.ops を呼べない（dispatch はメイン直列）ため、再 import せずファイルを直接パースする。
    """
    xs, ys, zs = [], [], []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                _, sx, sy, sz = line.split()[:4]
                xs.append(float(sx))
                ys.append(float(sy))
                zs.append(float(sz))
    if not xs:
        return None, None, 0
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)), len(xs)


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


def ensure_straighten_fixtures():
    """M8 straighten（直立補正）の検証用 MESH を用意（メインスレッドで生成・回転を直接設定）。

    - StrAlign: cube を X 周り 30° tilt（world-align axis=Z で localZ→+Z・回転除去）。
    - StrAuto : cube を小さく tilt（X15/Y10）（world-align auto で最近軸=+Z を自動選択）。
    - StrReset: cube を 30/20/10° 回転（reset で回転クリア）。
    - StrPCA  : Z 方向に細長く +Z 端へ重心を偏らせた rod を tilt（pca で主成分→+Z）。
    - StrFloor: cube を z=5 に浮かせる（floor で最下点 z→0 へ接地）。
    - StrBake : cube を Z 周り 45° yaw（world-align axis=Z は no-op→bake で mesh へ焼き込み・
                回転 0 化しても world bbox=見た目は不変）。
    - StrFloorY: cube を y=5 に浮かせる（floor --up-axis +Y で最下点 y→0・up≠+Z の一般性）。
    - StrAlignY: 無回転 cube（world-align axis=Z --up-axis +Y で localZ→+Y・up≠+Z の一般性）。
    - StrShA/StrShB: 同一 cube mesh を共有（StrShA は 30° yaw）。bake の共有 mesh ガード検証用。
    - StrAngle/StrAngleBake/StrAlignVec/StrRef/StrRefGuide: 基準指定 method（#4）検証用。
    いずれも MESH なので list-objects(MESH) golden に加える。
    """

    def _fresh_cube_obj(name):
        o = bpy.data.objects.get(name)
        if o is not None:
            return o
        bpy.ops.mesh.primitive_cube_add(size=2.0)  # spike のみ（AST guard 対象外）
        o = bpy.context.active_object
        o.name = name
        return o

    align = _fresh_cube_obj("StrAlign")
    align.rotation_euler = (math.radians(30), 0.0, 0.0)
    auto = _fresh_cube_obj("StrAuto")
    auto.rotation_euler = (math.radians(15), math.radians(10), 0.0)
    reset = _fresh_cube_obj("StrReset")
    reset.rotation_euler = (math.radians(30), math.radians(20), math.radians(10))
    floor = _fresh_cube_obj("StrFloor")
    floor.location = (0.0, 0.0, 5.0)
    bake = _fresh_cube_obj("StrBake")
    bake.rotation_euler = (0.0, 0.0, math.radians(45))  # yaw（local Z は up のまま）
    floor_y = _fresh_cube_obj("StrFloorY")
    floor_y.location = (0.0, 5.0, 0.0)  # +Y 方向に浮かせる（floor --up-axis +Y）
    _fresh_cube_obj("StrAlignY")  # 無回転（world-align axis=Z --up-axis +Y で localZ→+Y）

    # StrPCA: Z に細長い rod（+Z 端に重複頂点で重心を偏らせる）を tilt して主成分復元を検証。
    if "StrPCA" not in bpy.data.objects:
        coords = []
        for z in (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 3.0, 3.0):  # +Z 端で重心を偏らせる
            coords.append((0.2, 0.0, z))
            coords.append((-0.2, 0.0, z))
            coords.append((0.0, 0.2, z))
        me = bpy.data.meshes.new("StrPCAMesh")
        me.from_pydata(coords, [], [])
        me.update()
        pca = bpy.data.objects.new("StrPCA", me)
        bpy.context.scene.collection.objects.link(pca)
        pca.rotation_euler = (math.radians(40), math.radians(15), 0.0)

    # StrPCADown: Z に細長い rod（-Z 端へ重心を偏らせる＝ベースが重いスキャン物体を模す）を
    # X 周り 20° tilt。auto は重心(下)寄りで principal を下向きに誤判定→反転、up_hint=current は
    # up 寄りで上向きに選び反転しない（実地フィードバック #5 の再現）。傾きは 20°（tilt golden）。
    if "StrPCADown" not in bpy.data.objects:
        coords = []
        for z in (-3.0, -3.0, -3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0):  # -Z 端で重心を偏らせる
            coords.append((0.2, 0.0, z))
            coords.append((-0.2, 0.0, z))
            coords.append((0.0, 0.2, z))
        me = bpy.data.meshes.new("StrPCADownMesh")
        me.from_pydata(coords, [], [])
        me.update()
        down = bpy.data.objects.new("StrPCADown", me)
        bpy.context.scene.collection.objects.link(down)
        down.rotation_euler = (math.radians(20), 0.0, 0.0)

    # StrShA/StrShB: 同一 cube mesh を共有（bake 共有ガード検証・mesh_users=2）。
    sha = _fresh_cube_obj("StrShA")
    sha.rotation_euler = (0.0, 0.0, math.radians(30))  # yaw（world-align axis=Z は no-op）
    if "StrShB" not in bpy.data.objects:
        shb = bpy.data.objects.new("StrShB", sha.data)  # 同一 mesh を共有
        bpy.context.scene.collection.objects.link(shb)

    # StrQuat: QUATERNION モードの cube（X 周り 45°）。dry-run が euler 以外の回転表現も厳密に
    # 復元すること（_restore_transform は全 3 表現を退避）を検証する。
    quat = _fresh_cube_obj("StrQuat")
    quat.rotation_mode = "QUATERNION"
    quat.rotation_quaternion = (0.9238795, 0.3826834, 0.0, 0.0)  # X 周り 45°

    # ---- 実地FB #4 基準指定 method（angle / align-vector / reference）の検証用 ----
    # StrAngle    : 無回転 cube（angle --axis Z --degrees 45 → 回転 [0,0,45]・world bbox≈1.414）。
    # StrAngleBake: 無回転 cube（angle Z 45 + bake → object 回転 0・mesh へ焼き込み world bbox≈1.414）。
    # StrAlignVec : 無回転 cube（align-vector --from 0,sin20,cos20 → +Z へ・angle_deg≈20）。
    # StrRefGuide : Y 周り 25° tilt の cube（参照側・+Z 軸の world 方向 ≈ [sin25,0,cos25]）。
    # StrRef      : X 周り 30° tilt の cube（reference で StrRefGuide の +Z 方向へ整列）。
    _fresh_cube_obj("StrAngle")  # 無回転
    _fresh_cube_obj("StrAngleBake")  # 無回転
    _fresh_cube_obj("StrAlignVec")  # 無回転
    guide = _fresh_cube_obj("StrRefGuide")
    guide.rotation_euler = (0.0, math.radians(25), 0.0)  # +Z 軸を world で [sin25,0,cos25] へ傾ける
    ref = _fresh_cube_obj("StrRef")
    ref.rotation_euler = (math.radians(30), 0.0, 0.0)  # X 周り 30° tilt


def ensure_print_check_fixtures():
    """M8 print-check/repair の検証用 MESH を用意（メインスレッドで生成・破損 mesh 含む・§E6）。

    - PCClean: clean cube（is_printable True・全カテゴリ 0）。
    - PCOpen : 1面削除 cube（非多様体 boundary edge=4）→ check 後 make-manifold で穴埋め。
    - PCFlip : 1面反転 cube（反転法線 non_contiguous=4）→ check 後 recalc-normals で一貫化。
    - PCDegen: 退化三角形（面積0・degenerate_faces=1）→ check 後 remove-degenerate で除去。
    - PCShA/PCShB: 同一 cube mesh を共有（print-repair 共有ガード検証・mesh_users=2）。
    いずれも MESH なので list-objects(MESH) golden に加える。
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

    _fresh_cube_obj("PCClean")

    # PCOpen: 1面削除（boundary/non-manifold edge を作る）
    pcopen = _fresh_cube_obj("PCOpen")
    if len(pcopen.data.polygons) == 6:
        bm = _bmesh.new()
        bm.from_mesh(pcopen.data)
        bm.faces.ensure_lookup_table()
        _bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY")
        bm.to_mesh(pcopen.data)
        bm.free()
        pcopen.data.update()

    # PCFlip: 1面の巻き順反転（法線不整合）
    pcflip = _fresh_cube_obj("PCFlip")
    if len(pcflip.data.polygons) == 6:
        bm = _bmesh.new()
        bm.from_mesh(pcflip.data)
        bm.faces.ensure_lookup_table()
        _bmesh.ops.reverse_faces(bm, faces=[bm.faces[0]])
        bm.to_mesh(pcflip.data)
        bm.free()
        pcflip.data.update()

    # PCDegen: 退化三角形（2頂点一致＝面積0）
    if "PCDegen" not in bpy.data.objects:
        me = bpy.data.meshes.new("PCDegenMesh")
        me.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 0.0)], [], [(0, 1, 2)])
        me.update()
        obj = bpy.data.objects.new("PCDegen", me)
        bpy.context.scene.collection.objects.link(obj)

    # PCShA/PCShB: 同一 cube mesh を共有（print-repair 共有ガード検証・mesh_users=2）
    pcsha = _fresh_cube_obj("PCShA")
    if "PCShB" not in bpy.data.objects:
        pcshb = bpy.data.objects.new("PCShB", pcsha.data)  # 同一 mesh を共有
        bpy.context.scene.collection.objects.link(pcshb)

    # PCBroken: 複合破損（1面削除＝非多様体 + 別1面反転＝法線不整合）。print-repair の
    # 「全省略=全修復」チェーン（remove-degenerate→make-manifold→recalc）の E2E 検証用。
    pcb = _fresh_cube_obj("PCBroken")
    if len(pcb.data.polygons) == 6:
        bm = _bmesh.new()
        bm.from_mesh(pcb.data)
        bm.faces.ensure_lookup_table()
        _bmesh.ops.reverse_faces(bm, faces=[bm.faces[1]])  # 1面反転（法線不整合）
        _bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY")  # 別1面削除（穴）
        bm.to_mesh(pcb.data)
        bm.free()
        pcb.data.update()


def ensure_export_fixture():
    """print-export 検証用 cube（world (5,0,0) に平行移動）。STL の world 焼きを bbox で裏付ける。"""
    o = bpy.data.objects.get("ExpCube")
    if o is None:
        bpy.ops.mesh.primitive_cube_add(size=2.0)  # spike のみ（AST guard 対象外）
        o = bpy.context.active_object
        o.name = "ExpCube"
    o.location = (5.0, 0.0, 0.0)
    bpy.context.view_layer.update()  # matrix_world を確定（export は world 座標を焼く）
    return o


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
        "StrAlign",
        "StrAuto",
        "StrReset",
        "StrPCA",
        "StrPCADown",
        "StrFloor",
        "StrBake",
        "StrFloorY",
        "StrAlignY",
        "StrShA",
        "StrShB",
        "StrQuat",
        "StrAngle",
        "StrAngleBake",
        "StrAlignVec",
        "StrRefGuide",
        "StrRef",
        "PCClean",
        "PCOpen",
        "PCFlip",
        "PCDegen",
        "PCShA",
        "PCShB",
        "PCBroken",
        "ExpCube",  # M8 T8.5 print-export 検証用（world (5,0,0) cube）
    }, lo_names
    assert lo["data"]["count"] == 45, lo["data"]
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

    # 21) straighten（M8 T8.2・シナリオ2 直立補正）: reset / world-align / pca / floor / bake。
    # DoD: 補正後のローカル整列軸が world up と一致（閾値内・golden）。3シナリオは全 stable。

    # world-align（explicit axis=Z）: X 周り 30° tilt の cube → localZ を +Z へ・回転が除去される。
    wa, _ = call_retry(
        "straighten",
        {"targets": "StrAlign", "method": "world-align", "axis": "Z", "up_axis": "+Z"},
    )
    assert wa["data"]["method"] == "world-align", wa["data"]
    assert wa["data"]["axis"] == "+Z", wa["data"]
    assert approx(wa["data"]["aligned_world"], [0.0, 0.0, 1.0]), wa["data"]["aligned_world"]
    assert approx(wa["data"]["rotation_euler_deg"], [0.0, 0.0, 0.0], tol=1e-3), wa["data"]
    assert wa["data"]["baked"] is False, wa["data"]
    assert wa.get("fingerprint") and len(wa["fingerprint"]) == 16, wa
    print("straighten_world_align_ok axis=", wa["data"]["axis"], wa["data"]["aligned_world"])

    # world-align（auto）: 小さい tilt → up に最も近い軸 = +Z を自動選択し localZ を +Z へ。
    au, _ = call_retry(
        "straighten", {"targets": "StrAuto", "method": "world-align", "up_axis": "+Z"}
    )
    assert au["data"]["axis"] == "+Z", au["data"]  # 自動選択
    assert approx(au["data"]["aligned_world"], [0.0, 0.0, 1.0]), au["data"]["aligned_world"]
    print("straighten_world_align_auto_ok axis=", au["data"]["axis"])

    # reset: 30/20/10° 回転 → 回転がクリアされる。
    rs, _ = call_retry("straighten", {"targets": "StrReset", "method": "reset"})
    assert rs["data"]["method"] == "reset", rs["data"]
    assert approx(rs["data"]["rotation_euler_deg"], [0.0, 0.0, 0.0], tol=1e-3), rs["data"]
    print("straighten_reset_ok rot=", rs["data"]["rotation_euler_deg"])

    # pca: Z 方向に細長く tilt した rod → 主成分（最大分散軸）が +Z に整列する。
    pc, _ = call_retry("straighten", {"targets": "StrPCA", "method": "pca", "up_axis": "+Z"})
    assert pc["data"]["method"] == "pca", pc["data"]
    # 最大分散は Z 方向（eigvals 昇順の末尾が最大）。整列後の主成分 world は +Z に一致。
    assert pc["data"]["eigenvalues"][2] > pc["data"]["eigenvalues"][1], pc["data"]["eigenvalues"]
    assert approx(pc["data"]["principal_world_after"], [0.0, 0.0, 1.0], tol=1e-4), pc["data"]
    print("straighten_pca_ok principal_after=", pc["data"]["principal_world_after"])

    # up_hint（実地フィードバック #5）+ dry-run（#2）: ベースが重い rod は重心ベース(auto)だと
    # principal を下向きに誤判定→上下反転する。up_hint=current は up 寄りの符号を選び反転を防ぐ。
    # dry-run で auto/current を非破壊比較（principal_world の符号が分かれる・傾き角は同じ 20°）。
    da, _ = call_retry(
        "straighten",
        {"targets": "StrPCADown", "method": "pca", "up_hint": "auto", "dry_run": True},
    )
    dc, _ = call_retry(
        "straighten",
        {"targets": "StrPCADown", "method": "pca", "up_hint": "current", "dry_run": True},
    )
    assert da["data"]["dry_run"] is True and dc["data"]["dry_run"] is True, (da["data"], dc["data"])
    assert approx([da["data"]["tilt_from_up_deg"]], [20.0], tol=0.5), da["data"]
    assert approx([dc["data"]["tilt_from_up_deg"]], [20.0], tol=0.5), dc["data"]
    assert da["data"]["principal_world"][2] < 0.0, da["data"]  # auto: 重心(下)寄り→下向き（反転）
    assert dc["data"]["principal_world"][2] > 0.0, dc[
        "data"
    ]  # current: up 寄り→上向き（反転しない）
    assert approx(da["data"]["principal_world_after"], [0.0, 0.0, 1.0], tol=1e-3), da["data"]
    assert approx(dc["data"]["principal_world_after"], [0.0, 0.0, 1.0], tol=1e-3), dc["data"]
    print(
        "straighten_pca_up_hint_ok auto_z=",
        da["data"]["principal_world"][2],
        "current_z=",
        dc["data"]["principal_world"][2],
        "tilt=",
        dc["data"]["tilt_from_up_deg"],
    )

    # dry-run は非破壊（適用→計画読取→厳密復元）: 前後で rotation 不変、かつ計画 rotation は
    # 実適用の結果と一致（実コード経路を使うため忠実）。
    rb, _ = call_retry("object-info", {"targets": "StrPCADown"})
    dprev, _ = call_retry(
        "straighten",
        {"targets": "StrPCADown", "method": "pca", "up_hint": "current", "dry_run": True},
    )
    ra, _ = call_retry("object-info", {"targets": "StrPCADown"})
    assert approx(ra["data"]["rotation_euler_deg"], rb["data"]["rotation_euler_deg"], tol=1e-4), (
        "dry-run が状態を変えた",
        rb["data"]["rotation_euler_deg"],
        ra["data"]["rotation_euler_deg"],
    )
    real, _ = call_retry(
        "straighten", {"targets": "StrPCADown", "method": "pca", "up_hint": "current"}
    )
    assert real["data"]["dry_run"] is False, real["data"]
    assert approx(
        real["data"]["rotation_euler_deg"], dprev["data"]["rotation_euler_deg"], tol=1e-3
    ), (
        "dry-run 計画と実適用が不一致",
        dprev["data"]["rotation_euler_deg"],
        real["data"]["rotation_euler_deg"],
    )
    print("straighten_pca_dry_run_ok planned=", dprev["data"]["rotation_euler_deg"])

    # dry-run は QUATERNION モードでも全回転表現を厳密復元する（_restore_transform の核・非 euler 経路）。
    qb, _ = call_retry("object-info", {"targets": "StrQuat"})
    call_retry("straighten", {"targets": "StrQuat", "method": "reset", "dry_run": True})
    qa, _ = call_retry("object-info", {"targets": "StrQuat"})
    assert approx(qa["data"]["rotation_euler_deg"], qb["data"]["rotation_euler_deg"], tol=1e-4), (
        "QUATERNION dry-run が状態を変えた",
        qb["data"]["rotation_euler_deg"],
        qa["data"]["rotation_euler_deg"],
    )
    print("straighten_dry_run_quaternion_ok rot=", qa["data"]["rotation_euler_deg"])

    # floor: z=5 に浮かせた cube → up(+Z) 方向の最下点が z=0 に接地する。
    fbi, _ = call_retry("object-info", {"targets": "StrFloor"})
    assert approx([fbi["data"]["bbox"]["min"][2]], [4.0], tol=1e-3), fbi["data"]["bbox"]  # 接地前
    # dry-run floor: 計画（min_up/floor_offset）を返すが location は不変（非破壊・location 復元）。
    fdry, _ = call_retry(
        "straighten", {"targets": "StrFloor", "method": "floor", "up_axis": "+Z", "dry_run": True}
    )
    assert fdry["data"]["dry_run"] is True, fdry["data"]
    fmid, _ = call_retry("object-info", {"targets": "StrFloor"})
    assert approx([fmid["data"]["bbox"]["min"][2]], [4.0], tol=1e-3), fmid["data"][
        "bbox"
    ]  # 浮いたまま
    print("straighten_floor_dry_run_ok min_up=", fdry["data"]["min_up"])
    fl, _ = call_retry("straighten", {"targets": "StrFloor", "method": "floor", "up_axis": "+Z"})
    assert fl["data"]["method"] == "floor", fl["data"]
    assert approx([fl["data"]["min_up"]], [0.0], tol=1e-4), fl["data"]["min_up"]
    fai, _ = call_retry("object-info", {"targets": "StrFloor"})
    assert approx([fai["data"]["bbox"]["min"][2]], [0.0], tol=1e-3), fai["data"]["bbox"]  # 接地後
    print(
        "straighten_floor_ok min_up=", fl["data"]["min_up"], "offset=", fl["data"]["floor_offset"]
    )

    # up≠+Z の一般性: floor --up-axis +Y で y=5 の cube が y=0 に接地する（_floor が任意 up 対応）。
    fly, _ = call_retry("straighten", {"targets": "StrFloorY", "method": "floor", "up_axis": "+Y"})
    assert approx([fly["data"]["min_up"]], [0.0], tol=1e-4), fly["data"]["min_up"]
    fyi, _ = call_retry("object-info", {"targets": "StrFloorY"})
    assert approx([fyi["data"]["bbox"]["min"][1]], [0.0], tol=1e-3), fyi["data"]["bbox"]  # y 接地
    print("straighten_floor_upY_ok min_up=", fly["data"]["min_up"])

    # up≠+Z の一般性: world-align axis=Z --up-axis +Y → 無回転 cube の localZ を +Y へ整列。
    way, _ = call_retry(
        "straighten",
        {"targets": "StrAlignY", "method": "world-align", "axis": "Z", "up_axis": "+Y"},
    )
    assert way["data"]["up_axis"] == "+Y", way["data"]
    assert approx(way["data"]["aligned_world"], [0.0, 1.0, 0.0]), way["data"]["aligned_world"]
    print("straighten_world_align_upY_ok aligned=", way["data"]["aligned_world"])

    # bake-rotation: 45° yaw の cube。world-align axis=Z は localZ が既に +Z で no-op だが、
    # --bake-rotation で現在の回転を mesh へ焼き込む → 回転 0 化・world bbox（見た目）は不変。
    bbi, _ = call_retry("object-info", {"targets": "StrBake"})
    assert approx(bbi["data"]["rotation_euler_deg"], [0.0, 0.0, 45.0], tol=1e-3), bbi["data"]
    yaw_max = bbi["data"]["bbox"]["max"]  # 45° yaw の cube の world AABB（≈1.414）
    bk, _ = call_retry(
        "straighten",
        {"targets": "StrBake", "method": "world-align", "axis": "Z", "bake_rotation": True},
    )
    assert bk["data"]["baked"] is True, bk["data"]
    assert approx(bk["data"]["rotation_euler_deg"], [0.0, 0.0, 0.0], tol=1e-3), bk["data"]
    bai, _ = call_retry("object-info", {"targets": "StrBake"})
    assert approx(bai["data"]["rotation_euler_deg"], [0.0, 0.0, 0.0], tol=1e-3), bai["data"]
    # 回転を mesh へ焼いたので object 回転は 0 だが world bbox（見た目）は不変。
    assert approx(bai["data"]["bbox"]["max"], yaw_max, tol=1e-3), (bai["data"]["bbox"], yaw_max)
    print("straighten_bake_ok rot=", bai["data"]["rotation_euler_deg"], "bbox_max=", yaw_max)

    # straighten（bake 無し）は object 回転のみ変更 → 共有 mesh でも安全（ガード不要・分離しない）。
    sh0, _ = call_retry("object-info", {"targets": "StrShA"})
    assert sh0["data"]["mesh_users"] == 2, sh0["data"]  # StrShB と mesh 共有
    call_retry("straighten", {"targets": "StrShA", "method": "world-align", "axis": "Z"})
    sh1, _ = call_retry("object-info", {"targets": "StrShA"})
    assert sh1["data"]["mesh_users"] == 2, sh1["data"]  # 非破壊なので単一ユーザ化しない
    print("straighten_shared_no_bake_safe_ok")

    # bake は mesh を焼き込む破壊的操作 → 共有 mesh は --make-single-user 必須（apply 系と同様）。
    try:
        call_retry(
            "straighten",
            {"targets": "StrShA", "method": "world-align", "axis": "Z", "bake_rotation": True},
        )
        raise AssertionError("bake on shared mesh should require make_single_user")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    sh2, _ = call_retry("object-info", {"targets": "StrShA"})
    assert sh2["data"]["mesh_users"] == 2, sh2["data"]  # 失敗時に単一ユーザ化していない
    # --make-single-user 付き bake → 成功し単一ユーザ化（StrShB は元 mesh を保持＝波及せず）。
    call_retry(
        "straighten",
        {
            "targets": "StrShA",
            "method": "world-align",
            "axis": "Z",
            "bake_rotation": True,
            "make_single_user": True,
        },
    )
    sh3, _ = call_retry("object-info", {"targets": "StrShA"})
    assert sh3["data"]["mesh_users"] == 1, sh3["data"]  # 単一ユーザ化された
    # StrShB は元 mesh を保持＝StrShA の bake（30° を mesh へ焼き込み）が波及しない。
    # 焼き込み後の StrShA の mesh は 30° 回転して world bbox が広がるが、StrShB は軸並行の
    # clean cube（bbox max=[1,1,1]）のまま＝非波及の証明（回転も object 単位で 0 のまま）。
    shb, _ = call_retry("object-info", {"targets": "StrShB"})
    assert approx(shb["data"]["bbox"]["max"], [1.0, 1.0, 1.0], tol=1e-3), shb["data"]["bbox"]
    assert approx(shb["data"]["rotation_euler_deg"], [0.0, 0.0, 0.0], tol=1e-3), shb["data"]
    print("straighten_bake_shared_guard_ok")

    # 非対応型/前提（pca=非mesh / floor=非ジオメトリ / bake=非mesh）は E_PRECONDITION（INTERNAL 回避）。
    for bad in (
        {"targets": "QRot", "method": "pca"},  # EMPTY に pca（頂点なし）
        {"targets": "QRot", "method": "floor"},  # EMPTY に floor（bbox なし）
        {"targets": "QRot", "method": "reset", "bake_rotation": True},  # EMPTY に bake
    ):
        try:
            call_retry("straighten", bad)
            raise AssertionError(f"expected E_PRECONDITION for {bad}")
        except client.RpcRemoteError as e:
            assert e.error.get("message") == "E_PRECONDITION", (bad, e.error)
    print("straighten_precondition_guard_ok")

    # 21b) 基準指定 method（実地フィードバック #4・エージェント算出の補正を straighten 経由で安全適用）。

    # angle: 無回転 cube を world Z 周り 45° 回転 → 回転 [0,0,45]・world bbox≈1.414（45° yaw）。
    an, _ = call_retry(
        "straighten", {"targets": "StrAngle", "method": "angle", "axis": "Z", "degrees": 45.0}
    )
    assert an["data"]["method"] == "angle", an["data"]
    assert an["data"]["axis"] == "Z" and approx([an["data"]["degrees"]], [45.0]), an["data"]
    assert approx(an["data"]["rotation_euler_deg"], [0.0, 0.0, 45.0], tol=1e-3), an["data"]
    assert an["data"]["baked"] is False, an["data"]
    ani, _ = call_retry("object-info", {"targets": "StrAngle"})
    assert approx([ani["data"]["bbox"]["max"][0]], [1.41421], tol=1e-3), ani["data"]["bbox"]
    print("straighten_angle_ok rot=", an["data"]["rotation_euler_deg"])

    # angle + bake: 無回転 cube を Z 45° 回し mesh へ焼き込み → object 回転 0・world bbox は不変（≈1.414）。
    anb, _ = call_retry(
        "straighten",
        {
            "targets": "StrAngleBake",
            "method": "angle",
            "axis": "Z",
            "degrees": 45.0,
            "bake_rotation": True,
        },
    )
    assert anb["data"]["baked"] is True, anb["data"]
    assert approx(anb["data"]["rotation_euler_deg"], [0.0, 0.0, 0.0], tol=1e-3), anb["data"]
    anbi, _ = call_retry("object-info", {"targets": "StrAngleBake"})
    assert approx(anbi["data"]["rotation_euler_deg"], [0.0, 0.0, 0.0], tol=1e-3), anbi["data"]
    # 回転は mesh へ焼き込まれ object 回転は 0 だが world bbox（見た目）は 45° yaw のまま（≈1.414）。
    assert approx([anbi["data"]["bbox"]["max"][0]], [1.41421], tol=1e-3), anbi["data"]["bbox"]
    print("straighten_angle_bake_ok bbox_max_x=", anbi["data"]["bbox"]["max"][0])

    # align-vector（dry-run・to_dir 省略=up へ）: 20° 傾いた現在方向 → +Z へ。非破壊（前後で回転不変）。
    avb, _ = call_retry("object-info", {"targets": "StrAlignVec"})
    tilt = (0.0, math.sin(math.radians(20)), math.cos(math.radians(20)))  # +Z から 20° 傾けた方向
    avd, _ = call_retry(
        "straighten",
        {
            "targets": "StrAlignVec",
            "method": "align-vector",
            "from_dir": list(tilt),
            "dry_run": True,
        },
    )
    assert avd["data"]["dry_run"] is True, avd["data"]
    assert approx(avd["data"]["to_dir"], [0.0, 0.0, 1.0], tol=1e-6), avd["data"]  # 省略時は up(+Z)
    assert approx([avd["data"]["angle_deg"]], [20.0], tol=1e-3), avd["data"]
    assert approx(avd["data"]["from_world_after"], [0.0, 0.0, 1.0], tol=1e-4), avd["data"]
    ava, _ = call_retry("object-info", {"targets": "StrAlignVec"})
    assert approx(ava["data"]["rotation_euler_deg"], avb["data"]["rotation_euler_deg"], tol=1e-4), (
        "align-vector dry-run が状態を変えた",
        avb["data"]["rotation_euler_deg"],
        ava["data"]["rotation_euler_deg"],
    )
    # align-vector（実適用・explicit to_dir）: 同じ方向を +Z へ立てる → from_world_after≈+Z。
    av, _ = call_retry(
        "straighten",
        {
            "targets": "StrAlignVec",
            "method": "align-vector",
            "from_dir": list(tilt),
            "to_dir": [0.0, 0.0, 1.0],
        },
    )
    assert av["data"]["method"] == "align-vector", av["data"]
    assert approx(av["data"]["from_world_after"], [0.0, 0.0, 1.0], tol=1e-4), av["data"]
    assert approx([av["data"]["angle_deg"]], [20.0], tol=1e-3), av["data"]
    print("straighten_align_vector_ok angle=", av["data"]["angle_deg"])

    # reference: 参照 StrRefGuide（Y 25° tilt）の +Z 軸 world 方向（≈[sin25,0,cos25]）へ対象 StrRef を整列。
    # 整列後の対象 localZ world ≈ 参照軸方向 ＝ aligned_world ≈ reference_world（world up=+Z とは異なる）。
    refdir = [math.sin(math.radians(25)), 0.0, math.cos(math.radians(25))]
    rf, _ = call_retry(
        "straighten",
        {
            "targets": "StrRef",
            "method": "reference",
            "reference": "StrRefGuide",
            "ref_axis": "+Z",
            "axis": "Z",
        },
    )
    assert rf["data"]["method"] == "reference", rf["data"]
    assert rf["data"]["reference"] == "StrRefGuide" and rf["data"]["ref_axis"] == "+Z", rf["data"]
    assert approx(rf["data"]["reference_world"], refdir, tol=1e-3), rf["data"]["reference_world"]
    assert approx(rf["data"]["aligned_world"], refdir, tol=1e-3), rf["data"]["aligned_world"]
    # 参照軸は world up（+Z）と異なる＝reference が「世界の up」ではなく「ガイドの向き」を使う証明。
    assert not approx(rf["data"]["aligned_world"], [0.0, 0.0, 1.0], tol=1e-2), rf["data"]
    print("straighten_reference_ok aligned=", rf["data"]["aligned_world"])

    # ref_axis 省略時は up_axis にフォールバック（up_axis=+Y → 参照の +Y 軸方向へ）。dry-run で経路確認。
    rfb, _ = call_retry(
        "straighten",
        {
            "targets": "StrRef",
            "method": "reference",
            "reference": "StrRefGuide",
            "up_axis": "+Y",
            "dry_run": True,
        },
    )
    assert rfb["data"]["ref_axis"] == "+Y", rfb["data"]  # 省略 → up_axis(+Y) にフォールバック
    # StrRefGuide は Y 周り回転なので +Y 軸は不変＝world [0,1,0]（参照軸が ref_axis で切り替わる証明）。
    assert approx(rfb["data"]["reference_world"], [0.0, 1.0, 0.0], tol=1e-3), rfb["data"][
        "reference_world"
    ]
    print("straighten_reference_ref_axis_default_ok ref_axis=", rfb["data"]["ref_axis"])

    # reference 自己参照は USER_INPUT（INVALID_PARAMS）で弾く（補正前に解決→比較・bpy 後）。
    try:
        call_retry(
            "straighten",
            {"targets": "StrRef", "method": "reference", "reference": "StrRef"},
        )
        raise AssertionError("self-reference should be rejected")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "INVALID_PARAMS", e.error
    print("straighten_reference_self_guard_ok")

    # 22) print-setup（M8 T8.3・シナリオ3 3Dプリンタ対応）: 表示単位 mm/m を設定（geometry 非破壊）。
    # 既定は METERS（研究 §E5）。Cube の dimensions を捕え、mm 設定後も不変＝表示専用を確認。
    cdim, _ = call_retry("object-info", {"targets": "Cube"})
    dims_before = cdim["data"]["dimensions"]
    ps, _ = call_retry("print-setup", {"unit": "mm"})
    assert ps["data"]["unit"] == "mm", ps["data"]
    assert ps["data"]["unit_settings"]["system"] == "METRIC", ps["data"]
    assert ps["data"]["unit_settings"]["length_unit"] == "MILLIMETERS", ps["data"]
    assert ps["data"]["changed"] is True, ps["data"]  # 既定 METERS → mm へ変化
    assert ps.get("fingerprint") and len(ps["fingerprint"]) == 16, ps
    # scene-info の unit_settings にも反映される（SSOT 一致）。
    siu, _ = call_retry("scene-info", {"depth": 1})
    assert siu["data"]["unit_settings"]["length_unit"] == "MILLIMETERS", siu["data"][
        "unit_settings"
    ]
    # geometry 非破壊: Cube の dimensions は表示単位を変えても不変（length_unit は表示専用）。
    cdim2, _ = call_retry("object-info", {"targets": "Cube"})
    assert approx(cdim2["data"]["dimensions"], dims_before), (
        dims_before,
        cdim2["data"]["dimensions"],
    )
    print("print_setup_mm_ok", ps["data"]["unit_settings"], "dims_unchanged=", dims_before)

    # 冪等性: もう一度 mm → changed=False（既に mm）。
    ps_again, _ = call_retry("print-setup", {"unit": "mm"})
    assert ps_again["data"]["changed"] is False, ps_again["data"]
    assert ps_again["fingerprint"] == ps["fingerprint"], (
        ps_again["fingerprint"],
        ps["fingerprint"],
    )
    print("print_setup_idempotent_ok")

    # --unit m に戻す + --scene で active シーン名を明示指定（解決が通る）。
    active_scene = siu["data"]["scene"]
    psm, _ = call_retry("print-setup", {"unit": "m", "scene": active_scene})
    assert psm["data"]["unit_settings"]["length_unit"] == "METERS", psm["data"]
    assert psm["data"]["scene"] == active_scene, psm["data"]
    print("print_setup_m_scene_ok", active_scene)

    # 存在しないシーン名 → E_TARGET_NOT_FOUND（USER_INPUT・状態は汚さない）。
    try:
        call_retry("print-setup", {"unit": "mm", "scene": "NoSuchSceneXYZ"})
        raise AssertionError("print-setup on missing scene should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_TARGET_NOT_FOUND", e.error
        assert e.error.get("data", {}).get("category") == "USER_INPUT", e.error
    print("print_setup_missing_scene_ok")

    # 23) print-check / print-repair（M8 T8.4・シナリオ3）: bmesh 自前チェック + 縮退 + 修復。
    # clean cube → is_printable True・全カテゴリ 0。
    pcc, _ = call_retry("print-check", {"targets": "PCClean"})
    cc = pcc["data"]["checks"]
    assert cc["is_printable"] is True, cc
    assert cc["non_manifold_edges"] == 0 and cc["flipped_normals"] == 0, cc
    assert cc["degenerate_faces"] == 0, cc
    assert pcc.get("fingerprint") and len(pcc["fingerprint"]) == 16, pcc
    print("print_check_clean_ok", cc["is_printable"])

    # 1面削除 cube → 非多様体 boundary edge=4・is_manifold False・is_printable False。
    pco, _ = call_retry("print-check", {"targets": "PCOpen"})
    co = pco["data"]["checks"]
    assert co["non_manifold_edges"] == 4 and co["boundary_edges"] == 4, co
    assert co["is_manifold"] is False and co["is_printable"] is False, co
    print("print_check_open_ok non_manifold=", co["non_manifold_edges"])

    # 1面反転 cube → 反転法線 non_contiguous=4（flipped_normals）。
    pcf, _ = call_retry("print-check", {"targets": "PCFlip", "normals": True})
    cf = pcf["data"]["checks"]
    assert cf["flipped_normals"] == 4 and cf["normals_consistent"] is False, cf
    # --normals だけ要求 → degenerate キーは出ない（カテゴリ選択）。
    assert "degenerate_faces" not in cf, cf
    assert pcf["data"]["checked"] == ["normals"], pcf["data"]
    print("print_check_flip_ok flipped=", cf["flipped_normals"])

    # 退化三角形 → degenerate_faces=1。
    pcd, _ = call_retry("print-check", {"targets": "PCDegen", "degenerate": True})
    assert pcd["data"]["checks"]["degenerate_faces"] == 1, pcd["data"]
    print("print_check_degenerate_ok")

    # thin/intersect は print3d 依存（§E6: この環境では実体なし）→ CAPABILITY_UNAVAILABLE。
    # bmesh カテゴリと混在指定でも、print3d 要求が混ざれば全体が CAPABILITY_UNAVAILABLE（黙殺しない）。
    for cap in (
        {"thin": True, "min_thickness": 0.5},
        {"intersect": True},
        {"manifold": True, "thin": True},
    ):
        try:
            call_retry("print-check", {"targets": "PCClean", **cap})
            raise AssertionError(f"print3d check should be unavailable: {cap}")
        except client.RpcRemoteError as e:
            assert e.error.get("message") == "CAPABILITY_UNAVAILABLE", (cap, e.error)
            assert e.error.get("data", {}).get("category") == "ENVIRONMENT", (cap, e.error)
    print("print_check_capability_unavailable_ok")

    # 非 mesh（QRot=EMPTY）→ E_PRECONDITION。
    try:
        call_retry("print-check", {"targets": "QRot"})
        raise AssertionError("print-check on non-mesh should error")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    print("print_check_nonmesh_guard_ok")

    # print-repair make-manifold: PCOpen の穴を埋めて manifold 化（非多様体 4→0）。
    rpo, _ = call_retry("print-repair", {"targets": "PCOpen", "make_manifold": True})
    assert "make-manifold" in rpo["data"]["applied"], rpo["data"]
    assert rpo["data"]["before"]["non_manifold_edges"] == 4, rpo["data"]
    assert rpo["data"]["after"]["non_manifold_edges"] == 0, rpo["data"]
    assert rpo["data"]["after"]["is_manifold"] is True, rpo["data"]
    assert rpo["data"]["fixed"]["non_manifold_edges"] == 4, rpo["data"]
    print("print_repair_make_manifold_ok", rpo["data"]["fixed"])

    # print-repair recalc-normals: PCFlip の法線を一貫化（反転 4→0）。
    rpf, _ = call_retry("print-repair", {"targets": "PCFlip", "recalc_normals": True})
    assert "recalc-normals" in rpf["data"]["applied"], rpf["data"]
    assert rpf["data"]["after"]["flipped_normals"] == 0, rpf["data"]
    assert rpf["data"]["after"]["normals_consistent"] is True, rpf["data"]
    print("print_repair_recalc_ok")

    # print-repair remove-degenerate: PCDegen の退化面を除去（1→0）。
    rpd, _ = call_retry("print-repair", {"targets": "PCDegen", "remove_degenerate": True})
    assert "remove-degenerate" in rpd["data"]["applied"], rpd["data"]
    assert rpd["data"]["after"]["degenerate_faces"] == 0, rpd["data"]
    print("print_repair_remove_degenerate_ok")

    # print-repair 共有 mesh ガード: PCShA/PCShB は同一 mesh 共有（mesh_users=2）。
    # --make-single-user 無しは E_PRECONDITION（破壊的＝apply 系と同様）。
    try:
        call_retry("print-repair", {"targets": "PCShA", "recalc_normals": True})
        raise AssertionError("print-repair on shared mesh should require make_single_user")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    psa0, _ = call_retry("object-info", {"targets": "PCShA"})
    assert psa0["data"]["mesh_users"] == 2, psa0["data"]  # 失敗時に単一ユーザ化していない
    call_retry(
        "print-repair", {"targets": "PCShA", "recalc_normals": True, "make_single_user": True}
    )
    psa1, _ = call_retry("object-info", {"targets": "PCShA"})
    assert psa1["data"]["mesh_users"] == 1, psa1["data"]  # 単一ユーザ化された
    print("print_repair_shared_guard_ok")

    # print-repair 全省略=全修復（remove-degenerate→make-manifold→recalc の連結）。複合破損 PCBroken
    # （穴+反転）→ before は非printable・after は printable（致命カテゴリ全 0・S3 完了条件の直接裏付け）。
    pcb_before, _ = call_retry("print-check", {"targets": "PCBroken"})
    assert pcb_before["data"]["checks"]["is_printable"] is False, pcb_before["data"]
    rpa, _ = call_retry("print-repair", {"targets": "PCBroken"})  # 全省略＝全修復
    assert set(rpa["data"]["applied"]) == {
        "remove-degenerate",
        "make-manifold",
        "recalc-normals",
    }, rpa["data"]["applied"]
    assert rpa["data"]["after"]["is_printable"] is True, rpa["data"]
    assert rpa["data"]["after"]["non_manifold_edges"] == 0, rpa["data"]
    assert rpa["data"]["after"]["flipped_normals"] == 0, rpa["data"]
    print(
        "print_repair_all_ok",
        rpa["data"]["applied"],
        "printable=",
        rpa["data"]["after"]["is_printable"],
    )

    # print-export（M8 T8.5・シナリオ3）: STL 書き出し（wm.stl_export・world 焼き・global_scale 一本化・§E8）。
    import hashlib as _hashlib

    export_dir = tempfile.mkdtemp(prefix="bli-export-smoke-")
    bin_path = os.path.join(export_dir, "expcube.stl")
    ex1, _ = call_retry("print-export", {"targets": "ExpCube", "format": "stl", "path": bin_path})
    assert ex1["data"]["format"] == "stl", ex1["data"]
    assert ex1["data"]["triangles"] == 12, ex1["data"]  # cube=6面→12三角形
    assert ex1["data"]["size"] == 684, ex1["data"]  # binary cube は format 固定 684B（84+12*50）
    assert ex1["data"]["ascii"] is False, ex1["data"]
    assert ex1["data"]["global_scale"] == 1.0, ex1["data"]
    assert ex1["data"]["apply_modifiers"] is True, ex1["data"]
    assert "scale_length" in ex1["data"], ex1["data"]  # 検証用に scale_length を報告
    assert os.path.exists(ex1["data"]["path"]), ex1["data"]  # 実ファイル生成
    with open(ex1["data"]["path"], "rb") as _ef:
        file_sha = _hashlib.sha256(_ef.read()).hexdigest()
    assert ex1["data"]["sha256"] == file_sha, ex1["data"]  # 報告 sha == 実ファイル sha
    assert ex1["fingerprint"] == file_sha[:16], (
        ex1["fingerprint"],
        file_sha,
    )  # fingerprint=content-address
    # world 焼き: ExpCube は world (5,0,0)・size 2 → 出力 bbox x∈[4,6] y,z∈[-1,1]（transform 適用の裏付け）。
    bmin, bmax = stl_binary_bbox(ex1["data"]["path"])
    assert approx(bmin, (4.0, -1.0, -1.0)) and approx(bmax, (6.0, 1.0, 1.0)), (bmin, bmax)
    print("print_export_binary_ok", ex1["data"]["triangles"], ex1["data"]["size"])

    # global_scale=2: 全座標が2倍（world 位置も含む）→ bbox x∈[8,12] y,z∈[-2,2]（§E8・決定的 golden）。
    s2_path = os.path.join(export_dir, "expcube_s2.stl")
    ex2, _ = call_retry(
        "print-export", {"targets": "ExpCube", "format": "stl", "path": s2_path, "scale": 2.0}
    )
    assert ex2["data"]["global_scale"] == 2.0, ex2["data"]
    bmin2, bmax2 = stl_binary_bbox(ex2["data"]["path"])
    assert approx(bmin2, (8.0, -2.0, -2.0)) and approx(bmax2, (12.0, 2.0, 2.0)), (bmin2, bmax2)
    print("print_export_scale_ok", bmin2, bmax2)

    # ascii STL: 先頭が "solid"（binary と別形式）・triangles は facet 行から数える。
    ascii_path = os.path.join(export_dir, "expcube_ascii.stl")
    exa, _ = call_retry(
        "print-export", {"targets": "ExpCube", "format": "stl", "path": ascii_path, "ascii": True}
    )
    assert exa["data"]["ascii"] is True, exa["data"]
    assert exa["data"]["triangles"] == 12, exa["data"]
    with open(exa["data"]["path"], "rb") as _f:
        assert _f.read(5) == b"solid", "ascii STL は solid で始まる"
    print("print_export_ascii_ok")

    # 非破壊（mutates=False）: export はシーンを変えない。ExpCube の object_fingerprint が export 前後で不変。
    eo0, _ = call_retry("object-info", {"targets": "ExpCube"})
    call_retry("print-export", {"targets": "ExpCube", "format": "stl", "path": bin_path})
    eo1, _ = call_retry("object-info", {"targets": "ExpCube"})
    assert eo0["fingerprint"] == eo1["fingerprint"], (eo0["fingerprint"], eo1["fingerprint"])
    print("print_export_nondestructive_ok")

    # apply_modifiers トグルの実効: SUBSURF を一時付与 → True（既定）=焼き込みで三角形が増える /
    # False=素の cube（12三角形）。フラグが実際に出力ジオメトリを変えることを裏付ける。
    call_retry("modifier", {"action": "add", "targets": "ExpCube", "type": "SUBSURF", "levels": 1})
    am_on_path = os.path.join(export_dir, "expcube_modon.stl")
    am_on, _ = call_retry(
        "print-export", {"targets": "ExpCube", "format": "stl", "path": am_on_path}
    )
    am_off_path = os.path.join(export_dir, "expcube_modoff.stl")
    am_off, _ = call_retry(
        "print-export",
        {"targets": "ExpCube", "format": "stl", "path": am_off_path, "apply_modifiers": False},
    )
    assert am_on["data"]["triangles"] > 12, am_on["data"]  # SUBSURF 焼き込みで三角形が増える
    assert am_off["data"]["triangles"] == 12, am_off["data"]  # 素の cube（12三角形）
    call_retry(
        "modifier", {"action": "remove", "targets": "ExpCube", "name": "Subsurf"}
    )  # 後片付け
    print(
        "print_export_apply_modifiers_ok",
        "on=",
        am_on["data"]["triangles"],
        "off=",
        am_off["data"]["triangles"],
    )

    # 3mf は両版とも export operator が実体なし（§E8）→ CAPABILITY_UNAVAILABLE + STL hint。
    try:
        call_retry(
            "print-export",
            {"targets": "ExpCube", "format": "3mf", "path": os.path.join(export_dir, "x.3mf")},
        )
        raise AssertionError("3mf は CAPABILITY_UNAVAILABLE のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "CAPABILITY_UNAVAILABLE", e.error
    # 非 mesh（EMPTY 等）は E_PRECONDITION（require_mesh）。
    try:
        call_retry(
            "print-export",
            {"targets": "QRot", "format": "stl", "path": os.path.join(export_dir, "q.stl")},
        )
        raise AssertionError("非 mesh の export は E_PRECONDITION のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    # 出力先ディレクトリ不在は USER_INPUT（bpy 到達前）。
    try:
        call_retry(
            "print-export",
            {
                "targets": "ExpCube",
                "format": "stl",
                "path": os.path.join(export_dir, "no_such_subdir", "x.stl"),
            },
        )
        raise AssertionError("不在ディレクトリは INVALID_PARAMS のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "INVALID_PARAMS", e.error
    print("print_export_guards_ok")

    # export（M9 T9.1・多形式 export）: print-export の作法を obj/fbx/gltf/stl へ一般化（§E9）。
    # 検証は worker スレッドから bpy.ops を呼べない（dispatch メイン直列）ため、出力ファイルを直接
    # パースして world 焼き/選択を裏付ける。往復 bbox 全形式一致は fileio_spike.py（両版）が担う。
    gen_dir = tempfile.mkdtemp(prefix="bli-gen-export-smoke-")

    # --targets でターゲットのみ出力（STL）: ExpCube は world (5,0,0)・size 2 → bbox x∈[4,6]。
    # もし選択が効かず全シーンを出すと原点 Cube（x∈[-1,1]）を含み min x が -1 になる＝選択 param の裏付け。
    gx_stl = os.path.join(gen_dir, "exp.stl")
    gx1, _ = call_retry("export", {"format": "stl", "path": gx_stl, "targets": "ExpCube"})
    assert gx1["data"]["format"] == "stl", gx1["data"]
    assert gx1["data"]["use_selection"] is True, gx1["data"]
    assert gx1["data"]["exported_objects"] == ["ExpCube"], gx1["data"]
    assert gx1["data"]["operator"] == "wm.stl_export", gx1["data"]
    bmn, bmx = stl_binary_bbox(gx1["data"]["path"])
    assert approx(bmn, (4.0, -1.0, -1.0)) and approx(bmx, (6.0, 1.0, 1.0)), (bmn, bmx)
    with open(gx1["data"]["path"], "rb") as _f:
        assert gx1["data"]["sha256"] == _hashlib.sha256(_f.read()).hexdigest(), gx1["data"]
    assert gx1["fingerprint"] == gx1["data"]["sha256"][:16], gx1

    # --targets で OBJ 出力: world 焼きの v 行 bbox = x∈[4,6]・頂点数8（選択が効いている裏付け）。
    gx_obj = os.path.join(gen_dir, "exp.obj")
    gx2, _ = call_retry("export", {"format": "obj", "path": gx_obj, "targets": "ExpCube"})
    assert gx2["data"]["operator"] == "wm.obj_export", gx2["data"]
    omn, omx, overts = obj_text_bbox(gx2["data"]["path"])
    assert approx(omn, (4.0, -1.0, -1.0)) and approx(omx, (6.0, 1.0, 1.0)), (omn, omx)
    assert overts == 8, overts  # cube=8頂点（OBJ は法線/UV で頂点分割しない）

    # シーン全体（targets/use_selection 省略）の OBJ: 原点 Cube（x -1）と ExpCube（x 6）を含み bbox が広がる。
    gx_all = os.path.join(gen_dir, "scene.obj")
    gx3, _ = call_retry("export", {"format": "obj", "path": gx_all})
    assert gx3["data"]["use_selection"] is False, gx3["data"]
    assert gx3["data"]["exported_objects"] is None, gx3["data"]  # 全シーンは列挙しない
    amn, amx, averts = obj_text_bbox(gx3["data"]["path"])
    assert amn[0] <= -1.0 + 1e-4 and amx[0] >= 6.0 - 1e-4, (amn, amx)  # 原点 Cube〜ExpCube を内包
    assert averts > 8, averts  # 複数オブジェクト分の頂点

    # --use-selection: select で ExpCube を選び、現在の選択集合のみ出力（targets 省略）。
    call_retry("select", {"targets": "ExpCube"})
    gx_sel = os.path.join(gen_dir, "sel.obj")
    gx4, _ = call_retry("export", {"format": "obj", "path": gx_sel, "use_selection": True})
    assert gx4["data"]["use_selection"] is True, gx4["data"]
    assert gx4["data"]["exported_objects"] == ["ExpCube"], gx4["data"]
    smn, smx, _sverts = obj_text_bbox(gx4["data"]["path"])
    assert approx(smn, (4.0, -1.0, -1.0)) and approx(smx, (6.0, 1.0, 1.0)), (smn, smx)

    # glTF（.glb=GLB 単一バイナリ）/ FBX: 単一ファイルが生成され metadata が整う（往復 bbox は spike が担保）。
    gx_glb = os.path.join(gen_dir, "exp.glb")
    gx5, _ = call_retry("export", {"format": "gltf", "path": gx_glb, "targets": "ExpCube"})
    assert gx5["data"]["operator"] == "export_scene.gltf", gx5["data"]
    assert os.path.getsize(gx5["data"]["path"]) > 0, gx5["data"]
    with open(gx5["data"]["path"], "rb") as _f:
        assert _f.read(4) == b"glTF", "GLB は magic 'glTF' で始まる"  # 単一バイナリ確認
    gx_fbx = os.path.join(gen_dir, "exp.fbx")
    gx6, _ = call_retry("export", {"format": "fbx", "path": gx_fbx, "targets": "ExpCube"})
    assert gx6["data"]["operator"] == "export_scene.fbx", gx6["data"]
    assert os.path.getsize(gx6["data"]["path"]) > 0, gx6["data"]
    with open(gx6["data"]["path"], "rb") as _f:
        assert _f.read(20).startswith(b"Kaydara FBX Binary"), "binary FBX は Kaydara magic で始まる"

    # glTF は GLB 単一固定（.glb 必須）: .gltf 等は bpy 到達前に USER_INPUT で弾く（INVALID_PARAMS）。
    try:
        call_retry("export", {"format": "gltf", "path": os.path.join(gen_dir, "x.gltf")})
        raise AssertionError("gltf の非 .glb 拡張子は INVALID_PARAMS のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "INVALID_PARAMS", e.error

    # 3mf は両版とも export operator が実体なし（§E8）→ CAPABILITY_UNAVAILABLE。
    try:
        call_retry("export", {"format": "3mf", "path": os.path.join(gen_dir, "x.3mf")})
        raise AssertionError("3mf export は CAPABILITY_UNAVAILABLE のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "CAPABILITY_UNAVAILABLE", e.error
    # 存在しない targets は E_TARGET_NOT_FOUND（USER_INPUT）。
    try:
        call_retry("export", {"format": "stl", "path": gx_stl, "targets": "NoSuchObj_xyz"})
        raise AssertionError("存在しない targets は E_TARGET_NOT_FOUND のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_TARGET_NOT_FOUND", e.error
    # 出力先ディレクトリ不在は USER_INPUT（bpy 到達前）。
    try:
        call_retry(
            "export",
            {
                "format": "stl",
                "path": os.path.join(gen_dir, "no_such", "x.stl"),
                "targets": "ExpCube",
            },
        )
        raise AssertionError("不在ディレクトリは INVALID_PARAMS のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "INVALID_PARAMS", e.error
    print("export_multiformat_ok", "glb=", os.path.getsize(gx_glb), "fbx=", os.path.getsize(gx_fbx))

    # import（M9 T9.2・多形式 import）: 上で書き出した gen_dir/exp.* を取り込み、前後 diff の取込特定と
    # 往復 bbox（object-info 経由）を裏付ける。worker から bpy 直呼び不可なので検証は dispatch コマンド
    # （import→object-info→delete）で行う。ExpCube は world(5,0,0)・size2 で export 時に world 焼き →
    # 取り込んだ mesh の world bbox は x∈[4,6]（往復一致）。検証後は delete で取込物を消し scene を安定化。
    for fmt, src in (("stl", gx_stl), ("obj", gx_obj), ("gltf", gx_glb), ("fbx", gx_fbx)):
        imp, _ = call_retry("import", {"format": fmt, "path": src})
        assert imp["data"]["format"] == fmt, imp["data"]
        # operator は能力解決された実在 operator 名（非空）。正しさは往復 bbox が裏付ける。
        assert imp["data"]["operator"], imp["data"]
        items = imp["data"]["imported"]
        assert imp["data"]["count"] == len(items), imp["data"]
        meshes = [o for o in items if o["type"] == "MESH"]
        assert len(meshes) >= 1, (fmt, imp["data"])  # mesh が1つ以上取り込まれる
        # 取り込んだ mesh の world bbox = x∈[4,6]（往復一致・object-info で検証）。
        oi, _ = call_retry("object-info", {"targets": meshes[0]["name"]})
        bb = oi["data"]["bbox"]
        assert approx(bb["min"], (4.0, -1.0, -1.0)) and approx(bb["max"], (6.0, 1.0, 1.0)), (
            fmt,
            bb,
        )
        # cleanup: 取り込んだ全オブジェクトを削除（次形式の .001 リネーム汚染回避・再実行安定）。
        for o in items:
            call_retry("delete", {"targets": o["name"]})

    # 3mf import は両版とも operator が実体なし（§E8）→ CAPABILITY_UNAVAILABLE。
    try:
        call_retry("import", {"format": "3mf", "path": gx_stl})  # path は何でもよい（能力解決が先）
        raise AssertionError("3mf import は CAPABILITY_UNAVAILABLE のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "CAPABILITY_UNAVAILABLE", e.error
    # 入力ファイル不在は bpy 到達前に USER_INPUT（INVALID_PARAMS）。
    try:
        call_retry("import", {"format": "stl", "path": os.path.join(gen_dir, "no_such_file.stl")})
        raise AssertionError("入力ファイル不在は INVALID_PARAMS のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "INVALID_PARAMS", e.error
    # 壊れたファイル（拡張子は合うが中身が不正）は INTERNAL でなく E_OPERATOR に写像する（glTF importer は
    # Python 実装で RuntimeError 以外を投げ得る・import_generic の except Exception 防御の裏付け）。
    bad_glb = os.path.join(gen_dir, "corrupt.glb")
    with open(bad_glb, "wb") as _bf:
        _bf.write(b"this is not a valid glb file" * 4)
    try:
        call_retry("import", {"format": "gltf", "path": bad_glb})
        raise AssertionError("壊れた gltf は E_OPERATOR のはず（INTERNAL にしない）")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_OPERATOR", e.error
    print("import_multiformat_ok")

    # save（M9 T9.3）: .blend 保存 + backup（.blend1）の決定的制御（save_version 一時上書き）を裏付ける。
    # 注: save_as_mainfile は bpy.data.filepath を当該パスへ更新する（以降の no-path save が現在ファイルへ）。
    save_dir = tempfile.mkdtemp(prefix="bli-save-smoke-")
    sv_path = os.path.join(save_dir, "scene.blend")
    sv_abs = os.path.abspath(sv_path)
    # 1) 新規保存: ファイル生成・有効な .blend magic・backed_up=False（既存なし）。
    # magic は版で異なる: 4.4=非圧縮 b"BLENDER" / 5.0=zstd 圧縮 b"\x28\xb5\x2f\xfd"（compress 既定でも
    # 5.0 は zstd・研究 §E10）。どちらも valid な .blend なので両対応で判定する。
    sv1, _ = call_retry("save", {"path": sv_path})
    assert sv1["data"]["path"] == sv_abs, sv1["data"]
    assert sv1["data"]["size"] > 0, sv1["data"]
    assert sv1["data"]["backed_up"] is False and sv1["data"]["backup_path"] is None, sv1["data"]
    with open(sv_abs, "rb") as _sf:
        _magic = _sf.read(7)
    assert _magic.startswith(b"BLENDER") or _magic.startswith(b"\x28\xb5\x2f\xfd"), repr(_magic)
    # 2) 上書き保存（backup 既定 on）: .blend1 が生成される。
    sv2, _ = call_retry("save", {"path": sv_path})
    assert sv2["data"]["backed_up"] is True, sv2["data"]
    assert sv2["data"]["backup_path"] == sv_abs + "1", sv2["data"]
    assert os.path.exists(sv_abs + "1"), "backup .blend1 が生成される"
    # 3) --no-backup で上書き: .blend1 を作らない（save_version=0 抑止）。既存 backup を消して検証。
    os.remove(sv_abs + "1")
    sv3, _ = call_retry("save", {"path": sv_path, "backup": False})
    assert sv3["data"]["backed_up"] is False and sv3["data"]["backup_path"] is None, sv3["data"]
    assert not os.path.exists(sv_abs + "1"), (
        "--no-backup は .blend1 を作らない（save_version=0 抑止）"
    )
    # 4) --path 省略: 直近 save で設定された現在の .blend へ保存（bpy.data.filepath 解決）。
    sv4, _ = call_retry("save", {})
    assert sv4["data"]["path"] == sv_abs, sv4["data"]
    # 5) 保存先ディレクトリ不在は USER_INPUT（bpy 到達前）。
    try:
        call_retry("save", {"path": os.path.join(save_dir, "no_such", "x.blend")})
        raise AssertionError("不在ディレクトリは INVALID_PARAMS のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "INVALID_PARAMS", e.error
    print("save_ok", "size=", sv1["data"]["size"])

    # open（M9 T9.4）: .blend を開く（シーン全体置換）+ 未保存ガード（自前 session_state・§E11）。
    # 常駐 GUI でのタイマ/サーバ生存・open を含む 1 ジョブの結果 return は GUI スパイク（open_spike /
    # open_job_spike・両版確認済み）が担う。ここでは background の手動 pump で「往復」「ガード」「結果」を裏付ける。
    from bli_addon import session_state as _sstate

    # 直前に save して clean を保証（save→mark_saved）し、開く .blend を確定させる。
    call_retry("save", {"path": sv_path})
    assert _sstate.is_modified() is False, "save 後は clean"
    # 1) clean で open（force 不要）: シーン置換が成功し path/scene/objects/fingerprint を返す。
    op1, _ = call_retry("open", {"path": sv_path})
    assert op1["data"]["path"] == sv_abs, op1["data"]
    assert op1["data"]["object_count"] >= 1, op1["data"]
    assert op1["data"]["forced"] is False, op1["data"]
    assert op1["data"]["discarded_unsaved"] is False, op1["data"]
    assert op1["fingerprint"], op1
    assert bpy.data.filepath == sv_abs, bpy.data.filepath
    assert _sstate.is_modified() is False, "open 後は clean（mark_saved）"
    # open 後も dispatch は機能する（scene-info が通る＝pump/サーバ生存・background での裏付け）。
    si_open, _ = call_retry("scene-info", {})
    assert si_open["data"]["object_count"] >= 1, si_open["data"]
    print("open_roundtrip_ok", "object_count=", op1["data"]["object_count"])

    # 2) 未保存ガード: mutate（transform）→ session modified → open（force なし）は E_PRECONDITION。
    call_retry("transform", {"targets": "Cube", "location": [1.0, 0.0, 0.0]})
    assert _sstate.is_modified() is True, "transform 後は modified"
    try:
        call_retry("open", {"path": sv_path})
        raise AssertionError("未保存変更ありで open（force なし）は E_PRECONDITION のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_PRECONDITION", e.error
    # ガードは bpy 到達前に弾く＝シーンは置換されない（Cube は移動後のまま）。
    assert _sstate.is_modified() is True, "ガード後も modified のまま"
    # 3) --force で破棄して open: 成功し discarded_unsaved=True・以後 clean。
    op3, _ = call_retry("open", {"path": sv_path, "force": True})
    assert op3["data"]["forced"] is True and op3["data"]["discarded_unsaved"] is True, op3["data"]
    assert _sstate.is_modified() is False, "open 後は clean"
    # 4) 不在ファイルは bpy 到達前に USER_INPUT（INVALID_PARAMS）。
    try:
        call_retry("open", {"path": os.path.join(save_dir, "no_such.blend")})
        raise AssertionError("不在ファイルは INVALID_PARAMS のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "INVALID_PARAMS", e.error
    # 5) .blend 以外の拡張子も USER_INPUT（save と対称・ファイル実在は問わず拡張子で先に弾く）。
    try:
        call_retry("open", {"path": os.path.join(save_dir, "scene.txt")})
        raise AssertionError(".blend 以外は INVALID_PARAMS のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "INVALID_PARAMS", e.error
    print("open_unsaved_guard_ok")

    # capture（実地FB #1）: --background では GUI（window/area）が無いため viewport/screen は
    # E_PRECONDITION で graceful に縮退する（INTERNAL にしない）。実機 viewport/screen/render の
    # 機能検証は GUI の capture_spike.py（両版確認済み）が担う。
    for src in ("viewport", "screen"):
        try:
            call_retry("capture", {"source": src})
            raise AssertionError(f"capture {src} は --background で E_PRECONDITION のはず")
        except client.RpcRemoteError as e:
            assert e.error.get("message") == "E_PRECONDITION", e.error
    # render の不正カメラはレンダ到達前に E_TARGET_NOT_FOUND（GPU 不要・background でも検証可）。
    try:
        call_retry("capture", {"source": "render", "camera": "NoSuchCamera"})
        raise AssertionError("存在しないカメラは E_TARGET_NOT_FOUND のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "E_TARGET_NOT_FOUND", e.error
    print("capture_background_graceful_ok")

    # undo/redo（実地FB #3）: --background では undo スタックが不定なので E_PRECONDITION で graceful
    # 縮退する（INTERNAL にしない）。実巻き戻し（dispatch→ed.undo→ed.redo・複数段）の機能検証は GUI の
    # undo_spike.py（両版確認済み・研究 §E7）が担う。steps 範囲外は bpy 到達前に INVALID_PARAMS。
    for method in ("undo", "redo"):
        try:
            call_retry(method, {"steps": 1})
            raise AssertionError(f"{method} は --background で E_PRECONDITION のはず")
        except client.RpcRemoteError as e:
            assert e.error.get("message") == "E_PRECONDITION", (method, e.error)
        # steps 範囲外は GUI 不要で弾ける（サーバ側 USER_INPUT・bpy 到達前）。
        try:
            call_retry(method, {"steps": 0})
            raise AssertionError(f"{method} steps=0 は INVALID_PARAMS のはず")
        except client.RpcRemoteError as e:
            assert e.error.get("message") == "INVALID_PARAMS", (method, e.error)
    print("undo_redo_background_graceful_ok")

    # render busy（M10 T10.2）: レンダ中（render_state.mark_busy）は mutating/heavy を dispatch 前に
    # BUSY_RENDERING で即拒否し、read-only は通す（観測性維持）。render handler の実発火（GUI）は
    # render_spike.py（研究 §E12）が担う。ここでは busy フラグを手動 ON にして拒否経路を裏付ける。
    render_state.mark_busy()
    try:
        try:
            call_retry("transform", {"targets": "Cube", "location": [0.0, 0.0, 0.0]})
            raise AssertionError("レンダ中の transform は BUSY_RENDERING のはず")
        except client.RpcRemoteError as e:
            assert e.error.get("message") == "BUSY_RENDERING", e.error
            assert (e.error.get("data") or {}).get("retryable") is True, e.error
        # heavy だが mutates=False の export も拒否（heavy ブランチが mutating と独立）。
        try:
            call_retry("export", {"format": "stl", "path": "busy.stl"})
            raise AssertionError("レンダ中の export は BUSY_RENDERING のはず")
        except client.RpcRemoteError as e:
            assert e.error.get("message") == "BUSY_RENDERING", e.error
        # read-only（scene-info）はレンダ中も通る＝観測性を維持。
        si_busy, _ = call_retry("scene-info", {})
        assert si_busy["data"]["object_count"] >= 1, si_busy["data"]
    finally:
        render_state.mark_idle()
    # idle に戻れば mutating も通常どおり通る（busy 判定が誤爆していない）。
    call_retry("transform", {"targets": "Cube", "location": [0.0, 0.0, 0.0]})
    print("render_busy_reject_ok")

    # watchdog（M10 T10.3）: メインスレッド応答性は request-status（lock-free）応答に載る。background は
    # bpy.app.timers が発火しない＝heartbeat が自動更新されないため、生存印を手動で操作して観測経路を
    # 裏付ける（実 timer 停止の観測は watchdog_spike.py・研究 §E13 が担う）。
    watchdog.reset()
    watchdog.mark_alive()  # 直近 heartbeat あり＝responsive
    sr_wd, _ = call_retry("request-status", {"id": "smoke-wd"})
    assert sr_wd["data"]["watchdog"]["responsive"] is True, sr_wd["data"]["watchdog"]
    # 生存印を閾値より十分過去へずらす＝pump 停止（メインが固まった状態）の模擬。
    watchdog._last_pump_ts = time.time() - (runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD + 30)
    sr_wd2, _ = call_retry("request-status", {"id": "smoke-wd"})
    wd = sr_wd2["data"]["watchdog"]
    assert wd["responsive"] is False and wd["unresponsive_since"] is not None, wd
    watchdog.reset()  # idle へ戻す（後続が無ければ無害）
    print("watchdog_observability_ok")

    # exec-python（M11 T11.1）: mode の真実源はユーザローカル policy.toml（R-A）。GUI スパイク不要＝
    # 既存 Dispatcher のメインスレッド直列で実 bpy 上を走る（研究 §E14）。off→拒否 / trusted→実行を裏付ける。
    _policy_file = policy.policy_path()
    # (a) policy 不在＝off → EXEC_DISABLED（CLI からは mode を送れない＝昇格不可）。
    if _policy_file.exists():
        _policy_file.unlink()
    try:
        call_retry("exec-python", {"code": "1 + 1"})
        raise AssertionError("policy off で exec は EXEC_DISABLED のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "EXEC_DISABLED", e.error
    # (b) audited（T11.3・R-B）: 許可リストに無いコードは EXEC_DISABLED（不一致は fail-closed）。
    _policy_file.write_text('[exec]\nmode = "audited"\n', encoding="utf-8")
    try:
        call_retry("exec-python", {"code": "1 + 1"})
        raise AssertionError("audited で未許可コードは EXEC_DISABLED のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "EXEC_DISABLED", e.error
    # (b2) audited で sha256 を allow_hashes に追加すれば自走実行できる。
    _audited_code = "1 + 1"
    _audited_sha = audit.code_sha256(_audited_code)
    _policy_file.write_text(
        f'[exec]\nmode = "audited"\nallow_hashes = ["{_audited_sha}"]\n', encoding="utf-8"
    )
    exa, _ = call_retry("exec-python", {"code": _audited_code})
    assert exa["data"]["mode"] == "audited", exa["data"]
    assert exa["data"]["result_repr"] == "2", exa["data"]
    assert exa["data"]["code_sha256"] == _audited_sha, exa["data"]
    # (c) trusted へ昇格＝実行できる。stdout キャプチャ + 最終式 repr + security_guarantee=false。
    _policy_file.write_text('[exec]\nmode = "trusted"\n', encoding="utf-8")
    ex, _ = call_retry(
        "exec-python",
        {"code": "import bpy\nprint('hi from exec')\nlen(bpy.data.objects)"},
    )
    assert ex["operation"] == "exec-python", ex
    assert ex["data"]["mode"] == "trusted", ex["data"]
    assert ex["data"]["stdout"].strip() == "hi from exec", ex["data"]
    assert ex["data"]["result_repr"] == str(len(bpy.data.objects)), ex["data"]
    assert ex["data"]["security_guarantee"] is False, ex["data"]
    # bpy の import は注目モジュールではない＝無害コードは flag なし（T11.2）。
    assert ex["data"]["heuristic_flags"] == [], ex["data"]
    # (c2) AST ヒューリスティック（T11.2・R-D）: 危険 import は flag に載るが **ブロックしない**＝実行は成功。
    exf, _ = call_retry("exec-python", {"code": "import os\nlen(list(bpy.data.objects))"})
    assert exf["data"]["heuristic_flags"] == ["import:os"], exf["data"]
    assert exf["data"]["security_guarantee"] is False, exf["data"]
    # (d) 実 bpy を mutate できる（exec 経由でシーンが変わる）。Cube.x を 7 にして検証→0 へ戻す。
    exm, _ = call_retry(
        "exec-python",
        {
            "code": "import bpy\nbpy.data.objects['Cube'].location.x = 7.0\nbpy.data.objects['Cube'].location.x"
        },
    )
    assert exm["data"]["result_repr"] == "7.0", exm["data"]
    oi_exec, _ = call_retry("object-info", {"targets": "Cube"})
    assert approx(oi_exec["data"]["location"], [7.0, 0.0, 0.0]), oi_exec["data"]["location"]
    call_retry("exec-python", {"code": "import bpy\nbpy.data.objects['Cube'].location.x = 0.0"})
    # (e) ユーザコードの実行時例外 → EXEC_ERROR（INTERNAL 化しない）。
    try:
        call_retry("exec-python", {"code": "raise ValueError('boom')"})
        raise AssertionError("実行時例外は EXEC_ERROR のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "EXEC_ERROR", e.error
    # (f) 構文エラー → EXEC_ERROR（USER_INPUT・compile フェーズ）。
    try:
        call_retry("exec-python", {"code": "def (:\n  pass"})
        raise AssertionError("構文エラーは EXEC_ERROR のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "EXEC_ERROR", e.error
    # (g) policy を off へ戻すと再び拒否＝トグルが即反映される（再起動不要）。
    _policy_file.unlink()
    try:
        call_retry("exec-python", {"code": "1 + 1"})
        raise AssertionError("off へ戻したら再び EXEC_DISABLED のはず")
    except client.RpcRemoteError as e:
        assert e.error.get("message") == "EXEC_DISABLED", e.error
    # (h) 監査ログ（T11.3・§280）: 実行も拒否もすべて audit/exec.jsonl に残る（防止でなく検知）。
    _audit_rows = audit.read_entries()
    _decisions = [r["decision"] for r in _audit_rows]
    assert "executed" in _decisions, _decisions  # trusted/audited の実行
    assert "rejected:off" in _decisions, _decisions  # off 拒否
    assert "rejected:audited-unlisted" in _decisions, _decisions  # 未許可 audited 拒否
    print("exec_python_mode_gate_ok")


def main():
    print("=== BLI_OPS_SMOKE_BEGIN ===")
    print("python", sys.version.split()[0], "blender", bpy.app.version_string)
    ensure_cube()
    ensure_quaternion_empty()  # 非 Euler 回転モード検証用（メインスレッドで生成）
    ensure_parented()  # world 空間 transform 検証用（メインスレッドで生成）
    ensure_shared_mesh()  # 共有 mesh ガード検証用（メインスレッドで生成）
    ensure_object_linked_shared()  # OBJECT リンク slot ガードスキップ検証用（Codex P2）
    ensure_mesh_fixtures()  # M7 mesh 編集（flip/double/共有）検証用（メインスレッドで生成）
    ensure_straighten_fixtures()  # M8 straighten（直立補正）検証用（メインスレッドで生成）
    ensure_print_check_fixtures()  # M8 print-check/repair（破損 mesh 含む）検証用（メインスレッドで生成）
    ensure_export_fixture()  # M8 T8.5 print-export（world (5,0,0) cube）検証用（メインスレッドで生成）

    dispatcher = Dispatcher()  # background では timer を使わず手動 pump
    render_state.install()  # render handler を登録（busy フラグ駆動・本番 register と同じ）

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
        render_busy=render_state.is_busy,  # レンダ中は重量/破壊系を dispatch 前に拒否
        watchdog_status=watchdog.snapshot,  # メインスレッド応答性を request-status に載せる（M10 T10.3）
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

    # @persistent 生存確認（M10 T10.2・研究 §E12）: worker 完了後＝以後 quit するのでシーンを汚しても可。
    # render handler（install 済み）が open_mainfile を跨いで残るか。GUI 内で timer から open を呼ぶと
    # 固まるため GUI スパイクでは扱わず、read_homefile が無確認で安全な background でここで裏付ける。
    # 「未登録」と「open で消えた」を区別する（前者は install 不全＝別バグ）。
    installed_before = render_state.init_handler_registered()
    bpy.ops.wm.read_homefile(use_empty=True)  # ファイル読み込み相当（background は無確認）
    survived_open = render_state.init_handler_registered()
    persist_ok = installed_before and survived_open
    print(
        f"render_handler_persistent_ok installed={installed_before} survived_open={survived_open}"
    )

    srv_mod.stop()
    render_state.remove()

    if state.get("ok") and persist_ok:
        print("OPS SMOKE OK")
    else:
        print("OPS SMOKE FAIL")
        if not installed_before:
            print("render handler が install されていない（@persistent 以前の登録不全）")
        elif not survived_open:
            print("render handler が open_mainfile を跨いで生存しなかった（@persistent 不全）")
        print(state.get("error", "worker did not finish in time"))
    print("=== BLI_OPS_SMOKE_END ===")


if __name__ == "__main__":
    main()
