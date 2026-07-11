# NEXT-P2 — リファクタ & 汎用化（P2-2 / P2-3 / P2-4）着手書

状態: ⬜ 未着手 / 前提: **v1 + 設計レビュー P1 対応まで main マージ済み**（main=c5d5544・pytest 584・HANDOFF §6o）。
発注書: **`report/2026-07-11-design-review-generality.md` §4**（P2-2 / P2-3 / P2-4 の節に受け入れ基準あり）。

> P2-2 / P2-4 は**挙動を変えない内部リファクタ**（機能追加ではない）。P2-3 だけが機能追加。
> 推奨順: **P2-2 → P2-3 → P2-4**（自動生成で追加コストを下げてから汎用化・最後に分割）。ただし各項目は独立着手可。

## §0 着手コピペ
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout main && git pull origin main
git checkout -b feature/p2-2-typer-ssot   # 例
PYTHONUTF8=1 uv run pytest -q | tail -2    # 584 passed を確認してから着手
```

## §1 完了済み（P1）の確定要約 → HANDOFF §6o
次タスクが乗る主な機構: `bli_core/policy.py`（読み書き集約）/ `exec_restricted.scan_blocked` / `gateway.parent_set`（world 退避→復元）/ `--regex` BOOL（targets 系 19 コマンド）と `--name-regex`（list-objects・別物）/ `add`〜`collection` の5コマンド / export `fbx_options`（`_fbx_operator_kwargs` 純関数）。

## §2 P2-2: Typer コマンドの SSOT 自動生成（拡張コスト削減）
- **目的**: 新コマンド追加の必須変更を「definitions.py + ops.py + gateway.py（+任意 human フォーマッタ）」の 3 箇所 + 再生成へ削減（現状 7–8 箇所）。
- **前例（実証済み）**: `bli-cli/models.py` の `model_for()` が同じ SSOT から Pydantic モデルを動的生成している。同じパターンを Typer に広げる。
- **方針**: definitions.py から Typer コマンドを動的生成する共通ファクトリ。型写像 STR/PATH/INT/FLOAT/BOOL/ENUM/VEC3/VEC4 → typer.Option（VEC は既存 `_parse_vec` を共通適用）。human フォーマッタは `HUMAN_FORMATTERS: dict[str, Callable]` に**コマンド別登録**（未登録は JSON 整形へフォールバック）。
- **受け入れ基準（report §4）**: 既存 38 コマンドの CLI 挙動が回帰しない（**ヘルプ文言・終了コード・JSON 出力のスナップショット比較**を先に固定してから移行すること）。
- **注意**: presence-sensitive パラメータ（default を schema に出さない・指定時のみ params に載せる）と `--target` 別名・`--regex`/`--name-regex` の非対称・exec-python の `--code|--file` 排他など**手書き固有ロジックの棚卸しが先**。全コマンド一括でなく「単純コマンドから段階移行」も可。
- 付随: `contracts/methods.md` も SSOT から生成（または diff テスト）してドリフト検出対象に。

## §3 P2-3: modifier / material の汎用化（機能追加・G4/G5）
- **modifier**: `--type` を enum 固定（5種）から任意 type へ（**能力検出**で検証・bpy の rna から型検証する `--props '<JSON>'` を追加）。既存 5 種の専用フラグは互換のため残す。
- **material**: `--metallic --roughness --emission --alpha`、`--texture <path>`（Base Color への Image Texture ノード接続・パッキング選択）。
- **受け入れ基準（report §4）**: BEVEL/ARRAY 等が `--type BEVEL --props '{"width":0.1}'` で追加でき、メタリック値とテクスチャ付きマテリアルが FBX/GLB export に反映される。
- **着手前スパイク**: modifier の rna プロパティ列挙/型検証の版差（5.0/4.4）と、Image Texture ノード接続の最小手順を `--background` で確認（GUI 不要のはず）。

## §4 P2-4: モジュール分割（規約準拠・純リファクタ）
- 対象: `gateway.py`（約2,300行）/ `ops.py`（約2,100行）/ `main.py`（約1,900行）。いずれも `# ---- ラベル ----` 見出しでドメイン境界が明示済み＝境界に沿った機械的抽出。
- **方針（report §4）**: `gateway/` パッケージ化（`__init__.py` で re-export すれば ops 側の `gateway.foo()` は無改修）。最初の切り出し候補は **straighten（約380行の自己完結ブロック）**。`ops/` は同じ境界でミラー（`_BPY_HANDLERS` 集約と `dispatch()` は `__init__.py`）。`main.py` は P2-2 の自動生成導入と同時に縮小するのが効率的。
- **受け入れ基準**: pytest 全通過・公開シンボルの import 互換（`from bli_addon import gateway` 経由の参照維持）・1 ファイル 500 行程度以下。
- **注意**: fake-bpy テスト（test_gateway_targets.py）は `_forget_gateway_module()` で `bli_addon.gateway` を忘れさせる方式＝パッケージ化しても `bli_addon.__dict__` と `sys.modules` の両方を消す対策を維持すること（mistakes-memo）。

## §4.5 併行候補: レビュー suggestions の品質パス（小粒・低リスク）
`.claude/review/state.json`（**git 非管理**・ローカル）に 10 件記録済み。主なもの:
S2 regex 時間制限/ヒント全数カウントの早期打ち切り / S3 `_OS_DELETE_ATTRS` に truncate・rename・replace / S4 parent・collection の空 `--targets`+`--regex` ガード（export の流儀を共通化）/ G4 regex param 定義の 19 回コピー→ `_REGEX_PARAM` 定数共有 / G5 `_is_write_open` の公開名昇格 / G6 add human 出力に要求 type / G7 rename `--with-data` の共有 datablock ガード / G8 collection move の他シーン unlink 仕様化 / G9 `ABORTED` を ErrorCode 登録。

## §5 規約（変わらず）
main 直接禁止 / 日本語コミット + prefix / PR 経由（マージはユーザー判断）。**スタック PR を順次マージする際は「子 PR を先に main へリターゲット → マージ」**（head ブランチ自動削除設定で子が自動 CLOSE される・§6o の教訓）。bli-core は純 Python・依存ゼロ・**3.10 互換維持**（tomllib はガード付き import 済み）。生 bpy.ops は gateway のみ（AST guard）。実機 smoke は 5.0.1/4.4.3 両版（**常駐 GUI Blender を落としてから**＝9876 占有で E2E が誤爆）。SSOT 変更時は `uv run python scripts/generate_cli_schema.py` 再生成。

## §6 参照
- 発注書: `report/2026-07-11-design-review-generality.md` §4（P2-2/P2-3/P2-4・「修正しないこと」も明記）
- 確定要約: HANDOFF §6o / レビュー台帳: `.claude/review/state.json`・`rounds/{1,2}/report.md`（ローカル）
- 実機デモの残骸: `demo_table_scene.blend`（repo 直下・未追跡）・`dist/launch_dist.py`（zip 常駐ランチャ）
