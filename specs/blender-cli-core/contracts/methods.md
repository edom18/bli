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
| `duplicate` | `--targets` `--linked?` `--count?` `--offset?` | 新オブジェクト名 | ✓ | - | OBJECT | s |
| `delete` | `--targets` `--backup?` | 削除結果 | ✓ | - | OBJECT | s |
| `material` | `assign\|create\|list` `--targets` `--name?` `--color?` | 材質状態 | ✓ | - | OBJECT | s |
| `modifier` | `add\|remove\|list\|apply` `--targets` `--type?` `[params]` | modifier状態 | ✓ | - | OBJECT | s |

`modifier --type`（v1必須）: `MIRROR` / `SUBSURF` / `SOLIDIFY` / `DECIMATE` / `BOOLEAN`。

## メッシュ編集（編集モード / bmesh一次）
| method | params | result | M | H | Mode | St |
|--------|--------|--------|:-:|:-:|----|:--:|
| `mesh extrude` | `--targets` `--offset?` `--faces?` | mesh統計 | ✓ | - | ANY | e |
| `mesh bevel` | `--targets` `--width` `--segments?` | mesh統計 | ✓ | - | ANY | e |
| `mesh inset` | `--targets` `--thickness` | mesh統計 | ✓ | - | ANY | e |
| `mesh boolean` | `--targets` `--with` `--op union\|difference\|intersect` | mesh統計 | ✓ | △ | ANY | e |
| `mesh decimate` | `--targets` `--ratio` | 削減後ポリ数 | ✓ | △ | ANY | e |
| `mesh recalc-normals` | `--targets` `--inside?` | 法線統計 | ✓ | - | ANY | s |
| `mesh merge-by-distance` | `--targets` `--distance?` | マージ頂点数 | ✓ | - | ANY | s |

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
