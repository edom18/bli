"""modifier ハンドラ（任意 type + --props・ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

from typing import Any

from ..handlers import ServerInfo
from ._shared import (
    _check_mode,
    _command,
    _guard_shared_mesh,
    _ok,
    _require_input,
    _resolve_boolean_operand,
    _validate,
)

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

    from .. import gateway  # lazy: bpy 依存

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
