"""M10 T10.3 ウォッチドッグ観測性の E2E（L3）。CLI client ⇄ サーバ（bpy 不要）。

メインスレッド応答性（watchdog.snapshot）が **lock-free な request-status** 応答に載ることを検証する。
これにより、固まった heavy job を job-wait でポーリング中のエージェントが「進行中だが固まっている」を
観測できる（request-status は受信スレッド処理＝メインを待たない）。watchdog 判定は注入された callable で
行うため、実 timer（bpy）なしで経路全体を検証できる。GUI での pump 停止の実観測は watchdog_spike.py
（research §E13）が担う。
"""

from __future__ import annotations

import time

from bli import client
from bli_addon import server as srv_mod
from bli_addon import watchdog
from bli_core.commands import load_definitions


def _start_server(tmp_path, monkeypatch, watchdog_status=None):
    monkeypatch.setenv("BLI_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("BLI_TOKEN", raising=False)
    monkeypatch.delenv("BLI_PORT", raising=False)
    load_definitions()

    def executor(method, params, info, settle):
        return settle({"success": True, "operation": method, "data": {}}, None)

    srv_mod.start(
        blender_version="5.0.1-test",
        schema_hash="deadbeef",
        capabilities=[],
        host="127.0.0.1",
        port=0,
        handler=executor,
        watchdog_status=watchdog_status,
    )
    return srv_mod.stop


def test_request_status_includes_watchdog_responsive(tmp_path, monkeypatch):
    stop = _start_server(
        tmp_path,
        monkeypatch,
        watchdog_status=lambda: {
            "responsive": True,
            "unresponsive_since": None,
            "last_pump_age": 0.01,
        },
    )
    try:
        r, _ = client.call("request-status", {"id": "no-such-id"})
        wd = r["data"]["watchdog"]
        assert wd["responsive"] is True
        assert wd["unresponsive_since"] is None
    finally:
        stop()


def test_request_status_includes_watchdog_unresponsive(tmp_path, monkeypatch):
    stop = _start_server(
        tmp_path,
        monkeypatch,
        watchdog_status=lambda: {
            "responsive": False,
            "unresponsive_since": 123.0,
            "last_pump_age": 99.0,
        },
    )
    try:
        r, _ = client.call("request-status", {"id": "no-such-id"})
        wd = r["data"]["watchdog"]
        assert wd["responsive"] is False
        assert wd["unresponsive_since"] == 123.0
        assert wd["last_pump_age"] == 99.0
    finally:
        stop()


def test_request_status_default_watchdog_is_responsive(tmp_path, monkeypatch):
    # 注入なし（既定＝GUI 非常駐/テスト）は responsive を返す（server は bpy 非依存のまま）。
    stop = _start_server(tmp_path, monkeypatch, watchdog_status=None)
    try:
        r, _ = client.call("request-status", {"id": "no-such-id"})
        assert r["data"]["watchdog"]["responsive"] is True
    finally:
        stop()


def test_request_status_reports_real_watchdog_snapshot(tmp_path, monkeypatch):
    # 本番 watchdog.snapshot を注入し、生存印を過去へずらした状態（pump 停止の模擬）が
    # request-status 応答に unresponsive として現れることを end-to-end で検証する。
    watchdog.reset()
    watchdog._last_pump_ts = time.time() - 1000.0
    stop = _start_server(tmp_path, monkeypatch, watchdog_status=watchdog.snapshot)
    try:
        r, _ = client.call("request-status", {"id": "no-such-id"})
        wd = r["data"]["watchdog"]
        assert wd["responsive"] is False
        assert wd["unresponsive_since"] is not None
        # spec §8 の kind が観測系に載る（throw ではなくステータスラベル）。
        assert wd["kind"] == "MAIN_THREAD_UNRESPONSIVE"
    finally:
        watchdog.reset()
        stop()
