# bli (Blender CLI) — 引き継ぎ資料 (HANDOFF)

最終更新: 2026-06-20 / 状態: **PR #1–#27 マージ済み（origin/main）。M0–M9 完了。M10 非同期job 進行中＝T10.1 job 化（#27）完了・T10.2 render busy（#28・マージ待ち）完了。残り T10.3 watchdog（着手書 `.handoff/NEXT-M10.md`）。M9 確定要約は §6i・M10 T10.1/T10.2 は §6j・GT research §E9–§E12。**次は T10.3（heartbeat watchdog・GUI スパイク必須）**。俯瞰 `.handoff/ROADMAP.md`**。

> 新規セッションはこの1枚を読めば再開できる。詳細は `specs/blender-cli-core/` を参照。
> **全体俯瞰は `.handoff/ROADMAP.md`。M9 は完了。次の作業（M10）の着手書は未作成（M10 キックオフ時に `.handoff/NEXT-M10.md` を作る）**（このファイルは全体史 + 規約・§6h=実地FB確定要約・§6i=M9 確定要約）。

---

## 0. このプロダクトは何か
- AIエージェントが **CLI 経由で Blender を自律操作**するツール。名称 `bli`（Blender CLI）。
- 背景: ユーザはモデリング初心者。原点変更/直立補正/3Dプリンタ対応などをAIに委譲したい。
- 参照: `hatayama/unity-cli-loop`（uLoop）の CLIファースト思想の Blender 版。MCP のトークン非効率を CLI で解消。

## 1. 信頼できる情報源（spec駆動の成果物）
`specs/blender-cli-core/` に一式:
- `spec.md` — 機能仕様（確定判断 D1–D14 / 付録に判断ログ）
- `research.md` — 技術調査 + **付録A–D = M0.5 実機検証の確定値**（最重要）
- `data-model.md` — エンティティ（Command/プロトコル/Error/Registry/Config/Capability）
- `contracts/` — `protocol.schema.json`・`methods.md`（RPCメソッドカタログ）
- `plan.md` — 実装計画・ロードマップ **M0–M14**・タスク分割
- `tasks.md` + `tasks/01..18` — チケット台帳（**M0–M4 は ✅**。M3/M4 セクションと PR #1 レビュー追補も tasks.md に追記済み）

## 2. 環境（このマシン）
- OS: Windows 11 / シェル: Git Bash（Bashツール）。日本語標準出力は `PYTHONUTF8=1` を付ける。
- Python(dev/CLI): **3.10.6**（system）。uv: **0.7.3**。
- Blender: **5.0.1**（Python 3.11.13・主軸）/ **4.4.3**（ベストエフォート）。
  - `C:\Program Files\Blender Foundation\Blender 5.0\blender.exe`
  - `C:\Program Files\Blender Foundation\Blender 4.4\blender.exe`
- git: **PR #1（feature/m0-bootstrap → main）マージ済み**。`origin/main` に M0–M4。
  - **次の作業は `main` を pull してから新しい feature ブランチを切る**（例 `feature/m5-info`）。
  - ルール: main 直接コミット禁止 / 日本語コミット + prefix（feat/fix/docs/chore…）/ PR 経由でマージ。
  - レビューは Codex（PR コメント `@codex review`）。指摘対応→push→再依頼のループは前回 PR で実績あり。

## 3. 確定判断（D1–D14 要点）
- D1 接続=常駐Blender(GUI)+アドオンTCPソケット ← Python/Typer製CLI
- D2 Blender 5.0主軸 / 4.4ベストエフォート（**番号分岐禁止・能力検出で吸収**）
- D3 ハイブリッド（構造化主軸 + exec逃げ道）/ D5 **exec-python 既定 off**
- D6 同時接続 fail-fast（SESSION_BUSY）/ D7 重量ガードなし（watchdog+非同期job）
- D11 I/O=stl/obj/gltf/3mf/fbx / D12 発見=Claude Code Skill同梱+`help --json`
- D13 編集=オブジェクト+主要モディファイア+メッシュ編集 / D14 設定=ハイブリッド配置
- セキュリティ: 127.0.0.1固定・トークン認証・監査ログ・プロセス内sandboxは提供しない

## 4. M0.5 実機グラウンドトゥルース（research.md 付録に詳細）
- **operator 実在は `get_rna_type()` 成功で判定**（`hasattr` は旧名 stub を誤検出。重要）。
- STL=`wm.stl_export`/`wm.stl_import`、OBJ=`wm.obj_export`/`wm.obj_import`（4.4/5.0両対応・旧名は両方stub）。
- glTF=`export_scene.gltf`、FBX export=`export_scene.fbx`。
- **FBX import の唯一の版差**: `wm.fbx_import`(5.0)→`import_scene.fbx`(両対応)。
- **3MF・print3d は標準で実体なし** → 3MFはSTLフォールバック。print3dの実モジュールid特定は **M8 に繰越**（`object_print3d_utils`/`print3d_toolbox` の enable は両版で False）。
- `object.origin_set` props = `type`,`center`。`transform_apply` に `isolate_users`（4.4にも存在）。
- **background でも** `origin_set.poll()=True`、`temp_override(active_object, selected_objects, object)` で origin_set/transform_apply が `{'FINISHED'}`。直接行列フォールバックも動作。`ed.undo_push(message=...)` OK。
- **ディスパッチ安定性**: 別スレッド→queue→メインpump→`Event.wait(timeout)` が 5.0/4.4 とも N=500 で timeouts=0/errors=0（**STABLE**）。`time.sleep`フォールバックは不要。

## 5. 進捗
| マイルストーン | 状態 | 検証 |
|---|---|---|
| M0 基盤（uv workspace 3パッケージ・ruff/pyright・AST guard・CI枠） | ✅ | ruff緑 |
| M0.5 実機スパイク（能力ダンプ・dispatch安定・op_spike） | ✅ | 5.0/4.4実機 |
| M1 コア bli-core（commands/schema/errors/protocol/runtime/types） | ✅ | L1テスト |
| M2 通信層（server/auth/session/registry/shutdown/client/CLI ping） | ✅ | L3 E2E 38件 + Blender5.0実機スモークOK |
| **M3 アドオン実行基盤**（ops/gateway/dispatcher結線・CLI 3コマンド） | ✅ | pytest 45件 + Blender5.0/4.4実機 smoke_ops OK |
| **M4 CLI骨格 & 診断コマンド**（Pydanticラッパ/help/list-commands/request-status/--id） | ✅ | pytest 79件 + parity緑 + 実機 request-status OK |
| **M5 情報取得**（list-objects / object-info bbox / scene-info の output_ref 退避） | ✅ main（PR #2） | pytest 95 + 5.0/4.4 実機 smoke OK |
| **M6 汎用編集**（select/transform/apply-transform・duplicate/delete・material・modifier） | ✅ main（PR #6 で M6 完了） | pytest 151 + 5.0/4.4 実機 smoke OK |
| **M7 メッシュ編集**（mesh --op: bmesh一次 + heavy modifier 経由） | ✅ main（PR #9 で T7.1–7.3 完了＝**M7 完了**） | pytest 184 + 5.0/4.4 実機 smoke OK |
| **M8 3シナリオ中核価値**（set-origin / straighten / print-*）+ 実地フィードバック対応 | ✅ **完了**（PR #10–#18, #20）: T8.1–T8.5 + 実地FB PR-1〜5 | pytest + 5.0/4.4 実機 smoke OK |
| **M9** ファイルI/O（export/import/save/open） | ✅ **完了**（§6i）: T9.1 export（#21）/ T9.2 import（#22）/ T9.3 save（#23）/ T9.4 open（#25） | pytest 303 + 5.0/4.4 実機 smoke OK |
| **M10** 非同期job & フリーズ対策（job 化 / render busy / watchdog） | 🔶 **進行中**（§6j）: T10.1 job 化 ✅（#27）/ T10.2 render busy ✅（#28・マージ待ち）/ **残り T10.3 watchdog** | pytest 331 + 5.0/4.4 実機 smoke OK |
| M11–M14 | 未着手 | — |

**状態（T10.2＝PR #28）: `uv run pytest` = 331 passed / `ruff check` = 緑 / `ruff format --check` = 緑 / AST guard = OK / pyright（公式 = bli-core+bli-cli）= 0 errors（bli-addon は config で strict 対象外＝既存 `gateway.py` materials / `ops.py` name / `__init__.py` の ACCEPTED センチネル型は明示パス時のみ・実行時安全）。Blender 5.0.1/4.4.3 両版 background smoke OPS SMOKE OK（busy 拒否 + @persistent 生存）。**

> PR #1 の Codex レビュー対応で M4 を追補（§6b 参照）: ①request-status のロック迂回（限定セッション）②タイムアウト後の registry 後追い更新（settle）③発見系を implemented 済みに限定 ④サーバ/クライアントのタイムアウト整合（DISPATCH_TIMEOUT < CLIENT_READ_TIMEOUT）⑤TIMEOUT 時に request id を提示。

## 6. M3 完了（アドオン実行基盤）✅
### 実装済みファイル
- `dispatcher.py` — `Dispatcher`（submit/pump/install_timer/remove_timer, TimeoutPending）。bpy依存は install_timer 内のみ。
- `capability.py` — `CapabilityRegistry`/`operator_real`（get_rna_type判定）/RESOLVERS表（M0.5確定）。
- `gateway.py` — `run_operator`/`push_undo`/`resolve_targets`/`require_single`/`object_summary`/`scene_summary` に加え **M3追加**: `origin_set`(operator経由) / `set_origin_world`(直接行列) / `make_single_user_mesh` / `mesh_user_count` / `current_mode` / `object_fingerprint`。**bpy.ops は gateway.py のみ**（AST guard 許可）。
- `ops.py`（新規）— ドメインハンドラ `scene-info`/`object-info`/`set-origin` + `dispatch(method,params,info)` ルータ（bpy系→ハンドラ / その他→`handlers.dispatch`）。gateway は**遅延 import**（pytest で bpy 無しでも検証パスへ到達可能）。
- `definitions.py` — `object-info` 追加（`scene-info`/`set-origin` は既存）。
- `__init__.py` — `register()` 結線: `Dispatcher()`→`install_timer()`→`server.start(handler=executor)`。executor=`submit(ops.dispatch)`。`unregister()` で `server.stop()`+`remove_timer()`。
- `bli/main.py` — CLI サブコマンド `scene-info`/`object-info`/`set-origin`。`_rpc()` ヘルパで終了コード写像（USER_INPUT/INVALID_PARAMS→4, business→1, 接続→3）。
- テスト: `tests/test_ops_dispatch.py` 7件（ルーティング + param検証, bpy不要）。
- スモーク: `spikes/smoke_ops.py`（メインスレッド手動pump + 別スレッドclient）。

### M3 で確定した設計（research.md 付録Bに準拠・実機確認済み）
- set-origin: `to=geometry`→`gateway.origin_set(obj, origin_type="ORIGIN_GEOMETRY", center="MEDIAN"|"BOUNDS")`。`to=cursor`→`ORIGIN_CURSOR`。`to=world`→`gateway.set_origin_world`（`diff_local = matrix.to_3x3().inverted() @ (new_origin - translation)` で回転/スケールも整合）後 `push_undo`。
- 共有mesh（`mesh_user_count>=2`）は `make_single_user` 無しなら `E_PRECONDITION` で拒否。許可時は `obj.data = obj.data.copy()` で単一ユーザ化。
- `required_mode` を実行直前に検証、不一致は自動遷移せず `E_MODE_MISMATCH`。
- サーバ側でも `bli_core.schema.validate_from_dict` で params 検証（INVALID_PARAMS）。**検証は bpy import より前**＝不正入力は bpy 無しでも弾ける（テスト容易）。
- **golden 確認**: world(1,0,0)→geometry median の往復で原点が (1,0,0)→(0,0,0)、寸法は不変（見た目固定）。5.0/4.4 で fingerprint 一致。

## 6b. M4 完了（CLI骨格 & 診断コマンド）✅
- `bli/models.py`（新規）— bli-core Command 定義から **Pydanticモデルを動的生成**（`validate_params`/`model_for`）。CLI 送信前のローカル検証に使用。bli-core は純Python のまま（Pydantic は CLI 側のみ）。
- **parity テスト**（`tests/test_models_parity.py`）— Pydantic `model_json_schema` と bli-core `to_json_schema` の一致を全コマンドで検証 = SSOT ドリフト検出。
- `bli/main.py` 追加コマンド: `help [--command] [--json]` / `list-commands [--json]`（**SSOTから生成・schema_hash 同梱・ローカル完結**=addon不要）/ `request-status --id`。set-origin に `--id`（冪等リトライ）。`_rpc` は送信前に `models.validate_params` を呼ぶ（不正入力は接続前に exit 4）。
- `request-status` サーバ側: `server._handle_rpc` で **begin/メイン直列を経由せず** `registry.lookup(id)` を直接返す（メタ問い合わせ）。`request_registry.lookup()` 追加。`{known, state, result}` を返す。
- テスト: parity 6件 + `test_cli_help.py` 10件 + request-status E2E + dispatcher 4件。実機 5.0.1 で smoke_ops に request-status 検証を追加（DONE / unknown=False）。
- **繰越**: `job-status`/`job-wait`→M10（非同期job依存）、`--dry-run`→後続。

### M4 追補（PR #1 Codex レビュー対応）
- **request-status のロック迂回**: 認証後は常に hello-ok を返し、ロック未取得は「限定セッション」（lock-free=request-status のみ許可、他は SESSION_BUSY を RPC エラーで返す）。`LOCK_FREE_METHODS` で管理。→ 実行中でも別接続から決着確認が可能。
- **タイムアウト後の後追い更新（settle）**: `Dispatcher.submit(fn, settle=...)` を追加。ジョブ完了時にメインスレッドで settle が registry を確定（resp構築+complete）。受信スレッドが TimeoutPending しても、ジョブ完走時に settle が DONE/FAILED へ更新する。サーバは TimeoutPending を `TIMEOUT`（retryable, exit 2）として返し、registry は RUNNING のまま残す（FAILED にしない）。ハンドラ契約は `(method, params, info, settle)` に変更。同期既定は `_sync_handler`。
- **発見系の implemented フィルタ**: `Command.implemented`(bool) を追加。`transform`(M6)/`exec-python`(M11) は `implemented=False`。`list-commands`/`help` は既定で実装済みのみ表示（`--all` で全件、`help --command` は未実装でも introspection 可）。schema_hash に implemented を含める。
- **タイムアウト整合**: `bli_core.runtime` に `DISPATCH_TIMEOUT=30`（サーバ watchdog）/ `CLIENT_READ_TIMEOUT=40`（クライアント読取猶予）を追加。不変条件 `CLIENT_READ_TIMEOUT > DISPATCH_TIMEOUT`。サーバが先に TIMEOUT を返すのでクライアントは CONNECTION ではなく retryable TIMEOUT(exit2) を受け取れる。
- **request id 提示**: `_rpc` が request id を確定（`--id` 省略時も生成）。成功 payload と全エラー出力（特に TIMEOUT）に `request_id` を含め、`request-status --id <id>` で後追い可能に。
- **TTL purge は終端のみ**: `RequestRegistry._purge` は DONE/FAILED のみ掃除し、実行中（PENDING/RUNNING）は settle まで保持する。`lookup`/`begin` 双方で適用。長時間ジョブの id が消えて再送が二重実行される（IN_PROGRESS 冪等性が壊れる）のを防ぐ。
- **ping もタイムアウト写像を共通化**: `_call_or_exit` を抽出し `_rpc`/`ping` 双方で使用。ping も実機では Dispatcher 経由のため TIMEOUT→exit2 + id 提示に統一（doctor は診断目的でエラーを握るため対象外）。

## 6c. M5 完了（情報取得）✅ main マージ済み（PR #2）
- **判断3点（着手時確定）**: ①`list-objects --type` = freeform STR（大小無視照合・版差/将来型に強い）②output_ref の GC は M10 へ繰越（M5 は退避+検証を優先）③CLI 既定は参照のみ・`--fetch` で展開。
- **T5.1 出力退避** — `bli_core/output_ref.py`（新規・純Python・依存ゼロ）: `INLINE_THRESHOLD=64KiB` / `maybe_offload(schema, data, outputs_dir)→(inline, descriptor)` / `load_verified(ref)→data` / `build_descriptor`。退避は temp→`os.replace` でアトミック。**退避 id はコンテンツアドレス（sha256 先頭16桁）**＝request id を ops 層へ配線せず M4 のハンドラ契約 `(method, params, info, settle)` を再変更しない設計。`_safe_output_path` で outputs 配下逸脱を拒否、改竄は `StaleOutputError`。`runtime.outputs_dir()`=`BLI_STATE_DIR/outputs`（git 非管理）。
  - `ops._ok` の `output_ref` を **dict 化**、`_ok_offload` で `scene-info` を閾値超なら退避（inline 時は従来どおり `data=<...>`/`output_ref=None`）。
  - CLI `_rpc` に `fetch` を追加。既定は **参照のみ**（`output_ref` 素通し・人間向けは退避サマリ表示）。`scene-info --fetch` 時のみ `load_verified` で sha256 検証→`data` 展開。不一致は **`STALE_OUTPUT`(exit1)**。
- **T5.2 情報拡充** — `object-info`: `gateway.world_bbox`（`matrix_world @ bound_box` の world AABB min/max/size）を `object_summary` に追加。**fingerprint が変わる**（object_summary 内包・5.0/4.4 で一致 `f7d31df4ef48be6c`）。`list-objects`（新規）: definitions 登録（type/regex 任意・required_mode=OBJECT）/ `gateway.list_objects`（name/type/location の軽量サマリ・不正 regex は USER_INPUT）/ `ops._list_objects` + `_BPY_HANDLERS` 登録 / CLI サブコマンド。
- **テスト**: `test_output_ref.py`（L1: 閾値/往復/改竄/配下逸脱/id 決定性 9件）、`test_ops_dispatch.py`（list-objects param 検証 +2）、`test_cli_help.py`（list-objects 発見 +1）、`test_cli_scene_info.py`（退避/--fetch/STALE_OUTPUT/人間向け 4件）。`smoke_ops.py` に bbox golden・list-objects・退避往復を追加。**pytest=95 passed / ruff・format・AST guard 緑 / Blender 5.0.1・4.4.3 実機 OPS SMOKE OK**。
- **繰越**: output_ref の GC（24h/200件/200MiB）→M10。`bli/main.py:83` の既存 pyright narrowing（M5 以前から・実行時安全）→別途。

## 6d. M6 汎用編集（進行中）
M6 は7コマンドと大きいため **サブPRに分割**して進める（ユーザー判断で確定）。順序: T6.1 → T6.2 → T6.3 → T6.4。
- **判断（着手時確定）**: ①M6 はサブPR分割 ②`transform --mode delta` の scale は **乗算**（loc/rot は加算）③`select` は実装（select_set + active 設定・他コマンドは従来どおり --targets で独立解決）。

### T6.1 完了 ✅ main マージ済み（PR #3）— select / transform / apply-transform
- `definitions.py`: `transform` を `implemented=True` 化。`select`（targets/type/active）・`apply-transform`（targets + location/rotation/scale の **presence-sensitive BOOL**・全省略=全適用・`make_single_user`）を追加。
- `gateway.py`: `transform_object`（直接プロパティ・op不要。**location は world 空間**=`matrix_world.translation` を in-place 更新 / rotation は度→ラジアンで **rotation_mode の native 表現**へ反映 = QUATERNION/AXIS_ANGLE 対応・delta は loc/rot 加算 ただし scale 乗算・quaternion は合成 / location を最後に適用）。`apply_transform`（`transform_apply` を `_override_for` の `selected_editable_objects=[obj]` で **--targets のみに限定**・非mesh型は事前 `E_PRECONDITION`）。`select_objects`（`select_set`+active 直接・op不要・**アクティブ view layer 内へ限定**・active を変更前に検証・`selected` は sorted）。`_rotation_euler_deg`（報告も mode 非依存）。
- `ops.py`: `_select`/`_transform`/`_apply_transform` + `_BPY_HANDLERS`。**再利用ヘルパ**: `_require_input(cond, symptom, remediation)`（bpy 到達前の USER_INPUT 検証）/ `_guard_shared_mesh(gateway, obj, params)`（users>=2 は `--make-single-user` 無しで `E_PRECONDITION`・set-origin と共有）。`bli/main.py`: 3サブコマンド（`--id`）+ `_parse_vec3`（"x,y,z"→float×3・nan/inf/3要素を exit4）。**targets は全コマンド `--targets` オプション**（契約準拠）。
- **テスト/検証**: pytest=107。smoke_ops に transform(set/delta/複合/非Euler/親付き world)・apply-transform(bake/非mesh/共有mesh ガード/--targets限定)・select(active検証/不正regex/並び決定性) の golden。5.0.1/4.4.3 実機 OPS SMOKE OK（Cube fp 不変 `f7d31df4ef48be6c`）。

### T6.1 レビュー対応（Codex 10件 + セルフレビュー 6件）
PR #3 で **Codex P2×9 + P1×1** を解消（bbox 非ジオメトリ None / apply false-vs-omit / select 検証順 / select fingerprint / apply schema default / select view-layer / 不正regex→USER_INPUT / 非Euler回転 / `--targets` / location world 化 / **P1 apply の selected_editable_objects 限定**）。その後 Codex が利用上限に達したため、**サブエージェント2体（設計レビュー + 敵対的 correctness 監査）でセルフレビュー**し追加6件を解消（**P1 apply の共有mesh 黙示単一化を廃止し set-origin と同じ `--make-single-user` ガードへ統一** / 複合 transform の並進ずれ / 非mesh apply のエラー品質 / nan-inf 弾き / transform 全省略弾き / select 並び決定化）。**この過程で確立した再利用パターンは T6.2–6.4 でも踏襲すること**（下記 §6e）。

### T6.2 完了 ✅ main マージ済み（PR #4）— duplicate / delete
- **判断（キックオフ確定）**: ①delete は **即実行 + backup 常時返却**（`--confirm` なし＝他コマンドと対称・ユーザー選択）②backup 実体は削除前 `object_summary` のみ（`.blend` 退避は M9 繰越）③対象は単一（`require_single`）④duplicate offset は world 空間・`(i+1)*offset` 累積。
- `definitions.py`: `duplicate`（targets/linked[BOOL default]/count[INT default=1・1〜1000]/offset[VEC3]）・`delete`（targets のみ）を追加。`methods.md` の `delete --backup?` を実装に合わせ更新（summary 常時返却・.blend は M9）。
- `gateway.py`: `duplicate_object`（`obj.copy()`＋非linkedは`data.copy()`／全 collection に link・0件は `scene.collection` フォールバック／offset 基準は **元 obj の評価済み matrix_world**＝親付き複製でも world 正確）・`delete_object`（`bpy.data.objects.remove(do_unlink=True)`）・`names_fingerprint`。**生 bpy.ops 不要・bpy.data 直接**。
- `ops.py`: `_duplicate`（count を `runtime.MAX_DUPLICATE_COUNT` で範囲検証）・`_delete`（**削除前に** name/summary/fingerprint を取得→remove）。共有 mesh は delete では object のみ除去のためガード不要。
- `runtime.py`: `MAX_DUPLICATE_COUNT=1000`（CLI/ops 共有）。`bli/main.py`: `duplicate`（`--offset`=`_parse_vec3`・上限を送信前に弾く）・`delete` サブコマンド。
- **テスト/検証**: pytest=121。smoke に duplicate（count=2 offset 累積・linked `mesh_users 1→2`・親付き Child world offset）・delete（backup・名前集合厳密確認・存在しない名）の golden。5.0.1/4.4.3 実機 OPS SMOKE OK（Cube fp 不変）。

### T6.2 レビュー対応（サブエージェント・セルフレビュー 2体）
Codex 上限の代替として **設計レビュー（software-design-reviewer）+ 敵対的 correctness 監査（general-purpose）** を並列実行し、収束指摘を `ccdfa75` で解消。
- **P1（両者一致）**: VEC3/FLOAT の `nan/inf` をサーバ側で検証していなかった（`json.loads` が `Infinity/NaN` を通し `_check_type` も有限性を見ない＝CLI 非経由 RPC で `matrix_world` 破壊可能）→ **`schema._check_type` に `math.isfinite` を追加**（SSOT を単一防御線化・transform/set-origin も一括保護）。
- **P2**: 親付き複製の offset 基準を評価済み matrix_world へ / 0-collection フォールバック / count 上限を runtime 集約し CLI も送信前に弾く / methods.md ドリフト解消 / smoke に linked・親付きカバレッジ追加。

### T6.3 完了 ✅ main マージ済み（PR #5）— material（assign / create / list）+ VEC4 基盤
- **判断（キックオフ確定）**: ①create は **create-and-assign**（`--targets` 必須・作成して付与）②スロットは **active 置換・空なら追加** ③assign は既存マテリアルのみ（無ければ E_TARGET_NOT_FOUND）④color は create 専用⑤`--action` ENUM。
- **VEC4 基盤**（bli-core 純Python）: `ParamType.VEC4` / `schema._JSON_TYPE`+`_check_type`（4要素・有限性）/ `models._PY_TYPE`=`tuple[float×4]` / CLI `_parse_vec3`→`_parse_vec(name, raw, n)` 一般化（VEC3/VEC4 共通・nan/inf 拒否）。
- `gateway.py`: `create_material`（use_nodes+Principled "Base Color"+diffuse_color）/ `assign_material`（書き込みは `material_slots[idx].material` 経由で **slot.link 尊重**）/ `list_object_materials`（slot/name/link/base_color）/ `require_material`（無ければ E_TARGET_NOT_FOUND）/ `find_material` / `require_material_support`（非mesh は E_PRECONDITION）/ `material_write_touches_mesh_data` + `_target_slot_index`（ガード判定と実書き込みの slot を一致）/ `material_fingerprint`。**生 bpy.ops 不要・bpy.data 直接**。
- `ops._material`: 条件付き必須（action 別 targets/name・color は create 専用）を bpy 前に検証→assign は **解決を guard の前**（失敗時に mesh 分離しない）→ **書き込みが DATA slot に触れるときだけ** `_guard_shared_mesh`（OBJECT リンク slot はガード不要）。`bli/main.py`: `material` サブコマンド（`--action`/`--make-single-user`/`--color`=`_parse_vec(,4)`）。
- **テスト/検証**: pytest=132。Codex P2×4 + 設計レビュー P2 群に対応（共有 mesh 兄弟波及防止・slot.link 尊重・失敗時非破壊・OBJECT リンク・契約ドリフト・slot 書き込み先集約）。smoke に material golden 多数（共有ガード・OBJECT リンク・fingerprint 決定性 `bd7f516481257d9a` 5.0/4.4 同値）。
- **着手前スパイク**で material ノード API（"Base Color" 入力安定）を 5.0.1/4.4.3 確認。

### T6.4 完了 ✅（PR #6・マージ待ち）— modifier（add / remove / list / apply）【M6 完了】
- **判断（キックオフ確定）**: ①`--action` ENUM ②型別 params は最小1つ（schema 任意・ops で action/type 別検証）③add は type 必須・remove/apply は name 必須・BOOLEAN add は with 必須 ④apply のみ mesh 焼き込み→共有ガード（add/remove/list は不要）。
- `gateway.py`: `add_modifier`/`remove_modifier`/`list_modifiers`/`require_modifier`/`apply_modifier`/`require_modifier_support`（非対応型は E_PRECONDITION）/`modifiers_fingerprint`。modifier は **オブジェクト単位**（`obj.modifiers` 直接・生 bpy.ops 不要）。**apply のみ** `bpy.ops.object.modifier_apply` を `run_operator` 経由で mesh 焼き込み（AST guard 緑）。型別最小プロパティ（MIRROR=use_axis/SUBSURF=levels/SOLIDIFY=thickness/DECIMATE=ratio/BOOLEAN=operation+object）。M0.5 スパイクで 5.0.1/4.4.3 確認。
- `ops._modifier`: 条件付き必須を bpy 前に検証（add は type 必須・型別paramは当該typeのみ・BOOLEAN は with 必須・levels 0..6/ratio 0..1 範囲・remove/apply は name 必須）。`require_modifier_support` で非mesh を E_PRECONDITION（INTERNAL 回避）。BOOLEAN operand は require_single + 型/自己参照検証。apply は無効名を**共有ガードの前**に弾き、mesh 焼き込みなので `_guard_shared_mesh`。`_ALL_MODIFIER_TYPE_PARAMS` は `_MODIFIER_TYPE_PARAMS` から導出。fingerprint: add/remove/list は `modifiers_fingerprint`（param込み）/ apply は `object_fingerprint`（mesh 変化）。
- **テスト/検証**: pytest=151。独立3視点のサブエージェント・セルフレビュー（P1×4 解消: 非mesh INTERNAL誤分類 / boolean operand未検証 / levels上限なし / smoke apply偽陽性）。smoke に modifier golden（add5種→list→remove→apply の頂点 8→16 焼き込み / apply共有ガード / 非mesh・boolean operand ガード）。5.0.1/4.4.3 実機 OPS SMOKE OK。
- **繰越**: ハンドラ分割 / type-param のテーブル化 / apply 応答キー命名（P2/P3・後続改善）。

### M6 完了後 → **M7（メッシュ編集 bmesh一次）= `.handoff/NEXT-M7.md`**

## 6f. M7 メッシュ編集（完了・bmesh 一次 + heavy は modifier 経由）
M7 も7操作と大きいため **サブPR分割**（T7.1 stable → T7.2 experimental → T7.3 heavy）。material/modifier と同じ **単一 `mesh` コマンド + `--op` ENUM**（キックオフでユーザー確定）。**T7.3 完了で M7 完了**。

### T7.1 完了 🔶（実装・セルフレビュー済み・PR 待ち）— mesh recalc-normals / merge-by-distance
- **判断（キックオフ確定）**: ①単一 `mesh` コマンド + `--op`（material/modifier と一貫・SSOT 1コマンド）②stability はコマンド単位 → `mesh` 全体を **experimental**（experimental op を含むため。recalc/merge 自体は安定だが同一コマンド内）③スコープは T7.1（recalc-normals/merge-by-distance）のみ ④bmesh-on-data（OBJECT モードのまま）/ 当面 Mode=OBJECT ⑤破壊的 mesh 編集は全 op で共有ガード。
- **着手前スパイク**（`spikes/bmesh_spike.py`）で bmesh-on-data API を 5.0.1/4.4.3 確認（research.md §E）: `from_mesh`→`bmesh.ops`→`to_mesh`→`free`→`data.update()` が OBJECT モードで動作・context 非依存。`recalc_face_normals`/`reverse_faces`/`remove_doubles`（戻り値 None・merged は頂点 before/after 差）。
- `bmesh_ops.py`（**新規・gateway 同様の bpy 接点層**・`bmesh.ops` のみで AST guard 対象外）: `recalc_normals`（flipped 統計＝この操作で向きが変わった面数・inside で reverse_faces）/ `merge_by_distance`。`try/finally` で `bm.free()` 保証。`gateway` の `push_undo`/`mesh_stats` を利用。
- `gateway.py`: `require_mesh`（非mesh は E_PRECONDITION）/ `mesh_stats`（verts/edges/polys）/ `mesh_fingerprint`（**法線込み**＝頂点数不変の recalc も drift 検出・符号付きゼロは `+0.0` で正規化し版間同値を保証）。
- `ops._mesh`: op 別の条件付き検証（無効 param 排除・distance>=0）を bpy 前に → `require_mesh` → `_guard_shared_mesh` → bmesh ヘルパ → `mesh_fingerprint`。`_ALL_MESH_OP_PARAMS` は `_MESH_OP_PARAMS` から導出（modifier と同流儀）。op 専用 param（inside/distance）は **schema default なし**（別 op への誤送信を弾けるよう presence 維持）。
- **テスト/検証**: pytest=162。独立3視点セルフレビュー（Codex 上限の代替）で P2 群解消（**符号付きゼロ fingerprint** / spec.md ドリフト / make_single_user 退行ガード / flipped docstring / 明示 distance smoke）。smoke に mesh golden（recalc flipped=1→0→6・法線変化で fingerprint 変化・merge 9→8・明示 distance collapse・非mesh/共有ガード）。5.0.1/4.4.3 実機 OPS SMOKE OK（fingerprint 両版同値 `clean=8460160a4a4e6d7c`）。
- **繰越（T7.2 で検討）**: 結果スキーマの冗長（`faces`==`stats.polygons` / `after`==`stats.vertices`）を T7.2 で追加する extrude/bevel/inset（verts/edges/faces 同時変化）に合わせて `stats` 中心に統一するか（設計レビュー P2・現状は methods.md と整合し動作に支障なし）。

### T7.2 完了 🔶（実装・セルフレビュー済み・PR 待ち）— mesh extrude / bevel / inset
- **判断（着手時確定）**: ①全 geometry 対象（v1・`--faces` セレクタは Deferred）②op 別に必須（extrude=offset / bevel=width / inset=thickness）・segments 任意（既定1・1〜100 で暴走防止）③**extrude offset は world 空間**（matrix_world で world→local 変換・move/duplicate と一貫）/ bevel width・inset thickness はスカラ量のため mesh ローカル単位④inset は閉じた mesh の全 face で `inset_region` が no-op → **`inset_individual`**。
- **着手前スパイク**（`spikes/bmesh_spike_t72.py`）で 5.0.1/4.4.3 確認（research.md §E2）: extrude=`extrude_face_region`+`translate` / bevel=`bevel(geom=edges, affect=EDGES)` / inset=`inset_individual`。faceless mesh は no-op・no-crash。
- `bmesh_ops.py`: `extrude`（world offset を `matrix_world.to_3x3().inverted()` で local 変換）/ `bevel` / `inset` / `_stats_delta`。結果 `{<param>, delta, stats}`（`delta`=符号付き増減＝decimate/boolean でも一貫・**`added` から改名**）。
- `ops._mesh`: op 別の条件付き必須 + 範囲ガード（width/thickness>=0・segments 1〜100）を bpy 前に。`_MESH_OP_PARAMS` 拡張（offset/width/segments/thickness）・`_ALL_MESH_OP_PARAMS` 自動導出。
- **テスト/検証**: pytest=176。独立3視点セルフレビューで P1（extrude offset の world/local footgun → world 化）+ P2（added→delta 改名 / make_single_user 退行ガード拡張 / segments cross-op leak / nan/inf テスト）を解消。smoke に exact count golden（extrude 8→16v・bevel 24v・inset 32v）+ **world offset 検証**（scale=2 で world_max_z=3）+ op 別ガード。5.0.1/4.4.3 実機 OPS SMOKE OK（両版同値）。
- **繰越（T7.3 で検討）**: `_mesh` の op 別検証が `elif` 連鎖で伸びる → per-op validator テーブル化（設計 P3）。segments の上限は「segments のみ」で「input_edges×segments」は見ない（高ポリ入力で同期実行が長くなり得る・subsurf levels と同じリスクモデル・experimental では許容）。

### T7.3 完了 🔶（実装・セルフレビュー済み・PR 待ち）— mesh boolean / decimate【M7 完了】
- **判断（着手時確定・スパイクで確定）**: ①bmesh に boolean/decimate 相当が **無い**（`bmesh_spike_t73.py` で両版 `hasattr=False` 確認）→ いずれも **BOOLEAN/DECIMATE モディファイア add + `modifier_apply`** にフォールバック（bmesh-on-data ではない・生 bpy.ops は gateway のみ）。②既存 `add_modifier`+`apply_modifier` を再利用（`gateway._add_then_apply`・apply 失敗時は追加 modifier 撤去＝アトミック）。③boolean は `--operation`(UNION/DIFFERENCE/INTERSECT) + `--with`(相手 mesh) 必須・相手の world 位置は Blender が解決（手動変換不要・extrude と異なる）・相手は read-only。④decimate は `--ratio`(0..1・COLLAPSE) 必須。⑤共有 mesh は全 op でガード（多ユーザ mesh への modifier_apply は Blender が拒否＝必須・ratio=1.0 等 no-op でも焼き直すため）。
- **着手前スパイク**（`spikes/bmesh_spike_t73.py`）で 5.0.1/4.4.3 確認（research.md §E3）: decimate=DECIMATE COLLAPSE+ratio（ico subdiv=2 → 80f→40f・両版一致）/ boolean=BOOLEAN operation+operand（solver 既定 EXACT）。**boolean の world bbox は幾何的に決定的**（UNION x[-1,2] / DIFFERENCE x[-1,0] / INTERSECT x[0,1]）＝solver 非依存の頑健 golden。
- `gateway.py`: `mesh_boolean`/`mesh_decimate`/`_add_then_apply`（add+apply アトミック・撤去付き）/ `stats_delta`（gateway に集約し bmesh_ops から再利用＝DRY）。結果 `{<param>, delta, stats}`（boolean は `{operation, with_object, delta, stats}`・キーは入力 `with_object` と対称）。
- `ops._mesh`: `_MESH_OP_PARAMS` に boolean:{operation,with_object} / decimate:{ratio} 追加。boolean operand は `_resolve_boolean_operand`（mesh boolean と modifier BOOLEAN add の共有ヘルパ＝二重定義廃止）で **共有ガード前**に解決・自己参照/非mesh 検証。実行分岐の終端 else は到達不能ガード（新 op 分岐漏れ検出）。
- **テスト/検証**: pytest=184。独立3視点セルフレビュー（Codex 上限の代替）で P1（operand 検証二重化 → 共有ヘルパ抽出）+ P2/P3（add_modifier 再利用・add/apply アトミック化・`with`→`with_object` 改名・終端 else 防御・UNION/ratio=1.0/共有ガード/存在しない operand の smoke 追加・退化 mesh 注記）を解消。smoke に boolean UNION/INTERSECT/DIFFERENCE の world bbox golden（solver 非依存）+ decimate ico 80f→40f + ratio=1.0 no-op + 共有ガード。5.0.1/4.4.3 実機 OPS SMOKE OK（両版同値）。
- **繰越（M8 以降で検討）**: `_mesh` の op 別検証 `elif` 連鎖（7 op に到達）→ 8 op 目を足すなら per-op validator + executor のテーブル化（設計 P3・現状は `_ALL_MESH_OP_PARAMS` 導出 + cross-op leak ガードで追従漏れは弾けるため許容）。boolean/decimate の「対象に他 modifier がある場合は焼き込まれる」前提は v1 未保証（methods.md 注記済み）。

## 6g. M8 3シナリオ中核価値（進行中）
M8 はサブPR分割（NEXT-M8.md）。順序: T8.2 straighten → T8.3 print-setup → T8.4 print-check/repair → T8.5 print-export。**T8.1 set-origin は M3 実装済み**（S1 golden 緑＝新規実装不要）。

### T8.2 完了 🔶（実装・独立3視点セルフレビュー済み・PR 待ち）— straighten（直立補正・シナリオ2）
- **判断（キックオフ確定）**: ①対象は単一（`require_single`・set-origin と対称）②world-align の `--axis` 省略時は up に最も近い signed local 軸を自動選択（spec『最も近い主軸』）③`--bake-rotation` の mesh 焼き込みは共有 mesh で `--make-single-user` 必須（straighten に make_single_user param を追加・methods.md/spec 追記）。3シナリオは全 stable（DoD）。
- **着手前スパイク**（`spikes/straighten_spike.py`・research.md §E4）で 5.0.1/4.4.3 確認: mathutils `Matrix.LocRotScale`/`Vector.rotation_difference`/`matrix_world.decompose` と **numpy 1.26.4（`linalg.eigh`）が両版同梱**。**落とし穴**: background では rotation 直接設定後 `matrix_world` が stale → 読み取り前に `bpy.context.view_layer.update()` 必須（実装も冒頭/補正後に呼ぶ）。
- `gateway.py`: `straighten_object`（reset=回転 identity / world-align=signed local 軸を up へ最小回転・axis 省略時は最近軸自動 / pca=共分散→numpy eigh の最大分散軸を up へ・符号は原点→重心方向で一意化 / floor=up 方向最下点を接地）。`_rotation_to`（anti-parallel を固定軸 180° で決定化）/ `_min_up_projection`（floor と min_up 報告の単一窓口・DRY）/ `_apply_world_rotation`（decompose→LocRotScale で loc/scale 保持）/ `require_geometry`（floor 用）。**reset/world-align/pca は object 回転のみ・floor は平行移動のみで mesh 非破壊（共有 mesh でも安全）**。numpy は `_principal_axis` 内 lazy import に局所化。
- `ops._straighten`: `--axis` は world-align 専用（他 method は USER_INPUT）。pca=require_mesh / floor=require_geometry。`--bake-rotation` は **補正より前**に require_mesh + 共有ガード（失敗時に obj を回転させない）→ straighten_object → `apply_transform(rotation)` で焼き込み（apply 経路再利用）。fingerprint は **非 bake=object_fingerprint / bake=mesh_fingerprint**（§6e）。
- **テスト/検証**: pytest=195。独立3視点セルフレビュー（P1 無し）で P2 群解消（floor 最小射影の DRY 集約 / rotation_difference anti-parallel の決定化 / pca 中心対称符号の正準 tie-break / bake は mesh_fingerprint / up≠+Z の golden 追加）。smoke に world-align(explicit/auto/+Y)/reset/pca/floor(+Z/+Y)/bake(見た目不変)/共有ガード(bake のみ・非 bake は安全)/前提ガードの golden。5.0.1/4.4.3 実機 OPS SMOKE OK（両版同値）。
- **繰越（v1 未保証・methods.md 注記済み）**: 親付き対象 / 非一様・シアスケール下の整列精度（matrix_world 回転成分で近似）/ 中心対称 mesh の pca 符号（正準 tie-break で決定化済み）/ 複合 tilt の up 周り yaw 残留（最小回転のため向き不保証）。`straighten_object` の実行分岐と report 分岐の二重化は method 追加時にテーブル化検討（設計 P3）。

### T8.3 完了 🔶（実装・独立3視点セルフレビュー済み・PR 待ち）— print-setup（単位 mm/m・シナリオ3）
- **判断（キックオフ確定）**: print-setup は **表示単位のみ**（`unit_settings.system='METRIC'` + `length_unit`）を設定し geometry を再スケールしない＝**非破壊**（spike で dims 不変を実機確認）。実寸 export スケールは T8.5 で一本化（global_scale 一本化）。`--unit` ENUM(mm|m) 既定 mm / `--scene?` 省略時 active。
- **着手前スパイク**（`spikes/print_setup_spike.py`・research.md §E5）で 5.0.1/4.4.3 確認: unit_settings 既定 METRIC/scale_length=1.0/METERS。`system='METRIC'`+`length_unit='MILLIMETERS'|'METERS'` 直接代入は両版 OK（background 可）・**length_unit は表示専用で dimensions 不変**。
- `gateway.py`: `set_print_units`（system=METRIC + length_unit・`changed` 指標）/ `require_scene`（name=完全名 / 省略=active・無ければ E_TARGET_NOT_FOUND）/ `_unit_settings_dict`（scene_summary と共有＝SSOT）/ `unit_settings_fingerprint`。geometry 非破壊のため共有 mesh ガード不要。
- `ops._print_setup`: 表示単位のみ設定（ガード無し）。fingerprint=unit_settings ハッシュ。`CLI`: `print-setup --unit/--scene`。
- **テスト/検証**: pytest=201。独立3視点セルフレビュー（P1 無し）で P2/P3 解消（scene_summary の unit_settings を `_unit_settings_dict` に DRY 集約 / required_mode コメント / changed・非破壊・冪等・状態非汚染を3視点確認）。smoke に mm/m・dims 不変（非破壊）・冪等 changed・scene-info 反映・--scene 解決・存在しないシーンガードの golden。5.0.1/4.4.3 実機 OPS SMOKE OK（両版同値）。
- **繰越**: IMPERIAL 起点の changed=True 経路は golden 未追加（コードは無条件 system=METRIC で対応・P3）。print-export（T8.5）の global_scale はこの表示単位/`scale_length` から一本算出する設計。

### T8.4 完了 🔶（実装・独立3視点セルフレビュー済み・PR 待ち）— print-check / print-repair（シナリオ3）
- **判断（キックオフ確定・スパイクで確定）**: ①thin/intersect は print3d 依存 → 要求 かつ 未導入なら `CAPABILITY_UNAVAILABLE`（黙殺しない）。manifold/normals/degenerate は **bmesh 自前で常時 stable**。②`--save-to`（ファイル書き出し）は M9 へ繰越（大結果は output_ref 退避で対応）。③print-check は読み取り専用（mutates=False）・print-repair は破壊的（共有ガード）。④フラグは presence-sensitive（check カテゴリ省略=全 / repair 省略=全修復）。
- **着手前スパイク（最重要・M0.5 繰越を消化）**（`spikes/print3d_spike.py`・research §E6・両版同値）: **print3d は両版とも実体なし**（addon module 自体が無い・enable 全失敗・operator stub・`scene.print_3d` なし）。bmesh 自前: 非多様体=`not e.is_manifold` / 反転法線=`e.is_manifold and not e.is_contiguous` / 退化面=`f.calc_area()<1e-8`。repair: `recalc_face_normals`/`dissolve_degenerate`/`holes_fill`/`remove_doubles`/`delete` が機能。
- `bmesh_ops.py`: `mesh_check`（is_manifold/normals_consistent/is_printable 要約付き・read-only）/ `mesh_repair`（best-effort・before/after/fixed 差分・**完全修復は非保証**・wire→loose の順で削除）。
- `gateway.py`: `print3d_available`（enable 試行→operator_real・§E6 でこの環境は常に False・`default_set/persistent=False` で preferences を汚さない）。
- `ops.py`: `_print_check`（thin/intersect 要求 かつ print3d 不在で CAPABILITY_UNAVAILABLE(ENVIRONMENT)・カテゴリは1パス計算しサブセット報告・min_thickness は thin 専用・`_ok_offload` で大結果退避・fingerprint=mesh_fingerprint）/ `_print_repair`（破壊的→共有ガードを編集前に）。`CLI`: print-check（--fetch 対応・human はサブセット表示）/ print-repair。
- **テスト/検証**: pytest=212。独立3視点セルフレビュー（P1 無し）で P2/P3 解消（make-manifold の wire/loose 削除順 / 全省略=全修復の複合破損 golden 追加 / thin+manifold 混在も CAPABILITY / spec §10 S3 に v1 注記 / human サブセット表示）。smoke に clean/面欠け/反転/退化 の check・CAPABILITY_UNAVAILABLE・非mesh ガード・make-manifold/recalc/remove-degenerate/全修復 の repair・共有ガードの golden。5.0.1/4.4.3 同値。
- **繰越**: `--save-to`→M9。thin/intersect は print3d 導入時に配線（min_thickness は現状 dead param）。print3d/check/repair は heavy 候補（M10 で job 化）。holes_fill の非平面 n-gon・退化 eps 絶対値は v1 単純化（methods.md 注記）。

### T8.5 完了 ✅（PR #20・マージ待ち）— print-export（STL 出力・シナリオ3）【M8 実装完了】
- **判断（キックオフ確定・ユーザー確認）**: ①対象は単一（`require_single`・他 M8 と対称）②スケールは明示 `--scale`（既定 1.0）を `global_scale` 一本化・`use_scene_unit=False` 固定で `scale_length` を出力へ反映させない（1000倍ずれ防止・`scale_length` は検証用に報告）③STL は常に world 焼きのため spec 当初案の `--apply-transform` を廃止し実トグルの `--apply-modifiers`（既定 on）を公開④3mf は両版とも export operator が実体なし（§E8）→ `CAPABILITY_UNAVAILABLE`（ENVIRONMENT）+ STL hint（黙って差し替えない）。
- **着手前スパイク**（`spikes/print_export_spike.py`・研究 §E8・5.0.1/4.4.3 **完全同値**）: `wm.stl_export` の正確な引数集合（filepath/ascii_format/export_selected_objects/global_scale/use_scene_unit/apply_modifiers・両版同一）を確定＝研究 §[要実機検証] line 175 を消化。`global_scale=2`→寸法2倍（決定的）/ `use_scene_unit=True` は出力を歪める（False 固定の根拠）/ 3MF は `export_mesh.3mf` も `addon_utils.enable("io_mesh_3mf"…)` も全失敗。
- `gateway.py`: `export_stl`（対象だけ選択して `export_selected_objects=True`・world 焼き・選択は `_select_only`/`_restore_selection` で save→restore＝非破壊・`run_operator` 経由で AST guard 緑）/ `resolve_export_operator`（`CapabilityRegistry.resolve` へ委譲）。**`wm.stl_export` は永続選択フラグを見るため temp_override では絞れない**（§E8・docstring 明記）。
- `ops._print_export`: path 空/`scale<=0`（退化/反転）を bpy 前に弾く（USER_INPUT）→ 能力判定（対象非依存なので require_single より前）→ 3mf は CAPABILITY_UNAVAILABLE→ require_single/require_mesh → 出力先ディレクトリ不在は USER_INPUT → `export_stl` → ファイル統計（sha256/size/三角形数）。`OSError`→`E_OPERATOR`。fingerprint=出力ファイルの content-address（binary STL は決定的・capture と同流儀）。`_capability_unavailable` ヘルパで CAPABILITY 組み立てを集約。
- `bli/main.py`: `print-export` サブコマンド（`--targets`/`--target`・`--format`・`--path`・`--ascii`・`--scale`・`--apply-modifiers/--no-apply-modifiers`）。result `{name, path(絶対), size, sha256, triangles, format, ascii, global_scale, apply_modifiers, scale_length}`。
- **テスト/検証**: pytest=257。独立3視点セルフレビュー（Codex 上限の代替）で P2（`--scale<=0` 正値ガード追加 / `resolve_export_operator` を registry へ委譲 / 能力判定を対象解決より前）+ P3（`_select_only` の意図 docstring / `apply_modifiers=False` golden）を解消。smoke に world 焼き bbox（ExpCube world(5,0,0)→x∈[4,6]）/ scale=2（x∈[8,12]）/ ascii(solid) / 非破壊（object_fingerprint 不変）/ apply_modifiers(on=48・off=12) / 3mf=CAPABILITY / 非mesh=E_PRECONDITION / 不在dir=INVALID_PARAMS の golden。5.0.1/4.4.3 両版 OPS SMOKE OK（同値）。
- **繰越**: 複数オブジェクトをまとめて1 STL（v1 は単一）/ obj/gltf/fbx/3mf export は M9 ファイルI/O（`export` コマンド・RESOLVERS は capability.py に確定値・print-export の作法を踏襲）/ 3mf export は addon 導入時に gateway に writer 配線（現状は v1 未対応で CAPABILITY）。

## 6h. M8 実地フィードバック対応ワークストリーム（feedback-first・T8.5 の前に差し込み）
エージェントに `straighten` 傾き補正を実地で使わせた検証で「単体では完遂不可」と判明（主因: PCA が重心ベース符号で上下反転 + 計画確認手段なし）。出典 `FEEDBACK-straighten-2026-06-15.md`（全7項目）。サブPR分割・各 PR は独立3視点セルフレビュー済み。**残作業 PR-4/PR-5 と詳細は `.handoff/NEXT-M8-feedback.md`**。

### PR-1 ✅ main（PR #13）— 横断クイックウィン（#7）
- **UTF-8 出力固定**: `bli/main.py` の `_force_utf8_output()`（import 時に `sys.stdout/stderr.reconfigure("utf-8")`・reconfigure 不可な stream は黙ってスキップ）。Windows CP932 化けを `PYTHONUTF8=1` 強制なしで解消。
- 全 `--targets` に単数別名 `--target`（Typer の複数宣言）。methods.md に dimensions（回転不変）vs bbox.size（world AABB）の注記。FEEDBACK 資料を tracked 化・`scripts/launch_blender_gui.py`（GUI 起動ヘルパ）追加。CLI のみ＝実機 smoke 対象外。
- **CI 教訓**: `--help` レンダリング（rich・端末幅依存）に文字列マッチするテストは CI(80桁)で偽陰性 → **登録済み click オプション名（`param.opts`）を直接検証**する方式に修正。

### PR-2 ✅ main（PR #14）— straighten 根本修正（#5/#2/#6）
- **#5 符号反転防止**: `gateway._principal_axis(obj, *, up, up_hint)`。`up_hint="current"` は principal を **up に近い側**に符号付け（`principal·up>=0`）し最小回転で合わせ、ベースが重いスキャン物体の上下反転を防ぐ。`auto`(既定)=従来の重心ベース（**既存 golden 不変**）。pca 結果に `tilt_from_up_deg`（up からの傾き鋭角・符号非依存）。
- **#2 dry-run / #6 非破壊計測**: `straighten_object(..., dry_run)`。`_snapshot_transform`/`_restore_transform` が全 transform チャンネル（mode/loc/3回転表現/scale）を raw 値で退避→適用→レポート読取→**厳密復元**（push_undo もしない）。`--dry-run` と `--bake-rotation` は矛盾のため**排他**（USER_INPUT）。fingerprint は bake=mesh / それ以外=object。
- smoke: StrPCADown（-Z 偏重 rod・20°tilt）で auto=`principal_world.z<0`（反転）/ current=`z>0`（反転せず）・tilt=20.0、dry-run 非破壊（前後不変・計画=実適用）、QUATERNION reset と floor の dry-run 復元を 5.0/4.4 両版確認。

### PR-3 ✅ main（PR #15）— capture（#1 状態キャプチャ）
- 新コマンド `capture --source viewport|screen|render`（読み取り専用 mutates=False・**Mode.ANY**＝EDIT 中も可）。viewport=gpu offscreen `draw_view3d`（UI なし・`--width/--height`・numpy 保存）/ screen=`screenshot_area`（領域そのまま）/ render=カメラ（`--camera` 省略時 active・render 設定 save/restore で非破壊）。
- 出力 PNG は `outputs_dir` に **content-address 名**で書きパス/サイズ/sha256/**実解像度**（保存 PNG の IHDR から抽出＝screen の領域≠出力ずれ吸収）を返す。viewport/screen は GUI 必須（`--background` は `E_PRECONDITION` で graceful 縮退）。
- バイナリ退避は `output_ref.offload_file`（パス安全・アトミック・ストリーミング sha・content-address 規約を JSON 退避と共有）。gateway 成功後のファイル I/O 失敗は `E_OPERATOR` へ写像（INTERNAL 回避）。`camera` は render 専用 / `width・height` は screen 不可。
- **着手前スパイク `spikes/capture_spike.py`（Spike V・GUI モードで実行＝`blender.exe --python`・`--background` 不可）**: 5.0.1/4.4.3 両版で 4手法 + 本番 gateway.capture_* + 本番 `ops.dispatch("capture")`（viewport 320x240 / render 1024x768）を確認。background smoke は viewport/screen→E_PRECONDITION・不正 camera→E_TARGET_NOT_FOUND の graceful 縮退。

### PR-4 ✅ main（PR #17）— 基準指定整列（#4・支柱問題の本丸）
- **kickoff スコープ（ユーザー確定 (b)）**: `straighten` に基準指定 method 3種を追加。いずれも object 回転のみ・既存作法（dry-run/bake 共有ガード/fingerprint 使い分け）を継承。
  - **angle**: world 軸 `--axis X|Y|Z` まわりに `--degrees`（符号で向き）回転。result `{axis, degrees}`。
  - **align-vector**: `--from-dir`(world) を `--to-dir`(world・**省略時 up**) へ最小回転で合わせる（`_rotation_to`）。**向きを数値で渡せば同一メッシュ内の支柱でも整列でき `transform` 手計算迂回を解消**＝支柱問題の実用解。result `{from_dir, to_dir, from_world_after, angle_deg}`。
  - **reference**: 参照 obj の `--ref-axis`(signed local・省略時 up_axis) world 方向へ、対象の `--axis`(local・省略時最近軸) を合わせる（`_world_align` の目標を up→参照軸へ差し替え・再利用）。result `{axis, aligned_world, reference, ref_axis, reference_world}`。
- 検証は bpy 前（method 別 presence/必須/ゼロベクトル/自己参照=USER_INPUT）。gateway も None ガードで E_PRECONDITION（INTERNAL 化回避）。op 専用 param（degrees/from_dir/to_dir/reference/ref_axis）は schema default なし。CLI `_parse_vec` は try/except→INVALID_PARAMS。**スパイク不要**（検証済みプリミティブの合成）＝smoke golden で両版検証（angle Z45→[0,0,45] / align-vector tilt20°→+Z / reference は参照軸[sin25,0,cos25]へ整列し world up と区別 / ref_axis 省略の up_axis フォールバック）。
- **繰越**: 部分ジオメトリ PCA（頂点サブセット基準・部分指定方法の決定要）は別 PR。`straighten_object`/`_straighten` の if/elif 7 method テーブル化（非緊急）。

### PR-5 ✅ main（PR #18）— undo/redo 公開（#3）
- 新コマンド `bli undo` / `bli redo --steps N`（1〜`runtime.MAX_UNDO_STEPS`=100）。グローバル undo スタックを steps 段戻す/進める。`mutates=True`・`Mode.ANY`・result `{requested, applied}` + 粗いシーン fingerprint。
- gateway `undo_steps`/`redo_steps` = bare `bpy.ops.ed.undo()`/`ed.redo()` を steps 回（**GUI で context override 不要**）。`_step_undo_stack` がスタック端を正規化（`FINISHED` 以外 **および RuntimeError** の両方を break＝applied 頭打ち・INTERNAL 化回避）。`_require_gui_for_undo`=`bpy.app.background`→E_PRECONDITION（capture と同流儀）。`scene_state_fingerprint`（name/type/matrix_world の粗い指標・mesh 内部編集は捉えない）。
- ops `_do_undo_redo` 共通ヘルパ（steps 範囲を bpy 前検証・上限 runtime 集約）。CLI も送信前に弾く（duplicate と同流儀）。
- **着手前 GUI スパイク `spikes/undo_spike.py`（GUI モード実行・研究 §E7）**: 5.0.1/4.4.3 両版で実巻き戻し/redo/複数段/undo 直後の matrix_world 確定を確認。**重要発見**: スタック端は両版とも `RuntimeError('poll() failed, context is incorrect')` を投げる（CANCELLED ではない）→ `_step_undo_stack` の try/except で頑健化。background smoke は E_PRECONDITION 縮退・steps 範囲外 INVALID_PARAMS。

## 6i. M9 ファイルI/O（✅ 完了・T9.1〜T9.4）
M9 はサブPR分割。順序 T9.1 export → T9.2 import → T9.3 save → T9.4 open（**全完了**）。各 PR は独立3視点セルフレビュー済み。グラウンドトゥルース: research.md **§E9**（export/import）/ **§E10**（save）/ **§E11**（open）。**次は M10（非同期job）。**

### M9 共通基盤（T9.4 でも踏襲）
- `capability.RESOLVERS` に import/export 全形式の確定 operator（obj/gltf/fbx/stl ✅・3mf 両版 stub）。`gateway.resolve_export_operator`/`resolve_import_operator` が `CapabilityRegistry.resolve("export.<fmt>"/"import.<fmt>")` へ委譲。`gateway._resolve_op("ns.name")` で operator callable に解決（export/import 共用）。
- 能力解決は**対象解決より前**（3mf 等不在は対象エラーより先に `CAPABILITY_UNAVAILABLE`）。path は `os.path.abspath` 正規化。出力先 dir/入力 file の存在は bpy 到達前に USER_INPUT。副作用後の I/O 失敗は `E_OPERATOR`（INTERNAL にしない）。生 bpy.ops は gateway のみ（`run_operator`）。

### T9.1 export ✅（PR #21）— 多形式 export（print-export の一般化・§E9）
- `export --format obj|fbx|gltf|stl|3mf --path [--targets <name>] [--use-selection]`（mutates=False・OBJECT）。セレクタ: `--targets`=その集合を選択して出す / `--use-selection`=現在の選択集合 / どちらも省略=シーン全体。
- gateway `export_generic`（**形式別 selection param マップ**: stl/obj=`export_selected_objects`・gltf/fbx=`use_selection`）/ `require_targets`（0件NG・複数OK）/ `current_selection` / `_select_set`（複数対象の選択 save→restore・`_select_only` は委譲に縮退）。scale=1.0 素通し（print-export が scale 窓口・gltf は scale param 自体が無い）。
- **glTF は GLB 単一固定・`--path` は `.glb` 必須**（`export_format` 有効値は両版とも GLB/GLTF_SEPARATE のみ＝**GLTF_EMBEDDED は存在しない**＝実機 enum ダンプで確定・SEPARATE は .bin 分離で統計が崩れるため不採用）。3mf=CAPABILITY。result `{path,size,sha256,format,operator,use_selection,exported_objects}`・fingerprint=sha 先頭16桁。
- セルフレビュー: P1-1 解消（GLTF_EMBEDDED 不在→.glb 必須化で無効 enum→INTERNAL 化防止）+ P2（_select_only DRY/空 targets 弾き/methods.md result/両指定テスト/FBX magic）。

### T9.2 import ✅（PR #22）— 多形式 import（export と対称・§E9）
- `import --format obj|fbx|gltf|stl|3mf --path`（mutates=True・OBJECT）。取込は **import 前後の `bpy.data.objects` 差分**で特定（名前衝突時 Blender が `.001` リネームのため集合差が唯一信頼可）。result `{format,operator,path,imported:[{name,type}],count}`・fingerprint=names_fingerprint・大量取込は output_ref 退避（`_ok_offload`）。
- gateway `import_generic`（前後 diff・undo 境界・**壊れファイルの非 RuntimeError＝glTF importer は Python 実装で KeyError/JSONDecodeError 等を投げ得る → `except Exception` で E_OPERATOR 写像し INTERNAL 化防止**）。**FBX import 版差**（5.0=`wm.fbx_import` / 4.4=`import_scene.fbx`）は RESOLVERS 優先順で吸収。scale は渡さず operator 既定。CLI 関数名 `import_`（予約語回避）+`@app.command("import")`。
- セルフレビュー: P1 なし・P2（壊れファイル→E_OPERATOR/abspath 正規化/`_resolve_op` DRY/methods.md import 注記）。

### T9.3 save ✅（PR #23）— .blend 保存（§E10）
- `save [--path <.blend>] [--backup/--no-backup]`（mutates=True・Mode.ANY）。target=--path（abspath・`.blend` 必須）/ 省略時は現在ファイル（`bpy.data.filepath`・未保存=空なら USER_INPUT）。
- **上書きは既定 backup**（spec『上書きは既定でバックアップ強制』）＝gateway `save_blend` が **preferences `save_version` を一時上書き（1 if backup else 0）→try/finally restore** して native `.blend1` 機構を決定的制御（preference 非汚染・逐次処理前提）。`--no-backup`=save_version 0 で抑止。`current_filepath`（未保存=空文字）。
- result `{path,size,backed_up,backup_path}`（**backed_up は保存後に `.blend1` 実在確認してから報告**＝偽報告防止）・fingerprint=metadata digest（path|size・.blend 全体 sha は大容量/非決定的のため不採用）。**保存 .blend の magic 版差: 4.4=非圧縮 `BLENDER` / 5.0=zstd 圧縮**（compress 既定でも 5.0 zstd）。
- セルフレビュー: P1 解消（.blend1 実在確認）+P2（methods.md save 注記/save_version 逐次前提 docstring）。

### T9.4 open ✅（PR #25）— .blend を開く（最高リスク解消）【M9 完了】
`open --path <.blend> [--force]`（mutates=True・Mode.ANY）。シーン全体を置換（§E11）。
- **最高リスク解消**: 当初懸念（常駐 GUI で open がディスパッチ機構を壊すか）を **GUI 実機スパイクで確定**（`open_spike.py`/`open_job_spike.py`・両版）: Dispatcher の **persistent pump タイマ + "bli-accept" TCP スレッドは `open_mainfile` を跨いで生存**し、open を含む **1 ジョブ内で結果構築→return も成立**＝**再登録不要**（当初想定の `load_post` 再登録は不要だった）。
- **未保存ガード**（ユーザー選択=`--force`）: `bpy.data.is_dirty` は dispatch（pump タイマ）文脈で **save 後に reset せず**・background 常時 True で**信頼不可**（`dirty_probe_gui.py` 実測）→ **自前 `session_state`**（純Python）で「bli が最後の save/open 以降に mutate したか」を追跡。**mutating コマンドの実行 *前* に pessimistic に modified**（partial mutation で例外を投げても安全側＝silent data loss 回避）/ save・open 成功で clean（dispatch で一元遷移）。未保存 かつ `--force` なしは `E_PRECONDITION`。v1 は静的 `mutates` 判定で保守的（select/undo・検証失敗も modified 扱い）。
- `gateway.open_blend` は **`run_operator` を使わず素の `open_mainfile`**（temp_override は scene 置換で teardown 破損・load は undo 不要）・`except Exception`→E_OPERATOR（RuntimeError 以外＝OSError 等も・INTERNAL 化回避）。検証は全て bpy 到達前（空/`.blend`/ファイル実在=USER_INPUT・未保存ガード=session_state）。result `{path, scene, object_count, forced, discarded_unsaved}`・fingerprint=`scene_state_fingerprint`。
- セルフレビュー（独立3視点）: **P1**（実行後フック→pre-mark 化＝partial mutation の silent data loss 回避）+**P2**（open_blend の例外を OSError 等まで写像）+**P3**（object_count 命名統一・spec 監査ログ open 追記）解消。pytest 303・両版 smoke OPS SMOKE OK（open 往復+未保存ガード golden）。

### M9 繰越（別途・M10 以降）
- NUL バイト等の病的 path で `os.path.abspath`/`isdir` が `ValueError` 未捕捉→INTERNAL 化の恐れ（export/import/save/open 共通・信頼境界）。大量取込時の count inline 表示。GLTF_SEPARATE 対応。複数オブジェクト1ファイル（export は選択集合まとめ可・OK）。open の未保存追跡を per-invocation な dirtied 信号へ精緻化（現状は select/undo・検証失敗も保守的に modified 扱い＝--force 要求）。

## 6j. M10 非同期job & フリーズ対策（進行中・T10.1/T10.2 完了・残り T10.3）
M10 はサブPR分割（NEXT-M10.md・キックオフ D-A〜D-E 確定）。順序 T10.1 job 化 → T10.2 render busy → T10.3 watchdog。**本質**: bpy はメインスレッド直列で重量ネイティブ処理は中断不能＝「非同期」は**接続を塞がない + 観測性**（メインの並列化ではない・spec §7 残存リスク）。土台は M4（settle/RUNNING/request-status）。**次は T10.3（heartbeat watchdog・GUI スパイク必須）= `.handoff/NEXT-M10.md`**。

### T10.1 job 化 ✅（PR #27）— heavy は accepted 即返＋auto-wait/--async
- heavy（`import`/`export`/`print-check`/`print-repair`・`mesh` op が `boolean`/`decimate`）を非同期 job 化。`Command.is_heavy`/`heavy_ops` + `is_heavy_request(cmd, params)`（純Python）。`Dispatcher.submit_async` + `ACCEPTED` センチネル。executor が heavy→submit_async→ACCEPTED、server が `{accepted, job_id=rid}` 即返し settle が registry 確定。
- **UX（D-B）**: CLI 既定は **sync 見え**（内部で accepted→`request-status` ポーリングで最終結果まで auto-wait）/ `--async` で job_id 即返。`job-status`/`job-wait`（CLI ポーリングのシュガー・request-status RPC を使う）。
- **DoD**: `request-status` は `LOCK_FREE_METHODS`＝受信スレッド処理（メイン dispatch を経ない）→ 重量 job がメインを塞いでも応答（接続が塞がらない）。L3 E2E（`test_e2e_jobs.py`・heaviness executor + 別スレッド pump）で実証。**T10.2/T10.3 の観測系も lock-free で受信スレッド処理を踏襲**。
- **セルフレビュー（独立3視点）で解消**: **P1** registry TTL を `max(600, JOB_WAIT_TIMEOUT)` へ（完了 job の遅延回収が purge で消えない）+ `_await_job` が UNKNOWN job_id を即失敗（30分ハング防止）。**P2** `pump()` を `except BaseException`（重量 C 異常で settle 漏れ→registry 孤児化＋pump 死を防ぐ）/ `job-wait` を `_present_result` で `_rpc` と共有（output_ref/--fetch drift 解消）/ `heavy_ops` を list-commands 露出（発見性）/ ポーリング瞬断許容。pytest 316・両版 OPS SMOKE OK（既存 heavy は smoke の同期 executor で回帰なし）。
- **落とし穴**: 背景 smoke は**自前の同期 executor**＝非同期経路を通らない（job 経路は L3 が担保）。`bpy.app.timers` は `--background` で非発火＝render handler/watchdog の実発火は **GUI スパイク必須**。

### T10.2 render busy ✅（PR #28・マージ待ち）— レンダ中は重量/破壊系を即拒否
- レンダリング中（`render_init`〜`render_complete`/`render_cancel`）は **mutating または heavy** を **dispatch 前**（`begin`/`settle` より前）に `BUSY_RENDERING`（ENVIRONMENT・retryable・CLI exit 2）で即拒否＝**キューに積まない**（フリーズ中のジョブ滞留を防ぐ）。**read-only と lock-free（request-status/job-status/job-wait）はレンダ中も通す**＝観測性を維持。キックオフ確定: **R-A**=mutating/heavy のみ拒否（read-only/lock-free は通す）/ **R-B**=`BUSY_RENDERING`→exit 2（retryable）。
- **GUI スパイク（`spikes/render_spike.py`・GUI 必須・両版確定・研究 §E12）**: `render_init`/`render_complete`/`render_cancel` は GUI 常駐で発火し **Blender のレンダスレッド（`Dummy-N`）から呼ばれる（メインではない）**→ busy は `threading.Event`（受信スレッドが安全に読む・レンダ中 `is_busy()=True` を別スレッドが観測）。`render_complete` と `render_cancel` の両方で降ろす（キャンセル取りこぼし防止）。**`@persistent` で handler は `open_mainfile`（bli open）を跨いで生存**（付けないと open 後に busy 検知が無言で壊れる・background smoke `survived_open=True` で裏付け／GUI 内 timer から `read_homefile`/open を呼ぶと splash/再入で固まるため生存確認は background で行う）。
- 実装: `render_state.py`（新規・純Python の busy フラグ + bpy 依存の `install`/`remove` に閉込め・`install` は冪等＝register 再入で二重登録しない）/ `server._handle_rpc` が `render_busy()`（注入＝server は bpy 非依存のまま）かつ（`Command.mutates` または `is_heavy_request`）で `BUSY_RENDERING` 即返し（未知メソッドは None→不ブロックで通常経路へ）/ CLI `_exit_code_for` で `BUSY_RENDERING`→exit 2。
- **セルフレビュー（独立3視点・P1 なし）で解消**: **P2** `install` 冪等化 / smoke の「未登録」と「open で消失」を分離 / mesh テスト docstring 訂正（heavy 独立検証は `export`＝mutates=False heavy=True）。**P3** `_blocked_during_render` に `load_definitions()` 冪等ロード / 未知メソッドは BUSY_RENDERING にしないテスト追加 / spec 終了コード表に exit 2 注記。pytest 331・両版 OPS SMOKE OK（busy 拒否 + @persistent 生存）。
- **繰越**: `print-export` は `is_heavy=False`（T10.1 D-C で heavy 対象外）だが methods.md 表は `H=✓`＝ドリフト要再整合（heavy にするか表を直すか）。レンダが complete/cancel を発火せず死んだ場合の busy stuck-ON は T10.3 watchdog の領域。

### T10.3 watchdog ⬜（次）
- pump 生存印（`last_pump_ts`）+ 監視スレッドで `MAIN_THREAD_UNRESPONSIVE` を検知し**通知のみ**（kill しない）・lock-free な観測系に載せる。**GUI スパイク必須**（重量 op で timer 停止を実測）。busy stuck-ON（T10.2 繰越）の検知もここで扱える。
- 詳細・着手手順・キックオフ判断（R-C〜R-E）は **`.handoff/NEXT-M10.md` §3-2/§4 T10.3/§4.5**。

## 6e. M6 で確立した再利用パターン（T6.2 以降で踏襲）
- **破壊的 mesh 操作は共有ガード**: `ops._guard_shared_mesh(gateway, obj, params)` を呼ぶ（delete も対象になり得る）。`--make-single-user` 無しで users>=2 は `E_PRECONDITION`。
- **bpy 到達前の入力検証**: `ops._require_input(cond, symptom, remediation)` で USER_INPUT を投げる（pytest が bpy 無しで到達できる＝テスト容易）。param/前提チェックは `from . import gateway` より前に。
- **数値の有限性はサーバ側（SSOT）で弾く**: VEC3/VEC4/FLOAT の `nan/inf` は `schema._check_type`（`math.isfinite`）で拒否済み。CLI の `_parse_vec3` だけに頼らない（`json.loads` が `Infinity/NaN` を通すため CLI 非経由 RPC を防御）。新しい数値 ParamType を足す時も `_check_type` に有限性を入れる。
- **暴走防止の上限は bli-core 定数に集約**: `runtime.MAX_DUPLICATE_COUNT` のように CLI/ops 双方が同じ定数を参照（マジックナンバー散在・片側欠落を防ぐ）。CLI でも「送信前に弾く」を貫く。
- **複製/破壊系の matrix 基準は評価済み元 obj**: 新規 `copy()` 直後の `matrix_world` は depsgraph 未評価で誤値になり得る。world 計算は **元 obj の評価済み matrix_world** を基準にする（親付きでも正確）。collection 解決は 0 件時に `scene.collection` フォールバック。
- **非対応型は INTERNAL でなく E_PRECONDITION**: bpy.data 直接操作（`obj.modifiers.new`/`obj.material_slots` 等）は非対応型で生 RuntimeError/TypeError を投げ INTERNAL/code_bug 誤分類になる。`require_material_support`/`require_modifier_support` のような前提検証を `require_single` 直後に置き、必要なら gateway 側で try/except → E_PRECONDITION に変換する（USER_INPUT 起因を INTERNAL にしない）。operand（boolean の相手等）も型/自己参照を add 前に検証。
- **暴走しうる数値は範囲を bpy 前に弾く**: count（duplicate）/ levels（subsurf）等は巨大値で Blender を固める。`_require_input` で上限・範囲を検証（silent クランプにも頼らない）。
- **集合は導出して手書きを避ける**: 派生定数（例 `_ALL_MODIFIER_TYPE_PARAMS = set().union(*_MODIFIER_TYPE_PARAMS.values())`）は元から導出し、type 追加時の追従漏れを防ぐ。
- **fingerprint は操作の本質に合わせる**: stack/状態系（modifier add/remove/list, material）は専用 `*_fingerprint`（param 込み）/ mesh を変える操作（apply 系）は `object_fingerprint`（mesh 込み）。
- **targets は `--targets` オプション**（positional 不可）。複数解決は `gateway.resolve_targets`（完全名>regex・**不正regex は USER_INPUT** 済み・view layer 限定が要るなら名前で絞る）。単一は `require_single`。
- **presence-sensitive な BOOL フラグは schema default を持たせない**（`help --json` の default:false で生成クライアントが誤送信するため）。通常の許可フラグ（make_single_user 等）は default=False で可。
- **world 座標は matrix_world 経由**・回転は `rotation_mode` を尊重・出力（selected/一覧等）は決定的順序（sorted）に。
- **エンベロープ**: 破壊系は `_ok(op, data, fingerprint=...)`。drift 検証用に意味ある fingerprint を返す（select は selection_fingerprint）。
- **AST guard**: `bpy.ops.*()` は gateway.py のみ。`obj.copy()`/`data.copy()`/`collection.objects.link/.remove` は bpy.data 直接操作で **ops でなく gateway に**置く（生 ops ではないが bpy 接点は gateway 集約の方針）。

## 7. 再開手順（コピペ可）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
# 1) main を最新化して新しい作業ブランチを切る（M5 例）
git checkout main && git pull origin main
git checkout -b feature/m5-info
# 2) 環境と現状確認（ベースライン緑を確認してから着手）
uv sync
PYTHONUTF8=1 uv run pytest -q                 # 79 passed を確認
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
# 3) 実機スモーク（ops 一式 + set-origin golden + request-status）:
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background \
  --python packages/bli-addon/spikes/smoke_ops.py 2>&1 \
  | sed -n '/BLI_OPS_SMOKE_BEGIN/,/BLI_OPS_SMOKE_END/p'   # → OPS SMOKE OK
# 4) CLI ローカルコマンド（addon不要）:
PYTHONUTF8=1 uv run bli list-commands --json
PYTHONUTF8=1 uv run bli help --command set-origin --json
```
M8 は T8.1–T8.4 + 実地フィードバック PR-1〜5 完了。**次は T8.5 print-export = `.handoff/NEXT-M8.md`**（これで M8 完了）。
（main のベースライン緑は `uv run pytest` = 244 passed。新ブランチは main pull 後に `feature/m8-print-export` で切る。）
GUI 常駐での `bpy.app.timers` 実発火・undo/redo の実巻き戻しは GUI スパイク/L4 手動検証で別途（background では近似/縮退）。

## 8. 重要な落とし穴
- **bli-core は純Python・依存ゼロを厳守**（アドオンにPydanticを入れない。CLI側のみPydantic可）。3.10互換を維持。
- **bpy.app.timers は `--background` で発火しない**。実機テストはメインスレッド手動pumpで近似（dispatch_poc/smoke参照）。GUI実発火はL4手動。
- **AST guard**: `bpy.ops.*()` の直接呼び出しは `gateway.py` のみ許可。他は `run_operator` 経由。
- Windows: ソケット切断は RST（ConnectionReset）になり得る。テストは空recvとRST両方を許容済み。
- 一時ファイル `connection.json`/`session.token` は `BLI_STATE_DIR`（既定 `%LOCALAPPDATA%/bli`）。テストは env で差し替え。
- ※前セッションでアシスタントのツール呼び出しタグ書式ミスが頻発し作業が見かけ上停止した。**これはコード/設計の問題ではない**。新セッションでは正しいツール呼び出し書式を厳守すること。

## 9. アーキテクチャ要約
```
AIエージェント → bli CLI(Typer) → TCP(127.0.0.1, 長さ接頭辞JSON, HELLO+token)
  → bli-addon サーバ(受信スレッド) → Dispatcher(bpy.app.timers, メイン直列)
  → ops ドメインハンドラ → BpyGateway(run_operator/temp_override) → bpy
共有: bli-core(純Python SSOT: commands/schema/protocol/errors/runtime)
packages/{bli-core, bli-cli, bli-addon}（uv workspace）。
```

## 10. 後続マイルストーンの繰越事項
- M5: scene-info の output_ref 退避（大きい結果はファイル退避 + sha256 + os.replace）。詳細は NEXT-M5.md。
- M6: T6.1–6.4 全完了。`exec-python` は M11。
- M7: T7.1–7.3 全完了（T7.3 boolean/decimate は PR マージ待ち＝M7 完了）。次は M8。`_mesh` の op 別検証テーブル化（設計 P3）は 8 op 目を足す時に検討。boolean/decimate の「対象に他 modifier がある場合は焼き込まれる」前提は v1 未保証（methods.md 注記済み）。
- M6 編集系の**孤児データブロック**（delete の sole-user mesh / material create-and-assign の置換で外れた material）の purge は後続（save/cleanup 系）で対応。即時 GC しない bpy 仕様どおりで設計上は意図的（レビューで P2 記録）。
- M8: **実装完了**（T8.1–T8.5 + 実地FB PR-1〜5）。T8.5 print-export は STL のみ・3mf は CAPABILITY+STL hint（§E8）・global_scale 一本化。実地FB 繰越: 部分ジオメトリ PCA（straighten・別 PR）/ straighten・mesh の op 別検証テーブル化（非緊急）/ undo の fingerprint は粗い（mesh 内部編集は捉えない）/ redo スタックは新規操作で消える（v1 許容）。print-export 繰越: 複数オブジェクト1 STL（v1 単一）/ 3mf export writer（addon 導入時）。
- M9: **完了**（T9.1 export #21 / T9.2 import #22 / T9.3 save #23 / T9.4 open #25・確定要約 §6i・GT research §E9/§E10/§E11）。M9 繰越: NUL バイト等病的 path の `ValueError` 未捕捉（export/import/save/open 共通・信頼境界）/ 大量取込の count inline 表示 / GLTF_SEPARATE 対応 / open の未保存追跡の精緻化（per-invocation dirtied 信号）。**次は M10**。
- M10: `job-status`/`job-wait`（非同期job）+ `--dry-run`。settle/RUNNING 機構は M4 で土台済み。
- M12: Claude Code Skill 同梱（`.claude/skills/bli/`）+ `help --json` 自動生成 + `schema_hash` 同期。
