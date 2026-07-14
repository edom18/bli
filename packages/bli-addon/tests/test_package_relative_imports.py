"""gateway/ ops/ パッケージ内の相対 import 深さの静的検証（L1）。

P2-4 のパッケージ分割で、関数内 lazy import の `from . import X` は 1 段深くなり
`from .. import X` へ書き換えが必要になった。書き換え漏れは bpy 依存の実行時
（実機 smoke）まで顕在化しない（例: gateway/core.py の exec_runner 漏れ→INTERNAL）ため、
「level=1 の相対 import が指す名前は必ず同一パッケージ内の実在サブモジュール」を
AST で静的に強制する。
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "bli_addon"
PACKAGES = ("gateway", "ops")


def _sibling_modules(pkg_dir: pathlib.Path) -> set[str]:
    return {p.stem for p in pkg_dir.glob("*.py")}


def _level1_violations(pkg_dir: pathlib.Path) -> list[str]:
    siblings = _sibling_modules(pkg_dir)
    violations: list[str] = []
    for f in sorted(pkg_dir.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 1:
                continue
            if node.module is None:
                # `from . import X` → X はサブモジュール名でなければならない
                bad = [a.name for a in node.names if a.name not in siblings]
            else:
                # `from .mod import X` → mod はサブモジュールでなければならない
                bad = [] if node.module.split(".")[0] in siblings else [node.module]
            for name in bad:
                violations.append(f"{f.name}:{node.lineno}: from . import {name}")
    return violations


def test_gateway_and_ops_level1_imports_resolve_within_package():
    for pkg in PACKAGES:
        pkg_dir = SRC / pkg
        assert pkg_dir.is_dir(), pkg_dir
        assert _level1_violations(pkg_dir) == []


def test_detects_missing_double_dot(tmp_path):
    # 例: gateway/core.py の `from . import exec_runner` 漏れ（親パッケージのモジュール）を検出できる
    pkg = tmp_path / "gateway"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        "def go():\n    from . import exec_runner\n    return exec_runner\n",
        encoding="utf-8",
    )
    violations = _level1_violations(pkg)
    assert len(violations) == 1
    assert "exec_runner" in violations[0]
