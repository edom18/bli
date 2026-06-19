"""レンダ中 busy フラグ（M10 T10.2・spec §7 line 336）の L1 テスト（bpy 不要）。

`render_state` の純Python 部（is_busy/mark_busy/mark_idle/reset）を検証する。render handler の
実発火（GUI 常駐）と @persistent 生存は GUI スパイク（render_spike.py・research §E12）/ background
smoke で別途検証する。
"""

from __future__ import annotations

import pytest

from bli_addon import render_state


@pytest.fixture(autouse=True)
def _reset():
    render_state.reset()
    yield
    render_state.reset()


def test_initial_state_is_idle():
    assert render_state.is_busy() is False


def test_mark_busy_then_idle():
    render_state.mark_busy()
    assert render_state.is_busy() is True
    render_state.mark_idle()
    assert render_state.is_busy() is False


def test_mark_busy_is_idempotent():
    render_state.mark_busy()
    render_state.mark_busy()
    assert render_state.is_busy() is True
    render_state.mark_idle()
    assert render_state.is_busy() is False


def test_reset_clears_busy():
    render_state.mark_busy()
    render_state.reset()
    assert render_state.is_busy() is False


def test_render_end_handler_clears_busy():
    # render_complete / render_cancel どちらも _on_render_end に集約＝busy を降ろす（取りこぼし防止）。
    render_state.mark_busy()
    render_state._on_render_end()  # render_cancel 相当
    assert render_state.is_busy() is False


def test_render_init_handler_sets_busy():
    render_state._on_render_init()
    assert render_state.is_busy() is True
