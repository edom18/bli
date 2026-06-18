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
    # 特定の op 値のときだけ heavy になるコマンド向け（例: mesh は boolean/decimate のみ重量）。
    # `op` パラメータの値がこの集合に入れば heavy 扱い（is_heavy=False でも）。M10 非同期 job 用。
    heavy_ops: tuple[str, ...] = ()
    stability: Stability = Stability.STABLE
    result_schema: dict[str, Any] | None = None
    # CLI/サーバで実行可能か。False=SSOT に定義済みだが未実装（発見系の既定一覧から除外）。
    implemented: bool = True


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
    heavy_ops: tuple[str, ...] = (),
    stability: Stability = Stability.STABLE,
    result_schema: dict[str, Any] | None = None,
    implemented: bool = True,
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
        heavy_ops=tuple(heavy_ops),
        stability=stability,
        result_schema=result_schema,
        implemented=implemented,
    )
    COMMANDS[name] = cmd
    return cmd


def is_heavy_request(cmd: Command, params: dict[str, Any]) -> bool:
    """この呼び出しが重量（非同期 job 化対象）かを判定する（M10・spec §7）。

    `cmd.is_heavy`（コマンド全体が重量＝import/export/print-check/print-repair）か、`cmd.heavy_ops`
    に該当する `op` 値（mesh の boolean/decimate のみ重量）なら True。純Python・bpy 非依存。
    """
    if cmd.is_heavy:
        return True
    if cmd.heavy_ops:
        return str(params.get("op", "")) in cmd.heavy_ops
    return False


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
