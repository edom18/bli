"""`bli policy`（CLI ローカル・P1-1）の CliRunner テスト。

exec mode の真実源はサーバが読むユーザローカル policy.toml。このコマンドは RPC を一切送らず
（サーバに接続しない）表示/編集を助けるだけ＝ここではその読み書きのローカル挙動のみを検証する。
BLI_STATE_DIR を tmp_path に向けて policy.toml の実体を差し替える（test_policy.py と同じ流儀）。
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from bli.main import app

runner = CliRunner()


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BLI_STATE_DIR", str(tmp_path))
    return tmp_path


def _read_mode() -> str:
    from bli_core import policy as core_policy

    return core_policy.read_exec_mode()


def test_policy_show_missing_file_reports_off(state_dir):
    res = runner.invoke(app, ["policy", "--action", "show", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["mode"] == "off"
    assert data["exists"] is False
    assert data["allow_hashes_count"] == 0


def test_policy_show_human_output(state_dir):
    res = runner.invoke(app, ["policy", "--action", "show"])
    assert res.exit_code == 0
    assert "mode: off" in res.output


def test_policy_set_with_yes_writes_and_reads_back(state_dir):
    res = runner.invoke(
        app, ["policy", "--action", "set", "--mode", "restricted", "--yes", "--json"]
    )
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["previous_mode"] == "off"
    assert data["mode"] == "restricted"
    assert _read_mode() == "restricted"


def test_policy_set_without_yes_declines_by_default_does_not_write(state_dir):
    res = runner.invoke(
        app, ["policy", "--action", "set", "--mode", "trusted", "--json"], input="n\n"
    )
    assert res.exit_code == 1  # ABORTED（確認されなかった）
    assert _read_mode() == "off"  # 書き込まれていない


def test_policy_set_without_yes_confirmed_writes(state_dir):
    res = runner.invoke(
        app, ["policy", "--action", "set", "--mode", "trusted", "--json"], input="y\n"
    )
    assert res.exit_code == 0
    assert _read_mode() == "trusted"


def test_policy_set_no_input_aborts_gracefully(state_dir):
    # 非対話（EOF即時）でもクラッシュせず中断する（typer.Abort を捕まえる）。
    res = runner.invoke(app, ["policy", "--action", "set", "--mode", "trusted"], input="")
    assert res.exit_code == 1
    assert _read_mode() == "off"


def test_policy_set_preserves_allow_hashes(state_dir):
    (state_dir / "policy.toml").write_text(
        '[exec]\nmode = "audited"\nallow_hashes = ["abc123"]\n', encoding="utf-8"
    )
    res = runner.invoke(app, ["policy", "--action", "set", "--mode", "trusted", "--yes", "--json"])
    assert res.exit_code == 0
    from bli_core import policy as core_policy

    assert core_policy.read_allow_hashes() == frozenset({"abc123"})
    assert _read_mode() == "trusted"


def test_policy_set_rejects_unknown_top_level_section(state_dir):
    (state_dir / "policy.toml").write_text(
        '[exec]\nmode = "off"\n\n[server]\nport = 9876\n', encoding="utf-8"
    )
    res = runner.invoke(app, ["policy", "--action", "set", "--mode", "trusted", "--yes", "--json"])
    assert res.exit_code == 4
    assert _read_mode() == "off"  # 元のファイルは変更されない


def test_policy_set_rejects_unknown_exec_key(state_dir):
    (state_dir / "policy.toml").write_text(
        '[exec]\nmode = "off"\nsome_other_key = 1\n', encoding="utf-8"
    )
    res = runner.invoke(app, ["policy", "--action", "set", "--mode", "trusted", "--yes", "--json"])
    assert res.exit_code == 4
    assert _read_mode() == "off"


def test_policy_set_rejects_malformed_existing_file(state_dir):
    (state_dir / "policy.toml").write_text("this is not = valid toml [[[", encoding="utf-8")
    res = runner.invoke(app, ["policy", "--action", "set", "--mode", "trusted", "--yes", "--json"])
    assert res.exit_code == 4


def test_policy_set_missing_mode_is_input_error(state_dir):
    res = runner.invoke(app, ["policy", "--action", "set", "--yes", "--json"])
    assert res.exit_code == 4


def test_policy_bad_action_is_input_error(state_dir):
    res = runner.invoke(app, ["policy", "--action", "bogus", "--json"])
    assert res.exit_code == 4


def test_policy_bad_mode_is_input_error(state_dir):
    res = runner.invoke(app, ["policy", "--action", "set", "--mode", "bogus", "--yes", "--json"])
    assert res.exit_code == 4
    assert _read_mode() == "off"


def test_policy_is_discoverable_and_local_only():
    # SSOT に登録済み（list-commands / help から発見できる）。
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    names = {c["name"] for c in data["commands"]}
    assert "policy" in names
    schema = json.loads(runner.invoke(app, ["help", "--command", "policy", "--json"]).output)[
        "schema"
    ]
    assert set(schema["properties"]) == {"action", "mode", "yes"}
    assert schema["required"] == ["action"]
