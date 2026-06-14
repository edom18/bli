# bli (Blender CLI) — 引き継ぎ資料 (HANDOFF)

最終更新: 2026-06-13 / ブランチ: `feature/m0-bootstrap`（**未コミット**）

> 新規セッションはこの1枚を読めば再開できる。詳細は `specs/blender-cli-core/` を参照。

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
- `tasks.md` + `tasks/01..18` — M0–M2 のチケット台帳（M0,M0.5,M1,M2 は ✅）

## 2. 環境（このマシン）
- OS: Windows 11 / シェル: Git Bash（Bashツール）。日本語標準出力は `PYTHONUTF8=1` を付ける。
- Python(dev/CLI): **3.10.6**（system）。uv: **0.7.3**。
- Blender: **5.0.1**（Python 3.11.13・主軸）/ **4.4.3**（ベストエフォート）。
  - `C:\Program Files\Blender Foundation\Blender 5.0\blender.exe`
  - `C:\Program Files\Blender Foundation\Blender 4.4\blender.exe`
- git: 初期化済み・`feature/m0-bootstrap` 上。**まだ1度もコミットしていない**（全ファイル untracked）。
  - ルール: main 直接コミット禁止 / 日本語コミット + prefix（feat/fix/docs/chore…）。

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
| **M4 CLI骨格 & 診断コマンド**（Pydanticラッパ/help/list-commands/request-status/--id） | ✅ | pytest 78件 + parity緑 + 実機 request-status OK |
| M5–M14 | 未着手 | — |

**現在の全テスト/lint状態: `uv run pytest` = 78 passed / `ruff check` = 緑 / `ruff format --check` = 緑 / AST guard = OK。**

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
- **lookup の TTL purge**: `RequestRegistry.lookup` も読み取り前に `_purge` する（request-status だけポーリングしても未確定エントリが恒久化しない）。
- **ping もタイムアウト写像を共通化**: `_call_or_exit` を抽出し `_rpc`/`ping` 双方で使用。ping も実機では Dispatcher 経由のため TIMEOUT→exit2 + id 提示に統一（doctor は診断目的でエラーを握るため対象外）。

## 7. 再開手順（コピペ可）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
uv sync
PYTHONUTF8=1 uv run pytest -q                 # 60 passed を確認
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
# 実機スモーク（ops 一式 + set-origin golden + request-status）:
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background \
  --python packages/bli-addon/spikes/smoke_ops.py 2>&1 \
  | sed -n '/BLI_OPS_SMOKE_BEGIN/,/BLI_OPS_SMOKE_END/p'   # → OPS SMOKE OK
# CLI ローカルコマンド（addon不要）:
PYTHONUTF8=1 uv run bli list-commands --json
PYTHONUTF8=1 uv run bli help --command set-origin --json
```
次は **M5 以降**（plan.md §4：M5 情報取得 / M6 汎用編集 / M7 メッシュ編集は概ね並行可）。GUI 常駐での `bpy.app.timers` 実発火は L4 手動検証で別途。

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
- M8: print3d Toolbox の実モジュールid特定（Extensions）。3MFは addon 必要 or STLフォールバック。
- M9: import/export 各フォーマット（RESOLVERS は capability.py に確定値あり）。
- M12: Claude Code Skill 同梱（`.claude/skills/bli/`）+ `help --json` 自動生成 + `schema_hash` 同期。
- いずれ: 初回コミット（feature/m0-bootstrap、日本語コミットメッセージ、main直接禁止）。
