"""SSOT（bli_core.definitions）から Typer コマンドを動的生成する共通ファクトリ（P2-2）。

`models.model_for()` が SSOT から Pydantic 検証モデルを動的生成しているのと同じパターンを
Typer に広げる。definitions.py の Param（型/required/default/choices/help）から typer.Option
を組み立て、動的シグネチャ（`__signature__`）付きコールバックとして app に登録する。

新コマンドの必須変更は「definitions.py + ops/ + gateway/（+任意で formatters.py の
HUMAN_FORMATTERS 登録）」に減る。CLI 固有の互換情報（help 文言の差・別名・送信ポリシー例外・
手書きバリデーション）は cli_specs.py のオーバーライド表に集約し、SSOT 側は変更しない
（schema_hash 不変＝挙動を変えない内部リファクタ。report §4 P2-2）。

送信ポリシー（params dict への載せ方）の既定則:
- required                → 常時送信
- BOOL default=True       → `--x/--no-x` スイッチ・常時送信
- BOOL それ以外           → 値なしフラグ・True のときだけ送信（presence-sensitive）
- 非BOOL default あり     → その default で常時送信
- 非BOOL default なし     → Optional・指定時のみ送信（presence-sensitive）
例外は CmdSpec.always_send / tristate で個別指定する（例: export の use_selection は
default=False だが常時送信・apply_unit_scale は三値 `--x/--no-x`）。
"""

from __future__ import annotations

import inspect
import json
import keyword
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import typer

from bli_core.commands import Command, Param, load_definitions
from bli_core.errors import ErrorCode, ExitCode
from bli_core.types import ParamType

from .cli_specs import (
    DEFAULT_OPTION_NAMES,
    EXCLUDED_COMMANDS,
    GENERATED_ORDER,
    SPECS,
    CmdSpec,
)
from .formatters import HUMAN_FORMATTERS

_VEC_SIZE = {ParamType.VEC3: 3, ParamType.VEC4: 4}
_PY_ANNOT = {
    ParamType.STR: str,
    ParamType.PATH: str,
    ParamType.ENUM: str,  # 現行 CLI と同じく素の文字列で受け、enum 検証は送信前 Pydantic に委ねる
    ParamType.INT: int,
    ParamType.FLOAT: float,
    ParamType.BOOL: bool,
}

# 全コマンド共通の固定文言（手書き時代の文言をそのまま踏襲）
_FETCH_HELP = "退避(output_ref)を読み込み sha256 検証して展開する"
_JSON_HELP = "JSON で出力"
_ID_HELP_DEFAULT = "リクエストID(UUIDv4)"
_ASYNC_HELP_DEFAULT = "job_id を即返し（既定は完了まで自動待機）"


@dataclass(frozen=True)
class FactoryContext:
    """main.py のインフラ関数への注入点（factory→main の循環 import を避ける）。"""

    rpc: Callable[..., None]
    emit_error: Callable[..., None]
    parse_vec: Callable[[str, str, int], list[float]]


@dataclass(frozen=True)
class _Entry:
    """ドメイン param 1 個ぶんの生成計画。"""

    param: Param
    py_name: str
    send: str  # "required" | "always" | "flag" | "optional"
    vec_n: int | None


def _py_name(name: str, spec: CmdSpec) -> str:
    override = spec.py_names.get(name)
    if override is not None:
        return override
    return name + "_" if keyword.iskeyword(name) else name


def _send_policy(p: Param, spec: CmdSpec) -> str:
    if p.name in spec.cli_defaults:
        return "always"  # CLI 側既定値を持つ＝常時送信（print-export の format=stl）
    if p.required:
        return "required"
    if p.name in spec.always_send:
        return "always"
    if p.type is ParamType.BOOL:
        if p.name in spec.tristate:
            return "optional"
        return "always" if p.default is True else "flag"
    return "always" if p.default is not None else "optional"


def _option_names(p: Param, spec: CmdSpec) -> tuple[str, ...]:
    names = spec.option_names.get(p.name) or DEFAULT_OPTION_NAMES.get(p.name)
    return names or ("--" + p.name.replace("_", "-"),)


def _typer_param(entry: _Entry, spec: CmdSpec) -> tuple[Any, Any]:
    """(annotation, typer.Option) を返す。"""
    p = entry.param
    names = _option_names(p, spec)
    help_ = spec.help_overrides.get(p.name, p.help)
    if entry.vec_n is not None:
        # VEC は "a,b,..." 文字列で受けてコールバック内で _parse_vec する（手書き時代と同じ）
        return str | None, typer.Option(None, *names, help=help_)
    base = _PY_ANNOT[p.type]
    if p.type is ParamType.BOOL:
        switch = f"{names[0]}/--no-{names[0][2:]}"
        if p.name in spec.tristate:
            return bool | None, typer.Option(None, switch, help=help_)
        if p.default is True:
            return bool, typer.Option(True, switch, help=help_)
        return bool, typer.Option(False, *names, help=help_)
    if p.name in spec.cli_defaults:
        return base, typer.Option(spec.cli_defaults[p.name], *names, help=help_)
    if p.required:
        return base, typer.Option(..., *names, help=help_)
    if p.default is not None:
        return base, typer.Option(p.default, *names, help=help_)
    return base | None, typer.Option(None, *names, help=help_)


def _entries(cmd: Command, spec: CmdSpec) -> list[_Entry]:
    by_name = {p.name: p for p in cmd.params}
    order = list(spec.param_order) if spec.param_order else [p.name for p in cmd.params]
    if set(order) != set(by_name):
        raise ValueError(f"{cmd.name}: param_order が SSOT と一致しません: {order}")
    return [
        _Entry(
            param=by_name[name],
            py_name=_py_name(name, spec),
            send=_send_policy(by_name[name], spec),
            vec_n=_VEC_SIZE.get(by_name[name].type),
        )
        for name in order
    ]


def _json_fallback(data: dict[str, Any]) -> str:
    """HUMAN_FORMATTERS 未登録コマンドのフォールバック（JSON 整形・report §4）。"""
    return json.dumps(data, ensure_ascii=False)


def _make_callback(
    cmd: Command, spec: CmdSpec, entries: list[_Entry], ctx: FactoryContext
) -> Callable[..., None]:
    method = spec.method or cmd.name
    human = HUMAN_FORMATTERS.get(cmd.name, _json_fallback)
    has_fetch = spec.with_fetch
    has_id = (
        spec.with_request_id if spec.with_request_id is not None else (cmd.mutates or cmd.is_heavy)
    )
    has_async = cmd.is_heavy or bool(cmd.heavy_ops)

    def callback(**kw: Any) -> None:
        json_out: bool = kw["json_out"]
        if spec.pre_hook is not None:
            spec.pre_hook(kw, json_out, ctx)
        if spec.build is not None:
            params = spec.build(kw, json_out, ctx)
        else:
            params: dict[str, Any] = {}
            for e in entries:
                v = kw[e.py_name]
                if e.send == "flag":
                    if v:
                        params[e.param.name] = True
                    continue
                if e.send == "optional" and v is None:
                    continue
                if e.vec_n is not None:
                    try:
                        v = ctx.parse_vec(e.param.name.replace("_", "-"), v, e.vec_n)
                    except ValueError as exc:
                        ctx.emit_error(json_out, ErrorCode.INVALID_PARAMS, str(exc))
                        raise typer.Exit(int(ExitCode.INPUT)) from None
                params[e.param.name] = v
        # infra オプションは「このコマンドに付いているときだけ」読む。ドメイン param が
        # 同じ py_name を持ち得る（request-status の id→request_id）ため kw.get では取り違える。
        ctx.rpc(
            method,
            params,
            json_out=json_out,
            port=kw["port"],
            human=human,
            request_id=kw["request_id"] if has_id else None,
            fetch=kw["fetch"] if has_fetch else False,
            async_=kw["async_out"] if has_async else False,
        )

    # Typer は inspect.signature() を読む＝ __signature__ で動的シグネチャを与えられる
    sig: list[inspect.Parameter] = []

    def add(name: str, annotation: Any, default: Any) -> None:
        sig.append(
            inspect.Parameter(
                name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=annotation
            )
        )

    for e in entries:
        annotation, option = _typer_param(e, spec)
        add(e.py_name, annotation, option)
    if has_fetch:
        add("fetch", bool, typer.Option(False, "--fetch", help=_FETCH_HELP))
    if has_id:
        add(
            "request_id",
            str | None,
            typer.Option(None, "--id", help=spec.request_id_help or _ID_HELP_DEFAULT),
        )
    if has_async:
        add(
            "async_out",
            bool,
            typer.Option(False, "--async", help=spec.async_help or _ASYNC_HELP_DEFAULT),
        )
    add("json_out", bool, typer.Option(False, "--json", help=_JSON_HELP))
    add("port", int | None, typer.Option(None, "--port"))

    callback.__signature__ = inspect.Signature(sig)  # type: ignore[attr-defined]
    callback.__doc__ = spec.doc if spec.doc is not None else cmd.summary
    callback.__name__ = cmd.name.replace("-", "_")
    return callback


def register_generated_commands(app: typer.Typer, ctx: FactoryContext) -> None:
    """SSOT の全コマンド（EXCLUDED を除く）を Typer コマンドとして登録する。

    登録順は GENERATED_ORDER（`bli --help` の一覧順＝手書き時代の互換）。載っていない
    新コマンドは末尾へアルファベット順で自動追加される（追加時の必須変更を増やさない）。
    """
    cmds = load_definitions()
    ordered = [n for n in GENERATED_ORDER if n in cmds]
    ordered += sorted(n for n in cmds if n not in ordered)
    for name in ordered:
        if name in EXCLUDED_COMMANDS:
            continue
        cmd = cmds[name]
        spec = SPECS.get(name, CmdSpec())
        entries = _entries(cmd, spec)
        app.command(name)(_make_callback(cmd, spec, entries, ctx))
