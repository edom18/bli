#!/usr/bin/env python3
"""生 `bpy.ops.*()` 呼び出しを禁止する AST チェッカ（plan.md §1.3 / T0.5）。

bli-addon 配下で `bpy.ops.<ns>.<op>(...)` を直接呼ぶことを禁止する。
operator 実行は必ず `run_operator()` ラッパ（BpyGateway）経由とする。
（戻り値 set 判定漏れ・context 依存・undo 境界の崩れを防ぐため）

許可:
- ラッパ定義ファイル（既定 `gateway.py`）。`--allow` で追加可。
- ラッパ定義ディレクトリ（既定 `bli_addon/gateway`＝gateway.py をパッケージへ分割した配下）。
  `--allow-dir` で追加可。**走査ルートからの相対パスの先頭一致（prefix）**で判定する。
  別位置の同名ディレクトリ（例: `ops/gateway/`）やネスト再現
  （例: `x/bli_addon/gateway/`）を誤って免除しないため。
- `spikes/` 配下（実験コード）。
- `bpy.app.timers.*` / `bpy.data.*` 等、`bpy.ops` 以外は対象外。

使い方:
    python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
    python scripts/check_no_raw_bpy_ops.py <path...> --allow gateway.py
    python scripts/check_no_raw_bpy_ops.py <path...> --allow-dir bli_addon/gateway
終了コード: 違反あり=1 / なし=0
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

DEFAULT_ALLOW = {"gateway.py"}
DEFAULT_ALLOW_DIRS = {"bli_addon/gateway"}
SKIP_DIR_PARTS = {"spikes", "vendored", "__pycache__", "tests"}


def _allow_dir_segments(allow_dirs: set[str]) -> list[tuple[str, ...]]:
    return [
        segs
        for spec in allow_dirs
        if (segs := tuple(s for s in spec.replace("\\", "/").split("/") if s))
    ]


def _matches_allow_dir(rel_parts: tuple[str, ...], allow_dirs: set[str]) -> bool:
    """走査ルートからの相対パスが、許可ディレクトリのセグメント列で**始まる**か。

    裸名の集合演算（`set(parts) & allow_dirs`）は任意階層の同名ディレクトリを、
    連続部分列一致は `x/bli_addon/gateway/` のようなネスト再現を、それぞれ丸ごと
    免除してしまう。走査ルート起点の先頭一致（prefix）に固定して両方を塞ぐ。
    """
    return any(rel_parts[: len(segs)] == segs for segs in _allow_dir_segments(allow_dirs))


def _parent_suffix_matches(parent_parts: tuple[str, ...], allow_dirs: set[str]) -> bool:
    """親ディレクトリのパス構成要素が、許可ディレクトリのセグメント列で**終わる**か。

    単一ファイル指定には走査ルートが無く prefix 判定が適用できないため、
    こちらで判定する（ディレクトリ走査のデコイ検出は prefix 判定が担う）。
    """
    return any(parent_parts[-len(segs) :] == segs for segs in _allow_dir_segments(allow_dirs))


def _attr_root_is_bpy_ops(node: ast.Attribute) -> bool:
    """属性チェーンの根が `bpy.ops` で始まるか判定する。"""
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()  # 例: ['bpy', 'ops', 'object', 'origin_set']
    return len(parts) >= 2 and parts[0] == "bpy" and parts[1] == "ops"


class _RawOpsVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and _attr_root_is_bpy_ops(func):
            self.hits.append((node.lineno, ast.unparse(func)))
        self.generic_visit(node)


def _iter_py_files(paths: list[str]) -> list[tuple[Path | None, Path]]:
    """(走査ルート, ファイル) の組で列挙する（allow-dir のルート相対判定に使う）。

    単一ファイル指定には走査ルートが無いため root=None を返す。
    """
    files: list[tuple[Path | None, Path]] = []
    for p in paths:
        root = Path(p)
        if root.is_file() and root.suffix == ".py":
            files.append((None, root))
        else:
            files.extend((root, f) for f in root.rglob("*.py"))
    return files


def check(paths: list[str], allow: set[str], allow_dirs: set[str] | None = None) -> list[str]:
    allow_dirs = allow_dirs if allow_dirs is not None else set()
    violations: list[str] = []
    for root, f in _iter_py_files(paths):
        if set(f.parts) & SKIP_DIR_PARTS:
            continue
        if f.name in allow:
            continue
        if root is not None:
            allowed = _matches_allow_dir(f.relative_to(root).parts, allow_dirs)
        else:
            # 単一ファイル指定: 走査ルートが無いので親ディレクトリ列の末尾一致で判定
            # （ディレクトリ走査と同じファイルが同じ判定になるようにする・R3-1）
            allowed = _parent_suffix_matches(f.resolve().parent.parts, allow_dirs)
        if allowed:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError as e:
            violations.append(f"{f}: 構文エラー: {e}")
            continue
        v = _RawOpsVisitor()
        v.visit(tree)
        for lineno, expr in v.hits:
            violations.append(
                f"{f}:{lineno}: 生 bpy.ops 呼び出し禁止 -> {expr}() （run_operator 経由にする）"
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生 bpy.ops 禁止チェッカ")
    ap.add_argument("paths", nargs="+", help="検査対象ディレクトリ/ファイル")
    ap.add_argument("--allow", action="append", default=[], help="許可するファイル名（追加）")
    ap.add_argument(
        "--allow-dir",
        action="append",
        default=[],
        help="許可するディレクトリ（走査ルートからの相対プレフィックス 'bli_addon/gateway' 形式・追加）",
    )
    args = ap.parse_args(argv)
    allow = DEFAULT_ALLOW | set(args.allow)
    allow_dirs = DEFAULT_ALLOW_DIRS | set(args.allow_dir)
    violations = check(args.paths, allow, allow_dirs)
    if violations:
        print("生 bpy.ops 呼び出しが検出されました:", file=sys.stderr)
        for v in violations:
            print("  " + v, file=sys.stderr)
        return 1
    print("OK: 生 bpy.ops 呼び出しなし")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
