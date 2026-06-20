# 【履歴】M10「非同期job & フリーズ対策」: **T10.1/T10.2/T10.3 全完了 ✅（M10 完了）**

> このファイルは履歴。**次の作業は M11 exec-python = `.handoff/NEXT-M11.md`**。M10 の確定要約は `HANDOFF.md §6j`、GT は `research.md §E12`（render busy）/`§E13`（watchdog）。

最終更新: 2026-06-20 / 状態: **M10 完了** — T10.1 job 化（PR #27）/ T10.2 render busy（PR #28）/ T10.3 watchdog（PR #30）。出典: `plan.md §M10` / `spec.md §7 重量処理`（観測性で守る）/ §エラー（`BUSY_RENDERING`/`MAIN_THREAD_UNRESPONSIVE`/`TIMEOUT`）/ §終了コード（`2=TIMEOUT_PENDING`）。土台は M4（settle/RUNNING/request-status/RequestRegistry＝HANDOFF §6b）。全体俯瞰 `.handoff/ROADMAP.md`。

> 以下は M10 着手時の計画・キックオフ判断・スパイク手順の記録（履歴）。T10.1=§1.5 / T10.2=§1.6 / T10.3 の確定は HANDOFF §6j。キックオフ判断 D-A〜D-E（§2）・R-A/R-B（T10.2）・R-C〜R-E（T10.3）はすべて確定済み。

## 0. M10 の本質（最初に共有・誤解しやすい）
- **bpy はメインスレッド直列**。重量ネイティブ処理（importer/exporter/boolean/decimate/print-check の C 内部）は **1回の blocking 呼び出しで、中断もチャンク化もできない**（spec §7 line 338 の残存リスク）。
- したがって M10 の「非同期」は **接続（クライアント）を塞がない + 観測性の確保**であって、メインスレッドの真の並列化ではない（重量処理中は GUI/メインスレッドは固まり得る＝仕様上の許容リスク）。
- **土台は M4 で既にある**: `Dispatcher.submit(fn, timeout, settle)` は完了で返るか `DISPATCH_TIMEOUT` で `TimeoutPending`→サーバが `TIMEOUT`(exit2) を返し、`settle` がジョブ完走時にメインスレッドで `RequestRegistry` を DONE/FAILED 確定。`request-status`（LOCK_FREE）は実行中（RUNNING）でも registry を直接読んで決着を回収。**M10 はこれを「accepted 即返 + job-status/job-wait」の一級モデルに整える**のが主眼で、機構をゼロから作るわけではない。

## 1. スコープ（plan.md §M10）
| タスク | 内容 | 状態 |
|---|---|:--:|
| T10.1 | heavy コマンドの job 化（job_id 採番・`accepted` 即返・`job-status`/`job-wait`） | ✅ **PR #27**（§1.5） |
| T10.2 | render busy 拒否（`render_init`/`render_complete`/`render_cancel` handler・`BUSY_RENDERING` 即拒否） | ✅ **PR #28**（§1.6） |
| T10.3 | heartbeat watchdog（`MAIN_THREAD_UNRESPONSIVE` 検知・通知） | ✅ **PR #30**（HANDOFF §6j・研究 §E13） |
| ~~T10.4~~ | ~~`--dry-run` 一般化~~ | **M13 へ繰越（M10 スコープ外・確定）** |
- **DoD（plan）**: 重量 import 中も接続が塞がらない（L3 で検証）→ **T10.1 で達成**（`test_e2e_jobs.py`）。

## 1.5 T10.1 完了要約（✅ PR #27・実装の現実）
heavy コマンド（`import`/`export`/`print-check`/`print-repair`・`mesh` op が `boolean`/`decimate`）を非同期 job 化した。**T10.2/T10.3 はこの機構の上に乗る**ので、以下のシンボルを把握しておく。
- **bli-core**: `Command.is_heavy`（コマンド全体が heavy）/ `Command.heavy_ops`（op 依存・mesh の boolean/decimate）+ `is_heavy_request(cmd, params)`（純Python・両者を判定）。`job-status`/`job-wait` 定義（CLI ポーリングのシュガー）。runtime: `JOB_WAIT_TIMEOUT=1800`/`JOB_POLL_INTERVAL=0.5`/`JOB_POLL_MAX_CONNECT_FAILS=10`。
- **addon**: `dispatcher.submit_async(fn, settle)`（待たずにキュー）+ `dispatcher.ACCEPTED` センチネル。`__init__._executor` が `is_heavy_request` で分岐＝heavy は `submit_async`→`ACCEPTED` を返す。`server._handle_rpc` が `ACCEPTED` を見て `{success, operation, accepted:true, job_id:rid}` を即返す（registry は `begin` の RUNNING のまま・`settle` が完了時に確定）。**registry TTL は `max(600, JOB_WAIT_TIMEOUT)`**（完了 job の遅延回収が purge で消えないように・レビュー P1）。
- **CLI**: `_await_job`（request-status ポーリング・UNKNOWN job_id は即失敗・瞬断は `JOB_POLL_MAX_CONNECT_FAILS` まで許容）/ `_present_result`（提示の共有）/ `_rpc` が `accepted` を検出して auto-wait か `--async` 即返。`job-status`/`job-wait` コマンド。
- **DoD の鍵**: `request-status` は `LOCK_FREE_METHODS` で**受信スレッド処理（メイン dispatch を経由しない）**＝重量 job がメインを塞いでも応答する。**T10.2/T10.3 でも「観測系は lock-free で受信スレッド処理」を踏襲**（busy 拒否・watchdog 状態の問い合わせがメインを待たない）。
- **落とし穴（T10.1 で確認済み）**: `pump()` は `except BaseException`（重量ネイティブの C 異常で settle 漏れ→registry 孤児化＋pump 死を防ぐ）。背景 smoke は**自前の同期 executor**を使うため非同期経路を通らない＝job 経路の検証は L3 E2E（`test_e2e_jobs.py`・heaviness executor + 別スレッド pump）が担う。**bpy.app.timers は `--background` で発火しない**＝render handler/watchdog の実発火は GUI スパイク必須。

## 1.6 T10.2 完了要約（✅ PR #28・実装の現実）
レンダ中（`render_init`〜`render_complete`/`render_cancel`）は **mutating または heavy** を **dispatch 前**（`begin`/`settle` より前）に `BUSY_RENDERING`（ENVIRONMENT・retryable・CLI exit 2）で即拒否＝**キューに積まない**。**read-only と lock-free（request-status/job-status/job-wait）はレンダ中も通す**＝観測性を維持。キックオフ確定（§4.5）: **R-A**=mutating/heavy のみ拒否 / **R-B**=`BUSY_RENDERING`→exit 2。**T10.3 はこの上に乗る**ので以下を把握。
- **addon `render_state.py`（新規）**: 純Python の busy フラグ（`threading.Event`・`is_busy`/`mark_busy`/`mark_idle`/`reset`）+ bpy 依存の `install`/`remove`（render handler 登録・lazy import）。`install` は**冪等**（`_installed` 非空なら先に `remove`＝register 再入で二重登録しない）。`init_handler_registered()`（@persistent 生存のスモーク確認用）。
- **GUI スパイク（`spikes/render_spike.py`・GUI 必須・研究 §E12・両版）で確定**: render handler は **Blender のレンダスレッド（`Dummy-N`）から発火**（メインではない）→ busy は `threading.Event`（受信スレッドが安全に読む）。`render_complete` と `render_cancel` の両方で busy を降ろす（キャンセル取りこぼし防止）。**`@persistent` で `open_mainfile`（bli open）を跨いで生存**（付けないと open 後に busy 検知が無言で壊れる・background smoke `survived_open=True` で裏付け／GUI 内 timer から `read_homefile`/open を呼ぶと固まるため生存確認は background smoke で行う）。
- **server**: `Server(__init__/start)` に `render_busy: Callable[[], bool]` を注入（既定 `lambda: False`＝server は bpy 非依存のまま・テスト容易）。`_handle_rpc` が lock-free 早期 return と `has_lock` チェックの**後**、`begin` の**前**に `self._render_busy() and self._blocked_during_render(method, params)` で `BUSY_RENDERING` を即返す。`_blocked_during_render`=`load_definitions()`（受信スレッド冪等ロード）→`get_command` None なら不ブロック（未知メソッドは通常経路）→`cmd.mutates or is_heavy_request(cmd, params)`。アドオン `register()` が `render_state.install()` + `render_busy=render_state.is_busy` を配線。
- **CLI**: `_exit_code_for` に `BUSY_RENDERING`→`TIMEOUT_PENDING`(exit 2)。heavy が accepted ではなく業務エラーで返るため auto-wait に入らず exit 2 で即終了（`_call_or_exit`→`_emit_remote_error_exit`）。
- **テスト**: L1 `test_render_state.py`（純フラグ）/ L3 `test_render_busy.py`（注入 busy で mutating/heavy-non-mutating(export)/mesh 拒否・read-only/ping/request-status 通過・idle 非拒否・未知メソッド非 BUSY）/ CLI exit 写像。background smoke に busy 拒否（実 executor）+ @persistent 生存。独立3視点セルフレビュー P1 なし・P2/P3 解消（§6j）。pytest 331・両版 OPS SMOKE OK。
- **繰越**: `print-export` は `is_heavy=False`（T10.1 D-C で対象外）だが methods.md 表 `H=✓`＝ドリフト要再整合。busy stuck-ON（render が complete/cancel 発火せず死亡）は T10.3 watchdog で扱える。

## 2. キックオフ判断（**✅ 確定 2026-06-18**）
非同期は設計の肝。以下で確定（ユーザー確認済み）。**いずれも下記「推奨」で確定**:
- **D-A** ✅ job_id = 既存 `request_id`/`RequestRegistry` を再利用（新 JobRegistry は作らない）。
- **D-B** ✅ **(1) sync 見え既定 + `--async` オプトイン**＝heavy コマンドは既定で CLI が内部 job 化→`accepted`→`job-wait` で最終結果まで自動待機して返す（エージェントには同期に見える）/ `--async` で `{status:accepted, job_id}` を即返（fire-and-forget）。
- **D-C** ✅ heavy 対象 = `import`/`export`/`mesh --op boolean|decimate`/`print-check`/`print-repair`（`Command.heavy` フラグを SSOT に追加）。
- **D-D** ✅ サブPR分割 T10.1 → T10.2 → T10.3。
- **D-E** ✅ `--dry-run` 一般化は **M13 へ繰越**（M10 は job/watchdog に集中）。T10.4 は M10 スコープ外。

（以下は確定理由の控え）

- **D-A. job_id は既存 request_id（rid）/ `RequestRegistry` を再利用するか** → 推奨: **再利用（Yes）**。registry は既に PENDING/RUNNING/DONE/FAILED を持ち request-status で決着回収できる。`job-status` は実質 request-status の job 文脈版（state + accepted_at + 経過）/ `job-wait` は「DONE/FAILED まで `--timeout` 付きで待つ」ポーリング。新 JobRegistry は作らない（M4 機構の二重化回避）。
- **D-B. 非同期コマンドの UX（エージェント体験の肝・最重要）** → 推奨: **(1) 既定は「sync 見え」＝CLI が `accepted`→`job-wait` で最終結果まで自動待機して返す / `--async`（fire-and-forget）で job_id を即返**。
  - 理由: エージェントの大半は結果を同期で欲しい。「呼ぶ→結果」が最も使いやすい。接続を即解放して別作業を回す真の非同期は `--async` でオプトイン。
  - 代替: (2) heavy は常に `accepted` 即返・ポーリング必須（spec line 335 の字面に忠実だが毎回ポーリングは煩雑）/ (3) 現状の「sync 実行・超過で TIMEOUT_PENDING+id」を formalize するだけ（最小変更だが accepted 即返にはならない）。
- **D-C. どのコマンドを heavy（job 対象）とするか** → 推奨: `import` / `export` / `mesh --op boolean|decimate` / `print-check` / `print-repair`（plan/handoff で heavy 候補と明記済み）。`open`/`save` は I/O だが比較的軽く同期維持（要検討）。判定は **コマンド単位の静的フラグ `Command.heavy: bool`** を SSOT に追加（`mutates` と同流儀）。
- **D-D. サブPR分割**（M6–M9 同様・各 PR 独立3視点セルフレビュー） → 推奨: **T10.1（job 化 + job-status/wait・最重要）→ T10.2（render busy）→ T10.3（watchdog）**。T10.4（--dry-run）は独立性が高いので最後 or 別途。
- **D-E. `--dry-run` 一般化のスコープ** → 推奨: **M10 では深追いせず絞る or 繰越**。straighten の dry-run は「適用→読取→厳密復元」で実装済み。一般化は mutate 系ごとに「非破壊プレビュー」の定義が別物で設計が膨らむ。M10 コアは job/watchdog。--dry-run は影響が予測しやすいもの（例: delete/transform）に絞るか M13 へ繰越。

## 0. 着手前（コピペ可）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout main && git pull origin main          # T10.2(#28) マージ後
git checkout -b feature/m10-watchdog                # T10.3（次）
uv sync
PYTHONUTF8=1 uv run pytest -q                        # 331 passed（T10.2 まで）を確認
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
# 両版 background smoke（OPS SMOKE OK）:
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background \
  --python packages/bli-addon/spikes/smoke_ops.py 2>&1 \
  | sed -n '/BLI_OPS_SMOKE_BEGIN/,/BLI_OPS_SMOKE_END/p'
# GUI スパイク（watchdog timer は --background で発火しない＝GUI 必須・T10.3 着手時に作成）:
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" \
  --python packages/bli-addon/spikes/watchdog_spike.py   # ← T10.3 着手時に作成
# 参考: T10.2 の render handler スパイクは spikes/render_spike.py（GUI・実装済み）
```

## 3. 着手前に必須のスパイク（M0.5 流・両版・research.md に §E* として残す）
1. ~~**render handler（T10.2・GUI 必須）**~~ ✅ 済（`spikes/render_spike.py`・研究 §E12）: render_init/complete/cancel は GUI 常駐で発火し **レンダスレッド（Dummy-N）から呼ばれる**＝busy は `threading.Event`・別スレッドが `is_busy()=True` を観測・`@persistent` で open 跨ぎ生存（生存は background smoke で裏付け）。
2. **watchdog timing（T10.3・GUI 必須）**: pump タイマが生存印（`last_pump_ts`）を更新 → 別スレッドが N 秒未更新で `MAIN_THREAD_UNRESPONSIVE` 判定。重量 op 中に **pump タイマが止まる**ことを実機で確認（background は timer 非発火なので GUI スパイク）。`DISPATCH_TIMEOUT` との関係で誤検知しない閾値を詰める。research.md に **§E13** として残す。
3. ~~**accepted 即返（T10.1）**~~ ✅ 済（T10.1・`test_e2e_jobs.py` で L3 検証＝重量 job 実行中も request-status が応答・接続が塞がらない）。

## 4. 実装手順（サブPR ごと・M9 までと同じ流儀）
### T10.1 job 化 ✅ 完了（PR #27・要約は §1.5）
- ~~SSOT/dispatcher/server/CLI/テスト~~ 完了。独立3視点セルフレビューで P1（TTL/UNKNOWN ハング）+P2（pump BaseException / job-wait 提示共有 / heavy_ops 発見性 / 瞬断許容）解消。pytest 316・両版 OPS SMOKE OK。

### T10.2 render busy ✅ 完了（PR #28・要約は §1.6）
- ~~スパイク/render_state/server/CLI/テスト/smoke~~ 完了。GUI スパイク `render_spike.py`（研究 §E12）で render handler が**レンダスレッド（Dummy-N）から発火**＝busy は `threading.Event`、`@persistent` で open 跨ぎ生存（background smoke で裏付け）を確定。独立3視点セルフレビュー P1 なし・P2/P3 解消（install 冪等化 / smoke の未登録vs消失分離 / mesh docstring 訂正 / load_definitions 冪等ロード / 未知メソッド非 BUSY テスト / spec 終了コード表 exit2 注記）。pytest 331・両版 OPS SMOKE OK。**T10.3 でも「観測系は lock-free で受信スレッド処理」を踏襲**（busy フラグ同様、watchdog 状態も受信スレッドが読む）。

### T10.3 watchdog ✅ 完了（PR #30・確定要約は HANDOFF §6j・研究 §E13）
重量処理でメインスレッドが固まったことを検知して通知する（実行は止めない・観測性のみ）。**実装の現実**（着手時計画との差分）: 監視スレッドに加え `snapshot()` が**読み取り時にも**判定する二段構え（監視ポーリングに依存しない堅牢化）。露出は request-status（→job-status/job-wait）+ doctor に加え、**auto-wait/job-wait のポーリング中に固まりを一度だけ stderr 通知**（既定 auto-wait でも可視化＝セルフレビュー P1）。閾値 60s は実 decimate(1.3M面<1s)で誤検知しないことを GUI スパイクで実測。以下は着手時の計画（履歴）。
- **A. 着手前 GUI スパイク必須**（§3-2）: pump タイマが毎 tick で生存印（`last_pump_ts`）を更新 → 別スレッドが「今 − last_pump_ts > 閾値」で `MAIN_THREAD_UNRESPONSIVE` 判定。重量 op 中に **pump タイマが止まる**（=last_pump_ts が進まない）ことを GUI 実機で確認。`background` は timer 非発火なので **GUI スパイク必須**。閾値は `DISPATCH_TIMEOUT`(30s) と整合（誤検知しない・短すぎない）。research.md §E12 に追記。
- **B. addon**: dispatcher（または `__init__`）が `last_pump_ts` を更新（pump tick / install_timer 内）。別スレッド watchdog が定期チェックし、応答不能を**フラグ/状態**に載せる（通知のみ＝実行は止めない・kill しない）。
- **C. 露出**: `MAIN_THREAD_UNRESPONSIVE` を **lock-free な観測系**で読めるようにする（例: `request-status`/`job-status`/`doctor` の応答に `main_thread_responsive`/`unresponsive_since` を載せる＝受信スレッドが busy フラグ同様に読む・メインを待たない）。これがエージェントの「固まっている」可視化。
- **D. テスト/smoke**: L1（last_pump_ts 未更新 → watchdog が unresponsive 判定）+ GUI スパイク（重量 op 中に実際に unresponsive を観測）+ background smoke は timer 非発火のため近似（手動で last_pump_ts を古くして判定経路を裏付け）。

## 4.5 T10.2/T10.3 キックオフ判断（着手時にユーザー確認・推奨を併記）
- **R-A. 何を BUSY_RENDERING で拒否するか** → 推奨: **mutating または heavy のみ拒否**。read-only（scene-info/list-objects/object-info/capture）と lock-free（request-status/job-status/job-wait）は通す（レンダ中でも観測できる）。
- **R-B. BUSY_RENDERING の CLI 終了コード** → 推奨: **retryable として exit 2（TIMEOUT_PENDING 同様の「後で再試行」）** or 専用 exit。spec §終了コードに合わせる（要確認）。
- **R-C. watchdog は通知のみか / job をどう扱うか** → 推奨: **通知のみ（kill しない）**。spec『watchdog 通知＋非同期 job で観測性を確保』に忠実。unresponsive を観測系に載せるだけ。
- **R-D. unresponsive の閾値** → 推奨: `DISPATCH_TIMEOUT`(30s) 以上の余裕（例 45–60s）で誤検知回避。スパイクで実測して決める。
- **R-E. サブ PR 分割** → 推奨: **T10.2 を1 PR、T10.3 を1 PR**（各 独立3視点セルフレビュー）。順序は T10.2 → T10.3（render busy が先・watchdog は最後の仕上げ）。

## 5. 必ず守る規約（HANDOFF §8 / §6e）
- bli-core 純Python・依存ゼロ。生 `bpy.ops` は gateway のみ。検証は bpy 到達前。非対応/能力欠如は INTERNAL にしない。
- main 直接禁止・日本語コミット + prefix・PR 経由（マージはユーザー判断）。Codex 上限時は **独立3視点セルフレビュー**（設計 / 敵対的 correctness / 仕様・テスト）。
- 実機 smoke は 5.0.1 / 4.4.3 両版。GUI 必須（render handler / watchdog timer）は GUI スパイク（`blender.exe --python`）。

## 6. 参照
- **土台**: M4（HANDOFF §6b・`settle`/`RUNNING`/`request-status`/`RequestRegistry`・`DISPATCH_TIMEOUT < CLIENT_READ_TIMEOUT`）。`dispatcher.py`（submit/pump/settle/TimeoutPending）・`server.py`（session_lock/`LOCK_FREE_METHODS`/_handle_rpc/settle）・`request_registry.py`（begin/complete/lookup・PENDING/RUNNING/DONE/FAILED）。
- **spec**: §7 重量処理（line 332–338）/ §エラー（`BUSY_RENDERING`/`MAIN_THREAD_UNRESPONSIVE`/`TIMEOUT`）/ §終了コード（2=TIMEOUT_PENDING）。**plan**: §M10。
- **繰越元**: M9 の heavy 候補（boolean/decimate/import/print-check/repair）の job 化はここで回収。output_ref GC（M5 繰越・24h/200件/200MiB）も M10 で対応可。
