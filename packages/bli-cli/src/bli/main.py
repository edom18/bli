"""bli CLI エントリポイント（Typer）。M2: init / ping / doctor。

終了コード（spec §8）: 0=成功 / 1=確定失敗 / 3=接続不能・認証失敗 / 4=入力エラー。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from bli_core.errors import ErrorCategory, ErrorCode, ExitCode

from . import client, config

app = typer.Typer(
    name="bli",
    help="Blender CLI: AIエージェント向けに Blender を CLI で操作する。",
    no_args_is_help=True,
    add_completion=False,
)


def _emit(json_out: bool, human: str, payload: dict[str, Any]) -> None:
    if json_out:
        typer.echo(json.dumps(payload, ensure_ascii=False))
    else:
        typer.echo(human)


def _emit_error(json_out: bool, kind: str, message: str) -> None:
    if json_out:
        typer.echo(
            json.dumps({"ok": False, "kind": kind, "message": message}, ensure_ascii=False),
            err=True,
        )
    else:
        typer.echo(f"エラー[{kind}]: {message}", err=True)


def _exit_code_for(err: dict[str, Any]) -> ExitCode:
    """サーバ error の category から終了コードを決める（spec §8）。"""
    kind = err.get("message", "")
    data = err.get("data")
    category = data.get("category") if isinstance(data, dict) else None
    if kind == ErrorCode.INVALID_PARAMS or category == ErrorCategory.USER_INPUT:
        return ExitCode.INPUT
    return ExitCode.FAILURE


def _rpc(
    method: str,
    params: dict[str, Any],
    *,
    json_out: bool,
    port: int | None,
    human: Callable[[dict[str, Any]], str],
) -> None:
    """RPC を1往復し結果を出力する（接続/業務エラーは終了コードへ写像）。"""
    try:
        result, _hello = client.call(method, params, port=port)
    except client.ConnectError as e:
        _emit_error(json_out, "CONNECTION", str(e))
        raise typer.Exit(int(ExitCode.CONNECTION)) from None
    except client.RpcRemoteError as e:
        data = e.error.get("data") if isinstance(e.error.get("data"), dict) else {}
        symptom = data.get("userVisibleSymptom") or str(e)
        _emit_error(json_out, e.error.get("message", "RPC_ERROR"), symptom)
        raise typer.Exit(int(_exit_code_for(e.error))) from None

    payload: dict[str, Any] = {"ok": True, "operation": result.get("operation", method)}
    for key in ("verified", "fingerprint", "output_ref", "data"):
        if key in result:
            payload[key] = result[key]
    _emit(json_out, human(result.get("data") or {}), payload)


@app.command()
def ping(
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port", help="接続ポート（既定は connection.json）"),
) -> None:
    """アドオンへ疎通確認する（HELLO→ping）。"""
    try:
        result, hello = client.call("ping", port=port)
    except client.ConnectError as e:
        _emit_error(json_out, "CONNECTION", str(e))
        raise typer.Exit(int(ExitCode.CONNECTION)) from None
    except client.RpcRemoteError as e:
        _emit_error(json_out, e.error.get("message", "RPC_ERROR"), str(e))
        raise typer.Exit(int(ExitCode.FAILURE)) from None

    payload = {
        "ok": True,
        "protocol_version": hello.get("protocol_version"),
        "blender_version": hello.get("blender_version"),
        "schema_hash": hello.get("schema_hash"),
        "capabilities": hello.get("capabilities", []),
        "ping": result.get("data"),
    }
    human = (
        f"pong: Blender {payload['blender_version']} "
        f"(protocol {payload['protocol_version']}, schema {str(payload['schema_hash'])[:12]})"
    )
    _emit(json_out, human, payload)


@app.command()
def doctor(
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """環境診断（connection.json/token の有無・アドオン到達性）。"""
    from bli_core import runtime

    cp = runtime.connection_path()
    tp = runtime.token_path()
    reachable = False
    detail = ""
    blender_version = None
    try:
        _result, hello = client.call("ping", port=port, timeout=5.0)
        reachable = True
        blender_version = hello.get("blender_version")
    except client.ConnectError as e:
        detail = str(e)
    except client.RpcRemoteError as e:
        detail = f"認証/RPCエラー: {e}"

    payload = {
        "connection_json": cp.exists(),
        "connection_path": str(cp),
        "token_present": tp.exists(),
        "addon_reachable": reachable,
        "blender_version": blender_version,
        "detail": detail,
    }
    human = "\n".join(
        [
            "bli doctor:",
            f"  connection.json : {'あり' if payload['connection_json'] else 'なし'} ({cp})",
            f"  token           : {'あり' if payload['token_present'] else 'なし'}",
            f"  アドオン到達     : {'OK (Blender ' + str(blender_version) + ')' if reachable else 'NG'}",
        ]
        + ([f"  詳細            : {detail}"] if detail else [])
    )
    _emit(json_out, human, payload)


@app.command()
def init(
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    force: bool = typer.Option(False, "--force", help="既存ファイルを上書き"),
) -> None:
    """プロジェクトに .bli/ 設定雛形を作成する。"""
    created = config.write_project_scaffold(Path.cwd(), force=force)
    payload = {"ok": True, "created": created}
    human = (
        "作成: " + ", ".join(created) if created else ".bli/ は既に存在します（--force で上書き）"
    )
    _emit(json_out, human, payload)


@app.command("scene-info")
def scene_info(
    depth: int = typer.Option(1, "--depth", help="階層の深さ"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """シーンのオブジェクト一覧/単位設定を取得する。"""

    def human(data: dict[str, Any]) -> str:
        names = ", ".join(o["name"] for o in data.get("objects", []))
        return f"scene '{data.get('scene')}': {data.get('object_count')} objects [{names}]"

    _rpc("scene-info", {"depth": depth}, json_out=json_out, port=port, human=human)


@app.command("object-info")
def object_info(
    targets: str = typer.Argument(..., help="対象オブジェクト（name|regex）"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトの寸法/頂点数/transform/材質/modifier を取得する。"""

    def human(data: dict[str, Any]) -> str:
        return (
            f"{data.get('name')} ({data.get('type')}): "
            f"loc={data.get('location')} dims={data.get('dimensions')}"
        )

    _rpc("object-info", {"targets": targets}, json_out=json_out, port=port, human=human)


@app.command("set-origin")
def set_origin(
    targets: str = typer.Argument(..., help="対象オブジェクト（name|regex）"),
    to: str = typer.Option(..., "--to", help="原点の決め方: geometry|cursor|world"),
    center: str | None = typer.Option(None, "--center", help="geometry時の中心: median|bounds"),
    x: float | None = typer.Option(None, "--x", help="world時のX"),
    y: float | None = typer.Option(None, "--y", help="world時のY"),
    z: float | None = typer.Option(None, "--z", help="world時のZ"),
    make_single_user: bool = typer.Option(
        False, "--make-single-user", help="共有mesh時に単一ユーザ化を許可"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトの原点を変更する。"""
    params: dict[str, Any] = {"targets": targets, "to": to}
    if center is not None:
        params["center"] = center
    if x is not None:
        params["x"] = x
    if y is not None:
        params["y"] = y
    if z is not None:
        params["z"] = z
    if make_single_user:
        params["make_single_user"] = True

    def human(data: dict[str, Any]) -> str:
        return f"origin of {data.get('name')} -> {data.get('to')} @ {data.get('origin_world')}"

    _rpc("set-origin", params, json_out=json_out, port=port, human=human)


if __name__ == "__main__":
    app()
