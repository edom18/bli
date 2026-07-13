"""mesh 編集ハンドラ（bmesh 系 op ディスパッチ・ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

from typing import Any

from bli_core.errors import RPC_METHOD_NOT_FOUND
from bli_core.protocol import JsonRpcError

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

    from .. import bmesh_ops, gateway  # lazy: bpy 依存

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
