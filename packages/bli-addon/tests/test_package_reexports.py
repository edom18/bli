"""gateway/ ops/ の __init__.py re-export 網羅性の静的検証（L1・R1-2）。

網羅的 re-export は「from .sub import (...) ブロック」と「__all__ リスト」の 2 箇所を
人力同期させる構造だが、サブモジュールへの**シンボル追加漏れ**は ruff / pyright /
既存 pytest のどれにも掛からず（pyright は bli-addon を include 対象外）、
「パッケージ属性経由では見えない」状態がサイレントに発生する（検証で実証済み）。
削除・改名は ImportError で即失敗するため、ここでは追加方向の不変条件
「サブモジュールの全トップレベル定義名（dunder 除く）は __init__ の import と
__all__ の両方に含まれる」を AST で強制する。
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "bli_addon"
PACKAGES = ("gateway", "ops")


def _toplevel_defined_names(path: pathlib.Path) -> set[str]:
    """モジュールのトップレベル定義名（def / class / 代入。import 由来と dunder は除く）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {n for n in names if not (n.startswith("__") and n.endswith("__"))}


def _init_exports(init_path: pathlib.Path) -> tuple[dict[str, set[str]], set[str]]:
    """__init__.py から (サブモジュール別 import 名, __all__ 集合) を取り出す。"""
    tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    imported: dict[str, set[str]] = {}
    dunder_all: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            mod = node.module.split(".")[0]
            imported.setdefault(mod, set()).update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    dunder_all = {
                        elt.value
                        for elt in ast.walk(node.value)
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    }
    return imported, dunder_all


def test_reexports_cover_all_submodule_definitions():
    for pkg in PACKAGES:
        pkg_dir = SRC / pkg
        imported, dunder_all = _init_exports(pkg_dir / "__init__.py")
        missing: list[str] = []
        for sub in sorted(pkg_dir.glob("*.py")):
            if sub.name == "__init__.py":
                continue
            defined = _toplevel_defined_names(sub)
            from_init = imported.get(sub.stem, set())
            for name in sorted(defined):
                if name not in from_init:
                    missing.append(f"{pkg}/__init__.py が {sub.stem}.{name} を import していない")
                elif name not in dunder_all:
                    missing.append(f"{pkg}/__all__ に {sub.stem}.{name} が無い")
        assert missing == []


def test_detects_missing_reexport(tmp_path):
    # サブモジュールに関数を追加して __init__ に足し忘れたケースを検出できる
    pkg = tmp_path / "gateway"
    pkg.mkdir()
    (pkg / "core.py").write_text("def added():\n    return 1\n", encoding="utf-8")
    (pkg / "__init__.py").write_text("__all__ = []\n", encoding="utf-8")
    imported, _ = _init_exports(pkg / "__init__.py")
    assert "added" not in imported.get("core", set())
    assert "added" in _toplevel_defined_names(pkg / "core.py")
