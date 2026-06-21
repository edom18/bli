"""配布 zip（vendored bli_core）を Blender 実機で疎通させるスモーク（M14 T14.2 検証）。

    blender --background --python packages/bli-addon/spikes/vendored_smoke.py

通常の `smoke_ops.py` は dev の uv workspace を `sys.path` に通すが、本スモークは
**配布 zip をビルド → 展開し、その展開先だけを `sys.path` に載せる**（workspace は載せない）。
これは Blender 埋め込み Python が dev workspace を知らない＝実配布と同じ条件であり、
`bli_addon/__init__._ensure_bli_core_on_path()` が同梱の `vendored/bli_core` を解決して
ops が実 bpy 上で動くことを確かめる（GUI からの zip 導入の headless 近似）。

成功すると BLI_VENDORED_SMOKE_BEGIN..END の間に `VENDORED SMOKE OK` を印字する。
"""

import importlib.util
import os
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))


def _load_build_addon():
    """scripts/build_addon.py をパス指定でロードする（純Python・bpy 非依存）。"""
    path = os.path.join(ROOT, "scripts", "build_addon.py")
    spec = importlib.util.spec_from_file_location("build_addon", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _approx(a, b, eps=1e-5):
    return all(abs(x - y) <= eps for x, y in zip(a, b, strict=False))


def main() -> None:
    import bpy  # type: ignore

    build_addon = _load_build_addon()

    tmp = tempfile.mkdtemp(prefix="bli-vendored-smoke-")
    zip_path = build_addon.build(Path(tmp) / "dist")
    extract = os.path.join(tmp, "addons")
    with zipfile.ZipFile(str(zip_path)) as zf:
        zf.extractall(extract)

    # 配布条件の再現: workspace を載せず、展開先のみを sys.path へ。
    # Blender 埋め込み Python に bli_core が事前ロードされていないことも確認（検証の前提）。
    assert "bli_core" not in sys.modules, "bli_core が事前ロードされている＝vendored 検証が無意味"
    sys.path.insert(0, extract)

    import bli_addon  # _ensure_bli_core_on_path() が vendored/bli_core を sys.path へ
    import bli_core

    core_loc = bli_core.__file__.replace(os.sep, "/")
    assert "vendored/bli_core" in core_loc, core_loc

    from bli_addon import ops
    from bli_addon.handlers import ServerInfo
    from bli_core.commands import load_definitions
    from bli_core.schema import schema_hash

    info = ServerInfo(bpy.app.version_string, schema_hash(load_definitions()), [])

    # 1) read-only: 既定起動シーンの Cube を scene-info で取得（vendored 経路で ops + bpy 結線）。
    si = ops.dispatch("scene-info", {"depth": 1}, info)
    assert si.get("success") is True, si
    names = [o["name"] for o in si["data"]["objects"]]
    assert "Cube" in names, names

    # 2) gateway 書き込み経路: set-origin world(1,0,0) → object-info で location==[1,0,0]・dims 不変。
    so = ops.dispatch(
        "set-origin", {"targets": "Cube", "to": "world", "x": 1.0, "y": 0.0, "z": 0.0}, info
    )
    assert so.get("success") is True, so
    oi = ops.dispatch("object-info", {"targets": "Cube"}, info)
    loc = oi["data"]["location"]
    dims = oi["data"]["dimensions"]
    assert _approx(loc, [1.0, 0.0, 0.0]), loc
    assert _approx(dims, [2.0, 2.0, 2.0]), dims

    # 3) operator 経路: set-origin geometry median で原点が幾何中心（world 原点）へ戻る。
    og = ops.dispatch("set-origin", {"targets": "Cube", "to": "geometry", "center": "median"}, info)
    assert og.get("success") is True, og
    oi2 = ops.dispatch("object-info", {"targets": "Cube"}, info)
    assert _approx(oi2["data"]["location"], [0.0, 0.0, 0.0]), oi2["data"]["location"]

    print("BLI_VENDORED_SMOKE_BEGIN")
    print("blender:", bpy.app.version_string)
    print("addon version:", bli_addon.__version__)
    print("vendored bli_core:", core_loc)
    print("scene objects:", names)
    print("after world  location:", loc, "dims:", dims)
    print("after median location:", oi2["data"]["location"])
    print("VENDORED SMOKE OK")
    print("BLI_VENDORED_SMOKE_END")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
