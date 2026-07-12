"""gateway.py の modifier --props（P2-3 G4）rna 検証の L1 ユニット。

test_gateway_targets.py と同じ流儀（フェイク bpy を `sys.modules["bpy"]` に差し込んで
`bli_addon.gateway` を直接 import）で、`set_modifier_props` / `_coerce_prop_value` /
`add_modifier` の props 失敗時アトミック性を bpy 無しで検証する。

後片付けは `sys.modules.pop("bli_addon.gateway", None)` **と**
`sys.modules["bli_addon"].__dict__.pop("gateway", None)` の両方を行う（mistakes-memo の罠:
`from . import gateway` は親パッケージ属性キャッシュを経由するため、sys.modules だけの pop では
フェイク bpy を積んだ gateway が後続テストへ漏れる）。
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest

from bli_core.errors import ErrorCode
from bli_core.protocol import JsonRpcError


class _FakeObjects:
    """bpy.data.objects の最小スタブ（POINTER の Object 名前解決に必要な get のみ）。"""

    def __init__(self, objs: tuple[Any, ...] = ()) -> None:
        self._by_name = {o.name: o for o in objs}

    def get(self, name: str, default: Any = None) -> Any:
        return self._by_name.get(name, default)


class _FakeTargetObj:
    """POINTER(Object) 解決先の最小オブジェクト（.name のみ）。"""

    def __init__(self, name: str) -> None:
        self.name = name


def _make_fake_bpy(objects: tuple[Any, ...] = ()) -> types.ModuleType:
    bpy_mod = types.ModuleType("bpy")
    bpy_mod.data = types.SimpleNamespace(objects=_FakeObjects(objects))  # type: ignore[attr-defined]
    return bpy_mod


def _forget_gateway_module() -> None:
    """bli_addon.gateway を sys.modules と親パッケージ属性の両方から除去する（mistakes-memo の罠）。"""
    sys.modules.pop("bli_addon.gateway", None)
    bli_addon = sys.modules.get("bli_addon")
    if bli_addon is not None:
        bli_addon.__dict__.pop("gateway", None)


@pytest.fixture
def make_gateway(monkeypatch):
    """フェイク bpy を差し込んで bli_addon.gateway を新規 import するファクトリを返す。"""

    def _factory(objects: tuple[Any, ...] = ()) -> Any:
        fake_bpy = _make_fake_bpy(objects)
        monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
        _forget_gateway_module()
        return importlib.import_module("bli_addon.gateway")

    yield _factory
    _forget_gateway_module()


# ---- フェイク rna プロパティ / Modifier（identifier/type/is_readonly/array_length/enum_items/
# fixed_type/is_enum_flag を持つ最小クラス。gateway._coerce_prop_value が読む属性のみ実装）----


class _FakeEnumItem:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier


class _FakeFixedType:
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier


class _FakeProp:
    """rna Property の最小スタブ。"""

    def __init__(
        self,
        identifier: str,
        prop_type: str,
        *,
        is_readonly: bool = False,
        array_length: int = 0,
        enum_items: tuple[str, ...] = (),
        fixed_type: str | None = None,
        is_enum_flag: bool = False,
    ) -> None:
        self.identifier = identifier
        self.type = prop_type
        self.is_readonly = is_readonly
        self.array_length = array_length
        self.enum_items = [_FakeEnumItem(i) for i in enum_items]
        self.fixed_type = _FakeFixedType(fixed_type) if fixed_type is not None else None
        self.is_enum_flag = is_enum_flag


class _FakeModifier:
    """rna 検証つき setattr が効く最小 Modifier スタブ（bl_rna.properties + 属性 setattr）。"""

    def __init__(self, mod_type: str, props: tuple[_FakeProp, ...], **initial: Any) -> None:
        self.type = mod_type
        self.name = mod_type.title()
        self.bl_rna = types.SimpleNamespace(properties=list(props))
        for key, value in initial.items():
            setattr(self, key, value)


class _FakeModifiersCollection:
    """obj.modifiers の最小スタブ（new は固定 mod を返す・remove 呼び出しを記録）。"""

    def __init__(self, mod: Any) -> None:
        self._mod = mod
        self.removed: list[Any] = []

    def new(self, name: str, mod_type: str) -> Any:
        return self._mod

    def remove(self, mod: Any) -> None:
        self.removed.append(mod)


# ---- set_modifier_props: 正常系（型別設定 + applied 実値）----


def test_set_modifier_props_sets_and_returns_applied_values(make_gateway):
    gw = make_gateway()
    props_rna = (
        _FakeProp("width", "FLOAT"),
        _FakeProp("segments", "INT"),
        _FakeProp("use_clamp_overlap", "BOOLEAN"),
        _FakeProp("limit_method", "ENUM", enum_items=("ANGLE", "WEIGHT")),
        _FakeProp("vertex_group", "STRING"),
    )
    mod = _FakeModifier("BEVEL", props_rna)
    applied = gw.set_modifier_props(
        mod,
        {
            "width": 0.1,
            "segments": 3,
            "use_clamp_overlap": True,
            "limit_method": "ANGLE",
            "vertex_group": "Group",
        },
    )
    assert applied == {
        "width": 0.1,
        "segments": 3,
        "use_clamp_overlap": True,
        "limit_method": "ANGLE",
        "vertex_group": "Group",
    }
    assert mod.width == 0.1
    assert mod.segments == 3
    assert mod.use_clamp_overlap is True
    assert mod.limit_method == "ANGLE"
    assert mod.vertex_group == "Group"


def test_set_modifier_props_unknown_key_lists_valid_keys(make_gateway):
    gw = make_gateway()
    mod = _FakeModifier("BEVEL", (_FakeProp("width", "FLOAT"), _FakeProp("segments", "INT")))
    with pytest.raises(JsonRpcError) as ei:
        gw.set_modifier_props(mod, {"bogus": 1})
    assert ei.value.message == ErrorCode.INVALID_PARAMS
    assert ei.value.data.category == "USER_INPUT"
    assert "width" in ei.value.data.userVisibleSymptom
    assert "segments" in ei.value.data.userVisibleSymptom


def test_set_modifier_props_validates_all_keys_before_setattr(make_gateway):
    # 1個目（width）は有効・2個目（bogus）は未知 → 全キー検証が setattr より先のため
    # 有効な1個目も設定されない（半端な適用をしない）。
    gw = make_gateway()
    mod = _FakeModifier("BEVEL", (_FakeProp("width", "FLOAT"),), width=0.0)
    with pytest.raises(JsonRpcError) as ei:
        gw.set_modifier_props(mod, {"width": 0.5, "bogus": 1})
    assert ei.value.message == ErrorCode.INVALID_PARAMS
    assert mod.width == 0.0


# ---- _coerce_prop_value: 型別の検証・変換 ----


def test_coerce_prop_value_float_rejects_bool(make_gateway):
    gw = make_gateway()
    prop = _FakeProp("val", "FLOAT")
    with pytest.raises(JsonRpcError) as ei:
        gw._coerce_prop_value(prop, True)
    assert ei.value.message == ErrorCode.INVALID_PARAMS
    assert ei.value.data.category == "USER_INPUT"


def test_coerce_prop_value_int_rejects_float(make_gateway):
    gw = make_gateway()
    prop = _FakeProp("val", "INT")
    with pytest.raises(JsonRpcError) as ei:
        gw._coerce_prop_value(prop, 1.5)
    assert ei.value.message == ErrorCode.INVALID_PARAMS
    assert ei.value.data.category == "USER_INPUT"


def test_coerce_prop_value_float_array_wrong_length(make_gateway):
    gw = make_gateway()
    prop = _FakeProp("val", "FLOAT", array_length=3)
    with pytest.raises(JsonRpcError) as ei:
        gw._coerce_prop_value(prop, [1.0, 2.0])
    assert ei.value.message == ErrorCode.INVALID_PARAMS
    assert ei.value.data.category == "USER_INPUT"


def test_coerce_prop_value_enum_invalid_value_lists_valid(make_gateway):
    gw = make_gateway()
    prop = _FakeProp("val", "ENUM", enum_items=("ANGLE", "WEIGHT"))
    with pytest.raises(JsonRpcError) as ei:
        gw._coerce_prop_value(prop, "BOGUS")
    assert ei.value.message == ErrorCode.INVALID_PARAMS
    assert ei.value.data.category == "USER_INPUT"
    assert "ANGLE" in ei.value.data.userVisibleSymptom
    assert "WEIGHT" in ei.value.data.userVisibleSymptom


def test_coerce_prop_value_enum_flag_unsupported(make_gateway):
    gw = make_gateway()
    prop = _FakeProp("val", "ENUM", enum_items=("A", "B"), is_enum_flag=True)
    with pytest.raises(JsonRpcError) as ei:
        gw._coerce_prop_value(prop, "A")
    assert ei.value.message == ErrorCode.INVALID_PARAMS
    assert ei.value.data.category == "USER_INPUT"


def test_coerce_prop_value_pointer_object_resolves_by_name(make_gateway):
    target = _FakeTargetObj("Empty")
    gw = make_gateway((target,))
    prop = _FakeProp("val", "POINTER", fixed_type="Object")
    resolved = gw._coerce_prop_value(prop, "Empty")
    assert resolved is target


def test_coerce_prop_value_pointer_object_unknown_name_not_found(make_gateway):
    gw = make_gateway()
    prop = _FakeProp("val", "POINTER", fixed_type="Object")
    with pytest.raises(JsonRpcError) as ei:
        gw._coerce_prop_value(prop, "NoSuchObject")
    assert ei.value.message == ErrorCode.E_TARGET_NOT_FOUND
    assert ei.value.data.category == "USER_INPUT"


def test_coerce_prop_value_pointer_collection_unsupported(make_gateway):
    gw = make_gateway()
    prop = _FakeProp("val", "POINTER", fixed_type="Collection")
    with pytest.raises(JsonRpcError) as ei:
        gw._coerce_prop_value(prop, "Whatever")
    assert ei.value.message == ErrorCode.INVALID_PARAMS
    assert ei.value.data.category == "USER_INPUT"


def test_coerce_prop_value_float_rejects_nan(make_gateway):
    gw = make_gateway()
    prop = _FakeProp("val", "FLOAT")
    with pytest.raises(JsonRpcError) as ei:
        gw._coerce_prop_value(prop, float("nan"))
    assert ei.value.message == ErrorCode.INVALID_PARAMS
    assert ei.value.data.category == "USER_INPUT"


# ---- add_modifier: props 失敗時のアトミック性（追加した modifier を撤去してから送出）----


def test_add_modifier_removes_modifier_when_props_fail(make_gateway):
    gw = make_gateway()
    mod = _FakeModifier("BEVEL", (_FakeProp("width", "FLOAT"),))
    modifiers = _FakeModifiersCollection(mod)
    obj = types.SimpleNamespace(modifiers=modifiers)
    with pytest.raises(JsonRpcError):
        gw.add_modifier(obj, "BEVEL", props={"bogus": 1})
    assert modifiers.removed == [mod]
