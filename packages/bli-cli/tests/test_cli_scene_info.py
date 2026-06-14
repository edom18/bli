"""scene-info の output_ref 退避まわり CLI テスト（L1・addon非接続）。

client.call をフェイクし、退避(shared-fs)を含む応答に対して:
- 既定（--fetch なし）は参照のみ（data=None / output_ref を素通し）
- --fetch で退避ファイルを読み sha256 検証して data へ展開
- 改竄時は STALE_OUTPUT で exit 1
を検証する。退避ファイル自体は output_ref モジュールで実際に作る。
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bli import client as cli_client
from bli.main import app
from bli_core import output_ref as outref

runner = CliRunner()


def _big_scene() -> dict:
    return {
        "scene": "Scene",
        "object_count": 4000,
        "objects": [{"name": f"Obj{i:05d}", "type": "MESH"} for i in range(4000)],
    }


def _make_offloaded(tmp_path: Path) -> tuple[dict, dict]:
    """退避済みの (envelope_result, descriptor) を返す。"""
    data = _big_scene()
    inline, descriptor = outref.maybe_offload("scene-info/v1", data, tmp_path)
    assert inline is None and descriptor is not None  # 閾値超で退避された前提
    result = {
        "success": True,
        "operation": "scene-info",
        "verified": True,
        "fingerprint": None,
        "output_ref": descriptor,
        "data": None,
    }
    return result, descriptor


def _patch_call(monkeypatch, result: dict) -> None:
    def fake_call(method, params=None, *, port=None, request_id=None, timeout=None):
        return result, {"type": "hello-ok"}

    monkeypatch.setattr(cli_client, "call", fake_call)


def test_scene_info_reference_only_by_default(tmp_path, monkeypatch):
    result, descriptor = _make_offloaded(tmp_path)
    _patch_call(monkeypatch, result)

    res = runner.invoke(app, ["scene-info", "--json"])
    assert res.exit_code == 0
    payload = json.loads(res.output)
    # 既定では退避ファイルを読まない → data は None、output_ref を素通し
    assert payload["data"] is None
    assert payload["output_ref"]["transport"] == "shared-fs"
    assert payload["output_ref"]["sha256"] == descriptor["sha256"]


def test_scene_info_fetch_expands_data(tmp_path, monkeypatch):
    result, _descriptor = _make_offloaded(tmp_path)
    _patch_call(monkeypatch, result)

    res = runner.invoke(app, ["scene-info", "--fetch", "--json"])
    assert res.exit_code == 0
    payload = json.loads(res.output)
    # --fetch で退避ファイルを読み sha256 検証 → data が復元される
    assert payload["data"]["object_count"] == 4000
    assert payload["data"]["objects"][0]["name"] == "Obj00000"


def test_scene_info_fetch_stale_output_exit1(tmp_path, monkeypatch):
    result, descriptor = _make_offloaded(tmp_path)
    # 退避ファイルを改竄 → sha256 不一致
    Path(descriptor["path"]).write_text(json.dumps({"objects": []}), encoding="utf-8")
    _patch_call(monkeypatch, result)

    res = runner.invoke(app, ["scene-info", "--fetch", "--json"])
    assert res.exit_code == 1
    err = json.loads(res.output)
    assert err["kind"] == "STALE_OUTPUT"


def test_scene_info_human_reference_summary(tmp_path, monkeypatch):
    result, _descriptor = _make_offloaded(tmp_path)
    _patch_call(monkeypatch, result)

    res = runner.invoke(app, ["scene-info"])  # 人間向け（--json なし）
    assert res.exit_code == 0
    assert "output_ref" in res.output
    assert "--fetch" in res.output
