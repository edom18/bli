# bli — RPCメソッドカタログ (methods.md)

凡例: **M**=mutates(状態変更) / **H**=heavy(非同期job) / **Mode**=required_mode / **Cap**=capability_deps / **St**=stability(s=stable/e=experimental)

> 各メソッドは `bli <method>` CLI サブコマンドに 1:1 対応。params は `bli-core` の Command 定義が真実。

> **レンダ中の拒否（M10 T10.2・spec §7・研究 §E12）**: Blender がレンダリング中（`render_init`〜`render_complete`/`render_cancel`）は、**M=mutates または H=heavy** のメソッドを dispatch 前に `BUSY_RENDERING`（category=ENVIRONMENT・retryable・CLI exit 2）で即拒否する（キューに積まない＝フリーズ中の滞留防止）。**読み取り専用（scene-info/list-objects/object-info 等）と lock-free（request-status/job-status/job-wait）はレンダ中も通る**（観測性を維持）。busy 検知は `render_state`（`threading.Event`）＝render handler は内部レンダスレッドから発火するため thread-safe に保持。

> **メインスレッド応答性 watchdog（M10 T10.3・spec §7・研究 §E13）**: 重量ネイティブ処理（boolean/decimate/import 等）がメインスレッドを占有して固まると、pump タイマが止まり生存印が更新されなくなる。これを別スレッド監視が「閾値(既定 60s)を超えて未更新」で検知し **通知のみ**（実行は止めない／kill しない）。`request-status`（→`job-status`/`job-wait`）応答の `data.watchdog`（`{responsive, unresponsive_since, last_pump_age, threshold}`）と `doctor` の `main_thread_responsive` に載せる＝**lock-free**（受信スレッド処理）でメインが固まっていても観測できる。

---

## 接続・診断（ローカル完結 / 一部はRPC前）
| method | params | result | M | H | Mode | St |
|--------|--------|--------|:-:|:-:|----|:--:|
| `init` | `--port?` `--force?` | token生成・connection.json・.bli/雛形 | - | - | - | s |
| `doctor` | - | blender検出/addon導入/port/version/能力の診断 | - | - | - | s |
| `ping` | - | hello往復→protocol/blender版/capabilities | - | - | ANY | s |
| `policy` | `--action show\|set` `--mode?` `--yes?` | policy.toml の表示/編集（**CLIローカル・RPCなし**） | - | - | - | s |
| `request-status` | `--id` | RequestRegistryの状態（PENDING/RUNNING/DONE/FAILED） | - | - | ANY | s |
| `job-status` / `job-wait` | `--id` `--timeout?` | 非同期jobの状態/結果 | - | - | ANY | s |
| `help` | `--json?` `--command?` | コマンドスキーマ（machine可読） | - | - | - | s |
| `list-commands` | `--json?` | メソッド一覧 | - | - | - | s |

## 情報取得（読み取り専用）
| method | params | result | M | H | Mode | St |
|--------|--------|--------|:-:|:-:|----|:--:|
| `scene-info` | `--depth?` | シーン階層/オブジェクト一覧/単位（大→output_ref） | - | △ | OBJECT | s |
| `list-objects` | `--type?` `--name-regex?` | フィルタ済み一覧 | - | - | OBJECT | s |
| `object-info` | `--targets` | 寸法/頂点数/transform/bbox/材質/modifier | - | - | OBJECT | s |
| `capture` | `--source viewport\|screen\|render` `--width?` `--height?` `--camera?`(render) | PNG パス/サイズ/sha256/解像度 | - | - | ANY | s |

> **`capture`（実地フィードバック #1）**: 現在の状態を画像で取得する（エージェントの「現状確認」手段）。`viewport`=gpu offscreen で描画（UI なし・`--width/--height` 指定可・既定）/ `screen`=ビューポート領域をそのまま screenshot（領域サイズ固定で width/height 不可）/ `render`=カメラからレンダ（`--camera` 省略時 active）。読み取り専用（render 設定は save/restore で非破壊）。PNG は `outputs_dir`（git 非管理・shared-fs・コンテンツアドレス名）に書き出しパスを返す。`viewport`/`screen` は GUI 必須（`--background` では `E_PRECONDITION`）。Spike V で 5.0.1/4.4.3 両版確認。

> **`dimensions` と `bbox.size` の違い**（紛らわしいので明記）: `dimensions` は **オブジェクト固有サイズ**（`obj.dimensions`・scale 反映・**回転不変**）。`bbox.size` は **world AABB**（`matrix_world @ bound_box` の軸並行境界・**回転すると変化**）。傾いた物体では両者は一致しない。`--targets` は `--target`（単数）も別名で受け付ける。

> **`--targets` の解決セマンティクス**（全 `--targets` パラメータ共通・設計レビュー 2026-07-11 B2）: 既定（`--regex` 省略）は**完全名一致のみ**。`--regex` を明示したときだけ正規表現（`re.search`）として解釈する。暗黙のフォールバックは廃止済み（`Cube.001` のような既定命名は `.` が regex の任意一文字に当たるため、typo が別オブジェクトへ誤マッチし得た）。完全一致 0 件で、それが正規表現として解釈すると当たる場合は `E_TARGET_NOT_FOUND` の症状文に一致件数と `--regex` 使用のヒントを添える。不正な正規表現（`--regex` 指定時のみ評価）は `E_PRECONDITION`（category=USER_INPUT）。**紛らわしい別物**: `list-objects` の名前フィルタは `--name-regex <pat>`（パターン値を取る STR。旧 `--regex <pat>` は CLI 別名で受理・レビュー R1-4 で改名）で、この `--regex`（値なしの解釈フラグ）とは異なる。

## 汎用編集（オブジェクト操作）
| method | params | result | M | H | Mode | St |
|--------|--------|--------|:-:|:-:|----|:--:|
| `select` | `--targets` `--type?` `--active?` | 選択結果/fingerprint | ✓ | - | OBJECT | s |
| `transform` | `--targets` `--location?` `--rotation?` `--scale?` `--mode set\|delta` | 適用後transform | ✓ | - | OBJECT | s |
| `apply-transform` | `--targets` `--location?` `--rotation?` `--scale?` | verified | ✓ | - | OBJECT | s |
| `duplicate` | `--targets` `--linked?` `--count?`(1〜1000) `--offset?` | 新オブジェクト名 | ✓ | - | OBJECT | s |
| `delete` | `--targets` | 削除結果（削除前 summary を backup として常時返却） | ✓ | - | OBJECT | s |
| `material` | `--action assign\|create\|list` `--targets?` `--name?` `--color r,g,b,a?` `[create専用: --metallic? --roughness? --emission r,g,b,a? --emission-strength? --alpha? --texture <path>? --pack-texture?]` `--make-single-user?` | 材質状態（list は slot/name/link/base_color・create は principled/texture 実値） | ✓ | - | OBJECT | s |
| `modifier` | `--action add\|remove\|list\|apply` `--targets` `--type?`(任意 type) `--props '<JSON>'?` `[type別params]` `--make-single-user?` | modifier状態（list は name/type/型別値・--props 時は applied_props） | ✓ | - | OBJECT | s |

`modifier --type`（add で必須）: **任意の Modifier type**（P2-3 G4・例 `BEVEL`/`ARRAY`/`WELD`。実在はサーバが `bpy.types.Modifier` の rna enum から**能力検出**で検証＝両版 83 種・無効は有効一覧つき `INVALID_PARAMS`）。`MIRROR`/`SUBSURF`/`SOLIDIFY`/`DECIMATE`/`BOOLEAN` の 5 種は専用フラグあり（互換・下記）。

> `modifier`: 操作は `--action`（ENUM）。`--type` は add で必須（schema 上は任意・サーバが action 別に検証）。型別 params（**add 専用**）= MIRROR:`--axis X\|Y\|Z` / SUBSURF:`--levels`(0〜6) / SOLIDIFY:`--thickness` / DECIMATE:`--ratio`(0〜1) / BOOLEAN:`--operation`+`--with`(相手mesh・必須)。`remove`/`apply` は `--name` 必須。**apply のみ** mesh へ焼き込む破壊的操作で、共有 mesh は `--make-single-user` 必須（add/remove/list はオブジェクト単位で不要）。非対応型は `E_PRECONDITION`。
>
> **`--props '<JSON>'`（P2-3 G4・add 専用）**: 任意プロパティを JSON オブジェクトで設定する（例: `--type BEVEL --props '{"width":0.1,"segments":2}'`）。サーバが対象 modifier の **rna から編集可能プロパティを列挙して検証**する: 未知キーは有効キー一覧つき `INVALID_PARAMS` / 型は rna 型（BOOLEAN/INT/FLOAT〔配列含む〕/ENUM〔有効値提示〕/STRING/POINTER）で検証 / POINTER は **Object 参照のみ名前文字列で解決**（他は未対応と明示）。数値の**範囲外は Blender の rna が clamp** するため、結果 `modifier.applied_props` に**設定後の実値**を返して可視化する（silent drop なし）。専用フラグとの**併用は不可**（曖昧さ排除・`INVALID_PARAMS`）。BOOLEAN を --props 経由で作る場合は `--with` の代わりに `{"object":"名前"}` を**必須**指定（--with と同一の自己参照禁止・mesh 限定検証を通す・レビュー R1-1）。検証失敗時は追加した modifier を撤去する（アトミック）。

> `material`: 操作は `--action`（ENUM）。`create` は対象へ作成と同時に割当（create-and-assign）。`--color` は RGBA(VEC4)・create の Base Color。`targets`/`name` の必須は action 別（schema 上は任意・サーバが action ごとに検証）。スロットは active 置換・空なら追加。共有 mesh の **DATA slot** 書き込みは `--make-single-user` 必須（OBJECT リンク slot は object 限定で不要）。
>
> **PBR/テクスチャ（P2-3 G5・create 専用）**: `--metallic`/`--roughness`/`--alpha`（0..1・bpy 到達前に範囲検証）/`--emission r,g,b,a`（Emission Color。`--emission-strength` 省略時は **1.0 を明示設定**＝strength 既定 0 で発光が silent 無効化されるのを防ぐ）/`--texture <path>`（画像を読み込み **Image Texture ノードを Base Color に接続**・パス不在は bpy 到達前 USER_INPUT・壊れ画像は `E_OPERATOR`）/`--pack-texture`（画像を .blend にパック・`--texture` 必須）。Principled 入力名は両版同一（Metallic/Roughness/Alpha/Emission Color/Emission Strength・スパイク確定）で、欠如時は `E_PRECONDITION`（silent drop しない）。**`--color` と `--texture` 併用時**は Base Color 入力にノードが接続されるため color はビューポート表示色としてのみ有効。他 action（assign/list）でこれらを渡すと `INVALID_PARAMS`。設定失敗時は作りかけ material を撤去（アトミック）。result に `principled`（設定実値）/`texture`（image/path/packed）を返す。

> `delete` は削除前の object summary を `backup` として結果に常時含める（即実行・確認フラグなし）。`.blend` への退避バックアップ（`backup.on_overwrite`）は save 依存のため **M9 へ繰越**。`duplicate --count` は 1〜1000（暴走防止の上限・`bli_core.runtime.MAX_DUPLICATE_COUNT`）。

## シーングラフ生成/操作（P1-2・欠落プリミティブ第1弾: 設計レビュー 2026-07-11 §4 P1-2・G1/G2/G3）
| method | params | result | M | Mode | St |
|--------|--------|--------|:-:|----|:--:|
| `add` | `--type <T>` `--name?` `--location? --rotation? --scale?` `--light-type?`(type=light専用) | 生成後 object summary | ✓ | OBJECT | s |
| `mode` | `--to object\|edit\|sculpt\|vertex-paint\|weight-paint` `--targets?` `--regex?` | `{from_mode, to_mode, active}` | ✓ | ANY | s |
| `rename` | `--targets` `--regex?` `--name` `--with-data?` | `{old_name, new_name, data_renamed}` | ✓ | OBJECT | s |
| `parent` | `--targets`(複数可) `--regex?` `--to?\|--clear?` `--keep-transform?`(既定on) | `{action, results:[{name,parent}]}` | ✓ | OBJECT | s |
| `collection` | `--action create\|move\|link\|unlink\|list` `--name?`(list以外必須) `--targets?`(move/link/unlinkで必須) `--regex?` | action別（list=`{collections:[...]}` / 他=`{action,collection?,results?}`） | ✓ | OBJECT | s |

> **`add`（U4「樽の作成」対策・G1）**: `--type` は `cube`/`uv-sphere`/`ico-sphere`/`cylinder`/`cone`/`plane`/`torus`（mesh primitive）/`empty`/`light`（`--light-type POINT\|SUN\|SPOT\|AREA`・既定 POINT・presence-sensitive で他 type に渡すと `INVALID_PARAMS`）/`camera`/`text`。生成 operator（`mesh.primitive_*_add`/`object.*_add`）の実在は `capability.operator_real`（`get_rna_type()` 判定）で確認し、無ければ `CAPABILITY_UNAVAILABLE`。生成オブジェクトは **実行前後の `bpy.data.objects` 名差分**で特定する（`import` と同じ流儀・active_object 依存より決定的）。差分が1個でなければ `E_OPERATOR`（INTERNAL にしない）。`--location` のみ operator 引数で渡し、`--name`（衝突時は Blender が `.001` 等を付与し実名を返す）/`--rotation`(度)/`--scale` は生成後に直接プロパティへ反映する。
>
> **`mode`（U9「Edit モード放置」対策・G2）**: `bpy.ops.object.mode_set` を薄く包む。`--targets` 省略時は現在の active を対象にする。他モードからでも呼べる必要があるのがこのコマンドの存在意義そのもののため **required_mode=ANY**。active 不在・切替不能型（EMPTY へ edit 等）は `run_operator` の `poll()` が False を返し `E_PRECONDITION` に写像される（INTERNAL 化しない）。**`E_MODE_MISMATCH` の remediation はこのコマンドの実行方法（例: `bli mode --to object`）を案内する**（従来は「OBJECT モードに切り替えてください」という趣旨の文言のみで具体的な復帰手段が無かった）。
>
> **`rename`**: `--with-data` で `obj.data` も同名に変更する。要求名が既存と衝突した場合 Blender が `.001` 等へ実名を確定するため、結果は `old_name`/`new_name`（実名）の両方を返す。
>
> **`parent`（G3）**: `--to`（親名）と `--clear`（解除）は排他でどちらか必須（`INVALID_PARAMS`）。対象自身を親にしようとした場合、および親にしようとした対象が既に子孫（循環）になる場合は `E_PRECONDITION`（親の祖先チェーンを辿って検出）。`--keep-transform`（既定 on）は設定/解除いずれも見た目のワールド transform を保つ（**設定/解除とも world 行列を退避→復元**。設定時は `matrix_parent_inverse` も新親基準に再計算する。`matrix_parent_inverse` 単独方式は既に別の親を持つ子の付け替えで world が飛ぶため不採用・レビュー R1-2）。`targets` は複数可（`require_targets`）。
>
> **`collection`**: `create` は同名重複を `E_PRECONDITION` で拒否。`move` は対象を所属する全 collection から外して指定 collection のみへ link する（シーンから消えない）。`link` は既に link 済みなら静かに skip し結果で報告する（`{name, linked:false}`）。`unlink` は外すと対象の所属 collection が 0 になる場合（view layer から消える事故）を **全対象を検証してから** `E_PRECONDITION` で拒否し、remediation で `move` を促す（部分的に外して状態を汚さない）。`list` は scene の master collection 配下（子 collection）を再帰 walk した**フラットな配列**で返す（入れ子ツリーではない）。各要素は `{name, objects:<count>, children:[...]}` で、`children` は**直下の子 collection の名前（文字列）の配列**（子 collection 自体も配列のトップレベル要素として別途並ぶ・レビュー R1-6 で明確化）。collection 名の解決は `bpy.data.collections.get`（完全一致のみ）。

## メッシュ編集（bmesh 一次 / 単一 `mesh` コマンド + `--op`）
| method | params | result | M | H | Mode | St |
|--------|--------|--------|:-:|:-:|----|:--:|
| `mesh` | `--op <op>` `--targets` `[op別params]` `--make-single-user?` | op 別（法線統計 / マージ頂点数 / mesh統計） | ✓ | △ | OBJECT | e |

`mesh --op`（v1）= `recalc-normals` / `merge-by-distance`（**T7.1 実装済み**）/ `extrude` / `bevel` / `inset`（**T7.2 実装済み**）/ `boolean` / `decimate`（**T7.3 実装済み＝M7 完了**）。

> `mesh`: 操作は `--op`（ENUM）。material/modifier の `--action` と同じ流儀で、op 別 params は schema 上は任意・サーバが op 別に検証する（条件付き必須・無効 param は弾く）。**bmesh 一次**（recalc/merge/extrude/bevel/inset は `from_mesh`→`bmesh.ops`→`to_mesh`・object モードのまま編集＝context 非依存）。`boolean`/`decimate` のみ bmesh に相当が無く modifier add+apply 経由（後述）。mesh データを直接書き換える破壊的操作のため、共有 mesh は `--make-single-user` 必須。非 mesh 型は `E_PRECONDITION`。stability はコマンド単位なので（experimental op を含むため）`mesh` 全体を **experimental** とする。op 専用 param（`--inside`/`--distance`/`--offset`/`--width`/`--segments`/`--thickness`/`--operation`/`--with`/`--ratio`）は schema default を持たない（生成クライアントが既定値を別 op へ誤送信し op 別検証で弾かれるのを防ぐ）。
>
> op 別 params: ① `recalc-normals`:`--inside?` → `{faces, flipped, inside, stats}`（flipped=この操作で向きが変わった面数）。② `merge-by-distance`:`--distance?`（既定 0.0001・0 以上）→ `{merged, before, after, distance, stats}`。③ `extrude`:`--offset x,y,z`（**必須**・world 空間）→ `{offset, delta, stats}`。④ `bevel`:`--width`（**必須**・ローカル単位・0以上）`--segments?`（既定1・1〜100）→ `{width, segments, delta, stats}`。⑤ `inset`:`--thickness`（**必須**・ローカル単位・0以上）→ `{thickness, delta, stats}`。`stats`=`{vertices, edges, polygons}`（編集後）/ `delta`=before→after の増減（符号付き＝追加は正・削減は負。decimate/boolean でも一貫）。fingerprint は mesh 法線込みの専用 `mesh_fingerprint`（頂点数不変の recalc も検出できる）。
>
> `extrude --offset` は **world 空間**ベクトル（move/duplicate の `--offset` と一貫・matrix_world で world→local 変換）。`bevel --width` / `inset --thickness` はスカラ量のため **mesh ローカル単位**（非一様スケール下の world 幅は定義不能）。extrude=全 face を region 押し出し / bevel=全 edge を `affect=EDGES` / inset=全 face を `inset_individual`（閉じた mesh の全 face は `inset_region` だと no-op のため個別 inset）。Mode は当面 OBJECT 固定（bmesh-on-data・5.0.1/4.4.3 で確認）。
>
> op 別 params（T7.3・**heavy 候補**）: ⑥ `boolean`:`--operation UNION|DIFFERENCE|INTERSECT`（**必須**）`--with <mesh>`（**必須**・相手 mesh）→ `{operation, with_object, delta, stats}`。⑦ `decimate`:`--ratio 0..1`（**必須**）→ `{ratio, delta, stats}`。結果キー `with_object` は入力 `--with`（SSOT param `with_object`）と対称。`bmesh` に boolean/decimate 相当が無いため（スパイク §E3 で確認）、いずれも **BOOLEAN/DECIMATE モディファイアを追加して `modifier_apply` で焼き込む**（既存 `add_modifier`+`apply_modifier` を再利用・生 bpy.ops は gateway のみ・AST guard 準拠・apply 失敗時は追加 modifier を撤去）。boolean 相手の **world 位置は Blender が両者の matrix_world から解決**（手動変換不要）・相手は read-only（編集されない）・自己参照/非 mesh/相手不在は `INVALID_PARAMS(USER_INPUT)`/`E_TARGET_NOT_FOUND`。boolean/decimate は heavy 候補（同期実行・非同期 job は M10）。多ユーザ mesh への `modifier_apply` は Blender が拒否するため共有 mesh は `--make-single-user` 必須（ratio=1.0 等の実質 no-op でも mesh は焼き直される）。**注意**: boolean/decimate は対象を空/退化 mesh にし得る（INTERSECT で非重複・decimate `ratio→0`）。success は operator 完了を表し幾何的健全性は保証しない（`stats`/`delta` で確認）。対象に未適用の他 modifier がある場合、`modifier_apply` 仕様によりそれらも焼き込まれる（v1 は対象に他 modifier が無い前提）。

## 状態操作: undo / redo（実地フィードバック #3）
| method | params | result | M | Mode | St |
|--------|--------|--------|:-:|----|:--:|
| `undo` | `--steps?`(既定1・1〜100) | `{requested, applied}` + シーン fingerprint | ✓ | ANY | s |
| `redo` | `--steps?`(既定1・1〜100) | `{requested, applied}` + シーン fingerprint | ✓ | ANY | s |

> **`undo`/`redo`（実地フィードバック #3）**: グローバル undo スタック（ユーザーの GUI 操作も含む）を `--steps`（既定1・1〜`runtime.MAX_UNDO_STEPS`=100）段だけ戻す/進める。可逆性を「直前 transform の自力再構築」に頼らせず、試行錯誤の安全性を上げる。実機は bare `bpy.ops.ed.undo()`/`ed.redo()` を steps 回（GUI で context override 不要・**研究 §E7** で 5.0.1/4.4.3 確認）。result の `applied` は実際に適用できた段数（スタック端で頭打ち＝`requested` 未満になり得る）。**GUI 必須**で `--background` は `E_PRECONDITION` 縮退（本番は常駐 GUI Blender なので実用上問題なし・capture と同流儀）。Mode は ANY（モードを跨ぐ復元になり得るため一致を要求しない）。`steps` 範囲外は `INVALID_PARAMS(USER_INPUT)`。**v1 注記**: undo はグローバルスタックを戻すため、直前のコマンドだけでなくユーザーの手動操作も対象になり得る。返す fingerprint は名前/型/world 行列ベースの**粗い**シーン指標（mesh データ内部の編集までは見ない）。

## シナリオ1: 原点変更
| method | params | result | M | Mode | St |
|--------|--------|--------|:-:|----|:--:|
| `set-origin` | `--targets` `--to geometry\|cursor\|world` `--center median\|bounds?` `--x?--y?--z?` `--make-single-user?` | 新origin座標/verified | ✓ | OBJECT | s |

errors: `E_TARGET_NOT_FOUND` / `E_PRECONDITION(shared mesh: users>=2)` / `E_MODE_MISMATCH`

## シナリオ2: 直立補正
| method | params | result | M | Mode | St |
|--------|--------|--------|:-:|----|:--:|
| `straighten` | `--targets` `--method reset\|world-align\|pca\|floor\|angle\|align-vector\|reference` `--up-axis?`(既定 +Z) `--axis?`(world-align/reference=local 軸 / angle=回転 world 軸) `--up-hint?`(pca: auto\|current) `--degrees?`(angle) `--from-dir? --to-dir?`(align-vector) `--reference? --ref-axis?`(reference) `--dry-run?` `--bake-rotation?` `--make-single-user?` | 補正後回転/整列軸/接地Z/`tilt_from_up_deg`(pca)/`angle_deg`(align-vector) | ✓ | OBJECT | s |

> **`--up-hint`（pca 専用・実地フィードバック #5）**: PCA は主成分の符号が不定。`auto`（既定）は原点→重心方向で符号決定。`current` は**現在の up に近い側を +** にする＝最小回転で合わせ、ベースが重いスキャン物体で起きる**上下反転を防ぐ**。pca 結果は `tilt_from_up_deg`（up からの傾き角・符号非依存の鋭角）を含む。
> **`--dry-run`（#2）**: 適用せず計画（`rotation_euler_deg` / `tilt_from_up_deg` / `principal_world*`）のみ返す。内部は適用→読取→**厳密復元**で副作用なし（push_undo もしない）。非破壊の傾き計測（#6）にも使える。
> **基準指定 method（#4・エージェント算出の補正を straighten 経由で安全に適用＝`transform` 迂回の解消）**: いずれも **object 回転のみ**で、dry-run / bake / 共有 mesh ガードの作法を他 method と共有する。**angle**=world 軸 `--axis X\|Y\|Z` まわりに `--degrees`（符号で向き）回転（result `{axis, degrees}`）。**align-vector**=`--from-dir`（world 方向）を `--to-dir`（world 方向・**省略時は up_axis**）へ最小回転で合わせる（result `{from_dir, to_dir, from_world_after, angle_deg}`・`from_dir`/`to_dir` は**正規化して返す**）。同一メッシュ内の支柱など別オブジェクト化できない基準でも、向きを数値で渡せば安全に整列でき、`transform --mode delta` での手計算迂回を不要にする（実地検証の主訴）。**reference**=参照オブジェクト `--reference` の `--ref-axis`（signed local・省略時 up_axis）の world 方向へ、対象の `--axis`（local・省略時は最近軸を自動）を合わせる（world-align の目標を up→参照軸へ差し替え・result は world-align と同じ `{axis(signed), aligned_world}` + `{reference, ref_axis, reference_world}`）。`--reference` に対象自身は不可（`INVALID_PARAMS`）。op 専用 param（`--degrees`/`--from-dir`/`--to-dir`/`--reference`/`--ref-axis`）は presence-sensitive で別 method に渡すと `INVALID_PARAMS`。**v1 未保証**: align-vector の最小回転は up 周りの yaw を保存しない（向きは不定・pca/world-align と同じ）。reference は参照の matrix_world 回転成分で目標方向を取る（非一様/シアスケール下は近似）。

> `straighten`: 対象は単一（`require_single`・set-origin と対称）。**reset** は回転を identity に / **world-align** は指定（`--axis X\|Y\|Z`）または **省略時は up に最も近い signed local 軸を自動選択**して `--up-axis`（既定 +Z）へ最小回転で合わせる / **pca** は頂点分布の最大分散軸を up へ（符号は原点→重心方向で一意化・numpy.linalg.eigh） / **floor** は up 方向の最下点を up=0 平面へ接地（平行移動のみ）。reset/world-align/pca は **object 回転のみ**・floor は **平行移動のみ**変更し mesh データは触らない（共有 mesh でも安全・ガード不要）。`--bake-rotation` のときだけ回転を mesh データへ焼き込む（apply-transform rotation 経路を再利用）破壊的操作になり、共有 mesh は `--make-single-user` 必須。`--axis` は world-align 専用（他 method では `INVALID_PARAMS`）。pca は mesh 型・floor はジオメトリ（bbox）が必要で、非対応は `E_PRECONDITION`。result: `{method, up_axis, rotation_euler_deg, baked}` + world-align は `{axis(signed), aligned_world}` / pca は `{principal_world, principal_world_after, eigenvalues}` / floor は `{floor_offset}`。`min_up`（up 方向の最下点）は **bbox を持てば全 method で常時付与**（floor 後は ≈0）。fingerprint は非 bake=object_fingerprint / bake=mesh_fingerprint（mesh へ焼き込むため・§6e）。DoD: 補正後の整列軸（world-align=aligned_world / pca=principal_world_after）が world up と一致（ゴールデン・5.0.1/4.4.3 同値）。**v1 未保証**: 親付き対象・非一様/シアスケール下の整列精度（matrix_world の回転成分で近似）・中心対称 mesh の pca 符号（正準 tie-break で決定化）・複合 tilt の up 周り yaw 残留（最小回転のため向きは不保証）。

## シナリオ3: 3Dプリンタ対応
| method | params | result | M | H | Cap | St |
|--------|--------|--------|:-:|:-:|----|:--:|
| `print-check` | `--targets` `--manifold?` `--normals?` `--degenerate?` `--thin --min-thickness?` `--intersect?` | チェック結果（大→output_ref） | - | ✓ | thin/intersect のみ print3d | s |
| `print-repair` | `--targets` `--make-manifold?` `--recalc-normals?` `--remove-degenerate?` `--make-single-user?` | 修復前後差分 | ✓ | ✓ | - | s |
| `print-setup` | `--unit mm\|m`(既定 mm) `--scene?` | 単位設定後の値 | ✓ | - | - | s |
| `print-export` | `--targets` `--format stl\|3mf` `--path` `--ascii?` `--scale?` `--apply-modifiers?` | 出力パス/サイズ/sha256/三角形数 | - | ✓ | export.stl（3mf は未導入） | s |

> `print-export --format 3mf` 不可時は STL フォールバックを hint。
>
> `print-export`（**T8.5 実装済み・読み取り専用＝mutates=false**）: 対象1個（`require_single`）の mesh を STL で書き出す。STL は `wm.stl_export`（両版同一引数・研究 §E8）で `export_selected_objects=True`（対象だけ選択して出力・選択は save/restore で非破壊）・world 空間でジオメトリを焼く（オブジェクト transform は常に適用）。`--scale` は `global_scale` 一本化（既定 1.0＝Blender 単位を STL に 1:1）で、`use_scene_unit=False` 固定で `scale_length` を出力へ反映させない（**`global_scale` を唯一の真実・`scale_length` は検証専用＝1000倍ずれ防止**・§E8）。`--ascii` で ASCII STL（既定 binary）。`--apply-modifiers`（既定 on）でモディファイア適用後の最終形を出力（`--no-apply-modifiers` で素の mesh）。**`--apply-transform` は廃止**（STL は常に world 焼きのため冗長＝spec 当初案からの実機追従・§E8）。`--format 3mf` は両版とも export operator が実体なし（§E8）→ **`CAPABILITY_UNAVAILABLE`（category=ENVIRONMENT）+ STL フォールバック hint**（黙って STL に差し替えない）。出力先ディレクトリ不在は USER_INPUT で bpy 到達前に弾く。result `{name, path（絶対）, size, sha256, triangles, format, ascii, global_scale, apply_modifiers, scale_length}`。fingerprint=出力ファイルの content-address（sha 先頭16桁・binary STL は決定的・capture と同流儀）。`print-export` は heavy 候補（同期実行・非同期 job は M10）。
>
> `print-check`（**T8.4 実装済み・読み取り専用**）: `manifold`（非多様体辺）/`normals`（反転法線）/`degenerate`（退化面）は **bmesh 自前計算**（print3d 非依存・常時 stable・研究 §E6）。カテゴリ flag は presence-sensitive で、省略時は bmesh 3種すべて・指定時はそのサブセットのみ報告。result `{name, checked:[...], checks:{non_manifold_edges, boundary_edges, wire_edges, loose_verts, is_manifold, flipped_normals, normals_consistent, degenerate_faces, is_printable}}`（要求カテゴリのキーのみ + `is_printable` は常時）。`is_printable`=致命カテゴリ（非多様体/反転法線/退化面）が全 0。`--thin`（薄壁・`--min-thickness` 専用）/`--intersect`（自己交差）は **print3d 依存**で、未導入時（`addon_utils.enable` 試行も失敗・§E6 で両版実体なし）は **`CAPABILITY_UNAVAILABLE`（category=ENVIRONMENT）**。大きい結果は output_ref 退避（M5・_ok_offload）。fingerprint=mesh_fingerprint（検査した mesh 状態の確定）。**`--save-to`（ファイル書き出し）は M9 ファイルI/O へ繰越**。
>
> `print-repair`（**T8.4 実装済み・破壊的**）: `make-manifold`（退化除去 + 重複マージ + loose 除去 + 穴埋め holes_fill）/`recalc-normals`（面法線一貫化）/`remove-degenerate`（dissolve_degenerate）を **bmesh 自前**で実行（print3d 非依存）。フラグは presence-sensitive で **全省略時は全修復**。**完全修復は保証しない**（spec §10 S3・穴形状により埋めきれない）。result `{name, applied:[...], before, after, fixed:{non_manifold_edges, flipped_normals, degenerate_faces}}`（`fixed`=致命カテゴリの改善数＝正で減少）。mesh データを書き換えるため共有 mesh は `--make-single-user` 必須。fingerprint=mesh_fingerprint。`print-check`/`print-repair` は heavy 候補（同期実行・非同期 job は M10）。
>
> `print-setup`（**T8.3 実装済み**）: シーンの **表示単位** を設定する（`scene.unit_settings.system='METRIC'` + `length_unit=MILLIMETERS|METERS`）。`--unit` 既定 mm（`bli print-setup` 単体で mm）。`--scene?` で対象シーン名（省略時 active・無ければ `E_TARGET_NOT_FOUND`）。**`length_unit` は表示専用で geometry（dimensions/頂点）を再スケールしない＝非破壊**（研究 §E5）ため共有 mesh ガード不要。実寸の export スケールは print-export（T8.5）が `scale_length`/単位から一本で算出する（global_scale 一本化）。result: `{scene, unit, unit_settings:{system, scale_length, length_unit}, changed}`（`changed`=設定前後で変化したか＝冪等性指標）。required_mode=OBJECT。fingerprint=unit_settings の決定的ハッシュ。

## ファイルI/O
| method | params | result | M | H | St |
|--------|--------|--------|:-:|:-:|:--:|
| `save` | `--path?` `--backup?` | 保存パス | ✓ | △ | s |
| `open` | `--path` `--force?` | シーン要約 | ✓ | ✓ | s |
| `import` | `--format obj\|fbx\|gltf\|stl\|3mf` `--path` | 取込オブジェクト一覧 | ✓ | ✓ | s |
| `export` | `--format obj\|fbx\|gltf\|stl\|3mf` `--path` `--targets?` `--use-selection?` `--axis-forward?` `--axis-up?` `--scale?` `--apply-unit-scale?` `--embed-textures?`（axis/scale/apply-unit-scale/embed-textures は fbx 専用・P1-3） | 出力パス | - | ✓ | s |

> `export`（**M9 T9.1 実装済み・読み取り専用** mutates=False）: print-export（3Dプリント特化・単一+global_scale 一本化）を多形式へ一般化（研究 §E9）。セレクタは `--targets <name\|regex>` 指定=その集合を選択して書き出す / `--use-selection`=現在の選択集合 / どちらも省略=シーン全体。selection 制御 param は形式別（stl/obj=`export_selected_objects` / gltf/fbx=`use_selection`）で gateway が写像。scale は 1.0 素通し（3Dプリント用スケールは print-export が窓口・gltf は scale param 自体が無い）。**glTF は GLB 単一固定で `--path` は `.glb` 必須**（`export_format` 有効値は両版とも GLB/GLTF_SEPARATE のみ・SEPARATE は .bin 分離で統計が崩れるため不採用・GLTF_EMBEDDED は存在せず）。**`--format 3mf` は両版とも export operator 不在（§E8）→ `CAPABILITY_UNAVAILABLE`（category=ENVIRONMENT）+ 別形式 hint**（黙って差し替えない）。result `{path(絶対), size, sha256, format, operator, use_selection, exported_objects}`（exported_objects はシーン全体時 null・fbx 専用オプション指定時は `fbx_options` も付与）。fingerprint=出力ファイルの content-address（sha 先頭16桁・STL は決定的 / gltf・fbx は環境で変わり得るため版間 golden は往復 bbox で検証）。選択は save/restore で非破壊。
>
> **fbx 専用オプション**（**P1-3 実装済み・Unity/ゲームエンジン向け**）: `--axis-forward X\|Y\|Z\|-X\|-Y\|-Z`（既定 -Z）/ `--axis-up X\|Y\|Z\|-X\|-Y\|-Z`（既定 Y）/ `--scale <f>`（`global_scale` へ写像・既定 1.0・0/負値は退化/反転のため bpy 到達前に `INVALID_PARAMS`）/ `--apply-unit-scale/--no-apply-unit-scale`（既定 Blender 既定 on・presence-sensitive で3状態）/ `--embed-textures`（`path_mode='COPY'` を自動設定＝COPY でないと Blender は embed_textures を無視する仕様のため）。両版実機確定（`export_scene.fbx` のプロパティは 5.0.1/4.4.3 で完全同一）。いずれも **`--format fbx` 以外に指定すると `INVALID_PARAMS`**（silent ignore しない・§6e の presence-sensitive 方針）。写像は gateway 側で解決済み operator の rna properties を検査してから適用し、**写像先プロパティが1つでも無ければ silent drop せず `CAPABILITY_UNAVAILABLE`** で拒否する（将来 operator が差し替わっても黙って無視しない防御・バージョン番号分岐は行わない）。result に `fbx_options`（適用した値のみ）を追加する。Unity 取込レシピは `.claude/skills/bli/SKILL.md` 参照（Blender の FBX 既定はそのまま Unity に合う）。

> `import`（**M9 T9.2 実装済み・mutates=True**）: export と対称に operator を能力検出で解決（`import.<fmt>`・RESOLVERS）→不在は CAPABILITY_UNAVAILABLE。取込オブジェクトは **import 前後の `bpy.data.objects` 差分**で特定（名前衝突時 Blender が `.001` 等へリネームするため集合差が唯一信頼できる方式）。scale 引数は渡さず operator 既定（=1.0 相当）に委ねる（取込後の単位補正は transform）。**FBX import の版差**（5.0=`wm.fbx_import` / 4.4=`import_scene.fbx`）は RESOLVERS 優先順で吸収。**`--format 3mf` は両版とも import operator 不在（§E8）→ `CAPABILITY_UNAVAILABLE`**。入力ファイル不在は bpy 到達前に `INVALID_PARAMS`（USER_INPUT）・壊れたファイルは `E_OPERATOR`（INTERNAL にしない）。result `{format, operator, path(絶対), imported:[{name,type}], count}`・fingerprint=取込名集合の決定的ハッシュ。シーンにオブジェクトを足す破壊的操作（取込物は選択状態で残る）・大量取込は output_ref 退避（_ok_offload）。往復 golden（export→import で world bbox 一致）は smoke + 研究 §E9。

> `save`（**M9 T9.3 実装済み・mutates=True・Mode.ANY**）: 現在のシーンを `.blend` に保存（`wm.save_as_mainfile`・研究 §E10）。target = `--path`（絶対化・`.blend` 必須）/ 省略時は現在ファイル（`bpy.data.filepath`・未保存=空なら USER_INPUT）。**上書きは既定でバックアップ**（`--backup` 既定 on・spec §セキュリティ「上書きは既定でバックアップ強制」）で、preferences `save_version` を `1 if backup else 0` に一時上書きして native `.blend1` 機構を決定的に制御し restore する（preference 非汚染・逐次処理前提で安全）。`--no-backup` で抑止。保存先 dir 不在は USER_INPUT。result `{path(絶対), size, backed_up, backup_path}`（backed_up は **保存後に `.blend1` の実在を確認**して確定＝偽報告防止・backup_path=`<target>1`）。fingerprint=metadata digest（path|size・.blend 全体 sha は大容量/非決定的のため不採用）。保存 .blend の magic は版差（4.4=非圧縮 `BLENDER` / 5.0=zstd 圧縮）。

> `open`（**M9 T9.4 実装済み・mutates=True・Mode.ANY**）: `.blend` を開いてシーン全体を置換する（`wm.open_mainfile`・研究 §E11）。target = `--path`（絶対化・`.blend` 必須・save と対称）。**未保存ガード**: bli が最後の save/open 以降に mutating コマンドを実行していたら（自前の `session_state` 追跡）、open はその未保存変更を不可逆に失うため **`--force` なしは `E_PRECONDITION`** で拒否する。`--force`（既定 off）で破棄して開く。なぜ `bpy.data.is_dirty` を使わないか: dispatch（pump タイマ）文脈では **save 後も is_dirty が False に戻らず**、background では常時 True で信頼できない（§E11 実測）。`session_state` は dispatch で「mutates=True コマンドの実行 **前** に modified（pre-mark）/ save・open 成功で clean」と遷移する。**実行前 pre-mark の理由**: 途中まで mutate して例外を投げるハンドラ（例: material の create 後に assign 失敗）でも open が未保存を検知して --force を要求する **安全側** に倒すため（実行後フックだと partial mutation を取りこぼし silent data loss）。v1 は静的 `mutates` フラグ判定で **保守的**（select/undo や、検証失敗で何も変えなかった mutating コマンドも modified 扱い＝open に --force が要る安全側）・**bli 由来の変更のみ**追跡（GUI で人間が直接した編集/Ctrl+S は対象外）。per-invocation な dirtied 信号への精緻化は繰越。ファイル不在は bpy 到達前 `INVALID_PARAMS`（USER_INPUT）・壊れ/ロック `.blend` は `E_OPERATOR`（RuntimeError 以外＝OSError 等も写像し INTERNAL にしない）。result `{path(絶対), scene, object_count, forced, discarded_unsaved}`（`object_count`=開いたシーンの総オブジェクト数・scene-info と命名統一 / `discarded_unsaved`=--force で実際に未保存変更を破棄したか）。fingerprint=`scene_state_fingerprint`（開いたシーンの name/type/matrix 粗い指標・undo/redo と共通）。**常駐 GUI の安全性**: open_mainfile はシーン全体を置換するが、Dispatcher の **persistent pump タイマ / `bli-accept` TCP スレッドは open を跨いで生存**し、open を含む 1 ジョブ内で結果構築→return も成立する（§E11 で両版 GUI 実機確定＝**再登録不要**）。

## 逃げ道
| method | params | result | M | St |
|--------|--------|--------|:-:|:--:|
| `exec-python` | `--code\|--file` | `{mode, stdout, stderr, result_repr, security_guarantee:false, heuristic_flags, code_sha256, audit_ok}` | ✓ | e |

> `exec-python`（**M11 実装済み・mutates=True・Mode.ANY・サンドボックスなし**）: 構造化サブコマンドで表現できない操作のフォールバック（spec D3 ハイブリッドの逃げ道）。**`--code` と `--file` は排他**（CLI は `--file` を CLI 側の CWD 基準で読み、code として送る＝Blender プロセスの CWD と区別。サーバ側 file 読取は直接 RPC 用フォールバック）。実行は既存 Dispatcher のメインスレッド直列で実 bpy 上を走る（新 timer/handler 機構なし＝研究 §E14）。`bpy` を namespace に注入し、最後の文が式ならその repr を `result_repr` に載せる（REPL 流儀・None は抑制）。stdout/stderr を分離キャプチャ。
>
> **mode ゲート（R-A・M11 の肝）**: exec の可否（off/restricted/audited/trusted）の**真実源はサーバが読むユーザローカル `policy.toml`**（`BLI_STATE_DIR/policy.toml`・OS 所有者限定・git 非管理）。**CLI は mode を送らない＝CLI フラグ単体では昇格できない**。リポジトリ内の `.bli/config.toml` の `[exec] mode` は表示用ヒントに過ぎず、サーバは読まない（mode=trusted を commit しても昇格しない・spec §276/§459）。`mode==off` または `mode==audited` で未許可は **`EXEC_DISABLED`**（PRECONDITION・retryable=False）。policy 読取は実行ごとに最新化（trusted→off の切替を即反映）。表示/編集は `bli policy`（後述・CLI ローカル完結）で行う。
>
> - **off**: 常に `EXEC_DISABLED`（file は読まずに拒否）。
> - **restricted（P1-1・設計レビュー 2026-07-11 G0・エージェント用途の推奨）**: `exec_restricted.scan_blocked` の AST ブロックリスト検査で自走可否を決める。`bpy`/`bmesh`/`mathutils` 等 **Blender API は全面許可**。プロセス起動/並行プロセス/FFI/動的ロード/ネットワーク/シリアライズ実行系モジュールの import、`os`/`shutil` のプロセス起動・削除系**属性呼び出し**（`import os as x` の別名・`from os import system` も追跡）、`eval`/`exec`/`compile`/`__import__`/`breakpoint`/`input` の呼び出し、組込み `open` の書込モードを検出したら **`EXEC_BLOCKED_RESTRICTED`**（PRECONDITION・retryable=False）で拒否し、検出理由（`import:<module>` / `attr-call:<module>.<attr>` / `from-import:<module>.<name>` / `call:<builtin>` / `file-write`）を症状文に列挙する。検出が無ければブロックリスト検査の観点では自走実行する（他モードと同じ実行経路・下記 `heuristic_flags` は別レイヤで独立に付与）。
> - **trusted**: 無条件で実行。
> - **audited（T11.3・R-B）**: コードの sha256 が `policy.toml` の `[exec] allow_hashes`（小文字16進の配列）に**一致するときだけ自走実行**。不一致は `EXEC_DISABLED` で、remediation に**追加すべき sha256 を提示**する（応答の `code_sha256` か拒否メッセージの sha を `allow_hashes` に足せば次回から自走）。
>
> **監査ログ（T11.3・spec §280「防止でなく検知」）**: exec の**試行はすべて** `BLI_STATE_DIR/audit/exec.jsonl`（JSONL・1行1イベント）に追記される＝trusted/audited/restricted の `executed` も、off / audited 未許可の `rejected:*` も、**restricted のブロック（`rejected:restricted-blocked`・P1-1）**も記録（`ts`・`mode`・`decision`・`code_sha256`・`code_len`・`heuristic_flags`・`source`・restricted のみ `blocked`）。サンドボックス非提供の代償としての事後追跡。書込は best-effort＝失敗しても exec は止めず応答 `audit_ok=false` で証跡欠落を観測可能にする（可用性優先・v1）。
>
> **サンドボックスは提供しない**（spec §459・確定判断）。実行コードは同一 OS 権限で走る＝`security_guarantee` は**常に false**（過信させない・restricted でも不変）。`heuristic_flags`（**T11.2 実装済み**）は AST 走査による注意喚起で、**ブロックはしない**（restricted の拒否判定＝`exec_restricted.scan_blocked` とは別レイヤ・全モード共通で付与）。語彙: `import:<top-module>`（os/subprocess/socket/shutil/urllib/ctypes/pickle/... ＝プロセス/ネットワーク/FS/シリアライズ系。`import os.path`/`from urllib.request import ...` は top で集約）/ `call:eval`・`call:exec`・`call:compile`・`call:__import__` / `file-write`（`open(...,'w'/'a'/'x'/'+')`・非定数 mode は保守的に flag）。ヒューリスティックゆえ false negative あり（属性経由・別名束縛は捕捉しない）＝安全保証ではない。restricted のブロックリスト検査（`scan_blocked`）も同様に**完全ではない**（`getattr` 迂回・多段の別名束縛・`pathlib` 経由の削除等は捕捉できない）＝事故防止であり悪意対策ではない。ユーザコードの実行時例外は **`EXEC_ERROR`**（runtime=ENVIRONMENT / 構文エラー=USER_INPUT・compile フェーズ）へ写像し INTERNAL にしない（実行段は `except BaseException`＝`sys.exit()` 等が dispatch を巻き込まないようにし、例外直前の stdout/stderr は error の cause に載せる）。大出力は output_ref 退避。
>
> **job 化しない（M10 との非対称・v1 制約）**: exec は `is_heavy=False`＝既存 Dispatcher のメインスレッド**同期**実行。他の heavy 操作（import/export/mesh decimate）は accepted 即返で接続を塞がないが、exec は重量コード（重い `bpy.ops`・無限ループ等）でメインを同期占有し得る＝**M10 watchdog（`MAIN_THREAD_UNRESPONSIVE`）の通知対象**。将来 job 化する場合は bpy 注入 namespace の job ワーカー生存が論点。
>
> **`--file` のパス封じ込めは意図的に課さない**: CLI は `--file` を CLI 側 CWD で読み code 送信。サーバ側 file 読取（直接 RPC 用フォールバック）は存在確認のみで配下制限しない＝**trusted 前提ではユーザコードが `open()` で任意ファイルを読めるため confinement は security 価値が無い**（NEXT-M11 §48 の「outputs/state 配下逸脱拒否」は入力スクリプトには不適＝不採用）。`isfile` 不在は USER_INPUT、読取失敗は EXEC_ERROR(compile)。
>
> `policy`（**P1-1・CLI ローカル完結・RPC を送らない**）: `--action show` は `policy.toml` のパス/現在の mode（fail-closed 解決値）/`allow_hashes` 件数を表示する（ファイル不在でも動作し mode=off と表示）。`--action set --mode <m>` は現在値→新値を提示し、`--yes` が無ければ対話確認（拒否/非対話は中断）。書き込み前に既存 `policy.toml` を検査し、`[exec]` 以外のセクションや `mode`/`allow_hashes` 以外のキーがあれば**自動編集を拒否**して手動編集を案内する（他の設定を静かに失わないため）。書き込みは `[exec] mode` + 既存 `allow_hashes` を保持した決定的な TOML（所有者限定権限・原子的置換）。**このコマンドはサーバへ何も送らない**＝R-A（CLI フラグ単体では昇格できない）はこのコマンドでも不変で、昇格は人間が `bli policy --action set` を明示実行（またはエディタで直接編集）したときだけ成立する。
