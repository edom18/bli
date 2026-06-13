"""M2 スタックを Blender 埋め込み Python(3.11) 上で疎通させるスモーク。

`blender --background --python smoke_inproc.py` で実行。
bli-core(純Python)/bli-addon/bli を sys.path 追加で import し、
サーバ起動→クライアント ping/echo→停止 を同一プロセスで検証する。
（bli-core が Blender の Python でも依存ゼロで動くことの実証）
"""

import os
import sys
import tempfile

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
for pkg in ("bli-core", "bli-cli", "bli-addon"):
    sys.path.insert(0, os.path.join(ROOT, "packages", pkg, "src"))

os.environ["BLI_STATE_DIR"] = tempfile.mkdtemp(prefix="bli-smoke-")

import bpy  # type: ignore  # noqa: E402

from bli import client  # noqa: E402
from bli_addon import server as srv_mod  # noqa: E402


def main():
    print("=== BLI_SMOKE_BEGIN ===")
    print("python", sys.version.split()[0], "blender", bpy.app.version_string)
    srv_mod.start(
        blender_version=bpy.app.version_string,
        schema_hash="smoke-hash",
        capabilities=["wm.stl_export"],
        host="127.0.0.1",
        port=0,
    )
    try:
        result, hello = client.call("ping")
        assert hello["type"] == "hello-ok", hello
        assert hello["blender_version"] == bpy.app.version_string
        assert result["data"]["blender_version"] == bpy.app.version_string
        echo, _ = client.call("echo", {"k": "値", "n": 7})
        assert echo["data"]["echo"] == {"k": "値", "n": 7}
        print("ping_blender_version", hello["blender_version"])
        print("echo_ok", echo["data"]["echo"])
        print("SMOKE OK")
    finally:
        srv_mod.stop()
    print("=== BLI_SMOKE_END ===")


if __name__ == "__main__":
    main()
