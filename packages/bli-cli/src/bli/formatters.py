"""コマンド別 human フォーマッタの登録表（P2-2）。

手書き時代に main.py の各コマンド内クロージャだった human() を、コマンド名 →
Callable[[data], str] の表として集約する。cli_factory が生成コマンドに紐付け、
未登録コマンドは JSON 整形へフォールバックする（report §4 P2-2）。

新コマンドで human 表示を整えたいときは、ここに関数を足して HUMAN_FORMATTERS へ
登録するだけでよい（登録しなくても動く）。本文は手書き時代の文言を verbatim 維持
（behavior スナップショットの比較対象）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _watchdog_suffix(data: dict[str, Any]) -> str:
    """request-status/job-status の human 出力に付けるメインスレッド応答性の注記（M10 T10.3）。

    応答中（または watchdog 情報なし）は空文字。固まっている場合のみ注記を返す（実行は継続中＝
    重量ネイティブ処理が固めている可能性をエージェントに可視化する）。
    """
    wd = data.get("watchdog")
    if not isinstance(wd, dict) or wd.get("responsive", True):
        return ""
    age = wd.get("last_pump_age")
    age_s = f"{age:.0f}s" if isinstance(age, (int, float)) else "?"
    return f"  ⚠ メインスレッド応答なし（{age_s} 停止・重量処理で固まっている可能性／実行は継続中）"


def _human_scene_info(data: dict[str, Any]) -> str:
    names = ", ".join(o["name"] for o in data.get("objects", []))
    return f"scene '{data.get('scene')}': {data.get('object_count')} objects [{names}]"


def _human_list_objects(data: dict[str, Any]) -> str:
    objs = data.get("objects", [])
    names = ", ".join(f"{o['name']}({o['type']})" for o in objs)
    return f"{data.get('count', len(objs))} objects [{names}]"


def _human_object_info(data: dict[str, Any]) -> str:
    return (
        f"{data.get('name')} ({data.get('type')}): "
        f"loc={data.get('location')} dims={data.get('dimensions')}"
    )


def _human_set_origin(data: dict[str, Any]) -> str:
    return f"origin of {data.get('name')} -> {data.get('to')} @ {data.get('origin_world')}"


def _human_straighten(data: dict[str, Any]) -> str:
    m = data.get("method")
    prefix = "[dry-run] " if data.get("dry_run") else ""
    head = f"{prefix}straighten {data.get('name')} [{m}] up={data.get('up_axis')}"
    if m == "floor":
        return f"{head}: grounded min_up={data.get('min_up')} offset={data.get('floor_offset')}"
    if m in ("world-align", "reference"):
        ref = f" ref={data.get('reference')}:{data.get('ref_axis')}" if m == "reference" else ""
        return (
            f"{head}: axis={data.get('axis')}{ref} -> {data.get('aligned_world')} "
            f"rot={data.get('rotation_euler_deg')}"
        )
    if m == "pca":
        return (
            f"{head}: tilt={data.get('tilt_from_up_deg')}deg "
            f"principal -> {data.get('principal_world_after')} "
            f"rot={data.get('rotation_euler_deg')}"
        )
    if m == "angle":
        return (
            f"{head}: axis={data.get('axis')} degrees={data.get('degrees')} "
            f"rot={data.get('rotation_euler_deg')} baked={data.get('baked')}"
        )
    if m == "align-vector":
        return (
            f"{head}: {data.get('from_dir')} -> {data.get('from_world_after')} "
            f"angle={data.get('angle_deg')}deg rot={data.get('rotation_euler_deg')} "
            f"baked={data.get('baked')}"
        )
    return f"{head}: rot={data.get('rotation_euler_deg')} baked={data.get('baked')}"


def _human_capture(data: dict[str, Any]) -> str:
    cam = f" camera={data['camera']}" if data.get("camera") else ""
    return (
        f"capture [{data.get('source')}]{cam} {data.get('width')}x{data.get('height')} "
        f"-> {data.get('path')} ({data.get('size')}B)"
    )


def _human_undo(data: dict[str, Any]) -> str:
    return f"undo: requested={data.get('requested')} applied={data.get('applied')}"


def _human_redo(data: dict[str, Any]) -> str:
    return f"redo: requested={data.get('requested')} applied={data.get('applied')}"


def _human_print_setup(data: dict[str, Any]) -> str:
    us = data.get("unit_settings") or {}
    return (
        f"scene '{data.get('scene')}' unit={data.get('unit')} "
        f"(system={us.get('system')} length_unit={us.get('length_unit')} "
        f"changed={data.get('changed')})"
    )


def _human_print_check(data: dict[str, Any]) -> str:
    c = data.get("checks") or {}
    # 報告されたカテゴリのキーのみ並べる（未要求カテゴリで None を出さない）。
    detail = " ".join(f"{k}={v}" for k, v in c.items() if k != "is_printable")
    return f"{data.get('name')} printable={c.get('is_printable')} {detail}".rstrip()


def _human_print_repair(data: dict[str, Any]) -> str:
    fixed = data.get("fixed") or {}
    after = data.get("after") or {}
    return (
        f"repaired {data.get('name')} applied={data.get('applied')} "
        f"fixed_non_manifold={fixed.get('non_manifold_edges')} "
        f"printable={after.get('is_printable')}"
    )


def _human_print_export(data: dict[str, Any]) -> str:
    return (
        f"exported {data.get('name')} [{data.get('format')}] -> {data.get('path')} "
        f"({data.get('size')}B, {data.get('triangles')} tris, scale={data.get('global_scale')})"
    )


def _human_export(data: dict[str, Any]) -> str:
    scope = (
        f"objects={data.get('exported_objects')}"
        if data.get("exported_objects") is not None
        else "whole scene"
    )
    fbx_opts = data.get("fbx_options")
    opts = f" fbx_options={fbx_opts}" if fbx_opts else ""
    return (
        f"exported [{data.get('format')}] {scope} -> {data.get('path')} "
        f"({data.get('size')}B, sha={str(data.get('sha256'))[:12]}){opts}"
    )


def _human_import(data: dict[str, Any]) -> str:
    names = [o.get("name") for o in (data.get("imported") or [])]
    return f"imported [{data.get('format')}] {data.get('count')}: {names}"


def _human_save(data: dict[str, Any]) -> str:
    bk = f" backup={data.get('backup_path')}" if data.get("backed_up") else ""
    return f"saved -> {data.get('path')} ({data.get('size')}B){bk}"


def _human_open(data: dict[str, Any]) -> str:
    disc = " (discarded unsaved)" if data.get("discarded_unsaved") else ""
    return (
        f"opened -> {data.get('path')} scene={data.get('scene')} "
        f"objects={data.get('object_count')}{disc}"
    )


def _human_exec_python(data: dict[str, Any]) -> str:
    parts = ["exec ok（security_guarantee=false・サンドボックスなし）"]
    out = (data.get("stdout") or "").rstrip()
    if out:
        parts.append(out)
    err = (data.get("stderr") or "").rstrip()
    if err:
        parts.append(f"[stderr] {err}")
    if data.get("result_repr") is not None:
        parts.append(f"=> {data.get('result_repr')}")
    flags = data.get("heuristic_flags") or []
    if flags:
        parts.append(f"[heuristic_flags] {', '.join(flags)}（注意喚起・ブロックはしない）")
    if data.get("audit_ok") is False:
        parts.append("[warn] 監査ログの書き込みに失敗しました（証跡が残っていません）")
    return "\n".join(parts)


def _human_select(data: dict[str, Any]) -> str:
    return f"selected {data.get('count')}: {data.get('selected')} active={data.get('active')}"


def _human_transform(data: dict[str, Any]) -> str:
    return (
        f"{data.get('name')}: loc={data.get('location')} "
        f"rot={data.get('rotation_euler_deg')} scale={data.get('scale')}"
    )


def _human_apply_transform(data: dict[str, Any]) -> str:
    return f"applied to {data.get('name')}: scale={data.get('scale')} dims={data.get('dimensions')}"


def _human_duplicate(data: dict[str, Any]) -> str:
    return f"duplicated '{data.get('source')}' -> {data.get('created')} (count={data.get('count')})"


def _human_delete(data: dict[str, Any]) -> str:
    bk = data.get("backup") or {}
    return (
        f"deleted '{data.get('deleted')}' (backup: type={bk.get('type')} loc={bk.get('location')})"
    )


def _human_material(data: dict[str, Any]) -> str:
    if data.get("action") == "list":
        slots = ", ".join(
            f"{m['slot']}:{m['name']}={m['base_color']}" for m in data.get("materials", [])
        )
        return f"{data.get('name')} materials [{slots}]"
    return (
        f"{data.get('action')} '{data.get('material')}' -> "
        f"{data.get('name')} slot={data.get('slot')}"
    )


def _human_modifier(data: dict[str, Any]) -> str:
    if data.get("action") == "list":
        mods = ", ".join(f"{m['name']}({m['type']})" for m in data.get("modifiers", []))
        return f"{data.get('name')} modifiers [{mods}]"
    if data.get("action") == "add":
        m = data.get("modifier") or {}
        return f"added {m.get('type')} '{m.get('name')}' to {data.get('name')}"
    if data.get("action") == "apply":
        return f"applied '{data.get('applied')}' to {data.get('name')}"
    return f"removed '{data.get('removed')}' from {data.get('name')}"


def _human_add(data: dict[str, Any]) -> str:
    return f"added {data.get('type')}: {data.get('name')} loc={data.get('location')}"


def _human_mode(data: dict[str, Any]) -> str:
    return f"mode {data.get('from_mode')} -> {data.get('to_mode')} (active={data.get('active')})"


def _human_rename(data: dict[str, Any]) -> str:
    return (
        f"renamed '{data.get('old_name')}' -> '{data.get('new_name')}' "
        f"(data_renamed={data.get('data_renamed')})"
    )


def _human_parent(data: dict[str, Any]) -> str:
    results = data.get("results") or []
    summary = ", ".join(f"{r['name']}->{r['parent']}" for r in results)
    return f"parent {data.get('action')}: {summary}"


def _human_collection(data: dict[str, Any]) -> str:
    if data.get("action") == "list":
        cols = ", ".join(f"{c['name']}({c['objects']})" for c in data.get("collections", []))
        return f"collections [{cols}]"
    if data.get("action") == "create":
        return f"created collection '{data.get('name')}'"
    results = data.get("results") or []
    names = ", ".join(r["name"] for r in results)
    return f"{data.get('action')} '{data.get('collection')}': {names}"


def _human_mesh(data: dict[str, Any]) -> str:
    op_ = data.get("op")
    if op_ == "recalc-normals":
        return (
            f"{data.get('name')} recalc-normals: faces={data.get('faces')} "
            f"flipped={data.get('flipped')} inside={data.get('inside')}"
        )
    if op_ == "merge-by-distance":
        return (
            f"{data.get('name')} merge-by-distance: merged={data.get('merged')} "
            f"({data.get('before')}→{data.get('after')})"
        )
    # extrude / bevel / inset / boolean / decimate: ジオメトリ増減（符号付き）+ 結果統計。
    delta = data.get("delta") or {}
    st = data.get("stats") or {}

    def _signed(n: Any) -> str:
        return f"{n:+d}" if isinstance(n, int) else str(n)

    prefix = f"{data.get('name')} {op_}"
    if op_ == "boolean":
        prefix += f" ({data.get('operation')} with {data.get('with_object')})"
    elif op_ == "decimate":
        prefix += f" (ratio={data.get('ratio')})"
    return (
        f"{prefix}: "
        f"{_signed(delta.get('vertices'))}v/{_signed(delta.get('edges'))}e/"
        f"{_signed(delta.get('polygons'))}f → "
        f"{st.get('vertices')}v/{st.get('edges')}e/{st.get('polygons')}f"
    )


def _human_request_status(data: dict[str, Any]) -> str:
    base = f"id={data.get('id')} state={data.get('state')} known={data.get('known')}"
    return base + _watchdog_suffix(data)


def _human_job_status(data: dict[str, Any]) -> str:
    base = f"job_id={data.get('id')} state={data.get('state')} known={data.get('known')}"
    return base + _watchdog_suffix(data)


HUMAN_FORMATTERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "scene-info": _human_scene_info,
    "list-objects": _human_list_objects,
    "object-info": _human_object_info,
    "set-origin": _human_set_origin,
    "straighten": _human_straighten,
    "capture": _human_capture,
    "undo": _human_undo,
    "redo": _human_redo,
    "print-setup": _human_print_setup,
    "print-check": _human_print_check,
    "print-repair": _human_print_repair,
    "print-export": _human_print_export,
    "export": _human_export,
    "import": _human_import,
    "save": _human_save,
    "open": _human_open,
    "exec-python": _human_exec_python,
    "select": _human_select,
    "transform": _human_transform,
    "apply-transform": _human_apply_transform,
    "duplicate": _human_duplicate,
    "delete": _human_delete,
    "material": _human_material,
    "modifier": _human_modifier,
    "add": _human_add,
    "mode": _human_mode,
    "rename": _human_rename,
    "parent": _human_parent,
    "collection": _human_collection,
    "mesh": _human_mesh,
    "request-status": _human_request_status,
    "job-status": _human_job_status,
}
