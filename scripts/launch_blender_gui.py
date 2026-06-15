"""GUI 常駐 Blender に bli アドオンを登録して TCP サーバを起動する開発ヘルパ。

実機検証（screenshot/render など `--background` では不可な機能のスパイク）や CLI の
手動疎通に使う。Blender 内の Python で実行する想定:

    "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe" \
        --python scripts/launch_blender_gui.py

登録後は 127.0.0.1:9876 で待ち受ける。別シェルから `bli ping` 等で疎通できる。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg in ("bli-core", "bli-addon"):
    p = os.path.join(ROOT, "packages", pkg, "src")
    if p not in sys.path:
        sys.path.insert(0, p)

import bli_addon  # noqa: E402  （sys.path 構築後に import する必要があるため）

bli_addon.register()
print("[bli] addon registered — TCP server on 127.0.0.1:9876")
