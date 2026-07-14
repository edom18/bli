"""transform / apply-transform / set-origin ハンドラ（ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

from typing import Any

from ..handlers import ServerInfo
from ._shared import _check_mode, _command, _guard_shared_mesh, _ok, _require_input, _validate


def _transform(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("transform")
    _validate(cmd, params)
    # 変更チャンネル皆無は無音 no-op + 空 undo になるため弾く（apply-transform と整合）。
    _require_input(
        any(k in params for k in ("location", "rotation", "scale")),
        symptom="transform に変更するチャンネルがありません",
        remediation="--location/--rotation/--scale のいずれかを指定してください",
    )
    from .. import gateway  # lazy: bpy 依存

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

    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    # 破壊的（mesh データへ焼き込む）。共有 mesh は set-origin と同様にガードする。
    _guard_shared_mesh(gateway, obj, params)
    data = gateway.apply_transform(
        obj, location=loc, rotation=rot, scale=scl, message="apply-transform"
    )
    return _ok("apply-transform", data, fingerprint=gateway.object_fingerprint(obj))


def _set_origin(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("set-origin")
    _validate(cmd, params)
    from .. import gateway  # lazy: bpy 依存

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
