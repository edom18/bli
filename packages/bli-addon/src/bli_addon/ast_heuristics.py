"""exec-python の AST ヒューリスティック flag（M11 T11.2・R-D）。spec §277。

ユーザコードを `ast` で走査し「注意喚起」を `heuristic_flags` に列挙する。**ブロックはしない**
＝実行可否は mode ゲート（policy）だけが決める。これは **安全保証ではなくヒューリスティック**なので
レスポンスの `security_guarantee` は常に false（spec §459 サンドボックス非提供）。off/audited の
ゲートとは独立。bpy 非依存＝pytest で検証できる。

検出対象（R-D「広く注記」）:
  - 注目モジュールの import（os/subprocess/socket/shutil/urllib/... ＝プロセス/ネットワーク/FS/シリアライズ）
    → `import:<top-module>`（`import os.path` / `from urllib.request import ...` は top の os/urllib で集約）。
  - 危険な組込み呼び出し eval/exec/compile/__import__ → `call:<name>`。
  - 書き込みモードの **組込み `open(...)`**（'w'/'a'/'x'/'+'） → `file-write`。
ヒューリスティックなので false negative はあり得る（属性経由・別名束縛・getattr 等は捕捉しない）。
特に `file-write` は **組込み `open` のみ**対象＝`io.open`/`pathlib.Path.write_text`/`os.open`/`shutil.*`
等は無印（ただし shutil/os は import flag で間接的に拾う）。`scan` は **決して例外を投げない**契約
（構文エラー/null byte 等は [] を返す＝ブロックしないし呼び出し元を壊さない）。
"""

from __future__ import annotations

import ast

# プロセス起動 / ネットワーク / ファイルシステム / 動的ロード / シリアライズなど、注意を促したい標準/著名モジュール。
NOTABLE_MODULES = frozenset(
    {
        "os",
        "subprocess",
        "sys",
        "shutil",
        "socket",
        "ctypes",
        "urllib",
        "http",
        "ftplib",
        "requests",
        "pickle",
        "marshal",
        "importlib",
        "multiprocessing",
        "pty",
        "tempfile",
        "glob",
    }
)
DANGEROUS_CALLS = frozenset({"eval", "exec", "compile", "__import__", "breakpoint"})
_WRITE_MODE_CHARS = frozenset("wax+")


def _is_write_open(call: ast.Call) -> bool:
    """`open(...)` が書き込みモードか（mode は 2番目の位置引数 or `mode=` キーワード）。

    mode 省略時は既定 "r"（読み取り）＝flag しない。定数文字列なら w/a/x/+ を含むかで判定。
    非定数（変数）の mode は書き込みの可能性があるため保守的に True とする。
    """
    mode_node: ast.expr | None = None
    if len(call.args) >= 2:
        mode_node = call.args[1]
    for kw in call.keywords:
        if kw.arg == "mode":
            mode_node = kw.value
    if mode_node is None:
        return False
    if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
        return any(c in _WRITE_MODE_CHARS for c in mode_node.value)
    return True  # mode が定数でない＝書き込みかもしれない（保守的に注記）


def scan(code: str) -> list[str]:
    """`code` を走査して heuristic_flags（ソート済み・重複排除）を返す。

    構文エラーは [] を返す（EXEC_ERROR(compile) が別途その不備を報告する）。**ブロックはしない**。
    """
    try:
        tree = ast.parse(code, mode="exec")
    except (SyntaxError, ValueError):
        # SyntaxError: 構文エラー / ValueError: null byte 等。scan は決して落ちない契約＝[] を返す
        # （EXEC_ERROR(compile) が別途その不備を報告する・flag はブロックしない）。
        return []
    flags: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in NOTABLE_MODULES:
                    flags.add(f"import:{top}")
        elif isinstance(node, ast.ImportFrom):
            # `from . import x`（相対）は module=None＝対象外。
            if node.module:
                top = node.module.split(".")[0]
                if top in NOTABLE_MODULES:
                    flags.add(f"import:{top}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in DANGEROUS_CALLS:
                flags.add(f"call:{node.func.id}")
            elif node.func.id == "open" and _is_write_open(node):
                flags.add("file-write")
    return sorted(flags)
