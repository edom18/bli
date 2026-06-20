"""exec-python の実行メカニクス（M11 T11.1・純Python／bpy 非依存）。research §E14。

ユーザコード文字列を実行し `(stdout, stderr, result_repr, error)` を返す。`bpy` は **呼び出し側**
（gateway）が namespace に注入する＝この層は bpy に依存せず pytest で検証できる。

セマンティクスは研究 §E14 で 5.0.1/4.4.3 両版確定:
  - 最後の文が **式** なら、その式だけ eval して `repr` を取り出す（REPL 流儀の result_repr）。
    それ以外（代入等）は result_repr=None。**式の値が None なら result_repr も None**
    （REPL の sys.displayhook と同じ＝`print(...)` 等の noise を出さない）。
  - stdout / stderr を `contextlib.redirect_stdout/redirect_stderr` で分離キャプチャ。
  - 例外型は多様（NameError/KeyError/SyntaxError 等）＝広く捕捉し、呼び出し側で EXEC_ERROR へ
    写像する（INTERNAL 化しない）。SyntaxError は compile 段で起き "compile" フェーズとして報告する
    （=ユーザコードの構文エラー＝USER_INPUT 寄り）。実行段は **`except BaseException`**＝ユーザコードの
    `sys.exit()`（SystemExit）等がメインスレッドの dispatch を巻き込んで停止するのを防ぐ。
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import io
from typing import Any

FILENAME = "<bli-exec>"


@dataclasses.dataclass
class ExecOutcome:
    """exec の結果。error が None なら正常終了、そうでなければ例外で停止した。"""

    stdout: str
    stderr: str
    result_repr: str | None
    error: ExecError | None


@dataclasses.dataclass
class ExecError:
    """ユーザコードが投げた例外（型名・メッセージ・発生フェーズ）。"""

    type: str
    message: str
    phase: str  # "compile"（parse/compile 失敗＝SyntaxError 等）| "runtime"（実行時例外）


def run_code(code: str, namespace: dict[str, Any]) -> ExecOutcome:
    """`code` を `namespace` 上で実行し ExecOutcome を返す（例外は捕捉して error に載せる）。

    namespace は呼び出し側が用意する（gateway が `{"bpy": bpy, ...}` を渡す）。実行で更新された
    namespace はそのまま副作用として残る（bpy 経由のシーン変更は namespace 非依存）。
    """
    out, err = io.StringIO(), io.StringIO()
    result_repr: str | None = None

    # 1) parse + compile（SyntaxError 等はここで起き、実行前に "compile" フェーズで報告する）。
    try:
        parsed = ast.parse(code, filename=FILENAME, mode="exec")
        last_expr: ast.Expression | None = None
        if parsed.body and isinstance(parsed.body[-1], ast.Expr):
            # 最後の文が式なら、それだけ pop して eval 用に包み直す（REPL 流儀の result_repr）。
            last_expr = ast.Expression(parsed.body.pop().value)
        exec_code = compile(parsed, FILENAME, "exec")
        eval_code = compile(last_expr, FILENAME, "eval") if last_expr is not None else None
    except Exception as e:  # SyntaxError/ValueError 等を広く捕捉する（§E14）
        return ExecOutcome("", "", None, ExecError(type(e).__name__, str(e), "compile"))

    # 2) 実行（stdout/stderr をキャプチャ。最後の式があれば eval して repr を取る）。
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(exec_code, namespace)
            if eval_code is not None:
                value = eval(eval_code, namespace)
                # REPL の displayhook と同様に None は表示しない（print(...) 等の noise 抑制）。
                result_repr = repr(value) if value is not None else None
    except BaseException as e:
        # 実行時例外は **型を問わず** EXEC_ERROR へ写像する（§E14）。`except Exception` だと
        # ユーザコードの `sys.exit()`（SystemExit）/ KeyboardInterrupt が素通りし、サーバ側で
        # INTERNAL(code_bug) に化けるばかりか **メインスレッドの dispatch を巻き込んで停止** させ得る。
        # exec はメインスレッド直列なので、ここで握って EXEC_ERROR に倒すのが安全（観測性も維持）。
        return ExecOutcome(
            out.getvalue(), err.getvalue(), None, ExecError(type(e).__name__, str(e), "runtime")
        )

    return ExecOutcome(out.getvalue(), err.getvalue(), result_repr, None)
