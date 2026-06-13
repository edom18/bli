"""bli-core の Command 定義から Pydantic モデルを動的生成する（CLI の検証層）。

bli-core は純Python（dataclass + 手書き JSON Schema、依存ゼロ）。CLI 側はここで
Pydantic に変換し、送信前のローカル検証に使う。両者の表現が一致することを
`tests/test_models_parity.py` の parity テストで保証し、SSOT ドリフトを検出する。

JSON Schema の正本は bli-core（`to_json_schema`）。Pydantic は検証専用。
"""

from __future__ import annotations

from functools import cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from bli_core.commands import Param, get_command, load_definitions
from bli_core.types import ParamType

_PY_TYPE: dict[ParamType, Any] = {
    ParamType.STR: str,
    ParamType.PATH: str,
    ParamType.INT: int,
    ParamType.FLOAT: float,
    ParamType.BOOL: bool,
    ParamType.VEC3: tuple[float, float, float],
}


class ParamValidationError(Exception):
    """ローカル param 検証エラー（CLI 終了コード 4 相当）。"""

    def __init__(self, command: str, detail: str) -> None:
        self.command = command
        self.detail = detail
        super().__init__(f"{command}: {detail}")


def _field_type(param: Param) -> Any:
    if param.type is ParamType.ENUM and param.choices:
        return Literal[tuple(param.choices)]  # type: ignore[valid-type]  # 動的 Literal
    return _PY_TYPE[param.type]


@cache
def model_for(name: str) -> type[BaseModel]:
    """コマンド名から Pydantic モデルを生成する（キャッシュ）。"""
    load_definitions()
    cmd = get_command(name)
    if cmd is None:
        raise KeyError(name)
    fields: dict[str, Any] = {}
    for param in cmd.params:
        ftype = _field_type(param)
        if param.required:
            fields[param.name] = (ftype, ...)
        else:
            fields[param.name] = (ftype | None, param.default)
    return create_model(
        f"{name.replace('-', '_')}_params",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def _format(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def validate_params(name: str, params: dict[str, Any]) -> None:
    """params を Pydantic で検証する。COMMANDS 外はスキップ。不正なら ParamValidationError。"""
    load_definitions()
    if get_command(name) is None:
        return
    try:
        model_for(name).model_validate(params)
    except ValidationError as exc:
        raise ParamValidationError(name, _format(exc)) from exc
