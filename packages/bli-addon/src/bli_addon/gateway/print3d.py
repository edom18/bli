"""BpyGateway 3Dプリンタ対応: 単位設定・print3d 能力検出（gateway/ 分割 P2-4）。

元 gateway.py の該当セクションをそのまま移設（挙動変更なし）。print-export（STL 等）は io.py。
"""

from __future__ import annotations

from typing import Any

import bpy  # type: ignore

from bli_core.errors import ErrorCategory, ErrorCode

from .core import _digest16, _op_error, _unit_settings_dict, push_undo

# ---- 3Dプリンタ対応（M8 T8.3 / print-setup・シナリオ3）----
#
# print-setup はシーンの **表示単位**（unit_settings.system/length_unit）を mm/m に設定する。
# length_unit は表示専用で geometry（dimensions）を再スケールしない＝**非破壊**（研究 §E5）。
# mesh データを触らないため共有 mesh ガード不要。実寸の export スケールは print-export（T8.5）が
# scale_length/単位から一本で算出する方針（global_scale 一本化）。

_UNIT_LENGTH = {"mm": "MILLIMETERS", "m": "METERS"}


def require_scene(name: str | None) -> Any:
    """シーンを解決する（name=完全名 / 省略=active）。無ければ E_TARGET_NOT_FOUND。"""
    if name is None:
        return bpy.context.scene
    scene = bpy.data.scenes.get(name)
    if scene is None:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"シーンが見つかりません: {name}",
            category=ErrorCategory.USER_INPUT,
        )
    return scene


def set_print_units(
    unit: str, *, scene_name: str | None = None, message: str | None = None
) -> dict[str, Any]:
    """シーンの表示単位を mm/m に設定する（system=METRIC + length_unit・geometry 非破壊）。

    length_unit は表示専用で頂点/寸法を再スケールしない（研究 §E5）。changed は設定前後で
    system/length_unit が変わったか（冪等性の指標・既に mm なら False）。
    """
    scene = require_scene(scene_name)
    us = scene.unit_settings
    before = (us.system, us.length_unit)
    us.system = "METRIC"
    us.length_unit = _UNIT_LENGTH[unit]
    changed = (us.system, us.length_unit) != before
    if message:
        push_undo(message)
    return {
        "scene": scene.name,
        "unit": unit,
        "unit_settings": _unit_settings_dict(us),
        "changed": changed,
    }


def unit_settings_fingerprint(unit_settings: dict[str, Any]) -> str:
    """単位設定の決定的フィンガープリント（print-setup の drift 検証用）。"""
    return _digest16(unit_settings)


# ---- print3d 能力検出（M8 T8.4 / thin/intersect は print3d 依存・研究 §E6）----
#
# print3d Toolbox は両版とも実体なし（§E6）。manifold/normals/degenerate は bmesh 自前で計算する
# （print3d 非依存・bmesh_ops.mesh_check）。thin（薄壁）/ intersect（自己交差）のみ print3d 依存で、
# 不在時は ops 側が CAPABILITY_UNAVAILABLE を返す。将来 Extensions で導入された場合のみ True になる。

_PRINT3D_ENABLE_CANDIDATES = (
    "object_print3d_utils",
    "print3d_toolbox",
    "bl_ext.blender_org.print3d_toolbox",
)
_PRINT3D_CHECK_OP = "mesh.print3d_check_all"


def print3d_available() -> bool:
    """print3d Toolbox の能力を検出する（未導入なら enable 試行→不可なら False）。

    operator が既に実在すれば True。無ければ候補 module を `addon_utils.enable` で試行し、
    実在判定（`get_rna_type`）し直す。§E6 でこの環境（5.0.1/4.4.3）では module 自体が無く常に False。
    """
    from .. import capability  # lazy: operator_real（bpy 依存）

    if capability.operator_real(_PRINT3D_CHECK_OP):
        return True
    import addon_utils  # type: ignore  # lazy: bpy 依存

    for mod in _PRINT3D_ENABLE_CANDIDATES:
        try:
            addon_utils.enable(mod, default_set=False, persistent=False)
        except Exception:
            continue
        if capability.operator_real(_PRINT3D_CHECK_OP):
            return True
    return False
