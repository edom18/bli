"""シーン情報・生成/モード切替/親子・コレクション・undo/redo ハンドラ（ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..handlers import ServerInfo
from ._shared import _check_mode, _command, _ok, _ok_offload, _require_input, _validate


def _scene_info(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("scene-info")
    _validate(cmd, params)
    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    data = gateway.scene_summary(int(params.get("depth", 1)))
    return _ok_offload("scene-info", data, "scene-info/v1")


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

    from .. import gateway  # lazy: bpy 依存

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

    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    targets = params.get("targets")
    data = gateway.set_object_mode(
        to,
        targets=str(targets) if targets is not None else None,
        regex=bool(params.get("regex", False)),
        message=f"mode {to}",
    )
    return _ok("mode", data, fingerprint=gateway.state_fingerprint(data))


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

    from .. import gateway  # lazy: bpy 依存

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

    from .. import gateway  # lazy: bpy 依存

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
    from .. import gateway  # lazy: bpy 依存

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
