"""dispatch 共通ヘルパ（param 検証/mode 検証/レスポンス整形/破壊操作ガード。ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。`_file_sha256_size` は元は
print-export セクションの直前に定義されていたが、`_export`（io.py）からも呼ばれる
複数ドメイン利用のためここへ集約する（単一ドメイン利用は各サブモジュールへ、複数ドメイン
利用は共通ヘルパへ、という P2-4 分割方針に従う）。
"""

from __future__ import annotations

from typing import Any

from bli_core.commands import Command, get_command, load_definitions
from bli_core.errors import (
    RPC_BUSINESS_ERROR,
    RPC_INVALID_PARAMS,
    RPC_METHOD_NOT_FOUND,
    ErrorCategory,
    ErrorCode,
    make_error,
)
from bli_core.protocol import JsonRpcError
from bli_core.schema import validate_from_dict
from bli_core.types import Mode

# ---- 共通ヘルパ ----


def _command(name: str) -> Command:
    load_definitions()
    cmd = get_command(name)
    if cmd is None:  # 定義漏れ（コードバグ）
        raise JsonRpcError(RPC_METHOD_NOT_FOUND, f"method not found: {name}")
    return cmd


def _validate(cmd: Command, params: dict[str, Any]) -> None:
    """params を SSOT スキーマで検証する。不正なら INVALID_PARAMS。"""
    errors = validate_from_dict(cmd, params)
    if errors:
        raise JsonRpcError(RPC_INVALID_PARAMS, ErrorCode.INVALID_PARAMS, errors[0])


# required_mode -> `bli mode --to <...>` の案内文（P1-2: mode コマンド新設に伴い、GUI操作でしか
# 戻れなかった E_MODE_MISMATCH の remediation を具体的な復帰コマンドへ更新・U9対策）。
_MODE_CLI_HINT: dict[Mode, str] = {
    Mode.OBJECT: "bli mode --to object",
    Mode.EDIT: "bli mode --to edit",
}


def _check_mode(cmd: Command, current: str) -> None:
    """required_mode を検証する。不一致は自動遷移せず E_MODE_MISMATCH。"""
    req = cmd.required_mode
    if req is Mode.ANY:
        return
    ok = (req is Mode.OBJECT and current == "OBJECT") or (
        req is Mode.EDIT and current.startswith("EDIT")
    )
    if not ok:
        hint = _MODE_CLI_HINT.get(req, f"{req.value} モードに切り替えて")
        raise JsonRpcError(
            RPC_BUSINESS_ERROR,
            ErrorCode.E_MODE_MISMATCH,
            make_error(
                ErrorCode.E_MODE_MISMATCH,
                category=ErrorCategory.PRECONDITION,
                retryable=False,
                symptom=f"必要モード {req.value}（現在 {current}）",
                remediation=f"{hint} を実行してください（自動遷移はしません）",
            ),
        )


def _ok(
    operation: str,
    data: dict[str, Any] | None,
    *,
    verified: bool = True,
    fingerprint: str | None = None,
    output_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """成功レスポンス（data-model §2.5 のエンベロープ）。

    退避時は data=None / output_ref=descriptor、inline 時は data=<...> / output_ref=None。
    """
    return {
        "success": True,
        "operation": operation,
        "verified": verified,
        "fingerprint": fingerprint,
        "output_ref": output_ref,
        "data": data,
    }


def _ok_offload(
    operation: str, data: dict[str, Any], schema: str, *, fingerprint: str | None = None
) -> dict[str, Any]:
    """閾値超ならファイル退避し output_ref を、未満なら inline data を載せて返す（M5）。"""
    from bli_core import output_ref as outref
    from bli_core import runtime

    inline, descriptor = outref.maybe_offload(schema, data, runtime.outputs_dir())
    return _ok(operation, inline, fingerprint=fingerprint, output_ref=descriptor)


def _require_input(condition: bool, symptom: str, remediation: str) -> None:
    """USER_INPUT 前提を満たさなければ INVALID_PARAMS を投げる（bpy 到達前に弾ける）。"""
    if not condition:
        raise JsonRpcError(
            RPC_INVALID_PARAMS,
            ErrorCode.INVALID_PARAMS,
            make_error(
                ErrorCode.INVALID_PARAMS,
                category=ErrorCategory.USER_INPUT,
                retryable=False,
                symptom=symptom,
                remediation=remediation,
            ),
        )


def _guard_shared_mesh(gateway: Any, obj: Any, params: dict[str, Any]) -> None:
    """共有 mesh（users>=2）は --make-single-user 明示が無い限り拒否する（spec §破壊防止）。

    set-origin / apply-transform など mesh データを書き換える破壊的操作で共通利用する。
    """
    if gateway.mesh_user_count(obj) >= 2:
        if not bool(params.get("make_single_user", False)):
            raise JsonRpcError(
                RPC_BUSINESS_ERROR,
                ErrorCode.E_PRECONDITION,
                make_error(
                    ErrorCode.E_PRECONDITION,
                    category=ErrorCategory.PRECONDITION,
                    retryable=False,
                    symptom=f"共有 mesh（users={gateway.mesh_user_count(obj)}）です",
                    remediation="--make-single-user を付けて単一ユーザ化を許可してください",
                ),
            )
        gateway.make_single_user_mesh(obj)


def _resolve_boolean_operand(gateway: Any, obj: Any, with_object: Any) -> Any:
    """BOOLEAN 演算の相手を解決し、自己参照/非 mesh を弾く。

    `modifier --action add --type BOOLEAN` と `mesh --op boolean` の両方から呼ぶ共有ロジック
    （二重定義で文言/条件がドリフトするのを防ぐ）。呼び出し側は **状態変更（共有 mesh の単一
    ユーザ化）より前** にこれを通すこと（不正な相手で対象 mesh を分離しないため）。
    """
    operand = gateway.require_single(str(with_object))
    _require_input(
        operand.name != obj.name,
        symptom="BOOLEAN の相手に自分自身は指定できません",
        remediation="別のオブジェクトを --with に指定してください",
    )
    _require_input(
        operand.type == "MESH",
        symptom=f"BOOLEAN の相手は mesh が必要です（--with={operand.name} type={operand.type}）",
        remediation="mesh オブジェクトを --with に指定してください",
    )
    return operand


def _capability_unavailable(symptom: str, remediation: str) -> JsonRpcError:
    """能力欠如（CAPABILITY_UNAVAILABLE・category=ENVIRONMENT）の業務エラーを組み立てる。"""
    return JsonRpcError(
        RPC_BUSINESS_ERROR,
        ErrorCode.CAPABILITY_UNAVAILABLE,
        make_error(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            category=ErrorCategory.ENVIRONMENT,
            retryable=False,
            symptom=symptom,
            remediation=remediation,
        ),
    )


def _file_sha256_size(path: str) -> tuple[str, int]:
    """ファイルの sha256（16進）とサイズをストリーミング算出する（大きい出力でも省メモリ）。"""
    import hashlib

    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size
