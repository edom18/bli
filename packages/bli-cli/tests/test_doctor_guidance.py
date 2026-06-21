"""doctor 導入ガイド（M14 T14.3）の単体テスト。

アドオン未到達時に状況別の導入ガイドを返すこと、到達時は空であることを確認する。
"""

from __future__ import annotations

from bli.main import _doctor_guidance


def test_reachable_has_no_guidance() -> None:
    assert _doctor_guidance(connection_exists=False, reachable=True) == []
    assert _doctor_guidance(connection_exists=True, reachable=True) == []


def test_unreachable_no_connection_suggests_install() -> None:
    lines = _doctor_guidance(connection_exists=False, reachable=False)
    assert lines
    text = "\n".join(lines)
    # 未導入の可能性として zip ビルド + Install from Disk を案内する。
    assert "build_addon.py" in text
    assert "Install from Disk" in text


def test_unreachable_with_connection_suggests_check_running() -> None:
    lines = _doctor_guidance(connection_exists=True, reachable=False)
    assert lines
    text = "\n".join(lines)
    # 接続情報はあるので、未導入ではなく稼働確認へ誘導する（zip ビルドは案内しない）。
    assert "build_addon.py" not in text
    assert "Blender" in text
