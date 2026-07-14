"""BpyGateway コア: operator 実行ラッパ・undo・実行時 exec・共通ヘルパ（gateway/ 分割 P2-4）。

gateway パッケージの基盤層。run_operator（temp_override + poll 先行 + FINISHED 判定 + undo_push）・
undo/redo・scene fingerprint・exec_user_code に加え、他サブモジュールが共有するヘルパ
（_digest16 / _resolve_op / _unit_settings_dict）を集約する。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import bpy  # type: ignore

from bli_core.errors import (
    RPC_BUSINESS_ERROR,
    ErrorCategory,
    ErrorCode,
    make_error,
)
from bli_core.protocol import JsonRpcError


def _op_error(
    kind: str, symptom: str, *, category: str = ErrorCategory.PRECONDITION
) -> JsonRpcError:
    eo = make_error(kind, category=category, retryable=False, symptom=symptom)
    return JsonRpcError(RPC_BUSINESS_ERROR, kind, eo)


def _override_for(obj: Any, extra: dict[str, Any] | None) -> dict[str, Any]:
    ov: dict[str, Any] = {}
    if obj is not None:
        ov["active_object"] = obj
        ov["object"] = obj
        ov["selected_objects"] = [obj]
        # transform_apply 等は selected_editable_objects を反復する。現在の選択が
        # --targets と異なる場合に無関係なオブジェクトを巻き込まないよう、対象だけに絞る
        # （Codex P1）。読み取り専用の派生コンテキストだが temp_override で上書き可能。
        ov["selected_editable_objects"] = [obj]
    if extra:
        ov.update(extra)
    return ov


def run_operator(
    op: Any,
    obj: Any = None,
    *,
    message: str | None = None,
    extra_override: dict[str, Any] | None = None,
    **kwargs: Any,
) -> set[str]:
    """operator を temp_override 下で実行する（poll 先行 / FINISHED 判定 / undo_push）。"""
    override = _override_for(obj, extra_override)
    try:
        with bpy.context.temp_override(**override):
            if not op.poll():
                raise _op_error(ErrorCode.E_PRECONDITION, "poll() False（前提条件未達）")
            result = op(**kwargs)
    except RuntimeError as e:
        raise _op_error(ErrorCode.E_OPERATOR, f"operator 実行時エラー: {e}") from e
    if "FINISHED" not in result:
        raise _op_error(ErrorCode.E_OPERATOR, f"operator が完了しませんでした: {sorted(result)}")
    if message:
        with bpy.context.temp_override(**override):
            bpy.ops.ed.undo_push(message=message)
    return result


def push_undo(message: str) -> None:
    """operator を介さない直接変更後の Undo 境界を作る。"""
    bpy.ops.ed.undo_push(message=message)


def _require_gui_for_undo(verb: str) -> None:
    """undo/redo は GUI 前提（--background では undo スタックが不定・研究 §E7）。"""
    if bpy.app.background:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"{verb} には GUI が必要です（--background では undo/redo は機能しません）",
        )


def _step_undo_stack(op: Any, steps: int) -> int:
    """undo/redo operator を steps 回適用し、実際に適用できた段数を返す（スタック端で頭打ち）。

    bare 呼び出しで GUI では context override 不要（§E7・両版確認済み）。スタック端では `FINISHED`
    以外（CANCELLED）になる版と RuntimeError を投げる版の両方を「これ以上進めない＝端」として
    break で正規化し、INTERNAL 化を避ける（§6e）。
    """
    applied = 0
    for _ in range(steps):
        try:
            result = op()
        except RuntimeError:  # スタック端で raise する版も端として扱う（未捕捉→INTERNAL を防ぐ）
            break
        if "FINISHED" in result:
            applied += 1
        else:  # CANCELLED 等＝これ以上戻せない/進められない（スタック端）
            break
    return applied


def undo_steps(steps: int) -> int:
    """グローバル undo スタックを steps 段戻す。実際に適用できた段数を返す（GUI 必須・§E7）。"""
    _require_gui_for_undo("undo")
    return _step_undo_stack(bpy.ops.ed.undo, steps)


def redo_steps(steps: int) -> int:
    """グローバル undo スタックを steps 段進める（やり直す）。実際に適用できた段数を返す（GUI 必須）。"""
    _require_gui_for_undo("redo")
    return _step_undo_stack(bpy.ops.ed.redo, steps)


def scene_state_fingerprint() -> str:
    """シーン全体の粗いフィンガープリント（undo/redo の状態変化検証用）。

    全オブジェクトの name/type と matrix_world（丸め）をハッシュする。transform/add/delete の変化は
    捉えるが mesh データ内部の編集（bevel/merge 等）までは見ない（undo の粗い drift 指標・v1）。
    そのため matrix を変えない undo（mesh 内部編集のみの巻き戻し）では前後で同一値になり得る。
    読み取り前に view_layer.update() で matrix を最新化する。
    """
    bpy.context.view_layer.update()
    items = [
        {
            "name": o.name,
            "type": o.type,
            "matrix": [round(v, 6) for row in o.matrix_world for v in row],
        }
        for o in sorted(bpy.data.objects, key=lambda x: x.name)
    ]
    return _digest16({"objects": items})


def exec_user_code(code: str) -> tuple[Any, str]:
    """ユーザコードを `bpy` 注入済み namespace で実行し (ExecOutcome, fingerprint) を返す（M11）。

    bpy の接点（namespace への注入・実行後のシーン fingerprint）をこの gateway 層に集約する。
    実行メカニクス（ast 分割・stdout/stderr キャプチャ・例外捕捉）は bpy 非依存の `exec_runner` に
    委譲する（研究 §E14）。**サンドボックスはしない**＝コードは同一 OS 権限で走る（spec §459）。
    namespace は毎回新規（セッション REPL ではない・v1）。`__builtins__` は exec が自動注入する。
    """
    from .. import exec_runner

    namespace: dict[str, Any] = {"bpy": bpy, "__name__": "__bli_exec__"}
    outcome = exec_runner.run_code(code, namespace)
    # 実行で何が変わったか観測できるよう粗いシーン fingerprint を返す（undo/open と同じ指標）。
    return outcome, scene_state_fingerprint()


def _digest16(payload: dict[str, Any]) -> str:
    """JSON 化可能な状態の決定的 16 桁ハッシュ（verified 用の短ハッシュ）。"""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _unit_settings_dict(us: Any) -> dict[str, Any]:
    """unit_settings の要約（system / scale_length / length_unit）。"""
    return {
        "system": us.system,
        "scale_length": round(us.scale_length, 8),
        "length_unit": us.length_unit,
    }


def _resolve_op(operator_path: str) -> Any:
    """'ns.name' 文字列を bpy.ops の operator callable へ解決する（export/import 共用・dotロジック単一化）。"""
    ns, _, name = operator_path.partition(".")
    return getattr(getattr(bpy.ops, ns), name)
