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


def test_lookup_purges_expired_entries():
    # status のみをポーリングする経路でも TTL 掃除が効く（Codex P2 指摘の修正）
    reg = RequestRegistry(ttl=0.05)
    reg.complete("rid", {"v": 1}, ok=True)
    assert reg.lookup("rid")[0] == "DONE"
    time.sleep(0.06)
    assert reg.lookup("rid") == (None, None)
