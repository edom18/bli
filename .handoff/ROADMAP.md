# bli (Blender CLI) — ロードマップ俯瞰（ROADMAP）

最終更新: 2026-06-21 / このファイルは**全体像を1枚で見渡す**ための索引。詳細は各リンク先へ。

- **プロダクト**: AIエージェントが **CLI 経由で Blender を自律操作**するツール（`bli`）。常駐 GUI Blender + アドオン TCP ソケット ← Python/Typer 製 CLI。MCP のトークン非効率を CLI で解消。
- **真実の情報源（SSOT）**: `specs/blender-cli-core/`（`spec.md` / `plan.md` / `research.md` / `contracts/methods.md` / `data-model.md`）。
- **全体史 + 規約 + 再利用パターン**: `.handoff/HANDOFF.md`（新規セッションはまずこれ）。
- **マイルストーン別の着手手順**: `.handoff/NEXT-M*.md`。

---

## マイルストーン進捗（plan.md §4 / M0→M14 直列・M5/M6/M7 は概ね並行可）

| MS | 内容 | 状態 | PR / 備考 |
|:--:|---|:--:|---|
| M0 | プロジェクト基盤（uv workspace 3パッケージ・ruff/pyright・AST guard・CI枠） | ✅ | PR #1 |
| M0.5 | 実機スパイク（能力ダンプ・dispatch 安定・op_spike）＝研究 §付録 | ✅ | 5.0/4.4 実機 |
| M1 | コア bli-core（commands/schema/errors/protocol/runtime/types・純Python） | ✅ | |
| M2 | 通信層（server/auth/session/registry/shutdown/client/CLI ping）★walking skeleton | ✅ | |
| M3 | アドオン実行基盤（ops/gateway/dispatcher 結線・set-origin 等） | ✅ | |
| M4 | CLI骨格 & 診断（Pydantic ラッパ/help/list-commands/request-status/--id） | ✅ | PR #1 追補 |
| M5 | 情報取得（list-objects / object-info bbox / scene-info の output_ref 退避） | ✅ | PR #2 |
| M6 | 汎用編集（select/transform/apply-transform・duplicate/delete・material・modifier） | ✅ | PR #3–#6 |
| M7 | メッシュ編集（mesh --op: bmesh 一次 + heavy は modifier 経由） | ✅ | PR #7–#9 |
| **M8** | **代表ユースケース（ドメインパック: set-origin / straighten / print-*）+ 実地フィードバック対応** | ✅ **完了**（PR #10–#18, #20） | 下記 §M8 |
| **M9** | ファイルI/O（export / import / save / open・3mf 不可→CAPABILITY） | ✅ **完了**: T9.1 export(#21)/T9.2 import(#22)/T9.3 save(#23)/T9.4 open(#25) | 確定要約 HANDOFF §6i / GT research §E9・§E10・§E11 |
| **M10** | 非同期job & フリーズ対策（job 化 / render busy / watchdog） | ✅ **完了**: T10.1 job 化(#27)・T10.2 render busy(#28)・T10.3 watchdog(#30) | 確定要約 HANDOFF §6j / GT research §E12・§E13（--dry-run は M13 繰越） |
| **M11** | exec-python（既定 off・`EXEC_DISABLED` / audited=許可ハッシュ自走 / trusted・AST flag・監査） | ✅ **完了**: T11.1 mode ゲート / T11.2 AST flag / T11.3 監査+許可ハッシュ（**PR #32**・base main） | 確定要約 HANDOFF §6k / GT research §E14 |
| **M12** | Skill 同梱 & スキーマ同期（`.claude/skills/bli/` + cli-schema.json 生成 + schema_hash） | ✅ **完了**（**PR #33**・stacked on #32） | 確定要約 HANDOFF §6l / D12 |
| **M13** | テスト網羅 & CI 仕上げ（bl_rna 契約 / L2 Blender マトリクス / golden / L3 / snapshot） | ✅ **完了**（**PR #34**・stacked on #33） | 確定要約 HANDOFF §6m |
| **M14** | ドキュメント & 配布（addon zip ビルド・vendoring 検証・README・doctor 導入支援・mistakes-memo） | ✅ **実装完了**（**PR #36**・base main・マージ待ち） | 確定要約 HANDOFF §6n / DoD=クリーン環境で導入→ping→3シナリオ |

★ = walking skeleton。✅=完了 / 🔶=進行中 / ⬜=未着手。

---

## M8 の現況（代表ユースケース + 実地フィードバック対応）

**M8 は代表ユースケース（ドメインパック）**（spec の3例: 原点変更 / 直立補正 / 3Dプリンタ対応）。汎用基盤の上に載る具体例という位置づけ。サブPR分割で進行。

### コア3シナリオ（タスク T8.x）
| タスク | コマンド | 状態 |
|---|---|:--:|
| T8.1 | `set-origin` | ✅（M3 で実装済み・S1 golden 緑） |
| T8.2 | `straighten`（reset/world-align/pca/floor） | ✅ PR #10 |
| T8.3 | `print-setup`（単位 mm/m・非破壊） | ✅ PR #11 |
| T8.4 | `print-check` / `print-repair`（bmesh 自前 + print3d 縮退） | ✅ PR #12 |
| **T8.5** | **`print-export`（stl / 3mf→CAPABILITY+STL hint）** | ✅ PR #20（マージ待ち）＝**M8 実装完了**・研究 §E8 |

### 実地フィードバック対応ワークストリーム（T8.5 の前に差し込み・feedback-first）✅ **完了（PR-1〜5）**
エージェントに `straighten` 傾き補正を実地で使わせた検証で「単体では完遂不可」と判明 → 全7項目に対応。
出典: `FEEDBACK-straighten-2026-06-15.md`。詳細は **`.handoff/NEXT-M8-feedback.md`**。

| PR | 対応（FB番号） | 状態 |
|:--:|---|:--:|
| PR-1 | 横断クイックウィン（#7 UTF-8 出力固定・`--target` 別名・dimensions/bbox 文書化） | ✅ PR #13 |
| PR-2 | straighten 根本修正（#5 up_hint/tilt・#2 dry-run・#6 吸収） | ✅ PR #14 |
| PR-3 | capture（#1 viewport/screen/render の状態キャプチャ） | ✅ PR #15 |
| PR-4 | 基準指定整列（#4 straighten に angle/align-vector/reference 追加・支柱問題） | ✅ PR #17 |
| PR-5 | undo/redo 公開（#3 `bli undo`/`redo`・GUI 必須・スタック端 RuntimeError 頑健化） | ✅ PR #18 |

→ **M8〜M13 完了（main マージ済み・PR #10〜#35）。M14 ドキュメント&配布は実装完了＝PR #36 マージ待ち（確定要約 HANDOFF §6n）。これで v1 全マイルストーン（M0–M14）実装完了。** 残るは GUI 実機での zip 導入→`bli ping` の手動確認（headless 不可・README 記載）と任意の配布公開（Extensions/PyPI は後続）。
※ FB #4 の「部分ジオメトリ PCA（頂点サブセット基準）」は部分指定方法の決定が要るため別 PR 繰越（PR-4 では angle/align-vector/reference で支柱問題に実用解を提供済み）。

---

## 確定済みの主要判断（詳細は HANDOFF §3 / spec D1–D14）
- D1 接続=常駐 Blender(GUI)+アドオン TCP / D2 5.0 主軸・4.4 ベストエフォート（**番号分岐禁止・能力検出**）。
- D3 ハイブリッド（構造化主軸 + exec 逃げ道）/ D5 exec-python 既定 off。
- D6 同時接続 fail-fast / D7 重量ガードなし（watchdog+非同期job）。
- セキュリティ: 127.0.0.1 固定・トークン認証・監査ログ。
- アーキテクチャ: `packages/{bli-core(純Python SSOT・依存ゼロ), bli-cli(Typer/Pydantic), bli-addon(TCP+bpy・Pydantic 禁止)}`（uv workspace）。

## 守る規約（HANDOFF §8 / §6e）
- bli-core は**純Python・依存ゼロ**。生 `bpy.ops` は **gateway.py のみ**（AST guard）。bmesh も gateway/bmesh_ops 集約。
- 入力検証は **bpy 到達前**（`_require_input`）。非対応型/能力欠如は **E_PRECONDITION / CAPABILITY_UNAVAILABLE**（INTERNAL にしない）。
- 破壊的 mesh 編集は**共有ガード**（`_guard_shared_mesh`）。fingerprint は操作の本質に合わせる。
- main 直接コミット禁止 / 日本語コミット + prefix / PR 経由マージ（マージはユーザー判断）。
- レビュー: Codex 上限時は **独立3視点セルフレビュー**（設計 / 敵対的 correctness / 仕様・テスト）。
- 実機 smoke は **5.0.1 / 4.4.3 両版**（`spikes/smoke_ops.py`・`--background`）。GUI 必須機能（capture 等）は GUI スパイク（`spikes/capture_spike.py`・`blender.exe --python`）。
