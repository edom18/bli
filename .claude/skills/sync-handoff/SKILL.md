---
name: sync-handoff
description: bli プロジェクトの引き継ぎ資料（.handoff/HANDOFF.md・ROADMAP.md・NEXT-M*.md）を現在の実装状態へ同期し、docs ブランチで PR する。タスク/サブPR が完了した後や、ユーザーが「引き継ぎを更新/作成して」「handoff を同期して」と言ったとき、または feature PR をマージした後に使う。新規セッションが1枚で再開できる状態を保つのが目的。
---

# sync-handoff — 引き継ぎ資料の同期

bli（Blender CLI）は `.handoff/` に引き継ぎ資料を置き、各タスク/サブPR 完了ごとに更新する運用。
この更新は毎回ほぼ同じ手順なので、その手順をここに固定する。**目的**: 新規セッションが
`HANDOFF.md` → `ROADMAP.md` → `NEXT-M*.md` の順に読めば即再開できる状態を常に保つこと。

## いつ使うか
- feature PR（サブPR）をマージした直後、または実装が main に乗った後。
- ユーザーが「引き継ぎ（handoff）を更新/作成して」「次の作業の着手書を作って」と言ったとき。
- マイルストーンを完了した／次のタスクへ移るとき。

## 致命的な教訓（必ず守る）— handoff の stale 化を防ぐ
**コード PR は次々マージされるのに handoff PR が取り残されると、main の `.handoff` が数マイルストーン
古いまま腐る**（過去に M8 時点で stale 化して混乱した）。これを防ぐため:
1. **handoff PR は1本に集約する**。新しく別の handoff PR を乱立させず、**既存の open な handoff
   docs PR があればそのブランチに追記**して常に「現状=最新」にする（`gh pr list` で確認）。
2. PR を出したら**ユーザーにマージを促す**一文を必ず添える（「これをマージすると main の handoff が
   最新化されます」）。マージはユーザー判断（main 直接禁止）。
3. 余裕があれば「handoff 更新を feature PR に同梱する」運用も提案してよい（別 PR の lag を根絶できる）。

## 手順

### 1. 現状を把握する
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout main && git pull origin main
git log --oneline -8            # 直近マージ（どのサブPRが入ったか）
gh pr list --state open         # 既存の open handoff PR があるか
```
- 直近の完了タスク（例 T10.1）と**次のタスク**（例 T10.2）を特定する。
- 数値の根拠を取る: `PYTHONUTF8=1 uv run pytest -q | tail -2`（pytest 件数）。緑なら状態行に書く。
- 出典の確認: `specs/blender-cli-core/`（plan.md のマイルストーン定義 / spec / methods.md / research.md §E*）。

### 2. ブランチを決める（集約優先）
- **既存の open handoff docs PR があれば、そのブランチに `git checkout` して追記**（集約）。
- 無ければ `git checkout -b docs/handoff-<topic>`（main 直接禁止）。
- handoff は **`.handoff/` だけ**を触る（コード変更は別 PR）。main の `.handoff` が古い場合でも、
  既存 handoff ブランチには最新内容があることが多い（そのブランチを基底にする）。

### 3. 3種のファイルを更新する（フォーマットは既存に厳密に倣う）
**A. `HANDOFF.md`**（全体史 + 規約。新規セッションの入口）
- 行3の**状態行**: `PR #1–#NN マージ済み / M? 完了 / M? 進行中＝T?.? 完了 / 残り … / 次は …`。
- **§5 進捗表**の該当マイルストーン行: `✅ 完了` / `🔶 進行中` / `⬜`。pytest 件数も更新。
- 直下の**状態サマリ行**（pytest/ruff/format/AST/pyright/両版 smoke）を最新の数値に。
- マイルストーンごとに **§6x セクション**を追加/更新（例 §6i=M9・§6j=M10）。**確定要約の型**:
  - 何を作ったか（コマンド/シンボル）/ キックオフ判断（D-* or R-*）/ DoD / 落とし穴 /
    **独立3視点セルフレビューで解消した P1/P2**（設計・敵対的・仕様の3観点）/ pytest 件数・両版 smoke。
  - グラウンドトゥルース参照（`research.md §E*`）を必ず明記。

**B. `ROADMAP.md`**（1枚俯瞰）
- 該当マイルストーン行の状態と PR 番号・確定要約（HANDOFF §6x）・着手書（NEXT-M*.md）リンク。
- 行3の最終更新日を today に。

**C. `NEXT-M<次>.md`**（次の作業の着手書）
- 完了タスクは `✅ PR #NN`、**次タスクを「次」に**。完了タスクの「確定要約」節（§1.5 等）を残す
  （次タスクが乗る機構のシンボルを把握できるように）。
- 次タスクの**着手手順**: §0 着手コピペ / §2 or §3 着手前スパイク（GUI 必須なら明記） /
  §4 実装手順（A SSOT→B gateway/addon→C ops→D CLI→E テスト/smoke の流儀）/
  §4.5 キックオフ判断（推奨を併記・着手時にユーザー確認）/ §5 規約 / §6 参照。
- マイルストーン完了時はそのファイルを「履歴」化し、次マイルストーンの `NEXT-M<次>.md` を新規作成。

### 4. 規約（守る）
- 文体は**日本語・短文**。記号や略号は既存に倣う（✅/🔶/⬜・§6x・GT=research §E*）。
- マイルストーン完了後は **HANDOFF/ROADMAP/NEXT-* を必ず更新**（plan.md の運用）。
- main 直接禁止・日本語コミット + `docs:` prefix・PR 経由（マージはユーザー判断）。
- レビューは Codex 上限時 **独立3視点セルフレビュー**（設計 / 敵対的 correctness / 仕様・テスト）—
  これは handoff の §6x にも要約として書く対象。
- 実機検証は 5.0.1 / 4.4.3 両版・GUI 必須機能は GUI スパイク（背景では timer 非発火）。

### 5. コミット & PR & メモリ
```bash
git add .handoff/
git commit -m "docs: 引き継ぎを <状態> へ更新（次は <次タスク>）"   # 末尾に Co-Authored-By トレーラ
git push origin <branch>
```
- 既存 handoff PR があれば `gh pr edit <n> --title/--body` で**集約**（新規 PR を作らない）。
  無ければ `gh pr create --base main`。**body に「マージすると main の handoff が最新化される」一文**。
- 永続メモリ（`memory/project-bli-status.md` と `MEMORY.md` 索引行）も現フェーズに同期する
  （状態行・次タスク・open PR 番号）。

## 出力（ユーザーへ）
- 更新した3ファイルの要点（状態行・進捗・次タスク）を3〜5行で。
- handoff PR 番号と「マージで main 最新化」の一言。
- 次の作業（次タスク名 + GUI スパイクの要否など）を1行で。
