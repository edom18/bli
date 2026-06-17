# bli — RPCメソッドカタログ (methods.md)

凡例: **M**=mutates(状態変更) / **H**=heavy(非同期job) / **Mode**=required_mode / **Cap**=capability_deps / **St**=stability(s=stable/e=experimental)

> 各メソッドは `bli <method>` CLI サブコマンドに 1:1 対応。params は `bli-core` の Command 定義が真実。

---

## 接続・診断（ローカル完結 / 一部はRPC前）
| method | params | result | M | H | Mode | St |
|--------|--------|--------|:-:|:-:|----|:--:|
| `init` | `--port?` `--force?` | token生成・connection.json・.bli/雛形 | - | - | - | s |
| `doctor` | - | blender検出/addon導入/port/version/能力の診断 | - | - | - | s |
| `ping` | - | hello往復→protocol/blender版/capabilities | - | - | ANY | s |
| `request-status` | `--id` | RequestRegistryの状態（PENDING/RUNNING/DONE/FAILED） | - | - | ANY | s |
| `job-status` / `job-wait` | `--id` `--timeout?` | 非同期jobの状態/結果 | - | - | ANY | s |
| `help` | `--json?` `--command?` | コマンドスキーマ（machine可読） | - | - | - | s |
| `list-commands` | `--json?` | メソッド一覧 | - | - | - | s |

## 情報取得（読み取り専用）
| method | params | result | M | H | Mode | St |
|--------|--------|--------|:-:|:-:|----|:--:|
| `scene-info` | `--depth?` | シーン階層/オブジェクト一覧/単位（大→output_ref） | - | △ | OBJECT | s |
| `list-objects` | `--type?` `--regex?` | フィルタ済み一覧 | - | - | OBJECT | s |
| `object-info` | `--targets` | 寸法/頂点数/transform/bbox/材質/modifier | - | - | OBJECT | s |
| `capture` | `--source viewport\|screen\|render` `--width?` `--height?` `--camera?`(render) | PNG パス/サイズ/sha256/解像度 | - | - | ANY | s |

> **`capture`（実地フィードバック #1）**: 現在の状態を画像で取得する（エージェントの「現状確認」手段）。`viewport`=gpu offscreen で描画（UI なし・`--width/--height` 指定可・既定）/ `screen`=ビューポート領域をそのまま screenshot（領域サイズ固定で width/height 不可）/ `render`=カメラからレンダ（`--camera` 省略時 active）。読み取り専用（render 設定は save/restore で非破壊）。PNG は `outputs_dir`（git 非管理・shared-fs・コンテンツアドレス名）に書き出しパスを返す。`viewport`/`screen` は GUI 必須（`--background` では `E_PRECONDITION`）。Spike V で 5.0.1/4.4.3 両版確認。

> **`dimensions` と `bbox.size` の違い**（紛らわしいので明記）: `dimensions` は **オブジェクト固有サイズ**（`obj.dimensions`・scale 反映・**回転不変**）。`bbox.size` は **world AABB**（`matrix_world @ bound_box` の軸並行境界・**回転すると変化**）。傾いた物体では両者は一致しない。`--targets` は `--target`（単数）も別名で受け付ける。

## 汎用編集（オブジェクト操作）
| method | params | result | M | H | Mode | St |
|--------|--------|--------|:-:|:-:|----|:--:|
| `select` | `--targets` `--type?` `--active?` | 選択結果/fingerprint | ✓ | - | OBJECT | s |
| `transform` | `--targets` `--location?` `--rotation?` `--scale?` `--mode set\|delta` | 適用後transform | ✓ | - | OBJECT | s |
| `apply-transform` | `--targets` `--location?` `--rotation?` `--scale?` | verified | ✓ | - | OBJECT | s |
| `duplicate` | `--targets` `--linked?` `--count?`(1〜1000) `--offset?` | 新オブジェクト名 | ✓ | - | OBJECT | s |
| `delete` | `--targets` | 削除結果（削除前 summary を backup として常時返却） | ✓ | - | OBJECT | s |
| `material` | `--action assign\|create\|list` `--targets?` `--name?` `--color r,g,b,a?` `--make-single-user?` | 材質状態（list は slot/name/link/base_color） | ✓ | - | OBJECT | s |
| `modifier` | `--action add\|remove\|list\|apply` `--targets` `--type?` `[type別params]` `--make-single-user?` | modifier状態（list は name/type/型別値） | ✓ | - | OBJECT | s |

`modifier --type`（v1必須）: `MIRROR` / `SUBSURF` / `SOLIDIFY` / `DECIMATE` / `BOOLEAN`。

> `modifier`: 操作は `--action`（ENUM）。`--type` は add で必須（schema 上は任意・サーバが action 別に検証）。型別 params（**add 専用**）= MIRROR:`--axis X\|Y\|Z` / SUBSURF:`--levels`(0〜6) / SOLIDIFY:`--thickness` / DECIMATE:`--ratio`(0〜1) / BOOLEAN:`--operation`+`--with`(相手mesh・必須)。`remove`/`apply` は `--name` 必須。**apply のみ** mesh へ焼き込む破壊的操作で、共有 mesh は `--make-single-user` 必須（add/remove/list はオブジェクト単位で不要）。非対応型は `E_PRECONDITION`。

> `material`: 操作は `--action`（ENUM）。`create` は対象へ作成と同時に割当（create-and-assign）。`--color` は RGBA(VEC4)・create の Base Color。`targets`/`name` の必須は action 別（schema 上は任意・サーバが action ごとに検証）。スロットは active 置換・空なら追加。共有 mesh の **DATA slot** 書き込みは `--make-single-user` 必須（OBJECT リンク slot は object 限定で不要）。

> `delete` は削除前の object summary を `backup` として結果に常時含める（即実行・確認フラグなし）。`.blend` への退避バックアップ（`backup.on_overwrite`）は save 依存のため **M9 へ繰越**。`duplicate --count` は 1〜1000（暴走防止の上限・`bli_core.runtime.MAX_DUPLICATE_COUNT`）。

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
| `open` | `--path` | シーン要約 | ✓ | ✓ | s |
| `import` | `--format obj\|fbx\|gltf\|stl\|3mf` `--path` | 取込オブジェクト一覧 | ✓ | ✓ | s |
| `export` | `--format obj\|fbx\|gltf\|stl\|3mf` `--path` `--targets?` `--use-selection?` | 出力パス | - | ✓ | s |

> `export`（**M9 T9.1 実装済み・読み取り専用** mutates=False）: print-export（3Dプリント特化・単一+global_scale 一本化）を多形式へ一般化（研究 §E9）。セレクタは `--targets <name\|regex>` 指定=その集合を選択して書き出す / `--use-selection`=現在の選択集合 / どちらも省略=シーン全体。selection 制御 param は形式別（stl/obj=`export_selected_objects` / gltf/fbx=`use_selection`）で gateway が写像。scale は 1.0 素通し（3Dプリント用スケールは print-export が窓口・gltf は scale param 自体が無い）。**glTF は GLB 単一固定で `--path` は `.glb` 必須**（`export_format` 有効値は両版とも GLB/GLTF_SEPARATE のみ・SEPARATE は .bin 分離で統計が崩れるため不採用・GLTF_EMBEDDED は存在せず）。**`--format 3mf` は両版とも export operator 不在（§E8）→ `CAPABILITY_UNAVAILABLE`（category=ENVIRONMENT）+ 別形式 hint**（黙って差し替えない）。result `{path(絶対), size, sha256, format, operator, use_selection, exported_objects}`（exported_objects はシーン全体時 null）。fingerprint=出力ファイルの content-address（sha 先頭16桁・STL は決定的 / gltf・fbx は環境で変わり得るため版間 golden は往復 bbox で検証）。選択は save/restore で非破壊。

> `import`（**M9 T9.2 実装済み・mutates=True**）: export と対称に operator を能力検出で解決（`import.<fmt>`・RESOLVERS）→不在は CAPABILITY_UNAVAILABLE。取込オブジェクトは **import 前後の `bpy.data.objects` 差分**で特定（名前衝突時 Blender が `.001` 等へリネームするため集合差が唯一信頼できる方式）。scale 引数は渡さず operator 既定（=1.0 相当）に委ねる（取込後の単位補正は transform）。**FBX import の版差**（5.0=`wm.fbx_import` / 4.4=`import_scene.fbx`）は RESOLVERS 優先順で吸収。**`--format 3mf` は両版とも import operator 不在（§E8）→ `CAPABILITY_UNAVAILABLE`**。入力ファイル不在は bpy 到達前に `INVALID_PARAMS`（USER_INPUT）・壊れたファイルは `E_OPERATOR`（INTERNAL にしない）。result `{format, operator, path(絶対), imported:[{name,type}], count}`・fingerprint=取込名集合の決定的ハッシュ。シーンにオブジェクトを足す破壊的操作（取込物は選択状態で残る）・大量取込は output_ref 退避（_ok_offload）。往復 golden（export→import で world bbox 一致）は smoke + 研究 §E9。

## 逃げ道
| method | params | result | M | St |
|--------|--------|--------|:-:|:--:|
| `exec-python` | `--code\|--file` | 実行結果（既定 `EXEC_DISABLED`） | ✓? | e |

> `exec-python` は config `exec.mode` が off の場合 `EXEC_DISABLED` を返す。audited/trusted は設定昇格時のみ。レスポンスに `security_guarantee:false` / `heuristic_flags`。
