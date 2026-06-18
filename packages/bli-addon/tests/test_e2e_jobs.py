"""M10 T10.1 非同期 job の E2E（L3）。CLI ⇄ サーバ（bpy 不要・heavy は sleep ジョブで模擬）。

heavy コマンドは accepted 即返し、実行中でも request-status（lock-free）が応答する（DoD: 接続が
塞がらない）。CLI 既定は auto-wait（sync 見え）/ `--async` は job_id を即返す。pump は別スレッドで回す
（本番は bpy.app.timers・background smoke は手動 pump 相当）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest
from typer.testing import CliRunner

from bli import client
from bli.main import app
from bli_addon import server as srv_mod
from bli_addon.dispatcher import ACCEPTED, Dispatcher
from bli_core.commands import get_command, is_heavy_request, load_definitions


def _start_job_server(tmp_path, monkeypatch, job_fn: Callable[[], dict]):
    """heaviness ルーティング executor + 別スレッド pump でサーバを起動する。stop() を返す。

    heavy コマンド（import 等）は submit_async + ACCEPTED、light は同期 submit。heavy の実体は
    job_fn（sleep 等で重量を模擬・bpy 不要）。本番 __init__._executor と同じ分岐を再現する。
    """
    monkeypatch.setenv("BLI_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("BLI_TOKEN", raising=False)
    monkeypatch.delenv("BLI_PORT", raising=False)
    load_definitions()
    dispatcher = Dispatcher()

    def executor(method, params, info, settle):
        cmd = get_command(method)
        if cmd is not None and is_heavy_request(cmd, params):
            dispatcher.submit_async(job_fn, settle=settle)
            return ACCEPTED
        return dispatcher.submit(
            lambda: {"success": True, "operation": method, "data": {}}, settle=settle
        )

    srv_mod.start(
        blender_version="5.0.1-test",
        schema_hash="deadbeef",
        capabilities=[],
        host="127.0.0.1",
        port=0,
        handler=executor,
    )
    stop = threading.Event()

    def _pump_loop():
        while not stop.is_set():
            dispatcher.pump()
            time.sleep(0.005)

    pumper = threading.Thread(target=_pump_loop, daemon=True, name="test-pump")
    pumper.start()

    def _stop():
        stop.set()
        srv_mod.stop()

    return _stop


def test_heavy_accepted_and_status_pollable_while_running(tmp_path, monkeypatch):
    """DoD: heavy ジョブ実行中（pump が塞がっている間）も request-status が応答する。"""
    started = threading.Event()
    release = threading.Event()

    def job_fn():
        started.set()
        release.wait(5.0)  # 重量処理を模擬（この間 pump はブロック）
        return {"success": True, "operation": "import", "data": {"count": 7}}

    stop = _start_job_server(tmp_path, monkeypatch, job_fn)
    try:
        # 1) heavy コマンドは accepted を即返す（job_id=request_id）。
        r, _ = client.call("import", {"format": "stl", "path": "x.stl"}, request_id="job-1")
        assert r.get("accepted") is True, r
        assert r.get("job_id") == "job-1", r

        # 2) ジョブが pump で走り出す（=メインスレッド相当がブロック）。
        assert started.wait(3.0), "job did not start"

        # 3) その実行中でも request-status（lock-free）は応答する＝接続が塞がらない（DoD の核心）。
        rs, _ = client.call("request-status", {"id": "job-1"})
        assert rs["data"]["state"] == "RUNNING", rs["data"]

        # 4) ジョブ解放 → 完了 → request-status が DONE + 元の結果を回収できる。
        release.set()
        deadline = time.monotonic() + 5.0
        state = None
        while time.monotonic() < deadline:
            rs2, _ = client.call("request-status", {"id": "job-1"})
            state = rs2["data"]["state"]
            if state == "DONE":
                break
            time.sleep(0.02)
        assert state == "DONE", state
        assert rs2["data"]["result"]["result"]["data"]["count"] == 7
    finally:
        release.set()
        stop()


@pytest.fixture
def fast_job_server(tmp_path, monkeypatch):
    """即完了する heavy ジョブのサーバ（CLI auto-wait / --async 検証用）。"""

    def job_fn():
        return {"success": True, "operation": "import", "data": {"imported": [], "count": 0}}

    stop = _start_job_server(tmp_path, monkeypatch, job_fn)
    try:
        yield
    finally:
        stop()


def test_cli_import_auto_waits_to_result(fast_job_server):
    """CLI 既定（auto-wait）: heavy import が accepted を経て最終結果まで自動待機して提示する（sync 見え）。"""
    runner = CliRunner()
    res = runner.invoke(app, ["import", "--format", "stl", "--path", "x.stl", "--json"])
    assert res.exit_code == 0, res.output
    import json

    payload = json.loads(res.output)
    assert payload["ok"] is True
    # accepted ではなく最終 domain result（operation=import）が提示される。
    assert payload.get("operation") == "import"
    assert payload.get("status") != "accepted"


def test_cli_import_async_returns_jobid(fast_job_server):
    """CLI --async: job_id を即返して終了する（fire-and-forget）。"""
    runner = CliRunner()
    res = runner.invoke(app, ["import", "--format", "stl", "--path", "x.stl", "--async", "--json"])
    assert res.exit_code == 0, res.output
    import json

    payload = json.loads(res.output)
    assert payload["status"] == "accepted"
    assert payload.get("job_id")


def test_cli_job_wait_retrieves_result(fast_job_server):
    """--async で受けた job_id を job-wait で待って最終結果を回収できる。"""
    runner = CliRunner()
    import json

    started = json.loads(
        runner.invoke(
            app, ["import", "--format", "stl", "--path", "x.stl", "--async", "--json"]
        ).output
    )
    job_id = started["job_id"]
    res = runner.invoke(app, ["job-wait", "--id", job_id, "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["ok"] is True
    assert payload.get("operation") == "import"


def test_cli_heavy_job_failure_maps_to_nonzero_exit(tmp_path, monkeypatch):
    """heavy job が失敗したら auto-wait は非ゼロ exit へ写像する（成功偽装を防ぐ）。"""

    def job_fn():
        raise RuntimeError("boom in heavy job")

    stop = _start_job_server(tmp_path, monkeypatch, job_fn)
    try:
        res = CliRunner().invoke(app, ["import", "--format", "stl", "--path", "x.stl", "--json"])
        assert res.exit_code != 0, res.output  # FAILED → 非ゼロ（INTERNAL→1）
    finally:
        stop()


def test_job_wait_unknown_id_fast_fails(fast_job_server):
    """未知/typo の job_id は即失敗する（30分ハングしない・全レビュー P1）。"""
    res = CliRunner().invoke(app, ["job-wait", "--id", "no-such-job", "--timeout", "5", "--json"])
    assert res.exit_code != 0, res.output
    assert res.exit_code != 2, "UNKNOWN は TIMEOUT_PENDING(2) ではなく FAILURE で即失敗"


def test_job_wait_timeout_returns_exit2(tmp_path, monkeypatch):
    """完了しない job を --timeout 内に取れなければ TIMEOUT_PENDING(exit2)。"""
    release = threading.Event()

    def job_fn():
        release.wait(10.0)
        return {"success": True, "operation": "import", "data": {}}

    stop = _start_job_server(tmp_path, monkeypatch, job_fn)
    try:
        import json

        runner = CliRunner()
        started = json.loads(
            runner.invoke(
                app, ["import", "--format", "stl", "--path", "x.stl", "--async", "--json"]
            ).output
        )
        res = runner.invoke(
            app, ["job-wait", "--id", started["job_id"], "--timeout", "1", "--json"]
        )
        assert res.exit_code == 2, res.output  # RUNNING のまま期限超過 → TIMEOUT_PENDING
    finally:
        release.set()
        stop()


def test_light_command_not_accepted(fast_job_server):
    """light コマンドは accepted を返さず同期結果になる（heavy ルーティングの誤適用なし）。"""
    r, _ = client.call("scene-info", {})
    assert r.get("accepted") is not True, r
