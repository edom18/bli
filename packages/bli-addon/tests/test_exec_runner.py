"""exec_runner（exec 実行メカニクス・M11 T11.1）の L1 ユニット（bpy 非依存）。

研究 §E14 で 5.0.1/4.4.3 両版確定したセマンティクスを純Python で検証する。
namespace に bpy は注入しない（ここでは exec の機構＝stdout キャプチャ/最終式 repr/例外写像のみ）。
"""

from __future__ import annotations

from bli_addon import exec_runner


def test_stdout_capture():
    out = exec_runner.run_code("print('hello')", {})
    assert out.error is None
    assert out.stdout.strip() == "hello"
    assert out.stderr == ""
    # print(...) は式文だが戻り値 None ＝ displayhook 流儀で result_repr は抑制される（'None' を出さない）。
    assert out.result_repr is None


def test_none_value_is_suppressed():
    # 末尾式が None に評価される場合は repr('None') ではなく None（REPL の displayhook と同じ）。
    out = exec_runner.run_code("None", {})
    assert out.error is None
    assert out.result_repr is None
    out2 = exec_runner.run_code("(lambda: None)()", {})
    assert out2.result_repr is None


def test_falsy_nonnull_value_is_reported():
    # 0 や "" は None ではないので抑制しない（is not None 判定が正しいことの確認）。
    assert exec_runner.run_code("0", {}).result_repr == "0"
    assert exec_runner.run_code("''", {}).result_repr == "''"


def test_stderr_capture():
    out = exec_runner.run_code("import sys\nprint('boom', file=sys.stderr)", {})
    assert out.error is None
    assert out.stderr.strip() == "boom"
    assert out.stdout == ""


def test_last_expression_repr():
    # 最後の文が式なら repr が取れる（REPL 流儀）。
    out = exec_runner.run_code("a = 2\nb = 3\na * b", {})
    assert out.error is None
    assert out.result_repr == "6"


def test_assignment_last_has_no_repr():
    out = exec_runner.run_code("x = 1 + 1", {})
    assert out.error is None
    assert out.result_repr is None


def test_namespace_is_mutated():
    ns = {}
    exec_runner.run_code("created = 42", ns)
    assert ns.get("created") == 42


def test_namespace_injection_visible_to_code():
    ns = {"injected": 10}
    out = exec_runner.run_code("injected + 5", ns)
    assert out.result_repr == "15"


def test_runtime_error_is_captured_not_raised():
    out = exec_runner.run_code("undefined_name + 1", {})
    assert out.error is not None
    assert out.error.type == "NameError"
    assert out.error.phase == "runtime"


def test_syntax_error_is_compile_phase():
    out = exec_runner.run_code("def (:\n  pass", {})
    assert out.error is not None
    assert out.error.type == "SyntaxError"
    assert out.error.phase == "compile"


def test_system_exit_is_captured_not_raised():
    # ユーザコードの sys.exit() がメインスレッドの dispatch を巻き込まないこと（BaseException 捕捉）。
    out = exec_runner.run_code("import sys\nsys.exit(3)", {})
    assert out.error is not None
    assert out.error.type == "SystemExit"
    assert out.error.phase == "runtime"


def test_keyboard_interrupt_is_captured_not_raised():
    out = exec_runner.run_code("raise KeyboardInterrupt", {})
    assert out.error is not None
    assert out.error.type == "KeyboardInterrupt"
    assert out.error.phase == "runtime"


def test_stdout_preserved_before_runtime_error():
    # 例外直前の stdout はキャプチャされ観測性を失わない。
    out = exec_runner.run_code("print('before')\nraise ValueError('nope')", {})
    assert out.error is not None
    assert out.error.type == "ValueError"
    assert out.stdout.strip() == "before"


def test_empty_code_is_noop():
    out = exec_runner.run_code("", {})
    assert out.error is None
    assert out.result_repr is None
    assert out.stdout == ""
