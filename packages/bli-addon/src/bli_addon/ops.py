"""ドメインハンドラ + dispatch ルータ（M3）。spec §6 / methods.md / 付録B。

bpy 系コマンド（scene-info/object-info/set-origin）を `gateway` 経由で実行する。
それ以外（ping/echo 等）は `handlers.dispatch` に委譲する。

- param 検証はサーバ側でも行う（`bli_core.schema.validate_from_dict` → INVALID_PARAMS）。
- required_mode を実行直前に検証する（自動遷移はしない → E_MODE_MISMATCH）。
- `gateway`/`bpy` は **遅延 import**（pytest では bpy が無いため、検証パスだけ到達可能）。
"""

from __future__ import annotations

from collections.abc import Callable
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

from . import handlers
from .handlers import ServerInfo

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


def _check_mode(cmd: Command, current: str) -> None:
    """required_mode を検証する。不一致は自動遷移せず E_MODE_MISMATCH。"""
    req = cmd.required_mode
    if req is Mode.ANY:
        return
    ok = (req is Mode.OBJECT and current == "OBJECT") or (
        req is Mode.EDIT and current.startswith("EDIT")
    )
    if not ok:
        raise JsonRpcError(
            RPC_BUSINESS_ERROR,
            ErrorCode.E_MODE_MISMATCH,
            make_error(
                ErrorCode.E_MODE_MISMATCH,
                category=ErrorCategory.PRECONDITION,
                retryable=False,
                symptom=f"必要モード {req.value}（現在 {current}）",
                remediation=f"{req.value} モードに切り替えてください（自動遷移はしません）",
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


# ---- ハンドラ（bpy 系）----


def _scene_info(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("scene-info")
    _validate(cmd, params)
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    data = gateway.scene_summary(int(params.get("depth", 1)))
    return _ok_offload("scene-info", data, "scene-info/v1")


def _list_objects(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("list-objects")
    _validate(cmd, params)
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    type_filter = params.get("type")
    regex = params.get("regex")
    objs = gateway.list_objects(
        str(type_filter) if type_filter is not None else None,
        str(regex) if regex is not None else None,
    )
    return _ok("list-objects", {"objects": objs, "count": len(objs)})


def _object_info(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("object-info")
    _validate(cmd, params)
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]))
    return _ok(
        "object-info", gateway.object_summary(obj), fingerprint=gateway.object_fingerprint(obj)
    )


def _select(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("select")
    _validate(cmd, params)
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    type_filter = params.get("type")
    active = params.get("active")
    data = gateway.select_objects(
        str(params["targets"]),
        type_filter=str(type_filter) if type_filter is not None else None,
        active=str(active) if active is not None else None,
        message="select",
    )
    # select は mutating（選択/active を変更）。methods.md の契約どおり fingerprint を返し、
    # request-status / 応答で選択ドリフトを検証できるようにする（Codex P2）。
    fp = gateway.selection_fingerprint(data["selected"], data["active"])
    return _ok("select", data, fingerprint=fp)


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


def _transform(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("transform")
    _validate(cmd, params)
    # 変更チャンネル皆無は無音 no-op + 空 undo になるため弾く（apply-transform と整合）。
    _require_input(
        any(k in params for k in ("location", "rotation", "scale")),
        symptom="transform に変更するチャンネルがありません",
        remediation="--location/--rotation/--scale のいずれかを指定してください",
    )
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]))
    mode = str(params.get("mode", "set"))
    data = gateway.transform_object(
        obj,
        location=params.get("location"),
        rotation=params.get("rotation"),
        scale=params.get("scale"),
        mode=mode,
        message=f"transform {mode}",
    )
    return _ok("transform", data, fingerprint=gateway.object_fingerprint(obj))


def _apply_transform(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("apply-transform")
    _validate(cmd, params)

    # チャンネルは「キーの有無」で判定する（明示 false と省略を区別。Codex P2）。
    # 全キー省略 = 全チャンネル適用（利便）。明示指定があればその真偽値を尊重する。
    # 生成クライアントが既定 false を埋めても、意図せず全適用にならないようにする。
    keys = ("location", "rotation", "scale")
    if not any(k in params for k in keys):
        loc = rot = scl = True
    else:
        loc = bool(params.get("location", False))
        rot = bool(params.get("rotation", False))
        scl = bool(params.get("scale", False))
        # 明示的に全 false = 適用対象なし
        _require_input(
            loc or rot or scl,
            symptom="apply-transform に適用するチャンネルがありません（全 false）",
            remediation="--location/--rotation/--scale のいずれかを指定（全省略で全適用）",
        )

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]))
    # 破壊的（mesh データへ焼き込む）。共有 mesh は set-origin と同様にガードする。
    _guard_shared_mesh(gateway, obj, params)
    data = gateway.apply_transform(
        obj, location=loc, rotation=rot, scale=scl, message="apply-transform"
    )
    return _ok("apply-transform", data, fingerprint=gateway.object_fingerprint(obj))


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
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]))
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
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]))
    # 削除前にサマリ/fingerprint を取得する（削除後は obj が無効化されアクセス不可）。
    # 共有 mesh でも安全（object のみ除去・データは他利用者が残れば保持）→ ガード不要。
    name = obj.name
    backup = gateway.object_summary(obj)
    fp = gateway.object_fingerprint(obj)
    gateway.delete_object(obj, message="delete")
    return _ok("delete", {"deleted": name, "backup": backup}, fingerprint=fp)


def _material(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("material")
    _validate(cmd, params)
    action = str(params["action"])
    targets = params.get("targets")
    name = params.get("name")
    color = params.get("color")

    # 条件付き必須を bpy 到達前に検証する（schema は action 非依存で targets/name 任意）。
    _require_input(
        targets is not None,
        symptom="対象(--targets)が必要です",
        remediation="--targets を指定してください",
    )
    if action in ("assign", "create"):
        _require_input(
            name is not None,
            symptom=f"{action} には --name が必要です",
            remediation="--name を指定してください",
        )
    # color は create 専用（assign/list で渡されたら silent ignore せず弾く）。
    _require_input(
        action == "create" or color is None,
        symptom="--color は create のときのみ有効です",
        remediation="create で使うか --color を外してください",
    )

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(targets))
    gateway.require_material_support(obj)

    if action == "list":
        data = {"name": obj.name, "action": "list", "materials": gateway.list_object_materials(obj)}
        return _ok("material", data, fingerprint=gateway.material_fingerprint(obj))

    # assign/create は DATA slot を書き換えるため、共有 mesh は set-origin/apply-transform と
    # 同様に単一ユーザ化を要求する（--make-single-user 無しは E_PRECONDITION。Codex P2-A）。
    _guard_shared_mesh(gateway, obj, params)

    if action == "create":
        mat = gateway.create_material(str(name), list(color) if color is not None else None)
    else:  # assign（既存マテリアルのみ。無ければ E_TARGET_NOT_FOUND＝create と責務分離）
        mat = gateway.find_material(str(name))
        if mat is None:
            raise JsonRpcError(
                RPC_BUSINESS_ERROR,
                ErrorCode.E_TARGET_NOT_FOUND,
                make_error(
                    ErrorCode.E_TARGET_NOT_FOUND,
                    category=ErrorCategory.USER_INPUT,
                    retryable=False,
                    symptom=f"マテリアルが見つかりません: {name}",
                    remediation="既存のマテリアル名を指定するか create で作成してください",
                ),
            )

    slot = gateway.assign_material(obj, mat)
    gateway.push_undo(f"material {action}")
    data = {
        "name": obj.name,
        "action": action,
        "material": mat.name,
        "slot": slot,
        "materials": gateway.list_object_materials(obj),
    }
    return _ok("material", data, fingerprint=gateway.material_fingerprint(obj))


def _set_origin(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("set-origin")
    _validate(cmd, params)
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]))
    to = str(params["to"])

    # 共有 mesh は明示許可（make_single_user）が無い限り拒否する。
    _guard_shared_mesh(gateway, obj, params)

    if to == "geometry":
        center = "BOUNDS" if params.get("center") == "bounds" else "MEDIAN"
        gateway.origin_set(
            obj, origin_type="ORIGIN_GEOMETRY", center=center, message="set-origin geometry"
        )
    elif to == "cursor":
        gateway.origin_set(obj, origin_type="ORIGIN_CURSOR", message="set-origin cursor")
    else:  # world（直接行列）
        x = float(params.get("x") or 0.0)
        y = float(params.get("y") or 0.0)
        z = float(params.get("z") or 0.0)
        gateway.set_origin_world(obj, x, y, z)
        gateway.push_undo("set-origin world")

    data = {
        "name": obj.name,
        "to": to,
        "origin_world": gateway.object_summary(obj)["location"],
    }
    return _ok("set-origin", data, fingerprint=gateway.object_fingerprint(obj))


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
    "set-origin": _set_origin,
}


def dispatch(method: str, params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """bpy 系は専用ハンドラ、その他は handlers.dispatch に委譲する。"""
    fn = _BPY_HANDLERS.get(method)
    if fn is not None:
        return fn(params, info)
    return handlers.dispatch(method, params, info)
