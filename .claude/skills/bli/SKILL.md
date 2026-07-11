---
name: bli
description: 常駐 Blender(GUI) を CLI から汎用操作する（モデリング・シーン編集・マテリアル・入出力・3Dプリント準備など）。オブジェクト生成/変形/メッシュ編集/マテリアル/モディファイア/親子・コレクション整理、ファイル I/O（import/export/save/open）、状態キャプチャに加え、原点変更・直立補正・3Dプリンタ対応（単位/健全性チェック/STL書き出し）などの代表的な調整も `bli <command>` で行える。ユーザーが Blender の操作・モデリング調整・シーン編集・3Dプリント準備を依頼したとき、または .blend を CLI で扱いたいときに使う。
---

# bli — Blender CLI 操作スキル

`bli` は AIエージェントが **CLI 経由で常駐 Blender(GUI) を自律操作**するツール。
構造化サブコマンドが主軸で、各コマンドは JSON で結果を返す（`--json`）。
特定の3シナリオに限らず、Blender の一般的な操作（生成・変形・メッシュ編集・マテリアル・シーン整理・入出力）を広くカバーする。

## 前提と接続
- 常駐 Blender に bli アドオンが入り、TCP サーバ（127.0.0.1・トークン認証）が起動していること。
- まず疎通を確認する:
  - `bli doctor` — 環境診断（Blender 検出 / アドオン導入 / port / version / capabilities / メインスレッド応答性）。
  - `bli ping --json` — protocol/version/capabilities を返す。接続不能は exit 3。
- 接続情報・トークンはユーザローカル（`BLI_STATE_DIR`・既定 `%LOCALAPPDATA%/bli`）。未設定なら `bli init`。

## 汎用ワークフロー
どんな依頼でも、次の5段階（**発見 → 観察 → 編集 → 検証 → 入出力**）で着手できる。
「3つの決まったシナリオしかできない」ツールではなく、この流れに沿えば任意の Blender 操作に対応できる。

### 1. 発見（コマンドを知る・ローカル完結・アドオン不要）
- 全コマンド一覧: `bli list-commands --json`（`schema_hash` 同梱）。
- 個別の JSON Schema: `bli help --command <name> --json`（params・必須・enum・型）。
- 完全なカタログのキャッシュ: **`reference/cli-schema.json`**（このスキル同梱・全コマンドのメタ + JSON Schema + schema_hash）。
  - 迷ったらまずこれを読む。`bli ping` が返す `schema_hash` と一致していれば最新。

### 2. 観察（現在のシーン状態を知る）
```
bli scene-info [--depth N]                       # シーン階層・オブジェクト一覧・単位設定
bli list-objects [--type MESH] [--name-regex <pat>]   # 条件フィルタで一覧（名前はパターン**値**を渡す）
bli object-info --targets <name>                 # 寸法・頂点数・トランスフォーム・bbox・材質・モディファイア
bli capture [--source viewport|screen|render]    # 現在の状態を画像取得（実地FB #1）
```

### 3. 編集（オブジェクト・メッシュ・マテリアル・シーン構造を変更する）
```
bli add --type cube|uv-sphere|cylinder|...       # 生成
bli transform --targets <name> [--location ...]  # 変形
bli mesh --op bevel|extrude|boolean|...           # メッシュ編集（bmesh 一次）
bli material --action create|assign|list          # マテリアル
bli modifier --action add|remove|list|apply       # モディファイア
bli parent --targets <name> --to <name>           # 親子付け
bli collection --action create|move|link|unlink   # コレクション整理
```
ほかにも `select` / `apply-transform` / `duplicate` / `delete` / `mode` / `rename` が使える（詳細はコマンド発見または各所の代表レシピを参照）。

### 4. 検証（変更が意図通りか確かめる）
```
bli capture --source viewport                    # 目視確認
bli object-info --targets <name>                 # 数値で確認（bbox/transform 等）
bli print-check --targets <name> --json          # メッシュ健全性の診断
```

### 5. 入出力（ファイルとして受け渡す）
```
bli import --format obj|fbx|gltf|stl|3mf --path <file>
bli export --format obj|fbx|gltf|stl|3mf --path <file>
bli save [--path <file.blend>]
bli open --path <file.blend>
```

## 代表レシピ
以下は上記ワークフローの具体例（定石）。困ったら参照するが、汎用ワークフローに沿えばここに無い操作も自力で組み立てられる。

### 樽（バレル）のようなプリミティブから作る
```
bli add --type cylinder --name Barrel                       # 生成（--location/--rotation/--scale も指定可）
bli mesh --op bevel --targets Barrel --width 0.05            # 続けて mesh 編集
bli material --action create --targets Barrel --name Wood --color 0.4,0.25,0.1,1
```
`add --type` は `cube`/`uv-sphere`/`ico-sphere`/`cylinder`/`cone`/`plane`/`torus`/`empty`/`light`(`--light-type`)/`camera`/`text`。「まず生成する」から始めれば exec-python を使わず構造化コマンドだけで完結できる。

### Edit モードのまま放置された .blend から復帰する
```
bli mode --to object          # 現在の active を Object モードへ（--targets で対象指定も可）
```
`E_MODE_MISMATCH`（OBJECT 等が必要なコマンドを別モード中に呼んだ）に遭遇したら、remediation どおり `bli mode --to object` を実行してから再試行する（自動遷移はしない）。

### シーン構造を整理する
```
bli rename --targets Cube --name Barrel                     # 改名（衝突時は実名 .001 等を返す）
bli parent --targets Wheel1,Wheel2 --to CartBody             # 親子付け（--targets は正規表現で複数可）
bli parent --targets Wheel1 --clear                          # 親子解除（--keep-transform は既定 on）
bli collection --action create --name Props
bli collection --action move --name Props --targets Barrel   # Props コレクションへ移動
```

### Unity 取り込みレシピ（export --format fbx）
Blender の FBX 既定（`axis_forward=-Z` / `axis_up=Y` / `scale=1.0` / `apply_unit_scale=on`）は**そのまま Unity に合う**（両版実機確認済み）。オプションを省略するだけで正しい向き・スケールで取り込める。
```
bli export --format fbx --path Model.fbx --targets Model                    # 省略でOK（Unity既定と一致）
```
明示指定したい場合（既定と同値を明示する例・スケール変更・テクスチャ同梱）:
```
bli export --format fbx --path Model.fbx --targets Model \
    --axis-forward=-Z --axis-up=Y --scale 1.0            # 既定と同じ値を明示（確認用）
bli export --format fbx --path Model.fbx --targets Model --scale 100        # m→cm 等スケール変換
bli export --format fbx --path Model.fbx --targets Model --embed-textures   # テクスチャ同梱（path_mode=COPY を自動設定）
```
- **注意（重要）**: 負の軸を渡すときは `--axis-forward=-Z` のように **`=` で連結**すること。`--axis-forward -Z`（スペース区切り）だと `-Z` が別オプションと誤解釈され得る。
- `--axis-forward` / `--axis-up` / `--scale` / `--apply-unit-scale`（`--no-apply-unit-scale` で明示オフ） / `--embed-textures` は **`--format fbx` 専用**。他 format に指定すると `INVALID_PARAMS`。
- glTF(GLB) は **+Y up がフォーマット仕様で固定**（軸オプションは無い）。Unity 標準インポータはそのまま解釈するため追加設定は不要。

### 原点変更（set-origin）
オブジェクトの原点（ピボット）を変更する。見た目は変えず原点だけ動かす。
```
bli set-origin --target Cube --to geometry --center median   # 原点を形状中心へ
bli set-origin --target Cube --to cursor                     # 3Dカーソル位置へ
bli set-origin --target Cube --to world --x 0 --y 0 --z 0    # world 座標を指定
```
- 共有メッシュ（多ユーザ）は `--make-single-user` が無いと `E_PRECONDITION` で拒否。

### 直立補正（straighten）
傾いたオブジェクトを立てる。`--method` で方式を選ぶ。
```
bli straighten --target Scan --method world-align            # 最も近い主軸を up へ（軸自動）
bli straighten --target Scan --method world-align --axis Z --up-axis +Z
bli straighten --target Scan --method pca --up-hint current  # 主成分で立てる（current=上下反転を防ぐ）
bli straighten --target Scan --method floor                  # 最下点を接地（平行移動のみ）
bli straighten --target Scan --method align-vector --from-dir 0,0,1 --to-dir 0,1,0  # 向きを数値で合わせる
bli straighten --target Scan --method reset                  # 回転を identity へ
```
- **計画確認**: `--dry-run` で適用せず結果だけ見られる（非破壊）。`--bake-rotation` は mesh に焼き込む（`--dry-run` と排他）。
- `align-vector` は同一メッシュ内の支柱なども向きを数値で渡せば整列できる（手計算 transform 迂回の実用解）。

### 3Dプリンタ対応（print-*）
```
bli print-setup --unit mm                                    # 表示単位を mm に（非破壊・geometry 不変）
bli print-check --target Model --json                        # manifold/normals/退化面を診断
bli print-repair --target Model                              # best-effort 修復（make-manifold 等）
bli print-export --target Model --path model.stl --scale 1.0 # STL 書き出し（world 焼き）
```
- 薄壁/自己交差（thin/intersect）は print3d 依存でこの環境では `CAPABILITY_UNAVAILABLE`（manifold/normals/degenerate は常時可）。
- 3mf は export operator が無く `CAPABILITY_UNAVAILABLE`（STL を使う）。

## 主要な約束ごと（規約）
- **対象指定**: `--target <name>`（単数）または `--targets <name>`。既定は**完全一致のみ**（暗黙の正規表現フォールバックは無い）。正規表現で複数/曖昧一致させたいときは明示的に `--regex` を付ける（例: `bli select --targets "^Cube" --regex`）。完全一致 0 件のとき、それが正規表現として解釈すると当たる場合はエラーに `--regex` を使うヒントが付く。多くのコマンドは単一対象。**注意**: `list-objects` の名前フィルタは `--name-regex <pat>`（パターン**値**を取る）で、targets 系の `--regex`（値なしの解釈フラグ）とは別物。
- **大きい結果は退避**: 結果が大きいと `output_ref`（共有ファイルへ退避）を返す。既定は参照のみ。中身が要るときだけ `--fetch`（sha256 検証して展開）。
- **重いコマンドは自動待機**: import/export/print-check/print-repair・mesh の boolean/decimate は非同期 job。既定は内部で完了まで待ち同期的に見える。`--async` で job_id を即返し、`bli job-wait --id <id>` / `bli job-status --id <id>` で回収。
- **冪等リトライ**: `--id <UUIDv4>` を付けると同一リクエストは二重実行されない。タイムアウト（exit 2）後は `bli request-status --id <id>` で決着を確認。
- **終了コード**: 0=成功 / 1=業務エラー / 2=タイムアウト等（retryable・後追い可） / 3=接続不能 / 4=入力エラー。
- **破壊系の安全**: `delete` は削除前サマリを backup として返す。`open` は未保存の bli 変更があれば `--force` 必須。`save` は上書き時 .blend1 を既定で残す。
- **undo/redo**: `bli undo` / `bli redo --steps N`（GUI 必須）。

## exec-python（逃げ道・既定 off・サンドボックスなし）
構造化コマンドで表現できない操作のフォールバック。**既定で無効**。
```
bli exec-python --code "import bpy; bpy.data.objects['Cube'].location.x = 1"
bli exec-python --file script.py
```
- **既定は `EXEC_DISABLED`**。有効化はユーザ自身のユーザローカル `policy.toml`（`BLI_STATE_DIR/policy.toml`）の `[exec] mode` のみ（CLI フラグやリポジトリ設定では昇格できない）。表示/編集は `bli policy`（後述）。
- mode: `off`（拒否）/ **`restricted`（推奨・下記）** / `audited`（sha256 が `allow_hashes` に一致したコードだけ自走）/ `trusted`（無条件）。
- **restricted は無確認で自走したいエージェント用途の推奨モード**: Blender API（`bpy`/`bmesh`/`mathutils` 等）は全面許可しつつ、プロセス起動・ネットワーク・削除系・動的実行（`eval`/`exec`等）・書込 `open` を AST で検出したら実行前に `EXEC_BLOCKED_RESTRICTED` で拒否する。ブロックされたら症状文の検出理由（例: `import:subprocess`）を見てコードを直せば再実行できる。有効化はユーザに `bli policy --action set --mode restricted` の実行を**依頼**する（エージェントは昇格を実行しない・下記 policy 節）。
- **サンドボックスは無い**＝コードは同一 OS 権限で走る（restricted でも同じ）。応答の `security_guarantee` は常に false、`heuristic_flags` は注意喚起のみでブロックしない（restricted のブロック判定とは別レイヤ）。試行はすべて `audit/exec.jsonl` に記録される（restricted のブロックは `blocked` フィールドつき）。restricted の静的検査も完全ではない（迂回し得る）＝事故防止であり悪意対策ではない。
- まず構造化コマンドで解けないか確認する。代表レシピ（原点変更・直立補正・3Dプリンタ対応など）は exec 不要で完遂できる。

## policy（exec 権限の表示/編集・CLIローカル・RPCなし）
```
bli policy --action show --json                            # 現在の mode / policy.toml パスを確認（エージェント実行可）
bli policy --action set --mode restricted                   # 昇格。**ユーザ（人間）が実行する**・対話確認つき
```
- **`--action set`（昇格）はエージェントが実行しない。** EXEC_DISABLED / EXEC_BLOCKED_RESTRICTED に当たったら、上のコマンドの実行を**ユーザに依頼**する（勝手に昇格して再試行しない）。exec ゲートの価値は「人間が有効化を判断する」ことにある。
- サーバ（Blender アドオン）が読む真実源は常にユーザローカル `policy.toml`。このコマンドは表示/編集を助けるだけで **サーバへは何も送らない**（RPC なし・CLI フラグ単体で昇格できないという不変条件はそのまま）。
- `--action set` は既存の `policy.toml` に見覚えのない設定（`[exec]` 以外のセクション等）があると自動編集を拒否し、手動編集を促す（他の設定を壊さないため）。対話確認が入る（スキップ用の `--yes` は人間の非対話スクリプト向け）。

## 結果の読み方
- 成功は `{success:true, operation, data, fingerprint, output_ref}`。人間向けは要約、`--json` で機械可読。
- エラーは構造化（`category`/`kind`/`retryable`/`remediation`）＝remediation に従って自己修正する。
