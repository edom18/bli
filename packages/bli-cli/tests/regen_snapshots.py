"""CLI スナップショット（surface / behavior）を現行実装から再生成する。

実行: uv run python packages/bli-cli/tests/regen_snapshots.py
生成された diff は必ずレビューしてからコミットすること（意図しない挙動変更の検出が目的）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import snapshot_lib
from behavior_cases import CASES


def main() -> None:
    snapshot_lib.dump_snapshot(snapshot_lib.SURFACE_PATH, snapshot_lib.surface_document())
    print(f"wrote {snapshot_lib.SURFACE_PATH}")
    snapshot_lib.dump_snapshot(snapshot_lib.BEHAVIOR_PATH, snapshot_lib.behavior_document(CASES))
    print(f"wrote {snapshot_lib.BEHAVIOR_PATH}（cases={len(CASES)} × human/json）")


if __name__ == "__main__":
    main()
