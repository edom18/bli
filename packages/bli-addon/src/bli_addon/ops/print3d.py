"""3D プリント準備ハンドラ（単位設定/健全性チェック/修復/STL export・ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。`_file_sha256_size` は複数ドメイン
利用（io.py の `_export` からも呼ばれる）のため `_shared.py` へ集約済み（P2-4 分割方針）。
"""

from __future__ import annotations

from typing import Any

from bli_core.errors import RPC_BUSINESS_ERROR, ErrorCategory, ErrorCode, make_error
from bli_core.protocol import JsonRpcError

from ..handlers import ServerInfo
from ._shared import (
    _capability_unavailable,
    _check_mode,
    _command,
    _file_sha256_size,
    _guard_shared_mesh,
    _ok,
    _ok_offload,
    _require_input,
    _validate,
)


def _print_setup(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("print-setup")
    _validate(cmd, params)
    from .. import gateway  # lazy: bpy 依存

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

    from .. import bmesh_ops, gateway  # lazy: bpy 依存

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

    from .. import bmesh_ops, gateway  # lazy: bpy 依存

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

    from .. import gateway  # lazy: bpy 依存

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
