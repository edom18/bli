"""Pydantic モデル（CLI 検証層）と bli-core 手書き JSON Schema の parity（L1）。

両者は同じ Command 定義から独立に派生する。表現がズレる（= SSOT ドリフト）と
ここで fail させる。比較は「required 集合」「プロパティ名集合」「各プロパティの
正規化型/enum」の3観点。
"""

from __future__ import annotations

from typing import Any

import pytest

from bli.models import ParamValidationError, model_for, validate_params
from bli_core.commands import load_definitions
from bli_core.schema import to_json_schema


def _node_facts(node: dict[str, Any]) -> tuple[str | None, tuple | None]:
    # Optional は anyOf[..., null] になるので非 null を取り出す
    if "anyOf" in node:
        subs = [s for s in node["anyOf"] if s.get("type") != "null"]
        node = subs[0] if subs else {}
    if "enum" in node:  # enum は型表現差を無視して列挙値で比較
        return ("enum", tuple(node["enum"]))
    return (node.get("type"), None)


def _facts(schema: dict[str, Any]) -> tuple[set[str], dict[str, Any]]:
    required = set(schema.get("required", []))
    props = {name: _node_facts(node) for name, node in schema.get("properties", {}).items()}
    return required, props


def test_pydantic_matches_core_schema():
    cmds = load_definitions()
    assert cmds, "COMMANDS が空"
    for name, cmd in cmds.items():
        core_req, core_props = _facts(to_json_schema(cmd))
        pyd_req, pyd_props = _facts(model_for(name).model_json_schema())
        assert core_req == pyd_req, (name, "required", core_req, pyd_req)
        assert set(core_props) == set(pyd_props), (name, "props", set(core_props), set(pyd_props))
        for key in core_props:
            assert core_props[key] == pyd_props[key], (name, key, core_props[key], pyd_props[key])


def test_validate_params_accepts_valid():
    validate_params("set-origin", {"targets": "Cube", "to": "geometry", "center": "median"})


def test_validate_params_rejects_bad_enum():
    with pytest.raises(ParamValidationError):
        validate_params("set-origin", {"targets": "Cube", "to": "bogus"})


def test_validate_params_rejects_missing_required():
    with pytest.raises(ParamValidationError):
        validate_params("set-origin", {"to": "geometry"})


def test_validate_params_rejects_unknown_param():
    with pytest.raises(ParamValidationError):
        validate_params("scene-info", {"nope": 1})


def test_validate_params_skips_unknown_command():
    # COMMANDS に無いローカルコマンドは検証スキップ（例外を投げない）
    validate_params("help", {"anything": True})
