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
    assert lo_names == ["Cube"], lo_names
    assert lo["data"]["count"] == 1, lo["data"]
    lo2, _ = call_retry("list-objects", {"regex": "^Cu"})
    assert any(o["name"] == "Cube" for o in lo2["data"]["objects"]), lo2["data"]
    print("list_objects_ok mesh=", lo_names)

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


def main():
    print("=== BLI_OPS_SMOKE_BEGIN ===")
    print("python", sys.version.split()[0], "blender", bpy.app.version_string)
    ensure_cube()

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
