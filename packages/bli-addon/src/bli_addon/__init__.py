"""bli Blenderアドオン エントリポイント。

- 配布時は `vendored/bli_core` を同梱し sys.path に追加する。
- dev では workspace の bli-core が解決されるため、vendored が無くても動く。
- register/unregister で TCP サーバ + メインスレッド直列ディスパッチャを起動/停止する。

register の流れ（M3）:
  Dispatcher 生成 → install_timer（GUI 常駐で pump を駆動）→ server.start。
  サーバ受信スレッドからの RPC は executor 経由で Dispatcher.submit され、
  メインスレッドで `ops.dispatch` が直列実行される。

Python 3.10 互換を保つ（Blender 5.0/4.4 の埋め込み 3.11 でも動作）。
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dispatcher import Dispatcher

# legacy アドオン互換のメタ情報（Extensions では blender_manifest.toml が優先される）
bl_info = {
    "name": "bli (Blender CLI) server",
    "author": "edo_m",
    "version": (0, 1, 0),
    "blender": (4, 4, 0),
    "location": "Background TCP server (127.0.0.1)",
    "description": "AIエージェント向け CLI のための常駐 TCP サーバ",
    "category": "System",
}

__version__ = "0.1.0"


def _ensure_bli_core_on_path() -> None:
    """vendored/bli_core を import 可能にする（無ければ dev の workspace 解決に委ねる）。"""
    here = os.path.dirname(__file__)
    vendored = os.path.join(here, "vendored")
    if os.path.isdir(os.path.join(vendored, "bli_core")) and vendored not in sys.path:
        sys.path.insert(0, vendored)


_ensure_bli_core_on_path()


# メインスレッド直列ディスパッチャ（register〜unregister 間だけ生存）
_dispatcher: Dispatcher | None = None


def register() -> None:
    """ディスパッチャ + TCP サーバを起動する（GUI 常駐）。"""
    import bpy  # type: ignore  # lazy: アドオンロード時のみ

    from bli_core import runtime
    from bli_core.commands import get_command, is_heavy_request, load_definitions
    from bli_core.schema import schema_hash

    from . import ops, server
    from .capability import CapabilityRegistry
    from .dispatcher import ACCEPTED, Dispatcher

    global _dispatcher
    _dispatcher = Dispatcher()
    _dispatcher.install_timer()  # bpy.app.timers が pump を駆動

    dispatcher = _dispatcher

    def _executor(method, params, info, settle):
        # 受信スレッドから submit → メインスレッドで ops.dispatch を直列実行。
        # settle はジョブ完了時にメインスレッドで呼ばれ、registry を確定させる
        # （タイムアウト後に完走したジョブも request-status で回収可能になる）。
        def _run():
            return ops.dispatch(method, params, info)

        # heavy コマンド（M10・spec §7）は accepted 即返＝submit_async でキューに積んで待たずに返し、
        # ACCEPTED センチネルを返す（サーバが {accepted, job_id} 応答を組み立てる）。クライアントは
        # request-status / job-wait で回収する。これで重量 import 中も受信スレッドが塞がらない。
        cmd = get_command(method)
        if cmd is not None and is_heavy_request(cmd, params):
            dispatcher.submit_async(_run, settle=settle)
            return ACCEPTED

        # 通常（light）コマンドは同期 submit。ウォッチドッグはクライアント読み取り猶予より短くし、
        # 超過時は TIMEOUT 応答を先に返す（request-status で後追い可能）。
        return dispatcher.submit(_run, timeout=runtime.DISPATCH_TIMEOUT, settle=settle)

    server.start(
        blender_version=bpy.app.version_string,
        schema_hash=schema_hash(load_definitions()),
        capabilities=CapabilityRegistry().list_capabilities(),
        handler=_executor,
    )


def unregister() -> None:
    """TCP サーバを停止し、タイマを解除する。"""
    from . import server

    server.stop()
    global _dispatcher
    if _dispatcher is not None:
        _dispatcher.remove_timer()
        _dispatcher = None
