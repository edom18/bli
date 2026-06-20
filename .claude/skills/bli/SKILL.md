---
name: bli
description: 常駐 Blender(GUI) を CLI から自律操作する。原点変更・直立補正・3Dプリンタ対応（単位/健全性チェック/STL書き出し）や、オブジェクト/メッシュ/マテリアル/モディファイア編集、ファイル I/O（export/import/save/open）、状態キャプチャを `bli <command>` で行う。ユーザーが Blender の操作・モデリング調整・3Dプリント準備を依頼したとき、または .blend を CLI で扱いたいときに使う。
---

# bli — Blender CLI 操作スキル

`bli` は AIエージェントが **CLI 経由で常駐 Blender(GUI) を自律操作**するツール。
構造化サブコマンドが主軸で、各コマンドは JSON で結果を返す（`--json`）。

## 前提と接続
- 常駐 Blender に bli アドオンが入り、TCP サーバ（127.0.0.1・トークン認証）が起動していること。
- まず疎通を確認する:
  - `bli doctor` — 環境診断（Blender 検出 / アドオン導入 / port / version / capabilities / メインスレッド応答性）。
  - `bli ping --json` — protocol/version/capabilities を返す。接続不能は exit 3。
- 接続情報・トークンはユーザローカル（`BLI_STATE_DIR`・既定 `%LOCALAPPDATA%/bli`）。未設定なら `bli init`。

## コマンドの発見（ローカル完結・アドオン不要）
- 全コマンド一覧: `bli list-commands --json`（`schema_hash` 同梱）。
- 個別の JSON Schema: `bli help --command <name> --json`（params・必須・enum・型）。
- 完全なカタログのキャッシュ: **`reference/cli-schema.json`**（このスキル同梱・全コマンドのメタ + JSON Schema + schema_hash）。
  - 迷ったらまずこれを読む。`bli ping` が返す `schema_hash` と一致していれば最新。

## 3つの中核シナリオ（このプロダクトの主目的）

### S1. 原点変更（set-origin）
オブジェクトの原点（ピボット）を変更する。見た目は変えず原点だけ動かす。
```
bli set-origin --target Cube --to geometry --center median   # 原点を形状中心へ
bli set-origin --target Cube --to cursor                     # 3Dカーソル位置へ
bli set-origin --target Cube --to world --x 0 --y 0 --z 0    # world 座標を指定
```
- 共有メッシュ（多ユーザ）は `--make-single-user` が無いと `E_PRECONDITION` で拒否。

### S2. 直立補正（straighten）
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

### S3. 3Dプリンタ対応（print-*）
```
bli print-setup --unit mm                                    # 表示単位を mm に（非破壊・geometry 不変）
bli print-check --target Model --json                        # manifold/normals/退化面を診断
bli print-repair --target Model                              # best-effort 修復（make-manifold 等）
bli print-export --target Model --path model.stl --scale 1.0 # STL 書き出し（world 焼き）
```
- 薄壁/自己交差（thin/intersect）は print3d 依存でこの環境では `CAPABILITY_UNAVAILABLE`（manifold/normals/degenerate は常時可）。
- 3mf は export operator が無く `CAPABILITY_UNAVAILABLE`（STL を使う）。

## 主要な約束ごと（規約）
- **対象指定**: `--target <name>`（単数）または `--targets <name>`（完全名 > 正規表現）。多くのコマンドは単一対象。
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
- **既定は `EXEC_DISABLED`**。有効化はユーザ自身のユーザローカル `policy.toml`（`BLI_STATE_DIR/policy.toml`）の `[exec] mode` のみ（CLI フラグやリポジトリ設定では昇格できない）。
- mode: `off`（拒否）/ `audited`（sha256 が `allow_hashes` に一致したコードだけ自走）/ `trusted`（無条件）。
- **サンドボックスは無い**＝コードは同一 OS 権限で走る。応答の `security_guarantee` は常に false、`heuristic_flags` は注意喚起のみ。試行はすべて `audit/exec.jsonl` に記録される。
- まず構造化コマンドで解けないか確認する。3シナリオは exec 不要で完遂できる。

## 結果の読み方
- 成功は `{success:true, operation, data, fingerprint, output_ref}`。人間向けは要約、`--json` で機械可読。
- エラーは構造化（`category`/`kind`/`retryable`/`remediation`）＝remediation に従って自己修正する。
