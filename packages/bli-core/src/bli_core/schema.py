"""JSON Schema 生成・パラメータ検証・schema_hash。data-model.md §1 / spec §11。

純Python。CLI 側の Pydantic 生成スキーマと意味的に一致させ、`schema_hash` で
SSOT ドリフトを CI 検出する。
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .commands import Command, Param
from .errors import ErrorCategory, ErrorCode, ErrorObject, make_error
from .types import ParamType

_JSON_TYPE = {
    ParamType.STR: {"type": "string"},
    ParamType.PATH: {"type": "string"},
    ParamType.INT: {"type": "integer"},
    ParamType.FLOAT: {"type": "number"},
    ParamType.BOOL: {"type": "boolean"},
    ParamType.VEC3: {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    ParamType.VEC4: {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
}


def _param_schema(param: Param) -> dict[str, Any]:
    if param.type is ParamType.ENUM:
        node: dict[str, Any] = {"type": "string"}
        if param.choices is not None:
            node["enum"] = list(param.choices)
    else:
        node = dict(_JSON_TYPE[param.type])
    if param.help:
        node["description"] = param.help
    if param.default is not None:
        node["default"] = param.default
    return node


def to_json_schema(cmd: Command) -> dict[str, Any]:
    """コマンドの params から JSON Schema(draft 2020-12) を生成する。"""
    properties = {param.name: _param_schema(param) for param in cmd.params}
    required = [param.name for param in cmd.params if param.required]
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": cmd.name,
        "description": cmd.summary,
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _check_type(param: Param, value: Any) -> bool:
    t = param.type
    if t in (ParamType.STR, ParamType.PATH):
        return isinstance(value, str)
    if t is ParamType.ENUM:
        return isinstance(value, str) and (param.choices is None or value in param.choices)
    if t is ParamType.INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if t is ParamType.FLOAT:
        # nan/inf は行列を壊すため拒否する（サーバが信頼境界。CLI 非経由の RPC も保護）。
        return (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )
    if t is ParamType.BOOL:
        return isinstance(value, bool)
    if t in (ParamType.VEC3, ParamType.VEC4):
        n = 3 if t is ParamType.VEC3 else 4
        return (
            isinstance(value, (list, tuple))
            and len(value) == n
            and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in value
            )
        )
    return False


def validate_from_dict(cmd: Command, params: dict[str, Any]) -> list[ErrorObject]:
    """params を検証し ErrorObject のリストを返す（空 = 妥当）。"""
    errors: list[ErrorObject] = []
    by_name = {param.name: param for param in cmd.params}

    for param in cmd.params:
        if param.required and param.name not in params:
            errors.append(
                make_error(
                    ErrorCode.INVALID_PARAMS,
                    category=ErrorCategory.USER_INPUT,
                    cause=f"missing:{param.name}",
                    symptom=f"必須パラメータ '{param.name}' がありません",
                    remediation=f"--{param.name} を指定してください",
                )
            )

    for key, value in params.items():
        param = by_name.get(key)
        if param is None:
            errors.append(
                make_error(
                    ErrorCode.INVALID_PARAMS,
                    category=ErrorCategory.USER_INPUT,
                    cause=f"unknown:{key}",
                    symptom=f"未知のパラメータ '{key}'",
                    remediation=f"{cmd.name} の有効なパラメータは: {', '.join(by_name)}",
                )
            )
            continue
        if not _check_type(param, value):
            errors.append(
                make_error(
                    ErrorCode.INVALID_PARAMS,
                    category=ErrorCategory.USER_INPUT,
                    cause=f"type:{key}",
                    symptom=f"パラメータ '{key}' の型/値が不正です（期待: {param.type.value}）",
                    remediation=f"'{key}' は {param.type.value} で指定してください",
                )
            )
    return errors


def _command_to_canonical(cmd: Command) -> dict[str, Any]:
    return {
        "name": cmd.name,
        "summary": cmd.summary,
        "mutates": cmd.mutates,
        "required_mode": cmd.required_mode.value,
        "capability_deps": list(cmd.capability_deps),
        "is_heavy": cmd.is_heavy,
        "stability": cmd.stability.value,
        "implemented": cmd.implemented,
        "result_schema": cmd.result_schema,
        "params": [
            {
                "name": param.name,
                "type": param.type.value,
                "required": param.required,
                "default": param.default,
                "choices": list(param.choices) if param.choices is not None else None,
                "help": param.help,
            }
            for param in cmd.params
        ],
    }


def schema_hash(commands: dict[str, Command]) -> str:
    """全コマンド定義の決定的 SHA256（順序非依存）。"""
    canonical = {name: _command_to_canonical(cmd) for name, cmd in commands.items()}
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
