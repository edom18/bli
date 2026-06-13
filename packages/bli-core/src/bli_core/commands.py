"""コマンド定義（SSOT）。data-model.md §1。純Python・依存ゼロ。

`command(...)` で COMMANDS レジストリに登録する。実際のコマンド定義は
`definitions.py` に置き、`load_definitions()` で読み込む。
ハンドラ（bpy実行）はアドオン側に持ち、ここには **定義（メタ情報）のみ**を置く。
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .types import Mode, ParamType, Stability


@dataclasses.dataclass(frozen=True)
class Param:
    name: str
    type: ParamType
    required: bool = False
    default: Any = None
    choices: list[str] | None = None
    help: str = ""


@dataclasses.dataclass(frozen=True)
class Command:
    name: str
    summary: str
    params: tuple[Param, ...] = ()
    mutates: bool = False
    required_mode: Mode = Mode.ANY
    capability_deps: tuple[str, ...] = ()
    is_heavy: bool = False
    stability: Stability = Stability.STABLE
    result_schema: dict[str, Any] | None = None


# 全コマンド定義のレジストリ（name -> Command）
COMMANDS: dict[str, Command] = {}


def p(
    name: str,
    type: ParamType,
    *,
    required: bool = False,
    default: Any = None,
    choices: list[str] | None = None,
    help: str = "",
) -> Param:
    """Param 生成の薄いヘルパ。"""
    return Param(
        name=name, type=type, required=required, default=default, choices=choices, help=help
    )


def command(
    name: str,
    summary: str,
    *,
    params: tuple[Param, ...] = (),
    mutates: bool = False,
    required_mode: Mode = Mode.ANY,
    capability_deps: tuple[str, ...] = (),
    is_heavy: bool = False,
    stability: Stability = Stability.STABLE,
    result_schema: dict[str, Any] | None = None,
) -> Command:
    """Command を生成し COMMANDS に登録する。"""
    if name in COMMANDS:
        raise ValueError(f"duplicate command: {name}")
    cmd = Command(
        name=name,
        summary=summary,
        params=tuple(params),
        mutates=mutates,
        required_mode=required_mode,
        capability_deps=tuple(capability_deps),
        is_heavy=is_heavy,
        stability=stability,
        result_schema=result_schema,
    )
    COMMANDS[name] = cmd
    return cmd


def get_command(name: str) -> Command | None:
    return COMMANDS.get(name)


_loaded = False


def load_definitions() -> dict[str, Command]:
    """definitions.py を読み込み COMMANDS を populate する（冪等）。"""
    global _loaded
    if not _loaded:
        from . import definitions  # noqa: F401  （import 副作用で登録）

        _loaded = True
    return COMMANDS
