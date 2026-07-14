"""オブジェクト解決/情報・選択・複製/削除・改名ハンドラ（ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

from typing import Any

from ..handlers import ServerInfo
from ._shared import _check_mode, _command, _ok, _require_input, _validate


def _list_objects(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("list-objects")
    _validate(cmd, params)
    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    type_filter = params.get("type")
    name_regex = params.get("name_regex")
    objs = gateway.list_objects(
        str(type_filter) if type_filter is not None else None,
        str(name_regex) if name_regex is not None else None,
    )
    return _ok("list-objects", {"objects": objs, "count": len(objs)})


def _object_info(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("object-info")
    _validate(cmd, params)
    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    return _ok(
        "object-info", gateway.object_summary(obj), fingerprint=gateway.object_fingerprint(obj)
    )


def _select(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("select")
    _validate(cmd, params)
    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    type_filter = params.get("type")
    active = params.get("active")
    data = gateway.select_objects(
        str(params["targets"]),
        regex=bool(params.get("regex", False)),
        type_filter=str(type_filter) if type_filter is not None else None,
        active=str(active) if active is not None else None,
        message="select",
    )
    # select は mutating（選択/active を変更）。methods.md の契約どおり fingerprint を返し、
    # request-status / 応答で選択ドリフトを検証できるようにする（Codex P2）。
    fp = gateway.selection_fingerprint(data["selected"], data["active"])
    return _ok("select", data, fingerprint=fp)


def _duplicate(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("duplicate")
    _validate(cmd, params)
    # count は 1..上限。暴走（巨大 count で Blender を固める）を bpy 到達前に弾く。
    # 上限は bli-core の単一定数（CLI と共有）。
    from bli_core import runtime

    count = int(params.get("count", 1))
    _require_input(
        1 <= count <= runtime.MAX_DUPLICATE_COUNT,
        symptom=f"count は 1〜{runtime.MAX_DUPLICATE_COUNT} の範囲で指定してください（指定: {count}）",
        remediation=f"--count を 1〜{runtime.MAX_DUPLICATE_COUNT} にしてください",
    )
    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    offset = params.get("offset")
    linked = bool(params.get("linked", False))
    created = gateway.duplicate_object(
        obj,
        linked=linked,
        count=count,
        offset=list(offset) if offset is not None else None,
        message="duplicate",
    )
    data = {"source": obj.name, "created": created, "count": len(created), "linked": linked}
    return _ok("duplicate", data, fingerprint=gateway.names_fingerprint(created))


def _delete(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("delete")
    _validate(cmd, params)
    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    # 削除前にサマリ/fingerprint を取得する（削除後は obj が無効化されアクセス不可）。
    # 共有 mesh でも安全（object のみ除去・データは他利用者が残れば保持）→ ガード不要。
    name = obj.name
    backup = gateway.object_summary(obj)
    fp = gateway.object_fingerprint(obj)
    gateway.delete_object(obj, message="delete")
    return _ok("delete", {"deleted": name, "backup": backup}, fingerprint=fp)


def _rename(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("rename")
    _validate(cmd, params)

    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    data = gateway.rename_object(
        obj, str(params["name"]), with_data=bool(params.get("with_data", False)), message="rename"
    )
    return _ok("rename", data, fingerprint=gateway.object_fingerprint(obj))
