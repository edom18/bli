"""exec-python（逃げ道）実機確認（M11 T11.1 着手前スパイク / NEXT-M11 §3）。

`blender --background --python exec_spike.py` で実行（5.0.1 / 4.4.3 両版）。

exec は新しい timer/handler 機構を要さず既存 Dispatcher のメインスレッド直列で走る＝GUI スパイク不要。
ここで確認したいのは exec の実行セマンティクスのみ（research §E14）:
  (1) `exec(compile(code, ...), ns)` の namespace に `bpy` を注入すれば bpy.data.objects 等を触れるか。
  (2) stdout/stderr を `contextlib.redirect_stdout/redirect_stderr` でキャプチャできるか。
  (3) 失敗コードが投げる例外型（RuntimeError 以外も）を広く捕捉できるか（INTERNAL 化回避の裏付け）。
  (4) 「最終式の repr」（R-C）を ast 分割で取り出せるか
      （= 最後の文が式なら、それ以外を exec し最後の式だけ eval して repr する REPL 流儀）。
"""

import ast
import contextlib
import io

import bpy  # type: ignore


def report(label, fn):
    try:
        r = fn()
        print(f"[OK] {label}: {r}")
        return r
    except Exception as e:
        print(f"[ERR] {label}: {type(e).__name__}: {e}")
        return None


def run_user_code(code, namespace):
    """ユーザコードを実行し (stdout, stderr, last_repr, error) を返す本番候補ロジック。

    最後の文が式なら、その式だけ eval して repr を取り出す（REPL 流儀）。それ以外は None。
    例外は型を問わず捕捉して error に載せる（呼び出し側で EXEC_ERROR へ写像する想定）。
    """
    out, err = io.StringIO(), io.StringIO()
    last_repr = None
    error = None
    try:
        parsed = ast.parse(code, filename="<bli-exec>", mode="exec")
        last_expr = None
        if parsed.body and isinstance(parsed.body[-1], ast.Expr):
            last_expr = ast.Expression(parsed.body.pop().value)
        exec_code = compile(parsed, "<bli-exec>", "exec")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(exec_code, namespace)
            if last_expr is not None:
                value = eval(compile(last_expr, "<bli-exec>", "eval"), namespace)
                last_repr = repr(value)
    except BaseException as e:  # spike: 例外型の広さを観測する
        error = f"{type(e).__name__}: {e}"
    return out.getvalue(), err.getvalue(), last_repr, error


def main():
    print("=== BLI_EXEC_SPIKE_BEGIN ===")
    print("version", bpy.app.version_string)

    # (1) bpy 注入で bpy.data.objects を触れるか。namespace に bpy を入れて操作させる。
    def touch_bpy():
        ns = {"bpy": bpy}
        out, err, last, error = run_user_code(
            "names = [o.name for o in bpy.data.objects]\n"
            "print('objects:', names)\n"
            "len(names)",  # 最終式 → repr が取れるはず
            ns,
        )
        return {"stdout": out.strip(), "stderr": err, "last_repr": last, "error": error}

    report("(1) bpy 注入で objects を列挙＋最終式 repr", touch_bpy)

    # (1b) bpy で実際に mutate できるか（オブジェクトを移動）。
    def mutate_bpy():
        cube = bpy.data.objects.get("Cube")
        if cube is None:
            bpy.ops.mesh.primitive_cube_add(size=2.0)  # spike セットアップのみ
            cube = bpy.context.active_object
            cube.name = "Cube"
        ns = {"bpy": bpy}
        out, _err, last, error = run_user_code(
            "import bpy\n"
            "bpy.data.objects['Cube'].location.x = 5.0\n"
            "bpy.data.objects['Cube'].location.x",
            ns,
        )
        return {
            "stdout": out.strip(),
            "last_repr": last,
            "error": error,
            "actual_x": round(bpy.data.objects["Cube"].location.x, 4),
        }

    report("(1b) bpy で mutate（location.x=5）", mutate_bpy)

    # (2) stdout/stderr キャプチャ（print と sys.stderr 書き込み）。
    def capture_streams():
        ns = {}
        out, err, last, error = run_user_code(
            "import sys\nprint('hello stdout')\nprint('to stderr', file=sys.stderr)",
            ns,
        )
        return {"stdout": out.strip(), "stderr": err.strip(), "last_repr": last, "error": error}

    report("(2) stdout/stderr キャプチャ", capture_streams)

    # (3) 例外型を広く捕捉できるか。NameError / RuntimeError 相当 / KeyError。
    def exc_nameerror():
        _, _, _, error = run_user_code("undefined_name + 1", {})
        return error

    report("(3a) NameError", exc_nameerror)

    def exc_keyerror():
        _, _, _, error = run_user_code(
            "import bpy\nbpy.data.objects['NoSuchObject!!!']", {"bpy": bpy}
        )
        return error

    report("(3b) KeyError（存在しない object）", exc_keyerror)

    def exc_syntax():
        # compile 段でのみ起きる SyntaxError も run_user_code の try で捕まるはず。
        _, _, _, error = run_user_code("def (:\n  pass", {})
        return error

    report("(3c) SyntaxError（parse/compile 失敗）", exc_syntax)

    # (4) 最終式が無い（最後が代入文）の場合 last_repr=None。
    def no_last_expr():
        out, _err, last, error = run_user_code("x = 1 + 1\nprint(x)", {})
        return {"stdout": out.strip(), "last_repr": last, "error": error}

    report("(4) 最終文が代入 → last_repr=None", no_last_expr)

    print("=== BLI_EXEC_SPIKE_END ===")


if __name__ == "__main__":
    main()
