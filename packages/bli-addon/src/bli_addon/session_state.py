"""セッション変更追跡（open の未保存ガード用・M9 T9.4・研究 §E11）。

`bpy.data.is_dirty` は本番の dispatch（pump タイマ）文脈で **save しても False に戻らず**、
background では常時 True で、ガードの判定材料として信頼できない（§E11 で両版実測）。そこで
「bli が最後の save/open 以降に mutating コマンドを実行したか」を **自前で追跡**する:

  - mutates=True コマンドの成功 → modified（dirty）。
  - save / open の成功 → clean（saved）。

`open` は未保存変更があり `--force` でなければ E_PRECONDITION で拒否する。純Python（bpy 非依存＝
pytest で検証可能）。プロセスグローバル（=常駐 Blender 1 プロセスの bli 由来変更状態）として保持する。

スコープ（v1・methods.md 注記）: bli 由来の変更のみを追跡する。GUI で人間が直接行った編集や
Ctrl+S は追跡しない（=その種の未保存変更は open ガードの対象外）。
"""

from __future__ import annotations

_modified = False


def mark_modified() -> None:
    """mutating コマンドの成功時に呼ぶ（未保存変更あり＝dirty）。"""
    global _modified
    _modified = True


def mark_saved() -> None:
    """save / open の成功時に呼ぶ（ディスクと一致＝clean）。"""
    global _modified
    _modified = False


def is_modified() -> bool:
    """最後の save/open 以降に bli 由来の未保存変更があるか。"""
    return _modified


def reset() -> None:
    """テスト用の明示リセット（clean 状態へ）。"""
    global _modified
    _modified = False
