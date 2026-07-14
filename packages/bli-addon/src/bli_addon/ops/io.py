"""多形式 import/export・.blend save/open ハンドラ（ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。`_file_sha256_size` は複数ドメイン
利用（print3d.py の `_print_export` からも呼ばれる）のため `_shared.py` へ集約済み。
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
    _ok,
    _ok_offload,
    _require_input,
    _validate,
)

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

    from .. import gateway  # lazy: bpy 依存

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

    from .. import gateway  # lazy: bpy 依存

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
    # USER_INPUT として bpy 到達前に弾く（絶対パスで判定＝operator と同じファイルを見る・import と同流儀）。
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

    from .. import gateway  # lazy: bpy 依存

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
    from .. import session_state

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

    from .. import gateway  # lazy: bpy 依存

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
