"""help / list-commands（SSOT生成・ローカル完結）と送信前ローカル検証の CLI テスト（L1）。

addon に接続しない経路のみ。bad な入力は client.call より前に exit 4 で弾けること、
help/list-commands が SSOT から schema_hash 付きで生成されることを検証する。
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from bli.main import app

runner = CliRunner()


def test_list_commands_json():
    res = runner.invoke(app, ["list-commands", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert len(data["schema_hash"]) == 64
    names = {c["name"] for c in data["commands"]}
    assert {"ping", "set-origin", "scene-info", "request-status"} <= names
    so = next(c for c in data["commands"] if c["name"] == "set-origin")
    assert so["mutates"] is True
    assert so["required_mode"] == "OBJECT"


def test_help_all_json():
    res = runner.invoke(app, ["help", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert "set-origin" in data["commands"]
    assert data["commands"]["set-origin"]["title"] == "set-origin"


def test_help_one_json():
    res = runner.invoke(app, ["help", "--command", "set-origin", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["command"]["name"] == "set-origin"
    assert set(data["schema"]["required"]) == {"targets", "to"}


def test_help_unknown_command_exit_input():
    res = runner.invoke(app, ["help", "--command", "does-not-exist", "--json"])
    assert res.exit_code == 4


def test_local_validation_rejects_bad_enum_before_connect():
    # 不正な --to は送信前に exit 4（接続を試みない）
    res = runner.invoke(app, ["set-origin", "Cube", "--to", "bogus", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_schema_hash_matches_core():
    # CLI が出す schema_hash は bli-core の算出値と一致する
    from bli_core.commands import load_definitions
    from bli_core.schema import schema_hash

    res = runner.invoke(app, ["list-commands", "--json"])
    assert json.loads(res.output)["schema_hash"] == schema_hash(load_definitions())


def test_list_commands_excludes_unimplemented_by_default():
    # 発見系は未実装コマンド（transform/exec-python）を広告しない
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    names = {c["name"] for c in data["commands"]}
    assert "transform" not in names
    assert "exec-python" not in names
    assert "set-origin" in names


def test_list_commands_all_includes_unimplemented():
    data = json.loads(runner.invoke(app, ["list-commands", "--all", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "transform" in by_name
    assert by_name["transform"]["implemented"] is False


def test_help_excludes_unimplemented_by_default():
    data = json.loads(runner.invoke(app, ["help", "--json"]).output)
    assert "transform" not in data["commands"]
    data_all = json.loads(runner.invoke(app, ["help", "--all", "--json"]).output)
    assert "transform" in data_all["commands"]


def test_help_command_introspects_unimplemented():
    # 個別 introspection は未実装でも可（implemented=False を明示）
    res = runner.invoke(app, ["help", "--command", "transform", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["command"]["implemented"] is False
