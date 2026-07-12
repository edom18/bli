# NEXT-P2 — リファクタ & 汎用化（P2-2 ✅ / 次=P2-3 / P2-4）着手書

状態: **P2-2 ✅ 完了（PR #45・main=5dd3fac・pytest 753・確定要約 HANDOFF §6p）**。次は **P2-3（機能追加）→ P2-4（純リファクタ）**。
発注書: **`report/2026-07-11-design-review-generality.md` §4**（P2-3 / P2-4 の節に受け入れ基準あり）。

## §0 着手コピペ
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout main && git pull origin main
git checkout -b feature/p2-3-modifier-props   # 例
PYTHONUTF8=1 uv run pytest -q | tail -2        # 753 passed を確認してから着手
```

## §1 完了済み（P2-2）の確定要約 → HANDOFF §6p
次タスクが乗る主な機構:
- **CLI コマンドは SSOT（definitions.py）から自動生成**: `bli-cli/cli_factory.py`（Param→typer.Option・動的 `__signature__`・送信ポリシー既定則）+ `cli_specs.py`（互換オーバーライド表・**新コマンドはエントリ不要**）+ `formatters.py`（`HUMAN_FORMATTERS`・未登録は JSON フォールバック）。main.py は 705 行（インフラ + 手書き ping/doctor/init/policy/job-wait/list-commands/help）。
- **新コマンド/新パラメータ追加の手順**: ① definitions.py に定義 ② ops.py にハンドラ（+`_BPY_HANDLERS`）③ gateway.py に bpy 関数 ④（任意）formatters.py に human 登録 ⑤ 再生成 2 本＝`uv run python scripts/generate_cli_schema.py` と `uv run python packages/bli-cli/tests/regen_snapshots.py`（**surface/behavior スナップショットに載るため regen 必須・diff をレビューしてコミット**）。
- スナップショットは移行ガード兼回帰ガード: `packages/bli-cli/tests/snapshots/`（surface=ヘルプ文言/オプション構造・behavior=exit code/stdout/stderr/送信 params）。
- methods.md は `packages/bli-core/tests/test_methods_md_sync.py` がドリフト検出（全 param の `--kebab` 表記が文書に必要＝**新パラメータは methods.md 追記もセット**）。

## §2 P2-3: modifier / material の汎用化（機能追加・G4/G5）— **次はこれ**
- **modifier**: `--type` を enum 固定（5種）から**任意 type**へ（能力検出で検証）+ `--props '<JSON>'`（bpy の rna から型検証する汎用プロパティ設定）。既存 5 種の専用フラグは互換のため残す。
- **material**: `--metallic --roughness --emission --alpha`、`--texture <path>`（Base Color への Image Texture ノード接続・パッキング選択）。
- **受け入れ基準（report §4）**: BEVEL/ARRAY 等が `--type BEVEL --props '{"width":0.1}'` で追加でき、メタリック値とテクスチャ付きマテリアルが FBX/GLB export に反映される。
- **着手前スパイク（必須・両版 5.0.1/4.4.3・--background で可のはず）**:
  1. modifier type の列挙: `bpy.types.Modifier.bl_rna.properties['type'].enum_items` の版差 / `obj.modifiers.new(name, type)` の不正 type 挙動。
  2. rna プロパティ列挙/型検証: `mod.bl_rna.properties` から編集可能プロパティ（`is_readonly=False`）の name/type/範囲を取り、`--props` の JSON 値を型検証・setattr する最小手順。enum/bool/int/float/float array/ポインタ（object 参照）の写像。
  3. Image Texture: `mat.node_tree.nodes.new('ShaderNodeTexImage')` + `bpy.data.images.load(path)` + Principled Base Color への link の最小手順・pack の要否（`image.packed_file`/`pack()`）・FBX embed（path_mode=COPY）と GLB への反映。
- **設計上の注意**: SSOT は VEC/ENUM 等の既存 ParamType のみ＝`--props` は **STR（JSON 文字列）**で受けてサーバ側で rna 検証（silent drop 禁止・不正キー/型は INVALID_PARAMS + 有効キー候補を remediation に）。definitions.py 変更＝schema_hash が変わる → 再生成 2 本 + SKILL.md の該当節更新 + methods.md 追記。ポインタ型 props（BOOLEAN の object 等）は v1 では名前文字列→解決 or 対象外を明示。
- **実機 smoke**: definitions/ops/gateway を触る＝両版 smoke 必須（`spikes/smoke_ops.py` に modifier --props / material texture の golden を追加・**常駐 GUI Blender を落としてから**）。

## §3 P2-4: モジュール分割（規約準拠・純リファクタ）
- 対象: `gateway.py`（約2,300行）/ `ops.py`（約2,100行）。**main.py は P2-2 で 705 行に縮小済み＝対象外でよい**。いずれも `# ---- ラベル ----` 見出しでドメイン境界が明示済み＝境界に沿った機械的抽出。
- **方針（report §4）**: `gateway/` パッケージ化（`__init__.py` で re-export すれば ops 側の `gateway.foo()` は無改修）。最初の切り出し候補は **straighten（約380行の自己完結ブロック）**。`ops/` は同じ境界でミラー（`_BPY_HANDLERS` 集約と `dispatch()` は `__init__.py`）。
- **受け入れ基準**: pytest 全通過・公開シンボルの import 互換（`from bli_addon import gateway` 経由の参照維持）・1 ファイル 500 行程度以下（**gateway/ops 分割後の各ファイルに適用**・main.py 705 行は P2-2 で縮小済みのため対象外）。
- **注意**: fake-bpy テスト（test_gateway_targets.py）は `_forget_gateway_module()` で `bli_addon.gateway` を忘れさせる方式＝パッケージ化しても `bli_addon.__dict__` と `sys.modules` の両方を消す対策を維持すること（mistakes-memo）。

## §4 併行候補: レビュー suggestions の品質パス（小粒・低リスク）
`.claude/review/state.json`（**git 非管理**・ローカル）に 10 件記録済み。主なもの:
S2 regex 時間制限/ヒント全数カウントの早期打ち切り / S3 `_OS_DELETE_ATTRS` に truncate・rename・replace / S4 parent・collection の空 `--targets`+`--regex` ガード（export の流儀を共通化）/ G4 regex param 定義の 19 回コピー→ `_REGEX_PARAM` 定数共有（definitions.py 側・CLI 側は factory で解消済み）/ G5 `_is_write_open` の公開名昇格 / G6 add human 出力に要求 type / G7 rename `--with-data` の共有 datablock ガード / G8 collection move の他シーン unlink 仕様化 / G9 `ABORTED` を ErrorCode 登録。
フォローアップ候補: cli_specs.py の help 文言差（実測 74 件）を SSOT 側へ統一（schema_hash が変わる＝ユーザー判断）。

## §5 規約（変わらず + P2-2 で追加）
main 直接禁止 / 日本語コミット + prefix / PR 経由（マージはユーザー判断）。**スタック PR を順次マージする際は「子 PR を先に main へリターゲット → マージ」**。bli-core は純 Python・依存ゼロ・**3.10 互換維持**。生 bpy.ops は gateway のみ（AST guard）。実機 smoke は 5.0.1/4.4.3 両版（**常駐 GUI Blender を落としてから**＝9876 占有で E2E が誤爆）。**SSOT（definitions.py）変更時の再生成は 2 本**: `uv run python scripts/generate_cli_schema.py` + `uv run python packages/bli-cli/tests/regen_snapshots.py`（diff レビュー必須）。新パラメータは contracts/methods.md への追記もセット（test_methods_md_sync が強制）。

## §6 参照
- 発注書: `report/2026-07-11-design-review-generality.md` §4（P2-3/P2-4・「修正しないこと」も明記）
- 確定要約: HANDOFF §6o（P1）・§6p（P2-2）/ レビュー台帳: `.claude/review/state.json`・`rounds/{1,2}/report.md`（ローカル）
- 実機デモの残骸: `demo_table_scene.blend`（repo 直下・未追跡）・`dist/launch_dist.py`（zip 常駐ランチャ）
