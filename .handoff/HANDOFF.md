# bli (Blender CLI) — 引き継ぎ資料 (HANDOFF)

最終更新: 2026-06-15 / 状態: **PR #1–#10（M0–M7 全 + M8 T8.2 straighten）マージ済み（origin/main）。M8（3シナリオ中核価値）着手中: T8.1 set-origin ✅（M3 実装・S1 golden）/ T8.2 straighten ✅（PR #10 マージ済み）/ T8.3 print-setup（単位 mm/m）実装完了・独立3視点セルフレビュー済み＝feature/m8-print-setup で PR 作成/マージ待ち。次は T8.4 print-check/repair（print3d 再スパイク必須・`.handoff/NEXT-M8.md` 参照）**。

> 新規セッションはこの1枚を読めば再開できる。詳細は `specs/blender-cli-core/` を参照。
> **次の作業（M6）の着手手順とタスクは `.handoff/NEXT-M6.md` を参照**（このファイルは全体史 + 規約）。

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
| **M8 3シナリオ中核価値**（set-origin / straighten / print-*） | 🔶 進行中: T8.1 set-origin ✅（M3）/ T8.2 straighten ✅（PR #10 main）/ T8.3 print-setup ✅（PR待ち）/ T8.4 print-check/repair・T8.5 print-export 未着手 | pytest 201 + 5.0/4.4 実機 smoke OK |
| M9–M14 | 未着手（M8 完了後は M9 ファイルI/O / NEXT-M9.md） | — |

**状態（feature/m8-print-setup・M8 T8.3 まで）: `uv run pytest` = 201 passed / `ruff check` = 緑 / `ruff format --check` = 緑 / AST guard = OK / pyright は既存1件のみ（`bli/main.py:101` の narrowing・実行時安全）。main は 195 passed（M8 T8.2 まで）。**

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
M7 は T7.1–7.3 完了（T7.3 は PR マージ待ち＝M7 完了）。**次は M8（3シナリオ中核価値）= `.handoff/NEXT-M8.md`**。
（feature/m7-mesh-heavy のベースライン緑は `uv run pytest` = 184 passed。M8 は T7.3 PR マージ後に main から新ブランチを切る＝共有ファイルのコンフリクト回避。）
M5/M6/M7 は概ね並行可（plan.md §4）。GUI 常駐での `bpy.app.timers` 実発火は L4 手動検証で別途。

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
- M8: print3d Toolbox の実モジュールid特定（Extensions）。3MFは addon 必要 or STLフォールバック。
- M9: import/export 各フォーマット（RESOLVERS は capability.py に確定値あり）。
- M10: `job-status`/`job-wait`（非同期job）+ `--dry-run`。settle/RUNNING 機構は M4 で土台済み。
- M12: Claude Code Skill 同梱（`.claude/skills/bli/`）+ `help --json` 自動生成 + `schema_hash` 同期。
