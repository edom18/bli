"""ドメインハンドラ + dispatch ルータ（M3）。spec §6 / methods.md / 付録B。

bpy 系コマンド（scene-info/object-info/set-origin）を `gateway` 経由で実行する。
それ以外（ping/echo 等）は `handlers.dispatch` に委譲する。

- param 検証はサーバ側でも行う（`bli_core.schema.validate_from_dict` → INVALID_PARAMS）。
- required_mode を実行直前に検証する（自動遷移はしない → E_MODE_MISMATCH）。
- `gateway`/`bpy` は **遅延 import**（pytest では bpy が無いため、検証パスだけ到達可能）。

元は単一ファイル `ops.py`（2,222 行）だったものを責務単位のサブモジュールへ分割した
（P2-4・gateway/ 分割と同じ方針）。挙動は不変・re-export のみでこのパッケージの外から見た
`bli_addon.ops.*` の形は分割前と同一。テストがプライベート名（`_check_mode`・`_exec_python`・
`_premark_session_modified` 等）にも直接アクセスするため、公開名だけでなく全トップレベル・
シンボルをここで明示的に re-export する。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bli_core.commands import get_command, load_definitions

from .. import handlers
from ..handlers import ServerInfo
from ._shared import (
    _MODE_CLI_HINT,
    _capability_unavailable,
    _check_mode,
    _command,
    _file_sha256_size,
    _guard_shared_mesh,
    _ok,
    _ok_offload,
    _require_input,
    _resolve_boolean_operand,
    _validate,
)
from .capture import _capture, _png_dimensions
from .exec_python import (
    _audit_exec,
    _exec_blocked_restricted,
    _exec_disabled,
    _exec_error,
    _exec_python,
)
from .io import _EXPORT_FBX_ONLY_PARAMS, _export, _import, _open, _save
from .materials import _MATERIAL_CREATE_ONLY_PARAMS, _material
from .mesh import (
    _ALL_MESH_OP_PARAMS,
    _DEFAULT_BEVEL_SEGMENTS,
    _DEFAULT_MERGE_DISTANCE,
    _MAX_BEVEL_SEGMENTS,
    _MESH_OP_PARAMS,
    _mesh,
)
from .modifiers import (
    _ALL_MODIFIER_TYPE_PARAMS,
    _MAX_SUBSURF_LEVELS,
    _MODIFIER_TYPE_PARAMS,
    _modifier,
)
from .objects import _delete, _duplicate, _list_objects, _object_info, _rename, _select
from .print3d import (
    _BMESH_CHECK_CATEGORIES,
    _print_check,
    _print_export,
    _print_repair,
    _print_setup,
    _stl_triangle_count,
)
from .scene import _add, _collection, _do_undo_redo, _mode, _parent, _redo, _scene_info, _undo
from .straighten import _is_nonzero_vec, _straighten
from .transforms import _apply_transform, _set_origin, _transform

__all__ = [
    "_ALL_MESH_OP_PARAMS",
    "_ALL_MODIFIER_TYPE_PARAMS",
    "_BMESH_CHECK_CATEGORIES",
    "_BPY_HANDLERS",
    "_DEFAULT_BEVEL_SEGMENTS",
    "_DEFAULT_MERGE_DISTANCE",
    "_EXPORT_FBX_ONLY_PARAMS",
    "_MATERIAL_CREATE_ONLY_PARAMS",
    "_MAX_BEVEL_SEGMENTS",
    "_MAX_SUBSURF_LEVELS",
    "_MESH_OP_PARAMS",
    "_MODE_CLI_HINT",
    "_MODIFIER_TYPE_PARAMS",
    "_SESSION_CLEARING_METHODS",
    "ServerInfo",
    "_add",
    "_apply_transform",
    "_audit_exec",
    "_capability_unavailable",
    "_capture",
    "_check_mode",
    "_collection",
    "_command",
    "_delete",
    "_do_undo_redo",
    "_duplicate",
    "_exec_blocked_restricted",
    "_exec_disabled",
    "_exec_error",
    "_exec_python",
    "_export",
    "_file_sha256_size",
    "_guard_shared_mesh",
    "_import",
    "_is_nonzero_vec",
    "_list_objects",
    "_material",
    "_mesh",
    "_mode",
    "_modifier",
    "_object_info",
    "_ok",
    "_ok_offload",
    "_open",
    "_parent",
    "_png_dimensions",
    "_premark_session_modified",
    "_print_check",
    "_print_export",
    "_print_repair",
    "_print_setup",
    "_redo",
    "_rename",
    "_require_input",
    "_resolve_boolean_operand",
    "_save",
    "_scene_info",
    "_select",
    "_set_origin",
    "_stl_triangle_count",
    "_straighten",
    "_transform",
    "_undo",
    "_validate",
    "dispatch",
    "handlers",
]

_BPY_HANDLERS: dict[str, Callable[[dict[str, Any], ServerInfo], dict[str, Any]]] = {
    "scene-info": _scene_info,
    "object-info": _object_info,
    "list-objects": _list_objects,
    "select": _select,
    "transform": _transform,
    "apply-transform": _apply_transform,
    "duplicate": _duplicate,
    "delete": _delete,
    "material": _material,
    "modifier": _modifier,
    "add": _add,
    "mode": _mode,
    "rename": _rename,
    "parent": _parent,
    "collection": _collection,
    "mesh": _mesh,
    "set-origin": _set_origin,
    "straighten": _straighten,
    "print-setup": _print_setup,
    "print-check": _print_check,
    "print-repair": _print_repair,
    "print-export": _print_export,
    "export": _export,
    "import": _import,
    "save": _save,
    "open": _open,
    "capture": _capture,
    "undo": _undo,
    "redo": _redo,
    "exec-python": _exec_python,
}


# save/open は破壊/置換の後にディスクと一致＝セッションを clean に戻すコマンド（将来 new/revert 等を
# 足すならここに追加する）。それ以外の mutates=True は実行前に modified にする。
_SESSION_CLEARING_METHODS = ("save", "open")


def _premark_session_modified(method: str) -> None:
    """mutating コマンドの実行 **前** にセッションを modified にする（open の未保存ガード・§E11）。

    実行後ではなく **実行前** に立てる理由（敵対的レビュー P1）: 途中まで mutate して例外を投げる
    ハンドラ（例: material の create 後に assign 失敗）でも、後続 open が未保存変更を検知して --force を
    要求する **安全側** に倒れる（実行後フックだと partial mutation が flag に乗らず silent data loss）。
    save/open（_SESSION_CLEARING_METHODS）は対象外で、成功後に dispatch が clean 化する。

    v1 は静的 `mutates` フラグで判定する（**保守的**: select/undo や、検証失敗で何も変えなかった
    mutating コマンドも modified 扱い＝open に --force が要る安全側）。実際に変更したかの per-invocation な
    精緻化（result 駆動の dirtied 信号）は繰越（methods.md 注記）。
    """
    if method in _SESSION_CLEARING_METHODS:
        return
    load_definitions()
    cmd = get_command(method)
    if cmd is not None and cmd.mutates:
        from .. import session_state

        session_state.mark_modified()


def dispatch(method: str, params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """bpy 系は専用ハンドラ、その他は handlers.dispatch に委譲する。"""
    # mutating コマンドは実行 **前** に pessimistic に modified（partial mutation でも安全側・§E11）。
    _premark_session_modified(method)
    fn = _BPY_HANDLERS.get(method)
    result = fn(params, info) if fn is not None else handlers.dispatch(method, params, info)
    # save/open が成功したらディスクと一致＝clean に戻す（例外時はここに来ない＝失敗 save/open は modified のまま）。
    if method in _SESSION_CLEARING_METHODS and result.get("success") is True:
        from .. import session_state

        session_state.mark_saved()
    return result
