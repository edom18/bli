# NEXT-P2 — リファクタ & 汎用化（P2-2 ✅ / P2-3 ✅ / 次=P2-4）着手書

状態: **P2-2 ✅（PR #45・HANDOFF §6p）/ P2-3 ✅（PR #47・HANDOFF §6q）完了・main=a0d8b7b・pytest 794**。次は **P2-4（モジュール分割・純リファクタ）**。
発注書: **`report/2026-07-11-design-review-generality.md` §4**（git 追跡済み・P2-4 の節に受け入れ基準あり）。

## §0 着手コピペ
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout main && git pull origin main
git checkout -b feature/p2-4-gateway-split   # 例
PYTHONUTF8=1 uv run pytest -q | tail -2       # 794 passed を確認してから着手
```

## §1 完了済み（P2-2/P2-3）の確定要約 → HANDOFF §6p/§6q
P2-4 が触るコードの主な機構:
- **CLI は SSOT から自動生成**（P2-2）: `bli-cli/cli_factory.py` + `cli_specs.py` + `formatters.py`。main.py は 705 行＝**P2-4 の分割対象外**。新コマンド/新パラメータの手順＝definitions.py + ops.py + gateway.py（+任意 formatters）+ 再生成 2 本（`scripts/generate_cli_schema.py`・`packages/bli-cli/tests/regen_snapshots.py`）+ methods.md 追記（test_methods_md_sync が強制）。
- **modifier --props 機構**（P2-3・gateway 内）: `valid_modifier_types`/`require_modifier_type`（rna 能力検出）・`set_modifier_props`/`_coerce_prop_value`/`_prop_value_repr`（rna 型検証・applied_props 実値返し）。BOOLEAN は props.object 必須 + `_resolve_boolean_operand`（ops 側）と同一検証。
- **material PBR/テクスチャ機構**（P2-3・gateway 内）: `create_material`（→ `_apply_material_extras`・`_require_principled`/`_principled_input`/`_load_texture_image`）・`discard_created_material`。**アトミック性の流儀＝失敗時は BaseException 捕捉で datablock（material/image/modifier）を撤去して必ず再送出**。共有 mesh ガードは「失敗し得る処理の後」（ops._material の create 分岐）。
- 統合テストの型: `test_ops_material_rollback.py`＝**fake gateway 丸ごと注入**（sys.modules と bli_addon.__dict__ の二重後始末）で ops 経路を bpy なしで通す。fake-bpy 型は `test_gateway_modifier_props.py`/`test_gateway_material_extras.py`。

## §2 P2-4: モジュール分割（規約準拠・純リファクタ）— **次はこれ**
- 対象: `gateway.py`（**2,705 行**・P2-3 で増）/ `ops.py`（**2,222 行**）。**main.py（705 行）は P2-2 で縮小済み＝対象外**。いずれも `# ---- ラベル ----` 見出しでドメイン境界が明示済み＝境界に沿った機械的抽出。
- **方針（report §4）**: `gateway/` パッケージ化（`__init__.py` で re-export すれば ops 側の `gateway.foo()` は無改修）。最初の切り出し候補は **straighten（約380行の自己完結ブロック）**。P2-3 で増えた modifier props / material PBR ブロックも自己完結性が高く切り出しやすい。`ops/` は同じ境界でミラー（`_BPY_HANDLERS` 集約と `dispatch()` は `__init__.py`）。
- **受け入れ基準**: pytest 全通過・公開シンボルの import 互換（`from bli_addon import gateway` 経由の参照維持）・1 ファイル 500 行程度以下（**gateway/ops 分割後の各ファイルに適用**・main.py は対象外）。
- **注意**:
  - fake-bpy テスト（test_gateway_targets.py ほか）は `_forget_gateway_module()` で `bli_addon.gateway` を忘れさせる方式＝パッケージ化しても `bli_addon.__dict__` と `sys.modules` の**両方**を消す対策を維持（mistakes-memo）。`test_ops_material_rollback.py` は fake **gateway** を注入している＝gateway パッケージ化後も `bli_addon.gateway` の差し替えが成立する構造を保つこと。
  - AST guard（`scripts/check_no_raw_bpy_ops.py`）は「生 bpy.ops は gateway のみ」を強制＝gateway/ パッケージ化に伴い guard の許可対象パスを追随させる。
  - 挙動変更なしの純リファクタ＝behavior/surface スナップショットと両版 smoke が回帰ガードになる（再生成不要のはず・diff が出たら挙動が変わった証拠）。

## §3 併行候補: レビュー suggestions の品質パス（小粒・低リスク）
`.claude/review/state.json`（**git 非管理**・ローカル）に記録済み。主なもの:
- P1 由来: S2 regex 時間制限 / S3 `_OS_DELETE_ATTRS` 拡充 / S4 parent・collection の空 `--targets`+`--regex` ガード / G4 regex param 定義の 19 回コピー（SSOT 側）/ G9 `ABORTED` を ErrorCode 登録 ほか
- P2-3 由来: `--props` の STRING FILE_PATH subtype パス検証 / テクスチャ読込のサイズ上限・MemoryError 捕捉（いずれも**プロジェクト全体のパスポリシー議論**とセット・P3 候補）
- P2-2 由来: cli_specs の help 文言差（実測 73 件）の SSOT 統一（schema_hash が変わる＝ユーザー判断）

## §4 規約（変わらず + P2 で追加）
main 直接禁止 / 日本語コミット + prefix / PR 経由（マージはユーザー判断）。**PR ゲート（plugin hook）が全 `gh pr create` に review-orchestrator の APPROVED（branch/HEAD 一致）を強制**＝PR 前にパイプラインを回すこと（docs 同期 PR も対象＝ユーザー確認済み運用）。bli-core は純 Python・依存ゼロ・3.10 互換維持。生 bpy.ops は gateway のみ（AST guard）。実機 smoke は 5.0.1/4.4.3 両版（**常駐 GUI Blender を落としてから**。smoke 自体は port=0 で常駐と非競合だが、**重い評価を誘発する modifier は assert 後即 remove**＝BEVEL segments=1000 残置でメモリ爆発の実績・mistakes-memo）。**SSOT（definitions.py）変更時の再生成は 2 本** + methods.md 追記。

## §5 参照
- 発注書: `report/2026-07-11-design-review-generality.md` §4（P2-4・「修正しないこと」も明記）
- 確定要約: HANDOFF §6o（P1）・§6p（P2-2）・§6q（P2-3）/ レビュー台帳: `.claude/review/state.json`（ローカル）
- 実機デモの残骸: `demo_table_scene.blend`（repo 直下・未追跡）・`dist/launch_dist.py`（zip 常駐ランチャ）
