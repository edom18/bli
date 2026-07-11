"""bli CLI エントリポイント（Typer）。

コマンド: init / doctor / ping / request-status / scene-info / object-info /
set-origin / list-commands / help。
終了コード（spec §8）: 0=成功 / 1=確定失敗 / 2=未決 / 3=接続不能・認証失敗 / 4=入力エラー。

構造（P2-2）: RPC 系コマンドの大半は SSOT（bli_core.definitions）から cli_factory が
動的生成する（human 表示は formatters.HUMAN_FORMATTERS・CLI 固有互換は cli_specs）。
このファイルに残るのは共通インフラ（_rpc/_await_job 等）と、生成に乗らない手書き
コマンド（ping/doctor/init/policy/job-wait/list-commands/help）のみ。
"""

from __future__ import annotations

import json
import math
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import typer

from bli_core import runtime
from bli_core.commands import Command, load_definitions
from bli_core.errors import ErrorCategory, ErrorCode, ExitCode
from bli_core.schema import schema_hash, to_json_schema

from . import client, config, models
from .cli_factory import FactoryContext, register_generated_commands
from .formatters import _watchdog_suffix  # noqa: F401  # 互換 re-export（既存テストが参照）


def _force_utf8_output() -> None:
    """標準出力/エラーを UTF-8 に固定する。

    Windows の既定は CP932 のため、日本語サマリや `ensure_ascii=False` の JSON が
    化ける/UnicodeEncodeError になる。呼び出し側に `PYTHONUTF8=1` を強制せずとも
    読めるよう、CLI 起動時に stream を UTF-8 へ張り替える。リダイレクトや pytest の
    capture など reconfigure を持たない/拒否する stream は黙ってスキップする。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


_force_utf8_output()

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
    """サーバ error を終了コードへ写像する（spec §8）。

    ErrorObject の `retryable` フラグを真実源にする（kind 文字列の列挙だとサーバに
    エラー種別を足すたび CLI との手動同期が要る。設計レビュー 2026-07-11 B1）。
    retryable=True（TIMEOUT/BUSY_RENDERING/SESSION_BUSY/IN_PROGRESS）→ exit 2（未決・再試行可）。
    AUTH_FAILED/PROTOCOL_VERSION_MISMATCH は「接続不能」の一種なので kind で exit 3 に写像する
    （ErrorObject に接続層の軸は無いためここだけ kind 判定が残る）。
    """
    kind = err.get("message", "")
    data = err.get("data")
    obj = data if isinstance(data, dict) else {}
    if kind in (ErrorCode.AUTH_FAILED, ErrorCode.PROTOCOL_VERSION_MISMATCH):
        return ExitCode.CONNECTION
    if obj.get("retryable") is True:
        return (
            ExitCode.TIMEOUT_PENDING
        )  # 未決: request-status 後追い / レンダ・別セッション後に再試行
    if kind == ErrorCode.INVALID_PARAMS or obj.get("category") == ErrorCategory.USER_INPUT:
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
        _emit_remote_error_exit(e.error, json_out=json_out, request_id=request_id)


def _emit_remote_error_exit(error: dict[str, Any], *, json_out: bool, request_id: str) -> NoReturn:
    """サーバ業務エラーを終了コードへ写像して exit する（直接呼び出しと job 完了の共通写像）。必ず送出。"""
    kind = error.get("message", "RPC_ERROR")
    raw = error.get("data")
    data = raw if isinstance(raw, dict) else {}
    symptom = data.get("userVisibleSymptom") or kind
    if kind == ErrorCode.TIMEOUT:
        symptom = f"{symptom}（後追い: bli request-status --id {request_id}）"
    _emit_error(json_out, kind, symptom, request_id=request_id)
    raise typer.Exit(int(_exit_code_for(error))) from None


def _await_job(
    job_id: str, *, json_out: bool, port: int | None, timeout: float | None = None
) -> dict[str, Any]:
    """heavy job（accepted 即返）の完了を request-status ポーリングで待ち、最終 domain result を返す（M10）。

    DONE → domain result（_ok エンベロープ）。FAILED → 業務エラーを直接呼び出しと同じ写像で exit。
    上限超過 → TIMEOUT_PENDING(exit2)（job_id で後追い可能）。request-status は LOCK_FREE で受信スレッド
    処理＝重量 job がメインスレッドを塞いでも応答する（spec §7・DoD: 接続が塞がらない）。
    """
    deadline = time.monotonic() + (timeout if timeout is not None else runtime.JOB_WAIT_TIMEOUT)
    connect_fails = 0
    warned_unresponsive = False
    while True:
        try:
            sr, _ = client.call(
                "request-status", {"id": job_id}, port=port, request_id=str(uuid.uuid4())
            )
        except client.ConnectError as e:
            # ポーリング中の一過性の接続失敗は数回まで許容（瞬断/サーバ再接続に強くする）。超過で CONNECTION。
            connect_fails += 1
            if connect_fails > runtime.JOB_POLL_MAX_CONNECT_FAILS:
                _emit_error(json_out, "CONNECTION", str(e), request_id=job_id)
                raise typer.Exit(int(ExitCode.CONNECTION)) from None
            if time.monotonic() >= deadline:
                _emit_error(json_out, "CONNECTION", str(e), request_id=job_id)
                raise typer.Exit(int(ExitCode.CONNECTION)) from None
            time.sleep(runtime.JOB_POLL_INTERVAL)
            continue
        except client.RpcRemoteError as e:
            _emit_remote_error_exit(e.error, json_out=json_out, request_id=job_id)
        connect_fails = 0
        data = sr.get("data", {}) if isinstance(sr, dict) else {}
        state = data.get("state")
        # auto-wait/job-wait のポーリング中にメインスレッドが固まったら **一度だけ** stderr へ通知する
        # （M10 T10.3）。request-status は lock-free＝重量 op がメインを塞いでもこの観測は届く。重量
        # ネイティブ処理は中断不能なのでジョブは継続中＝待機を続ける（kill しない）。stderr に出すので
        # JSON 出力（stdout）は汚さない。
        wd = data.get("watchdog")
        if not warned_unresponsive and isinstance(wd, dict) and wd.get("responsive") is False:
            warned_unresponsive = True
            age = wd.get("last_pump_age")
            age_s = f"{age:.0f}s" if isinstance(age, (int, float)) else "?"
            typer.echo(
                f"[bli] メインスレッドが応答していません（{age_s} 停止・重量処理で固まっている可能性）。"
                f"ジョブは継続中です（job_id={job_id}・request-status で観測可）。",
                err=True,
            )
        # 未知/TTL 失効した job_id は即座に失敗させる（30分ハングを防ぐ・全レビュー P1）。accepted を受けた
        # 直後の auto-wait では begin() で RUNNING 登録済みなので UNKNOWN にはならない＝この分岐は遅延
        # job-wait での typo/失効が主。
        if data.get("known") is False or state == "UNKNOWN":
            _emit_error(
                json_out,
                "UNKNOWN_JOB",
                f"job_id が見つかりません（未送信/typo、または結果が失効しました）: {job_id}",
                request_id=job_id,
            )
            raise typer.Exit(int(ExitCode.FAILURE)) from None
        if state in ("DONE", "FAILED"):
            try:
                return client.interpret_stored_response(data.get("result"))
            except client.RpcRemoteError as e:
                _emit_remote_error_exit(e.error, json_out=json_out, request_id=job_id)
        if time.monotonic() >= deadline:
            _emit_error(
                json_out,
                ErrorCode.TIMEOUT,
                f"ジョブが時間内に完了しませんでした（job_id={job_id}・bli job-wait/request-status で後追い）",
                request_id=job_id,
            )
            raise typer.Exit(int(ExitCode.TIMEOUT_PENDING)) from None
        time.sleep(runtime.JOB_POLL_INTERVAL)


def _present_result(
    result: dict[str, Any],
    *,
    method: str,
    request_id: str,
    json_out: bool,
    fetch: bool,
    human: Callable[[dict[str, Any]], str] | None,
) -> None:
    """domain result（_ok エンベロープ）を提示する（output_ref/--fetch/human を含む）。

    `_rpc`（同期 + auto-wait）と `job-wait`（遅延 job）で共有し、output_ref/--fetch の取り扱いが
    二重実装で drift しないようにする（設計レビュー P2）。human=None は汎用メッセージ（job-wait 用）。
    """
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
    elif human is not None:
        human_msg = human(result.get("data") or {})
    else:
        human_msg = f"done operation={result.get('operation', method)}"
    _emit(json_out, human_msg, payload)


def _rpc(
    method: str,
    params: dict[str, Any],
    *,
    json_out: bool,
    port: int | None,
    human: Callable[[dict[str, Any]], str],
    request_id: str | None = None,
    fetch: bool = False,
    async_: bool = False,
) -> None:
    """RPC を1往復し結果を出力する（接続/業務エラーは終了コードへ写像）。

    結果が output_ref(shared-fs) を含む場合、既定は **参照のみ** を返す（エージェント向け
    オンデマンド取得）。`fetch=True` のときだけ退避ファイルを読み sha256 検証して data へ
    展開する。整合不一致は STALE_OUTPUT（exit 1）。

    heavy コマンド（M10・spec §7）はサーバが `{accepted, job_id}` を即返す。既定は job-wait で
    最終結果まで自動待機して通常どおり提示する（エージェントには同期に見える）。`async_=True` のときは
    job_id を返して即終了（fire-and-forget）。
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

    # heavy job（accepted 即返）の処理（M10）。job_id=request_id（サーバは rid を job_id にする）。
    if isinstance(result, dict) and result.get("accepted") is True:
        job_id = str(result.get("job_id") or request_id)
        if async_:
            _emit(
                json_out,
                f"accepted job_id={job_id}（bli job-wait --id {job_id} で結果取得）",
                {
                    "ok": True,
                    "operation": result.get("operation", method),
                    "status": "accepted",
                    "job_id": job_id,
                    "request_id": request_id,
                },
            )
            return
        # 既定: 完了まで自動待機して domain result を取得（以降は通常の result 提示に合流）。
        result = _await_job(job_id, json_out=json_out, port=port)

    _present_result(
        result, method=method, request_id=request_id, json_out=json_out, fetch=fetch, human=human
    )


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


def _doctor_guidance(connection_exists: bool, reachable: bool) -> list[str]:
    """アドオン未到達時の導入ガイドを返す（到達時は空）。

    接続情報の有無で状況を切り分ける。情報なし＝未導入の可能性なので zip 導入手順を、
    情報あり＝待受停止の可能性なので Blender/アドオンの稼働確認を案内する。
    """
    if reachable:
        return []
    if not connection_exists:
        return [
            "アドオン未到達（接続情報なし＝未導入の可能性）:",
            "  1. 配布 zip をビルド: uv run python scripts/build_addon.py",
            "  2. Blender > Edit > Preferences > Add-ons > Install from Disk… で",
            "     dist/bli_server-<ver>.zip を選び、チェックを入れて有効化する。",
            "  3. 有効化で 127.0.0.1 待受 + 接続情報が書き出される。再度 `bli doctor`。",
        ]
    return [
        "アドオン未到達（接続情報あり＝待受が停止している可能性）:",
        "  - Blender が起動中か、アドオンが有効か確認する（終了/無効化で待受は止まる）。",
        "  - 接続情報は前回値。Blender 再起動か再有効化で更新される。",
    ]


@app.command()
def doctor(
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """環境診断（connection.json/token の有無・アドオン到達性・導入ガイド）。"""
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

    # メインスレッド応答性（M10 T10.3）。lock-free な request-status 経由で取得する＝メインが重量
    # 処理で固まっていても観測できる（ping は Dispatcher 経由で固まると応答しないため別経路にする）。
    watchdog: dict[str, Any] | None = None
    if reachable:
        try:
            sr, _ = client.call("request-status", {"id": str(uuid.uuid4())}, port=port, timeout=5.0)
            wd = (sr.get("data") or {}).get("watchdog")
            if isinstance(wd, dict):
                watchdog = wd
        except Exception:
            # 純粋にベストエフォートの観測（到達性判定は別経路の ping が担う）。フレーム破損等の
            # 想定外も含めて握り潰し watchdog=None（「不明」）へ縮退する＝doctor の終了挙動を変えない。
            pass

    main_responsive = watchdog.get("responsive") if watchdog else None
    guidance = _doctor_guidance(cp.exists(), reachable)
    payload = {
        "connection_json": cp.exists(),
        "connection_path": str(cp),
        "token_present": tp.exists(),
        "addon_reachable": reachable,
        "blender_version": blender_version,
        "main_thread_responsive": main_responsive,
        "watchdog": watchdog,
        "detail": detail,
        "guidance": guidance,
    }
    if main_responsive is None:
        wd_line = "不明" if reachable else "—（未到達）"
    elif main_responsive:
        wd_line = "応答中"
    else:
        wd_line = "応答なし（重量処理で固まっている可能性／実行は継続中）"
    human = "\n".join(
        [
            "bli doctor:",
            f"  connection.json : {'あり' if payload['connection_json'] else 'なし'} ({cp})",
            f"  token           : {'あり' if payload['token_present'] else 'なし'}",
            f"  アドオン到達     : {'OK (Blender ' + str(blender_version) + ')' if reachable else 'NG'}",
            f"  メインスレッド   : {wd_line}",
        ]
        + ([f"  詳細            : {detail}"] if detail else [])
        + (["", *guidance] if guidance else [])
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


# ---- policy（CLI ローカル・exec 権限ヘルパ / P1-1）----
# init/doctor と同じ「CLI ローカル完結」系＝RPC を送らない。exec mode の真実源はサーバが読む
# ユーザローカル policy.toml（bli_core.policy・R-A）で、このコマンドはその表示/編集を助けるだけ。
# 昇格は人間がこのコマンド（またはエディタ）で policy.toml を書いたときだけ成立する。
# ファイル形式の知識（許容スキーマ・レンダラ・原子的書込）は bli_core.policy に集約されており
# （レビュー R1-3）、この層は Typer 受理・対話確認・出力整形のみを担う。


def _policy_show(json_out: bool) -> None:
    from bli_core import policy as core_policy

    path = core_policy.policy_path()
    mode = core_policy.read_exec_mode()
    hashes = core_policy.read_allow_hashes()
    payload = {
        "ok": True,
        "action": "show",
        "path": str(path),
        "exists": path.exists(),
        "mode": mode,
        "allow_hashes_count": len(hashes),
    }
    human = "\n".join(
        [
            f"policy.toml: {path} ({'あり' if payload['exists'] else 'なし（既定 off）'})",
            f"  mode: {mode}",
            f"  allow_hashes: {len(hashes)} 件",
        ]
    )
    _emit(json_out, human, payload)


def _policy_set(mode: str, *, yes: bool, json_out: bool) -> None:
    from bli_core import policy as core_policy

    if mode not in core_policy.VALID_MODES:
        _emit_error(
            json_out,
            ErrorCode.INVALID_PARAMS,
            f"--mode は {'|'.join(core_policy.VALID_MODES)} のいずれかです: {mode}",
        )
        raise typer.Exit(int(ExitCode.INPUT))

    path = core_policy.policy_path()
    current = core_policy.read_exec_mode()

    try:
        allow_hashes = core_policy.load_preserved_allow_hashes()
    except core_policy.UnsafePolicyError as e:
        _emit_error(
            json_out,
            ErrorCode.INVALID_PARAMS,
            f"{e}（他の設定を失わないよう自動編集を停止しました。{path} を手動で編集してください）",
        )
        raise typer.Exit(int(ExitCode.INPUT)) from None

    if not yes:
        prompt = f"mode: {current} -> {mode} に書き込みますか？（policy.toml: {path}）"
        try:
            confirmed = typer.confirm(prompt, default=False)
        except typer.Abort:
            confirmed = False
        if not confirmed:
            # バイパス手段（--yes）はここでは案内しない: exec 昇格は人間の判断で行う建前を、
            # エラー文が自ら崩さないため（レビュー R1-1。--yes 自体は --help に記載がある）。
            _emit_error(
                json_out,
                "ABORTED",
                "確認されなかったため中断しました（人間による対話確認が必要です）",
            )
            raise typer.Exit(int(ExitCode.FAILURE))

    core_policy.write_policy(mode, allow_hashes)
    payload = {
        "ok": True,
        "action": "set",
        "path": str(path),
        "previous_mode": current,
        "mode": mode,
        "allow_hashes_count": len(allow_hashes),
    }
    human = f"mode: {current} -> {mode}（policy.toml: {path}）"
    _emit(json_out, human, payload)


@app.command()
def policy(
    action: str = typer.Option(..., "--action", help="show|set"),
    mode: str | None = typer.Option(
        None, "--mode", help="set 時の新しい exec mode: off|restricted|audited|trusted"
    ),
    yes: bool = typer.Option(False, "--yes", help="set の対話確認をスキップする"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
) -> None:
    """exec-python の実行ポリシー（policy.toml）を表示/編集する（CLIローカル・サーバへは何も送らない）。

    真実源はサーバ（Blender アドオン）が読むユーザローカル policy.toml。昇格はこのコマンド
    （またはエディタ）で policy.toml を書いたときだけ成立する（CLI フラグ単体では昇格できない
    という R-A の不変条件はこのコマンドでも変わらない＝RPC は一切送らない）。
    """
    if action == "show":
        _policy_show(json_out)
        return
    if action == "set":
        if mode is None:
            _emit_error(json_out, ErrorCode.INVALID_PARAMS, "--action set には --mode が必要です")
            raise typer.Exit(int(ExitCode.INPUT))
        _policy_set(mode, yes=yes, json_out=json_out)
        return
    _emit_error(json_out, ErrorCode.INVALID_PARAMS, f"--action は show|set です: {action}")
    raise typer.Exit(int(ExitCode.INPUT))


# ---- SSOT 生成コマンド（P2-2）----
# scene-info〜mesh・request-status/job-status は definitions.py から動的生成する。
# 登録位置は手書き時代の `bli --help` の一覧順を保つ（policy の後・job-wait の前）。
register_generated_commands(
    app, FactoryContext(rpc=_rpc, emit_error=_emit_error, parse_vec=_parse_vec)
)


@app.command("job-wait")
def job_wait(
    request_id: str = typer.Option(..., "--id", help="ジョブID(=request_id)"),
    timeout: float | None = typer.Option(
        None, "--timeout", help="待機上限秒（既定 JOB_WAIT_TIMEOUT）"
    ),
    fetch: bool = typer.Option(
        False, "--fetch", help="退避(output_ref)を読み込み sha256 検証して展開する"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """非同期 job の完了を待って最終結果を取得する（request-status をポーリング）。"""
    result = _await_job(request_id, json_out=json_out, port=port, timeout=timeout)
    # _rpc と同じ提示経路（output_ref/--fetch も対応）を共有する。human=None で汎用メッセージ。
    _present_result(
        result, method="job-wait", request_id=request_id, json_out=json_out, fetch=fetch, human=None
    )


def _command_meta(cmd: Command) -> dict[str, Any]:
    return {
        "name": cmd.name,
        "summary": cmd.summary,
        "mutates": cmd.mutates,
        "required_mode": cmd.required_mode.value,
        "stability": cmd.stability.value,
        "is_heavy": cmd.is_heavy,
        # op 依存で heavy になる op 群（mesh の boolean/decimate）。エージェントが「どの呼び出しが
        # accepted/job 化されるか」を発見できるよう list-commands に載せる（M10・仕様レビュー P2）。
        "heavy_ops": list(cmd.heavy_ops),
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
