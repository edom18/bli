"""ops._material の create 経路 × 共有 mesh ガード失敗の統合ロールバック検証（レビュー R3-A/R3-B）。

gateway を丸ごとフェイクに差し替え（sys.modules と bli_addon.__dict__ の両方・mistakes-memo の
`_forget_gateway_module` と同じ二重後始末）、「create_material 成功 → ガード失敗」で
`discard_created_material` が呼ばれること（texture 付き extras 含む）を検証する。
- JsonRpcError（E_PRECONDITION＝--make-single-user なしの共有 mesh）経路
- JsonRpcError **以外**（MemoryError 等の想定外例外）経路 ＝ R3-A の回帰ガード
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

import bli_addon
from bli_addon import ops
from bli_addon.handlers import ServerInfo
from bli_core.protocol import JsonRpcError

INFO = ServerInfo("5.0.1-test", "deadbeef", ["wm.stl_export"])


class _FakeObj:
    name = "Cube"
    type = "MESH"


def _make_fake_gateway(
    *,
    mesh_users: int = 2,
    user_count_exc: BaseException | None = None,
) -> tuple[types.ModuleType, dict[str, Any]]:
    """create が成功しガードで失敗する構成のフェイク gateway と、呼び出し記録を返す。"""
    calls: dict[str, Any] = {"discarded": [], "created": []}
    gw = types.ModuleType("bli_addon.gateway")
    fake_mat = types.SimpleNamespace(name="SpikeMat")
    fake_extras = {"texture": {"image": "spike_img", "path": "x.png", "packed": True}}

    gw.current_mode = lambda: "OBJECT"
    gw.require_single = lambda name, regex=False: _FakeObj()
    gw.require_material_support = lambda obj: None
    gw.material_write_touches_mesh_data = lambda obj: True

    def mesh_user_count(obj: Any) -> int:
        if user_count_exc is not None:
            raise user_count_exc
        return mesh_users

    gw.mesh_user_count = mesh_user_count
    gw.make_single_user_mesh = lambda obj: None

    def create_material(name: str, color: Any, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
        calls["created"].append(name)
        return fake_mat, dict(fake_extras)

    gw.create_material = create_material

    def discard_created_material(mat: Any, extras: dict[str, Any]) -> None:
        calls["discarded"].append((mat, extras))

    gw.discard_created_material = discard_created_material
    # 到達しないはずの後段（呼ばれたらテスト失敗にする）
    gw.assign_material = lambda obj, mat: pytest.fail("ガード失敗後に assign へ到達してはならない")
    return gw, calls


@pytest.fixture
def fake_gateway(request: pytest.FixtureRequest):
    """フェイク gateway を注入し、テスト後に必ず二重後始末する（mistakes-memo の罠対策）。"""
    saved_mod = sys.modules.get("bli_addon.gateway")
    saved_attr = bli_addon.__dict__.get("gateway")

    def install(gw: types.ModuleType) -> None:
        sys.modules["bli_addon.gateway"] = gw
        bli_addon.gateway = gw  # type: ignore[attr-defined]

    yield install

    sys.modules.pop("bli_addon.gateway", None)
    bli_addon.__dict__.pop("gateway", None)
    if saved_mod is not None:
        sys.modules["bli_addon.gateway"] = saved_mod
    if saved_attr is not None:
        bli_addon.gateway = saved_attr  # type: ignore[attr-defined]


def _create_params(texture_path: str) -> dict[str, Any]:
    return {
        "action": "create",
        "targets": "Cube",
        "name": "SpikeMat",
        "metallic": 0.5,
        "texture": texture_path,
        "pack_texture": True,
    }


def test_guard_failure_after_create_discards_material_and_extras(fake_gateway, tmp_path):
    # 共有 mesh（users>=2）+ --make-single-user なし → E_PRECONDITION。create 済みの
    # material/extras（texture 付き）が discard_created_material へ渡ることを検証（R2-A/R3-B）。
    tex = tmp_path / "t.png"
    tex.write_bytes(b"png")
    gw, calls = _make_fake_gateway(mesh_users=2)
    fake_gateway(gw)

    with pytest.raises(JsonRpcError) as ei:
        ops.dispatch("material", _create_params(str(tex)), INFO)
    assert ei.value.message == "E_PRECONDITION"
    assert calls["created"] == ["SpikeMat"]
    assert len(calls["discarded"]) == 1
    mat, extras = calls["discarded"][0]
    assert mat.name == "SpikeMat"
    assert extras["texture"]["image"] == "spike_img"  # texture 付き extras がそのまま渡る


def test_guard_unexpected_exception_also_discards(fake_gateway, tmp_path):
    # JsonRpcError 以外（MemoryError 等の想定外例外）でも discard してから再送出する（R3-A）。
    tex = tmp_path / "t.png"
    tex.write_bytes(b"png")
    gw, calls = _make_fake_gateway(user_count_exc=MemoryError("boom"))
    fake_gateway(gw)

    with pytest.raises(MemoryError):
        ops.dispatch("material", _create_params(str(tex)), INFO)
    assert calls["created"] == ["SpikeMat"]
    assert len(calls["discarded"]) == 1
