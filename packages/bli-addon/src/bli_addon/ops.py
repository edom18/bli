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
    name_regex = params.get("name_regex")
    objs = gateway.list_objects(
        str(type_filter) if type_filter is not None else None,
        str(name_regex) if name_regex is not None else None,
    )
    return _ok("list-objects", {"objects": objs, "count": len(objs)})


def _object_info(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("object-info")
    _validate(cmd, params)
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
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
        regex=bool(params.get("regex", False)),
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


def _is_nonzero_vec(vec: Any) -> bool:
    """ベクトルが（ほぼ）ゼロでないか（純Python・bpy 到達前のゼロベクトル弾き用）。

    schema 検証済みで vec は有限値の3要素。正規化が不定になるゼロ近傍を弾く（align-vector）。
    """
    return sum(float(c) * float(c) for c in vec) > 1e-12


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
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
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
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
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
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    # 削除前にサマリ/fingerprint を取得する（削除後は obj が無効化されアクセス不可）。
    # 共有 mesh でも安全（object のみ除去・データは他利用者が残れば保持）→ ガード不要。
    name = obj.name
    backup = gateway.object_summary(obj)
    fp = gateway.object_fingerprint(obj)
    gateway.delete_object(obj, message="delete")
    return _ok("delete", {"deleted": name, "backup": backup}, fingerprint=fp)


# material create 専用の presence-sensitive パラメータ（P2-3 で PBR/テクスチャへ拡張・G5）。
# 他 action で渡されたら silent ignore せず弾く（color の従来ガードの一般化）。
_MATERIAL_CREATE_ONLY_PARAMS: tuple[str, ...] = (
    "color",
    "metallic",
    "roughness",
    "emission",
    "emission_strength",
    "alpha",
    "texture",
    "pack_texture",
)


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
    # create 専用 param（color/PBR/テクスチャ）は他 action で渡されたら弾く。
    given_create_only = [k for k in _MATERIAL_CREATE_ONLY_PARAMS if params.get(k) is not None]
    _require_input(
        action == "create" or not given_create_only,
        symptom=(
            " / ".join("--" + k.replace("_", "-") for k in given_create_only)
            + " は create のときのみ有効です"
        ),
        remediation="create で使うか該当オプションを外してください",
    )
    # 数値範囲と依存関係を bpy 到達前に検証する（rna の silent clamp に頼らない・§6e）。
    for key in ("metallic", "roughness", "alpha"):
        if params.get(key) is not None:
            _require_input(
                0.0 <= float(params[key]) <= 1.0,
                symptom=f"--{key} は 0.0〜1.0 で指定してください（指定: {params[key]}）",
                remediation=f"--{key} を 0.0〜1.0 にしてください",
            )
    if params.get("emission_strength") is not None:
        _require_input(
            params.get("emission") is not None,
            symptom="--emission-strength は --emission と併用してください",
            remediation="--emission r,g,b,a も指定してください",
        )
        _require_input(
            float(params["emission_strength"]) >= 0.0,
            symptom=f"--emission-strength は 0 以上で指定してください（指定: {params['emission_strength']}）",
            remediation="--emission-strength を 0 以上にしてください",
        )
    if params.get("pack_texture") is not None:
        _require_input(
            params.get("texture") is not None,
            symptom="--pack-texture は --texture と併用してください",
            remediation="--texture <path> も指定してください",
        )
    texture = params.get("texture")
    if texture is not None:
        import os

        # CLI/Blender の CWD 差を吸収するため abspath 正規化（import と同じ流儀）。
        texture = os.path.abspath(str(texture))
        _require_input(
            os.path.isfile(texture),
            symptom=f"テクスチャ画像が見つかりません: {texture}",
            remediation="存在する画像ファイルのパスを指定してください",
        )

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(targets), regex=bool(params.get("regex", False)))
    gateway.require_material_support(obj)

    if action == "list":
        data = {"name": obj.name, "action": "list", "materials": gateway.list_object_materials(obj)}
        return _ok("material", data, fingerprint=gateway.material_fingerprint(obj))

    # assign は既存マテリアルを **状態変更（単一ユーザ化）の前に** 解決する。見つからない名で
    # 先に mesh を単一ユーザ化してから失敗すると、エラー後にシーン状態が変わる（Codex P2）。
    # 未発見エラーは gateway.require_material に集約（require_single と同じ流儀。設計レビュー P2）。
    #
    # 共有 mesh ガード（Codex P2-A）は書き込み先が DATA slot / 空スロット append のときのみ
    # （OBJECT リンク slot は object 限定書き込みで共有 mesh を触らない＝false-positive 回避）。
    # ガード（--make-single-user 時は不可逆な単一ユーザ化）は **失敗し得る処理を全て通過した後**
    # に実行する＝失敗時に mesh を分離しない: assign はマテリアル解決後 / create は P2-3 で
    # texture・pack・Principled 欠如により失敗し得るため create_material 成功後（レビュー R2-A）。
    mat = None
    extras: dict[str, Any] = {}
    if action == "assign":  # 既存マテリアルのみ。無ければ E_TARGET_NOT_FOUND＝create と責務分離
        mat = gateway.require_material(str(name))
        if gateway.material_write_touches_mesh_data(obj):
            _guard_shared_mesh(gateway, obj, params)
    else:  # create
        mat, extras = gateway.create_material(
            str(name),
            list(color) if color is not None else None,
            metallic=float(params["metallic"]) if params.get("metallic") is not None else None,
            roughness=float(params["roughness"]) if params.get("roughness") is not None else None,
            emission=list(params["emission"]) if params.get("emission") is not None else None,
            emission_strength=(
                float(params["emission_strength"])
                if params.get("emission_strength") is not None
                else None
            ),
            alpha=float(params["alpha"]) if params.get("alpha") is not None else None,
            texture_path=texture,
            pack_texture=bool(params.get("pack_texture", False)),
        )
        try:
            if gateway.material_write_touches_mesh_data(obj):
                _guard_shared_mesh(gateway, obj, params)
        except JsonRpcError:
            # ガード失敗（--make-single-user なしの共有 mesh 等）で作りたて material/image を
            # 残さない（create_material 内のアトミック撤去と対称・レビュー R2-A）。
            gateway.discard_created_material(mat, extras)
            raise

    slot = gateway.assign_material(obj, mat)
    gateway.push_undo(f"material {action}")
    data = {
        "name": obj.name,
        "action": action,
        "material": mat.name,
        "slot": slot,
        "materials": gateway.list_object_materials(obj),
        **extras,
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

    # --props（任意プロパティの JSON・P2-3 G4）は bpy 到達前に parse/形状検証する。
    props: dict[str, Any] | None = None
    if params.get("props") is not None:
        import json

        _require_input(
            action == "add",
            symptom="--props は add のときのみ有効です",
            remediation="add で使うか --props を外してください",
        )
        try:
            parsed: Any = json.loads(str(params["props"]))
        except json.JSONDecodeError as e:
            parsed = None
            _require_input(
                False,
                symptom=f"--props の JSON が不正です: {e}",
                remediation="オブジェクト形式で指定してください（例: --props '{\"width\":0.1}'）",
            )
        _require_input(
            isinstance(parsed, dict) and len(parsed) > 0,
            symptom="--props は空でないオブジェクト（key:value）の JSON で指定してください",
            remediation='例: --props \'{"width":0.1,"segments":2}\'',
        )
        props = parsed

    # 条件付き必須を bpy 到達前に検証する（schema は action/type 非依存で任意）。
    if action == "add":
        _require_input(
            mtype is not None,
            symptom="add には --type が必要です",
            remediation="--type を指定してください",
        )
        mtype = str(mtype)
        # type-param（専用フラグ）は 5 種の当該 type のものだけ許可（silent ignore しない）。
        # 任意 type（P2-3）に専用フラグは無い＝ --props を案内する。
        allowed = _MODIFIER_TYPE_PARAMS.get(mtype, set())
        extra = present_type_params - allowed
        if mtype in _MODIFIER_TYPE_PARAMS:
            _require_input(
                not extra,
                symptom=f"{mtype} に無効なパラメータ: {sorted(extra)}",
                remediation=f"{mtype} で有効な追加パラメータ: {sorted(allowed)}",
            )
        else:
            _require_input(
                not extra,
                symptom=f"{mtype} に専用フラグはありません: {sorted(extra)}",
                remediation="任意 type のプロパティは --props '<JSON>' で指定してください",
            )
        # 専用フラグと --props の併用は曖昧（同じプロパティを二重指定し得る）ため弾く。
        _require_input(
            props is None or not present_type_params,
            symptom="--props と type 別の専用フラグは併用できません",
            remediation="どちらか一方で指定してください",
        )
        if mtype == "BOOLEAN":
            # 相手オブジェクトは --with（専用フラグ経路）か props.object（--props 経路）で必須。
            # object なしの BOOLEAN は不活性 modifier が無言で出来るだけ＝silent に許さない
            # （レビュー R1-1）。
            if props is None:
                _require_input(
                    "with_object" in params,
                    symptom="BOOLEAN の add には --with（相手オブジェクト）が必要です",
                    remediation='--with <object> を指定してください（--props \'{"object":"名前"}\' でも可）',
                )
            else:
                _require_input(
                    isinstance(props.get("object"), str),
                    symptom="BOOLEAN の add には相手オブジェクトが必要です（props.object）",
                    remediation='--props \'{"object":"名前"}\' で相手 mesh を指定してください',
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
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
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

    # add（type 別 param または --props を設定。BOOLEAN は相手を解決し型/自己参照を検証＝mesh
    # boolean と共有）。type の実在は rna enum から能力検出（P2-3・無効は有効一覧つき USER_INPUT）。
    gateway.require_modifier_type(str(mtype))
    operand = None
    if mtype == "BOOLEAN" and "with_object" in params:
        operand = _resolve_boolean_operand(gateway, obj, params["with_object"])
    if mtype == "BOOLEAN" and props is not None:
        # props.object も --with と**同一の検証**（自己参照禁止・mesh 限定）を通す（レビュー
        # R1-1・_resolve_boolean_operand の「二重定義で条件がドリフトするのを防ぐ」趣旨を
        # props 経路にも適用）。実際の設定は set_modifier_props の POINTER 解決が行う（同名→同 obj）。
        _resolve_boolean_operand(gateway, obj, props["object"])
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
        props=props,
        message=f"modifier add {mtype}",
    )
    data = {
        "name": obj.name,
        "action": "add",
        "modifier": summary,
        "modifiers": gateway.list_modifiers(obj),
    }
    return _ok("modifier", data, fingerprint=gateway.modifiers_fingerprint(obj))


# ---- シーングラフ生成 / モード切替 / 改名 / 親子 / コレクション（P1-2）----


def _add(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("add")
    _validate(cmd, params)
    add_type = str(params["type"])
    light_type = params.get("light_type")
    # light_type は type=light 専用（presence-sensitive・_MODIFIER_TYPE_PARAMS と同じ流儀）。
    _require_input(
        add_type == "light" or light_type is None,
        symptom="--light-type は --type light のときのみ有効です",
        remediation="--type light で使うか --light-type を外してください",
    )

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    data = gateway.add_object(
        add_type,
        name=params.get("name"),
        location=params.get("location"),
        rotation=params.get("rotation"),
        scale=params.get("scale"),
        light_type=str(light_type) if light_type is not None else None,
        message=f"add {add_type}",
    )
    return _ok("add", data, fingerprint=gateway.state_fingerprint(data))


def _mode(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("mode")
    _validate(cmd, params)
    to = str(params["to"])

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    targets = params.get("targets")
    data = gateway.set_object_mode(
        to,
        targets=str(targets) if targets is not None else None,
        regex=bool(params.get("regex", False)),
        message=f"mode {to}",
    )
    return _ok("mode", data, fingerprint=gateway.state_fingerprint(data))


def _rename(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("rename")
    _validate(cmd, params)

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    data = gateway.rename_object(
        obj, str(params["name"]), with_data=bool(params.get("with_data", False)), message="rename"
    )
    return _ok("rename", data, fingerprint=gateway.object_fingerprint(obj))


def _parent(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("parent")
    _validate(cmd, params)
    to = params.get("to")
    clear = bool(params.get("clear", False))
    # --to と --clear は排他でどちらか一方が必須（bpy 到達前に検証）。
    _require_input(
        (to is not None) != clear,
        symptom="--to（親にするオブジェクト）と --clear はどちらか一方が必要です",
        remediation="--to <object> または --clear のどちらか一方を指定してください",
    )

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    children = gateway.require_targets(
        str(params["targets"]), regex=bool(params.get("regex", False))
    )
    keep_transform = bool(params.get("keep_transform", True))

    if clear:
        results = gateway.parent_clear(
            children, keep_transform=keep_transform, message="parent clear"
        )
        action = "clear"
    else:
        parent_obj = gateway.require_single(str(to))
        results = gateway.parent_set(
            children, parent_obj, keep_transform=keep_transform, message="parent set"
        )
        action = "set"

    data = {"action": action, "results": results}
    return _ok("parent", data, fingerprint=gateway.parent_fingerprint(results))


def _collection(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("collection")
    _validate(cmd, params)
    action = str(params["action"])
    name = params.get("name")
    targets = params.get("targets")

    # name は list 以外で必須・targets は move/link/unlink でのみ有効（presence-sensitive・
    # material/modifier の action 別検証と同じ流儀）。
    _require_input(
        action == "list" or name is not None,
        symptom=f"{action} には --name（collection 名）が必要です",
        remediation="--name を指定してください",
    )
    if action in ("move", "link", "unlink"):
        _require_input(
            targets is not None,
            symptom=f"{action} には --targets（対象オブジェクト）が必要です",
            remediation="--targets を指定してください",
        )
    else:
        _require_input(
            targets is None,
            symptom=f"--targets は move/link/unlink のときのみ有効です（action={action}）",
            remediation="--targets を外すか move/link/unlink を使ってください",
        )

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())

    if action == "list":
        data = {"action": "list", "collections": gateway.list_collections()}
        return _ok("collection", data, fingerprint=gateway.collections_fingerprint())

    if action == "create":
        result = gateway.create_collection(str(name), message="collection create")
        data = {"action": "create", **result}
        return _ok("collection", data, fingerprint=gateway.collections_fingerprint())

    collection = gateway.require_collection(str(name))
    children = gateway.require_targets(str(targets), regex=bool(params.get("regex", False)))
    if action == "move":
        results = gateway.move_to_collection(children, collection, message="collection move")
    elif action == "link":
        results = gateway.link_to_collection(children, collection, message="collection link")
    else:  # unlink
        results = gateway.unlink_from_collection(children, collection, message="collection unlink")

    data = {"action": action, "collection": collection.name, "results": results}
    return _ok("collection", data, fingerprint=gateway.collections_fingerprint())


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
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    # 非 mesh 型（EMPTY/CURVE 等）を INTERNAL でなく E_PRECONDITION で弾く（material と同様）。
    gateway.require_mesh(obj)
    # boolean の相手は **共有ガード（単一ユーザ化）の前** に解決・検証する（不正な相手で obj の
    # mesh を分離しない。modifier の BOOLEAN add と同じ共有ヘルパ）。operand 自体は read-only。
    operand = None
    if op == "boolean":
        operand = _resolve_boolean_operand(gateway, obj, params["with_object"])
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
    elif op == "decimate":
        result = gateway.mesh_decimate(obj, ratio=float(params["ratio"]), message="mesh decimate")
    else:  # op は ENUM 検証済みのため到達不能。新 op の実行分岐漏れを早期検出する防御。
        raise JsonRpcError(RPC_METHOD_NOT_FOUND, f"mesh op の実行分岐がありません: {op}")
    # mesh が変わる → mesh 込みの mesh_fingerprint で drift を示す（recalc は頂点数不変のため
    # object_fingerprint では検出できない。法線込みの専用 fingerprint を使う。§6e）。
    data = {"name": obj.name, "op": op, **result}
    return _ok("mesh", data, fingerprint=gateway.mesh_fingerprint(obj))


def _set_origin(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("set-origin")
    _validate(cmd, params)
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
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


def _straighten(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("straighten")
    _validate(cmd, params)
    method = str(params["method"])
    # --- op 専用 param の presence ガード（別 method に渡されたら silent ignore せず弾く・§6e）---
    # axis は world-align/reference（合わせる local 軸）と angle（回転 world 軸）で有効。
    if "axis" in params:
        _require_input(
            method in ("world-align", "reference", "angle"),
            symptom="--axis は world-align / reference / angle のときのみ有効です",
            remediation="該当 method で使うか --axis を外してください",
        )
    # up_hint は pca 専用（符号決定の切替）。
    if "up_hint" in params:
        _require_input(
            method == "pca",
            symptom="--up-hint は pca のときのみ有効です",
            remediation="pca で使うか --up-hint を外してください",
        )
    # degrees は angle 専用。
    if "degrees" in params:
        _require_input(
            method == "angle",
            symptom="--degrees は angle のときのみ有効です",
            remediation="angle で使うか --degrees を外してください",
        )
    # from_dir/to_dir は align-vector 専用。
    for key in ("from_dir", "to_dir"):
        if key in params:
            _require_input(
                method == "align-vector",
                symptom=f"--{key.replace('_', '-')} は align-vector のときのみ有効です",
                remediation=f"align-vector で使うか --{key.replace('_', '-')} を外してください",
            )
    # reference/ref_axis は reference 専用。
    for key in ("reference", "ref_axis"):
        if key in params:
            _require_input(
                method == "reference",
                symptom=f"--{key.replace('_', '-')} は reference のときのみ有効です",
                remediation=f"reference で使うか --{key.replace('_', '-')} を外してください",
            )

    # --- method 別の必須 param（schema は method 非依存なのでここで検証・bpy 到達前）---
    if method == "angle":
        _require_input(
            "axis" in params and "degrees" in params,
            symptom="angle には --axis（回転軸）と --degrees（角度）が必要です",
            remediation="--axis X|Y|Z と --degrees を指定してください",
        )
    elif method == "align-vector":
        _require_input(
            "from_dir" in params,
            symptom="align-vector には --from-dir（揃えたい現在の方向）が必要です",
            remediation="--from-dir x,y,z を指定してください（--to-dir 省略時は up へ）",
        )
        # ゼロベクトルは normalized が不定で整列を決定できない（bpy 到達前に弾く）。
        _require_input(
            _is_nonzero_vec(params["from_dir"]),
            symptom="--from-dir がゼロベクトルです",
            remediation="長さのある方向ベクトルを指定してください",
        )
        if "to_dir" in params:
            _require_input(
                _is_nonzero_vec(params["to_dir"]),
                symptom="--to-dir がゼロベクトルです",
                remediation="長さのある方向ベクトルを指定してください（省略時は up）",
            )
    elif method == "reference":
        _require_input(
            "reference" in params,
            symptom="reference には --reference（基準オブジェクト名）が必要です",
            remediation="--reference <obj> を指定してください",
        )

    dry = bool(params.get("dry_run", False))
    bake = bool(params.get("bake_rotation", False))
    # dry-run（何も書き込まない）と bake（mesh 焼き込み）は矛盾。silent ignore せず弾く（§6e・
    # axis/up_hint と同流儀）。以降 bake が真なら dry は偽が保証される。
    _require_input(
        not (dry and bake),
        symptom="--dry-run と --bake-rotation は同時指定できません",
        remediation="計画確認は --dry-run のみ、焼き込みは --bake-rotation のみで実行してください",
    )

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    # method 別の前提（非対応型は INTERNAL でなく E_PRECONDITION）。
    reference_obj = None
    if method == "pca":
        gateway.require_mesh(obj)  # 頂点分布が必要
    elif method == "floor":
        gateway.require_geometry(obj)  # bbox が必要
    elif method == "reference":
        # 基準オブジェクトを解決（任意の型でよい・matrix_world だけ使う）。自己参照は弾く。
        reference_obj = gateway.require_single(str(params["reference"]))
        _require_input(
            reference_obj.name != obj.name,
            symptom="--reference に対象自身は指定できません",
            remediation="対象とは別のオブジェクトを --reference に指定してください",
        )

    if bake:  # dry と排他済み（上で弾く）→ bake は常に実適用
        # bake は回転を mesh データへ焼き込む破壊的操作。焼き込み先（mesh）と共有 mesh ガードを
        # **補正（obj 回転）より前**に検証する（失敗時に obj を回転させたまま残さない・§6e）。
        gateway.require_mesh(obj)
        _guard_shared_mesh(gateway, obj, params)

    data = gateway.straighten_object(
        obj,
        method=method,
        up_axis=str(params.get("up_axis", "+Z")),
        axis=str(params["axis"]) if "axis" in params else None,
        up_hint=str(params.get("up_hint", "auto")),
        degrees=float(params["degrees"]) if "degrees" in params else None,
        from_dir=params.get("from_dir"),
        to_dir=params.get("to_dir"),
        reference_obj=reference_obj,
        # ref_axis 省略時は up_axis（参照側の「up」軸方向へ合わせるのが既定）。
        ref_axis=str(params.get("ref_axis", params.get("up_axis", "+Z"))),
        # dry-run は push_undo しない・bake も apply の undo に委ねるため、どちらも message なし。
        message=None if (bake or dry) else f"straighten {method}",
        dry_run=dry,
    )
    if bake:
        # 回転を mesh へ焼き込む（apply-transform rotation 経路を再利用）。焼き込み後は object
        # 回転が 0 になり頂点が回転する。共有ガードは上で実施済み（undo 境界は apply が作る）。
        baked = gateway.apply_transform(
            obj, location=False, rotation=True, scale=False, message=f"straighten {method} bake"
        )
        data["baked"] = True
        data["rotation_euler_deg"] = baked["rotation_euler_deg"]
    else:
        data["baked"] = False
    # fingerprint は操作の本質に合わせる（§6e）。bake は回転を mesh データへ焼き込む（頂点座標が
    # 変わる）→ 法線込みの mesh_fingerprint で頂点数不変でも幾何変化を検出する（mesh 編集系と一貫・
    # require_mesh 通過後で MESH 限定が保証される）。非 bake / dry-run は object transform のみ
    # （dry-run は復元済みで不変）→ bbox 込みの object_fingerprint（set-origin/transform と同流儀）。
    fp = gateway.mesh_fingerprint(obj) if bake else gateway.object_fingerprint(obj)
    return _ok("straighten", data, fingerprint=fp)


def _print_setup(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("print-setup")
    _validate(cmd, params)
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    unit = str(params.get("unit", "mm"))  # SSOT default は mm（非 CLI RPC の省略も許容）
    scene_name = params.get("scene")
    # 表示単位のみ設定（geometry 非破壊・研究 §E5）→ 共有 mesh ガード不要。
    data = gateway.set_print_units(
        unit,
        scene_name=str(scene_name) if scene_name is not None else None,
        message="print-setup",
    )
    return _ok(
        "print-setup", data, fingerprint=gateway.unit_settings_fingerprint(data["unit_settings"])
    )


# print-check の bmesh カテゴリ -> 報告キー（カテゴリ flag 指定時の出力サブセット）。
_BMESH_CHECK_CATEGORIES: dict[str, tuple[str, ...]] = {
    "manifold": (
        "non_manifold_edges",
        "boundary_edges",
        "wire_edges",
        "loose_verts",
        "is_manifold",
    ),
    "normals": ("flipped_normals", "normals_consistent"),
    "degenerate": ("degenerate_faces",),
}


def _print_check(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("print-check")
    _validate(cmd, params)
    # min_thickness は thin 専用（他で渡されたら silent ignore せず弾く・bpy 到達前）。
    if "min_thickness" in params:
        _require_input(
            bool(params.get("thin", False)),
            symptom="--min-thickness は --thin のときのみ有効です",
            remediation="--thin と一緒に使ってください",
        )

    from . import bmesh_ops, gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    gateway.require_mesh(obj)
    # thin（薄壁）/ intersect（自己交差）は print3d 依存。要求 かつ 未導入なら CAPABILITY_UNAVAILABLE
    # で縮退する（§E6・この環境では print3d 実体なし）。manifold/normals/degenerate は bmesh 自前で常時可。
    wants_print3d = bool(params.get("thin", False)) or bool(params.get("intersect", False))
    if wants_print3d and not gateway.print3d_available():
        raise JsonRpcError(
            RPC_BUSINESS_ERROR,
            ErrorCode.CAPABILITY_UNAVAILABLE,
            make_error(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                category=ErrorCategory.ENVIRONMENT,
                retryable=False,
                symptom="薄壁/自己交差チェックには print3d Toolbox が必要ですが利用できません",
                remediation="print3d Toolbox（Extensions）を導入するか、--manifold/--normals/--degenerate を使ってください",
            ),
        )
    # bmesh カテゴリは presence-sensitive（省略時は3種すべて）。1パスで全計算し要求分のみ報告する。
    cats = [c for c in ("manifold", "normals", "degenerate") if bool(params.get(c, False))] or [
        "manifold",
        "normals",
        "degenerate",
    ]
    full = bmesh_ops.mesh_check(obj)
    checks = {k: full[k] for cat in cats for k in _BMESH_CHECK_CATEGORIES[cat]}
    checks["is_printable"] = full["is_printable"]  # 致命カテゴリ全 0 の要約は常時付与
    data = {"name": obj.name, "checked": sorted(cats), "checks": checks}
    # 読み取り専用だが mesh_fingerprint を返し「どの mesh 状態を検査したか」を確定（M5 退避も再利用）。
    return _ok_offload(
        "print-check", data, "print-check/v1", fingerprint=gateway.mesh_fingerprint(obj)
    )


def _print_repair(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("print-repair")
    _validate(cmd, params)
    # presence-sensitive: 全省略 = 全修復（apply-transform と同流儀）。明示時はその真偽を尊重。
    keys = ("make_manifold", "recalc_normals", "remove_degenerate")
    if not any(k in params for k in keys):
        make_manifold = recalc_normals = remove_degenerate = True
    else:
        make_manifold = bool(params.get("make_manifold", False))
        recalc_normals = bool(params.get("recalc_normals", False))
        remove_degenerate = bool(params.get("remove_degenerate", False))
        _require_input(
            make_manifold or recalc_normals or remove_degenerate,
            symptom="適用する修復がありません（全 false）",
            remediation="--make-manifold/--recalc-normals/--remove-degenerate のいずれか（全省略で全修復）",
        )

    from . import bmesh_ops, gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    gateway.require_mesh(obj)
    # mesh データを書き換える破壊的操作 → 共有 mesh は単一ユーザ化を要求（apply 系と同様）。
    _guard_shared_mesh(gateway, obj, params)
    result = bmesh_ops.mesh_repair(
        obj,
        make_manifold=make_manifold,
        recalc_normals=recalc_normals,
        remove_degenerate=remove_degenerate,
        message="print-repair",
    )
    return _ok(
        "print-repair", {"name": obj.name, **result}, fingerprint=gateway.mesh_fingerprint(obj)
    )


# ---- 逃げ道: exec-python（M11・既定 off・サンドボックスなし）----


def _exec_error(message: str, *, phase: str, cause: str = "") -> JsonRpcError:
    """ユーザコードの例外を EXEC_ERROR へ写像する（INTERNAL 化しない・研究 §E14）。

    compile フェーズ（SyntaxError 等）はユーザコードの不備＝USER_INPUT、runtime は ENVIRONMENT。
    """
    category = ErrorCategory.USER_INPUT if phase == "compile" else ErrorCategory.ENVIRONMENT
    return JsonRpcError(
        RPC_BUSINESS_ERROR,
        ErrorCode.EXEC_ERROR,
        make_error(
            ErrorCode.EXEC_ERROR,
            category=category,
            retryable=False,
            symptom=message,
            remediation="コードを修正して再実行してください",
            cause=cause,
        ),
    )


def _exec_disabled(symptom: str, remediation: str) -> JsonRpcError:
    """exec が無効（off / audited で許可リスト外）のときの EXEC_DISABLED（PRECONDITION・retryable=False）。"""
    return JsonRpcError(
        RPC_BUSINESS_ERROR,
        ErrorCode.EXEC_DISABLED,
        make_error(
            ErrorCode.EXEC_DISABLED,
            category=ErrorCategory.PRECONDITION,
            retryable=False,
            symptom=symptom,
            remediation=remediation,
        ),
    )


def _exec_blocked_restricted(blocked: list[str], remediation: str) -> JsonRpcError:
    """restricted のブロックリスト検出（EXEC_BLOCKED_RESTRICTED・PRECONDITION・retryable=False）。

    「何がブロックされたか」を症状文へ列挙する（scan_blocked の理由は `import:subprocess` 等の
    自己記述形式）。修正して再実行すれば通るコードはコード修正が本筋なので、trusted 昇格の案内は
    remediation 側に置く（P1-1・設計レビュー G0）。
    """
    return JsonRpcError(
        RPC_BUSINESS_ERROR,
        ErrorCode.EXEC_BLOCKED_RESTRICTED,
        make_error(
            ErrorCode.EXEC_BLOCKED_RESTRICTED,
            category=ErrorCategory.PRECONDITION,
            retryable=False,
            symptom=f"exec mode=restricted: ブロック対象を検出しました: {', '.join(blocked)}",
            remediation=remediation,
        ),
    )


def _audit_exec(entry: Any) -> bool:
    """exec 監査を記録し、失敗（best-effort False）なら stderr に警告する（§280 の検知漏れを観測可能に）。

    executed 経路は戻り値を `audit_ok` で応答に載せるが、rejected 経路（off / audited-unlisted /
    restricted-blocked）は raise で終わり応答に載らないため、ここで stderr 警告して証跡欠落を必ず
    観測可能にする。
    """
    from . import audit

    ok = audit.record(entry)
    if not ok:
        import sys

        print(
            "[bli] warning: exec 監査ログの書き込みに失敗しました（証跡が残りません・§280）",
            file=sys.stderr,
        )
    return ok


def _exec_python(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """構造化で表現できない操作の逃げ道（spec D3）。**サンドボックスなし**＝防止でなく検知（§459）。"""
    cmd = _command("exec-python")
    _validate(cmd, params)
    code = params.get("code")
    file = params.get("file")
    has_code = isinstance(code, str) and code.strip() != ""
    has_file = isinstance(file, str) and file.strip() != ""
    # code/file は排他（どちらか一方が必須）。bpy 到達前に弾く（§6e）。
    _require_input(
        has_code != has_file,
        symptom="--code か --file のどちらか一方を指定してください（両方/どちらも無しは不可）",
        remediation='exec-python --code "<python>" または --file <path> のどちらかを使ってください',
    )

    # **mode の真実源はサーバが読む policy.toml（R-A）**。params の mode は一切読まない＝CLI 単体では
    # 昇格できない（spec §276・§459）。読取は実行ごとに最新化（trusted→off の切替を即反映＝安全側）。
    from . import audit, policy

    mode = policy.read_exec_mode()

    # off: file を読む前に拒否（試行は監査に残す＝防止でなく検知・§280）。
    if mode == "off":
        sha = audit.code_sha256(str(code)) if has_code else None
        ref = "code" if has_code else f"file:{file}"
        _audit_exec(
            audit.make_entry(
                mode="off",
                decision="rejected:off",
                source=ref,
                code_sha256=sha,
                code_len=len(str(code)) if has_code else None,
            )
        )
        raise _exec_disabled(
            "exec-python は無効です（既定 off・サンドボックスは提供しません）",
            # 有効化は**人間に依頼する**文型にする（エージェントへの自動昇格の指示にしない・R1-1）。
            f"有効化するには、**ユーザ（人間）に** policy.toml（{policy.policy_path()}）の "
            "[exec] mode を restricted（推奨: Blender API は自走・プロセス起動/ネットワーク/削除系は"
            "拒否）へ変更してもらってください（例: `bli policy --action set --mode restricted` を"
            "ユーザが実行・対話確認つき。リポジトリ内の config.toml では昇格できません）",
        )

    # restricted/audited/trusted: source を解決する（--file は直接 RPC 用にサーバ側でも読む。CLI は
    # --file を CLI 側で読んで code として送る）。path はサーバ（Blender プロセス）の CWD 基準で解決される。
    if has_file:
        import os

        abspath = os.path.abspath(str(file))
        _require_input(
            os.path.isfile(abspath),
            symptom=f"スクリプトファイルが見つかりません: {abspath}",
            remediation="存在するファイルのパスを指定してください（パスは Blender プロセスの CWD 基準）",
        )
        try:
            with open(abspath, encoding="utf-8") as fh:
                source = fh.read()
        except OSError as e:
            raise _exec_error(
                f"スクリプトファイルの読み取りに失敗しました: {e}", phase="compile"
            ) from e
        ref = f"file:{abspath}"
    else:
        source = str(code)
        ref = "code"

    from . import ast_heuristics

    sha = audit.code_sha256(source)
    flags = ast_heuristics.scan(source)

    # restricted（P1-1・設計レビュー G0）: AST ブロックリスト検査で自走可否を決める。Blender API
    # （bpy/bmesh/mathutils 等）は全面許可・プロセス起動/ネットワーク/削除系/動的実行/書込 open を
    # 検出したら拒否（監査に blocked を残す）。**静的検査は完全ではない**（getattr 迂回等）＝安全保証
    # ではなく事故防止（spec §459・security_guarantee:false は不変）。
    # blocked は「restricted の検査を通ったか」の監査証跡: None=検査対象外の経路（trusted/audited）
    # / []=検査して通過 / 非空=拒否理由（audit.AuditEntry.blocked の契約と対）。
    blocked: list[str] | None = None
    if mode == "restricted":
        from . import exec_restricted

        blocked = exec_restricted.scan_blocked(source)
        if blocked:
            _audit_exec(
                audit.make_entry(
                    mode="restricted",
                    decision="rejected:restricted-blocked",
                    source=ref,
                    code_sha256=sha,
                    code_len=len(source),
                    heuristic_flags=flags,
                    blocked=blocked,
                )
            )
            raise _exec_blocked_restricted(
                blocked,
                "コードからブロック対象（プロセス起動/ネットワーク/削除系/動的実行/書込 open）を"
                "除いて再実行してください。ファイル書き出しは export/save コマンドを使ってください。"
                f"どうしても必要な場合は**ユーザ（人間）の判断で** policy.toml"
                f"（{policy.policy_path()}）の [exec] mode を trusted（無制限）へ変更して"
                "もらってください（エージェントが自ら昇格しないこと）",
            )

    # audited（R-B）: 許可ハッシュ集合に一致するコードだけ自走実行する。不一致は監査に残して拒否し、
    # 追加すべき sha を提示する（ユーザがその sha を policy.toml の allow_hashes に足せば次回から自走）。
    if mode == "audited" and sha not in policy.read_allow_hashes():
        _audit_exec(
            audit.make_entry(
                mode="audited",
                decision="rejected:audited-unlisted",
                source=ref,
                code_sha256=sha,
                code_len=len(source),
                heuristic_flags=flags,
            )
        )
        raise _exec_disabled(
            f"exec mode=audited: このコードは許可リストにありません（sha256={sha}）",
            f"承認するなら、**ユーザ（人間）に** policy.toml の [exec] allow_hashes へこの sha256 を"
            f"追加してもらってください: {sha}",
        )

    # 実行が確定（trusted / restricted で検査通過 / audited で許可済み）。**実行前に**監査へ記録する
    # （証跡を先に残す）。restricted の通過は blocked=[]（検査済みの証跡）として残る。
    audit_ok = _audit_exec(
        audit.make_entry(
            mode=mode,
            decision="executed",
            source=ref,
            code_sha256=sha,
            code_len=len(source),
            heuristic_flags=flags,
            blocked=blocked,
        )
    )

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    outcome, fingerprint = gateway.exec_user_code(source)
    if outcome.error is not None:
        # 例外直前までにキャプチャした stdout/stderr を cause に載せ、観測性を失わない。
        captured = []
        if outcome.stdout:
            captured.append(f"stdout: {outcome.stdout.strip()}")
        if outcome.stderr:
            captured.append(f"stderr: {outcome.stderr.strip()}")
        raise _exec_error(
            f"{outcome.error.type}: {outcome.error.message}",
            phase=outcome.error.phase,
            cause=" | ".join(captured),
        )
    data = {
        "mode": mode,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
        "result_repr": outcome.result_repr,
        # **サンドボックスはしない**＝この出力を信頼の根拠にしないこと（spec §459・常に false）。
        "security_guarantee": False,
        # AST ヒューリスティック（T11.2・R-D）。注意喚起のみでブロックしない（mode ゲートとは独立）。
        "heuristic_flags": flags,
        # 許可リスト追加用（audited 昇格に使える）。
        "code_sha256": sha,
        # 監査記録に成功したか（false なら証跡欠落＝可用性優先で実行はしたが観測可能にする・§280）。
        "audit_ok": audit_ok,
    }
    return _ok_offload("exec-python", data, "exec-python/v1", fingerprint=fingerprint)


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


def _stl_triangle_count(path: str, ascii_format: bool) -> int | None:
    """STL の三角形数を読む（binary=header の uint32 / ascii=facet 行数）。検証用の golden 指標。"""
    if not ascii_format:
        import struct

        with open(path, "rb") as f:
            head = f.read(84)
        if len(head) < 84:
            return None
        return int(struct.unpack("<I", head[80:84])[0])
    count = 0
    with open(path, "rb") as f:
        for line in f:
            if line.lstrip().startswith(b"facet"):
                count += 1
    return count


def _print_export(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("print-export")
    _validate(cmd, params)
    fmt = str(params["format"])
    path = str(params["path"])
    _require_input(
        path.strip() != "",
        symptom="--path が空です",
        remediation="出力ファイルパスを指定してください",
    )
    # scale は global_scale 一本化の knob。0 は退化（全座標が原点に潰れる）・負値は反転（法線が
    # 裏返り 3D プリント不可）になるため、無音で壊れた STL を出さないよう bpy 到達前に正値を要求する
    # （nan/inf は schema が拒否済み・duplicate の count 範囲ガードと同流儀・§6e）。
    scale = float(params.get("scale", 1.0))
    _require_input(
        scale > 0.0,
        symptom=f"--scale は正の値で指定してください（指定: {scale}）",
        remediation="0 は退化、負値は法線反転で 3D プリントに使えません",
    )
    ascii_format = bool(params.get("ascii", False))
    apply_modifiers = bool(params.get("apply_modifiers", True))

    import os

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())

    # 形式の export operator を能力検出で解決する（capability RESOLVERS 経由・§9 OperatorResolver）。
    # これは対象に依存しない判定なので require_single/require_mesh より前に行う（3mf を要求した場合は
    # 対象の型エラーより先に「形式が使えない」を返す）。3mf は両版とも operator が実体なし（§E8）→ None。
    operator = gateway.resolve_export_operator(fmt)
    if operator is None:
        # 黙って別形式に差し替えず、明示的に CAPABILITY_UNAVAILABLE で縮退する（要求と異なる形式を書かない）。
        hint = (
            "--format stl を使ってください（大半のスライサは STL を受容します）"
            if fmt == "3mf"
            else "Blender のバージョン/構成を確認してください（wm.stl_export が必要）"
        )
        raise _capability_unavailable(
            f"{fmt} 形式の export operator がこの環境では利用できません", hint
        )
    if fmt != "stl":
        # operator は実在するが v1 は STL 書き出しのみ実装（5.0/4.4 では 3mf operator 不在のため
        # ここには到達しない防御。将来 3mf exporter を配線する際にこの分岐を実装へ差し替える）。
        raise _capability_unavailable(
            f"v1 は {fmt} 出力に未対応です",
            "--format stl を使ってください（大半のスライサは STL を受容します）",
        )

    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    gateway.require_mesh(obj)  # STL は mesh のみ
    # 出力先ディレクトリの不在は operator の生 RuntimeError（INTERNAL 化）になり得るため弾く
    # （path は addon プロセス側＝Blender の CWD 基準で解決される）。
    parent = os.path.dirname(os.path.abspath(path)) or "."
    _require_input(
        os.path.isdir(parent),
        symptom=f"出力先ディレクトリがありません: {parent}",
        remediation="存在するディレクトリのパスを指定してください",
    )

    meta = gateway.export_stl(
        obj,
        path,
        ascii_format=ascii_format,
        global_scale=scale,
        apply_modifiers=apply_modifiers,
    )
    # 実際に書かれた成果物のファイル統計（検証材料）。export 後にファイルから算出する。
    try:
        sha, size = _file_sha256_size(path)
        triangles = _stl_triangle_count(path, ascii_format)
    except (
        OSError
    ) as e:  # 書き出し後の読み取り失敗は INTERNAL でなく業務エラーへ（capture と同流儀）。
        raise JsonRpcError(
            RPC_BUSINESS_ERROR,
            ErrorCode.E_OPERATOR,
            make_error(
                ErrorCode.E_OPERATOR,
                category=ErrorCategory.ENVIRONMENT,
                retryable=False,
                symptom=f"出力ファイルの読み取りに失敗しました: {e}",
                remediation="ディスク容量/権限/パスを確認してください",
            ),
        ) from e
    data = {
        "name": obj.name,
        "path": os.path.abspath(path),
        "size": size,
        "sha256": sha,
        "triangles": triangles,
        **meta,
    }
    # 読み取り専用（シーンは変えない）。fingerprint は成果物の content-address（sha 先頭16桁）＝
    # 出力アーティファクトの drift 指標（capture と同流儀・binary STL は決定的・§E8）。
    return _ok("print-export", data, fingerprint=sha[:16])


# format=fbx でのみ有効な追加パラメータ（P1-3・Unity 取込向け）。他 format に指定されたら
# silent ignore せず INVALID_PARAMS で弾く（modifier/mesh の type/op 別パラメータと同じ流儀）。
_EXPORT_FBX_ONLY_PARAMS: set[str] = {
    "axis_forward",
    "axis_up",
    "scale",
    "apply_unit_scale",
    "embed_textures",
}


def _export(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """多形式 export（M9 T9.1・obj/fbx/gltf/stl・3mf は CAPABILITY）。print-export の一般化（§E9）。

    fbx 専用オプション（axis_forward/axis_up/scale/apply_unit_scale/embed_textures）は P1-3・
    Unity 取込向け（両版とも export_scene.fbx のパラメータは完全同一・§4 P1-3 設計レビュー）。
    """
    cmd = _command("export")
    _validate(cmd, params)
    fmt = str(params["format"])
    path = str(params["path"])
    _require_input(
        path.strip() != "",
        symptom="--path が空です",
        remediation="出力ファイルパスを指定してください",
    )
    # glTF は GLB 単一固定（export_format 有効値は両版とも GLB/GLTF_SEPARATE のみ・GLTF_EMBEDDED は
    # 存在しない＝実機確認済み。SEPARATE は .bin 分離で sha256/size が崩れるため不採用・§E9）。.glb 以外の
    # 拡張子は無効 enum→TypeError→INTERNAL を避け、bpy 到達前に USER_INPUT で弾く（黙って中身と名前を
    # 食い違わせない）。stl/obj/fbx の拡張子は呼び出し側責任（exporter は filepath をそのまま使う）。
    if fmt == "gltf":
        _require_input(
            path.lower().endswith(".glb"),
            symptom="glTF は単一ファイルの .glb のみ対応です（v1）",
            remediation="--path の拡張子を .glb にしてください（.gltf 分離形式は未対応）",
        )
    # fbx 専用 param は format=fbx のときのみ有効（bpy 到達前に弾く・presence-sensitive）。
    present_fbx_params = {k for k in _EXPORT_FBX_ONLY_PARAMS if k in params}
    _require_input(
        fmt == "fbx" or not present_fbx_params,
        symptom=f"{sorted(present_fbx_params)} は --format fbx のときのみ有効です",
        remediation="--format fbx で使うか、これらのオプションを外してください",
    )
    if "scale" in present_fbx_params:
        # print-export の --scale と同じ理由（0 は退化・負値は法線反転）で、Unity 側に崩れた/裏返った
        # メッシュを渡さないよう bpy 到達前に正値を要求する。
        fbx_scale = float(params["scale"])
        _require_input(
            fbx_scale > 0.0,
            symptom=f"--scale は正の値で指定してください（指定: {fbx_scale}）",
            remediation="0 は退化、負値は法線反転で不正な FBX になります",
        )
    targets = params.get("targets")
    use_selection = bool(params.get("use_selection", False))
    # 空/空白のみの --targets は入力ミスとして早期に弾く（B2 の完全一致既定では 0 件エラーに
    # なるだけだが、--regex 併用時は空 regex が全マッチ＝シーン全体に化けるため。path と同流儀）。
    if targets is not None:
        _require_input(
            str(targets).strip() != "",
            symptom="--targets が空です",
            remediation="対象名/regex を指定するか、--targets 自体を省略してください（省略でシーン全体）",
        )

    import os

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())

    # 形式の export operator を能力検出で解決する（対象に依存しないので対象解決より前・print-export と同順）。
    # 3mf は両版とも operator が実体なし（§E8）→ 黙って別形式に差し替えず CAPABILITY_UNAVAILABLE で縮退。
    operator = gateway.resolve_export_operator(fmt)
    if operator is None:
        hint = (
            "--format stl/obj/gltf/fbx を使ってください（3mf は標準では未導入・§E8）"
            if fmt == "3mf"
            else "Blender のバージョン/構成を確認してください"
        )
        raise _capability_unavailable(
            f"{fmt} 形式の export operator がこの環境では利用できません", hint
        )

    # セレクタ解決: --targets 指定=その集合 / --use-selection=現在の選択集合 / どちらも省略=シーン全体（None）。
    if targets is not None:
        select_objs = gateway.require_targets(str(targets), regex=bool(params.get("regex", False)))
    elif use_selection:
        select_objs = gateway.current_selection()
        _require_input(
            len(select_objs) > 0,
            symptom="現在の選択集合が空です（--use-selection 指定）",
            remediation="select で対象を選ぶか、--targets で対象を指定してください",
        )
    else:
        select_objs = None  # シーン全体

    # 出力先ディレクトリの不在は operator の生 RuntimeError（INTERNAL 化）になり得るため弾く（print-export と同流儀）。
    parent = os.path.dirname(os.path.abspath(path)) or "."
    _require_input(
        os.path.isdir(parent),
        symptom=f"出力先ディレクトリがありません: {parent}",
        remediation="存在するディレクトリのパスを指定してください",
    )

    # fbx_options は指定されたキーのみ（省略キーは含めない＝gateway 側 rna 検査/写像の対象を絞る）。
    fbx_options: dict[str, Any] | None = (
        {k: params[k] for k in present_fbx_params} if present_fbx_params else None
    )

    meta = gateway.export_generic(
        fmt, operator, path, select_objs=select_objs, fbx_options=fbx_options
    )
    try:
        sha, size = _file_sha256_size(path)
    except (
        OSError
    ) as e:  # 書き出し後の読み取り失敗は INTERNAL でなく業務エラーへ（print-export と同流儀）。
        raise JsonRpcError(
            RPC_BUSINESS_ERROR,
            ErrorCode.E_OPERATOR,
            make_error(
                ErrorCode.E_OPERATOR,
                category=ErrorCategory.ENVIRONMENT,
                retryable=False,
                symptom=f"出力ファイルの読み取りに失敗しました: {e}",
                remediation="ディスク容量/権限/パスを確認してください",
            ),
        ) from e
    data = {"path": os.path.abspath(path), "size": size, "sha256": sha, **meta}
    # 読み取り専用（選択は save/restore で非破壊）。fingerprint は成果物の content-address（capture/print-export
    # と同流儀）。STL は決定的だが gltf/fbx はメタ情報で版/実行ごとに変わり得るため、版間 golden は
    # 往復 bbox（smoke）で検証し sha は「この成果物の id」として扱う。
    return _ok("export", data, fingerprint=sha[:16])


def _import(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """多形式 import（M9 T9.2・obj/fbx/gltf/stl・3mf は CAPABILITY）。前後 diff で取込特定（§E9）。"""
    cmd = _command("import")
    _validate(cmd, params)
    fmt = str(params["format"])
    path = str(params["path"])
    _require_input(
        path.strip() != "",
        symptom="--path が空です",
        remediation="入力ファイルパスを指定してください",
    )

    import os

    # 相対パスは Python CWD（os.path.isfile）と Blender importer の filepath 解決基準が食い違い得るため、
    # 先に絶対パス化して両者を一致させる（isfile も operator も同じ絶対パスを見る・export と同流儀）。
    path = os.path.abspath(path)

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())

    # 形式の import operator を能力検出で解決する（対象に依存しないので先・export と同順）。
    # 3mf は両版とも operator が実体なし（§E8）→ 黙って縮退せず CAPABILITY_UNAVAILABLE。
    operator = gateway.resolve_import_operator(fmt)
    if operator is None:
        hint = (
            "--format stl/obj/gltf/fbx を使ってください（3mf は標準では未導入・§E8）"
            if fmt == "3mf"
            else "Blender のバージョン/構成を確認してください"
        )
        raise _capability_unavailable(
            f"{fmt} 形式の import operator がこの環境では利用できません", hint
        )

    # 入力ファイルの不在は operator の生 RuntimeError（"Cannot open file"）になり得るため、より正確な
    # USER_INPUT として bpy 到達前に弾く（絶対パスで判定＝operator と同じファイルを見る）。
    _require_input(
        os.path.isfile(path),
        symptom=f"入力ファイルがありません: {path}",
        remediation="存在するファイルパスを指定してください",
    )

    imported = gateway.import_generic(fmt, operator, path)
    data = {
        "format": fmt,
        "operator": operator,
        "path": path,
        "imported": imported,
        "count": len(imported),
    }
    # import はシーンを変える（mutates=True）。fingerprint は取込オブジェクト名集合の決定的ハッシュ
    # （drift 指標・duplicate と同流儀）。大量取込は output_ref 退避（scene-info と同じ _ok_offload）。
    fp = gateway.names_fingerprint([o["name"] for o in imported])
    return _ok_offload("import", data, "import/v1", fingerprint=fp)


def _save(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """.blend 保存（M9 T9.3・上書きは既定 backup・研究 §E10）。"""
    cmd = _command("save")
    _validate(cmd, params)
    path = params.get("path")
    backup = bool(params.get("backup", True))
    # --path 指定時の拡張子チェックは bpy 不要なので早期に弾く（.glb 必須化と同流儀）。省略時は現在の
    # .blend へ保存するため拡張子は問わない（既存ファイル）。
    if path is not None:
        _require_input(
            str(path).strip() != "",
            symptom="--path が空です",
            remediation="保存先(.blend)を指定するか --path を省略してください",
        )
        _require_input(
            str(path).lower().endswith(".blend"),
            symptom="--path は .blend 拡張子で指定してください",
            remediation="保存先を <name>.blend にしてください",
        )

    import os

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())

    # target 解決: --path 指定=そのパス / 省略=現在の .blend（未保存=空なら USER_INPUT）。
    if path is not None:
        target = os.path.abspath(str(path))
    else:
        current = gateway.current_filepath()
        _require_input(
            current.strip() != "",
            symptom="まだ一度も保存されていません（保存先が不明）",
            remediation="--path で保存先(.blend)を指定してください",
        )
        target = os.path.abspath(current)

    # 保存先ディレクトリの不在は operator の生 RuntimeError になり得るため bpy 到達前に弾く（export と同流儀）。
    parent = os.path.dirname(target) or "."
    _require_input(
        os.path.isdir(parent),
        symptom=f"保存先ディレクトリがありません: {parent}",
        remediation="存在するディレクトリのパスを指定してください",
    )
    # backup を出したかは「保存前に既存ファイルがあったか」で決まる（新規保存は backup なし）。
    existed = os.path.isfile(target)

    gateway.save_blend(target, backup=backup)
    try:
        size = os.path.getsize(target)
    except OSError as e:  # 保存後の読み取り失敗は INTERNAL でなく業務エラーへ（export と同流儀）。
        raise JsonRpcError(
            RPC_BUSINESS_ERROR,
            ErrorCode.E_OPERATOR,
            make_error(
                ErrorCode.E_OPERATOR,
                category=ErrorCategory.ENVIRONMENT,
                retryable=False,
                symptom=f"保存ファイルの確認に失敗しました: {e}",
                remediation="ディスク容量/権限/パスを確認してください",
            ),
        ) from e

    # backup を取ったかは「保存後に .blend1 が実在するか」で確定する（backup 要求 かつ 保存前に既存 だけ
    # で報告すると、native backup が rename に失敗したケースで『backup 済み』と偽報告し、ロールバック不能を
    # 安全と誤認させる）。Blender native は <name>.blend → <name>.blend1（§E10・敵対的レビュー P1）。
    backup_path = target + "1"
    backed_up = backup and existed and os.path.isfile(backup_path)
    data = {
        "path": target,
        "size": size,
        "backed_up": backed_up,
        "backup_path": backup_path if backed_up else None,
    }
    # fingerprint は保存結果の軽量 digest（path|size）。.blend 全体 sha は大容量/非決定的のため採らない（§E10）。
    import hashlib

    fp = hashlib.sha256(f"{target}|{size}".encode()).hexdigest()[:16]
    return _ok("save", data, fingerprint=fp)


def _open(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """.blend を開く（M9 T9.4・シーン全体置換・未保存変更は --force 必須・研究 §E11）。"""
    cmd = _command("open")
    _validate(cmd, params)
    path = str(params["path"])
    force = bool(params.get("force", False))
    # 空/拡張子チェックは bpy 不要なので早期に弾く（save と対称・.glb 必須化と同流儀）。
    _require_input(
        path.strip() != "",
        symptom="--path が空です",
        remediation="開く .blend のパスを指定してください",
    )
    _require_input(
        path.lower().endswith(".blend"),
        symptom="--path は .blend 拡張子で指定してください",
        remediation="開くファイルを <name>.blend にしてください",
    )

    import os

    # 相対パスは Python CWD と Blender の解決基準が食い違い得るため先に絶対パス化する（import/save と同流儀）。
    path = os.path.abspath(path)

    # 入力ファイルの不在は operator の生 RuntimeError になり得るため bpy 到達前に USER_INPUT で弾く
    # （絶対パスで判定＝operator と同じファイルを見る・import と同流儀）。
    _require_input(
        os.path.isfile(path),
        symptom=f"ファイルがありません: {path}",
        remediation="存在する .blend のパスを指定してください",
    )

    # 未保存ガード（§E11・ユーザー選択）: open はシーン全体を置換して未保存変更を不可逆に失う。bli が
    # 最後の save/open 以降に mutate していたら（自前 session_state 追跡＝is_dirty は dispatch 文脈で
    # save 後に reset せず使えない）、--force なしは E_PRECONDITION で拒否する。検証はすべて bpy 到達前
    # （session_state は純Python・§6e）。
    from . import session_state

    was_modified = session_state.is_modified()
    if was_modified and not force:
        raise JsonRpcError(
            RPC_BUSINESS_ERROR,
            ErrorCode.E_PRECONDITION,
            make_error(
                ErrorCode.E_PRECONDITION,
                category=ErrorCategory.PRECONDITION,
                retryable=False,
                symptom="未保存の変更があります（open はシーン全体を置換し変更を失います）",
                remediation="先に save するか、破棄してよければ --force を付けてください",
            ),
        )

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())

    summary = gateway.open_blend(path)
    data = {
        "path": path,
        "scene": summary["scene"],
        "object_count": summary["object_count"],  # scene-info と命名を揃える（int カウント）
        "forced": force,
        # --force で実際に未保存変更を破棄したか（エージェントが破棄の有無を判別できるよう返す）。
        "discarded_unsaved": bool(was_modified),
    }
    # session_state の clean 化（mark_saved）は dispatch 後フックが method in _SESSION_CLEARING_METHODS
    # で行う（単一窓口）。fingerprint は開いたシーンの粗い状態指標（name/type/matrix・undo/redo と共通）。
    fp = gateway.scene_state_fingerprint()
    return _ok("open", data, fingerprint=fp)


def _png_dimensions(path: str) -> tuple[int, int] | None:
    """PNG の IHDR から実出力解像度 (width, height) を読む。

    screen は area 全体≠実出力（WINDOW リージョン）で解像度がずれ得るため、報告値は
    保存済み PNG の実寸を採る（全 source 共通・敵対的レビュー P2-2）。
    """
    import struct

    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) >= 24 and head[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)
    return None


def _capture(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("capture")
    _validate(cmd, params)
    source = str(params.get("source", "viewport"))
    # camera は render 専用 / width・height は screen 不可（領域サイズ固定）。silent ignore せず弾く（§6e）。
    if "camera" in params:
        _require_input(
            source == "render",
            symptom="--camera は render のときのみ有効です",
            remediation="render で使うか --camera を外してください",
        )
    if "width" in params or "height" in params:
        _require_input(
            source != "screen",
            symptom="--width/--height は screen では指定できません（領域サイズ固定）",
            remediation="viewport/render で使うか --width/--height を外してください",
        )

    from bli_core import runtime

    # 解像度は暴走防止のため範囲を bpy 到達前に弾く（範囲は ops が SSOT・CLI は型/ENUM のみ検証）。
    for key in ("width", "height"):
        if key in params:
            v = int(params[key])
            _require_input(
                runtime.CAPTURE_MIN_DIM <= v <= runtime.CAPTURE_MAX_DIM,
                symptom=f"--{key} は {runtime.CAPTURE_MIN_DIM}〜{runtime.CAPTURE_MAX_DIM} の範囲です",
                remediation="範囲内の値を指定してください",
            )

    import os

    from bli_core import output_ref as outref

    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())

    out_dir = runtime.outputs_dir()
    tmp_path = str(out_dir / f"capture_tmp{os.getpid()}.png")
    width = int(params.get("width", runtime.CAPTURE_DEFAULT_WIDTH))
    height = int(params.get("height", runtime.CAPTURE_DEFAULT_HEIGHT))
    try:
        if source == "viewport":
            meta = gateway.capture_viewport(tmp_path, width, height)
        elif source == "screen":
            meta = gateway.capture_screen(tmp_path)
        elif source == "render":
            camera = params.get("camera")
            meta = gateway.capture_render(
                tmp_path, width, height, str(camera) if camera is not None else None
            )
        else:  # source は ENUM 検証済みのため到達不能（新 source の分岐漏れ検出の防御）
            raise JsonRpcError(
                RPC_BUSINESS_ERROR,
                ErrorCode.E_PRECONDITION,
                make_error(ErrorCode.E_PRECONDITION, symptom=f"未対応の source: {source}"),
            )
        # 出力ファイルをコンテンツアドレスで退避（パス安全/アトミック/ストリーミング sha を output_ref と共有）。
        descriptor = outref.offload_file(tmp_path, "capture/v1", out_dir, suffix=".png")
    except OSError as e:
        # gateway 成功後のファイル I/O 失敗（書き出し失敗/容量/権限）は INTERNAL でなく業務エラーへ（敵対的 P1-1）。
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise JsonRpcError(
            RPC_BUSINESS_ERROR,
            ErrorCode.E_OPERATOR,
            make_error(
                ErrorCode.E_OPERATOR,
                category=ErrorCategory.ENVIRONMENT,
                retryable=False,
                symptom=f"キャプチャ出力の書き出しに失敗しました: {e}",
                remediation="ディスク容量/権限/outputs ディレクトリを確認してください",
            ),
        ) from e

    dims = _png_dimensions(descriptor["path"])  # 実出力解像度（screen の領域≠出力ずれを吸収）
    out_w, out_h = dims if dims is not None else (meta.get("width"), meta.get("height"))
    data: dict[str, Any] = {
        "source": source,
        "path": descriptor["path"],
        "size": descriptor["size"],
        "sha256": descriptor["sha256"],
        "width": out_w,
        "height": out_h,
    }
    if "camera" in meta:  # render の実描画カメラ（active 解決後の名前）
        data["camera"] = meta["camera"]
    return _ok("capture", data, fingerprint=descriptor["id"])


def _do_undo_redo(
    name: str, params: dict[str, Any], apply_fn: Callable[[Any, int], int]
) -> dict[str, Any]:
    """undo/redo の共通処理（steps 範囲検証 → GUI で steps 段適用 → 状態 fingerprint・実地FB #3）。

    steps 上限は runtime.MAX_UNDO_STEPS（暴走防止・CLI/ops 共有）。GUI 必須は gateway 側で
    E_PRECONDITION 縮退（--background）。応答に requested/applied と粗いシーン fingerprint を返す。
    """
    cmd = _command(name)
    _validate(cmd, params)
    from bli_core import runtime

    steps = int(params.get("steps", 1))
    _require_input(
        1 <= steps <= runtime.MAX_UNDO_STEPS,
        symptom=f"steps は 1〜{runtime.MAX_UNDO_STEPS} の範囲で指定してください（指定: {steps}）",
        remediation=f"--steps を 1〜{runtime.MAX_UNDO_STEPS} にしてください",
    )
    from . import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    applied = apply_fn(gateway, steps)
    return _ok(
        name,
        {"requested": steps, "applied": applied},
        fingerprint=gateway.scene_state_fingerprint(),
    )


def _undo(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    return _do_undo_redo("undo", params, lambda g, n: g.undo_steps(n))


def _redo(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    return _do_undo_redo("redo", params, lambda g, n: g.redo_steps(n))


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
        from . import session_state

        session_state.mark_modified()


def dispatch(method: str, params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    """bpy 系は専用ハンドラ、その他は handlers.dispatch に委譲する。"""
    # mutating コマンドは実行 **前** に pessimistic に modified（partial mutation でも安全側・§E11）。
    _premark_session_modified(method)
    fn = _BPY_HANDLERS.get(method)
    result = fn(params, info) if fn is not None else handlers.dispatch(method, params, info)
    # save/open が成功したらディスクと一致＝clean に戻す（例外時はここに来ない＝失敗 save/open は modified のまま）。
    if method in _SESSION_CLEARING_METHODS and result.get("success") is True:
        from . import session_state

        session_state.mark_saved()
    return result
