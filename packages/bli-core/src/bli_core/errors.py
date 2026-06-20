"""エラーモデル（data-model.md §3 / spec §8）。純Python・依存ゼロ。"""

from __future__ import annotations

import dataclasses
from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    """CLI 終了コード（spec §8）。"""

    SUCCESS = 0
    FAILURE = 1  # 確定失敗（業務エラー）
    TIMEOUT_PENDING = 2  # 未決（request-status で要確認）
    CONNECTION = 3  # 接続不能 / アドオン未起動 / 認証失敗
    INPUT = 4  # 入力エラー（CLI引数・スキーマ不一致）


class ErrorCategory:
    """エラーカテゴリ（定数）。"""

    PRECONDITION = "PRECONDITION"
    USER_INPUT = "USER_INPUT"
    ENVIRONMENT = "ENVIRONMENT"
    INTERNAL = "INTERNAL"


# JSON-RPC 標準コード
RPC_PARSE_ERROR = -32700
RPC_INVALID_REQUEST = -32600
RPC_METHOD_NOT_FOUND = -32601
RPC_INVALID_PARAMS = -32602
RPC_INTERNAL_ERROR = -32603
RPC_BUSINESS_ERROR = -32000  # 業務エラー（ErrorObject を data に格納）


class ErrorCode:
    """spec §8 のエラーコード定数（kind）。"""

    BUSY_RENDERING = "BUSY_RENDERING"
    MAIN_THREAD_UNRESPONSIVE = "MAIN_THREAD_UNRESPONSIVE"
    REQUEST_CANCELLED = "REQUEST_CANCELLED"
    TIMEOUT = "TIMEOUT"
    SERVER_SHUTTING_DOWN = "SERVER_SHUTTING_DOWN"
    NO_RESPONSE = "NO_RESPONSE"
    CONNECTION_RESET = "CONNECTION_RESET"
    SESSION_BUSY = "SESSION_BUSY"
    IN_PROGRESS = "IN_PROGRESS"
    E_MODE_MISMATCH = "E_MODE_MISMATCH"
    E_TARGET_NOT_FOUND = "E_TARGET_NOT_FOUND"
    E_PRECONDITION = "E_PRECONDITION"
    E_OPERATOR = "E_OPERATOR"
    W_STATE_DRIFT = "W_STATE_DRIFT"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    STALE_OUTPUT = "STALE_OUTPUT"
    PROTOCOL_VERSION_MISMATCH = "PROTOCOL_VERSION_MISMATCH"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    EXEC_DISABLED = "EXEC_DISABLED"
    EXEC_ERROR = "EXEC_ERROR"
    INVALID_PARAMS = "INVALID_PARAMS"
    INVALID_REQUEST = "INVALID_REQUEST"
    METHOD_NOT_FOUND = "METHOD_NOT_FOUND"
    PROTOCOL_FRAME_TOO_LARGE = "PROTOCOL_FRAME_TOO_LARGE"
    AUTH_FAILED = "AUTH_FAILED"


@dataclasses.dataclass
class ErrorObject:
    """構造化エラー（エージェントの自己修正用）。"""

    category: str
    kind: str
    retryable: bool
    cause: str = ""
    userVisibleSymptom: str = ""
    codeBug: bool = False
    remediation: str = ""
    tracebackRef: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def make_error(
    kind: str,
    *,
    category: str = ErrorCategory.PRECONDITION,
    retryable: bool = False,
    cause: str = "",
    symptom: str = "",
    code_bug: bool = False,
    remediation: str = "",
    traceback_ref: str | None = None,
) -> ErrorObject:
    """ErrorObject を生成する薄いヘルパ。"""
    return ErrorObject(
        category=category,
        kind=kind,
        retryable=retryable,
        cause=cause,
        userVisibleSymptom=symptom,
        codeBug=code_bug,
        remediation=remediation,
        tracebackRef=traceback_ref,
    )
