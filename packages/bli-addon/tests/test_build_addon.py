"""配布 zip ビルド（M14 T14.2）と vendoring 解決の検証。

最重要: アドオンに同梱した `vendored/bli_core` が、dev の uv workspace（editable）に
依存せずに import 解決できることを確かめる。これを保証しないと、クリーン環境（Blender
埋め込み Python）でインストールしたときに `import bli_core` が失敗する。

検証は `python -S`（site-packages を読まない＝workspace の editable bli_core を隠す）で
サブプロセス起動し、`vendored/` 経由でのみ bli_core が解決されることを確認する。
GUI 実機での zip 導入→register→`bli ping` は L4 手動検証（README に手順を記載）。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "build_addon.py"


def _load_build_module():
    spec = importlib.util.spec_from_file_location("build_addon", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_addon = _load_build_module()


def test_zip_contains_vendored_core_and_no_caches(tmp_path: Path) -> None:
    zip_path = build_addon.build(tmp_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())

    # アドオン本体 + vendored bli_core が同梱されている。
    assert "bli_addon/__init__.py" in names
    # gateway は単一ファイルから gateway/ パッケージへ分割済み（P2-4）。
    assert "bli_addon/gateway/__init__.py" in names
    assert "bli_addon/gateway/core.py" in names
    assert "bli_addon/vendored/bli_core/__init__.py" in names
    assert "bli_addon/vendored/bli_core/runtime.py" in names
    assert "bli_addon/vendored/bli_core/schema.py" in names

    # キャッシュ・バイトコードは持ち込まない。
    assert not any("__pycache__" in n for n in names)
    assert not any(n.endswith((".pyc", ".pyo")) for n in names)
    # legacy zip には Extensions の manifest を含めない（5.0 が Extension 誤認しないため）。
    assert not any(n.endswith("blender_manifest.toml") for n in names)

    # workspace の bli_core 全モジュールが漏れなく vendored 化されている。
    core_src = REPO_ROOT / "packages" / "bli-core" / "src" / "bli_core"
    for py in core_src.glob("*.py"):
        assert f"bli_addon/vendored/bli_core/{py.name}" in names


def test_build_is_deterministic(tmp_path: Path) -> None:
    a = build_addon.build(tmp_path / "a")
    b = build_addon.build(tmp_path / "b")
    assert a.read_bytes() == b.read_bytes()


def _run_isolated(extract_dir: Path, code: str) -> subprocess.CompletedProcess[str]:
    """`-S`（site なし）でサブプロセス実行＝workspace の editable bli_core を隠す。"""
    env = {**os.environ, "PYTHONPATH": "", "BLI_VENDOR_DIR": str(extract_dir)}
    return subprocess.run(
        [sys.executable, "-S", "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )


def test_isolation_hides_workspace_core() -> None:
    """前提検証: `-S` だと workspace の bli_core は import できない（隔離が本物である証明）。"""
    proc = _run_isolated(REPO_ROOT, "import bli_core")
    assert proc.returncode != 0, (
        "`-S` でも bli_core が import できてしまうと vendoring 検証が無意味になる。"
        f"\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_vendored_core_resolves_in_isolation(tmp_path: Path) -> None:
    """zip を展開し、隔離環境で bli_core が vendored 経由のみで解決されることを確認する。"""
    zip_path = build_addon.build(tmp_path / "dist")
    extract_dir = tmp_path / "addons"
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)

    code = (
        "import os, sys\n"
        "d = os.environ['BLI_VENDOR_DIR']\n"
        "sys.path.insert(0, d)\n"
        "import bli_addon\n"  # _ensure_bli_core_on_path() が vendored を sys.path に載せる
        "import bli_core\n"
        "loc = bli_core.__file__.replace(os.sep, '/')\n"
        "assert 'vendored/bli_core' in loc, loc\n"
        # 全サブモジュールを import＝純Python・依存ゼロを配布物レベルで担保する。
        "from bli_core import (\n"
        "    runtime, commands, schema, protocol, errors, types, output_ref, definitions,\n"
        ")\n"
        "print('VENDOR_OK', loc)\n"
    )
    proc = _run_isolated(extract_dir, code)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "VENDOR_OK" in proc.stdout
    assert "vendored/bli_core" in proc.stdout


def test_addon_version_matches_init() -> None:
    # build スクリプトが拾うバージョンが __init__ の __version__ と一致する。
    import re

    init_text = (
        REPO_ROOT / "packages" / "bli-addon" / "src" / "bli_addon" / "__init__.py"
    ).read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.MULTILINE)
    assert m is not None
    assert build_addon.addon_version() == m.group(1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
