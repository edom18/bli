"""CLI サーフェス（click 構造・ヘルプ文言）のスナップショット比較（P2-2 移行ガード）。"""

from __future__ import annotations

import snapshot_lib


def test_cli_surface_matches_snapshot():
    expected = snapshot_lib.load_snapshot(snapshot_lib.SURFACE_PATH)
    actual = snapshot_lib.surface_document()

    exp_cmds = expected["commands"]
    act_cmds = actual["commands"]
    assert sorted(act_cmds) == sorted(exp_cmds), (
        f"コマンド集合が変わった: +{sorted(set(act_cmds) - set(exp_cmds))} "
        f"-{sorted(set(exp_cmds) - set(act_cmds))}。{snapshot_lib.REGEN_HINT}"
    )
    # コマンドごとに比較して差分箇所を特定しやすくする
    for name in sorted(exp_cmds):
        assert act_cmds[name] == exp_cmds[name], (
            f"コマンド '{name}' のサーフェスが変わった。{snapshot_lib.REGEN_HINT}"
        )
