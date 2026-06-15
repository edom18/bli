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

> `mesh`: 操作は `--op`（ENUM）。material/modifier の `--action` と同じ流儀で、op 別 params は schema 上は任意・サーバが op 別に検証する（条件付き必須・無効 param は弾く）。**bmesh 一次**（`from_mesh`→`bmesh.ops`→`to_mesh`・object モードのまま編集＝context 非依存）。mesh データを直接書き換える破壊的操作のため、共有 mesh は `--make-single-user` 必須。非 mesh 型は `E_PRECONDITION`。stability はコマンド単位なので（experimental op を含むため）`mesh` 全体を **experimental** とする。op 専用 param（`--inside`/`--distance`/`--offset`/`--width`/`--segments`/`--thickness`）は schema default を持たない（生成クライアントが既定値を別 op へ誤送信し op 別検証で弾かれるのを防ぐ）。
>
> op 別 params: ① `recalc-normals`:`--inside?` → `{faces, flipped, inside, stats}`（flipped=この操作で向きが変わった面数）。② `merge-by-distance`:`--distance?`（既定 0.0001・0 以上）→ `{merged, before, after, distance, stats}`。③ `extrude`:`--offset x,y,z`（**必須**・world 空間）→ `{offset, delta, stats}`。④ `bevel`:`--width`（**必須**・ローカル単位・0以上）`--segments?`（既定1・1〜100）→ `{width, segments, delta, stats}`。⑤ `inset`:`--thickness`（**必須**・ローカル単位・0以上）→ `{thickness, delta, stats}`。`stats`=`{vertices, edges, polygons}`（編集後）/ `delta`=before→after の増減（符号付き＝追加は正・削減は負。decimate/boolean でも一貫）。fingerprint は mesh 法線込みの専用 `mesh_fingerprint`（頂点数不変の recalc も検出できる）。
>
> `extrude --offset` は **world 空間**ベクトル（move/duplicate の `--offset` と一貫・matrix_world で world→local 変換）。`bevel --width` / `inset --thickness` はスカラ量のため **mesh ローカル単位**（非一様スケール下の world 幅は定義不能）。extrude=全 face を region 押し出し / bevel=全 edge を `affect=EDGES` / inset=全 face を `inset_individual`（閉じた mesh の全 face は `inset_region` だと no-op のため個別 inset）。Mode は当面 OBJECT 固定（bmesh-on-data・5.0.1/4.4.3 で確認）。
>
> op 別 params（T7.3・**heavy**）: ⑥ `boolean`:`--operation UNION|DIFFERENCE|INTERSECT`（**必須**）`--with <mesh>`（**必須**・相手 mesh）→ `{operation, with, delta, stats}`。⑦ `decimate`:`--ratio 0..1`（**必須**）→ `{ratio, delta, stats}`。`bmesh` に boolean/decimate 相当が無いため（スパイク §E3 で確認）、いずれも **BOOLEAN/DECIMATE モディファイアを追加して `modifier_apply` で焼き込む**（生 bpy.ops は gateway のみ・AST guard 準拠）。boolean 相手の **world 位置は Blender が両者の matrix_world から解決**（手動変換不要）・相手は read-only（編集されない）・自己参照/非 mesh は `INVALID_PARAMS(USER_INPUT)`。boolean/decimate は heavy 候補（同期実行・非同期 job は M10）。多ユーザ mesh への `modifier_apply` は Blender が拒否するため共有 mesh は `--make-single-user` 必須（ratio=1.0 等の実質 no-op でも mesh は焼き直される）。

## シナリオ1: 原点変更
| method | params | result | M | Mode | St |
|--------|--------|--------|:-:|----|:--:|
| `set-origin` | `--targets` `--to geometry\|cursor\|world` `--center median\|bounds?` `--x?--y?--z?` `--make-single-user?` | 新origin座標/verified | ✓ | OBJECT | s |

errors: `E_TARGET_NOT_FOUND` / `E_PRECONDITION(shared mesh: users>=2)` / `E_MODE_MISMATCH`

## シナリオ2: 直立補正
| method | params | result | M | Mode | St |
|--------|--------|--------|:-:|----|:--:|
| `straighten` | `--targets` `--method reset\|world-align\|pca\|floor` `--up-axis?` `--axis?` `--bake-rotation?` | 補正後回転/接地Z | ✓ | OBJECT | s |

## シナリオ3: 3Dプリンタ対応
| method | params | result | M | H | Cap | St |
|--------|--------|--------|:-:|:-:|----|:--:|
| `print-check` | `--targets` `--manifold?` `--normals?` `--thin --min-thickness?` `--intersect?` `--degenerate?` `--save-to?` | チェック結果（大→output_ref） | - | ✓ | print3d_toolbox | s |
| `print-repair` | `--targets` `--make-manifold?` `--recalc-normals?` `--remove-degenerate?` | 修復前後差分 | ✓ | ✓ | print3d_toolbox | s |
| `print-setup` | `--unit mm\|m` `--scene?` | 単位設定後の値 | ✓ | - | - | s |
| `print-export` | `--targets` `--format stl\|3mf` `--path` `--ascii?` `--apply-transform?` | 出力パス/サイズ | - | ✓ | export.stl / io_mesh_3mf | s |

> `print-check`/`print-repair` は `print3d_toolbox` 未導入時 `addon_utils.enable` を試行→不可なら `CAPABILITY_UNAVAILABLE`。`print-export --format 3mf` 不可時は STL フォールバックを hint。

## ファイルI/O
| method | params | result | M | H | St |
|--------|--------|--------|:-:|:-:|:--:|
| `save` | `--path?` `--backup?` | 保存パス | ✓ | △ | s |
| `open` | `--path` | シーン要約 | ✓ | ✓ | s |
| `import` | `--format obj\|fbx\|gltf\|stl\|3mf` `--path` | 取込オブジェクト一覧 | ✓ | ✓ | s |
| `export` | `--format obj\|fbx\|gltf\|stl\|3mf` `--path` `--use-selection?` | 出力パス | - | ✓ | s |

## 逃げ道
| method | params | result | M | St |
|--------|--------|--------|:-:|:--:|
| `exec-python` | `--code\|--file` | 実行結果（既定 `EXEC_DISABLED`） | ✓? | e |

> `exec-python` は config `exec.mode` が off の場合 `EXEC_DISABLED` を返す。audited/trusted は設定昇格時のみ。レスポンスに `security_guarantee:false` / `heuristic_flags`。
