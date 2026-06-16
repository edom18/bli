"""output_ref（出力退避）の L1 ユニット（純Python・bpy不要）。

閾値判定 / 退避（temp→os.replace）/ sha256 検証往復 / 改竄検出 / 配下逸脱拒否 /
コンテンツアドレス id の決定性を検証する。
"""

from __future__ import annotations

import json

import pytest

from bli_core import output_ref as outref


def _big_data(n: int = 5000) -> dict:
    # 1要素あたり数十バイト → n=5000 で 64KiB を確実に超える
    return {"objects": [{"name": f"Obj{i:05d}", "value": i, "tag": "x" * 4} for i in range(n)]}


def test_small_data_stays_inline(tmp_path):
    data = {"scene": "Scene", "objects": [{"name": "Cube"}]}
    inline, descriptor = outref.maybe_offload("scene-info/v1", data, tmp_path)
    assert inline == data
    assert descriptor is None
    # 退避ファイルは作られない
    assert not list(tmp_path.iterdir())


def test_boundary_just_below_threshold_inline(tmp_path):
    # 閾値未満ちょうど（INLINE_THRESHOLD-1 バイト）は inline
    blob = outref.encode_payload({"a": ""})
    pad = outref.INLINE_THRESHOLD - 1 - len(outref.encode_payload({"a": "x"}))
    data = {"a": "x" * (pad + 1)}
    assert len(outref.encode_payload(data)) < outref.INLINE_THRESHOLD
    inline, descriptor = outref.maybe_offload("s/v1", data, tmp_path)
    assert descriptor is None
    assert inline == data
    assert blob  # encode_payload は決定的バイト列


def test_large_data_offloaded_with_descriptor(tmp_path):
    data = _big_data()
    inline, descriptor = outref.maybe_offload("scene-info/v1", data, tmp_path)
    assert inline is None
    assert descriptor is not None
    assert descriptor["transport"] == "shared-fs"
    assert descriptor["schema"] == "scene-info/v1"
    assert descriptor["encoding"] == "utf-8"
    assert descriptor["size"] == len(outref.encode_payload(data))
    assert len(descriptor["sha256"]) == 64
    assert descriptor["id"] == descriptor["sha256"][:16]
    # ファイルは outputs_dir 配下に作られ、tmp は残らない
    from pathlib import Path

    p = Path(descriptor["path"])
    assert p.exists()
    assert p.parent == tmp_path.resolve()
    assert not list(tmp_path.glob("*.tmp*"))


def test_load_verified_roundtrip(tmp_path):
    data = _big_data()
    _inline, descriptor = outref.maybe_offload("scene-info/v1", data, tmp_path)
    restored = outref.load_verified(descriptor)
    assert restored == data


def test_load_verified_detects_tamper(tmp_path):
    data = _big_data()
    _inline, descriptor = outref.maybe_offload("scene-info/v1", data, tmp_path)
    # 退避ファイルを書き換える → sha256 不一致
    from pathlib import Path

    Path(descriptor["path"]).write_text(json.dumps({"objects": []}), encoding="utf-8")
    with pytest.raises(outref.StaleOutputError):
        outref.load_verified(descriptor)


def test_load_verified_missing_path(tmp_path):
    with pytest.raises(outref.StaleOutputError):
        outref.load_verified({"transport": "shared-fs", "sha256": "x"})


def test_load_verified_missing_file(tmp_path):
    bogus = {
        "transport": "shared-fs",
        "path": str(tmp_path / "nope.json"),
        "sha256": "0" * 64,
    }
    with pytest.raises(outref.StaleOutputError):
        outref.load_verified(bogus)


def test_content_addressed_id_is_deterministic(tmp_path):
    data = _big_data()
    _i1, d1 = outref.maybe_offload("scene-info/v1", data, tmp_path)
    _i2, d2 = outref.maybe_offload("scene-info/v1", data, tmp_path)
    # 同一内容 → 同一 id / 同一パス（自然な重複排除）
    assert d1["id"] == d2["id"]
    assert d1["path"] == d2["path"]


def test_safe_output_path_rejects_escape(tmp_path):
    # id にパス区切りを混ぜても outputs 配下を逸脱しない
    with pytest.raises(ValueError):
        outref._safe_output_path(tmp_path, "../escape")


# ---- offload_file（バイナリ成果物・capture/実地FB #1 で使用）----


def test_offload_file_content_addressed(tmp_path):
    # 一時ファイルをコンテンツアドレス名で退避（src は消え、<sha16>.png へ収束・descriptor 整合）。
    src = tmp_path / "capture_tmp.png"
    blob = b"\x89PNG\r\n\x1a\n" + b"binary-image-bytes" * 10
    src.write_bytes(blob)
    out = tmp_path / "outputs"
    out.mkdir()
    desc = outref.offload_file(src, "capture/v1", out, suffix=".png")
    assert not src.exists()  # アトミック改名で src は残らない
    assert desc["sha256"] == outref.sha256_of(blob)
    assert desc["id"] == desc["sha256"][:16]
    assert desc["size"] == len(blob)
    assert desc["transport"] == "shared-fs"
    assert desc["schema"] == "capture/v1"
    final = out / f"{desc['id']}.png"
    assert final.read_bytes() == blob and desc["path"] == str(final)


def test_offload_file_dedups_identical_bytes(tmp_path):
    # 同一バイト列は同一 id/パスへ収束する（content-address）。
    out = tmp_path / "outputs"
    out.mkdir()
    blob = b"same-bytes" * 50
    s1 = tmp_path / "a.png"
    s1.write_bytes(blob)
    d1 = outref.offload_file(s1, "capture/v1", out, suffix=".png")
    s2 = tmp_path / "b.png"
    s2.write_bytes(blob)
    d2 = outref.offload_file(s2, "capture/v1", out, suffix=".png")
    assert d1["id"] == d2["id"] and d1["path"] == d2["path"]


def test_offload_file_missing_src_raises_oserror(tmp_path):
    # 存在しない src は OSError（呼び出し側が業務エラーへ写像する前提）。
    out = tmp_path / "outputs"
    out.mkdir()
    with pytest.raises(OSError):
        outref.offload_file(tmp_path / "nope.png", "capture/v1", out, suffix=".png")
