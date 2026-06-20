"""exec 監査ログ（M11 T11.3・spec §280）の L1 ユニット（bpy 非依存）。"""

from __future__ import annotations

import hashlib

import pytest

from bli_addon import audit


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BLI_STATE_DIR", str(tmp_path))
    return tmp_path


def test_code_sha256_matches_hashlib():
    code = "import bpy\nprint(1)"
    assert audit.code_sha256(code) == hashlib.sha256(code.encode("utf-8")).hexdigest()


def test_record_and_read_roundtrip(state_dir):
    entry = audit.make_entry(
        mode="trusted",
        decision="executed",
        source="code",
        code_sha256="abc",
        code_len=3,
        heuristic_flags=["import:os"],
    )
    assert audit.record(entry) is True
    rows = audit.read_entries()
    assert len(rows) == 1
    assert rows[0]["mode"] == "trusted"
    assert rows[0]["decision"] == "executed"
    assert rows[0]["heuristic_flags"] == ["import:os"]
    assert "ts" in rows[0]


def test_record_appends(state_dir):
    audit.record(audit.make_entry(mode="off", decision="rejected:off", source="code"))
    audit.record(audit.make_entry(mode="trusted", decision="executed", source="code"))
    rows = audit.read_entries()
    assert [r["decision"] for r in rows] == ["rejected:off", "executed"]


def test_read_entries_missing_is_empty(state_dir):
    assert audit.read_entries() == []


def test_record_failure_is_best_effort(state_dir, monkeypatch):
    # 書込失敗（OSError）でも例外を投げず False を返す（exec を止めない・可用性優先）。
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", boom)
    assert (
        audit.record(audit.make_entry(mode="trusted", decision="executed", source="code")) is False
    )


def test_make_entry_timestamp_is_iso_utc(state_dir):
    entry = audit.make_entry(mode="off", decision="rejected:off", source="code")
    assert entry.ts.endswith("+00:00")  # UTC ISO8601
