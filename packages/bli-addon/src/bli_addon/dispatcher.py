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


# heavy コマンドの非同期実行（accepted 即返）を表すセンチネル。executor が submit_async 後に返し、
# サーバはこれを見て {accepted, job_id} 応答を組み立てる（M10・spec §7）。
class _Accepted:
    __slots__ = ()


ACCEPTED = _Accepted()


# settle(result, error) -> resp: ジョブ完了時にメインスレッドで呼ばれる確定処理。
# 受信スレッドのタイムアウト有無に関わらず必ず実行されるため、タイムアウト後に
# 完走したジョブの結果も registry 等へ反映できる（spec §7 後追い回収）。
SettleFn = Callable[[Any, BaseException | None], Any]


class _Job:
    __slots__ = ("error", "event", "fn", "result", "settle")

    def __init__(self, fn: Callable[[], Any], settle: SettleFn | None = None) -> None:
        self.fn = fn
        self.settle = settle
        self.event = threading.Event()
        self.result: Any = None
        # 重量ジョブは BaseException（C レベル異常）も投げ得るため広めに保持する（M10）。
        self.error: BaseException | None = None


class Dispatcher:
    def __init__(self) -> None:
        self._q: queue.Queue[_Job] = queue.Queue()
        self._tick: Callable[[], float] | None = None

    def submit(
        self, fn: Callable[[], Any], timeout: float = 30.0, settle: SettleFn | None = None
    ) -> Any:
        """メインスレッドで fn を実行し結果を返す（受信スレッドから呼ぶ）。

        settle を渡すと、ジョブ完了時にメインスレッドで settle(result, error) を呼び、
        その戻り値を結果とする。タイムアウトしても settle はジョブ完走時に必ず呼ばれる。
        """
        job = _Job(fn, settle)
        self._q.put(job)
        if not job.event.wait(timeout):
            raise TimeoutPending()
        if job.error is not None:
            raise job.error
        return job.result

    def submit_async(self, fn: Callable[[], Any], settle: SettleFn) -> None:
        """fn をキューに積んで **即座に返す**（受信スレッドをブロックしない・M10 heavy job）。

        pump が後でメインスレッドで fn を実行し、完了時に settle が registry を確定する（DONE/FAILED）。
        submit と違い job.event を待たない（accepted を即返するため）。結果はクライアントが
        request-status / job-wait で回収する。settle はジョブ完走時に必ず呼ばれる（spec §7）。
        """
        self._q.put(_Job(fn, settle))

    def pump(self) -> int:
        """キューを drain しメインスレッドで実行する。処理件数を返す。"""
        count = 0
        while True:
            try:
                job = self._q.get_nowait()
            except queue.Empty:
                return count
            try:
                result = job.fn()
                error: BaseException | None = None
            except BaseException as e:
                # 重量ネイティブ処理は C レベルの異常（BaseException）を投げ得る。Exception 限定だと
                # settle が走らず registry が RUNNING のまま孤児化し、例外が _tick へ伝播してタイマ
                # （pump）ごと死ぬ（=以降のジョブがハング）。BaseException を捕捉して必ず settle へ流し、
                # registry を FAILED 確定 + pump を生かす（M10・敵対的レビュー P2）。
                result, error = None, e
            if job.settle is not None:
                # 確定処理（registry 更新等）もメインスレッドで実行する。
                try:
                    job.result = job.settle(result, error)
                except Exception as se:
                    job.error = se
            elif error is not None:
                job.error = error
            else:
                job.result = result
            job.event.set()
            count += 1

    # ---- bpy.app.timers 連携（GUI 常駐）----

    def install_timer(
        self, interval: float = 0.02, on_tick: Callable[[], None] | None = None
    ) -> None:
        import bpy  # type: ignore  # lazy: bpy 依存をここに閉じ込める

        def _tick() -> float:
            # 生存印を tick の冒頭で更新する（M10 T10.3）。tick が発火したこと＝メインスレッドが
            # 動いている証拠。重量 op 中は pump→job が tick を占有して以後の tick が止まり、
            # 生存印が進まなくなる＝ウォッチドッグが unresponsive を検知できる。on_tick の例外は
            # pump を巻き添えにしないよう握り潰す。
            if on_tick is not None:
                try:
                    on_tick()
                except Exception:
                    pass
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
