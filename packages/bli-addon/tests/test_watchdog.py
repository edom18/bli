"""メインスレッド応答性ウォッチドッグ（M10 T10.3・spec §7 line 337）の L1 テスト（bpy 不要）。

`watchdog` の純Python 部（mark_alive / snapshot / _evaluate / start / stop）を検証する。
pump タイマの実発火（GUI 常駐）で生存印が止まることは GUI スパイク（watchdog_spike.py・
research §E13）/ background smoke で別途検証する。

判定はすべて last_pump_ts と閾値から計算されるため、実時間を待たずに last_pump_ts を過去へ
ずらして応答不能経路を再現する（重量 op で pump が止まった状態の模擬）。
"""

from __future__ import annotations

import time

import pytest

from bli_addon import watchdog
from bli_core import runtime


@pytest.fixture(autouse=True)
def _reset():
    watchdog.stop()
    watchdog.reset()
    # 直前のテストが start(threshold=...) で上書きした設定を既定へ戻す。
    watchdog._threshold = runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD
    watchdog._poll_interval = runtime.WATCHDOG_POLL_INTERVAL
    yield
    watchdog.stop()
    watchdog.reset()


def test_initial_state_is_responsive():
    # heartbeat 未受信（起動直後）は応答ありとみなす（誤検知しない）。
    snap = watchdog.snapshot()
    assert snap["responsive"] is True
    assert snap["unresponsive_since"] is None
    assert snap["last_pump_age"] is None


def test_mark_alive_is_responsive():
    watchdog.mark_alive()
    snap = watchdog.snapshot()
    assert snap["responsive"] is True
    assert snap["unresponsive_since"] is None
    assert snap["last_pump_age"] is not None
    assert snap["last_pump_age"] < 1.0
    assert watchdog.is_responsive() is True


def test_stale_heartbeat_is_unresponsive():
    # 生存印を閾値より十分過去へずらす＝pump が止まった状態。snapshot は監視スレッド無しでも
    # 読み取り時に応答不能を判定する（堅牢化）。
    watchdog._last_pump_ts = time.time() - (runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD + 40)
    snap = watchdog.snapshot()
    assert snap["responsive"] is False
    assert snap["unresponsive_since"] is not None
    assert snap["last_pump_age"] >= runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD
    assert watchdog.is_responsive() is False


def test_responsive_boundary_is_inclusive():
    # age <= threshold は responsive（境界は包含）。<= と < の取り違え回帰を固定する。
    th = runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD
    watchdog._last_pump_ts = time.time() - (th - 0.3)  # 閾値より僅かに新しい
    assert watchdog.snapshot()["responsive"] is True
    watchdog._last_pump_ts = time.time() - (th + 0.3)  # 閾値を僅かに超過
    assert watchdog.snapshot()["responsive"] is False


def test_snapshot_kind_when_unresponsive():
    # spec §8 の error kind を **ステータスラベル** として載せる（throw はしない＝通知のみ）。
    watchdog._last_pump_ts = time.time() - (runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD + 30)
    snap = watchdog.snapshot()
    assert snap["responsive"] is False
    assert snap["kind"] == "MAIN_THREAD_UNRESPONSIVE"


def test_snapshot_kind_none_when_responsive():
    watchdog.mark_alive()
    snap = watchdog.snapshot()
    assert snap["responsive"] is True
    assert snap["kind"] is None


def test_unresponsive_since_falls_back_without_monitor():
    # 監視スレッドが記録していなくても、snapshot は last + threshold を推定値として返す。
    last = time.time() - (runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD + 10)
    watchdog._last_pump_ts = last
    snap = watchdog.snapshot()
    assert snap["unresponsive_since"] == pytest.approx(
        last + runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD
    )


def test_evaluate_records_unresponsive_since():
    now = time.time()
    last = now - (runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD + 5)
    watchdog._last_pump_ts = last
    watchdog._evaluate(now)
    # 監視が「閾値を超えた時点」を起点として記録する。
    assert watchdog._unresponsive_since == pytest.approx(
        last + runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD
    )


def test_evaluate_does_not_flag_before_first_heartbeat():
    # last_pump_ts==0.0（heartbeat 未受信）の間は判定しない（誤検知防止）。
    watchdog._evaluate(time.time())
    assert watchdog._unresponsive_since is None


def test_evaluate_recovers_when_heartbeat_refreshed():
    now = time.time()
    watchdog._last_pump_ts = now - (runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD + 5)
    watchdog._evaluate(now)
    assert watchdog._unresponsive_since is not None
    # heartbeat が戻れば応答中へ回復し unresponsive_since が解除される。
    watchdog.mark_alive()
    watchdog._evaluate(time.time())
    assert watchdog._unresponsive_since is None
    assert watchdog.is_responsive() is True


def test_monitor_thread_detects_and_recovers():
    # 短い閾値/間隔で監視スレッドの検知→回復を実時間で検証する（1秒未満）。
    watchdog.start(threshold=0.2, poll_interval=0.05)
    watchdog.mark_alive()
    assert watchdog.is_responsive() is True
    time.sleep(0.4)  # 以後 mark_alive しない＝pump 停止の模擬
    snap = watchdog.snapshot()
    assert snap["responsive"] is False
    # 監視スレッドが unresponsive_since を記録している（snapshot の推定ではなく実記録）。
    assert watchdog._unresponsive_since is not None
    # heartbeat を戻すと回復する。
    watchdog.mark_alive()
    time.sleep(0.15)
    assert watchdog.is_responsive() is True
    assert watchdog._unresponsive_since is None


def test_start_is_idempotent():
    watchdog.start(threshold=5.0, poll_interval=0.1)
    first = watchdog._monitor
    watchdog.start(threshold=5.0, poll_interval=0.1)  # 再 start で二重起動しない
    assert watchdog._monitor is not first
    assert first is not None and not first.is_alive()


def test_stop_resets_state():
    watchdog.start(threshold=5.0, poll_interval=0.1)
    watchdog.mark_alive()
    watchdog.stop()
    assert watchdog._monitor is None
    snap = watchdog.snapshot()
    assert snap["last_pump_age"] is None  # reset 済み
