"""出力退避（OutputRef）。大きい結果をファイルへ退避し sha256 で整合検証する。

data-model.md §5 / spec §出力退避。bli-core に置くのは、アドオン（書込）と
CLI（読込検証）の双方が同じ規約を共有する必要があるため。純Python・依存ゼロ・
3.10 互換。Pydantic 等は持ち込まない。

設計:
- `INLINE_THRESHOLD = 64 KiB` 未満は inline（呼び出し側がそのまま data に載せる）。
  超過は shared-fs へ退避し descriptor（OutputRef）を返す。
- 退避 id は **コンテンツアドレス**（payload の sha256 先頭16桁）。request id を
  ops 層まで配線せずに済み、同一内容は同一ファイルへ収束（自然な重複排除）する。
  request との相関は応答エンベロープ側の request_id が担う。
- 書込は temp → `os.replace()` でアトミック。退避先は `outputs/<id>.json`。
- CLI は `load_verified` で raw bytes の sha256 を再計算して照合する。不一致や
  読込不能は `StaleOutputError`（CLI 側で STALE_OUTPUT 終了へ写像）。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

# 64 KiB（data-model §5 / outputs.inline_threshold 既定）
INLINE_THRESHOLD = 64 * 1024


class StaleOutputError(Exception):
    """退避ファイルの sha256 不一致 / 読込不能（CLI 終了コードは STALE_OUTPUT）。"""


def sha256_of(data: bytes) -> str:
    """バイト列の SHA256 16進ダイジェスト。"""
    return hashlib.sha256(data).hexdigest()


def encode_payload(data: Any) -> bytes:
    """退避/inline 判定と整合検証に使う決定的 UTF-8 バイト列へシリアライズする。

    sort_keys=True で同一内容→同一バイト列→同一 sha256（id 安定）にする。
    """
    return json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")


def build_descriptor(output_id: str, schema: str, path: Path, blob: bytes) -> dict[str, Any]:
    """OutputRef descriptor（data-model §5）を組み立てる。"""
    return {
        "id": output_id,
        "transport": "shared-fs",
        "path": str(path),
        "size": len(blob),
        "sha256": sha256_of(blob),
        "encoding": "utf-8",
        "schema": schema,
    }


def _safe_output_path(outputs_dir: Path, output_id: str) -> Path:
    """`outputs_dir` 配下の退避パスを返す（配下逸脱を防ぐ防御）。"""
    path = (outputs_dir / f"{output_id}.json").resolve()
    root = outputs_dir.resolve()
    if root != path.parent:
        raise ValueError(f"退避パスが outputs 配下ではありません: {path}")
    return path


def _write_atomic(outputs_dir: Path, output_id: str, blob: bytes) -> Path:
    """temp → os.replace でアトミックに退避ファイルを書き出す。"""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = _safe_output_path(outputs_dir, output_id)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        tmp.write_bytes(blob)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return path


def _safe_named_path(outputs_dir: Path, filename: str) -> Path:
    """`outputs_dir` 直下の任意ファイル名のパスを返す（配下逸脱を防ぐ防御・`_safe_output_path` の汎用版）。"""
    path = (outputs_dir / filename).resolve()
    if outputs_dir.resolve() != path.parent:
        raise ValueError(f"退避パスが outputs 配下ではありません: {path}")
    return path


def offload_file(
    tmp_path: Path | str, schema: str, outputs_dir: Path, *, suffix: str = ".bin"
) -> dict[str, Any]:
    """既存の一時ファイルをコンテンツアドレス名で outputs_dir へアトミック退避し descriptor を返す。

    画像など**バイナリ成果物**向け（JSON 退避の `maybe_offload` と対）。sha256 はファイルから
    ストリーミング算出（大解像度でも省メモリ）。退避先は `outputs/<sha16><suffix>`（`_safe_named_path`
    で配下逸脱を防ぐ）。`os.replace` で src を残さずアトミックに改名し、同一バイト列は同名へ収束する。
    読込/改名失敗（OSError）は呼び出し側が業務エラーへ写像する（INTERNAL にしない）。
    """
    src = Path(tmp_path)
    h = hashlib.sha256()
    size = 0
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
            size += len(chunk)
    sha = h.hexdigest()
    outputs_dir.mkdir(parents=True, exist_ok=True)
    dest = _safe_named_path(outputs_dir, f"{sha[:16]}{suffix}")
    os.replace(src, dest)
    return {
        "id": sha[:16],
        "transport": "shared-fs",
        "path": str(dest),
        "size": size,
        "sha256": sha,
        "schema": schema,
    }


def maybe_offload(schema: str, data: Any, outputs_dir: Path) -> tuple[Any, dict[str, Any] | None]:
    """data を inline で返すか、閾値超ならファイル退避して descriptor を返す。

    戻り値 `(inline_data, output_ref)`:
      - inline（閾値未満）: `(data, None)`
      - shared-fs（閾値超）: `(None, descriptor)`
    """
    blob = encode_payload(data)
    if len(blob) < INLINE_THRESHOLD:
        return data, None
    output_id = sha256_of(blob)[:16]
    path = _write_atomic(outputs_dir, output_id, blob)
    return None, build_descriptor(output_id, schema, path, blob)


def load_verified(output_ref: dict[str, Any]) -> Any:
    """shared-fs の退避ファイルを読み、sha256 を照合して data を復元する。

    path 欠落・読込不能・sha256 不一致はすべて `StaleOutputError`。
    """
    path = output_ref.get("path")
    if not isinstance(path, str) or not path:
        raise StaleOutputError("output_ref に path がありません")
    try:
        blob = Path(path).read_bytes()
    except OSError as e:
        raise StaleOutputError(f"退避ファイルを読めません: {path}: {e}") from e
    expected = output_ref.get("sha256")
    actual = sha256_of(blob)
    if expected != actual:
        raise StaleOutputError(
            f"sha256 不一致（退避ファイルが変化した可能性）: expected={expected} actual={actual}"
        )
    return json.loads(blob.decode(str(output_ref.get("encoding") or "utf-8")))
