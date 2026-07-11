"""exec policy 読取（M11 T11.1・R-A）の L1 ユニット（bpy 非依存）。

mode の真実源はユーザローカル policy.toml。不在/不正は fail-closed で "off"。
"""

from __future__ import annotations

import pytest

from bli_addon import policy


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BLI_STATE_DIR", str(tmp_path))
    return tmp_path


def _write_policy(state_dir, text: str):
    (state_dir / policy.POLICY_FILENAME).write_text(text, encoding="utf-8")


def test_missing_policy_defaults_to_off(state_dir):
    # ファイルが無ければ無効（fail-closed）。
    assert policy.read_exec_mode() == "off"


def test_reads_trusted(state_dir):
    _write_policy(state_dir, '[exec]\nmode = "trusted"\n')
    assert policy.read_exec_mode() == "trusted"


def test_reads_restricted(state_dir):
    # restricted は P1-1 で追加された4つ目の mode（fail-closed の丸め先である off とは別）。
    _write_policy(state_dir, '[exec]\nmode = "restricted"\n')
    assert policy.read_exec_mode() == "restricted"


def test_reads_audited(state_dir):
    _write_policy(state_dir, '[exec]\nmode = "audited"\n')
    assert policy.read_exec_mode() == "audited"


def test_reads_off(state_dir):
    _write_policy(state_dir, '[exec]\nmode = "off"\n')
    assert policy.read_exec_mode() == "off"


def test_invalid_mode_value_falls_back_to_off(state_dir):
    # 未知の mode 値（typo/攻撃）は off へ丸める（fail-closed）。
    _write_policy(state_dir, '[exec]\nmode = "TRUSTED_PLEASE"\n')
    assert policy.read_exec_mode() == "off"


def test_malformed_toml_falls_back_to_off(state_dir):
    # パース不能な TOML は off（壊れた設定で昇格させない）。
    _write_policy(state_dir, "this is not = valid toml [[[")
    assert policy.read_exec_mode() == "off"


def test_missing_exec_section_falls_back_to_off(state_dir):
    _write_policy(state_dir, "[server]\nport = 9876\n")
    assert policy.read_exec_mode() == "off"


def test_exec_section_not_a_table_falls_back_to_off(state_dir):
    # [exec] が table でない（攻撃的入力）でも壊れず off。
    _write_policy(state_dir, 'exec = "trusted"\n')
    assert policy.read_exec_mode() == "off"


def test_policy_path_is_under_state_dir(state_dir):
    assert policy.policy_path() == state_dir / "policy.toml"


# ---- allow_hashes（T11.3・R-B）----


def test_allow_hashes_missing_is_empty(state_dir):
    assert policy.read_allow_hashes() == frozenset()


def test_allow_hashes_read_and_normalized(state_dir):
    _write_policy(state_dir, '[exec]\nmode = "audited"\nallow_hashes = ["ABC123", "def456"]\n')
    # 小文字へ正規化される。
    assert policy.read_allow_hashes() == frozenset({"abc123", "def456"})


def test_allow_hashes_non_list_is_empty(state_dir):
    _write_policy(state_dir, '[exec]\nallow_hashes = "abc"\n')
    assert policy.read_allow_hashes() == frozenset()


def test_allow_hashes_ignores_non_string_items(state_dir):
    _write_policy(state_dir, '[exec]\nallow_hashes = ["abc", 123, true]\n')
    assert policy.read_allow_hashes() == frozenset({"abc"})


def test_allow_hashes_malformed_toml_is_empty(state_dir):
    _write_policy(state_dir, "broken [[[")
    assert policy.read_allow_hashes() == frozenset()
