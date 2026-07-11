"""exec-python の restricted モード用 AST ブロックリスト検査（P1-1・設計レビュー 2026-07-11 G0）。

uLoop の Restricted 相当:「Blender API（bpy/bmesh/mathutils 等）は**全面許可**・事故を招く OS 操作
だけを静的に拒否」して、エージェントが都度生成するアドホックコードを確認なしで自走可能にする。
`ast_heuristics`（注意喚起のみ・ブロックしない・全モード共通）とは役割が異なる:
こちらは mode=restricted のときだけ使われ、**検出＝実行拒否**（EXEC_BLOCKED_RESTRICTED）。

ブロック対象（uLoop Restricted 相当・spec §276）:
  - プロセス起動/FFI/動的ロード/ネットワーク/シリアライズ実行系モジュールの import
  - `os` / `shutil` は import 自体は許可（os.path 等の正当用途が多い）し、プロセス起動・削除系の
    **属性呼び出し**だけを拒否（`import os as x` の別名は追跡・`from os import system` も拒否）
  - eval/exec/compile/__import__/breakpoint/input の呼び出し（input はメインスレッドを固める）
  - 組込み `open` の書き込みモード（読み取りは許可。書き出しは export/save コマンドを使う）

**静的検査は完全ではない**（getattr 迂回・文字列組み立て・多段の別名束縛・pathlib の unlink 等は
捕捉できない）。位置づけは「事故防止＋監査」であり悪意対策ではない（spec §459・
`security_guarantee:false` は不変）。悪意あるコードを防げるのは「実行させない運用」だけ。

`scan_blocked` は `ast_heuristics.scan` と同じく **決して例外を投げない**契約
（構文エラー/null byte は []＝この層では拒否理由なし。コンパイル不能は後段の EXEC_ERROR(compile)
が報告する）。bpy 非依存＝pytest 可。
"""

from __future__ import annotations

import ast

from .ast_heuristics import _is_write_open

# import そのものを拒否するモジュール（top-level 名）。プロセス起動 / 並行プロセス / FFI /
# 動的ロード / ネットワーク / リモート操作 / シリアライズ実行 / 端末。
BLOCKED_IMPORTS = frozenset(
    {
        "subprocess",
        "multiprocessing",
        "ctypes",
        "importlib",
        "socket",
        "ssl",
        "socketserver",
        "urllib",
        "http",
        "ftplib",
        "telnetlib",
        "smtplib",
        "poplib",
        "imaplib",
        "xmlrpc",
        "requests",
        "aiohttp",
        "websocket",
        "websockets",
        "paramiko",
        "pty",
        "pickle",
        "marshal",
        "shelve",
        "webbrowser",
    }
)

# import は許可しつつ、危険な属性呼び出しだけを拒否するモジュール → 拒否属性の集合。
_OS_PROCESS_ATTRS = (
    "system",
    "popen",
    "fork",
    "forkpty",
    "kill",
    "killpg",
    "abort",
    "_exit",
    "startfile",
    "posix_spawn",
    "posix_spawnp",
    # exec* / spawn* 系（全 variant を列挙。プレフィックス判定だと execvpe 追随漏れがない代わりに
    # 将来 os に exec で始まる無害 API が増えたとき誤ブロックするため、明示列挙にする）。
    "execl",
    "execle",
    "execlp",
    "execlpe",
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
)
_OS_DELETE_ATTRS = ("remove", "unlink", "rmdir", "removedirs")
BLOCKED_ATTRS: dict[str, frozenset[str]] = {
    "os": frozenset(_OS_PROCESS_ATTRS + _OS_DELETE_ATTRS),
    "shutil": frozenset({"rmtree"}),
}

# 動的実行系の組込み呼び出し。input はコンソール入力待ちで Blender メインスレッドを固めるため
# 事故防止として拒否（headless/GUI とも応答不能になる）。
BLOCKED_CALLS = frozenset({"eval", "exec", "compile", "__import__", "breakpoint", "input"})


def scan_blocked(code: str) -> list[str]:
    """restricted モードで実行を拒否すべき理由の一覧（ソート済み・重複排除）を返す。空なら自走可。

    返す理由の形式（remediation にそのまま載せられる自己記述形式）:
      `import:<module>` / `from-import:<module>.<name>` / `attr-call:<module>.<attr>` /
      `call:<builtin>` / `file-write`
    """
    try:
        tree = ast.parse(code, mode="exec")
    except (SyntaxError, ValueError):
        # 構文エラー等はこの層の拒否理由にしない（後段の EXEC_ERROR(compile) が報告する）。
        return []

    blocked: set[str] = set()

    # pass 1: import の拒否判定と、属性監視モジュール（os/shutil）の別名収集。
    # 別名収集を先に済ませてから呼び出しを見る（`x.system()` が `import os as x` より前の行に
    # 現れるコードはないが、ast.walk の訪問順は行順を保証しないため 2 pass にする）。
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in BLOCKED_IMPORTS:
                    blocked.add(f"import:{top}")
                if top in BLOCKED_ATTRS:
                    # `import os` → os / `import os as x` → x / `import os.path` → os を追跡。
                    aliases[alias.asname or top] = top
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                continue  # `from . import x`（相対）は対象外
            top = node.module.split(".")[0]
            if top in BLOCKED_IMPORTS:
                blocked.add(f"import:{top}")
            elif top in BLOCKED_ATTRS:
                # `from os import system` は Name 呼び出しになり属性追跡をすり抜けるため
                # import 時点で拒否する（`*` は個別判定不能なので保守的に拒否）。
                for alias in node.names:
                    if alias.name == "*" or alias.name in BLOCKED_ATTRS[top]:
                        blocked.add(f"from-import:{top}.{alias.name}")

    # pass 2: 呼び出しの拒否判定。
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in BLOCKED_CALLS:
                blocked.add(f"call:{func.id}")
            elif func.id == "open" and _is_write_open(node):
                blocked.add("file-write")
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            canonical = aliases.get(func.value.id)
            if canonical is not None and func.attr in BLOCKED_ATTRS[canonical]:
                blocked.add(f"attr-call:{canonical}.{func.attr}")

    return sorted(blocked)
