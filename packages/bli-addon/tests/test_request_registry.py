"""RequestRegistry のユニット（L1）。冪等性と TTL 掃除。"""

from __future__ import annotations

import time

from bli_addon.request_registry import RequestRegistry


def test_begin_new_then_in_progress_then_cached():
    reg = RequestRegistry(ttl=60.0)
    state, cached = reg.begin("rid")
    assert state == "new" and cached is None
    # 実行中の再送は in_progress
    assert reg.begin("rid")[0] == "in_progress"
    # 確定後は cached
    reg.complete("rid", {"ok": True}, ok=True)
    state, cached = reg.begin("rid")
    assert state == "cached"
    assert cached == {"ok": True}


def test_lookup_returns_state_and_result():
    reg = RequestRegistry(ttl=60.0)
    reg.complete("rid", {"v": 1}, ok=True)
    state, result = reg.lookup("rid")
    assert state == "DONE"
    assert result == {"v": 1}


def test_lookup_unknown_id():
    reg = RequestRegistry(ttl=60.0)
    assert reg.lookup("nope") == (None, None)


def test_lookup_purges_expired_terminal_entries():
    # 終端（DONE/FAILED）エントリは status ポーリング経路でも TTL 掃除される
    reg = RequestRegistry(ttl=0.05)
    reg.complete("rid", {"v": 1}, ok=True)
    assert reg.lookup("rid")[0] == "DONE"
    time.sleep(0.06)
    assert reg.lookup("rid") == (None, None)


def test_lookup_does_not_purge_running_entries():
    # 実行中（RUNNING）は TTL 超過でも保持し、冪等性を守る（Codex P2 指摘の修正）
    reg = RequestRegistry(ttl=0.05)
    assert reg.begin("rid")[0] == "new"  # RUNNING に遷移
    time.sleep(0.06)
    # ポーリングしても RUNNING は消えない
    assert reg.lookup("rid")[0] == "RUNNING"
    # 同一 id 再送は IN_PROGRESS のまま（変更操作を二重実行しない）
    assert reg.begin("rid")[0] == "in_progress"
    # 完走後は終端化し、以降は TTL 掃除の対象になる
    reg.complete("rid", {"v": 1}, ok=True)
    time.sleep(0.06)
    assert reg.lookup("rid") == (None, None)
