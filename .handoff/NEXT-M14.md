# 次の作業 — M14「ドキュメント & 配布」着手書

最終更新: 2026-06-21 / 前提: **M0–M13 完了**（M11–M13 は PR #32–#34 マージ待ち＝マージ後に着手するのが安全）。出典: `plan.md §M14`。全体俯瞰 `.handoff/ROADMAP.md`。

> 新規セッションは `HANDOFF.md` → `ROADMAP.md` → この着手書の順で読む。**M14 着手前に PR #32→#33→#34 をこの順でマージする**（スタックのため。マージ後に main を pull）。

## 0. 着手（コピペ）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout main && git pull origin main      # #32–#34 マージ後
git checkout -b feature/m14-docs-dist
uv sync && PYTHONUTF8=1 uv run pytest -q        # ベースライン緑（438+）
```

## 1. M14 の本質
**v1 の総仕上げ＝クリーン環境で「導入→ping→3シナリオ」が再現できる状態にする**（DoD）。最大の技術論点は
**vendoring**: アドオンは Blender 埋め込み Python（3.11系・venv なし）で動くため、`bli-core` をアドオンに
**同梱（vendoring）**しないと実機インストール時に `import bli_core` が解決できない。現状の dev/smoke は
`sys.path` を手で通している（`smoke_ops.py`/`contract_check.py`）だけで、配布物では成立しない。

## 2. スコープ（plan.md §M14）
| タスク | 内容 | 状態 |
|---|---|:--:|
| T14.1 | README / インストール手順（pipx で CLI + addon zip 導入） | ⬜ |
| T14.2 | **addon zip ビルド**（`bli-core` を `bli_addon/vendored/bli_core` へコピー + `blender_manifest.toml`） | ⬜ |
| T14.3 | `doctor` 導入支援（addon 未導入時の導入ガイド出力） | ⬜ |
| T14.4 | `mistakes-memo` 運用開始（`.claude/mistakes-memo.md`・規約 mistakes.md） | ⬜ |
- **DoD**: クリーン環境で「addon zip 導入 → `bli ping` 疎通 → 3シナリオ（set-origin/straighten/print-*）」が再現。

## 2.5 既存の足場
- `packages/bli-addon/blender_manifest.toml` は既にある（Extensions 形式の manifest・中身の確認要）。
- `packages/bli-addon/src/bli_addon/` がアドオン本体。`bli_core` への import が各所にある（vendoring 対象）。
- `README.md` あり（Claude Code 連携手順は追記済み・インストール章の整備が要）。
- `bli-cli` は `pipx install`（plan.md R5）。`bli-core` は CLI 側では通常依存、addon 側では vendoring。

## 3. 着手前の確認（スパイク相当）
- **vendoring 実機検証**: `bli_core` を `bli_addon/vendored/` にコピーし、アドオン内 import を
  `from .vendored import bli_core` 等へ切替えても 5.0/4.4 で動くか（または build 時に `bli_core` を
  `bli_addon/` 直下へ置き sys.path に頼らず解決できるか）。**zip 導入した実機**で `register()`→`bli ping`
  まで通すのが確証。GUI 実機（zip を Preferences から導入）で確認する。
- `blender_manifest.toml` の必須フィールド（id/version/blender_version_min/wheels or python_modules）と
  5.0/4.4 の Extensions 互換を確認（4.4 は legacy add-on 形式が要るかも＝両対応の要否を判断）。

## 4. キックオフ判断（着手時にユーザー確認・推奨を併記）
- **R-A. vendoring 方式**: (1) build 時に `bli_core` を `bli_addon/` 直下へコピーし import をそのまま
  解決（推奨・dev は editable で sys.path、配布は同梱）/ (2) `vendored/bli_core` サブパッケージ + import 書換え。
  → どちらも「アドオンに Pydantic を持ち込まない・`bli-core` は純Python」を守る（規約）。
- **R-B. 配布形式**: Extensions（5.0 の新 add-on 形式・`blender_manifest.toml`）一次 / legacy add-on（4.4）
  との両対応をどこまでやるか（HANDOFF D10=「手動zip一次・Extensions 後続」＝最小は手動 zip）。
- **R-C. build スクリプト**: `scripts/build_addon.py`（vendoring コピー + zip 化・決定的）。CI で artifact 化するか。
- **R-D. mistakes-memo**: `.claude/mistakes-memo.md` を作り運用開始（規約 `mistakes.md`）。M11–M13 で得た教訓
  （例: tomllib は addon=3.11/test=3.12・CRLF 警告は無害・setup-blender の 5.0 解決不確実）を初期エントリに。

## 5. 必ず守る規約（HANDOFF §8 / §6e）
- `bli-core` は純Python・依存ゼロ・3.10 互換（addon vendoring 後も維持）。生 `bpy.ops` は gateway のみ。
- main 直接禁止・日本語コミット + prefix（M14 は `docs:`/`chore:`/`feat:` を適宜）・PR 経由（マージはユーザー判断）。
- Codex 上限時は **独立3視点セルフレビュー**。実機検証は 5.0.1 / 4.4.3 両版。**zip 導入の実機確認は GUI**。

## 6. 参照
- **plan.md §M14** / HANDOFF §9（アーキテクチャ）・§6l（Skill 同梱）。`blender_manifest.toml`・`README.md`。
- M11–M13 繰越（HANDOFF §10）: audited 監査 fail-closed / policy.toml 権限検証 / setup-blender の 5.0 version 調整。
