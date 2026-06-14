# bli (Blender CLI) — 引き継ぎ資料 (HANDOFF)

最終更新: 2026-06-14 / 状態: **PR #1（M0–M4）・PR #2（M5）・PR #3（M6 T6.1）・PR #4（M6 T6.2 dup/delete）・PR #5（M6 T6.3 material）マージ済み。次は M6 T6.4（modifier）を `feature/m6-modifier` で実装（M6 最後のサブPR）**。

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
| **M6 汎用編集**（T6.1 select/transform/apply-transform ✅・T6.2 duplicate/delete ✅・T6.3 material ✅ main。T6.4 modifier 未） | 🔨 進行中（次=T6.4 modifier / feature/m6-modifier） | pytest 132 + 5.0/4.4 実機 smoke OK |
| M7–M14 | 未着手 | — |

**main の全テスト/lint状態（M6 T6.3 まで・PR #5 マージ済み）: `uv run pytest` = 132 passed / `ruff check` = 緑 / `ruff format --check` = 緑 / AST guard = OK / pyright は既存1件のみ（`bli/main.py` の narrowing・実行時安全）。**

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

### T6.4 未着手 → **次の作業（`.handoff/NEXT-M6.md` を参照・M6 最後）**
- T6.4 `modifier`（add/remove/list/apply: MIRROR/SUBSURF/SOLIDIFY/DECIMATE/BOOLEAN）→ **次・ブランチ `feature/m6-modifier` 作成済み**。modifier API は M0.5 的スパイクで確認済み（modifier は**オブジェクト単位**＝add/remove/list は共有ガード不要・apply のみ mesh 焼き込みでガード要）。

## 6e. M6 で確立した再利用パターン（T6.2 以降で踏襲）
- **破壊的 mesh 操作は共有ガード**: `ops._guard_shared_mesh(gateway, obj, params)` を呼ぶ（delete も対象になり得る）。`--make-single-user` 無しで users>=2 は `E_PRECONDITION`。
- **bpy 到達前の入力検証**: `ops._require_input(cond, symptom, remediation)` で USER_INPUT を投げる（pytest が bpy 無しで到達できる＝テスト容易）。param/前提チェックは `from . import gateway` より前に。
- **数値の有限性はサーバ側（SSOT）で弾く**: VEC3/VEC4/FLOAT の `nan/inf` は `schema._check_type`（`math.isfinite`）で拒否済み。CLI の `_parse_vec3` だけに頼らない（`json.loads` が `Infinity/NaN` を通すため CLI 非経由 RPC を防御）。新しい数値 ParamType を足す時も `_check_type` に有限性を入れる。
- **暴走防止の上限は bli-core 定数に集約**: `runtime.MAX_DUPLICATE_COUNT` のように CLI/ops 双方が同じ定数を参照（マジックナンバー散在・片側欠落を防ぐ）。CLI でも「送信前に弾く」を貫く。
- **複製/破壊系の matrix 基準は評価済み元 obj**: 新規 `copy()` 直後の `matrix_world` は depsgraph 未評価で誤値になり得る。world 計算は **元 obj の評価済み matrix_world** を基準にする（親付きでも正確）。collection 解決は 0 件時に `scene.collection` フォールバック。
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
次は **M6 T6.4（modifier）**。具体的なスコープ・タスク・設計は **`.handoff/NEXT-M6.md`** を参照。
（ベースライン緑は `uv run pytest` = 132 passed。ブランチ `feature/m6-modifier` 作成済み。）
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
- M6: T6.1（select/transform/apply-transform）✅ / T6.2（duplicate/delete）✅ / T6.3（material）✅。残り **T6.4 modifier（M6 最後）**。`exec-python` は M11。
- M6 編集系の**孤児データブロック**（delete の sole-user mesh / material create-and-assign の置換で外れた material）の purge は後続（save/cleanup 系）で対応。即時 GC しない bpy 仕様どおりで設計上は意図的（レビューで P2 記録）。
- M8: print3d Toolbox の実モジュールid特定（Extensions）。3MFは addon 必要 or STLフォールバック。
- M9: import/export 各フォーマット（RESOLVERS は capability.py に確定値あり）。
- M10: `job-status`/`job-wait`（非同期job）+ `--dry-run`。settle/RUNNING 機構は M4 で土台済み。
- M12: Claude Code Skill 同梱（`.claude/skills/bli/`）+ `help --json` 自動生成 + `schema_hash` 同期。
