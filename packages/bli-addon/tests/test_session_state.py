"""セッション変更追跡（open の未保存ガード・M9 T9.4・研究 §E11）の L1 テスト（bpy 不要）。

session_state は純Python。`_premark_session_modified` は dispatch が mutating コマンドの実行 **前** に
呼ぶ pre-mark ロジックで、コマンド registry（純Python）だけを参照するため bpy なしで検証できる。
save/open 成功時の clean 化（mark_saved）は dispatch 内で bpy ハンドラ成功後に行うため smoke で検証する。
"""

from __future__ import annotations

import pytest

from bli_addon import ops, session_state


@pytest.fixture(autouse=True)
def _reset():
    session_state.reset()
    yield
    session_state.reset()


def test_initial_state_is_clean():
    assert session_state.is_modified() is False


def test_mark_modified_then_saved():
    session_state.mark_modified()
    assert session_state.is_modified() is True
    session_state.mark_saved()
    assert session_state.is_modified() is False


def test_premark_mutating_command_sets_modified():
    # transform は mutates=True → 実行前に modified になる（partial mutation でも安全側）。
    assert session_state.is_modified() is False
    ops._premark_session_modified("transform")
    assert session_state.is_modified() is True


def test_premark_non_mutating_command_keeps_clean():
    # scene-info は mutates=False → clean のまま。
    ops._premark_session_modified("scene-info")
    assert session_state.is_modified() is False


def test_premark_save_does_not_touch_flag():
    # save は clearing method＝pre-mark の対象外（clean 化は成功後に dispatch が行う）。
    session_state.mark_modified()
    ops._premark_session_modified("save")
    assert session_state.is_modified() is True


def test_premark_open_does_not_touch_flag():
    # open も clearing method＝pre-mark の対象外。
    session_state.mark_modified()
    ops._premark_session_modified("open")
    assert session_state.is_modified() is True


def test_premark_unknown_method_keeps_state():
    # registry に無いメソッド（ping 等のメタ）はフラグを変えない。
    session_state.mark_modified()
    ops._premark_session_modified("ping")
    assert session_state.is_modified() is True
