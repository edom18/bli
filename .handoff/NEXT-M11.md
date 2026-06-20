# 【履歴】M11「exec-python（既定 off）」着手書 — ✅ 完了（PR #32）

> **このファイルは履歴**。M11（T11.1 mode ゲート / T11.2 AST flag / T11.3 監査+許可ハッシュ）は実装完了＝**PR #32**。確定要約は `HANDOFF.md §6k`、GT は `research.md §E14`。キックオフ R-A〜R-E は全て推奨案でユーザー確定済み（R-A=真実源はサーバが読む user-local policy.toml）。次の作業は **M14 = `.handoff/NEXT-M14.md`**。以下は着手時の設計メモ（参考）。

最終更新: 2026-06-20 / 前提: **M0–M10 完了**（M10 確定要約 HANDOFF §6j）。出典: `plan.md §M11` / `spec.md`（§270 exec-python 3モード・§277 AST ヒューリスティック・§280 監査・§284 設定配置・§459 脅威モデル・D3/D5）/ §エラー `EXEC_DISABLED`。全体俯瞰 `.handoff/ROADMAP.md`。

> 新規セッションは `HANDOFF.md` → `ROADMAP.md` → この着手書の順で読む。**M11 はセキュリティが主眼＝設計判断（§2）をユーザー確認してから着手する**。

## 0. M11 の本質（最初に共有・誤解しやすい）
- exec-python は **構造化サブコマンドで表現不能な操作のフォールバック**（spec D3 ハイブリッドの逃げ道）。**3シナリオは exec 不要**で完遂できる（M8 で実証済み）。
- **「サンドボックスは提供しない」**（spec §459・確定判断）。exec は同一 OS ユーザ権限で動く＝**防止ではなく検知・追跡**（監査ログ）。AST 検査は「安全保証」ではなく**ヒューリスティックなフラグ付け**（`security_guarantee: false` を必ず返す）。
- 既定 `off`。昇格（audited/trusted）は **ユーザ所有の設定ファイルでのみ**可能で、**CLI フラグ単体では緩められない**（spec §276）。**ここが M11 の肝**: enforcement は **サーバ側（Blender アドオン）** に置く。CLI は信頼境界の外（CLI が mode を送るだけでは昇格できない設計にする）。
- 実行はメインスレッド直列（既存 Dispatcher 経由・mutates=True）＝**新しい timer/handler 機構は不要 → GUI スパイク不要**。実機検証は両版 background smoke（exec はメインスレッドで走る）。

## 1. スコープ（plan.md §M11）
| タスク | 内容 | 状態 |
|---|---|:--:|
| T11.1 | `mode=off`（`EXEC_DISABLED`）/ audited / trusted。**設定昇格のみ**（CLI 単体で緩めない） | ⬜ **次** |
| T11.2 | AST ヒューリスティック flag（`security_guarantee:false` / `heuristic_flags`） | ⬜ |
| T11.3 | audited の監査記録（`audit/`）・承認ゲート or 許可ハッシュで自走 | ⬜ |
- **DoD（plan）**: 既定で無効、設定時のみ動作。監査ログ確認。

## 1.5 既存の足場（M11 はこの上に乗る）
- **bli-core**: `definitions.py` に `exec-python`（params=`code:STR`/`file:PATH`・mutates=True・EXPERIMENTAL・**`implemented=False`**＝発見系に出ない／help は introspection 可）。`errors.ErrorCode.EXEC_DISABLED` 定義済み。
- **設定**: `bli/config.py` の `.bli/config.toml` 雛形に `[exec] mode = "off"`（off|audited|trusted）。`.gitignore` に `audit/` 既出。**ただしこれは CLI（プロジェクト）側**＝サーバ enforcement の真実源は §2 R-A で決める。
- **監査基盤**: spec §280「メインスレッドの単一実行口を通る全 Python 文字列を `audit/` に記録」。runtime に `BLI_STATE_DIR` あり（audit の置き場候補）。

## 2. キックオフ判断（**着手時にユーザー確認**・推奨を併記）
M11 はセキュリティ設計が肝。以下を着手前に確定する（M6–M10 と同じ運用）。

- **R-A. exec mode の真実源（最重要）**: サーバ（アドオン）はどこから mode を読むか？
  - 推奨: **ユーザローカルのポリシーファイル**（`BLI_STATE_DIR/policy.toml` 等・OS 所有者限定）を**サーバが読む**。`.bli/config.toml`（プロジェクト・git 管理可）は既定の提示用だが、**昇格の真実源はユーザローカル**にして「リポジトリに mode=trusted を commit すれば昇格」を防ぐ（spec §276「ユーザ所有の設定ファイルでのみ」に忠実）。CLI が送る mode は**無視 or off へ丸める**（CLI 単体で緩めない）。
  - 代替: (2) `.bli/config.toml` をサーバが CWD 基準で読む（簡単だが CLI の CWD をサーバが知れず・git commit で昇格できてしまう＝非推奨）。
- **R-B. audited の自走方式**: 承認ゲート（対話承認）か許可ハッシュ（事前承認コードの sha 一致で自走）か。
  - 推奨: **許可ハッシュ優先**（エージェント自走と相性が良い・対話承認は無人で詰む）。audited は「全コードを `audit/` に記録 + 許可ハッシュ集合に一致すれば実行・不一致は記録して `EXEC_DISABLED` か承認待ち」。承認ゲートは v1 では最小（or 繰越）。
- **R-C. exec の戻り値**: 何を返すか。
  - 推奨: **stdout/stderr キャプチャ + 最終式の repr（あれば）+ 粗いシーン fingerprint + `security_guarantee:false`/`heuristic_flags`**。例外は `E_OPERATOR`/`EXEC_ERROR`（INTERNAL にしない）。大出力は output_ref 退避（既存 `_ok_offload`）。
- **R-D. AST ヒューリスティックの検出対象（T11.2）**: どこまで flag するか。
  - 推奨: **import（os/subprocess/socket/shutil 等）・ファイル書き込み・`eval`/`exec`/`__import__`・ネットワーク**を `heuristic_flags` に列挙（**ブロックはしない**＝あくまで注意喚起・`security_guarantee:false`）。off/audited のゲートとは独立。
- **R-E. サブPR分割**: 推奨 **T11.1（mode ゲート + EXEC_DISABLED + 最小実行）→ T11.2（AST flag）→ T11.3（audit ログ + 許可ハッシュ）**。各 独立3視点セルフレビュー。

## 3. 着手前スパイク
- **GUI スパイク不要**（新しい timer/handler 機構なし＝exec は既存 Dispatcher でメインスレッド直列実行）。
- ただし **着手前に最小スパイク**を推奨（M0.5 流・両版 background）: `exec(compile(code, ...), namespace)` を Blender 埋め込み Python で実行し、(1) `bpy` を渡した namespace で `bpy.data.objects` 等が触れるか、(2) stdout を `contextlib.redirect_stdout` でキャプチャできるか、(3) 例外の型（RuntimeError 以外も）を確認。research.md に **§E14** として残す。

## 4. 実装手順（サブPR ごと・M6〜M10 と同じ流儀）
### T11.1 mode ゲート + 最小実行（**次**）
- **A. SSOT/errors**: `exec-python` を `implemented=True` 化（発見系に出す・schema_hash 変わる）。`EXEC_DISABLED` は既出。必要なら `EXEC_ERROR`（実行時例外）を errors に追加（INTERNAL 化回避）。
- **B. ポリシー読取（R-A の真実源）**: サーバ側で mode を読むモジュール（bli-core or bli-addon）。**bli-core は純Python・依存ゼロを厳守**（toml 読取は CLI 側 or 標準 `tomllib`＝3.11+・アドオンは Blender 3.11 で可だが bli-core は 3.10 互換維持に注意＝toml 読取は addon 側に置く）。既定 off。
- **C. addon**: `ops` に `_exec_python` ハンドラ。**dispatch 前 or ハンドラ冒頭で mode を確認**し off なら `EXEC_DISABLED`（category=PRECONDITION・retryable=False）。audited/trusted のみ実行。code/file の排他・file は存在/パス安全（outputs/state 配下逸脱拒否は output_ref の作法を流用）。実行は `exec(compile(...), ns)`・`bpy` を namespace に注入・stdout/stderr キャプチャ。
- **D. CLI**: `exec-python --code <str> | --file <path>`（排他）。`--code`/`--file` 未指定は送信前 USER_INPUT。mode は**送らない**（or 送っても無視・R-A）。
- **E. テスト/smoke**: L1（off→EXEC_DISABLED・code/file 排他・パス安全）/ L3（mode off で拒否・trusted で実行して結果回収）/ background smoke 両版（trusted で `bpy.data.objects` を触る最小コード・stdout キャプチャ・例外→EXEC_ERROR）。**mode 昇格が CLI 単体でできないことを明示テスト**（R-A の核心）。

### T11.2 AST ヒューリスティック flag
- `ast.parse` で R-D の対象を検出し `heuristic_flags`（list）を結果に載せる。`security_guarantee: false` を常に付与。**ブロックはしない**（注意喚起）。L1 で各 flag の検出を golden 化。

### T11.3 audit ログ + 許可ハッシュ
- 実行した全コード文字列を `BLI_STATE_DIR/audit/`（or R-A の場所）へ追記（タイムスタンプ・sha256・mode・flags）。audited は許可ハッシュ集合（ユーザローカル）に一致すれば自走・不一致は記録して拒否 or 承認待ち（R-B）。L1/L3 で記録と一致判定を検証。

## 4.5 参考: 設計上の注意
- **bli-core 純Python・依存ゼロ・3.10 互換**を厳守（toml/exec 実行は addon/CLI 側）。生 `bpy.ops` は gateway のみ（exec 内のユーザコードは別＝AST guard の対象外だが、ハンドラ自身は規約遵守）。
- exec のユーザコードは**サンドボックスされない**＝レスポンスで必ず `security_guarantee:false` を返し、過信させない文言を `methods.md`/`help` に明記。
- 監査は「防止でなく検知」（spec §280）。off でも「呼ばれた事実」を記録するか（R 候補）。

## 5. 必ず守る規約（HANDOFF §8 / §6e）
- bli-core 純Python・依存ゼロ・3.10 互換。生 `bpy.ops` は gateway のみ。検証は bpy 到達前。非対応/能力欠如・実行時例外は INTERNAL にしない（EXEC_DISABLED/EXEC_ERROR/E_PRECONDITION へ写像）。
- main 直接禁止・日本語コミット + prefix・PR 経由（マージはユーザー判断）。Codex 上限時は **独立3視点セルフレビュー**（設計 / 敵対的 correctness / 仕様・テスト）。
- 実機 smoke は 5.0.1 / 4.4.3 両版（exec はメインスレッド直列＝background smoke で可・GUI スパイク不要）。

## 6. 参照
- **spec**: §270 exec-python 3モード / §277 AST ヒューリスティック / §280 監査・ロギング / §284 設定配置 / §459 脅威モデル / D3・D5。**plan**: §M11。
- **足場**: `definitions.py`（exec-python `implemented=False`）/ `errors.EXEC_DISABLED` / `config.py`（`.bli/config.toml [exec] mode`）。
- **流儀**: 入力検証は bpy 前（`ops._require_input`）/ 大出力は `_ok_offload`（output_ref 退避）/ パス安全は output_ref の `_safe_output_path` 作法。
