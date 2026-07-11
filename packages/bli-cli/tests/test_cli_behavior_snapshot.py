"""CLI 挙動（exit code / stdout / stderr / 送信 params）のスナップショット比較（P2-2 移行ガード）。"""

from __future__ import annotations

import pytest

import snapshot_lib
from behavior_cases import CASES


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return snapshot_lib.load_snapshot(snapshot_lib.BEHAVIOR_PATH)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
@pytest.mark.parametrize("json_out", [False, True], ids=["human", "json"])
def test_cli_behavior_matches_snapshot(case, json_out, snapshot):
    key = f"{case.id}|{'json' if json_out else 'human'}"
    assert key in snapshot, f"スナップショット未生成のケース: {key}。{snapshot_lib.REGEN_HINT}"
    actual = snapshot_lib.run_case(case, json_out=json_out)
    assert actual == snapshot[key], f"挙動が変わった: {key}。{snapshot_lib.REGEN_HINT}"


def test_no_orphan_snapshot_entries(snapshot):
    known = {f"{c.id}|{mode}" for c in CASES for mode in ("human", "json")}
    orphans = sorted(set(snapshot) - known)
    assert not orphans, f"ケース表に無いスナップショットが残っている: {orphans}。{snapshot_lib.REGEN_HINT}"
