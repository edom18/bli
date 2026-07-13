"""BpyGateway モディファイア（add/remove/list/apply + 任意 type/--props・gateway/ 分割 P2-4）。

元 gateway.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

import math
from typing import Any

import bpy  # type: ignore

from bli_core.errors import ErrorCategory, ErrorCode
from bli_core.protocol import JsonRpcError

from .core import _digest16, _op_error, push_undo, run_operator

# ---- モディファイア（M6 T6.4 / add/remove/list は bpy.data 直接・apply は run_operator）----
#
# modifier は **オブジェクト単位**（obj.modifiers）。add/remove/list は mesh データを触らないため
# 共有 mesh ガード不要。**apply のみ** mesh へ焼き込む（apply-transform と同様にガードが要る）。

_MIRROR_AXES = ("X", "Y", "Z")

# モディファイアを持てるオブジェクト型（これ以外は E_PRECONDITION。非対応型への
# obj.modifiers.new() は生 RuntimeError になり INTERNAL 誤分類されるのを防ぐ）。
_MODIFIER_OBJECT_TYPES = frozenset(
    {"MESH", "CURVE", "SURFACE", "FONT", "LATTICE", "VOLUME", "GREASEPENCIL", "POINTCLOUD"}
)


def require_modifier_support(obj: Any) -> None:
    """モディファイアを持てない型（EMPTY/LIGHT/CAMERA 等）は E_PRECONDITION で弾く。

    material の require_material_support と同じ流儀。USER_INPUT 的な型ミスを INTERNAL に
    しないための前提検証（個別 modifier×型 の細かな非対応は add_modifier 側で捕捉する）。
    """
    if obj.type not in _MODIFIER_OBJECT_TYPES:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"モディファイアを持てない型です（type={obj.type}）",
        )


def _modifier_summary(mod: Any) -> dict[str, Any]:
    """モディファイア1件の要約（name/type + 種類別の主要プロパティ）。"""
    data: dict[str, Any] = {"name": mod.name, "type": mod.type}
    t = mod.type
    if t == "MIRROR":
        data["axes"] = [ax for ax, on in zip(_MIRROR_AXES, mod.use_axis, strict=True) if on]
    elif t == "SUBSURF":
        data["levels"] = mod.levels
    elif t == "SOLIDIFY":
        data["thickness"] = round(mod.thickness, 6)
    elif t == "DECIMATE":
        data["ratio"] = round(mod.ratio, 6)
    elif t == "BOOLEAN":
        data["operation"] = mod.operation
        data["object"] = mod.object.name if mod.object is not None else None
    return data


def require_modifier(obj: Any, name: str) -> Any:
    """名前でモディファイアを解決する。無ければ E_TARGET_NOT_FOUND。"""
    mod = obj.modifiers.get(name)
    if mod is None:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"モディファイアが見つかりません: {name}",
            category=ErrorCategory.USER_INPUT,
        )
    return mod


# ---- modifier 任意 type + --props（P2-3 G4）----
# type の実在と props の型は **rna から能力検出**で検証する（版番号分岐なし・両版 83 種を
# スパイクで確定）。数値の範囲外は Blender の rna が silent に clamp する（segments 10000→1000）
# ため、設定後に実値を読み戻して applied_props で可視化する（silent drop 禁止の流儀）。


def valid_modifier_types() -> list[str]:
    """この Blender が受け付ける Modifier type の一覧（rna enum から能力検出）。"""
    return [i.identifier for i in bpy.types.Modifier.bl_rna.properties["type"].enum_items]


def require_modifier_type(mod_type: str) -> None:
    """Modifier type の実在を rna enum で検証する（無効は USER_INPUT・有効一覧を提示）。"""
    valid = valid_modifier_types()
    if mod_type not in valid:
        raise _op_error(
            ErrorCode.INVALID_PARAMS,
            f"未知の modifier type です: {mod_type}（有効: {'|'.join(valid)}）",
            category=ErrorCategory.USER_INPUT,
        )


def _modifier_prop_rna(mod: Any) -> dict[str, Any]:
    """mod の編集可能 rna プロパティ（identifier → rna Property・rna_type 除外）。"""
    return {
        p.identifier: p
        for p in mod.bl_rna.properties
        if not p.is_readonly and p.identifier != "rna_type"
    }


def _coerce_prop_value(prop: Any, value: Any) -> Any:
    """--props の JSON 値を rna プロパティ型へ検証・変換する（不正は USER_INPUT）。

    POINTER は v1 では Object 参照のみ（名前文字列で解決）。ENUM フラグ（複数選択）と
    Object 以外の参照は未対応として明示的に弾く（silent drop しない）。
    """
    t = prop.type
    ident = prop.identifier

    def _bad(expect: str) -> JsonRpcError:
        return _op_error(
            ErrorCode.INVALID_PARAMS,
            f"props.{ident} の型が不正です（期待: {expect} / 実際: {type(value).__name__}）",
            category=ErrorCategory.USER_INPUT,
        )

    array_n = int(getattr(prop, "array_length", 0) or 0)
    if t == "BOOLEAN":
        if array_n:
            if not (
                isinstance(value, list)
                and len(value) == array_n
                and all(isinstance(v, bool) for v in value)
            ):
                raise _bad(f"bool の配列（長さ {array_n}）")
            return value
        if not isinstance(value, bool):
            raise _bad("bool")
        return value
    if t == "INT":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _bad("int")
        return value
    if t == "FLOAT":

        def _num(v: Any) -> bool:
            return (
                not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(float(v))
            )

        if array_n:
            if not (
                isinstance(value, list) and len(value) == array_n and all(_num(v) for v in value)
            ):
                raise _bad(f"数値の配列（長さ {array_n}・有限値）")
            return [float(v) for v in value]
        if not _num(value):
            raise _bad("有限の数値")
        return float(value)
    if t == "ENUM":
        if getattr(prop, "is_enum_flag", False):
            raise _op_error(
                ErrorCode.INVALID_PARAMS,
                f"props.{ident}（複数選択 ENUM）は未対応です",
                category=ErrorCategory.USER_INPUT,
            )
        idents = [i.identifier for i in prop.enum_items]
        if not isinstance(value, str) or value not in idents:
            raise _op_error(
                ErrorCode.INVALID_PARAMS,
                f"props.{ident} の値が不正です: {value!r}（有効: {'|'.join(idents)}）",
                category=ErrorCategory.USER_INPUT,
            )
        return value
    if t == "STRING":
        if not isinstance(value, str):
            raise _bad("str")
        return value
    if t == "POINTER":
        fixed = getattr(getattr(prop, "fixed_type", None), "identifier", "")
        if fixed != "Object":
            raise _op_error(
                ErrorCode.INVALID_PARAMS,
                f"props.{ident}（{fixed or '不明'} 参照）は未対応です（v1 は Object 参照のみ名前で解決）",
                category=ErrorCategory.USER_INPUT,
            )
        if not isinstance(value, str):
            raise _bad("str（オブジェクト名）")
        target = bpy.data.objects.get(value)
        if target is None:
            raise _op_error(
                ErrorCode.E_TARGET_NOT_FOUND,
                f"props.{ident} のオブジェクトが見つかりません: {value}",
                category=ErrorCategory.USER_INPUT,
            )
        return target
    raise _op_error(
        ErrorCode.INVALID_PARAMS,
        f"props.{ident} のプロパティ型 {t} は未対応です",
        category=ErrorCategory.USER_INPUT,
    )


def _prop_value_repr(mod: Any, ident: str) -> Any:
    """設定後の実値を JSON 化可能な形で読み戻す（rna の clamp を applied_props で可視化）。"""
    v = getattr(mod, ident)
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if hasattr(v, "name"):
        return v.name  # Object 等の ID 参照
    try:
        return [float(x) if isinstance(x, float) else x for x in list(v)]
    except TypeError:
        return str(v)


def set_modifier_props(mod: Any, props: dict[str, Any]) -> dict[str, Any]:
    """--props(JSON) を rna 検証つきで設定し、設定後の実値を返す（P2-3 G4）。

    未知キーは USER_INPUT で弾き、有効キー一覧を提示する。全キーの検証を **setattr より前に**
    済ませる（半端な設定で失敗しない）。範囲外は Blender の rna が clamp するため、戻り値
    （読み戻し実値）で可視化する。
    """
    rna = _modifier_prop_rna(mod)
    unknown = [k for k in props if k not in rna]
    if unknown:
        raise _op_error(
            ErrorCode.INVALID_PARAMS,
            f"{mod.type} に無いプロパティです: {', '.join(sorted(unknown))}"
            f"（有効: {', '.join(sorted(rna))}）",
            category=ErrorCategory.USER_INPUT,
        )
    coerced = {key: _coerce_prop_value(rna[key], raw) for key, raw in props.items()}
    applied: dict[str, Any] = {}
    for key, value in coerced.items():
        try:
            setattr(mod, key, value)
        except (TypeError, ValueError, OverflowError) as e:
            raise _op_error(
                ErrorCode.INVALID_PARAMS,
                f"props.{key} を設定できません: {e}",
                category=ErrorCategory.USER_INPUT,
            ) from e
        applied[key] = _prop_value_repr(mod, key)
    return applied


def add_modifier(
    obj: Any,
    mod_type: str,
    *,
    name: str | None = None,
    axis: str | None = None,
    levels: int | None = None,
    thickness: float | None = None,
    ratio: float | None = None,
    operation: str | None = None,
    operand: Any = None,
    props: dict[str, Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """obj にモディファイアを追加し、要約を返す（op 不要・obj.modifiers 直接）。

    name 省略時は Blender 既定名（type 名）。専用フラグ（5種の主要プロパティ）または
    props（任意プロパティの rna 検証つき設定・P2-3）を適用する（併用は ops が弾く）。
    props の検証失敗時は追加した modifier を撤去してから送出する（半端な状態を残さない・
    _add_then_apply と同じアトミック流儀）。対象型がこの modifier を受け付けない場合
    （生 RuntimeError）は E_PRECONDITION に変換する。
    """
    try:
        mod = obj.modifiers.new(name or mod_type.title(), mod_type)
    except RuntimeError as e:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"この型にこのモディファイアは追加できません（type={obj.type}, modifier={mod_type}）: {e}",
        ) from e
    if mod is None:
        # 一部の非対応組み合わせは例外でなく None を返す（rna 仕様）。同じ E_PRECONDITION に写像。
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"この型にこのモディファイアは追加できません（type={obj.type}, modifier={mod_type}）",
        )
    if mod_type == "MIRROR" and axis is not None:
        for i, ax in enumerate(_MIRROR_AXES):
            mod.use_axis[i] = ax == axis
    elif mod_type == "SUBSURF" and levels is not None:
        mod.levels = levels
        mod.render_levels = levels
    elif mod_type == "SOLIDIFY" and thickness is not None:
        mod.thickness = thickness
    elif mod_type == "DECIMATE" and ratio is not None:
        mod.ratio = ratio
    elif mod_type == "BOOLEAN":
        if operation is not None:
            mod.operation = operation
        if operand is not None:
            mod.object = operand
    applied_props: dict[str, Any] | None = None
    if props:
        try:
            applied_props = set_modifier_props(mod, props)
        except JsonRpcError:
            obj.modifiers.remove(mod)
            raise
    if message:
        push_undo(message)
    summary = _modifier_summary(mod)
    if applied_props is not None:
        summary["applied_props"] = applied_props
    return summary


def remove_modifier(obj: Any, name: str, *, message: str | None = None) -> None:
    """名前でモディファイアを削除する（無効名は E_TARGET_NOT_FOUND・op 不要）。"""
    mod = require_modifier(obj, name)
    obj.modifiers.remove(mod)
    if message:
        push_undo(message)


def list_modifiers(obj: Any) -> list[dict[str, Any]]:
    """obj のモディファイアスタックを順に要約する（スタック順は意味があるので保持）。"""
    return [_modifier_summary(m) for m in obj.modifiers]


def apply_modifier(obj: Any, name: str, *, message: str | None = None) -> dict[str, Any]:
    """モディファイアを mesh データへ適用する（operator 経由・破壊的）。

    無効名の事前検証・共有 mesh ガードは呼び出し側（ops）が apply 前に行う。
    """
    run_operator(bpy.ops.object.modifier_apply, obj, message=message, modifier=name)
    return {"applied": name, "modifiers": list_modifiers(obj)}


def modifiers_fingerprint(obj: Any) -> str:
    """obj のモディファイアスタック状態の決定的フィンガープリント（drift 検証用）。

    型別の主要プロパティ込み（list_modifiers）でハッシュするため、名前のみの
    object_fingerprint より param 変化に敏感。add/remove/list の drift 検証に使う。
    """
    return _digest16({"name": obj.name, "modifiers": list_modifiers(obj)})
