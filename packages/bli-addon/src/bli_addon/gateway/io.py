"""BpyGateway 入出力: 多形式 import/export + print-export（STL）（gateway/ 分割 P2-4）。

元 gateway.py の該当セクションをそのまま移設（挙動変更なし）。print-export と多形式 export は
相互依存（_select_only/_select_set/_restore_selection を共有）のため同一ファイルに保つ。
"""

from __future__ import annotations

from typing import Any

import bpy  # type: ignore

from bli_core.errors import ErrorCategory, ErrorCode
from bli_core.protocol import JsonRpcError

from .core import _op_error, _resolve_op, run_operator
from .objects import _regex_match_hint, resolve_targets

# ---- 3Dプリンタ出力（M8 T8.5 / print-export・シナリオ3 / 研究 §E8）----
#
# STL は `wm.stl_export`（M0.5/§E8 確定・両版同一引数）。対象1個だけを選択して
# export_selected_objects=True で対象限定し、world 空間でジオメトリを焼いて出力する。
# スケールは `global_scale` 一本化（use_scene_unit=False 固定で scale_length を出力へ反映させない
# ＝1000倍ずれ防止）。選択/active は save→restore で非破壊（print-export は mutates=False）。
# 3MF は両版とも export operator が実体なし（§E8）→ resolve_export_operator が None を返し、呼び出し側
# （ops）が CAPABILITY_UNAVAILABLE + STL hint へ縮退する（黙って STL に差し替えない）。


def resolve_export_operator(fmt: str) -> str | None:
    """`export.<fmt>` の実在 export operator を能力検出で解決する（無ければ None）。

    解決ロジックは `CapabilityRegistry.resolve`（RESOLVERS 候補表＝spec §9 OperatorResolver の単一窓口・
    M0.5 確定）へ委譲する（候補ループを二重実装しない）。stl は `wm.stl_export`、3mf は候補
    `export_mesh.3mf` が両版とも stub のため None（§E8）。
    """
    from .. import capability  # lazy: bpy 依存

    return capability.CapabilityRegistry().resolve(f"export.{fmt}")


def resolve_import_operator(fmt: str) -> str | None:
    """`import.<fmt>` の実在 import operator を能力検出で解決する（無ければ None）。

    export と対称に `CapabilityRegistry.resolve`（RESOLVERS 候補表）へ委譲する。FBX import の唯一の
    版差（5.0=`wm.fbx_import` / 4.4=`import_scene.fbx`）は RESOLVERS の候補優先順で吸収する（§E9）。
    3mf は候補 `import_mesh.3mf` が両版とも stub のため None（§E8）。
    """
    from .. import capability  # lazy: bpy 依存

    return capability.CapabilityRegistry().resolve(f"import.{fmt}")


def import_generic(fmt: str, operator_path: str, path: str) -> list[dict[str, str]]:
    """多形式 import（前後 diff で取込特定・§E9）。取込オブジェクトの {name, type} 要約を返す。

    import 前後の `bpy.data.objects` 名集合の差分で取り込んだオブジェクトを特定する（名前衝突時に
    Blender が `.001` 等へリネームするため、集合差が唯一信頼できる方式）。生 operator は run_operator
    経由（AST guard 緑）。シーンを変える破壊的操作なので message を渡して undo 境界を作る。
    """
    before = {o.name for o in bpy.data.objects}
    op = _resolve_op(operator_path)
    try:
        run_operator(op, filepath=path, message=f"import-{fmt}")
    except JsonRpcError:
        raise  # run_operator が既に業務エラー（E_OPERATOR/E_PRECONDITION）へ写像済み＝そのまま伝播
    except Exception as e:
        # glTF importer 等は Python 実装で、壊れた入力に RuntimeError 以外（KeyError/struct.error/
        # JSONDecodeError 等）を投げ得る。run_operator の RuntimeError 限定 catch を漏れて INTERNAL
        # 化するのを防ぎ、入力起因のエラーとして E_OPERATOR に写像する（§6e: USER 起因を INTERNAL に
        # しない）。run_operator 由来の JsonRpcError は上で再送出済みなので、ここは operator 内部例外のみ。
        raise _op_error(
            ErrorCode.E_OPERATOR,
            f"import に失敗しました（ファイル内容/形式を確認してください）: {type(e).__name__}: {e}",
        ) from e
    imported = [o for o in bpy.data.objects if o.name not in before]
    return [{"name": o.name, "type": o.type} for o in sorted(imported, key=lambda x: x.name)]


def current_filepath() -> str:
    """現在開いている .blend のパス（未保存は空文字・save の --path 省略時の解決に使う）。"""
    return bpy.data.filepath


def save_blend(path: str, *, backup: bool) -> None:
    """現在のシーンを .blend に保存する（wm.save_as_mainfile・研究 §E10）。

    backup=True なら上書き時に `<name>.blend1` を残す。Blender の native backup は preferences
    `save_version`（既定 1）依存のため、決定的に制御するよう **`save_version` を一時上書き
    （1 if backup else 0）→ try/finally で restore** する（preference 非汚染・backup naming は
    Blender 標準の `<name>.blend1`）。check_existing=False で既存上書き可。message なし＝undo 不要。
    注: save_version はプロセスグローバル設定。この一時上書きはサーバがリクエストを逐次処理する
    （save と他コマンドが同時に走らない＝同時接続は SESSION_BUSY で fail-fast）前提で安全。
    """
    prefs = bpy.context.preferences.filepaths
    saved_version = prefs.save_version
    try:
        prefs.save_version = 1 if backup else 0
        try:
            run_operator(bpy.ops.wm.save_as_mainfile, filepath=path, check_existing=False)
        except JsonRpcError:
            raise  # run_operator 内で写像済み（poll 不可 / FINISHED 以外 / RuntimeError）
        except Exception as e:
            # ディスク満杯/権限エラー（OSError）等も入力起因 → E_OPERATOR に写像し INTERNAL 化を
            # 防ぐ（open_blend と同流儀・設計レビュー 2026-07-11 B4）。
            raise _op_error(
                ErrorCode.E_OPERATOR,
                f".blend を保存できませんでした（パス/空き容量/権限を確認してください）: "
                f"{type(e).__name__}: {e}",
            ) from e
    finally:
        prefs.save_version = saved_version


def open_blend(path: str) -> dict[str, Any]:
    """指定 .blend を開く（wm.open_mainfile・シーン全体を置換・研究 §E11）。開いた要約を返す。

    `run_operator` は使わない: ①open はシーン全体を差し替えるため `temp_override` 下で実行すると
    override 対象（active/selected）が無効化され `with` の teardown で壊れ得る ②load は undo 境界
    （push_undo）も不要。実機スパイク（open_spike）でも素の `open_mainfile` を採用している。
    常駐サーバの **persistent pump タイマ / "bli-accept" TCP スレッドは open を跨いで生存する**ため
    再登録は不要（§E11 で両版実機確定）。壊れ/ロック .blend は RuntimeError 以外（OSError 等）も投げ得る
    ため **`except Exception` で E_OPERATOR に写像**し INTERNAL 化しない（§6e・import_generic と同流儀・
    存在チェックは ops 側が bpy 到達前に済ませている）。
    """
    try:
        result = bpy.ops.wm.open_mainfile(filepath=path)
    except Exception as e:  # RuntimeError/OSError/MemoryError 等いずれも入力起因 → E_OPERATOR
        raise _op_error(
            ErrorCode.E_OPERATOR,
            f".blend を開けませんでした（ファイル内容を確認してください）: {type(e).__name__}: {e}",
        ) from e
    if "FINISHED" not in result:
        raise _op_error(ErrorCode.E_OPERATOR, f"open が完了しませんでした: {sorted(result)}")
    scene = bpy.context.scene
    return {
        "filepath": bpy.data.filepath,
        "scene": scene.name if scene is not None else None,
        "object_count": len(bpy.data.objects),
    }


def _select_only(obj: Any) -> tuple[list[Any], Any]:
    """obj だけを選択し active にする（単体専用・`_select_set([obj])` への薄い委譲）。

    `wm.stl_export(export_selected_objects=True)` は **永続化された view layer の選択フラグ**を見るため、
    run_operator の `temp_override(selected_objects=[obj])` だけでは対象を絞れない（§E8）。実選択を
    一時的に書き換え `_restore_selection` で厳密に戻す（mutates=False を保つ）。選択ロジックの真実は
    `_select_set` に一本化し、print-export(単体) と export(多形式) で二重実装が drift しないようにする。
    """
    return _select_set([obj])


def _restore_selection(saved_selected: list[Any], saved_active: Any) -> None:
    """_select_only/_select_set で退避した選択/active を厳密に復元する（非破壊）。"""
    view_layer = bpy.context.view_layer
    for o in view_layer.objects:
        o.select_set(False)
    for o in saved_selected:
        try:
            o.select_set(True)
        except RuntimeError:  # 復元中に view layer から消えた等は無視（best-effort 復元）
            pass
    view_layer.objects.active = saved_active


def export_stl(
    obj: Any,
    path: str,
    *,
    ascii_format: bool = False,
    global_scale: float = 1.0,
    apply_modifiers: bool = True,
) -> dict[str, Any]:
    """対象 obj 1個を STL で書き出す（wm.stl_export・world 焼き・global_scale 一本化）。

    対象だけを選択して export_selected_objects=True で対象限定し、選択は save→restore で非破壊に
    戻す。use_scene_unit=False 固定で scale_length を出力へ反映させず、スケールは global_scale のみで
    支配する（§E8・1000倍ずれ防止）。check_existing=False で既存ファイルを上書き可能にする。
    返すのは export パラメータ + 検証用の scale_length（ファイル統計は呼び出し側 ops が付与）。
    """
    saved_selected, saved_active = _select_only(obj)
    try:
        run_operator(
            bpy.ops.wm.stl_export,
            obj,
            filepath=path,
            export_selected_objects=True,
            ascii_format=ascii_format,
            global_scale=global_scale,
            use_scene_unit=False,
            apply_modifiers=apply_modifiers,
            check_existing=False,
        )
    finally:
        _restore_selection(saved_selected, saved_active)
    return {
        "format": "stl",
        "ascii": ascii_format,
        "global_scale": round(float(global_scale), 8),
        "apply_modifiers": apply_modifiers,
        # scale_length は検証専用（出力には use_scene_unit=False で未反映）。1000倍ずれ設定の検知材料。
        "scale_length": round(bpy.context.scene.unit_settings.scale_length, 8),
    }


# ---- 多形式 export（M9 T9.1・print-export の STL 限定を一般化・研究 §E9）----
#
# 形式 -> selection 制御 param 名（§E9 実機確定・5.0/4.4 同一）。stl/obj は export_selected_objects、
# gltf/fbx は use_selection。これが「print-export(STL 単体)を多形式へ広げる」核（形式別引数マップ）。
_EXPORT_SELECTION_PARAM: dict[str, str] = {
    "stl": "export_selected_objects",
    "obj": "export_selected_objects",
    "gltf": "use_selection",
    "fbx": "use_selection",
}


def require_targets(selector: str, *, regex: bool = False) -> list[Any]:
    """対象を1つ以上に解決する。0件はエラー（複数は許容＝export 等の集合操作向け・require_single の緩和版）。"""
    found = resolve_targets(selector, regex=regex)
    if not found:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"対象が見つかりません: {selector}{_regex_match_hint(selector, regex=regex)}",
            category=ErrorCategory.USER_INPUT,
        )
    return found


def current_selection() -> list[Any]:
    """アクティブ view layer で現在選択されているオブジェクト群（export --use-selection 用）。"""
    return [o for o in bpy.context.view_layer.objects if o.select_get()]


def _select_set(objs: list[Any]) -> tuple[list[Any], Any]:
    """objs 群だけを選択し先頭を active にする。元の (selected, active) を返す（restore 用）。

    export_selected_objects/use_selection は永続化された view layer の選択フラグを見るため
    temp_override では絞れない（§E8・_select_only と同理由）。これは _select_only の複数対象版。
    対象がアクティブ view layer に無ければ E_PRECONDITION（INTERNAL 回避）。
    """
    view_layer = bpy.context.view_layer
    vl_names = {o.name for o in view_layer.objects}
    missing = [o.name for o in objs if o.name not in vl_names]
    if missing:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"対象がアクティブ view layer にありません（export 不可）: {', '.join(missing[:5])}",
        )
    saved_selected = [o for o in view_layer.objects if o.select_get()]
    saved_active = view_layer.objects.active
    for o in view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    view_layer.objects.active = objs[0]
    return saved_selected, saved_active


# fbx_options（bli 側キー）-> export_scene.fbx operator の実プロパティ名（P1-3・単一の写像表）。
# 両版実機確定（axis_forward/axis_up/global_scale/apply_unit_scale/embed_textures は 5.0.1/4.4.3
# で完全同一の rna properties・§E9 の RESOLVERS 確定と同じ確認手順）。
_FBX_OPTION_TO_PROP: dict[str, str] = {
    "axis_forward": "axis_forward",
    "axis_up": "axis_up",
    "scale": "global_scale",
    "apply_unit_scale": "apply_unit_scale",
    "embed_textures": "embed_textures",
}


def _fbx_operator_kwargs(fbx_options: dict[str, Any], available_props: set[str]) -> dict[str, Any]:
    """export --format fbx の fbx_options を export_scene.fbx operator の kwargs へ写像する。

    純関数（bpy 非依存・L1 テスト可能）。embed_textures=True のときは path_mode='COPY' も自動付与
    する（Blender は path_mode が COPY 以外だと embed_textures を無視する仕様のため・実機確認済み）。
    写像先の operator プロパティが available_props に無いキーが1つでもあれば KeyError(prop) を送出
    する（silent drop 禁止。将来 operator が差し替わって当該プロパティが消えても黙って無視しない・
    呼び出し側の export_generic が CAPABILITY_UNAVAILABLE へ変換する）。
    """
    kwargs: dict[str, Any] = {}
    for key, value in fbx_options.items():
        prop = _FBX_OPTION_TO_PROP[key]
        if prop not in available_props:
            raise KeyError(prop)
        kwargs[prop] = value
    if fbx_options.get("embed_textures") is True:
        if "path_mode" not in available_props:
            raise KeyError("path_mode")
        kwargs["path_mode"] = "COPY"
    return kwargs


def export_generic(
    fmt: str,
    operator_path: str,
    path: str,
    *,
    select_objs: list[Any] | None,
    fbx_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """多形式 export（print-export の STL 限定を一般化・§E9）。

    select_objs=None はシーン全体（selection param=False）/ list は対象のみ（対象を選択して param=True・
    選択は save→restore で非破壊に戻す）。生 operator は run_operator 経由（AST guard 緑）。scale は
    素通し（global_scale 等は渡さない＝既定 1.0・print-export が 3D プリント用 scale 窓口・gltf は
    scale param 自体が無い）。選択は context override にも全集合を載せる（stl/obj は永続選択を、
    gltf/fbx が override を読む場合も全対象が渡るよう belt-and-suspenders）。
    glTF は **GLB 単一固定**（`export_format` の有効値は両版とも ('GLB','GLTF_SEPARATE') のみ＝
    GLTF_EMBEDDED は存在しない・実機確認済み。SEPARATE は .bin 分離で sha256/size が崩れるため不採用）。
    .glb 拡張子の要求は ops 側で bpy 到達前に検証する。

    fbx_options（P1-3・Unity 取込向け）は format=fbx のときだけ ops 側が渡す（他 format は None）。
    指定時のみ `op.get_rna_type().properties` で rna 検査する（通常経路のコストを増やさない）。
    """
    op = _resolve_op(operator_path)
    sel_param = _EXPORT_SELECTION_PARAM[fmt]
    kwargs: dict[str, Any] = {"filepath": path, "check_existing": False}
    if fmt == "gltf":
        kwargs["export_format"] = "GLB"
    if fbx_options:
        available_props = set(op.get_rna_type().properties.keys())
        try:
            kwargs.update(_fbx_operator_kwargs(fbx_options, available_props))
        except KeyError as e:
            raise _op_error(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                f"この Blender の FBX exporter は {e.args[0]} に未対応です",
                category=ErrorCategory.ENVIRONMENT,
            ) from e

    if select_objs is None:
        kwargs[sel_param] = False
        run_operator(op, **kwargs)
        exported = None
    else:
        saved_selected, saved_active = _select_set(select_objs)
        kwargs[sel_param] = True
        extra = {
            "active_object": select_objs[0],
            "object": select_objs[0],
            "selected_objects": list(select_objs),
            "selected_editable_objects": list(select_objs),
        }
        try:
            run_operator(op, extra_override=extra, **kwargs)
        finally:
            _restore_selection(saved_selected, saved_active)
        exported = sorted(o.name for o in select_objs)
    result: dict[str, Any] = {
        "format": fmt,
        "operator": operator_path,
        "use_selection": select_objs is not None,
        "exported_objects": exported,
    }
    if fbx_options:
        result["fbx_options"] = dict(fbx_options)
    return result
