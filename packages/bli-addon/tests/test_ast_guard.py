"""生 bpy.ops 禁止 AST チェッカ（scripts/check_no_raw_bpy_ops.py）のユニット（L1）。"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import check_no_raw_bpy_ops as guard  # noqa: E402


def test_detects_raw_bpy_ops(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text(
        "import bpy\ndef go():\n    bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')\n",
        encoding="utf-8",
    )
    violations = guard.check([str(f)], allow=set())
    assert len(violations) == 1
    assert "origin_set" in violations[0]


def test_allows_non_ops_bpy(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text(
        "import bpy\n"
        "def go():\n"
        "    bpy.app.timers.register(lambda: None)\n"
        "    obj = bpy.data.objects['Cube']\n"
        "    return obj\n",
        encoding="utf-8",
    )
    assert guard.check([str(f)], allow=set()) == []


def test_allowlist_file_is_skipped(tmp_path):
    f = tmp_path / "gateway.py"
    f.write_text("import bpy\nbpy.ops.ed.undo_push(message='x')\n", encoding="utf-8")
    # gateway.py は run_operator ラッパ定義ファイルとして許可
    assert guard.check([str(f)], allow={"gateway.py"}) == []
    # 許可しなければ検出される
    assert len(guard.check([str(f)], allow=set())) == 1


def test_skips_spikes_dir(tmp_path):
    d = tmp_path / "spikes"
    d.mkdir()
    f = d / "op_spike.py"
    f.write_text("import bpy\nbpy.ops.object.select_all(action='SELECT')\n", encoding="utf-8")
    assert guard.check([str(tmp_path)], allow=set()) == []


def test_allow_dir_is_skipped(tmp_path):
    d = tmp_path / "bli_addon" / "gateway"
    d.mkdir(parents=True)
    f = d / "objects.py"
    f.write_text(
        "import bpy\nbpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')\n", encoding="utf-8"
    )
    # bli_addon/gateway/ 配下は run_operator ラッパを分割したパッケージとして許可される
    assert guard.check([str(tmp_path)], allow=set(), allow_dirs={"bli_addon/gateway"}) == []
    # 許可しなければ検出される
    assert len(guard.check([str(tmp_path)], allow=set(), allow_dirs=set())) == 1


def test_allow_dir_is_anchored(tmp_path):
    # 別位置の同名ディレクトリ（例: bli_addon/xyz/gateway/）は免除されない
    # （裸名一致だと任意階層の「gateway」でサブツリー全体が素通りするため・R1-1）
    d = tmp_path / "bli_addon" / "xyz" / "gateway"
    d.mkdir(parents=True)
    f = d / "evil.py"
    f.write_text("import bpy\nbpy.ops.object.delete()\n", encoding="utf-8")
    assert len(guard.check([str(tmp_path)], allow=set(), allow_dirs={"bli_addon/gateway"})) == 1
