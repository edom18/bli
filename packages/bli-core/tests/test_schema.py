"""schema 生成・検証・schema_hash のユニット（L1）。"""

from __future__ import annotations

from bli_core.commands import Command, Param, get_command, load_definitions
from bli_core.schema import schema_hash, to_json_schema, validate_from_dict
from bli_core.types import ParamType


def _setup():
    load_definitions()


def test_to_json_schema_set_origin():
    _setup()
    schema = to_json_schema(get_command("set-origin"))
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "targets" in schema["required"]
    assert "to" in schema["required"]
    assert schema["properties"]["to"]["enum"] == ["geometry", "cursor", "world"]
    assert schema["properties"]["x"]["type"] == "number"


def test_to_json_schema_vec3():
    _setup()
    schema = to_json_schema(get_command("transform"))
    loc = schema["properties"]["location"]
    assert loc["type"] == "array"
    assert loc["minItems"] == 3 and loc["maxItems"] == 3


def test_validate_missing_required():
    _setup()
    errs = validate_from_dict(get_command("set-origin"), {"to": "geometry"})
    causes = {e.cause for e in errs}
    assert "missing:targets" in causes


def test_validate_unknown_param():
    _setup()
    errs = validate_from_dict(
        get_command("set-origin"), {"targets": "Cube", "to": "geometry", "nope": 1}
    )
    assert any(e.cause == "unknown:nope" for e in errs)


def test_validate_enum_and_types():
    _setup()
    cmd = get_command("set-origin")
    # 不正な enum 値
    errs = validate_from_dict(cmd, {"targets": "Cube", "to": "bad"})
    assert any(e.cause == "type:to" for e in errs)
    # 正常
    assert (
        validate_from_dict(cmd, {"targets": "Cube", "to": "world", "x": 1.0, "y": 2, "z": 0}) == []
    )


def test_validate_vec3():
    _setup()
    cmd = get_command("transform")
    assert validate_from_dict(cmd, {"targets": "Cube", "location": [1, 2, 3]}) == []
    errs = validate_from_dict(cmd, {"targets": "Cube", "location": [1, 2]})
    assert any(e.cause == "type:location" for e in errs)


def test_bool_not_int():
    # int パラメータに bool を渡すと不正（bool は int のサブクラスだが区別する）
    cmd = Command(name="t", summary="", params=(Param("n", ParamType.INT),))
    assert any(e.cause == "type:n" for e in validate_from_dict(cmd, {"n": True}))


def test_schema_hash_deterministic_and_order_independent():
    a = Command("a", "A", params=(Param("x", ParamType.INT),))
    b = Command("b", "B")
    h1 = schema_hash({"a": a, "b": b})
    h2 = schema_hash({"b": b, "a": a})  # 挿入順違い
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_schema_hash_changes_on_definition_change():
    a1 = Command("a", "A", params=(Param("x", ParamType.INT),))
    a2 = Command("a", "A", params=(Param("x", ParamType.FLOAT),))  # 型変更
    assert schema_hash({"a": a1}) != schema_hash({"a": a2})
