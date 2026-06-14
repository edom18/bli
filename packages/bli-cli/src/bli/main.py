"""bli CLI エントリポイント（Typer）。

コマンド: init / doctor / ping / request-status / scene-info / object-info /
set-origin / list-commands / help。
終了コード（spec §8）: 0=成功 / 1=確定失敗 / 2=未決 / 3=接続不能・認証失敗 / 4=入力エラー。
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from bli_core.commands import Command, load_definitions
from bli_core.errors import ErrorCategory, ErrorCode, ExitCode
from bli_core.schema import schema_hash, to_json_schema

from . import client, config, models

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


def _emit_error(json_out: bool, kind: str, message: str, request_id: str | None = None) -> None:
    if json_out:
        payload: dict[str, Any] = {"ok": False, "kind": kind, "message": message}
        if request_id is not None:
            payload["request_id"] = request_id
        typer.echo(json.dumps(payload, ensure_ascii=False), err=True)
    else:
        tail = f" (id={request_id})" if request_id is not None else ""
        typer.echo(f"エラー[{kind}]: {message}{tail}", err=True)


def _parse_vec(name: str, raw: str, n: int) -> list[float]:
    """ "a,b,..." 文字列を n 要素の float リストへ変換する（不正は ValueError）。

    VEC3（x,y,z）/ VEC4（r,g,b,a）共通。nan/inf は行列/色を壊すため弾く。
    """
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != n:
        raise ValueError(f"{name} は {n} 要素（カンマ区切り）で指定してください: {raw!r}")
    try:
        vals = [float(p) for p in parts]
    except ValueError as e:
        raise ValueError(f"{name} の数値が不正です: {raw!r}") from e
    if not all(math.isfinite(v) for v in vals):
        raise ValueError(f"{name} に有限でない値（nan/inf）は指定できません: {raw!r}")
    return vals


def _exit_code_for(err: dict[str, Any]) -> ExitCode:
    """サーバ error の kind/category から終了コードを決める（spec §8）。"""
    kind = err.get("message", "")
    data = err.get("data")
    category = data.get("category") if isinstance(data, dict) else None
    if kind == ErrorCode.TIMEOUT:
        return ExitCode.TIMEOUT_PENDING  # 未決: request-status で後追い
    if kind == ErrorCode.INVALID_PARAMS or category == ErrorCategory.USER_INPUT:
        return ExitCode.INPUT
    return ExitCode.FAILURE


def _call_or_exit(
    method: str,
    params: dict[str, Any] | None,
    *,
    json_out: bool,
    port: int | None,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """client.call をラップし、接続/業務エラーを終了コードへ写像する（id 提示つき）。

    成功時は (result, hello)。TIMEOUT は exit 2 + request-status ヒント、接続不能は exit 3。
    RPC を送る全コマンド（_rpc / ping）で共通利用する。
    """
    try:
        return client.call(method, params, request_id=request_id, port=port)
    except client.ConnectError as e:
        _emit_error(json_out, "CONNECTION", str(e), request_id=request_id)
        raise typer.Exit(int(ExitCode.CONNECTION)) from None
    except client.RpcRemoteError as e:
        kind = e.error.get("message", "RPC_ERROR")
        data = e.error.get("data") if isinstance(e.error.get("data"), dict) else {}
        symptom = data.get("userVisibleSymptom") or str(e)
        if kind == ErrorCode.TIMEOUT:
            symptom = f"{symptom}（後追い: bli request-status --id {request_id}）"
        _emit_error(json_out, kind, symptom, request_id=request_id)
        raise typer.Exit(int(_exit_code_for(e.error))) from None


def _rpc(
    method: str,
    params: dict[str, Any],
    *,
    json_out: bool,
    port: int | None,
    human: Callable[[dict[str, Any]], str],
    request_id: str | None = None,
    fetch: bool = False,
) -> None:
    """RPC を1往復し結果を出力する（接続/業務エラーは終了コードへ写像）。

    結果が output_ref(shared-fs) を含む場合、既定は **参照のみ** を返す（エージェント向け
    オンデマンド取得）。`fetch=True` のときだけ退避ファイルを読み sha256 検証して data へ
    展開する。整合不一致は STALE_OUTPUT（exit 1）。
    """
    try:
        models.validate_params(method, params)  # 送信前のローカル Pydantic 検証
    except models.ParamValidationError as e:
        _emit_error(json_out, ErrorCode.INVALID_PARAMS, e.detail)
        raise typer.Exit(int(ExitCode.INPUT)) from None

    # request id は CLI 側で確定させる。TIMEOUT 等でも request-status で後追いできるよう、
    # 生成した id を必ずユーザに提示する（--id 省略時に id が見えない問題を防ぐ）。
    request_id = request_id or str(uuid.uuid4())
    result, _hello = _call_or_exit(
        method, params, json_out=json_out, port=port, request_id=request_id
    )

    raw_ref = result.get("output_ref")
    output_ref: dict[str, Any] | None = raw_ref if isinstance(raw_ref, dict) else None
    offloaded = output_ref is not None and output_ref.get("transport") == "shared-fs"
    if offloaded and fetch and output_ref is not None:
        from bli_core import output_ref as outref

        try:
            result = {**result, "data": outref.load_verified(output_ref)}
        except outref.StaleOutputError as e:
            _emit_error(json_out, ErrorCode.STALE_OUTPUT, str(e), request_id=request_id)
            raise typer.Exit(int(ExitCode.FAILURE)) from None
        offloaded = False

    payload: dict[str, Any] = {
        "ok": True,
        "operation": result.get("operation", method),
        "request_id": request_id,
    }
    for key in ("verified", "fingerprint", "output_ref", "data"):
        if key in result:
            payload[key] = result[key]

    if offloaded and output_ref is not None:
        human_msg = (
            f"[output_ref] schema={output_ref.get('schema')} "
            f"size={output_ref.get('size')}B sha256={str(output_ref.get('sha256'))[:12]} "
            f"path={output_ref.get('path')}  (--fetch で展開)"
        )
    else:
        human_msg = human(result.get("data") or {})
    _emit(json_out, human_msg, payload)


@app.command()
def ping(
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port", help="接続ポート（既定は connection.json）"),
) -> None:
    """アドオンへ疎通確認する（HELLO→ping）。"""
    # ping も実機では Dispatcher 経由で実行されるため TIMEOUT があり得る。
    # 共通の終了コード写像（TIMEOUT→exit2 + id 提示）を _rpc と揃える。
    request_id = str(uuid.uuid4())
    result, hello = _call_or_exit("ping", None, json_out=json_out, port=port, request_id=request_id)

    payload = {
        "ok": True,
        "request_id": request_id,
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
    fetch: bool = typer.Option(
        False, "--fetch", help="退避(output_ref)を読み込み sha256 検証して展開する"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """シーンのオブジェクト一覧/単位設定を取得する（大きい結果は output_ref で退避）。"""

    def human(data: dict[str, Any]) -> str:
        names = ", ".join(o["name"] for o in data.get("objects", []))
        return f"scene '{data.get('scene')}': {data.get('object_count')} objects [{names}]"

    _rpc("scene-info", {"depth": depth}, json_out=json_out, port=port, human=human, fetch=fetch)


@app.command("list-objects")
def list_objects_cmd(
    type_filter: str | None = typer.Option(
        None, "--type", help="型フィルタ（MESH/CURVE/EMPTY/LIGHT/CAMERA 等・大小無視）"
    ),
    regex: str | None = typer.Option(None, "--regex", help="名前の正規表現フィルタ（部分一致）"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """シーン内オブジェクトを type/regex でフィルタして一覧する。"""
    params: dict[str, Any] = {}
    if type_filter is not None:
        params["type"] = type_filter
    if regex is not None:
        params["regex"] = regex

    def human(data: dict[str, Any]) -> str:
        objs = data.get("objects", [])
        names = ", ".join(f"{o['name']}({o['type']})" for o in objs)
        return f"{data.get('count', len(objs))} objects [{names}]"

    _rpc("list-objects", params, json_out=json_out, port=port, human=human)


@app.command("object-info")
def object_info(
    targets: str = typer.Option(..., "--targets", help="対象オブジェクト（name|regex）"),
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
    targets: str = typer.Option(..., "--targets", help="対象オブジェクト（name|regex）"),
    to: str = typer.Option(..., "--to", help="原点の決め方: geometry|cursor|world"),
    center: str | None = typer.Option(None, "--center", help="geometry時の中心: median|bounds"),
    x: float | None = typer.Option(None, "--x", help="world時のX"),
    y: float | None = typer.Option(None, "--y", help="world時のY"),
    z: float | None = typer.Option(None, "--z", help="world時のZ"),
    make_single_user: bool = typer.Option(
        False, "--make-single-user", help="共有mesh時に単一ユーザ化を許可"
    ),
    request_id: str | None = typer.Option(
        None, "--id", help="リクエストID(UUIDv4)。冪等リトライで同一IDを再利用する"
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

    _rpc("set-origin", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def select(
    targets: str = typer.Option(..., "--targets", help="対象オブジェクト（name|regex）"),
    type_filter: str | None = typer.Option(None, "--type", help="型フィルタ（MESH/CURVE/...）"),
    active: str | None = typer.Option(None, "--active", help="active にする対象名"),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトを選択し active を設定する。"""
    params: dict[str, Any] = {"targets": targets}
    if type_filter is not None:
        params["type"] = type_filter
    if active is not None:
        params["active"] = active

    def human(data: dict[str, Any]) -> str:
        return f"selected {data.get('count')}: {data.get('selected')} active={data.get('active')}"

    _rpc("select", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def transform(
    targets: str = typer.Option(..., "--targets", help="対象オブジェクト（name|regex）"),
    location: str | None = typer.Option(None, "--location", help="位置 x,y,z"),
    rotation: str | None = typer.Option(None, "--rotation", help="回転 x,y,z（度）"),
    scale: str | None = typer.Option(None, "--scale", help="拡縮 x,y,z"),
    mode: str = typer.Option(
        "set", "--mode", help="set|delta（delta は loc/rot 加算・scale 乗算）"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトの位置/回転/拡縮を設定または相対適用する。"""
    params: dict[str, Any] = {"targets": targets, "mode": mode}
    try:
        if location is not None:
            params["location"] = _parse_vec("location", location, 3)
        if rotation is not None:
            params["rotation"] = _parse_vec("rotation", rotation, 3)
        if scale is not None:
            params["scale"] = _parse_vec("scale", scale, 3)
    except ValueError as e:
        _emit_error(json_out, ErrorCode.INVALID_PARAMS, str(e))
        raise typer.Exit(int(ExitCode.INPUT)) from None

    def human(data: dict[str, Any]) -> str:
        return (
            f"{data.get('name')}: loc={data.get('location')} "
            f"rot={data.get('rotation_euler_deg')} scale={data.get('scale')}"
        )

    _rpc("transform", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command("apply-transform")
def apply_transform_cmd(
    targets: str = typer.Option(..., "--targets", help="対象オブジェクト（name|regex）"),
    location: bool = typer.Option(False, "--location", help="位置を適用"),
    rotation: bool = typer.Option(False, "--rotation", help="回転を適用"),
    scale: bool = typer.Option(False, "--scale", help="拡縮を適用"),
    make_single_user: bool = typer.Option(
        False, "--make-single-user", help="共有mesh時に単一ユーザ化を許可"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトの transform をメッシュデータに適用する（全省略時は全適用）。"""
    params: dict[str, Any] = {"targets": targets}
    if location:
        params["location"] = True
    if rotation:
        params["rotation"] = True
    if scale:
        params["scale"] = True
    if make_single_user:
        params["make_single_user"] = True

    def human(data: dict[str, Any]) -> str:
        return (
            f"applied to {data.get('name')}: "
            f"scale={data.get('scale')} dims={data.get('dimensions')}"
        )

    _rpc(
        "apply-transform", params, json_out=json_out, port=port, human=human, request_id=request_id
    )


@app.command()
def duplicate(
    targets: str = typer.Option(..., "--targets", help="対象オブジェクト（name|regex）"),
    linked: bool = typer.Option(False, "--linked", help="データを共有する（リンク複製）"),
    count: int = typer.Option(1, "--count", help="複製数（1〜1000）"),
    offset: str | None = typer.Option(None, "--offset", help="複製ごとの world オフセット x,y,z"),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトを複製する（count 回・world offset 累積）。"""
    from bli_core import runtime

    if not 1 <= count <= runtime.MAX_DUPLICATE_COUNT:
        _emit_error(
            json_out,
            ErrorCode.INVALID_PARAMS,
            f"--count は 1〜{runtime.MAX_DUPLICATE_COUNT} です: {count}",
        )
        raise typer.Exit(int(ExitCode.INPUT))
    params: dict[str, Any] = {"targets": targets, "count": count}
    if linked:
        params["linked"] = True
    try:
        if offset is not None:
            params["offset"] = _parse_vec("offset", offset, 3)
    except ValueError as e:
        _emit_error(json_out, ErrorCode.INVALID_PARAMS, str(e))
        raise typer.Exit(int(ExitCode.INPUT)) from None

    def human(data: dict[str, Any]) -> str:
        return f"duplicated '{data.get('source')}' -> {data.get('created')} (count={data.get('count')})"

    _rpc("duplicate", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def delete(
    targets: str = typer.Option(..., "--targets", help="対象オブジェクト（name|regex）"),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトを削除する（削除前サマリを backup として結果に残す）。"""
    params: dict[str, Any] = {"targets": targets}

    def human(data: dict[str, Any]) -> str:
        bk = data.get("backup") or {}
        return f"deleted '{data.get('deleted')}' (backup: type={bk.get('type')} loc={bk.get('location')})"

    _rpc("delete", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def material(
    action: str = typer.Option(..., "--action", help="操作: assign|create|list"),
    targets: str | None = typer.Option(None, "--targets", help="対象オブジェクト（name|regex）"),
    name: str | None = typer.Option(
        None, "--name", help="マテリアル名（assign=既存 / create=新規）"
    ),
    color: str | None = typer.Option(None, "--color", help="RGBA r,g,b,a（create の Base Color）"),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """マテリアルを割り当て/作成/一覧する（create は対象へ作成と同時に割り当て）。"""
    params: dict[str, Any] = {"action": action}
    if targets is not None:
        params["targets"] = targets
    if name is not None:
        params["name"] = name
    try:
        if color is not None:
            params["color"] = _parse_vec("color", color, 4)
    except ValueError as e:
        _emit_error(json_out, ErrorCode.INVALID_PARAMS, str(e))
        raise typer.Exit(int(ExitCode.INPUT)) from None

    def human(data: dict[str, Any]) -> str:
        if data.get("action") == "list":
            slots = ", ".join(
                f"{m['slot']}:{m['name']}={m['base_color']}" for m in data.get("materials", [])
            )
            return f"{data.get('name')} materials [{slots}]"
        return (
            f"{data.get('action')} '{data.get('material')}' -> "
            f"{data.get('name')} slot={data.get('slot')}"
        )

    _rpc("material", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command("request-status")
def request_status(
    request_id: str = typer.Option(..., "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """リクエストの決着状態を取得する（タイムアウト後の後追い回収）。"""

    def human(data: dict[str, Any]) -> str:
        return f"id={data.get('id')} state={data.get('state')} known={data.get('known')}"

    _rpc("request-status", {"id": request_id}, json_out=json_out, port=port, human=human)


def _command_meta(cmd: Command) -> dict[str, Any]:
    return {
        "name": cmd.name,
        "summary": cmd.summary,
        "mutates": cmd.mutates,
        "required_mode": cmd.required_mode.value,
        "stability": cmd.stability.value,
        "is_heavy": cmd.is_heavy,
        "capability_deps": list(cmd.capability_deps),
        "implemented": cmd.implemented,
    }


@app.command("list-commands")
def list_commands(
    show_all: bool = typer.Option(False, "--all", help="未実装コマンドも含める"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
) -> None:
    """利用可能なコマンド一覧を返す（SSOTから生成・ローカル完結）。

    既定では実行可能（implemented）なコマンドのみ。未実装の定義は --all で表示する。
    """
    cmds = load_definitions()
    chosen = [c for c in cmds.values() if show_all or c.implemented]
    items = [_command_meta(c) for c in sorted(chosen, key=lambda c: c.name)]
    if json_out:
        typer.echo(
            json.dumps({"schema_hash": schema_hash(cmds), "commands": items}, ensure_ascii=False)
        )
    else:
        for it in items:
            flag = "✎" if it["mutates"] else " "
            todo = "" if it["implemented"] else " (未実装)"
            typer.echo(f"  {flag} {it['name']:<16}{it['summary']}{todo}")


def _human_command(cmd: Command) -> str:
    lines = [
        f"{cmd.name} — {cmd.summary}",
        f"  mutates={cmd.mutates} mode={cmd.required_mode.value} stability={cmd.stability.value}",
    ]
    if cmd.params:
        lines.append("  params:")
        for prm in cmd.params:
            req = "必須" if prm.required else "任意"
            choices = f" choices={prm.choices}" if prm.choices else ""
            lines.append(f"    --{prm.name} ({prm.type.value}, {req}){choices}  {prm.help}")
    else:
        lines.append("  params: なし")
    return "\n".join(lines)


@app.command("help")
def help_(
    command: str | None = typer.Option(None, "--command", help="対象コマンド名"),
    show_all: bool = typer.Option(False, "--all", help="未実装コマンドも含める"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
) -> None:
    """コマンドの JSON Schema を返す（AIエージェントの発見用・SSOTから生成）。

    一覧は既定で実行可能なコマンドのみ。--command 指定時は未実装でも introspection 可。
    """
    cmds = load_definitions()
    sh = schema_hash(cmds)

    if command is not None:
        cmd = cmds.get(command)
        if cmd is None:
            _emit_error(json_out, ErrorCode.METHOD_NOT_FOUND, f"未知のコマンド: {command}")
            raise typer.Exit(int(ExitCode.INPUT))
        payload = {
            "schema_hash": sh,
            "command": _command_meta(cmd),
            "schema": to_json_schema(cmd),
        }
        _emit(json_out, _human_command(cmd), payload)
        return

    chosen = {name: c for name, c in cmds.items() if show_all or c.implemented}
    payload = {
        "schema_hash": sh,
        "commands": {name: to_json_schema(c) for name, c in sorted(chosen.items())},
    }
    human = "\n".join(
        [f"schema_hash: {sh[:12]}", "コマンド（詳細は --command NAME）:"]
        + [f"  {name}" for name in sorted(chosen)]
    )
    _emit(json_out, human, payload)


if __name__ == "__main__":
    app()
