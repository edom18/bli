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

# ---- シナリオ2: 直立補正（M8 T8.2 / 実地フィードバック PR-4 で基準指定 method を追加）----
command(
    "straighten",
    "オブジェクトを直立補正する（reset/world-align/pca/floor/angle/align-vector/reference）",
    # method により有効/必須 param が変わる（条件付き）。op 専用 param（axis/up_hint/degrees/
    # from_dir/to_dir/reference/ref_axis）は presence-sensitive（default なし）で、別 method に
    # 渡されたら silent ignore せず弾く（§6e）。bake-rotation は回転を mesh データへ焼き込む破壊的
    # 操作で、共有 mesh は --make-single-user 必須（apply-transform/mesh と同じガード・§6e）。
    # angle/align-vector/reference は「エージェントが算出した補正を straighten 経由で安全に適用」する
    # ための基準指定 method（transform 迂回の解消・実地フィードバック #4）。いずれも object 回転のみ。
    params=(
        p("targets", ParamType.STR, required=True, help="対象（name|regex）"),
        p(
            "method",
            ParamType.ENUM,
            required=True,
            choices=[
                "reset",
                "world-align",
                "pca",
                "floor",
                "angle",
                "align-vector",
                "reference",
            ],
            help="補正方法: reset|world-align|pca|floor|angle|align-vector|reference",
        ),
        p(
            "up_axis",
            ParamType.ENUM,
            default="+Z",
            choices=["+Z", "-Z", "+Y", "-Y", "+X", "-X"],
            help="up 方向（既定 +Z）",
        ),
        # axis は world-align/reference（対象 local 軸・省略時は最近軸を自動）と angle（回転する
        # world 軸・必須）で有効（presence-sensitive: default なし）。
        p(
            "axis",
            ParamType.ENUM,
            choices=["X", "Y", "Z"],
            help="world-align/reference=合わせる local 軸 / angle=回転する world 軸",
        ),
        # up_hint は pca 専用（presence-sensitive: default なし）。auto=重心方向で符号決定（既定）/
        # current=現在の up に近い側を + にする＝最小回転で上下反転を防ぐ（実地フィードバック #5）。
        p(
            "up_hint",
            ParamType.ENUM,
            choices=["auto", "current"],
            help="pca の符号決定: auto(重心)|current(現在 up 寄り=反転防止)",
        ),
        # angle 専用（presence-sensitive）: world 軸 axis まわりの回転量（度・符号で向き）。
        p("degrees", ParamType.FLOAT, help="angle: 回転量（度・符号で向き）"),
        # align-vector 専用（presence-sensitive）: from_dir(world) を to_dir(world・省略時は up)へ
        # 最小回転で合わせる。エージェントが計測した現在方向→目標方向を直接渡せる（#4 の本命）。
        p("from_dir", ParamType.VEC3, help="align-vector: 揃えたい現在の world 方向(x,y,z)"),
        p("to_dir", ParamType.VEC3, help="align-vector: 目標 world 方向(x,y,z・省略時は up)"),
        # reference 専用（presence-sensitive）: 参照オブジェクトの ref_axis(signed local)の world
        # 方向へ、対象の axis(local・省略時は最近軸)を合わせる（world-align の目標を up→参照軸に）。
        p("reference", ParamType.STR, help="reference: 基準にする別オブジェクト名"),
        # 選択肢は up_axis と同順（Pydantic は同一メンバの Literal を共有しparity が順序依存のため）。
        p(
            "ref_axis",
            ParamType.ENUM,
            choices=["+Z", "-Z", "+Y", "-Y", "+X", "-X"],
            help="reference: 参照側の signed local 軸（省略時は up_axis）",
        ),
        p("bake_rotation", ParamType.BOOL, default=False, help="回転を mesh データへ焼き込む"),
        p(
            "make_single_user",
            ParamType.BOOL,
            default=False,
            help="bake時に共有mesh単一ユーザ化を許可",
        ),
        # dry_run は適用せず計画（回転/傾き角）のみ返す（実地フィードバック #2・通常モードフラグ）。
        p("dry_run", ParamType.BOOL, default=False, help="適用せず計画（回転/傾き角）のみ返す"),
    ),
    mutates=True,
    required_mode=Mode.OBJECT,
)

# ---- シナリオ3: 3Dプリンタ対応（M8 T8.3 print-setup〜）----
command(
    "print-setup",
    "3Dプリント向けにシーンの表示単位を設定する（mm/m・geometry 非破壊）",
    # unit は表示単位（length_unit）の設定。geometry は再スケールしない（非破壊・研究 §E5）。
    # 実寸の export スケールは print-export（T8.5）が一本で算出する（global_scale 一本化）。
    params=(
        p("unit", ParamType.ENUM, default="mm", choices=["mm", "m"], help="表示単位（既定 mm）"),
        p("scene", ParamType.STR, help="対象シーン名（省略時は active）"),
    ),
    mutates=True,
    # 単位設定はモード非依存（geometry を触らない）だが、シナリオ3 全体を OBJECT に統一する方針
    # （set-origin/straighten/scene-info と同じ・自動遷移せず E_MODE_MISMATCH）。
    required_mode=Mode.OBJECT,
)
command(
    "print-check",
    "3Dプリント健全性をチェックする（manifold/normals/degenerate は bmesh 自前・件数を返す）",
    # manifold/normals/degenerate は bmesh 自前計算（print3d 非依存・常時 stable）。thin/intersect は
    # print3d 依存で、未導入時は CAPABILITY_UNAVAILABLE（研究 §E6）。カテゴリ flag は presence-sensitive
    # （省略時は bmesh 3種すべて）。min_thickness は thin 専用。--save-to はファイルI/O のため M9 へ繰越。
    params=(
        p("targets", ParamType.STR, required=True, help="対象（name|regex）"),
        p("manifold", ParamType.BOOL, help="非多様体チェック"),
        p("normals", ParamType.BOOL, help="反転法線チェック"),
        p("degenerate", ParamType.BOOL, help="退化面チェック"),
        p(
            "thin",
            ParamType.BOOL,
            help="薄壁チェック（print3d 依存・未導入は CAPABILITY_UNAVAILABLE）",
        ),
        p("min_thickness", ParamType.FLOAT, help="thin の最小厚み（thin 専用）"),
        p("intersect", ParamType.BOOL, help="自己交差チェック（print3d 依存・未導入は同上）"),
    ),
    required_mode=Mode.OBJECT,
)
command(
    "print-repair",
    "3Dプリント向けに mesh を best-effort 修復する（make-manifold/recalc-normals/remove-degenerate）",
    # 修復フラグは presence-sensitive（全省略時は全修復）。完全修復は保証しない（spec §10 S3）。
    # mesh データを書き換える破壊的操作のため共有 mesh は --make-single-user 必須（§6e）。
    params=(
        p("targets", ParamType.STR, required=True, help="対象（name|regex）"),
        p("make_manifold", ParamType.BOOL, help="穴埋め/重複マージ/loose 除去で manifold 化"),
        p("recalc_normals", ParamType.BOOL, help="面法線を一貫化"),
        p("remove_degenerate", ParamType.BOOL, help="退化面/辺を除去"),
        p("make_single_user", ParamType.BOOL, default=False, help="共有mesh時に単一ユーザ化を許可"),
    ),
    mutates=True,
    required_mode=Mode.OBJECT,
)
command(
    "print-export",
    "3Dプリント向けに mesh を STL で書き出す（3MF は未導入のため STL を hint）",
    # 対象は単一（require_single・set-origin/straighten/print-check と対称）。STL は対象 mesh を
    # world 空間で焼いて出力する（wm.stl_export は常に world 焼き・研究 §E8）。scale は global_scale
    # 一本化（use_scene_unit=False 固定で scale_length を出力へ反映させない・1000倍ずれ防止）。
    # 3mf は両版とも export operator が実体なし（§E8）→ CAPABILITY_UNAVAILABLE + STL hint。
    # ファイルを書くだけでシーンは変えない（mutates=False・選択は save/restore で非破壊）。
    params=(
        p("targets", ParamType.STR, required=True, help="対象（name|regex）"),
        p("format", ParamType.ENUM, required=True, choices=["stl", "3mf"], help="出力形式"),
        p("path", ParamType.PATH, required=True, help="出力ファイルパス"),
        p("ascii", ParamType.BOOL, default=False, help="STL を ASCII で出力（既定 binary）"),
        # global_scale 一本化（既定 1.0＝Blender 単位を STL に 1:1）。scale_length は検証専用で結果に報告。
        p("scale", ParamType.FLOAT, default=1.0, help="出力スケール（global_scale・既定 1.0）"),
        p(
            "apply_modifiers",
            ParamType.BOOL,
            default=True,
            help="モディファイア適用後の最終形を出力（既定 on）",
        ),
    ),
    mutates=False,
    required_mode=Mode.OBJECT,
)

# ---- 状態キャプチャ（実地フィードバック #1）----
command(
    "capture",
    "現在の状態を画像で取得する（viewport/screen/render・PNG をファイル出力しパスを返す）",
    # source=viewport: offscreen draw_view3d（UI なし・解像度指定可・既定）/ screen: ビューポート領域
    # をそのまま screenshot（領域サイズ固定）/ render: カメラからレンダ。読み取り専用（mutates=False）。
    # width/height は viewport/render 用（screen は領域サイズ固定で不可）・camera は render 専用。
    # 出力は outputs_dir（git 非管理・shared-fs）に PNG を書き、パス/サイズ/sha256 を返す。
    params=(
        p(
            "source",
            ParamType.ENUM,
            default="viewport",
            choices=["viewport", "screen", "render"],
            help="取得元: viewport(offscreen)|screen(領域)|render(カメラ)（既定 viewport）",
        ),
        p("width", ParamType.INT, help="出力幅px（viewport/render・省略時 既定値）"),
        p("height", ParamType.INT, help="出力高px（viewport/render・省略時 既定値）"),
        p(
            "camera",
            ParamType.STR,
            help="render で使うカメラ名（省略時 active camera・render 専用）",
        ),
    ),
    mutates=False,
    # 「現状を見る」手段なので EDIT/SCULPT 等どのモードでも使える（Mode.ANY）。viewport/screen/render は
    # オブジェクトモードに依存しない（敵対的レビュー P2-1）。他 info 系（OBJECT 固定）とは目的が異なる。
    required_mode=Mode.ANY,
)

# ---- 状態操作: undo / redo（実地フィードバック #3）----
# グローバル undo スタック（ユーザーの GUI 操作も含む）を steps 段だけ戻す/進める。可逆性を「直前
# transform の自力再構築」に頼らせない（試行錯誤の安全性向上）。実機は ed.undo()/ed.redo() を bare で
# steps 回（研究 §E7・GUI 確認済み）。GUI 必須で --background は E_PRECONDITION 縮退（capture と同流儀）。
command(
    "undo",
    "直前の操作を元に戻す（グローバル undo スタックを steps 段戻す・GUI 必須）",
    params=(
        p(
            "steps", ParamType.INT, default=1, help="戻す段数（1〜100・既定 1）"
        ),  # 上限=runtime.MAX_UNDO_STEPS
    ),
    mutates=True,
    # undo はモードを跨ぐ復元になり得るため Mode.ANY（モード一致を要求しない）。
    required_mode=Mode.ANY,
)
command(
    "redo",
    "元に戻した操作をやり直す（グローバル undo スタックを steps 段進める・GUI 必須）",
    params=(
        p(
            "steps", ParamType.INT, default=1, help="進める段数（1〜100・既定 1）"
        ),  # 上限=runtime.MAX_UNDO_STEPS
    ),
    mutates=True,
    required_mode=Mode.ANY,
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

# ---- 汎用編集（マテリアル / M6 T6.3）----
command(
    "material",
    "マテリアルを割り当て/作成/一覧する（create は対象へ作成と同時に割り当て）",
    # targets/name は action により必須が変わる（条件付き必須）。schema 上は任意にし、
    # ops 側で action 別に検証する（set-origin の center/x/y/z と同じ流儀）。
    params=(
        p(
            "action",
            ParamType.ENUM,
            required=True,
            choices=["assign", "create", "list"],
            help="操作: assign|create|list",
        ),
        p("targets", ParamType.STR, help="対象（name|regex）"),
        p("name", ParamType.STR, help="マテリアル名（assign=既存名 / create=新規名）"),
        p("color", ParamType.VEC4, help="RGBA r,g,b,a（create の Base Color）"),
        p("make_single_user", ParamType.BOOL, default=False, help="共有mesh時に単一ユーザ化を許可"),
    ),
    mutates=True,
    required_mode=Mode.OBJECT,
)

# ---- 汎用編集（モディファイア / M6 T6.4）----
command(
    "modifier",
    "モディファイアを追加/削除/一覧/適用する（add は --type 必須・apply は mesh へ焼き込み）",
    # type/name/type別params は action により必須が変わる（条件付き必須）。schema 上は任意にし、
    # ops 側で action/type 別に検証する（material/set-origin と同じ流儀）。
    params=(
        p(
            "action",
            ParamType.ENUM,
            required=True,
            choices=["add", "remove", "list", "apply"],
            help="操作: add|remove|list|apply",
        ),
        p("targets", ParamType.STR, required=True, help="対象（name|regex）"),
        p(
            "type",
            ParamType.ENUM,
            choices=["MIRROR", "SUBSURF", "SOLIDIFY", "DECIMATE", "BOOLEAN"],
            help="add 時の種類",
        ),
        p("name", ParamType.STR, help="モディファイア名（remove/apply の対象 / add の任意名）"),
        p("axis", ParamType.ENUM, choices=["X", "Y", "Z"], help="MIRROR の軸"),
        p("levels", ParamType.INT, help="SUBSURF の分割数"),
        p("thickness", ParamType.FLOAT, help="SOLIDIFY の厚み"),
        p("ratio", ParamType.FLOAT, help="DECIMATE の比率（0..1）"),
        p(
            "operation",
            ParamType.ENUM,
            choices=["UNION", "DIFFERENCE", "INTERSECT"],
            help="BOOLEAN の演算",
        ),
        p("with_object", ParamType.STR, help="BOOLEAN の相手オブジェクト名"),
        p(
            "make_single_user",
            ParamType.BOOL,
            default=False,
            help="apply 時に共有mesh単一ユーザ化を許可",
        ),
    ),
    mutates=True,
    required_mode=Mode.OBJECT,
)

# ---- メッシュ編集（bmesh 一次 / M7 T7.1–7.3）----
command(
    "mesh",
    "メッシュを編集する（op 別: 法線再計算 / 距離マージ / 押し出し / ベベル / インセット / ブール / デシメート）",
    # op により有効/必須 param が変わる（条件付き）。schema は op 非依存で任意にし、
    # ops 側で op 別に検証する（material/modifier と同じ流儀）。op 専用 param（inside/
    # distance/offset/width/segments/thickness/operation/with_object/ratio）は schema
    # default を持たせない（持たせると生成クライアントが既定値を埋めて別 op へ誤送信し
    # op 別検証で弾かれるため。§6e の presence-sensitive 方針の一般化）。
    params=(
        p(
            "op",
            ParamType.ENUM,
            required=True,
            choices=[
                "recalc-normals",
                "merge-by-distance",
                "extrude",
                "bevel",
                "inset",
                "boolean",
                "decimate",
            ],
            help="操作: recalc-normals|merge-by-distance|extrude|bevel|inset|boolean|decimate",
        ),
        p("targets", ParamType.STR, required=True, help="対象（name|regex）"),
        p("inside", ParamType.BOOL, help="recalc-normals: 法線を内向きにする"),
        p("distance", ParamType.FLOAT, help="merge-by-distance: マージ距離（既定 0.0001）"),
        # T7.2: extrude offset は world 空間 / bevel width・inset thickness はスカラで mesh ローカル
        # 単位。いずれも op 別に必須（ops で検証）。
        p("offset", ParamType.VEC3, help="extrude: 押し出しベクトル x,y,z（world・必須）"),
        p("width", ParamType.FLOAT, help="bevel: ベベル幅（ローカル・必須・0以上）"),
        p("segments", ParamType.INT, help="bevel: 分割数（既定1・1〜100）"),
        p("thickness", ParamType.FLOAT, help="inset: インセット厚み（ローカル・必須・0以上）"),
        # T7.3（boolean/decimate）: bmesh に無いため modifier add+apply 経由（ops で検証）。
        p(
            "operation",
            ParamType.ENUM,
            choices=["UNION", "DIFFERENCE", "INTERSECT"],
            help="boolean: 演算（必須）",
        ),
        p("with_object", ParamType.STR, help="boolean: 相手 mesh オブジェクト名（必須）"),
        p("ratio", ParamType.FLOAT, help="decimate: 削減比率 0..1（必須）"),
        p("make_single_user", ParamType.BOOL, default=False, help="共有mesh時に単一ユーザ化を許可"),
    ),
    mutates=True,
    stability=Stability.EXPERIMENTAL,
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
