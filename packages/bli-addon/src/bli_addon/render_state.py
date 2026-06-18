"""レンダ中 busy 検知（M10 T10.2・spec §7）。

レンダリング中はメインスレッドが固まり得るため、重量/破壊系コマンドを **dispatch 前に**
`BUSY_RENDERING` で即拒否する（キューに積まない＝フリーズ中のジョブ滞留を防ぐ）。判定材料は
このモジュールが持つ busy フラグ。サーバ受信スレッドが `is_busy()` を読み、render handler が
上げ下げする。

**GUI スパイク（research §E12・5.0.1/4.4.3 両版）で確定した前提**:
  - `render_init` / `render_complete` / `render_cancel` は GUI 常駐サーバで発火する
    （`--background` では bpy.app.timers も handler も発火しない）。
  - これらの handler は **Blender のレンダスレッド（Dummy-N）** から呼ばれる（メインではない）。
    よって busy は `threading.Event`（GIL 下で set/clear/is_set がアトミック）で持ち、受信スレッドが
    安全に読めるようにする。
  - handler はファイル読み込み（`bli open`＝open_mainfile）を跨ぐと既定で解除されるため、
    `@persistent` を付けて生存させる（付けないと open 後に busy 検知が無言で壊れる）。

純Python 部（`is_busy`/`mark_busy`/`mark_idle`/`reset`）は bpy 非依存＝pytest で検証可能。
bpy 依存（handler 登録）は `install`/`remove` 内に閉じ込める（dispatcher.install_timer と同流儀）。
プロセスグローバル（=常駐 Blender 1 プロセスのレンダ状態）として保持する。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

_rendering = threading.Event()


def is_busy() -> bool:
    """レンダリング中か（受信スレッドが dispatch 前に読む）。"""
    return _rendering.is_set()


def mark_busy() -> None:
    """レンダ開始（render_init）で busy にする。"""
    _rendering.set()


def mark_idle() -> None:
    """レンダ終了（render_complete / render_cancel）で busy を降ろす。"""
    _rendering.clear()


def reset() -> None:
    """テスト用の明示リセット（idle 状態へ）。"""
    _rendering.clear()


# ---- bpy.app.handlers 連携（GUI 常駐）----
# 登録した (handler_list, fn) を覚えておき remove で確実に外す。
_installed: list[tuple[Any, Callable[..., None]]] = []


def _on_render_init(*_args) -> None:
    mark_busy()


def _on_render_end(*_args) -> None:
    # render_complete / render_cancel のどちらでも降ろす（キャンセル時の取りこぼし防止）。
    mark_idle()


def install() -> None:
    """render handler を登録する（GUI 常駐）。bpy 依存はここに閉じ込める。"""
    if _installed:
        # 冪等化: register 再入（アドオン reload 等）で handler を二重登録しない
        # （server.start の「既存があれば先に停止」と同流儀）。
        remove()
    import bpy  # type: ignore  # lazy: アドオンロード時のみ

    persistent = bpy.app.handlers.persistent
    h = bpy.app.handlers
    # @persistent で open_mainfile（bli open）を跨いで生存させる（research §E12）。
    init = persistent(_on_render_init)
    end = persistent(_on_render_end)
    for lst, fn in (
        (h.render_init, init),
        (h.render_complete, end),
        (h.render_cancel, end),
    ):
        lst.append(fn)
        _installed.append((lst, fn))


def remove() -> None:
    """登録した render handler を解除し idle へ戻す。"""
    for lst, fn in _installed:
        try:
            lst.remove(fn)
        except ValueError:
            pass
    _installed.clear()
    mark_idle()


def init_handler_registered() -> bool:
    """render_init handler が bpy のハンドラリストに登録済みか（@persistent 生存のスモーク確認用）。"""
    import bpy  # type: ignore

    return any(fn is _on_render_init for fn in bpy.app.handlers.render_init)
