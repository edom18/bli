"""bli アドオンの TCP サーバ（M2）。spec §5/§6/§7 / data-model.md。

bpy 非依存（L3 でプロセス内テスト可能）。bpy 実行ディスパッチ（timers）は M3 で
handler を差し替えて結線する。ここでは ping/echo の疎通までを担う。

スレッド: accept ループ（1本）+ 接続ごとのハンドラスレッド。
セッションは session_lock で単一直列（2本目は SESSION_BUSY fail-fast）。
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import select
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from bli_core import protocol as proto
from bli_core import runtime
from bli_core.commands import get_command, is_heavy_request, load_definitions
from bli_core.errors import (
    RPC_BUSINESS_ERROR,
    RPC_INTERNAL_ERROR,
    RPC_INVALID_PARAMS,
    ErrorCategory,
    ErrorCode,
    make_error,
)
from bli_core.protocol import JsonRpcError

from .dispatcher import ACCEPTED, TimeoutPending
from .handlers import ServerInfo
from .handlers import dispatch as default_dispatch
from .request_registry import RequestRegistry

READ_TIMEOUT = 30.0

# settle(result, error) -> resp: ジョブ完了時に呼ぶ確定処理（resp 構築 + registry 完了）。
SettleFn = Callable[[Any, BaseException | None], dict[str, Any]]
# ハンドラは settle を受け取り、ジョブ完了時（同期なら即時、非同期ならメインスレッド）に呼ぶ。
DispatchFn = Callable[[str, dict[str, Any], ServerInfo, SettleFn], dict[str, Any]]

# セッションロックを必要としないメタ問い合わせ（registry を直接読むだけ）。
# 別セッションが実行中（SESSION_BUSY）でも応答できる＝タイムアウト後の後追い回収を成立させる。
LOCK_FREE_METHODS = frozenset({"request-status"})


def _sync_handler(
    method: str, params: dict[str, Any], info: ServerInfo, settle: SettleFn
) -> dict[str, Any]:
    """dispatcher を使わない同期ディスパッチ（テスト/疎通用）。接続スレッドで即 settle。"""
    try:
        return settle(default_dispatch(method, params, info), None)
    except Exception as e:
        return settle(None, e)


def _atomic_write(path, text: str, mode: int = 0o600) -> None:
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(tmp, mode)  # posix で所有者限定。Windows は限定的
    except OSError:
        pass
    os.replace(tmp, path)


class Server:
    def __init__(
        self,
        host: str,
        port: int,
        info: ServerInfo,
        handler: DispatchFn,
        ttl: float | None = None,
        render_busy: Callable[[], bool] | None = None,
        watchdog_status: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.info = info
        self._handler = handler
        # レンダ中判定（受信スレッドが dispatch 前に読む・既定は常に False＝GUI 非常駐/テスト）。
        # アドオンは render_state.is_busy を注入する（bpy 依存は addon 側に閉じ込める）。
        self._render_busy = render_busy or (lambda: False)
        # メインスレッド応答性（M10 T10.3）。受信スレッドが lock-free に読み request-status へ載せる。
        # アドオンは watchdog.snapshot を注入する（既定は常に responsive＝GUI 非常駐/テスト）。
        # 既定 stub も watchdog.snapshot と同じキー形（responsive/unresponsive_since/last_pump_age/
        # threshold/kind）を返す＝注入有無で消費側の契約がぶれない。
        self._watchdog_status = watchdog_status or (
            lambda: {
                "responsive": True,
                "unresponsive_since": None,
                "last_pump_age": None,
                "threshold": runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD,
                "kind": None,
            }
        )
        # registry の終端エントリ保持時間（冪等性 + 非同期 job 結果の後追い回収）。M10: 既定 auto-wait /
        # job-wait の上限（JOB_WAIT_TIMEOUT）より **短くしない**（短いと完了 job が TTL purge され、
        # 遅延 job-wait が結果を取り損ねて UNKNOWN になる・敵対的/設計レビュー P1）。
        self._registry = RequestRegistry(
            ttl if ttl is not None else max(600.0, runtime.JOB_WAIT_TIMEOUT)
        )
        self._session_lock = threading.Lock()
        self._stop = threading.Event()
        self._listen: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._conns: set[socket.socket] = set()
        self._token = ""

    # ---- ライフサイクル ----

    def start(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((self.host, self.port))
        except OSError as e:
            srv.close()
            raise RuntimeError(
                f"bind 失敗 {self.host}:{self.port}: {e}（二重起動 or ポート使用中の可能性）"
            ) from e
        srv.listen(8)
        srv.settimeout(0.5)
        # 実際にバインドされたポートを採用（port=0 で OS 割当を許容）
        self.port = srv.getsockname()[1]
        self._listen = srv

        self._token = secrets.token_urlsafe(32)
        self._write_runtime_files()

        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="bli-accept"
        )
        self._accept_thread.start()

    def stop(self) -> None:
        self._stop.set()
        listen = self._listen
        self._listen = None
        if listen is not None:
            try:
                listen.close()
            except OSError:
                pass
        # in-flight 接続を解放
        for c in list(self._conns):
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2.0)
            self._accept_thread = None
        self._cleanup_runtime_files()

    # ---- ランタイムファイル ----

    def _write_runtime_files(self) -> None:
        _atomic_write(str(runtime.token_path()), self._token)
        conn = {
            "host": self.host,
            "port": self.port,
            "pid": os.getpid(),
            "protocol_version": proto.PROTOCOL_VERSION,
            "blender_version": self.info.blender_version,
            "schema_hash": self.info.schema_hash,
            "started_at": time.time(),
        }
        _atomic_write(
            str(runtime.connection_path()), json.dumps(conn, ensure_ascii=False), mode=0o644
        )

    def _cleanup_runtime_files(self) -> None:
        for p in (runtime.connection_path(), runtime.token_path()):
            try:
                p.unlink()
            except OSError:
                pass

    # ---- 受信 ----

    def _accept_loop(self) -> None:
        listen = self._listen
        while not self._stop.is_set() and listen is not None:
            try:
                r, _, _ = select.select([listen], [], [], 0.5)
            except OSError:
                break
            if not r:
                continue
            try:
                conn, _addr = listen.accept()
            except OSError:
                break
            conn.settimeout(READ_TIMEOUT)
            t = threading.Thread(target=self._serve, args=(conn,), daemon=True, name="bli-conn")
            t.start()

    def _serve(self, conn: socket.socket) -> None:
        self._conns.add(conn)
        got_lock = False
        try:
            # 先に HELLO を受信・認証してから、セッション取得を判定する。
            if not self._authenticate(conn):
                return
            # ロックを取れれば通常セッション。取れなくても hello-ok は返し、
            # lock-free メソッド（request-status）だけ受け付ける限定セッションにする。
            # こうすることで別セッション実行中でもタイムアウト後の決着確認が可能になる。
            got_lock = self._session_lock.acquire(blocking=False)
            self._send(conn, self._hello_ok())
            self._rpc_loop(conn, has_lock=got_lock)
        except (OSError, ConnectionError):
            pass
        finally:
            if got_lock:
                self._session_lock.release()
            self._conns.discard(conn)
            try:
                conn.close()
            except OSError:
                pass

    def _authenticate(self, conn: socket.socket) -> bool:
        try:
            first = proto.read_frame(conn.recv)
        except (proto.FrameTooLarge, json.JSONDecodeError, ConnectionError, OSError):
            # HTTP/WebSocket 様式や巨大フレームは即切断（DNS rebinding 対策）
            return False
        if not proto.is_hello(first):
            self._send(
                conn, self._err(None, ErrorCode.AUTH_FAILED, "最初のフレームは hello が必要")
            )
            return False
        token = first.get("token", "")
        if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
            self._send(conn, self._err(None, ErrorCode.AUTH_FAILED, "トークン不一致"))
            return False
        if proto.major(first.get("protocol_version", "")) != proto.major(proto.PROTOCOL_VERSION):
            self._send(
                conn,
                self._err(None, ErrorCode.PROTOCOL_VERSION_MISMATCH, "protocol MAJOR 不一致"),
            )
            return False
        return True

    def _hello_ok(self) -> dict[str, Any]:
        session_uid = secrets.token_hex(8)
        return proto.build_hello_ok(
            self.info.blender_version,
            self.info.schema_hash,
            session_uid,
            self.info.capabilities,
        )

    def _rpc_loop(self, conn: socket.socket, has_lock: bool) -> None:
        while not self._stop.is_set():
            try:
                msg = proto.read_frame(conn.recv)
            except (ConnectionError, OSError, json.JSONDecodeError, proto.FrameTooLarge):
                return
            self._handle_rpc(conn, msg, has_lock)

    def _handle_rpc(self, conn: socket.socket, msg: Any, has_lock: bool) -> None:
        try:
            method, rid, params = proto.parse_request(msg)
        except JsonRpcError as e:
            rid = msg.get("id") if isinstance(msg, dict) else None
            self._send(conn, proto.error_response_from(rid, e))
            return

        # lock-free メソッド（request-status）は registry を直接読むメタ問い合わせ。
        # 冪等性登録（begin）やメイン直列ディスパッチを経由せず、セッションロックも不要。
        if method in LOCK_FREE_METHODS:
            self._send(conn, self._request_status(rid, params))
            return

        # 限定セッション（ロック未取得）は lock-free 以外を SESSION_BUSY で拒否する。
        if not has_lock:
            self._send(conn, self._busy_error(rid))
            return

        # レンダリング中は重量/破壊系を即拒否する（spec §7・研究 §E12）。**キューに積まない**＝
        # begin も settle もせず、フリーズ中のジョブ滞留を防ぐ。read-only と lock-free
        # （request-status は上で処理済み）はレンダ中も通す＝観測性を維持。
        if self._render_busy() and self._blocked_during_render(method, params):
            self._send(conn, self._busy_rendering_error(rid))
            return

        state, cached = self._registry.begin(rid)
        if state == "cached" and cached is not None:
            self._send(conn, cached)
            return
        if state == "in_progress":
            self._send(
                conn,
                proto.build_error(
                    rid,
                    RPC_BUSINESS_ERROR,
                    ErrorCode.IN_PROGRESS,
                    make_error(
                        ErrorCode.IN_PROGRESS,
                        category=ErrorCategory.ENVIRONMENT,
                        retryable=True,
                        symptom="同一IDのリクエストが実行中",
                        remediation="request-status で決着を確認してください",
                    ),
                ),
            )
            return

        def settle(result: Any, error: BaseException | None) -> dict[str, Any]:
            # ジョブ完了時の確定処理。非同期ハンドラではメインスレッドで呼ばれる。
            resp = self._build_resp(rid, result, error)
            self._registry.complete(rid, resp, ok=(error is None))
            return resp

        try:
            resp = self._handler(method, params, self.info, settle)
        except TimeoutPending:
            # 実行はメインスレッドで継続中。registry は RUNNING のまま残し、
            # ジョブ完走時に settle が DONE/FAILED へ更新する（request-status で回収可能）。
            resp = self._timeout_resp(rid)
        except Exception as e:
            # ハンドラが settle 前に異常終了した場合の保険。
            resp = settle(None, e)
        if resp is ACCEPTED:
            # heavy job を受理した（M10・spec §7）。実体は pump で実行継続中で、registry は begin() の
            # RUNNING のまま。job_id=rid で accepted を即返し、完了時に settle が DONE/FAILED を確定する。
            # クライアントは request-status / job-wait で最終結果を回収する。
            resp = proto.build_success(
                rid,
                {"success": True, "operation": method, "accepted": True, "job_id": rid},
            )
        self._send(conn, resp)

    def _build_resp(self, rid: str, result: Any, error: BaseException | None) -> dict[str, Any]:
        """ドメイン結果/例外から JSON-RPC レスポンスを構築する。"""
        if error is None:
            return proto.build_success(rid, result)
        if isinstance(error, JsonRpcError):
            return proto.error_response_from(rid, error)
        eo = make_error(
            "INTERNAL",
            category=ErrorCategory.INTERNAL,
            retryable=False,
            symptom=str(error),
            code_bug=True,
        )
        return proto.build_error(rid, RPC_INTERNAL_ERROR, "INTERNAL", eo)

    def _timeout_resp(self, rid: str) -> dict[str, Any]:
        """タイムアウト（実行は継続中の可能性）。後追いは request-status。"""
        eo = make_error(
            ErrorCode.TIMEOUT,
            category=ErrorCategory.ENVIRONMENT,
            retryable=True,
            symptom="実行がタイムアウト待機を超過しました（メインスレッドで継続中の可能性）",
            remediation="request-status --id <この id> で決着を確認してください",
        )
        return proto.build_error(rid, RPC_BUSINESS_ERROR, ErrorCode.TIMEOUT, eo)

    def _request_status(self, rid: str, params: dict[str, Any]) -> dict[str, Any]:
        """対象 id の決着状態を registry から返す（spec §7 後追い回収）。"""
        target = params.get("id")
        if not isinstance(target, str) or not target:
            eo = make_error(
                ErrorCode.INVALID_PARAMS,
                category=ErrorCategory.USER_INPUT,
                retryable=False,
                symptom="request-status には id が必要です",
                remediation="--id <UUIDv4> を指定してください",
            )
            return proto.build_error(rid, RPC_INVALID_PARAMS, ErrorCode.INVALID_PARAMS, eo)
        state, result = self._registry.lookup(target)
        data = {
            "id": target,
            "known": state is not None,
            "state": state or "UNKNOWN",
            "result": result,
            # メインスレッド応答性（M10 T10.3）。サーバ全体の状態で id 非依存。固まった重量 job を
            # job-wait でポーリング中のエージェントが「進行中だが固まっている」を観測できる。
            # 受信スレッドが watchdog を直接読むため、メインが塞がっていても応答する（lock-free）。
            "watchdog": self._watchdog_status(),
        }
        return proto.build_success(
            rid, {"success": True, "operation": "request-status", "data": data}
        )

    # ---- 補助 ----

    def _send(self, conn: socket.socket, obj: dict[str, Any]) -> None:
        try:
            conn.sendall(proto.encode_frame(obj))
        except OSError:
            pass

    def _busy_error(self, rid: str | None) -> dict[str, Any]:
        return self._err(
            rid,
            ErrorCode.SESSION_BUSY,
            "別セッションが使用中です（単一セッションのみ）。request-status は利用可能",
            category=ErrorCategory.ENVIRONMENT,
            retryable=True,
        )

    def _blocked_during_render(self, method: str, params: dict[str, Any]) -> bool:
        """レンダ中に拒否すべき要求か（mutating または heavy）。read-only は通す（観測性）。"""
        load_definitions()  # 受信スレッドで COMMANDS 未ロードでも get_command が解決できるよう冪等ロード
        cmd = get_command(method)
        if cmd is None:
            return False  # 未知メソッドは通常経路（METHOD_NOT_FOUND）に任せる
        return cmd.mutates or is_heavy_request(cmd, params)

    def _busy_rendering_error(self, rid: str | None) -> dict[str, Any]:
        eo = make_error(
            ErrorCode.BUSY_RENDERING,
            category=ErrorCategory.ENVIRONMENT,
            retryable=True,
            symptom="レンダリング中のため重量/破壊系コマンドを受け付けられません（キューに積みません）",
            remediation="レンダ完了後に再試行してください（read-only と request-status はレンダ中も可）",
        )
        return proto.build_error(rid, RPC_BUSINESS_ERROR, ErrorCode.BUSY_RENDERING, eo)

    def _err(
        self,
        rid: str | None,
        kind: str,
        symptom: str,
        category: str = ErrorCategory.ENVIRONMENT,
        retryable: bool = False,
    ) -> dict[str, Any]:
        return proto.build_error(
            rid,
            RPC_BUSINESS_ERROR,
            kind,
            make_error(kind, category=category, retryable=retryable, symptom=symptom),
        )


# ---- シングルトン（アドオン register/unregister から使う）----

_server: Server | None = None


def start(
    blender_version: str = "dev",
    capabilities: list[str] | None = None,
    schema_hash: str = "",
    host: str | None = None,
    port: int | None = None,
    handler: DispatchFn | None = None,
    render_busy: Callable[[], bool] | None = None,
    watchdog_status: Callable[[], dict[str, Any]] | None = None,
) -> Server:
    """サーバを起動（既存があれば先に停止して二重 listen を防ぐ）。"""
    global _server
    if _server is not None:
        _server.stop()
        _server = None
    info = ServerInfo(blender_version, schema_hash, capabilities or [])
    srv = Server(
        host or runtime.DEFAULT_HOST,
        port or runtime.DEFAULT_PORT,
        info,
        handler or _sync_handler,
        render_busy=render_busy,
        watchdog_status=watchdog_status,
    )
    srv.start()
    _server = srv
    return srv


def stop() -> None:
    global _server
    if _server is not None:
        _server.stop()
        _server = None
