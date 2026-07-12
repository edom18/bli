"""SSOT に無い CLI 固有メタデータ（P2-2 オーバーライド表）。

原則: **新コマンドはこの表に書かなくてよい**（cli_factory の既定則で生成される）。
ここに書くのは既存コマンドの互換維持だけ:
- doc: 手書き時代の docstring（--help のコマンド説明）を verbatim 維持
- help_overrides: definitions.py の param help と CLI オプション help の歴史的な文言差
- always_send / tristate: 送信ポリシーの既定則からの逸脱（挙動同一性のため）
- py_names / option_names: click パラメータ名・オプション別名の互換
- pre_hook / build: 送信前の手書きバリデーション（範囲チェック・排他・ファイル読込）

文言差を SSOT 側へ寄せる（definitions.py の help を CLI と統一する）のは schema_hash が
変わるため、挙動不変が要件の P2-2 では行わない（フォローアップ候補）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer

from bli_core.errors import ErrorCode, ExitCode

# 手書き維持するコマンド（SSOT に定義はあるが factory では生成しない）。
# ping/doctor は hello 由来の独自 payload、init/policy は RPC を送らない CLI ローカル、
# job-wait は _rpc を経由しない唯一の RPC 系（_await_job 直接呼び出し）。
EXCLUDED_COMMANDS = frozenset({"ping", "doctor", "init", "policy", "job-wait"})

# `bli --help` の一覧順（手書き時代の登録順の生成対象部分）。未掲載の新コマンドは末尾に
# アルファベット順で自動追加される（cli_factory.register_generated_commands）。
GENERATED_ORDER: tuple[str, ...] = (
    "scene-info",
    "list-objects",
    "object-info",
    "set-origin",
    "straighten",
    "capture",
    "undo",
    "redo",
    "print-setup",
    "print-check",
    "print-repair",
    "print-export",
    "export",
    "import",
    "save",
    "open",
    "exec-python",
    "select",
    "transform",
    "apply-transform",
    "duplicate",
    "delete",
    "material",
    "modifier",
    "add",
    "mode",
    "rename",
    "parent",
    "collection",
    "mesh",
    "request-status",
    "job-status",
)

# 全コマンド共通のオプション名既定則からの逸脱（param 名 → オプション名列）
DEFAULT_OPTION_NAMES: dict[str, tuple[str, ...]] = {
    "targets": ("--targets", "--target"),  # 単数別名は全 targets 系コマンド共通
    "with_object": ("--with",),
}

_ID_HELP_LONG = "リクエストID(UUIDv4)。冪等リトライで同一IDを再利用する"
_TARGETS_HELP = "対象オブジェクト（完全一致・--regex で正規表現）"


@dataclass(frozen=True)
class CmdSpec:
    doc: str | None = None
    method: str | None = None  # RPC メソッド名（省略時はコマンド名。job-status→request-status）
    param_order: tuple[str, ...] | None = None  # 省略時は SSOT の定義順
    option_names: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    py_names: Mapping[str, str] = field(default_factory=dict)  # click パラメータ名の互換
    help_overrides: Mapping[str, str] = field(default_factory=dict)
    # SSOT では required/default 無しだが CLI では既定値を持つ param（print-export の format=stl）。
    # 指定 param は「その既定で常時送信・required 扱いしない」になる。
    cli_defaults: Mapping[str, Any] = field(default_factory=dict)
    always_send: frozenset[str] = frozenset()
    tristate: frozenset[str] = frozenset()
    with_request_id: bool | None = None  # 省略時は mutates or is_heavy
    with_fetch: bool = False
    request_id_help: str | None = None
    async_help: str | None = None
    pre_hook: Callable[[dict[str, Any], bool, Any], None] | None = None
    build: Callable[[dict[str, Any], bool, Any], dict[str, Any]] | None = None


# ---- pre_hook / build（手書き時代の送信前バリデーションを移設）----


def _check_steps(kw: dict[str, Any], json_out: bool, ctx: Any) -> None:
    from bli_core import runtime

    steps = kw["steps"]
    if (
        not 1 <= steps <= runtime.MAX_UNDO_STEPS
    ):  # 暴走防止の上限は送信前に弾く（§6e・duplicate と同流儀）
        ctx.emit_error(
            json_out,
            ErrorCode.INVALID_PARAMS,
            f"--steps は 1〜{runtime.MAX_UNDO_STEPS} です: {steps}",
        )
        raise typer.Exit(int(ExitCode.INPUT))


def _check_duplicate_count(kw: dict[str, Any], json_out: bool, ctx: Any) -> None:
    from bli_core import runtime

    count = kw["count"]
    if not 1 <= count <= runtime.MAX_DUPLICATE_COUNT:
        ctx.emit_error(
            json_out,
            ErrorCode.INVALID_PARAMS,
            f"--count は 1〜{runtime.MAX_DUPLICATE_COUNT} です: {count}",
        )
        raise typer.Exit(int(ExitCode.INPUT))


def _check_name_regex_not_option(kw: dict[str, Any], json_out: bool, ctx: Any) -> None:
    name_regex = kw["name_regex"]
    if name_regex is None:
        return
    # `--regex --json` のような値の渡し忘れは click が次のオプションを値として食い、
    # 「0 件の空リスト」という silent 失敗になる（targets 系の値なし `--regex` フラグとの
    # 取り違えで起きやすい）。bli のオプションは全て `--` 始まりなので、`--` 始まりの
    # パターン値は誤用として loud に弾く（本当に `--` で始まる名前は `\-\-` でエスケープ可）。
    if name_regex.startswith("--"):
        ctx.emit_error(
            json_out,
            ErrorCode.INVALID_PARAMS,
            f"--name-regex の値がオプションに見えます: {name_regex!r}"
            "（値の渡し忘れの可能性。パターンが本当に -- で始まる場合は \\-\\- とエスケープ）",
        )
        raise typer.Exit(int(ExitCode.INPUT))


def _build_exec_python(kw: dict[str, Any], json_out: bool, ctx: Any) -> dict[str, Any]:
    code, file = kw["code"], kw["file"]
    # --code / --file は排他（どちらか一方が必須）。送信前に弾く（exit 4）。
    if (code is None) == (file is None):
        ctx.emit_error(
            json_out,
            ErrorCode.INVALID_PARAMS,
            "--code か --file のどちらか一方を指定してください（両方/どちらも無しは不可）",
        )
        raise typer.Exit(int(ExitCode.INPUT))
    # --file は **CLI 側で読む**（CLI の CWD 基準＝予測可能。Blender プロセスの CWD と区別）。
    # サーバには code として送る（サーバ側 file 読取は直接 RPC 用のフォールバック）。
    if file is not None:
        try:
            source = Path(file).read_text(encoding="utf-8")
        except OSError as e:
            ctx.emit_error(
                json_out, ErrorCode.INVALID_PARAMS, f"スクリプトファイルを読めません: {e}"
            )
            raise typer.Exit(int(ExitCode.INPUT)) from None
    else:
        source = str(code)
    return {"code": source}


# ---- コマンド別オーバーライド ----

SPECS: dict[str, CmdSpec] = {
    "scene-info": CmdSpec(
        doc="シーンのオブジェクト一覧/単位設定を取得する（大きい結果は output_ref で退避）。",
        with_fetch=True,
    ),
    "list-objects": CmdSpec(
        doc="シーン内オブジェクトを type/名前正規表現 でフィルタして一覧する。",
        py_names={"type": "type_filter"},
        option_names={"name_regex": ("--name-regex", "--regex")},
        help_overrides={"name_regex": "名前の正規表現フィルタ（部分一致・旧名 --regex も受理）"},
        pre_hook=_check_name_regex_not_option,
    ),
    "object-info": CmdSpec(
        doc="オブジェクトの寸法/頂点数/transform/材質/modifier を取得する。",
        help_overrides={"targets": _TARGETS_HELP},
    ),
    "set-origin": CmdSpec(
        doc="オブジェクトの原点を変更する。",
        help_overrides={
            "targets": _TARGETS_HELP,
            "to": "原点の決め方: geometry|cursor|world",
            "center": "geometry時の中心: median|bounds",
            "make_single_user": "共有mesh時に単一ユーザ化を許可",
        },
        request_id_help=_ID_HELP_LONG,
    ),
    "straighten": CmdSpec(
        doc="オブジェクトを直立補正する（reset/world-align/pca/floor/angle/align-vector/reference）。",
        # 手書き時代のオプション順（SSOT 定義順とは dry_run の位置が異なる）
        param_order=(
            "targets",
            "regex",
            "method",
            "up_axis",
            "axis",
            "up_hint",
            "degrees",
            "from_dir",
            "to_dir",
            "reference",
            "ref_axis",
            "dry_run",
            "bake_rotation",
            "make_single_user",
        ),
        help_overrides={
            "targets": _TARGETS_HELP,
            "method": "reset|world-align|pca|floor|angle|align-vector|reference",
            "up_axis": "up 方向: +Z|-Z|+Y|-Y|+X|-X（既定 +Z）",
            "axis": "world-align/reference=合わせる local 軸 / angle=回転する world 軸: X|Y|Z",
            "up_hint": "pca の符号: auto|current（current=現在 up 寄り・反転防止）",
            "from_dir": "align-vector: 揃えたい現在の world 方向 x,y,z",
            "to_dir": "align-vector: 目標 world 方向 x,y,z（省略時は up）",
            "ref_axis": "reference: 参照側の signed local 軸 +X..-Z（省略時 up-axis）",
        },
        request_id_help=_ID_HELP_LONG,
    ),
    "capture": CmdSpec(
        doc="現在の状態を画像で取得する（viewport/screen/render・PNG をファイル出力しパスを返す）。",
        help_overrides={
            "source": "取得元: viewport|screen|render（既定 viewport）",
            "width": "出力幅px（viewport/render・省略時既定）",
            "height": "出力高px（viewport/render・省略時既定）",
            "camera": "render で使うカメラ名（省略時 active・render 専用）",
        },
    ),
    "undo": CmdSpec(
        doc="直前の操作を元に戻す（グローバル undo スタックを steps 段戻す・GUI 必須）。",
        pre_hook=_check_steps,
        request_id_help=_ID_HELP_LONG,
    ),
    "redo": CmdSpec(
        doc="元に戻した操作をやり直す（グローバル undo スタックを steps 段進める・GUI 必須）。",
        pre_hook=_check_steps,
        request_id_help=_ID_HELP_LONG,
    ),
    "print-setup": CmdSpec(
        doc="3Dプリント向けにシーンの表示単位を設定する（mm/m・geometry 非破壊）。",
        help_overrides={"unit": "表示単位: mm|m（既定 mm）"},
        request_id_help=_ID_HELP_LONG,
    ),
    "print-check": CmdSpec(
        doc="3Dプリント健全性をチェックする（manifold/normals/degenerate・件数を返す）。",
        help_overrides={
            "targets": _TARGETS_HELP,
            "thin": "薄壁チェック（print3d 依存）",
            "min_thickness": "thin の最小厚み",
            "intersect": "自己交差チェック（print3d 依存）",
        },
        with_fetch=True,
    ),
    "print-repair": CmdSpec(
        doc="3Dプリント向けに mesh を best-effort 修復する（全省略で全修復・完全修復は非保証）。",
        help_overrides={"targets": _TARGETS_HELP},
    ),
    "print-export": CmdSpec(
        doc="3Dプリント向けに mesh を STL で書き出す（3MF は未導入のため STL を hint）。",
        py_names={"format": "fmt", "ascii": "ascii_format"},
        help_overrides={
            "targets": _TARGETS_HELP,
            "format": "出力形式: stl|3mf（3mf 未導入時は STL を hint）",
        },
        cli_defaults={"format": "stl"},  # SSOT は required だが CLI は歴史的に既定 stl
        always_send=frozenset({"ascii"}),
        with_request_id=True,  # mutates=False だが手書き時代から --id を持つ
    ),
    "export": CmdSpec(
        doc=(
            "シーン/選択を多形式で書き出す（obj/fbx/gltf/stl・3mf は未導入で CAPABILITY）。\n"
            "\n"
            "axis-forward/axis-up/scale/apply-unit-scale/embed-textures は **fbx 専用**（他 format に\n"
            "指定すると INVALID_PARAMS）。Unity 向けレシピは SKILL.md の「Unity 取り込みレシピ」参照。"
        ),
        py_names={"format": "fmt"},
        help_overrides={
            "format": "出力形式: obj|fbx|gltf|stl|3mf",
            "path": "出力ファイルパス（gltf は .glb 必須＝GLB 単一）",
            "targets": "対象（完全一致・--regex で正規表現・指定時はこれを書き出す）",
            "use_selection": "現在の選択集合のみ書き出す（targets 省略時・省略でシーン全体）",
            "axis_forward": (
                "fbx専用: forward軸 X|Y|Z|-X|-Y|-Z（既定 -Z・Unity 取込はこの既定のまま合う）。"
                "負の軸は --axis-forward=-Z のように '=' で連結すること"
                "（'--axis-forward -Z' は -Z が別オプションと誤解釈され得る）"
            ),
            "axis_up": (
                "fbx専用: up軸 X|Y|Z|-X|-Y|-Z（既定 Y・Unity 取込はこの既定のまま合う）。"
                "負の軸は --axis-up=-Z のように '=' で連結すること"
            ),
            "scale": "fbx専用: global_scale（既定は Blender 既定 1.0・正の値のみ）",
            "apply_unit_scale": (
                "fbx専用: シーン単位を1.0とみなして書き出す（既定は Blender 既定 on・省略時は指定しない）"
            ),
            "embed_textures": "fbx専用: テクスチャを FBX に同梱する（path_mode=COPY をサーバ側で自動設定）",
        },
        always_send=frozenset({"use_selection"}),
        tristate=frozenset({"apply_unit_scale"}),
    ),
    "import": CmdSpec(
        doc="多形式ファイルをシーンに取り込む（obj/fbx/gltf/stl・3mf は未導入で CAPABILITY）。",
        py_names={"format": "fmt"},
        help_overrides={"format": "入力形式: obj|fbx|gltf|stl|3mf"},
    ),
    "save": CmdSpec(
        doc=".blend ファイルに保存する（上書きは既定でバックアップ .blend1 を残す）。",
    ),
    "open": CmdSpec(
        doc=".blend ファイルを開く（シーン全体を置換・未保存変更があれば --force 必須）。",
        always_send=frozenset({"force"}),
    ),
    "exec-python": CmdSpec(
        doc=(
            "構造化サブコマンドで表現できない操作の逃げ道（既定 off・restricted で自走可・サンドボックスなし）。\n"
            "\n"
            "既定では無効。サーバ側のユーザローカル policy.toml で [exec] mode を restricted（推奨・AST\n"
            "ブロックリスト検査つきで自走可）/ audited / trusted にしたときだけ実行できる（`bli policy\n"
            "--action set --mode restricted` で有効化）。CLI からは mode を送れない＝CLI フラグ単体では\n"
            "昇格できない（spec §276・§459）。実行コードは同一 OS 権限で走る＝結果の security_guarantee は\n"
            "常に false（過信しないこと）。"
        ),
        help_overrides={
            "code": "実行する Python コード（--file と排他）",
            "file": "実行するスクリプトファイル（--code と排他）",
        },
        build=_build_exec_python,
    ),
    "select": CmdSpec(
        doc="オブジェクトを選択し active を設定する。",
        py_names={"type": "type_filter"},
        help_overrides={
            "targets": _TARGETS_HELP,
            "type": "型フィルタ（MESH/CURVE/...）",
            "active": "active にする対象名",
        },
    ),
    "transform": CmdSpec(
        doc="オブジェクトの位置/回転/拡縮を設定または相対適用する。",
        help_overrides={
            "targets": _TARGETS_HELP,
            "mode": "set|delta（delta は loc/rot 加算・scale 乗算）",
        },
    ),
    "apply-transform": CmdSpec(
        doc="オブジェクトの transform をメッシュデータに適用する（全省略時は全適用）。",
        help_overrides={
            "targets": _TARGETS_HELP,
            "make_single_user": "共有mesh時に単一ユーザ化を許可",
        },
    ),
    "duplicate": CmdSpec(
        doc="オブジェクトを複製する（count 回・world offset 累積）。",
        help_overrides={
            "targets": _TARGETS_HELP,
            "offset": "複製ごとの world オフセット x,y,z",
        },
        pre_hook=_check_duplicate_count,
    ),
    "delete": CmdSpec(
        doc="オブジェクトを削除する（削除前サマリを backup として結果に残す）。",
        help_overrides={"targets": _TARGETS_HELP},
    ),
    "material": CmdSpec(
        doc=(
            "マテリアルを割り当て/作成/一覧する"
            "（create は対象へ作成と同時に割り当て・PBR/テクスチャは create 専用）。"
        ),
        help_overrides={
            "targets": _TARGETS_HELP,
            "name": "マテリアル名（assign=既存 / create=新規）",
        },
    ),
    "modifier": CmdSpec(
        doc=(
            "モディファイアを追加/削除/一覧/適用する"
            "（add は --type 必須・任意 type + --props 対応・apply は mesh へ焼き込み）。"
        ),
        py_names={"type": "type_"},
        help_overrides={
            "action": "add|remove|list|apply",
            "targets": _TARGETS_HELP,
            "name": "モディファイア名（remove/apply 対象）",
            "axis": "MIRROR の軸: X|Y|Z",
            "operation": "BOOLEAN の演算: UNION|DIFFERENCE|INTERSECT",
        },
    ),
    "add": CmdSpec(
        doc="オブジェクトを生成する（mesh primitive / empty / light / camera / text）。",
        py_names={"type": "type_"},
        help_overrides={
            "type": "生成する種類: cube|uv-sphere|ico-sphere|cylinder|cone|plane|torus|empty|light|camera|text",
            "name": "生成後の名前",
            "light_type": "type=light 専用: POINT|SUN|SPOT|AREA（既定 POINT）",
        },
    ),
    "mode": CmdSpec(
        doc="編集モードを切り替える（object/edit/sculpt/vertex-paint/weight-paint）。",
        help_overrides={"to": "切替先: object|edit|sculpt|vertex-paint|weight-paint"},
    ),
    "rename": CmdSpec(
        doc="オブジェクトを改名する（--with-data で obj.data も同名に変更）。",
        help_overrides={"targets": _TARGETS_HELP, "name": "新しい名前"},
    ),
    "parent": CmdSpec(
        doc="親子関係を設定/解除する（--to と --clear は排他）。",
        help_overrides={"targets": "対象オブジェクト（複数可・完全一致・--regex で正規表現）"},
    ),
    "collection": CmdSpec(
        doc="コレクションを作成/移動/link/unlink/一覧する。",
        help_overrides={
            "action": "create|move|link|unlink|list",
            "targets": "対象オブジェクト（move/link/unlink で必須）",
        },
    ),
    "mesh": CmdSpec(
        doc="メッシュを編集する（法線再計算 / 距離マージ / 押し出し / ベベル / インセット / ブール / デシメート）。",
        help_overrides={
            "op": "recalc-normals|merge-by-distance|extrude|bevel|inset|boolean|decimate",
            "targets": _TARGETS_HELP,
            "inside": "recalc-normals: 法線を内向きに",
            "offset": "extrude: 押し出しベクトル x,y,z（world 空間・move/duplicate と同じ）",
            "width": "bevel: ベベル幅（ローカル単位・0以上）",
            "thickness": "inset: インセット厚み（0以上）",
            "operation": "boolean: 演算 UNION|DIFFERENCE|INTERSECT",
            "with_object": "boolean: 相手 mesh オブジェクト名",
            "ratio": "decimate: 削減比率 0..1",
        },
        async_help="job_id を即返し（boolean/decimate のみ・既定は自動待機）",
    ),
    "request-status": CmdSpec(
        doc="リクエストの決着状態を取得する（タイムアウト後の後追い回収）。",
        py_names={"id": "request_id"},
    ),
    "job-status": CmdSpec(
        doc="非同期 job（heavy コマンドの --async）の状態を取得する（request-status を1回問い合わせ）。",
        method="request-status",
        py_names={"id": "request_id"},
    ),
}
