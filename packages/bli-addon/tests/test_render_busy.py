"""M10 T10.2 render busy 拒否の E2E（L3）。CLI client ⇄ サーバ（bpy 不要）。

レンダ中（render_busy=True）は **mutating または heavy** を dispatch 前に `BUSY_RENDERING` で即拒否し、
**read-only**（scene-info/list-objects/ping）と **lock-free**（request-status）は通す（観測性を維持）。
busy 判定は注入された callable で行うため、実レンダ（bpy）なしで経路全体を検証できる。
GUI での render handler 実発火は render_spike.py（research §E12）が担う。
"""

from __future__ import annotations

import pytest

from bli import client
from bli_addon import server as srv_mod
from bli_core.commands import load_definitions
from bli_core.errors import ErrorCode


def _start_server(tmp_path, monkeypatch, render_busy):
    """busy 判定を注入してサーバを起動する。executor は検証せず即成功（busy 判定は dispatch 前）。"""
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
        render_busy=render_busy,
    )
    return srv_mod.stop


def _busy_rendering_raised(method, params):
    with pytest.raises(client.RpcRemoteError) as ei:
        client.call(method, params)
    err = ei.value.error  # 完全なエラー dict（args[0] は message 文字列のみ）
    assert err["message"] == ErrorCode.BUSY_RENDERING, err
    # retryable な ENVIRONMENT エラーであること（CLI は exit 2 へ写像）。
    data = err.get("data") or {}
    assert data.get("retryable") is True, data


def test_mutating_command_rejected_during_render(tmp_path, monkeypatch):
    """mutating（transform）はレンダ中 BUSY_RENDERING で即拒否される。"""
    stop = _start_server(tmp_path, monkeypatch, render_busy=lambda: True)
    try:
        _busy_rendering_raised("transform", {"targets": ["Cube"], "location": [1, 0, 0]})
    finally:
        stop()


def test_heavy_nonmutating_command_rejected_during_render(tmp_path, monkeypatch):
    """heavy だが mutates=False の export もレンダ中は拒否される（heavy ブランチが mutating と独立）。"""
    stop = _start_server(tmp_path, monkeypatch, render_busy=lambda: True)
    try:
        _busy_rendering_raised("export", {"format": "stl", "path": "x.stl"})
    finally:
        stop()


def test_mesh_rejected_during_render(tmp_path, monkeypatch):
    """mesh はレンダ中に拒否される（mesh は mutates=True なので mutating ブランチで拒否）。

    注: heavy ブランチ（is_heavy / heavy_ops）を mutating と独立に検証するのは
    test_heavy_nonmutating_command_rejected_during_render（export＝mutates=False heavy=True）。
    """
    stop = _start_server(tmp_path, monkeypatch, render_busy=lambda: True)
    try:
        _busy_rendering_raised("mesh", {"op": "boolean", "targets": ["A"]})
    finally:
        stop()


def test_unknown_method_not_treated_as_busy(tmp_path, monkeypatch):
    """未知メソッドはレンダ中でも BUSY_RENDERING で弾かれず通常経路へ落ちる。

    get_command が None を返す＝不ブロック。ここでは dummy executor が通すので成功する
    （本番の実 executor では METHOD_NOT_FOUND になる）。要点は BUSY_RENDERING にならないこと。
    """
    stop = _start_server(tmp_path, monkeypatch, render_busy=lambda: True)
    try:
        r, _ = client.call("no-such-method", {})
        assert r.get("success") is True, r
    finally:
        stop()


def test_readonly_command_passes_during_render(tmp_path, monkeypatch):
    """read-only（scene-info）はレンダ中も通る＝観測性を維持する。"""
    stop = _start_server(tmp_path, monkeypatch, render_busy=lambda: True)
    try:
        r, _ = client.call("scene-info", {})
        assert r.get("success") is True, r
    finally:
        stop()


def test_ping_passes_during_render(tmp_path, monkeypatch):
    """ping（mutates=False メタ）はレンダ中も通る。"""
    stop = _start_server(tmp_path, monkeypatch, render_busy=lambda: True)
    try:
        r, _ = client.call("ping", {})
        assert r.get("success") is True, r
    finally:
        stop()


def test_request_status_passes_during_render(tmp_path, monkeypatch):
    """request-status（lock-free）はレンダ中も応答する＝決着の後追いができる。"""
    stop = _start_server(tmp_path, monkeypatch, render_busy=lambda: True)
    try:
        r, _ = client.call("request-status", {"id": "no-such-id"})
        assert r["data"]["known"] is False
    finally:
        stop()


def test_mutating_command_passes_when_not_rendering(tmp_path, monkeypatch):
    """レンダしていなければ mutating も通常どおり通る（busy 判定が誤爆しない）。"""
    stop = _start_server(tmp_path, monkeypatch, render_busy=lambda: False)
    try:
        r, _ = client.call("transform", {"targets": ["Cube"], "location": [1, 0, 0]})
        assert r.get("success") is True, r
    finally:
        stop()
