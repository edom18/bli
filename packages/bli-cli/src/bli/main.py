"""bli CLI エントリポイント（Typer）。

コマンド: init / doctor / ping / request-status / scene-info / object-info /
set-origin / list-commands / help。
終了コード（spec §8）: 0=成功 / 1=確定失敗 / 2=未決 / 3=接続不能・認証失敗 / 4=入力エラー。
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


def _watchdog_suffix(data: dict[str, Any]) -> str:
    """request-status/job-status の human 出力に付けるメインスレッド応答性の注記（M10 T10.3）。

    応答中（または watchdog 情報なし）は空文字。固まっている場合のみ注記を返す（実行は継続中＝
    重量ネイティブ処理が固めている可能性をエージェントに可視化する）。
    """
    wd = data.get("watchdog")
    if not isinstance(wd, dict) or wd.get("responsive", True):
        return ""
    age = wd.get("last_pump_age")
    age_s = f"{age:.0f}s" if isinstance(age, (int, float)) else "?"
    return f"  ⚠ メインスレッド応答なし（{age_s} 停止・重量処理で固まっている可能性／実行は継続中）"


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
    # targets 系の `--regex`（値なしフラグ）と同名だと取り違えを誘発するため --name-regex を
    # 一次名に改名（R1-4）。旧 `--regex <pat>` は移行用の別名として受理を継続する。
    name_regex: str | None = typer.Option(
        None,
        "--name-regex",
        "--regex",
        help="名前の正規表現フィルタ（部分一致・旧名 --regex も受理）",
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """シーン内オブジェクトを type/名前正規表現 でフィルタして一覧する。"""
    params: dict[str, Any] = {}
    if type_filter is not None:
        params["type"] = type_filter
    if name_regex is not None:
        # `--regex --json` のような値の渡し忘れは click が次のオプションを値として食い、
        # 「0 件の空リスト」という silent 失敗になる（targets 系の値なし `--regex` フラグとの
        # 取り違えで起きやすい）。bli のオプションは全て `--` 始まりなので、`--` 始まりの
        # パターン値は誤用として loud に弾く（本当に `--` で始まる名前は `\-\-` でエスケープ可）。
        if name_regex.startswith("--"):
            _emit_error(
                json_out,
                ErrorCode.INVALID_PARAMS,
                f"--name-regex の値がオプションに見えます: {name_regex!r}"
                "（値の渡し忘れの可能性。パターンが本当に -- で始まる場合は \\-\\- とエスケープ）",
            )
            raise typer.Exit(int(ExitCode.INPUT))
        params["name_regex"] = name_regex

    def human(data: dict[str, Any]) -> str:
        objs = data.get("objects", [])
        names = ", ".join(f"{o['name']}({o['type']})" for o in objs)
        return f"{data.get('count', len(objs))} objects [{names}]"

    _rpc("list-objects", params, json_out=json_out, port=port, human=human)


@app.command("object-info")
def object_info(
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトの寸法/頂点数/transform/材質/modifier を取得する。"""
    params: dict[str, Any] = {"targets": targets}
    if regex:
        params["regex"] = True

    def human(data: dict[str, Any]) -> str:
        return (
            f"{data.get('name')} ({data.get('type')}): "
            f"loc={data.get('location')} dims={data.get('dimensions')}"
        )

    _rpc("object-info", params, json_out=json_out, port=port, human=human)


@app.command("set-origin")
def set_origin(
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
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
    if regex:
        params["regex"] = True
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
def straighten(
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    method: str = typer.Option(
        ..., "--method", help="reset|world-align|pca|floor|angle|align-vector|reference"
    ),
    up_axis: str = typer.Option("+Z", "--up-axis", help="up 方向: +Z|-Z|+Y|-Y|+X|-X（既定 +Z）"),
    axis: str | None = typer.Option(
        None,
        "--axis",
        help="world-align/reference=合わせる local 軸 / angle=回転する world 軸: X|Y|Z",
    ),
    up_hint: str | None = typer.Option(
        None, "--up-hint", help="pca の符号: auto|current（current=現在 up 寄り・反転防止）"
    ),
    degrees: float | None = typer.Option(None, "--degrees", help="angle: 回転量（度・符号で向き）"),
    from_dir: str | None = typer.Option(
        None, "--from-dir", help="align-vector: 揃えたい現在の world 方向 x,y,z"
    ),
    to_dir: str | None = typer.Option(
        None, "--to-dir", help="align-vector: 目標 world 方向 x,y,z（省略時は up）"
    ),
    reference: str | None = typer.Option(
        None, "--reference", help="reference: 基準にする別オブジェクト名"
    ),
    ref_axis: str | None = typer.Option(
        None, "--ref-axis", help="reference: 参照側の signed local 軸 +X..-Z（省略時 up-axis）"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="適用せず計画（回転/傾き角）のみ返す"),
    bake_rotation: bool = typer.Option(
        False, "--bake-rotation", help="回転を mesh データへ焼き込む"
    ),
    make_single_user: bool = typer.Option(
        False, "--make-single-user", help="bake時に共有mesh単一ユーザ化を許可"
    ),
    request_id: str | None = typer.Option(
        None, "--id", help="リクエストID(UUIDv4)。冪等リトライで同一IDを再利用する"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトを直立補正する（reset/world-align/pca/floor/angle/align-vector/reference）。"""
    params: dict[str, Any] = {"targets": targets, "method": method, "up_axis": up_axis}
    if regex:
        params["regex"] = True
    if axis is not None:
        params["axis"] = axis
    if up_hint is not None:
        params["up_hint"] = up_hint
    if degrees is not None:
        params["degrees"] = degrees
    try:
        if from_dir is not None:
            params["from_dir"] = _parse_vec("from-dir", from_dir, 3)
        if to_dir is not None:
            params["to_dir"] = _parse_vec("to-dir", to_dir, 3)
    except ValueError as e:
        _emit_error(json_out, ErrorCode.INVALID_PARAMS, str(e))
        raise typer.Exit(int(ExitCode.INPUT)) from None
    if reference is not None:
        params["reference"] = reference
    if ref_axis is not None:
        params["ref_axis"] = ref_axis
    if dry_run:
        params["dry_run"] = True
    if bake_rotation:
        params["bake_rotation"] = True
    if make_single_user:
        params["make_single_user"] = True

    def human(data: dict[str, Any]) -> str:
        m = data.get("method")
        prefix = "[dry-run] " if data.get("dry_run") else ""
        head = f"{prefix}straighten {data.get('name')} [{m}] up={data.get('up_axis')}"
        if m == "floor":
            return f"{head}: grounded min_up={data.get('min_up')} offset={data.get('floor_offset')}"
        if m in ("world-align", "reference"):
            ref = f" ref={data.get('reference')}:{data.get('ref_axis')}" if m == "reference" else ""
            return (
                f"{head}: axis={data.get('axis')}{ref} -> {data.get('aligned_world')} "
                f"rot={data.get('rotation_euler_deg')}"
            )
        if m == "pca":
            return (
                f"{head}: tilt={data.get('tilt_from_up_deg')}deg "
                f"principal -> {data.get('principal_world_after')} "
                f"rot={data.get('rotation_euler_deg')}"
            )
        if m == "angle":
            return (
                f"{head}: axis={data.get('axis')} degrees={data.get('degrees')} "
                f"rot={data.get('rotation_euler_deg')} baked={data.get('baked')}"
            )
        if m == "align-vector":
            return (
                f"{head}: {data.get('from_dir')} -> {data.get('from_world_after')} "
                f"angle={data.get('angle_deg')}deg rot={data.get('rotation_euler_deg')} "
                f"baked={data.get('baked')}"
            )
        return f"{head}: rot={data.get('rotation_euler_deg')} baked={data.get('baked')}"

    _rpc("straighten", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def capture(
    source: str = typer.Option(
        "viewport", "--source", help="取得元: viewport|screen|render（既定 viewport）"
    ),
    width: int | None = typer.Option(
        None, "--width", help="出力幅px（viewport/render・省略時既定）"
    ),
    height: int | None = typer.Option(
        None, "--height", help="出力高px（viewport/render・省略時既定）"
    ),
    camera: str | None = typer.Option(
        None, "--camera", help="render で使うカメラ名（省略時 active・render 専用）"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """現在の状態を画像で取得する（viewport/screen/render・PNG をファイル出力しパスを返す）。"""
    params: dict[str, Any] = {"source": source}
    if width is not None:
        params["width"] = width
    if height is not None:
        params["height"] = height
    if camera is not None:
        params["camera"] = camera

    def human(data: dict[str, Any]) -> str:
        cam = f" camera={data['camera']}" if data.get("camera") else ""
        return (
            f"capture [{data.get('source')}]{cam} {data.get('width')}x{data.get('height')} "
            f"-> {data.get('path')} ({data.get('size')}B)"
        )

    _rpc("capture", params, json_out=json_out, port=port, human=human)


@app.command()
def undo(
    steps: int = typer.Option(1, "--steps", help="戻す段数（1〜100・既定 1）"),
    request_id: str | None = typer.Option(
        None, "--id", help="リクエストID(UUIDv4)。冪等リトライで同一IDを再利用する"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """直前の操作を元に戻す（グローバル undo スタックを steps 段戻す・GUI 必須）。"""
    from bli_core import runtime

    if (
        not 1 <= steps <= runtime.MAX_UNDO_STEPS
    ):  # 暴走防止の上限は送信前に弾く（§6e・duplicate と同流儀）
        _emit_error(
            json_out,
            ErrorCode.INVALID_PARAMS,
            f"--steps は 1〜{runtime.MAX_UNDO_STEPS} です: {steps}",
        )
        raise typer.Exit(int(ExitCode.INPUT))
    params: dict[str, Any] = {"steps": steps}

    def human(data: dict[str, Any]) -> str:
        return f"undo: requested={data.get('requested')} applied={data.get('applied')}"

    _rpc("undo", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def redo(
    steps: int = typer.Option(1, "--steps", help="進める段数（1〜100・既定 1）"),
    request_id: str | None = typer.Option(
        None, "--id", help="リクエストID(UUIDv4)。冪等リトライで同一IDを再利用する"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """元に戻した操作をやり直す（グローバル undo スタックを steps 段進める・GUI 必須）。"""
    from bli_core import runtime

    if (
        not 1 <= steps <= runtime.MAX_UNDO_STEPS
    ):  # 暴走防止の上限は送信前に弾く（§6e・duplicate と同流儀）
        _emit_error(
            json_out,
            ErrorCode.INVALID_PARAMS,
            f"--steps は 1〜{runtime.MAX_UNDO_STEPS} です: {steps}",
        )
        raise typer.Exit(int(ExitCode.INPUT))
    params: dict[str, Any] = {"steps": steps}

    def human(data: dict[str, Any]) -> str:
        return f"redo: requested={data.get('requested')} applied={data.get('applied')}"

    _rpc("redo", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command("print-setup")
def print_setup(
    unit: str = typer.Option("mm", "--unit", help="表示単位: mm|m（既定 mm）"),
    scene: str | None = typer.Option(None, "--scene", help="対象シーン名（省略時は active）"),
    request_id: str | None = typer.Option(
        None, "--id", help="リクエストID(UUIDv4)。冪等リトライで同一IDを再利用する"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """3Dプリント向けにシーンの表示単位を設定する（mm/m・geometry 非破壊）。"""
    params: dict[str, Any] = {"unit": unit}
    if scene is not None:
        params["scene"] = scene

    def human(data: dict[str, Any]) -> str:
        us = data.get("unit_settings") or {}
        return (
            f"scene '{data.get('scene')}' unit={data.get('unit')} "
            f"(system={us.get('system')} length_unit={us.get('length_unit')} "
            f"changed={data.get('changed')})"
        )

    _rpc("print-setup", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command("print-check")
def print_check(
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    manifold: bool = typer.Option(False, "--manifold", help="非多様体チェック"),
    normals: bool = typer.Option(False, "--normals", help="反転法線チェック"),
    degenerate: bool = typer.Option(False, "--degenerate", help="退化面チェック"),
    thin: bool = typer.Option(False, "--thin", help="薄壁チェック（print3d 依存）"),
    min_thickness: float | None = typer.Option(None, "--min-thickness", help="thin の最小厚み"),
    intersect: bool = typer.Option(False, "--intersect", help="自己交差チェック（print3d 依存）"),
    fetch: bool = typer.Option(
        False, "--fetch", help="退避(output_ref)を読み込み sha256 検証して展開する"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    async_out: bool = typer.Option(
        False, "--async", help="job_id を即返し（既定は完了まで自動待機）"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """3Dプリント健全性をチェックする（manifold/normals/degenerate・件数を返す）。"""
    params: dict[str, Any] = {"targets": targets}
    if regex:
        params["regex"] = True
    # カテゴリ flag は presence-sensitive（省略時はサーバが bmesh 3種すべて）。
    if manifold:
        params["manifold"] = True
    if normals:
        params["normals"] = True
    if degenerate:
        params["degenerate"] = True
    if thin:
        params["thin"] = True
    if intersect:
        params["intersect"] = True
    if min_thickness is not None:
        params["min_thickness"] = min_thickness

    def human(data: dict[str, Any]) -> str:
        c = data.get("checks") or {}
        # 報告されたカテゴリのキーのみ並べる（未要求カテゴリで None を出さない）。
        detail = " ".join(f"{k}={v}" for k, v in c.items() if k != "is_printable")
        return f"{data.get('name')} printable={c.get('is_printable')} {detail}".rstrip()

    _rpc(
        "print-check",
        params,
        json_out=json_out,
        port=port,
        human=human,
        request_id=request_id,
        fetch=fetch,
        async_=async_out,
    )


@app.command("print-repair")
def print_repair(
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    make_manifold: bool = typer.Option(
        False, "--make-manifold", help="穴埋め/重複マージ/loose 除去で manifold 化"
    ),
    recalc_normals: bool = typer.Option(False, "--recalc-normals", help="面法線を一貫化"),
    remove_degenerate: bool = typer.Option(False, "--remove-degenerate", help="退化面/辺を除去"),
    make_single_user: bool = typer.Option(
        False, "--make-single-user", help="共有mesh時に単一ユーザ化を許可"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    async_out: bool = typer.Option(
        False, "--async", help="job_id を即返し（既定は完了まで自動待機）"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """3Dプリント向けに mesh を best-effort 修復する（全省略で全修復・完全修復は非保証）。"""
    params: dict[str, Any] = {"targets": targets}
    if regex:
        params["regex"] = True
    # presence-sensitive: 全省略時はサーバが全修復を実行。
    if make_manifold:
        params["make_manifold"] = True
    if recalc_normals:
        params["recalc_normals"] = True
    if remove_degenerate:
        params["remove_degenerate"] = True
    if make_single_user:
        params["make_single_user"] = True

    def human(data: dict[str, Any]) -> str:
        fixed = data.get("fixed") or {}
        after = data.get("after") or {}
        return (
            f"repaired {data.get('name')} applied={data.get('applied')} "
            f"fixed_non_manifold={fixed.get('non_manifold_edges')} "
            f"printable={after.get('is_printable')}"
        )

    _rpc(
        "print-repair",
        params,
        json_out=json_out,
        port=port,
        human=human,
        request_id=request_id,
        async_=async_out,
    )


@app.command("print-export")
def print_export(
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    fmt: str = typer.Option(
        "stl", "--format", help="出力形式: stl|3mf（3mf 未導入時は STL を hint）"
    ),
    path: str = typer.Option(..., "--path", help="出力ファイルパス"),
    ascii_format: bool = typer.Option(False, "--ascii", help="STL を ASCII で出力（既定 binary）"),
    scale: float = typer.Option(1.0, "--scale", help="出力スケール（global_scale・既定 1.0）"),
    apply_modifiers: bool = typer.Option(
        True,
        "--apply-modifiers/--no-apply-modifiers",
        help="モディファイア適用後の最終形を出力（既定 on）",
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """3Dプリント向けに mesh を STL で書き出す（3MF は未導入のため STL を hint）。"""
    params: dict[str, Any] = {
        "targets": targets,
        "format": fmt,
        "path": path,
        "ascii": ascii_format,
        "scale": scale,
        "apply_modifiers": apply_modifiers,
    }
    if regex:
        params["regex"] = True

    def human(data: dict[str, Any]) -> str:
        return (
            f"exported {data.get('name')} [{data.get('format')}] -> {data.get('path')} "
            f"({data.get('size')}B, {data.get('triangles')} tris, scale={data.get('global_scale')})"
        )

    _rpc("print-export", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def export(
    fmt: str = typer.Option(..., "--format", help="出力形式: obj|fbx|gltf|stl|3mf"),
    path: str = typer.Option(..., "--path", help="出力ファイルパス（gltf は .glb 必須＝GLB 単一）"),
    targets: str | None = typer.Option(
        None,
        "--targets",
        "--target",
        help="対象（完全一致・--regex で正規表現・指定時はこれを書き出す）",
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    use_selection: bool = typer.Option(
        False,
        "--use-selection",
        help="現在の選択集合のみ書き出す（targets 省略時・省略でシーン全体）",
    ),
    axis_forward: str | None = typer.Option(
        None,
        "--axis-forward",
        help="fbx専用: forward軸 X|Y|Z|-X|-Y|-Z（既定 -Z・Unity 取込はこの既定のまま合う）。"
        "負の軸は --axis-forward=-Z のように '=' で連結すること"
        "（'--axis-forward -Z' は -Z が別オプションと誤解釈され得る）",
    ),
    axis_up: str | None = typer.Option(
        None,
        "--axis-up",
        help="fbx専用: up軸 X|Y|Z|-X|-Y|-Z（既定 Y・Unity 取込はこの既定のまま合う）。"
        "負の軸は --axis-up=-Z のように '=' で連結すること",
    ),
    scale: float | None = typer.Option(
        None, "--scale", help="fbx専用: global_scale（既定は Blender 既定 1.0・正の値のみ）"
    ),
    apply_unit_scale: bool | None = typer.Option(
        None,
        "--apply-unit-scale/--no-apply-unit-scale",
        help="fbx専用: シーン単位を1.0とみなして書き出す（既定は Blender 既定 on・省略時は指定しない）",
    ),
    embed_textures: bool = typer.Option(
        False,
        "--embed-textures",
        help="fbx専用: テクスチャを FBX に同梱する（path_mode=COPY をサーバ側で自動設定）",
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    async_out: bool = typer.Option(
        False, "--async", help="job_id を即返し（既定は完了まで自動待機）"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """シーン/選択を多形式で書き出す（obj/fbx/gltf/stl・3mf は未導入で CAPABILITY）。

    axis-forward/axis-up/scale/apply-unit-scale/embed-textures は **fbx 専用**（他 format に
    指定すると INVALID_PARAMS）。Unity 向けレシピは SKILL.md の「Unity 取り込みレシピ」参照。
    """
    params: dict[str, Any] = {"format": fmt, "path": path, "use_selection": use_selection}
    if targets is not None:
        params["targets"] = targets
    if regex:
        params["regex"] = True
    # fbx 専用オプションは presence-sensitive（省略時はサーバへ送らず Blender 既定に委ねる）。
    if axis_forward is not None:
        params["axis_forward"] = axis_forward
    if axis_up is not None:
        params["axis_up"] = axis_up
    if scale is not None:
        params["scale"] = scale
    if apply_unit_scale is not None:
        params["apply_unit_scale"] = apply_unit_scale
    if embed_textures:
        params["embed_textures"] = True

    def human(data: dict[str, Any]) -> str:
        scope = (
            f"objects={data.get('exported_objects')}"
            if data.get("exported_objects") is not None
            else "whole scene"
        )
        fbx_opts = data.get("fbx_options")
        opts = f" fbx_options={fbx_opts}" if fbx_opts else ""
        return (
            f"exported [{data.get('format')}] {scope} -> {data.get('path')} "
            f"({data.get('size')}B, sha={str(data.get('sha256'))[:12]}){opts}"
        )

    _rpc(
        "export",
        params,
        json_out=json_out,
        port=port,
        human=human,
        request_id=request_id,
        async_=async_out,
    )


@app.command("import")
def import_(
    fmt: str = typer.Option(..., "--format", help="入力形式: obj|fbx|gltf|stl|3mf"),
    path: str = typer.Option(..., "--path", help="入力ファイルパス"),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    async_out: bool = typer.Option(
        False, "--async", help="job_id を即返し（既定は完了まで自動待機）"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """多形式ファイルをシーンに取り込む（obj/fbx/gltf/stl・3mf は未導入で CAPABILITY）。"""
    params: dict[str, Any] = {"format": fmt, "path": path}

    def human(data: dict[str, Any]) -> str:
        names = [o.get("name") for o in (data.get("imported") or [])]
        return f"imported [{data.get('format')}] {data.get('count')}: {names}"

    _rpc(
        "import",
        params,
        json_out=json_out,
        port=port,
        human=human,
        request_id=request_id,
        async_=async_out,
    )


@app.command()
def save(
    path: str | None = typer.Option(
        None, "--path", help="保存先 .blend（省略時は現在のファイル・未保存なら要指定）"
    ),
    backup: bool = typer.Option(
        True, "--backup/--no-backup", help="上書き時に .blend1 backup を残す（既定 on）"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """.blend ファイルに保存する（上書きは既定でバックアップ .blend1 を残す）。"""
    params: dict[str, Any] = {"backup": backup}
    if path is not None:
        params["path"] = path

    def human(data: dict[str, Any]) -> str:
        bk = f" backup={data.get('backup_path')}" if data.get("backed_up") else ""
        return f"saved -> {data.get('path')} ({data.get('size')}B){bk}"

    _rpc("save", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command("open")
def open_(
    path: str = typer.Option(..., "--path", help="開く .blend ファイル"),
    force: bool = typer.Option(False, "--force", help="未保存変更を破棄して開く（既定 off）"),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """.blend ファイルを開く（シーン全体を置換・未保存変更があれば --force 必須）。"""
    params: dict[str, Any] = {"path": path, "force": force}

    def human(data: dict[str, Any]) -> str:
        disc = " (discarded unsaved)" if data.get("discarded_unsaved") else ""
        return (
            f"opened -> {data.get('path')} scene={data.get('scene')} "
            f"objects={data.get('object_count')}{disc}"
        )

    _rpc("open", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command("exec-python")
def exec_python(
    code: str | None = typer.Option(None, "--code", help="実行する Python コード（--file と排他）"),
    file: str | None = typer.Option(
        None, "--file", help="実行するスクリプトファイル（--code と排他）"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """構造化サブコマンドで表現できない操作の逃げ道（既定 off・restricted で自走可・サンドボックスなし）。

    既定では無効。サーバ側のユーザローカル policy.toml で [exec] mode を restricted（推奨・AST
    ブロックリスト検査つきで自走可）/ audited / trusted にしたときだけ実行できる（`bli policy
    --action set --mode restricted` で有効化）。CLI からは mode を送れない＝CLI フラグ単体では
    昇格できない（spec §276・§459）。実行コードは同一 OS 権限で走る＝結果の security_guarantee は
    常に false（過信しないこと）。
    """
    # --code / --file は排他（どちらか一方が必須）。送信前に弾く（exit 4）。
    if (code is None) == (file is None):
        _emit_error(
            json_out,
            ErrorCode.INVALID_PARAMS,
            "--code か --file のどちらか一方を指定してください（両方/どちらも無しは不可）",
        )
        raise typer.Exit(int(ExitCode.INPUT))

    # --file は **CLI 側で読む**（CLI の CWD 基準＝予測可能。Blender プロセスの CWD と区別）。
    # サーバには code として送る（サーバ側 file 読取は直接 RPC 用のフォールバック）。
    if file is not None:
        try:
            source = Path(file).read_text(encoding="utf-8")
        except OSError as e:
            _emit_error(json_out, ErrorCode.INVALID_PARAMS, f"スクリプトファイルを読めません: {e}")
            raise typer.Exit(int(ExitCode.INPUT)) from None
    else:
        source = str(code)

    params: dict[str, Any] = {"code": source}

    def human(data: dict[str, Any]) -> str:
        parts = ["exec ok（security_guarantee=false・サンドボックスなし）"]
        out = (data.get("stdout") or "").rstrip()
        if out:
            parts.append(out)
        err = (data.get("stderr") or "").rstrip()
        if err:
            parts.append(f"[stderr] {err}")
        if data.get("result_repr") is not None:
            parts.append(f"=> {data.get('result_repr')}")
        flags = data.get("heuristic_flags") or []
        if flags:
            parts.append(f"[heuristic_flags] {', '.join(flags)}（注意喚起・ブロックはしない）")
        if data.get("audit_ok") is False:
            parts.append("[warn] 監査ログの書き込みに失敗しました（証跡が残っていません）")
        return "\n".join(parts)

    _rpc("exec-python", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def select(
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    type_filter: str | None = typer.Option(None, "--type", help="型フィルタ（MESH/CURVE/...）"),
    active: str | None = typer.Option(None, "--active", help="active にする対象名"),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトを選択し active を設定する。"""
    params: dict[str, Any] = {"targets": targets}
    if regex:
        params["regex"] = True
    if type_filter is not None:
        params["type"] = type_filter
    if active is not None:
        params["active"] = active

    def human(data: dict[str, Any]) -> str:
        return f"selected {data.get('count')}: {data.get('selected')} active={data.get('active')}"

    _rpc("select", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def transform(
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
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
    if regex:
        params["regex"] = True
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
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
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
    if regex:
        params["regex"] = True
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
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
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
    if regex:
        params["regex"] = True
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
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトを削除する（削除前サマリを backup として結果に残す）。"""
    params: dict[str, Any] = {"targets": targets}
    if regex:
        params["regex"] = True

    def human(data: dict[str, Any]) -> str:
        bk = data.get("backup") or {}
        return f"deleted '{data.get('deleted')}' (backup: type={bk.get('type')} loc={bk.get('location')})"

    _rpc("delete", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def material(
    action: str = typer.Option(..., "--action", help="操作: assign|create|list"),
    targets: str | None = typer.Option(
        None, "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    name: str | None = typer.Option(
        None, "--name", help="マテリアル名（assign=既存 / create=新規）"
    ),
    color: str | None = typer.Option(None, "--color", help="RGBA r,g,b,a（create の Base Color）"),
    make_single_user: bool = typer.Option(
        False, "--make-single-user", help="共有mesh時に単一ユーザ化を許可"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """マテリアルを割り当て/作成/一覧する（create は対象へ作成と同時に割り当て）。"""
    params: dict[str, Any] = {"action": action}
    if targets is not None:
        params["targets"] = targets
    if regex:
        params["regex"] = True
    if name is not None:
        params["name"] = name
    if make_single_user:
        params["make_single_user"] = True
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


@app.command()
def modifier(
    action: str = typer.Option(..., "--action", help="add|remove|list|apply"),
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    type_: str | None = typer.Option(
        None, "--type", help="add の種類: MIRROR|SUBSURF|SOLIDIFY|DECIMATE|BOOLEAN"
    ),
    name: str | None = typer.Option(None, "--name", help="モディファイア名（remove/apply 対象）"),
    axis: str | None = typer.Option(None, "--axis", help="MIRROR の軸: X|Y|Z"),
    levels: int | None = typer.Option(None, "--levels", help="SUBSURF の分割数"),
    thickness: float | None = typer.Option(None, "--thickness", help="SOLIDIFY の厚み"),
    ratio: float | None = typer.Option(None, "--ratio", help="DECIMATE の比率（0..1）"),
    operation: str | None = typer.Option(
        None, "--operation", help="BOOLEAN の演算: UNION|DIFFERENCE|INTERSECT"
    ),
    with_object: str | None = typer.Option(None, "--with", help="BOOLEAN の相手オブジェクト名"),
    make_single_user: bool = typer.Option(
        False, "--make-single-user", help="apply 時に共有mesh単一ユーザ化を許可"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """モディファイアを追加/削除/一覧/適用する（add は --type 必須・apply は mesh へ焼き込み）。"""
    params: dict[str, Any] = {"action": action, "targets": targets}
    if regex:
        params["regex"] = True
    if type_ is not None:
        params["type"] = type_
    if name is not None:
        params["name"] = name
    if axis is not None:
        params["axis"] = axis
    if levels is not None:
        params["levels"] = levels
    if thickness is not None:
        params["thickness"] = thickness
    if ratio is not None:
        params["ratio"] = ratio
    if operation is not None:
        params["operation"] = operation
    if with_object is not None:
        params["with_object"] = with_object
    if make_single_user:
        params["make_single_user"] = True

    def human(data: dict[str, Any]) -> str:
        if data.get("action") == "list":
            mods = ", ".join(f"{m['name']}({m['type']})" for m in data.get("modifiers", []))
            return f"{data.get('name')} modifiers [{mods}]"
        if data.get("action") == "add":
            m = data.get("modifier") or {}
            return f"added {m.get('type')} '{m.get('name')}' to {data.get('name')}"
        if data.get("action") == "apply":
            return f"applied '{data.get('applied')}' to {data.get('name')}"
        return f"removed '{data.get('removed')}' from {data.get('name')}"

    _rpc("modifier", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def add(
    type_: str = typer.Option(
        ...,
        "--type",
        help="生成する種類: cube|uv-sphere|ico-sphere|cylinder|cone|plane|torus|empty|light|camera|text",
    ),
    name: str | None = typer.Option(None, "--name", help="生成後の名前"),
    location: str | None = typer.Option(None, "--location", help="生成位置 x,y,z"),
    rotation: str | None = typer.Option(None, "--rotation", help="生成後の回転 x,y,z（度）"),
    scale: str | None = typer.Option(None, "--scale", help="生成後の拡縮 x,y,z"),
    light_type: str | None = typer.Option(
        None, "--light-type", help="type=light 専用: POINT|SUN|SPOT|AREA（既定 POINT）"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトを生成する（mesh primitive / empty / light / camera / text）。"""
    params: dict[str, Any] = {"type": type_}
    if name is not None:
        params["name"] = name
    if light_type is not None:
        params["light_type"] = light_type
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
        return f"added {data.get('type')}: {data.get('name')} loc={data.get('location')}"

    _rpc("add", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def mode(
    to: str = typer.Option(
        ..., "--to", help="切替先: object|edit|sculpt|vertex-paint|weight-paint"
    ),
    targets: str | None = typer.Option(
        None,
        "--targets",
        "--target",
        help="対象名（省略時は現在の active・完全一致・--regex で正規表現）",
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """編集モードを切り替える（object/edit/sculpt/vertex-paint/weight-paint）。"""
    params: dict[str, Any] = {"to": to}
    if targets is not None:
        params["targets"] = targets
    if regex:
        params["regex"] = True

    def human(data: dict[str, Any]) -> str:
        return (
            f"mode {data.get('from_mode')} -> {data.get('to_mode')} (active={data.get('active')})"
        )

    _rpc("mode", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def rename(
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    name: str = typer.Option(..., "--name", help="新しい名前"),
    with_data: bool = typer.Option(False, "--with-data", help="obj.data も同名に変更する"),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """オブジェクトを改名する（--with-data で obj.data も同名に変更）。"""
    params: dict[str, Any] = {"targets": targets, "name": name}
    if regex:
        params["regex"] = True
    if with_data:
        params["with_data"] = True

    def human(data: dict[str, Any]) -> str:
        return (
            f"renamed '{data.get('old_name')}' -> '{data.get('new_name')}' "
            f"(data_renamed={data.get('data_renamed')})"
        )

    _rpc("rename", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def parent(
    targets: str = typer.Option(
        ...,
        "--targets",
        "--target",
        help="対象オブジェクト（複数可・完全一致・--regex で正規表現）",
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    to: str | None = typer.Option(None, "--to", help="親にするオブジェクト名（--clear と排他）"),
    clear: bool = typer.Option(False, "--clear", help="親子関係を解除する（--to と排他）"),
    keep_transform: bool = typer.Option(
        True,
        "--keep-transform/--no-keep-transform",
        help="見た目のワールド transform を保つ（既定 on）",
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """親子関係を設定/解除する（--to と --clear は排他）。"""
    params: dict[str, Any] = {"targets": targets, "keep_transform": keep_transform}
    if regex:
        params["regex"] = True
    if to is not None:
        params["to"] = to
    if clear:
        params["clear"] = True

    def human(data: dict[str, Any]) -> str:
        results = data.get("results") or []
        summary = ", ".join(f"{r['name']}->{r['parent']}" for r in results)
        return f"parent {data.get('action')}: {summary}"

    _rpc("parent", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def collection(
    action: str = typer.Option(..., "--action", help="create|move|link|unlink|list"),
    name: str | None = typer.Option(None, "--name", help="collection 名（list 以外は必須）"),
    targets: str | None = typer.Option(
        None, "--targets", "--target", help="対象オブジェクト（move/link/unlink で必須）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """コレクションを作成/移動/link/unlink/一覧する。"""
    params: dict[str, Any] = {"action": action}
    if name is not None:
        params["name"] = name
    if targets is not None:
        params["targets"] = targets
    if regex:
        params["regex"] = True

    def human(data: dict[str, Any]) -> str:
        if data.get("action") == "list":
            cols = ", ".join(f"{c['name']}({c['objects']})" for c in data.get("collections", []))
            return f"collections [{cols}]"
        if data.get("action") == "create":
            return f"created collection '{data.get('name')}'"
        results = data.get("results") or []
        names = ", ".join(r["name"] for r in results)
        return f"{data.get('action')} '{data.get('collection')}': {names}"

    _rpc("collection", params, json_out=json_out, port=port, human=human, request_id=request_id)


@app.command()
def mesh(
    op: str = typer.Option(
        ..., "--op", help="recalc-normals|merge-by-distance|extrude|bevel|inset|boolean|decimate"
    ),
    targets: str = typer.Option(
        ..., "--targets", "--target", help="対象オブジェクト（完全一致・--regex で正規表現）"
    ),
    regex: bool = typer.Option(
        False, "--regex", help="targets を正規表現として解釈する（既定は完全名一致）"
    ),
    inside: bool = typer.Option(False, "--inside", help="recalc-normals: 法線を内向きに"),
    distance: float | None = typer.Option(
        None, "--distance", help="merge-by-distance: マージ距離（既定 0.0001）"
    ),
    offset: str | None = typer.Option(
        None,
        "--offset",
        help="extrude: 押し出しベクトル x,y,z（world 空間・move/duplicate と同じ）",
    ),
    width: float | None = typer.Option(
        None, "--width", help="bevel: ベベル幅（ローカル単位・0以上）"
    ),
    segments: int | None = typer.Option(None, "--segments", help="bevel: 分割数（既定1・1〜100）"),
    thickness: float | None = typer.Option(
        None, "--thickness", help="inset: インセット厚み（0以上）"
    ),
    operation: str | None = typer.Option(
        None, "--operation", help="boolean: 演算 UNION|DIFFERENCE|INTERSECT"
    ),
    with_object: str | None = typer.Option(
        None, "--with", help="boolean: 相手 mesh オブジェクト名"
    ),
    ratio: float | None = typer.Option(None, "--ratio", help="decimate: 削減比率 0..1"),
    make_single_user: bool = typer.Option(
        False, "--make-single-user", help="共有mesh時に単一ユーザ化を許可"
    ),
    request_id: str | None = typer.Option(None, "--id", help="リクエストID(UUIDv4)"),
    async_out: bool = typer.Option(
        False, "--async", help="job_id を即返し（boolean/decimate のみ・既定は自動待機）"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """メッシュを編集する（法線再計算 / 距離マージ / 押し出し / ベベル / インセット / ブール / デシメート）。"""
    params: dict[str, Any] = {"op": op, "targets": targets}
    if regex:
        params["regex"] = True
    # op 専用 param は明示時のみ送る（op 別検証で別 op への誤送信を弾けるよう presence を保つ）。
    if inside:
        params["inside"] = True
    if distance is not None:
        params["distance"] = distance
    if width is not None:
        params["width"] = width
    if segments is not None:
        params["segments"] = segments
    if thickness is not None:
        params["thickness"] = thickness
    if operation is not None:
        params["operation"] = operation
    if with_object is not None:
        params["with_object"] = with_object
    if ratio is not None:
        params["ratio"] = ratio
    if make_single_user:
        params["make_single_user"] = True
    try:
        if offset is not None:
            params["offset"] = _parse_vec("offset", offset, 3)
    except ValueError as e:
        _emit_error(json_out, ErrorCode.INVALID_PARAMS, str(e))
        raise typer.Exit(int(ExitCode.INPUT)) from None

    def human(data: dict[str, Any]) -> str:
        op_ = data.get("op")
        if op_ == "recalc-normals":
            return (
                f"{data.get('name')} recalc-normals: faces={data.get('faces')} "
                f"flipped={data.get('flipped')} inside={data.get('inside')}"
            )
        if op_ == "merge-by-distance":
            return (
                f"{data.get('name')} merge-by-distance: merged={data.get('merged')} "
                f"({data.get('before')}→{data.get('after')})"
            )
        # extrude / bevel / inset / boolean / decimate: ジオメトリ増減（符号付き）+ 結果統計。
        delta = data.get("delta") or {}
        st = data.get("stats") or {}

        def _signed(n: Any) -> str:
            return f"{n:+d}" if isinstance(n, int) else str(n)

        prefix = f"{data.get('name')} {op_}"
        if op_ == "boolean":
            prefix += f" ({data.get('operation')} with {data.get('with_object')})"
        elif op_ == "decimate":
            prefix += f" (ratio={data.get('ratio')})"
        return (
            f"{prefix}: "
            f"{_signed(delta.get('vertices'))}v/{_signed(delta.get('edges'))}e/"
            f"{_signed(delta.get('polygons'))}f → "
            f"{st.get('vertices')}v/{st.get('edges')}e/{st.get('polygons')}f"
        )

    _rpc(
        "mesh",
        params,
        json_out=json_out,
        port=port,
        human=human,
        request_id=request_id,
        async_=async_out,
    )


@app.command("request-status")
def request_status(
    request_id: str = typer.Option(..., "--id", help="リクエストID(UUIDv4)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """リクエストの決着状態を取得する（タイムアウト後の後追い回収）。"""

    def human(data: dict[str, Any]) -> str:
        base = f"id={data.get('id')} state={data.get('state')} known={data.get('known')}"
        return base + _watchdog_suffix(data)

    _rpc("request-status", {"id": request_id}, json_out=json_out, port=port, human=human)


@app.command("job-status")
def job_status(
    request_id: str = typer.Option(..., "--id", help="ジョブID(=request_id)"),
    json_out: bool = typer.Option(False, "--json", help="JSON で出力"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """非同期 job（heavy コマンドの --async）の状態を取得する（request-status を1回問い合わせ）。"""

    def human(data: dict[str, Any]) -> str:
        base = f"job_id={data.get('id')} state={data.get('state')} known={data.get('known')}"
        return base + _watchdog_suffix(data)

    _rpc("request-status", {"id": request_id}, json_out=json_out, port=port, human=human)


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
