"""bli アドオン配布用 zip をビルドする（M14 T14.2）。

- bli-core（純Python・依存ゼロ）を `bli_addon/vendored/bli_core/` へコピーして同梱（vendoring）。
  Blender 埋め込み Python は dev の uv workspace を知らないため、配布物に同梱しないと
  実機インストール時に `import bli_core` が解決できない。`bli_addon/__init__.py` の
  `_ensure_bli_core_on_path()` が `vendored/` を `sys.path` に載せる＝import は書き換えない。
- legacy add-on 形式（`bl_info`）の zip を 1 つ出力する。「Install from Disk…」で 4.4/5.0
  両対応（D10「手動zip一次」）。Extensions 形式（blender_manifest.toml）は zip に含めない
  ＝5.0 が誤って Extension 扱いしないため（後続で別ビルド）。
- 決定的（deterministic）: アーカイブ名を sort し mtime を固定する＝同一入力で同一バイト列。

使い方:
    uv run python scripts/build_addon.py                 # dist/bli_server-<ver>.zip を生成
    uv run python scripts/build_addon.py --out-dir build # 出力先を変更
"""

from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDON_SRC = REPO_ROOT / "packages" / "bli-addon" / "src" / "bli_addon"
CORE_SRC = REPO_ROOT / "packages" / "bli-core" / "src" / "bli_core"

# zip 内のトップレベルパッケージ名（= Blender が import するモジュール名）。
ADDON_PKG = "bli_addon"
VENDOR_PREFIX = f"{ADDON_PKG}/vendored/bli_core"

# 決定的ビルドのための固定 mtime（zip 下限の 1980-01-01）。
_FIXED_DATE = (1980, 1, 1, 0, 0, 0)


def addon_version() -> str:
    """アドオンの `__version__` を `__init__.py` から読む（bpy import を避ける）。"""
    text = (ADDON_SRC / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise RuntimeError("bli_addon/__init__.py に __version__ が見つからない")
    return m.group(1)


def _is_excluded(rel: Path) -> bool:
    """配布物に含めないファイルか（キャッシュ・バイトコード）。"""
    if "__pycache__" in rel.parts:
        return True
    return rel.suffix in (".pyc", ".pyo")


def collect_files() -> list[tuple[str, Path]]:
    """zip に入れる (アーカイブ名, ソースパス) を決定的に列挙する。

    - アドオン本体（`vendored/` サブツリーは除外し、bli_core を新規に同梱し直す）。
    - bli-core を `bli_addon/vendored/bli_core/` 配下へ。
    """
    files: list[tuple[str, Path]] = []

    for p in ADDON_SRC.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ADDON_SRC)
        if _is_excluded(rel):
            continue
        if rel.parts and rel.parts[0] == "vendored":
            continue  # 既存の .gitkeep 等は持ち込まず、下で bli_core を同梱する
        files.append((f"{ADDON_PKG}/{rel.as_posix()}", p))

    if not (CORE_SRC / "__init__.py").exists():
        raise RuntimeError(f"bli-core が見つからない: {CORE_SRC}")
    for p in CORE_SRC.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(CORE_SRC)
        if _is_excluded(rel):
            continue
        files.append((f"{VENDOR_PREFIX}/{rel.as_posix()}", p))

    files.sort(key=lambda item: item[0])
    return files


def write_zip(zip_path: Path, files: list[tuple[str, Path]]) -> None:
    """決定的に zip を書き出す（sort 済み・mtime 固定・権限固定）。"""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, src in files:
            info = zipfile.ZipInfo(arcname, date_time=_FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16  # 通常ファイル権限を固定
            zf.writestr(info, src.read_bytes())


def build(out_dir: Path) -> Path:
    """配布 zip を生成し、そのパスを返す。"""
    version = addon_version()
    files = collect_files()
    core_count = sum(1 for arc, _ in files if arc.startswith(VENDOR_PREFIX + "/"))
    if core_count == 0:
        raise RuntimeError("bli_core が 1 ファイルも同梱されていない（vendoring 失敗）")

    zip_path = out_dir / f"bli_server-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    write_zip(zip_path, files)

    data = zip_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    print(f"built: {zip_path}")
    print(f"  files     : {len(files)}（うち vendored bli_core {core_count}）")
    print(f"  size      : {len(data)} bytes")
    print(f"  sha256    : {digest}")
    print(f"  top-level : {ADDON_PKG}/ （legacy add-on・Install from Disk で 4.4/5.0 両対応）")
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description="bli アドオン配布 zip をビルドする")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "dist",
        help="出力ディレクトリ（既定: dist/）",
    )
    args = parser.parse_args()
    build(args.out_dir)


if __name__ == "__main__":
    main()
