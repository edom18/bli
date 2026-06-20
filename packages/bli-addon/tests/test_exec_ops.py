"""exec-python ハンドラ（ops._exec_python・M11 T11.1）の L1 ユニット。

bpy 非依存の経路（mode ゲート・排他・no-escalation）は bpy 無しで検証する。trusted 実行は
gateway を sys.modules スタブに差し替えて bpy 無しで通す（実 bpy 経路は background smoke が担保）。

**R-A の核心**: mode の真実源は policy.toml であり、params 経由では昇格できないこと。
"""

from __future__ import annotations

import sys
import types

import pytest

from bli_addon import exec_runner, ops
from bli_addon.handlers import ServerInfo
from bli_core.errors import ErrorCode
from bli_core.protocol import JsonRpcError

INFO = ServerInfo("5.0.1-test", "deadbeef", ["wm.stl_export"])


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BLI_STATE_DIR", str(tmp_path))
    return tmp_path


def _write_policy(state_dir, mode: str):
    (state_dir / "policy.toml").write_text(f'[exec]\nmode = "{mode}"\n', encoding="utf-8")


def _install_fake_gateway(monkeypatch):
    """bpy を import しない gateway スタブを差し込む（exec_user_code は純 exec_runner で実行）。"""
    fake = types.ModuleType("bli_addon.gateway")
    fake.current_mode = lambda: "OBJECT"
    fake.exec_user_code = lambda code: (exec_runner.run_code(code, {}), "fakefp")
    monkeypatch.setitem(sys.modules, "bli_addon.gateway", fake)


# ---- mode ゲート（off / audited は fail-closed で拒否）----


def test_off_rejects_with_exec_disabled(state_dir):
    # policy 不在 → off → EXEC_DISABLED（bpy へ到達しない）。
    with pytest.raises(JsonRpcError) as ei:
        ops._exec_python({"code": "1 + 1"}, INFO)
    assert ei.value.message == ErrorCode.EXEC_DISABLED
    assert ei.value.data.category == "PRECONDITION"
    assert ei.value.data.retryable is False


def test_explicit_off_rejects(state_dir):
    _write_policy(state_dir, "off")
    with pytest.raises(JsonRpcError) as ei:
        ops._exec_python({"code": "1 + 1"}, INFO)
    assert ei.value.message == ErrorCode.EXEC_DISABLED


def test_audited_is_fail_closed_in_t111(state_dir):
    # audited は許可ハッシュゲート（T11.3）まで fail-closed で拒否する。
    _write_policy(state_dir, "audited")
    with pytest.raises(JsonRpcError) as ei:
        ops._exec_python({"code": "1 + 1"}, INFO)
    assert ei.value.message == ErrorCode.EXEC_DISABLED


# ---- R-A: params 経由で昇格できない ----


def test_mode_param_is_not_a_valid_param(state_dir):
    # サーバスキーマに mode は無い＝送れば INVALID_PARAMS。CLI フラグで mode を渡す経路が存在しない。
    with pytest.raises(JsonRpcError) as ei:
        ops._exec_python({"code": "1 + 1", "mode": "trusted"}, INFO)
    assert ei.value.message == ErrorCode.INVALID_PARAMS


def test_off_policy_beats_any_params(state_dir):
    # policy off のもとでは、どんな（妥当な）params でも実行されない。
    _write_policy(state_dir, "off")
    with pytest.raises(JsonRpcError) as ei:
        ops._exec_python({"code": "print('should not run')"}, INFO)
    assert ei.value.message == ErrorCode.EXEC_DISABLED


# ---- code/file 排他（bpy 到達前の USER_INPUT）----


def test_neither_code_nor_file_is_user_input(state_dir):
    with pytest.raises(JsonRpcError) as ei:
        ops._exec_python({}, INFO)
    assert ei.value.data.category == "USER_INPUT"


def test_both_code_and_file_is_user_input(state_dir):
    with pytest.raises(JsonRpcError) as ei:
        ops._exec_python({"code": "1", "file": "/tmp/x.py"}, INFO)
    assert ei.value.data.category == "USER_INPUT"


def test_empty_code_string_is_user_input(state_dir):
    # 空文字 code は「指定なし」と同じ扱い（排他チェックで弾く）。
    with pytest.raises(JsonRpcError) as ei:
        ops._exec_python({"code": "   "}, INFO)
    assert ei.value.data.category == "USER_INPUT"


# ---- trusted 実行（gateway スタブで bpy 無しに通す）----


def test_trusted_executes_and_returns_envelope(state_dir, monkeypatch):
    _write_policy(state_dir, "trusted")
    _install_fake_gateway(monkeypatch)
    result = ops._exec_python({"code": "a = 21\na * 2"}, INFO)
    assert result["success"] is True
    assert result["operation"] == "exec-python"
    assert result["data"]["mode"] == "trusted"
    assert result["data"]["result_repr"] == "42"
    # **常に false**（サンドボックスしない）。
    assert result["data"]["security_guarantee"] is False
    assert result["data"]["heuristic_flags"] == []  # 無害コードは flag なし
    assert result["fingerprint"] == "fakefp"


def test_trusted_populates_heuristic_flags(state_dir, monkeypatch):
    # T11.2: 危険な import/呼び出しは heuristic_flags に載る（ブロックはしない＝実行は成功）。
    # 実行は fake gateway 経由＝副作用を避けるため import のみ（os の import は無害）。
    _write_policy(state_dir, "trusted")
    _install_fake_gateway(monkeypatch)
    result = ops._exec_python({"code": "import os\nimport socket"}, INFO)
    assert result["success"] is True
    assert result["data"]["heuristic_flags"] == ["import:os", "import:socket"]
    assert result["data"]["security_guarantee"] is False


def test_trusted_captures_stdout(state_dir, monkeypatch):
    _write_policy(state_dir, "trusted")
    _install_fake_gateway(monkeypatch)
    result = ops._exec_python({"code": "print('from exec')"}, INFO)
    assert result["data"]["stdout"].strip() == "from exec"


def test_trusted_runtime_error_maps_to_exec_error(state_dir, monkeypatch):
    _write_policy(state_dir, "trusted")
    _install_fake_gateway(monkeypatch)
    with pytest.raises(JsonRpcError) as ei:
        ops._exec_python({"code": "raise ValueError('boom')"}, INFO)
    assert ei.value.message == ErrorCode.EXEC_ERROR
    assert ei.value.data.category == "ENVIRONMENT"


def test_trusted_syntax_error_is_user_input(state_dir, monkeypatch):
    _write_policy(state_dir, "trusted")
    _install_fake_gateway(monkeypatch)
    with pytest.raises(JsonRpcError) as ei:
        ops._exec_python({"code": "def (:\n pass"}, INFO)
    assert ei.value.message == ErrorCode.EXEC_ERROR
    assert ei.value.data.category == "USER_INPUT"  # compile フェーズ＝ユーザコードの不備


# ---- サーバ側 --file 読取（直接 RPC 用フォールバック）----


def test_file_not_found_is_user_input(state_dir, monkeypatch):
    # trusted でも、サーバ側 file 読取は存在チェックを bpy 到達前にする＝不在は USER_INPUT。
    _write_policy(state_dir, "trusted")
    _install_fake_gateway(monkeypatch)
    with pytest.raises(JsonRpcError) as ei:
        ops._exec_python({"file": str(state_dir / "does_not_exist.py")}, INFO)
    assert ei.value.data.category == "USER_INPUT"


def test_file_is_read_and_executed(state_dir, monkeypatch):
    # サーバ側 file 経路（直接 RPC）でスクリプトを読み実行できる。封じ込めは意図的に課さない
    # （trusted 前提ではユーザコードが open() で任意ファイルを読めるため confinement は無意味）。
    _write_policy(state_dir, "trusted")
    _install_fake_gateway(monkeypatch)
    script = state_dir / "script.py"
    script.write_text("print('from file')\n6 * 7", encoding="utf-8")
    result = ops._exec_python({"file": str(script)}, INFO)
    assert result["data"]["stdout"].strip() == "from file"
    assert result["data"]["result_repr"] == "42"
