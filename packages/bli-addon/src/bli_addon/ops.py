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

    # assign は既存マテリアルを **状態変更（単一ユーザ化）の前に** 解決する。見つからない名で
    # 先に mesh を単一ユーザ化してから失敗すると、エラー後にシーン状態が変わる（Codex P2）。
    # 未発見エラーは gateway.require_material に集約（require_single と同じ流儀。設計レビュー P2）。
    mat = None
    if action == "assign":  # 既存マテリアルのみ。無ければ E_TARGET_NOT_FOUND＝create と責務分離
        mat = gateway.require_material(str(name))

    # 共有 mesh ガード（Codex P2-A）。ただし書き込み先が OBJECT リンク slot のときは object
    # 限定の書き込みで共有 mesh を触らないため掛けない（false-positive な E_PRECONDITION や
    # --make-single-user による不要な分離を避ける。Codex P2）。DATA slot 書き込み・空スロット
    # append のみガード対象。マテリアル解決（上）を通過した後に実行＝失敗時に mesh を分離しない。
    if gateway.material_write_touches_mesh_data(obj):
        _guard_shared_mesh(gateway, obj, params)

    if action == "create":
        mat = gateway.create_material(str(name), list(color) if color is not None else None)

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


# type 別に有効な追加パラメータ（add 時のみ。これ以外が来たら USER_INPUT で弾く）。
_MODIFIER_TYPE_PARAMS: dict[str, set[str]] = {
    "MIRROR": {"axis"},
    "SUBSURF": {"levels"},
    "SOLIDIFY": {"thickness"},
    "DECIMATE": {"ratio"},
    "BOOLEAN": {"operation", "with_object"},
}
# 全 type 別パラメータの和集合（手書きにせず導出＝type 追加時の追従漏れを防ぐ）。
_ALL_MODIFIER_TYPE_PARAMS: set[str] = set().union(*_MODIFIER_TYPE_PARAMS.values())
# SUBSURF levels の上限（巨大値で mesh 評価が指数的に膨らみ Blender を固めるのを防ぐ）。
_MAX_SUBSURF_LEVELS = 6


def _modifier(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("modifier")
    _validate(cmd, params)
    action = str(params["action"])
    mtype = params.get("type")
    name = params.get("name")
    present_type_params = {k for k in _ALL_MODIFIER_TYPE_PARAMS if k in params}

    # 条件付き必須を bpy 到達前に検証する（schema は action/type 非依存で任意）。
    if action == "add":
        _require_input(
            mtype is not None,
            symptom="add には --type が必要です",
            remediation="--type を指定してください",
        )
        mtype = str(mtype)
        # type-param は当該 type のものだけ許可（silent ignore しない）。
        extra = present_type_params - _MODIFIER_TYPE_PARAMS[mtype]
        _require_input(
            not extra,
            symptom=f"{mtype} に無効なパラメータ: {sorted(extra)}",
            remediation=f"{mtype} で有効な追加パラメータ: {sorted(_MODIFIER_TYPE_PARAMS[mtype])}",
        )
        if mtype == "BOOLEAN":
            _require_input(
                "with_object" in params,
                symptom="BOOLEAN の add には --with（相手オブジェクト）が必要です",
                remediation="--with <object> を指定してください",
            )
        # 数値 param の範囲を bpy 到達前に弾く（暴走防止・silent クランプ回避）。
        if "levels" in params:
            _require_input(
                0 <= int(params["levels"]) <= _MAX_SUBSURF_LEVELS,
                symptom=f"levels は 0〜{_MAX_SUBSURF_LEVELS} で指定してください（指定: {params['levels']}）",
                remediation=f"--levels を 0〜{_MAX_SUBSURF_LEVELS} にしてください",
            )
        if "ratio" in params:
            _require_input(
                0.0 <= float(params["ratio"]) <= 1.0,
                symptom=f"ratio は 0.0〜1.0 で指定してください（指定: {params['ratio']}）",
                remediation="--ratio を 0.0〜1.0 にしてください",
            )
    else:
        # remove/apply/list は type 別パラメータ不可（add 専用）。
        _require_input(
            not present_type_params,
            symptom=f"{action} に type 別パラメータは使えません: {sorted(present_type_params)}",
            remediation="type 別パラメータは add のときのみ有効です",
        )
        if action in ("remove", "apply"):
            _require_input(
                name is not None,
                symptom=f"{action} には --name（対象モディファイア）が必要です",
                remediation="--name <modifier> を指定してください",
            )

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]))
    # 非対応型（EMPTY/LIGHT/CAMERA 等）を INTERNAL でなく E_PRECONDITION で弾く（material と同様）。
    gateway.require_modifier_support(obj)

    if action == "list":
        data = {"name": obj.name, "action": "list", "modifiers": gateway.list_modifiers(obj)}
        return _ok("modifier", data, fingerprint=gateway.modifiers_fingerprint(obj))

    if action == "remove":
        gateway.remove_modifier(obj, str(name), message=f"modifier remove {name}")
        data = {
            "name": obj.name,
            "action": "remove",
            "removed": str(name),
            "modifiers": gateway.list_modifiers(obj),
        }
        return _ok("modifier", data, fingerprint=gateway.modifiers_fingerprint(obj))

    if action == "apply":
        # 無効名は **共有ガード（単一ユーザ化）の前** に弾く（失敗時に mesh を分離しない）。
        gateway.require_modifier(obj, str(name))
        # apply は mesh へ焼き込む破壊的操作 → 共有 mesh は単一ユーザ化を要求（apply-transform と同様）。
        _guard_shared_mesh(gateway, obj, params)
        result = gateway.apply_modifier(obj, str(name), message=f"modifier apply {name}")
        # apply は mesh が変わる → mesh 込みの object_fingerprint で drift を示す。
        data = {"name": obj.name, "action": "apply", **result}
        return _ok("modifier", data, fingerprint=gateway.object_fingerprint(obj))

    # add（type 別 param を設定。BOOLEAN は相手を require_single で解決し型/自己参照を検証）。
    operand = None
    if mtype == "BOOLEAN":
        operand = gateway.require_single(str(params["with_object"]))
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
    summary = gateway.add_modifier(
        obj,
        str(mtype),
        name=str(name) if name is not None else None,
        axis=params.get("axis"),
        levels=params.get("levels"),
        thickness=params.get("thickness"),
        ratio=params.get("ratio"),
        operation=params.get("operation"),
        operand=operand,
        message=f"modifier add {mtype}",
    )
    data = {
        "name": obj.name,
        "action": "add",
        "modifier": summary,
        "modifiers": gateway.list_modifiers(obj),
    }
    return _ok("modifier", data, fingerprint=gateway.modifiers_fingerprint(obj))


# op 別に有効な追加パラメータ（これ以外が来たら USER_INPUT で弾く・modifier と同じ流儀）。
_MESH_OP_PARAMS: dict[str, set[str]] = {
    "recalc-normals": {"inside"},
    "merge-by-distance": {"distance"},
    "extrude": {"offset"},
    "bevel": {"width", "segments"},
    "inset": {"thickness"},
    # T7.3（heavy・modifier add+apply 経由）: boolean=演算+相手 / decimate=削減比率。
    "boolean": {"operation", "with_object"},
    "decimate": {"ratio"},
}
# 全 op 別パラメータの和集合（手書きにせず導出＝op 追加時の追従漏れを防ぐ）。
_ALL_MESH_OP_PARAMS: set[str] = set().union(*_MESH_OP_PARAMS.values())
# merge-by-distance の既定マージ距離（Blender 既定と同値・methods.md 準拠）。
_DEFAULT_MERGE_DISTANCE = 0.0001
# bevel segments の既定と上限（巨大値で edge×segments のジオメトリが膨らみ固まるのを防ぐ）。
_DEFAULT_BEVEL_SEGMENTS = 1
_MAX_BEVEL_SEGMENTS = 100


def _mesh(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("mesh")
    _validate(cmd, params)
    op = str(params["op"])
    present_op_params = {k for k in _ALL_MESH_OP_PARAMS if k in params}

    # op 専用パラメータは当該 op のものだけ許可する（silent ignore せず弾く・bpy 到達前）。
    extra = present_op_params - _MESH_OP_PARAMS[op]
    _require_input(
        not extra,
        symptom=f"{op} に無効なパラメータ: {sorted(extra)}",
        remediation=f"{op} で有効な追加パラメータ: {sorted(_MESH_OP_PARAMS[op])}",
    )
    if op == "merge-by-distance" and "distance" in params:
        # 負の距離は remove_doubles で未定義。0 以上を要求する（有限性は schema が担保）。
        _require_input(
            float(params["distance"]) >= 0.0,
            symptom=f"distance は 0 以上で指定してください（指定: {params['distance']}）",
            remediation="--distance を 0 以上にしてください",
        )
    elif op == "extrude":
        # extrude は押し出しベクトルが必須（省略すると重なり面を作る無音の no-op になる）。
        _require_input(
            "offset" in params,
            symptom="extrude には --offset（押し出しベクトル）が必要です",
            remediation="--offset x,y,z を指定してください",
        )
    elif op == "bevel":
        _require_input(
            "width" in params,
            symptom="bevel には --width が必要です",
            remediation="--width <f> を指定してください",
        )
        _require_input(
            float(params["width"]) >= 0.0,
            symptom=f"width は 0 以上で指定してください（指定: {params['width']}）",
            remediation="--width を 0 以上にしてください",
        )
        if "segments" in params:
            _require_input(
                1 <= int(params["segments"]) <= _MAX_BEVEL_SEGMENTS,
                symptom=f"segments は 1〜{_MAX_BEVEL_SEGMENTS} で指定してください（指定: {params['segments']}）",
                remediation=f"--segments を 1〜{_MAX_BEVEL_SEGMENTS} にしてください",
            )
    elif op == "inset":
        _require_input(
            "thickness" in params,
            symptom="inset には --thickness が必要です",
            remediation="--thickness <f> を指定してください",
        )
        _require_input(
            float(params["thickness"]) >= 0.0,
            symptom=f"thickness は 0 以上で指定してください（指定: {params['thickness']}）",
            remediation="--thickness を 0 以上にしてください",
        )
    elif op == "boolean":
        # operation/相手は必須（相手の実在/型は bpy 到達後に require_single で検証）。
        _require_input(
            "operation" in params,
            symptom="boolean には --operation（演算）が必要です",
            remediation="--operation UNION|DIFFERENCE|INTERSECT を指定してください",
        )
        _require_input(
            "with_object" in params,
            symptom="boolean には --with（相手オブジェクト）が必要です",
            remediation="--with <object> を指定してください",
        )
    elif op == "decimate":
        _require_input(
            "ratio" in params,
            symptom="decimate には --ratio（削減比率）が必要です",
            remediation="--ratio 0..1 を指定してください",
        )
        _require_input(
            0.0 <= float(params["ratio"]) <= 1.0,
            symptom=f"ratio は 0.0〜1.0 で指定してください（指定: {params['ratio']}）",
            remediation="--ratio を 0.0〜1.0 にしてください",
        )

    from . import bmesh_ops, gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]))
    # 非 mesh 型（EMPTY/CURVE 等）を INTERNAL でなく E_PRECONDITION で弾く（material と同様）。
    gateway.require_mesh(obj)
    # boolean の相手は **共有ガード（単一ユーザ化）の前** に解決・検証する（不正な相手で obj の
    # mesh を分離しない。modifier の BOOLEAN add と同じ流儀）。operand 自体は read-only。
    operand = None
    if op == "boolean":
        operand = gateway.require_single(str(params["with_object"]))
        _require_input(
            operand.name != obj.name,
            symptom="boolean の相手に自分自身は指定できません",
            remediation="別のオブジェクトを --with に指定してください",
        )
        _require_input(
            operand.type == "MESH",
            symptom=f"boolean の相手は mesh が必要です（--with={operand.name} type={operand.type}）",
            remediation="mesh オブジェクトを --with に指定してください",
        )
    # 破壊的（mesh データを直接書き換える）→ 共有 mesh は単一ユーザ化を要求（apply 系と同様）。
    # 全 op が obj.data を書き換える: bmesh 系は to_mesh で上書き / boolean・decimate は
    # modifier_apply で焼き込む（多ユーザ mesh への modifier_apply は Blender が拒否するため
    # 単一ユーザ化は必須）。ratio=1.0 等の実質 no-op でも mesh は焼き直されるためガードする。
    _guard_shared_mesh(gateway, obj, params)

    if op == "recalc-normals":
        result = bmesh_ops.recalc_normals(
            obj, inside=bool(params.get("inside", False)), message="mesh recalc-normals"
        )
    elif op == "merge-by-distance":
        distance = float(params["distance"]) if "distance" in params else _DEFAULT_MERGE_DISTANCE
        result = bmesh_ops.merge_by_distance(
            obj, distance=distance, message="mesh merge-by-distance"
        )
    elif op == "extrude":
        result = bmesh_ops.extrude(obj, offset=list(params["offset"]), message="mesh extrude")
    elif op == "bevel":
        segments = int(params["segments"]) if "segments" in params else _DEFAULT_BEVEL_SEGMENTS
        result = bmesh_ops.bevel(
            obj, width=float(params["width"]), segments=segments, message="mesh bevel"
        )
    elif op == "inset":
        result = bmesh_ops.inset(obj, thickness=float(params["thickness"]), message="mesh inset")
    elif op == "boolean":
        result = gateway.mesh_boolean(
            obj, operand, operation=str(params["operation"]), message="mesh boolean"
        )
    else:  # decimate
        result = gateway.mesh_decimate(obj, ratio=float(params["ratio"]), message="mesh decimate")
    # mesh が変わる → mesh 込みの mesh_fingerprint で drift を示す（recalc は頂点数不変のため
    # object_fingerprint では検出できない。法線込みの専用 fingerprint を使う。§6e）。
    data = {"name": obj.name, "op": op, **result}
    return _ok("mesh", data, fingerprint=gateway.mesh_fingerprint(obj))


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
    "modifier": _modifier,
    "mesh": _mesh,
    "set-origin": _set_origin,
}


def dispatch(method: str, params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """bpy 系は専用ハンドラ、その他は handlers.dispatch に委譲する。"""
    fn = _BPY_HANDLERS.get(method)
    if fn is not None:
        return fn(params, info)
    return handlers.dispatch(method, params, info)
