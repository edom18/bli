"""AST ヒューリスティック flag（M11 T11.2・R-D）の L1 ユニット（bpy 非依存）。

flag は **注意喚起のみ**でブロックしない。security_guarantee は別途常に false。
"""

from __future__ import annotations

from bli_addon import ast_heuristics


def test_benign_code_has_no_flags():
    assert ast_heuristics.scan("x = 1 + 2\nprint(x)") == []


def test_import_os():
    assert ast_heuristics.scan("import os") == ["import:os"]


def test_import_submodule_collapses_to_top():
    assert ast_heuristics.scan("import os.path") == ["import:os"]


def test_from_import_collapses_to_top():
    assert ast_heuristics.scan("from urllib.request import urlopen") == ["import:urllib"]


def test_subprocess_import():
    assert ast_heuristics.scan("import subprocess") == ["import:subprocess"]


def test_socket_import_is_network_signal():
    assert ast_heuristics.scan("import socket") == ["import:socket"]


def test_relative_import_is_ignored():
    # `from . import x` は module=None＝対象外（注目モジュールでもない）。
    assert ast_heuristics.scan("from . import helper") == []


def test_non_notable_import_not_flagged():
    assert ast_heuristics.scan("import math\nimport json") == []


def test_eval_call():
    assert ast_heuristics.scan("eval('1+1')") == ["call:eval"]


def test_exec_call():
    assert ast_heuristics.scan("exec('x=1')") == ["call:exec"]


def test_dunder_import_call():
    # __import__("os") は文字列引数＝import 文ではないので import:os は付かない。
    assert ast_heuristics.scan("__import__('os')") == ["call:__import__"]


def test_compile_call():
    assert ast_heuristics.scan("compile('1', '<x>', 'eval')") == ["call:compile"]


def test_open_write_mode():
    assert ast_heuristics.scan("open('f.txt', 'w')") == ["file-write"]


def test_open_append_mode():
    assert ast_heuristics.scan("open('f.txt', 'a')") == ["file-write"]


def test_open_mode_keyword():
    assert ast_heuristics.scan("open('f.txt', mode='x')") == ["file-write"]


def test_open_read_mode_not_flagged():
    assert ast_heuristics.scan("open('f.txt', 'r')") == []


def test_open_default_mode_not_flagged():
    # mode 省略は既定 "r"＝読み取り＝flag しない。
    assert ast_heuristics.scan("open('f.txt')") == []


def test_open_nonconstant_mode_is_conservative():
    # mode が変数＝書き込みかもしれないので保守的に file-write。
    assert ast_heuristics.scan("m = 'w'\nopen('f.txt', m)") == ["file-write"]


def test_multiple_flags_sorted_and_deduped():
    code = "import os\nimport os.path\nfrom subprocess import run\neval('1')\nopen('f','w')"
    assert ast_heuristics.scan(code) == [
        "call:eval",
        "file-write",
        "import:os",
        "import:subprocess",
    ]


def test_syntax_error_returns_empty():
    # 構文エラーは [] （EXEC_ERROR(compile) が別途報告・flag はブロックしない）。
    assert ast_heuristics.scan("def (:\n pass") == []


def test_null_byte_returns_empty_not_raises():
    # null byte は ast.parse が ValueError を投げる＝scan は決して落ちない契約で [] を返す。
    assert ast_heuristics.scan("x = 1\x00") == []


def test_breakpoint_call_flagged():
    assert ast_heuristics.scan("breakpoint()") == ["call:breakpoint"]


def test_nested_import_in_function_is_detected():
    # ast.walk は関数内/try 内のネストした import も拾う。
    assert ast_heuristics.scan("def f():\n    import os\n    return os") == ["import:os"]


def test_open_mode_keyword_overrides_positional():
    # 位置とキーワード両方指定なら mode= が後勝ち（write 判定）。
    assert ast_heuristics.scan("open('f', 'r', mode='w')") == ["file-write"]


def test_open_star_args_is_not_flagged():
    # open(*args) は mode を静的に決められない＝位置/キーワードに無く read 既定扱い（保守の境界）。
    assert ast_heuristics.scan("args = ('f', 'w')\nopen(*args)") == []


def test_eval_inner_code_not_inspected():
    # eval の中身の文字列（二次コード）は見ない＝call:eval のみ（設計前提の FN）。
    assert ast_heuristics.scan("eval('import os')") == ["call:eval"]


def test_dunder_import_with_real_import():
    # 実 import 文と __import__ 呼び出しが両方ある場合は両方の flag。
    assert ast_heuristics.scan("import socket\n__import__('os')") == [
        "call:__import__",
        "import:socket",
    ]
