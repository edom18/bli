"""Dispatcher の settle コールバック挙動のユニット（L1）。

P1 修正の核: タイムアウトで submit が TimeoutPending を投げても、ジョブは queue に残り、
後続の pump で settle がメインスレッド相当で必ず呼ばれる（= 結果を registry へ反映できる）。
"""

from __future__ import annotations

import threading
import time

import pytest

from bli_addon.dispatcher import Dispatcher, TimeoutPending


def test_settle_called_on_late_completion():
    d = Dispatcher()
    captured: dict = {}

    def settle(result, error):
        captured["result"] = result
        captured["error"] = error
        return {"settled": result}

    # pump しないので submit はタイムアウトする
    with pytest.raises(TimeoutPending):
        d.submit(lambda: 42, timeout=0.05, settle=settle)
    assert "result" not in captured  # まだ実行されていない

    # 後から pump すると settle が呼ばれ、ジョブ結果が回収される
    d.pump()
    assert captured["result"] == 42
    assert captured["error"] is None


def test_settle_receives_error_from_job():
    d = Dispatcher()
    captured: dict = {}

    def boom():
        raise ValueError("boom")

    def settle(result, error):
        captured["error"] = error
        return {"ok": False}

    with pytest.raises(TimeoutPending):
        d.submit(boom, timeout=0.05, settle=settle)
    d.pump()
    assert isinstance(captured["error"], ValueError)


def test_submit_returns_settle_result_on_time():
    d = Dispatcher()

    def settle(result, error):
        return {"wrapped": result}

    box: dict = {}

    def worker():
        box["r"] = d.submit(lambda: 7, timeout=2.0, settle=settle)

    t = threading.Thread(target=worker)
    t.start()
    deadline = time.time() + 2.0
    while t.is_alive() and time.time() < deadline:
        d.pump()
        time.sleep(0.005)
    t.join(timeout=1.0)
    assert box["r"] == {"wrapped": 7}


def test_submit_without_settle_returns_raw_result():
    d = Dispatcher()
    box: dict = {}

    def worker():
        box["r"] = d.submit(lambda: 99, timeout=2.0)

    t = threading.Thread(target=worker)
    t.start()
    deadline = time.time() + 2.0
    while t.is_alive() and time.time() < deadline:
        d.pump()
        time.sleep(0.005)
    t.join(timeout=1.0)
    assert box["r"] == 99
