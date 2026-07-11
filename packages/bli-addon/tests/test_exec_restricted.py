"""exec_restricted.scan_blocked（P1-1・restricted モードの AST ブロックリスト）の L1 ユニット。

bpy 非依存。契約: ①Blender API（bpy/bmesh/mathutils）は全面許可 ②プロセス/ネットワーク/削除系/
動的実行/書込 open を検出したら自己記述形式の理由を返す ③決して例外を投げない（構文エラーは []）。
静的検査の限界（getattr 迂回等）は仕様（spec §459・security_guarantee:false）＝ここでは検証しない。
"""

from __future__ import annotations

import pytest

from bli_addon.exec_restricted import scan_blocked

# ---- 許可（Blender API と通常の Python は素通り）----


def test_bpy_operator_code_is_allowed():
    # 受け入れ基準（設計レビュー P1-1）: primitive_cube_add を含むコードが自走できる。
    code = "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2.0)\n"
    assert scan_blocked(code) == []


def test_bmesh_and_mathutils_allowed():
    code = (
        "import bmesh\nimport mathutils\nfrom mathutils import Vector\n"
        "v = Vector((1, 2, 3))\nprint(v.length)\n"
    )
    assert scan_blocked(code) == []


def test_plain_python_allowed():
    code = "import math\nimport json\nx = [math.sqrt(i) for i in range(10)]\nprint(json.dumps(x))\n"
    assert scan_blocked(code) == []


def test_os_path_usage_allowed():
    # os の import 自体と os.path 系の読み取り用途は許可（正当用途が多い）。
    code = "import os\nprint(os.path.join(os.getcwd(), 'x'))\nprint(os.listdir('.'))\n"
    assert scan_blocked(code) == []


def test_read_open_allowed():
    code = "with open('data.txt') as f:\n    print(f.read())\n"
    assert scan_blocked(code) == []


# ---- import ブロック ----


def test_import_subprocess_blocked():
    # 受け入れ基準（設計レビュー P1-1）: import subprocess は拒否。
    assert scan_blocked("import subprocess\n") == ["import:subprocess"]


@pytest.mark.parametrize(
    "module",
    ["socket", "urllib", "http", "ctypes", "importlib", "multiprocessing", "pickle", "requests"],
)
def test_blocked_module_imports(module):
    assert scan_blocked(f"import {module}\n") == [f"import:{module}"]


def test_from_import_submodule_blocked_by_top_module():
    # from urllib.request import urlopen → top の urllib で拒否。
    assert scan_blocked("from urllib.request import urlopen\n") == ["import:urllib"]


def test_import_alias_of_blocked_module_blocked():
    assert scan_blocked("import subprocess as sp\n") == ["import:subprocess"]


# ---- os / shutil の属性呼び出しブロック（import は許可・危険呼び出しのみ拒否）----


def test_os_system_call_blocked():
    got = scan_blocked("import os\nos.system('rm -rf /')\n")
    assert got == ["attr-call:os.system"]


def test_os_remove_blocked_but_os_import_allowed():
    got = scan_blocked("import os\nos.remove('a.txt')\n")
    assert got == ["attr-call:os.remove"]


def test_os_alias_attr_call_tracked():
    # `import os as o` の別名越しでも追跡する。
    got = scan_blocked("import os as o\no.unlink('a.txt')\n")
    assert got == ["attr-call:os.unlink"]


def test_from_os_import_system_blocked_at_import():
    # from-import は Name 呼び出しになり属性追跡をすり抜けるため import 時点で拒否。
    got = scan_blocked("from os import system\nsystem('whoami')\n")
    assert got == ["from-import:os.system"]


def test_from_os_import_star_blocked_conservatively():
    assert scan_blocked("from os import *\n") == ["from-import:os.*"]


def test_from_os_import_harmless_name_allowed():
    assert scan_blocked("from os import getcwd\nprint(getcwd())\n") == []


def test_shutil_rmtree_blocked_copy_allowed():
    assert scan_blocked("import shutil\nshutil.rmtree('/tmp/x')\n") == ["attr-call:shutil.rmtree"]
    assert scan_blocked("import shutil\nshutil.copyfile('a', 'b')\n") == []


def test_os_spawn_and_exec_variants_blocked():
    got = scan_blocked("import os\nos.execvpe('x', [], {})\nos.spawnl(0, 'x')\n")
    assert got == ["attr-call:os.execvpe", "attr-call:os.spawnl"]


# ---- 組込み呼び出し / 書込 open ブロック ----


@pytest.mark.parametrize(
    "builtin", ["eval", "exec", "compile", "__import__", "breakpoint", "input"]
)
def test_dangerous_builtin_calls_blocked(builtin):
    assert scan_blocked(f"{builtin}('x')\n" if builtin != "breakpoint" else "breakpoint()\n") == [
        f"call:{builtin}"
    ]


def test_write_open_blocked():
    assert scan_blocked("open('out.txt', 'w').write('x')\n") == ["file-write"]


def test_append_and_keyword_mode_blocked():
    assert scan_blocked("open('out.txt', mode='a')\n") == ["file-write"]


# ---- 複合・契約 ----


def test_multiple_reasons_sorted_and_deduped():
    code = (
        "import subprocess\nimport subprocess\nimport os\n"
        "os.system('x')\neval('1')\nopen('f', 'w')\n"
    )
    assert scan_blocked(code) == [
        "attr-call:os.system",
        "call:eval",
        "file-write",
        "import:subprocess",
    ]


def test_syntax_error_returns_empty_never_raises():
    # 構文エラーはこの層の拒否理由にしない（後段 EXEC_ERROR(compile) が報告する）。
    assert scan_blocked("def broken(:\n") == []


def test_null_byte_returns_empty_never_raises():
    assert scan_blocked("print('a')\x00") == []
