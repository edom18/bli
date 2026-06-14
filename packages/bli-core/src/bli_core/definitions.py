"""コマンド定義の宣言（SSOT 本体）。import 副作用で COMMANDS に登録する。

v1 の全コマンドはここに集約する。M0→M2 では診断系 + 代表コマンドを定義し、
M3 以降で各コマンドを追加する（plan.md §4）。
"""

from __future__ import annotations

from .commands import command, p
from .types import Mode, ParamType, Stability

# ---- 接続・診断 ----
command("ping", "アドオンへ疎通確認し protocol/version/capabilities を返す")
command("doctor", "環境診断（Blender検出/アドオン導入/port/version/能力）")
command(
    "init",
    "設定生成・セッショントークン発行・connection.json 書き込み",
    params=(
        p("port", ParamType.INT, help="リッスンポート（既定 9876）"),
        p("force", ParamType.BOOL, default=False, help="既存設定を上書き"),
    ),
)
command(
    "request-status",
    "リクエストの決着状態を取得（タイムアウト後の後追い回収）",
    params=(p("id", ParamType.STR, required=True, help="リクエストID(UUIDv4)"),),
)

# ---- 情報取得（読み取り専用）----
command(
    "scene-info",
    "シーン階層/オブジェクト一覧/単位設定を取得（大きい場合はファイル退避）",
    params=(p("depth", ParamType.INT, default=1, help="階層の深さ"),),
    required_mode=Mode.OBJECT,
)
command(
    "object-info",
    "オブジェクトの寸法/頂点数/transform/bbox/材質/modifier を取得",
    params=(p("targets", ParamType.STR, required=True, help="対象（name|regex）"),),
    required_mode=Mode.OBJECT,
)
command(
    "list-objects",
    "シーン内オブジェクトを type/regex でフィルタして一覧する",
    params=(
        p("type", ParamType.STR, help="型フィルタ（MESH/CURVE/EMPTY/LIGHT/CAMERA 等・大小無視）"),
        p("regex", ParamType.STR, help="名前の正規表現フィルタ（部分一致）"),
    ),
    required_mode=Mode.OBJECT,
)

# ---- シナリオ1: 原点変更（代表・schema 型確認用）----
command(
    "set-origin",
    "オブジェクトの原点を指定方法で変更する",
    params=(
        p("targets", ParamType.STR, required=True, help="対象（name|regex|session_uid）"),
        p(
            "to",
            ParamType.ENUM,
            required=True,
            choices=["geometry", "cursor", "world"],
            help="原点の決め方",
        ),
        p("center", ParamType.ENUM, choices=["median", "bounds"], help="geometry時の中心"),
        p("x", ParamType.FLOAT, help="world時のX"),
        p("y", ParamType.FLOAT, help="world時のY"),
        p("z", ParamType.FLOAT, help="world時のZ"),
        p("make_single_user", ParamType.BOOL, default=False, help="共有mesh時に明示許可"),
    ),
    mutates=True,
    required_mode=Mode.OBJECT,
)

# ---- 汎用編集（オブジェクト操作 / M6 T6.1）----
command(
    "select",
    "オブジェクトを選択し active を設定する（name|regex / type フィルタ）",
    params=(
        p("targets", ParamType.STR, required=True, help="対象（name|regex）"),
        p("type", ParamType.STR, help="型フィルタ（MESH/CURVE/... 大小無視）"),
        p("active", ParamType.STR, help="active にする対象名（省略時は先頭）"),
    ),
    mutates=True,
    required_mode=Mode.OBJECT,
)
command(
    "transform",
    "位置/回転/拡縮を設定または相対適用する（delta: loc/rot は加算・scale は乗算）",
    params=(
        p("targets", ParamType.STR, required=True, help="対象"),
        p("location", ParamType.VEC3, help="位置 x,y,z"),
        p("rotation", ParamType.VEC3, help="回転 x,y,z（度）"),
        p("scale", ParamType.VEC3, help="拡縮 x,y,z"),
        p("mode", ParamType.ENUM, default="set", choices=["set", "delta"], help="設定/相対"),
    ),
    mutates=True,
    required_mode=Mode.OBJECT,
)
command(
    "apply-transform",
    "オブジェクトの位置/回転/拡縮をメッシュデータに適用する（全省略時は全適用）",
    # location/rotation/scale は「キーの有無」で意味が決まる presence-sensitive フラグ。
    # schema に default を出すと、既定値を埋める生成クライアントが全 false を送って
    # しまうため、default は持たせない（Codex P2）。
    params=(
        p("targets", ParamType.STR, required=True, help="対象（name|regex）"),
        p("location", ParamType.BOOL, help="位置を適用"),
        p("rotation", ParamType.BOOL, help="回転を適用"),
        p("scale", ParamType.BOOL, help="拡縮を適用"),
        p("make_single_user", ParamType.BOOL, default=False, help="共有mesh時に明示許可"),
    ),
    mutates=True,
    required_mode=Mode.OBJECT,
)

# ---- 汎用編集（複製/削除 / M6 T6.2）----
command(
    "duplicate",
    "オブジェクトを複製する（count 回・world offset 累積・linked でデータ共有）",
    params=(
        p("targets", ParamType.STR, required=True, help="対象（name|regex）"),
        p("linked", ParamType.BOOL, default=False, help="データを共有する（リンク複製）"),
        p("count", ParamType.INT, default=1, help="複製数（1〜1000）"),
        p("offset", ParamType.VEC3, help="複製ごとの world オフセット x,y,z（累積）"),
    ),
    mutates=True,
    required_mode=Mode.OBJECT,
)
command(
    "delete",
    "オブジェクトを削除する（削除前サマリを backup として結果に残す）",
    params=(p("targets", ParamType.STR, required=True, help="対象（name|regex）"),),
    mutates=True,
    required_mode=Mode.OBJECT,
)

# ---- 逃げ道（既定 off / path 型確認用 / 実装は M11）----
command(
    "exec-python",
    "構造化で表現できない操作のフォールバック（既定 off）",
    params=(
        p("code", ParamType.STR, help="実行するPythonコード"),
        p("file", ParamType.PATH, help="実行するスクリプトファイル"),
    ),
    mutates=True,
    stability=Stability.EXPERIMENTAL,
    implemented=False,  # M11 で実装予定
)
