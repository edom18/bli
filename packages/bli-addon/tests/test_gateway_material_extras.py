"""gateway.create_material / _apply_material_extras の失敗時ロールバックの L1 ユニット（P2-3 レビュー R1-3）。

test_gateway_modifier_props.py と同じ流儀（フェイク bpy を `sys.modules["bpy"]` に差し込んで
`bli_addon.gateway` を直接 import）で、Principled BSDF / テクスチャ設定の失敗時に
作りかけの material（と image）を残さずロールバックすることを bpy 無しで検証する。

後片付けは `sys.modules.pop("bli_addon.gateway", None)` **と**
`sys.modules["bli_addon"].__dict__.pop("gateway", None)` の両方を行う（mistakes-memo の罠:
`from . import gateway` は親パッケージ属性キャッシュを経由するため、sys.modules だけの pop では
フェイク bpy を積んだ gateway が後続テストへ漏れる）。

テスト③（pack 失敗）は、gateway.py のテクスチャブロックに「JsonRpcError 時に
`bpy.data.images.remove(img)` してから re-raise」が入る**前提**の期待挙動を書いている。
その修正が未着手のうちは images.removed の assert で fail する（image がリークしたまま
material だけロールバックされるため）。
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest

from bli_core.errors import ErrorCategory, ErrorCode
from bli_core.protocol import JsonRpcError


def _forget_gateway_module() -> None:
    """bli_addon.gateway を sys.modules と親パッケージ属性の両方から除去する（mistakes-memo の罠）。"""
    sys.modules.pop("bli_addon.gateway", None)
    bli_addon = sys.modules.get("bli_addon")
    if bli_addon is not None:
        bli_addon.__dict__.pop("gateway", None)


# ---- フェイク bpy.data.images（load/remove を記録・pack は設定で RuntimeError を投げられる）----


class _FakeImage:
    """bpy.data.images.load() が返す最小 Image スタブ。"""

    def __init__(self, path: str, *, pack_should_fail: bool) -> None:
        self.filepath = path
        self.name = path.replace("\\", "/").rsplit("/", 1)[-1]
        self.packed_file: object | None = None
        self._pack_should_fail = pack_should_fail

    def pack(self) -> None:
        if self._pack_should_fail:
            raise RuntimeError("fake pack failure")
        self.packed_file = object()  # 実 bpy の packed_file は不透明オブジェクト＝存在有無だけ見る


class _FakeImagesCollection:
    """bpy.data.images の最小スタブ（load→記録・remove 呼び出しを記録）。"""

    def __init__(self, *, pack_should_fail: bool = False) -> None:
        self._pack_should_fail = pack_should_fail
        self.loaded: list[_FakeImage] = []
        self.removed: list[_FakeImage] = []

    def load(self, path: str) -> _FakeImage:
        img = _FakeImage(path, pack_should_fail=self._pack_should_fail)
        self.loaded.append(img)
        return img

    def remove(self, img: _FakeImage) -> None:
        self.removed.append(img)


# ---- フェイク Material / NodeTree（Principled BSDF・ShaderNodeTexImage・links.new）----


class _FakeSocket:
    """rna 入力ソケットの最小スタブ（default_value 設定可）。"""

    def __init__(self, default_value: Any) -> None:
        self.default_value = default_value


class _FakeInputs:
    """Principled.inputs の最小スタブ（dict 風の get のみ）。"""

    def __init__(self, sockets: dict[str, _FakeSocket]) -> None:
        self._sockets = sockets

    def get(self, name: str, default: Any = None) -> Any:
        return self._sockets.get(name, default)


# gateway が読む入力ソケット一式（P2-3 スパイクで確認済みの既定名）。missing_inputs で
# 個別に欠如させ、「想定外ビルド」の E_PRECONDITION 経路を再現する。
_PRINCIPLED_DEFAULT_SOCKETS: dict[str, Any] = {
    "Base Color": (0.8, 0.8, 0.8, 1.0),
    "Metallic": 0.0,
    "Roughness": 0.5,
    "Alpha": 1.0,
    "Emission Color": (0.0, 0.0, 0.0, 1.0),
    "Emission Strength": 0.0,
}


class _FakePrincipledNode:
    def __init__(self, *, missing_inputs: tuple[str, ...] = ()) -> None:
        self.type = "BSDF_PRINCIPLED"
        self.inputs = _FakeInputs(
            {
                name: _FakeSocket(value)
                for name, value in _PRINCIPLED_DEFAULT_SOCKETS.items()
                if name not in missing_inputs
            }
        )


class _FakeOutputSocket:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeTexOutputs:
    def __init__(self) -> None:
        self._sockets = {"Color": _FakeOutputSocket("Color")}

    def __getitem__(self, name: str) -> Any:
        return self._sockets[name]


class _FakeTexNode:
    """nodes.new("ShaderNodeTexImage") が返す最小 TexImage ノードスタブ。"""

    def __init__(self) -> None:
        self.type = "TEX_IMAGE"
        self.image: Any = None
        self.location: tuple[float, float] = (0.0, 0.0)
        self.outputs = _FakeTexOutputs()


class _FakeNodesCollection:
    """node_tree.nodes の最小スタブ（iterate 可能・new("ShaderNodeTexImage") のみ対応）。"""

    def __init__(self, nodes: list[Any]) -> None:
        self._nodes = nodes

    def __iter__(self) -> Any:
        return iter(self._nodes)

    def new(self, node_type: str) -> Any:
        if node_type != "ShaderNodeTexImage":
            raise NotImplementedError(node_type)
        node = _FakeTexNode()
        self._nodes.append(node)
        return node


class _FakeLinks:
    """node_tree.links の最小スタブ（links.new 呼び出しを記録）。"""

    def __init__(self) -> None:
        self.created: list[tuple[Any, Any]] = []

    def new(self, from_socket: Any, to_socket: Any) -> None:
        self.created.append((from_socket, to_socket))


class _FakeNodeTree:
    def __init__(self, nodes: list[Any]) -> None:
        self.nodes = _FakeNodesCollection(nodes)
        self.links = _FakeLinks()


class _FakeMaterial:
    """bpy.data.materials.new() が返す最小 Material スタブ（use_nodes 設定可・node_tree 付き）。"""

    def __init__(self, name: str, *, has_principled: bool, missing_inputs: tuple[str, ...]) -> None:
        self.name = name
        self.use_nodes = False
        self.diffuse_color: tuple[float, float, float, float] = (0.8, 0.8, 0.8, 1.0)
        nodes: list[Any] = []
        if has_principled:
            nodes.append(_FakePrincipledNode(missing_inputs=missing_inputs))
        self.node_tree = _FakeNodeTree(nodes)


class _FakeMaterialsCollection:
    """bpy.data.materials の最小スタブ（new→記録・remove 呼び出しを記録）。"""

    def __init__(
        self, *, has_principled: bool = True, missing_inputs: tuple[str, ...] = ()
    ) -> None:
        self._has_principled = has_principled
        self._missing_inputs = missing_inputs
        self.created: list[_FakeMaterial] = []
        self.removed: list[_FakeMaterial] = []

    def new(self, name: str) -> _FakeMaterial:
        mat = _FakeMaterial(
            name, has_principled=self._has_principled, missing_inputs=self._missing_inputs
        )
        self.created.append(mat)
        return mat

    def remove(self, mat: _FakeMaterial) -> None:
        self.removed.append(mat)


def _find_node(mat: _FakeMaterial, node_type: str) -> Any | None:
    for node in mat.node_tree.nodes:
        if node.type == node_type:
            return node
    return None


@pytest.fixture
def make_gateway(monkeypatch):
    """フェイク bpy.data.materials/images を差し込んで bli_addon.gateway を新規 import するファクトリ。

    戻り値は (gw, materials, images)。materials/images は生成物・remove 呼び出しの記録を
    保持するフェイクコレクションで、assert に直接使う。
    """

    def _factory(
        *,
        has_principled: bool = True,
        missing_inputs: tuple[str, ...] = (),
        pack_should_fail: bool = False,
    ) -> tuple[Any, _FakeMaterialsCollection, _FakeImagesCollection]:
        materials = _FakeMaterialsCollection(
            has_principled=has_principled, missing_inputs=missing_inputs
        )
        images = _FakeImagesCollection(pack_should_fail=pack_should_fail)
        fake_bpy = types.ModuleType("bpy")
        fake_bpy.data = types.SimpleNamespace(materials=materials, images=images)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
        _forget_gateway_module()
        gw = importlib.import_module("bli_addon.gateway")
        return gw, materials, images

    yield _factory
    _forget_gateway_module()


# ---- ① 正常系: PBR 実値 + テクスチャ(pack 成功) が extras に入り、Base Color へ links.new される ----


def test_create_material_full_extras_applies_and_links_texture(make_gateway):
    gw, materials, images = make_gateway()
    mat, extras = gw.create_material(
        "Mat1",
        [0.1, 0.2, 0.3, 1.0],
        metallic=0.2,
        roughness=0.4,
        alpha=0.9,
        emission=[1.0, 0.0, 0.0, 1.0],
        texture_path="C:/tmp/tex.png",
        pack_texture=True,
    )

    assert extras["principled"] == {
        "metallic": 0.2,
        "roughness": 0.4,
        "alpha": 0.9,
        "emission_color": [1.0, 0.0, 0.0, 1.0],
        "emission_strength": 1.0,  # emission_strength 省略時のデフォルト
    }

    img = images.loaded[0]
    assert extras["texture"] == {"image": img.name, "path": "C:/tmp/tex.png", "packed": True}
    assert img.packed_file is not None

    bsdf = _find_node(mat, "BSDF_PRINCIPLED")
    base_color_socket = bsdf.inputs.get("Base Color")
    assert base_color_socket.default_value == (0.1, 0.2, 0.3, 1.0)  # color も Base Color へ反映
    assert len(mat.node_tree.links.created) == 1
    link_from, link_to = mat.node_tree.links.created[0]
    assert link_from.name == "Color"
    assert link_to is base_color_socket

    assert materials.removed == []
    assert images.removed == []


# ---- ② Principled 入力欠如 → E_PRECONDITION + materials.remove（作りかけを残さない）----


def test_create_material_missing_principled_input_rolls_back_material(make_gateway):
    gw, materials, images = make_gateway(missing_inputs=("Metallic",))
    with pytest.raises(JsonRpcError) as ei:
        gw.create_material("Mat2", None, metallic=0.5)

    assert ei.value.message == ErrorCode.E_PRECONDITION
    assert ei.value.data.category == ErrorCategory.PRECONDITION
    assert materials.created  # 作りかけの material が実際に生成されていた前提
    assert materials.removed == materials.created
    assert images.removed == []


# ---- ③ pack 失敗 → E_OPERATOR + materials.remove と images.remove の両方（R1-2 回帰ガード）----


def test_create_material_pack_failure_rolls_back_material_and_image(make_gateway):
    gw, materials, images = make_gateway(pack_should_fail=True)
    with pytest.raises(JsonRpcError) as ei:
        gw.create_material("Mat3", None, texture_path="C:/tmp/tex.png", pack_texture=True)

    assert ei.value.message == ErrorCode.E_OPERATOR
    assert ei.value.data.category == ErrorCategory.USER_INPUT
    assert materials.created
    assert materials.removed == materials.created
    # image がリークしないこと（gateway.py 側の「pack 失敗時に images.remove してから
    # re-raise」修正が入って初めて通る＝R1-3 の回帰ガード）。
    assert images.loaded
    assert images.removed == images.loaded


# ---- ④ texture 無し・PBR のみで bsdf 欠如（Principled ノード無し）→ E_PRECONDITION + materials.remove ----


def test_create_material_missing_bsdf_rolls_back_material(make_gateway):
    gw, materials, images = make_gateway(has_principled=False)
    with pytest.raises(JsonRpcError) as ei:
        gw.create_material("Mat4", None, metallic=0.5)

    assert ei.value.message == ErrorCode.E_PRECONDITION
    assert ei.value.data.category == ErrorCategory.PRECONDITION
    assert materials.created
    assert materials.removed == materials.created
    assert images.removed == []


# ---- ⑤ 何も指定しない → extras == {} で materials.remove は呼ばれない ----


def test_create_material_no_extras_requested_returns_empty_and_no_rollback(make_gateway):
    gw, materials, images = make_gateway()
    mat, extras = gw.create_material("Mat5", None)

    assert extras == {}
    assert materials.created == [mat]
    assert materials.removed == []
    assert images.removed == []
