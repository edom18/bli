"""メインスレッド直列ディスパッチャ（M3 / research.md 論点1）。

受信スレッドが submit() で関数を積み、メインスレッドが pump() で実行する。
GUI 常駐時は bpy.app.timers が pump を駆動する。--background/テストでは
メインスレッドが手動 pump ループを回す。bpy 依存は install_timer 内に限定。
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any


class TimeoutPending(Exception):
    """タイムアウト（実体は実行継続中の可能性）。spec §7 後追い回収。"""


class _Job:
    __slots__ = ("error", "event", "fn", "result")

    def __init__(self, fn: Callable[[], Any]) -> None:
        self.fn = fn
        self.event = threading.Event()
        self.result: Any = None
        self.error: Exception | None = None


class Dispatcher:
    def __init__(self) -> None:
        self._q: queue.Queue[_Job] = queue.Queue()
        self._tick: Callable[[], float] | None = None

    def submit(self, fn: Callable[[], Any], timeout: float = 30.0) -> Any:
        """メインスレッドで fn を実行し結果を返す（受信スレッドから呼ぶ）。"""
        job = _Job(fn)
        self._q.put(job)
        if not job.event.wait(timeout):
            raise TimeoutPending()
        if job.error is not None:
            raise job.error
        return job.result

    def pump(self) -> int:
        """キューを drain しメインスレッドで実行する。処理件数を返す。"""
        count = 0
        while True:
            try:
                job = self._q.get_nowait()
            except queue.Empty:
                return count
            try:
                job.result = job.fn()
            except Exception as e:
                job.error = e
            finally:
                job.event.set()
                count += 1

    # ---- bpy.app.timers 連携（GUI 常駐）----

    def install_timer(self, interval: float = 0.02) -> None:
        import bpy  # type: ignore  # lazy: bpy 依存をここに閉じ込める

        def _tick() -> float:
            try:
                self.pump()
            except Exception:
                pass
            return interval

        self._tick = _tick
        bpy.app.timers.register(_tick, persistent=True)

    def remove_timer(self) -> None:
        if self._tick is None:
            return
        import bpy  # type: ignore

        try:
            if bpy.app.timers.is_registered(self._tick):
                bpy.app.timers.unregister(self._tick)
        except Exception:
            pass
        self._tick = None
