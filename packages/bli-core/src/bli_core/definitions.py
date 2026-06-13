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
    "オブジェクトの寸法/頂点数/transform/材質/modifier を取得",
    params=(p("targets", ParamType.STR, required=True, help="対象（name|regex）"),),
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

# ---- 汎用編集（代表・vec3 型確認用）----
command(
    "transform",
    "位置/回転/拡縮を設定または相対適用する",
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

# ---- 逃げ道（既定 off / path 型確認用）----
command(
    "exec-python",
    "構造化で表現できない操作のフォールバック（既定 off）",
    params=(
        p("code", ParamType.STR, help="実行するPythonコード"),
        p("file", ParamType.PATH, help="実行するスクリプトファイル"),
    ),
    mutates=True,
    stability=Stability.EXPERIMENTAL,
)
