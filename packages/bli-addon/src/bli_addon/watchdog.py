"""メインスレッド応答性ウォッチドッグ（M10 T10.3・spec §7 line 337）。

重量ネイティブ処理（boolean/decimate/import/print-check の C 内部）は bpy のメインスレッドを
**1回の blocking 呼び出しで占有し、中断もチャンク化もできない**（spec §7 残存リスク）。その間
pump タイマ（dispatcher.install_timer）は発火できず、メインスレッドは新しいジョブを処理できない
＝「固まっている」。ウォッチドッグはこれを **検知して観測可能にする**（実行は止めない・kill しない＝
通知のみ。重量ネイティブ op は中断不能なので安全に止められない）。

仕組み:
  - pump タイマが毎 tick で生存印 ``last_pump_ts`` を更新する（メインスレッドが動いている証拠）。
    重量 op 中は tick が発火しないため last_pump_ts が進まない＝固まりの信号。
  - 別スレッド（監視）が定期的に「今 − last_pump_ts > 閾値」を判定し、unresponsive フラグと
    ``unresponsive_since`` を更新する（＋初回検知をログ通知）。
  - 受信スレッドが lock-free に ``snapshot()`` を読み、request-status / doctor 応答へ載せる
    （メインスレッドを待たない＝固まっていても観測できる）。

**GUI スパイク（research §E13・5.0.1/4.4.3 両版）で確定する前提**:
  - bpy.app.timers は GUI 常駐でのみ発火する（``--background`` では発火しない）。
  - メインスレッドが重量 op で固まると pump タイマが止まり last_pump_ts が進まない
    ＝別スレッドの監視が unresponsive を観測できる。

純Python（threading + time のみ・bpy 非依存）＝pytest で検証可能。``mark_alive`` は pump tick
（メインスレッド）から、``snapshot`` は受信スレッドから、監視ループは専用スレッドから呼ばれる。
プロセスグローバル（=常駐 Blender 1 プロセス）として状態を保持する（render_state と同流儀）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from bli_core import runtime
from bli_core.errors import ErrorCode

# 状態。last_pump_ts は単一 float＝GIL 下で読み書きアトミック（mark_alive はホットパスのため
# ロックを取らない）。複合状態（unresponsive_since とログ済みフラグ）は _lock で保護する。
_lock = threading.Lock()
_last_pump_ts: float = 0.0  # 最後に pump tick が発火した時刻（0.0 = 未起動/heartbeat 未受信）
_unresponsive_since: float | None = None  # 監視が unresponsive を検知した起点（応答中は None）

# 設定（start で runtime 既定から上書き可能・スパイク/テストは短い値を渡す）。
_threshold: float = runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD
_poll_interval: float = runtime.WATCHDOG_POLL_INTERVAL

# 監視スレッド。
_monitor: threading.Thread | None = None
_stop = threading.Event()


def mark_alive() -> None:
    """生存印を更新する（pump tick からメインスレッドが呼ぶ）。

    単一 float の代入＝GIL 下でアトミック。ロックを取らない（毎 tick=20ms 程度のホットパス）。
    監視ループが last_pump_ts を読んで応答性を判定する。
    """
    global _last_pump_ts
    _last_pump_ts = time.time()


def snapshot() -> dict[str, Any]:
    """現在の応答性状態を返す（受信スレッドが lock-free に読む＝メインを待たない）。

    監視スレッドが動いていなくても last_pump_ts から **読み取り時に** 応答性を判定するため、
    監視のポーリング間隔に依存せず正しい値を返す（堅牢化）。監視スレッドは unresponsive_since の
    正確な記録とログ通知を担う。

    返却:
      responsive          : メインスレッドが応答中か（last_pump_ts が閾値内に更新されているか）。
      unresponsive_since   : 応答不能に陥った時刻（応答中は None）。
      last_pump_age        : 最後の生存印からの経過秒（heartbeat 未受信は None）。
      threshold            : 判定閾値（秒）。
      kind                 : 応答不能時は ErrorCode.MAIN_THREAD_UNRESPONSIVE（spec §8 のラベル）。応答中は None。
    """
    now = time.time()
    with _lock:
        last = _last_pump_ts
        since = _unresponsive_since
        threshold = _threshold
    age = None if last == 0.0 else max(0.0, now - last)
    # heartbeat 未受信（起動直後）は応答ありとみなす（誤検知しない）。
    responsive = age is None or age <= threshold
    unresp: float | None = None
    kind: str | None = None
    if not responsive:
        # 監視スレッドが記録した検知時刻を優先。未記録（閾値超過直後でまだポーリングしていない）なら
        # 「閾値を超えた時点」= last + threshold を推定値として返す（監視の有無に依存しない）。
        unresp = since if since is not None else last + threshold
        # spec §8 の error kind を **ステータスラベル** として載せる（throw はしない＝通知のみ）。
        # エージェントが MAIN_THREAD_UNRESPONSIVE で観測できるようにする。
        kind = ErrorCode.MAIN_THREAD_UNRESPONSIVE
    return {
        "responsive": responsive,
        "unresponsive_since": unresp,
        "last_pump_age": age,
        "threshold": threshold,
        "kind": kind,
    }


def is_responsive() -> bool:
    """メインスレッドが応答中か（snapshot の真偽だけが欲しいときの薄いヘルパ）。"""
    return bool(snapshot()["responsive"])


def _monitor_loop() -> None:
    """別スレッド: 定期的に応答性を判定し、unresponsive_since の記録とログ通知を行う。"""
    while not _stop.wait(_poll_interval):
        _evaluate(time.time())


def _evaluate(now: float) -> None:
    """1回ぶんの判定（監視ループ本体・テストから直接呼べるよう分離）。

    last_pump_ts を読み、閾値超過なら unresponsive_since を記録（初回はログ）、回復なら解除する。
    last_pump_ts が 0.0（heartbeat 未受信）の間は判定しない（誤検知防止）。
    """
    global _unresponsive_since
    with _lock:
        last = _last_pump_ts
        if last == 0.0:
            return
        threshold = _threshold
        age = now - last
        if age > threshold:
            if _unresponsive_since is None:
                _unresponsive_since = last + threshold
                first = True
            else:
                first = False
        else:
            if _unresponsive_since is not None:
                # 回復: 応答不能と判定していた期間を算出してから解除する。
                frozen = max(0.0, now - _unresponsive_since)
                _unresponsive_since = None
                _notify_recovered(frozen)
            return
    if first:
        _notify_unresponsive(age, threshold)


def _notify_unresponsive(age: float, threshold: float) -> None:
    """初回検知のログ通知（実行は止めない＝観測性のみ）。実際に発火した閾値を表示する。"""
    print(
        f"[bli watchdog] {threshold:.0f}s 以上 pump が停止"
        f"（メインスレッドが重量処理で固まっている可能性・経過 {age:.0f}s）。実行は継続中。",
        flush=True,
    )


def _notify_recovered(frozen: float) -> None:
    """応答回復のログ通知（応答不能だった概算時間を表示する）。"""
    print(
        f"[bli watchdog] メインスレッドが応答を回復しました（約 {frozen:.0f}s 応答なし）。",
        flush=True,
    )


def start(threshold: float | None = None, poll_interval: float | None = None) -> None:
    """監視スレッドを起動する（GUI 常駐の register から呼ぶ）。冪等（既存があれば先に停止）。

    threshold / poll_interval を渡すと既定（runtime）を上書きする（スパイク/テストは短い値で速める）。
    """
    global _monitor, _threshold, _poll_interval
    if _monitor is not None:
        stop()
    with _lock:
        _threshold = threshold if threshold is not None else runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD
        _poll_interval = (
            poll_interval if poll_interval is not None else runtime.WATCHDOG_POLL_INTERVAL
        )
    _stop.clear()
    t = threading.Thread(target=_monitor_loop, name="bli-watchdog", daemon=True)
    _monitor = t
    t.start()


def stop() -> None:
    """監視スレッドを停止し状態をリセットする。"""
    global _monitor
    _stop.set()
    t = _monitor
    _monitor = None
    if t is not None:
        t.join(timeout=2.0)
    reset()


def reset() -> None:
    """状態を初期化する（テスト/再起動用）。heartbeat と unresponsive_since をクリア。"""
    global _last_pump_ts, _unresponsive_since
    with _lock:
        _last_pump_ts = 0.0
        _unresponsive_since = None
