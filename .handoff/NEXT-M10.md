# 次の作業 — M10「非同期job & フリーズ対策」（計画・**キックオフ判断は要確認**）

最終更新: 2026-06-18 / 前提: **M0–M9 完了（PR #1–#26 マージ済み）**。出典: `plan.md §M10` / `spec.md §7 重量処理`（観測性で守る）/ §エラー（`BUSY_RENDERING`/`MAIN_THREAD_UNRESPONSIVE`/`TIMEOUT`）/ §終了コード（`2=TIMEOUT_PENDING`）。**土台は M4**（settle/RUNNING/request-status/RequestRegistry＝HANDOFF §6b）。全体俯瞰 `.handoff/ROADMAP.md`。

> このファイルは **計画**。実装着手の前に §2 のキックオフ判断をユーザーと確定する。確定後にこの計画を更新して着手する。

## 0. M10 の本質（最初に共有・誤解しやすい）
- **bpy はメインスレッド直列**。重量ネイティブ処理（importer/exporter/boolean/decimate/print-check の C 内部）は **1回の blocking 呼び出しで、中断もチャンク化もできない**（spec §7 line 338 の残存リスク）。
- したがって M10 の「非同期」は **接続（クライアント）を塞がない + 観測性の確保**であって、メインスレッドの真の並列化ではない（重量処理中は GUI/メインスレッドは固まり得る＝仕様上の許容リスク）。
- **土台は M4 で既にある**: `Dispatcher.submit(fn, timeout, settle)` は完了で返るか `DISPATCH_TIMEOUT` で `TimeoutPending`→サーバが `TIMEOUT`(exit2) を返し、`settle` がジョブ完走時にメインスレッドで `RequestRegistry` を DONE/FAILED 確定。`request-status`（LOCK_FREE）は実行中（RUNNING）でも registry を直接読んで決着を回収。**M10 はこれを「accepted 即返 + job-status/job-wait」の一級モデルに整える**のが主眼で、機構をゼロから作るわけではない。

## 1. スコープ（plan.md §M10）
| タスク | 内容 | 状態 |
|---|---|:--:|
| T10.1 | heavy コマンドの job 化（job_id 採番・`accepted` 即返・`job-status`/`job-wait`） | ⬜ 最重要 |
| T10.2 | render busy 拒否（`render_init`/`render_complete` handler・`BUSY_RENDERING` 即拒否） | ⬜ |
| T10.3 | heartbeat watchdog（`MAIN_THREAD_UNRESPONSIVE` 検知・通知） | ⬜ |
| T10.4 | `--dry-run` 一般化（現状 straighten のみ→対象拡張） | ⬜ 独立性高（最後 or 繰越） |
- **DoD（plan）**: 重量 import 中も接続が塞がらない（L3 で検証）。

## 2. キックオフ判断（**要確認・推奨を併記**）
非同期は設計の肝。実装前に確定する。

- **D-A. job_id は既存 request_id（rid）/ `RequestRegistry` を再利用するか** → 推奨: **再利用（Yes）**。registry は既に PENDING/RUNNING/DONE/FAILED を持ち request-status で決着回収できる。`job-status` は実質 request-status の job 文脈版（state + accepted_at + 経過）/ `job-wait` は「DONE/FAILED まで `--timeout` 付きで待つ」ポーリング。新 JobRegistry は作らない（M4 機構の二重化回避）。
- **D-B. 非同期コマンドの UX（エージェント体験の肝・最重要）** → 推奨: **(1) 既定は「sync 見え」＝CLI が `accepted`→`job-wait` で最終結果まで自動待機して返す / `--async`（fire-and-forget）で job_id を即返**。
  - 理由: エージェントの大半は結果を同期で欲しい。「呼ぶ→結果」が最も使いやすい。接続を即解放して別作業を回す真の非同期は `--async` でオプトイン。
  - 代替: (2) heavy は常に `accepted` 即返・ポーリング必須（spec line 335 の字面に忠実だが毎回ポーリングは煩雑）/ (3) 現状の「sync 実行・超過で TIMEOUT_PENDING+id」を formalize するだけ（最小変更だが accepted 即返にはならない）。
- **D-C. どのコマンドを heavy（job 対象）とするか** → 推奨: `import` / `export` / `mesh --op boolean|decimate` / `print-check` / `print-repair`（plan/handoff で heavy 候補と明記済み）。`open`/`save` は I/O だが比較的軽く同期維持（要検討）。判定は **コマンド単位の静的フラグ `Command.heavy: bool`** を SSOT に追加（`mutates` と同流儀）。
- **D-D. サブPR分割**（M6–M9 同様・各 PR 独立3視点セルフレビュー） → 推奨: **T10.1（job 化 + job-status/wait・最重要）→ T10.2（render busy）→ T10.3（watchdog）**。T10.4（--dry-run）は独立性が高いので最後 or 別途。
- **D-E. `--dry-run` 一般化のスコープ** → 推奨: **M10 では深追いせず絞る or 繰越**。straighten の dry-run は「適用→読取→厳密復元」で実装済み。一般化は mutate 系ごとに「非破壊プレビュー」の定義が別物で設計が膨らむ。M10 コアは job/watchdog。--dry-run は影響が予測しやすいもの（例: delete/transform）に絞るか M13 へ繰越。

## 3. 着手前に必須のスパイク（M0.5 流・両版・research.md に §E12 として残す）
1. **render handler（T10.2・GUI 必須）**: `bpy.app.handlers.render_init`/`render_complete`/`render_cancel` が常駐サーバで発火し busy フラグを立て/降ろせるか（`capture --source render` と組合せ）。`render_pre`/`render_post` の使い分けも確認。
2. **watchdog timing（T10.3・GUI 必須）**: pump タイマが生存印（`last_pump_ts`）を更新 → 別スレッドが N 秒未更新で `MAIN_THREAD_UNRESPONSIVE` 判定。重量 op 中に **pump タイマが止まる**ことを実機で確認（background は timer 非発火なので GUI スパイク）。`DISPATCH_TIMEOUT` との関係で誤検知しない閾値を詰める。
3. **accepted 即返（T10.1・机上+L3）**: 受信スレッドが job を queue に積んで即 `accepted` 返却し、`settle` が後で registry 確定する経路が成立するか。重量 job 実行中の 2 本目接続は `job-status`/`job-wait` だけ通す（**LOCK_FREE_METHODS 拡張**）＝SESSION_BUSY の緩和を確認。

## 4. 実装手順（サブPR ごと・M9 までと同じ流儀）
### T10.1 job 化（最重要）
- **A. SSOT**: `Command.heavy`(bool) を definitions に追加。`job-status`/`job-wait`（lock-free・request-status 同類・`--id`/`--timeout`）を定義。runtime に job 定数（既定 wait timeout 等）。
- **B. server/dispatcher**: heavy コマンドは受信時に rid を RUNNING 登録 → `{status: accepted, job_id: rid}` 即返 → 実体を dispatcher に submit（settle が registry 確定）。`job-status`/`job-wait` を `LOCK_FREE_METHODS` に追加（registry 直読・実行中の別接続でも応答）。
- **C. CLI**: heavy コマンドは既定 auto-wait（job-wait ループで最終結果を表示・`--async`/`--no-wait` で job_id 即返）。`job-status --id` / `job-wait --id [--timeout]` サブコマンド。終了コードは既存写像（DONE→0 / FAILED→1 / 未決→2）。
- **D. テスト/smoke**: L1（job-status/wait の param/exit）+ **L3（重量ジョブ実行中に別接続が job-status を取得できる＝接続が塞がらない＝DoD）** + 実機 smoke（軽量 job の往復）。
### T10.2 render busy
- render handler で busy フラグ → 重量/mutating は `BUSY_RENDERING` 即拒否（キューに積まない）。GUI スパイクで handler 発火を確認。
### T10.3 watchdog
- pump 生存印 + 監視スレッド → `MAIN_THREAD_UNRESPONSIVE` を job-status/応答に載せる（通知のみ・実行は止めない）。

## 5. 必ず守る規約（HANDOFF §8 / §6e）
- bli-core 純Python・依存ゼロ。生 `bpy.ops` は gateway のみ。検証は bpy 到達前。非対応/能力欠如は INTERNAL にしない。
- main 直接禁止・日本語コミット + prefix・PR 経由（マージはユーザー判断）。Codex 上限時は **独立3視点セルフレビュー**（設計 / 敵対的 correctness / 仕様・テスト）。
- 実機 smoke は 5.0.1 / 4.4.3 両版。GUI 必須（render handler / watchdog timer）は GUI スパイク（`blender.exe --python`）。

## 6. 参照
- **土台**: M4（HANDOFF §6b・`settle`/`RUNNING`/`request-status`/`RequestRegistry`・`DISPATCH_TIMEOUT < CLIENT_READ_TIMEOUT`）。`dispatcher.py`（submit/pump/settle/TimeoutPending）・`server.py`（session_lock/`LOCK_FREE_METHODS`/_handle_rpc/settle）・`request_registry.py`（begin/complete/lookup・PENDING/RUNNING/DONE/FAILED）。
- **spec**: §7 重量処理（line 332–338）/ §エラー（`BUSY_RENDERING`/`MAIN_THREAD_UNRESPONSIVE`/`TIMEOUT`）/ §終了コード（2=TIMEOUT_PENDING）。**plan**: §M10。
- **繰越元**: M9 の heavy 候補（boolean/decimate/import/print-check/repair）の job 化はここで回収。output_ref GC（M5 繰越・24h/200件/200MiB）も M10 で対応可。
