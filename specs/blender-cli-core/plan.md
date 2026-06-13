# bli (Blender CLI) — 実装計画書 (plan.md)

| 項目 | 内容 |
|------|------|
| 対象仕様 | `spec.md`（blender-cli-core v1） |
| 関連 | `research.md` / `data-model.md` / `contracts/` |
| 実装規模 | **プロダクトレベル堅牢**（エージェント自律操作の信頼性重視） |
| セキュリティ | **spec §6 準拠**（token認証 / exec既定off / 監査 / 127.0.0.1固定） |
| テスト | **4層フル + CIマトリクス**（5.0/4.4） |
| 作成日 | 2026-06-13 |

---

## 1. 実装方針

### 1.1 アーキテクチャ（関心事の分離 / 依存性逆転）
- monorepo（uv workspace）で3パッケージに分離（research.md 論点4/5 で確定）:
  - `bli-core` — **SSOT**。Command/Param dataclass、プロトコルcodec、ErrorObject、schema生成。**依存ゼロ・純Python**。
  - `bli-cli` — Typer製CLIクライアント。Pydanticラッパ可。
  - `bli-addon` — Blenderアドオン。TCPサーバ + bpy実行。**Pydantic禁止**（`bli-core` を vendoring）。
- ドメインハンドラは bpy 非依存。bpy 接点は `BpyGateway` に集約 → テスト時に差し替え、将来 headless と共有。

```
blender-auto-cli/
├── pyproject.toml              # [tool.uv.workspace] members
├── uv.lock
├── .claude/skills/bli/
│   ├── SKILL.md
│   └── reference/cli-schema.json
├── .github/workflows/test-blender.yml
├── packages/
│   ├── bli-core/   src/bli_core/{commands,schema,protocol,errors,types}.py
│   ├── bli-cli/    src/bli/{main,client,render,config}.py  (entry: bli)
│   └── bli-addon/  src/bli_addon/{__init__,server,dispatcher,gateway,
│                      resolver,handlers/…}.py + blender_manifest.toml
│                   src/bli_addon/vendored/bli_core/   # build時コピー
└── specs/blender-cli-core/…    # 本仕様一式
```

### 1.2 レイヤと責務
| レイヤ | パッケージ | bpy | 主責務 |
|--------|-----------|:--:|--------|
| CLI | bli-cli | × | 引数解析(Typer)・Pydantic検証・出力整形・終了コード |
| プロトコルcodec | bli-core | × | framing(recv_exactly)・HELLO・JSON-RPC・ErrorObject |
| サーバ受信 | bli-addon | × | listen・token照合・session_lock・queue投入 |
| ディスパッチ | bli-addon | △ | bpy.app.timers直列実行・Event待ち合わせ・RequestRegistry |
| ドメインハンドラ | bli-addon(将来core化) | × | コマンドロジック（origin算出等） |
| BpyGateway | bli-addon | ◯ | bpy読み書き・run_operatorラッパ |
| Adapter/Resolver | bli-addon | ◯ | 能力検出・operator解決・バージョン吸収 |

### 1.3 開発環境
- CLI/コア: uv で venv。**`bli-core` は Python 3.10 互換維持**（システム3.10.6, `tomllib`/`Self`等3.11+機能を core で使わない）。`bli-cli` も 3.10+。
- アドオン: Blender 5.0/4.4 の埋め込み Python（3.11系）で動作。venv なし、`bli-core` は vendoring。
- Lint/Format: `ruff`（lint+format）。型: `pyright` / `mypy`（CLI/core）。
- **生 `bpy.ops` 禁止の AST チェック**を CI に組込（`run_operator` 経由のみ許可）。

### 1.4 Git 運用（git-workflow ルール準拠）
- `git init` → `main`（保護、直接コミット禁止）。
- 機能ごとに `feature/<name>` ブランチ。
- コミットは日本語 + prefix（feat/fix/refactor/docs/chore）。
- 破壊的Git操作（push --force / reset --hard / branch -D）は使わない。
- 初期化は M0 で実施（feat: ではなく chore: プロジェクト初期化）。

---

## 2. リサーチサマリー（research.md 要約・確定事項）

| # | 決定 | 要点 |
|---|------|------|
| R1 | メインスレッド直列 | `bpy.app.timers` 固定間隔ディスパッチャ1本 + `queue.Queue` + 受信スレッドのみ `Event.wait(timeout)`。timerはNone返さず例外を握る |
| R2 | operatorラッパ | `run_operator()`: `temp_override` + `poll`先行 + `'FINISHED' in result` + 最小`undo_push`。生opsはAST禁止 |
| R3 | operator解決 | 能力検出。STL=`wm.stl_export`(新)→旧, OBJ/glTF=`*_scene.*`, FBX import=`wm.fbx_import`→旧, 3MF/print3d=Extension要 |
| R4 | **SSOT** | **Pydanticをアドオンに入れない**。`bli-core` dataclassが真実、CLIはPydanticラッパ、addonはvendoring+自前検証 |
| R5 | Skill/構成 | `.claude/skills/bli/SKILL.md` 同梱、uv workspace、`bli`エントリ、pipx配布 |
| R6 | CI | GitHub Actions matrix 5.0×4.4、`blender --background`、`bl_rna`契約+golden検証 |

### 要実機検証（Phase 0 スパイクで消化）
- STL/FBX/3MF/print3d の確定 operator 名・引数・addon module 名（API doc 403 で未確証）。
- `Event.wait(timeout)` の 5.0/4.4 長時間安定性（GIL競合の実害）。不安定なら `time.sleep` ポーリングへ切替可能に。
- `undo_push` の 5.0/4.4 引数・挙動一致。`temp_override` に必要な最小メンバ集合。

---

## 3. 設計概要

- データモデル → `data-model.md`（Command/Param, プロトコルメッセージ, ErrorObject, RequestRegistry, OutputRef, ConnectionInfo, Config, Capability, Target解決）。
- APIコントラクト → `contracts/`（`protocol.schema.json` エンベロープ, `methods.md` メソッドカタログ）。
- 大きな出力は `output_ref` 退避。エラーは構造化（category/kind/cause/remediation）。

---

## 4. タスク分割（依存順 / マイルストーン）

> 粒度は tasks.md 生成を見据える。各タスクに DoD（完了定義）とテスト層を付す。`★`=最初の縦切り（walking skeleton）。

### M0: プロジェクト基盤
- T0.1 `git init` + `main` + `.gitignore`（`.bli/`, `outputs/`, token, `__pycache__`, `*.blend1`）。
- T0.2 uv workspace 初期化、`packages/{bli-core,bli-cli,bli-addon}` の `pyproject.toml`（PEP 621）。
- T0.3 `ruff`/`pyright` 設定、pre-commit。
- T0.4 CI スケルトン（lint + L1 ジョブの空枠）。
- T0.5 生 `bpy.ops` 検出の AST lint スクリプト。
- DoD: `uv sync` が通り、CI(lint/空test)が緑。

### M0.5: Phase 0 スパイク（実機検証・最優先）
- T0.5.1 `dump_capabilities.py`：5.0/4.4 実機で `bl_rna`/`dir(bpy.ops)`/`addon_utils` をダンプ → STL/OBJ/glTF/FBX/3MF/print3d の確定 operator 名・引数・module 名を JSON 出力。
- T0.5.2 `OperatorResolver` 候補表を実機結果で確定（research.md 表を更新）。
- T0.5.3 ディスパッチ最小PoC：別スレッド→`queue`→`timer`→`Event.wait` を 5.0/4.4 で安定性計測。不安定時のフォールバック方針確定。
- T0.5.4 `run_operator`/`temp_override`/`undo_push` の実呼び出し確認（origin_set 等）。
- DoD: 全 `[要実機検証]` 項目が解消、`research.md` に実測値反映。

### M1: コア（bli-core / SSOT）
- T1.1 `Command`/`Param` dataclass と `@command` 登録機構。
- T1.2 `to_json_schema()` / `validate_from_dict()`（純Python）。
- T1.3 `ErrorObject` / エラーコード定数 / 終了コード。
- T1.4 `schema_hash`（全コマンド定義のSHA256）。
- T1.5 プロトコルcodec：framing(`recv_exactly`, `struct.pack(">I")`, MAX_FRAME), JSON-RPC エンコード/デコード。
- DoD: L1 ユニット（schema生成・検証・codec往復・hash安定）緑。

### M2: 通信層（CLIクライアント ⇄ アドオンサーバ骨格）★
- T2.1 アドオン：listen socket（`SO_REUSEADDR`, 127.0.0.1）、受信スレッド、`select`ポーリング。
- T2.2 HELLO + token認証（`secrets`生成・`hmac.compare_digest`・非HELLO/HTTP様式は即切断）。
- T2.3 `session_lock`（fail-fast `SESSION_BUSY`）。
- T2.4 `RequestRegistry` + 冪等性（同一id再利用・状態返却）。
- T2.5 シャットダウン手順（unregister/atexit/load_pre、in-flightにSHUTTING_DOWN）。
- T2.6 CLIクライアント：connection.json解決（flag>env>file>9876）、frame送受、終了コード。
- T2.7 エコーハンドラで疎通（bpy不要）。
- DoD: ★CLI→HELLO→echo の E2E（L3）緑。`ping` 相当が通る。

### M3: アドオン実行基盤（bpy直列実行）
- T3.1 `bpy.app.timers` ディスパッチャ + `queue` + `Event` 待ち合わせ（R1）。
- T3.2 `BpyGateway` + `run_operator()`（temp_override/poll/FINISHED判定/undo_push, R2）。
- T3.3 `CapabilityRegistry`/`OperatorResolver`（M0.5成果を実装, R3）。
- T3.4 Target解決（session_uid>name>regex、実行直前再解決、fingerprint, W_STATE_DRIFT）。
- T3.5 required_mode 検証（E_MODE_MISMATCH、自動遷移しない）。
- T3.6 監査ログ（実行Python文字列を `audit/` 記録）。
- DoD: ハンドラ登録機構が動き、ダミーstableコマンド1本が実機で往復（L2）。

### M4: CLI骨格 & 診断コマンド
- T4.1 Typer app・グローバルフラグ（`--json`/`--id`/`--targets`/`--dry-run`）。
- T4.2 Pydanticラッパ（`bli-core` dataclass→Pydantic）と `model_json_schema()`。
- T4.3 `init` / `doctor` / `ping` / `request-status` / `job-status` / `job-wait`。
- T4.4 `help --json` / `list-commands --json`（SSOTから生成）。
- DoD: 上記コマンドが実機アドオン相手に動作（L2/L3）。`doctor` が能力診断を表示。

### M5: 情報取得
- T5.1 `scene-info`（階層/単位、大→output_ref退避 + sha256 + os.replace）。
- T5.2 `list-objects` / `object-info`（bbox/dims/transform/材質/modifier）。
- DoD: golden検証（既知シーンの数値）緑。

### M6: 汎用編集（オブジェクト）
- T6.1 `select` / `transform` / `apply-transform`。
- T6.2 `duplicate`（`copy()`+`data.copy()`+link）/ `delete`（backup/確認）。
- T6.3 `material`（assign/create/list）。
- T6.4 `modifier`（add/remove/list/apply：Mirror/Subsurf/Solidify/Decimate/Boolean）。
- DoD: 各コマンドのL2 + golden。生opsゼロをAST確認。

### M7: メッシュ編集（bmesh一次）
- T7.1 `mesh recalc-normals` / `merge-by-distance`（stable）。
- T7.2 `mesh extrude/bevel/inset`（experimental, セレクタ最小）。
- T7.3 `mesh boolean` / `mesh decimate`（heavy判定）。
- DoD: L2 + golden（頂点/面数）。

### M8: 3シナリオ（中核価値）
- T8.1 `set-origin`（geometry/cursor/world、make-single-userガード、行列直接計算フォールバック）。
- T8.2 `straighten`（reset/world-align/pca/floor、up-axis、bake-rotation）。
- T8.3 `print-setup`（unit=mm, global_scale一本化）。
- T8.4 `print-check`/`print-repair`（print3d enable、CAPABILITY_UNAVAILABLE縮退）。
- T8.5 `print-export`（stl/3mf、3mf不可→stl hint）。
- DoD: spec §10 受け入れ基準をgolden数値で満たす。3シナリオ経路は全stable。

### M9: ファイルI/O
- T9.1 `save`（backup）/ `open`。
- T9.2 `import`/`export`（stl/obj/gltf/fbx/3mf、OperatorResolver、global_scale）。
- DoD: 各形式の往復（export→import）でオブジェクト不変をgolden確認。

### M10: 非同期job & フリーズ対策
- T10.1 heavy コマンドの job化（job_id採番、accepted即返、job-status/wait）。
- T10.2 render busy 拒否（render_init/complete handler、BUSY_RENDERING）。
- T10.3 heartbeat watchdog（MAIN_THREAD_UNRESPONSIVE）。
- DoD: 重量import中も接続が塞がらない（L3）。

### M11: exec-python（既定off）
- T11.1 mode=off（`EXEC_DISABLED`）/ audited / trusted、設定昇格のみ。
- T11.2 AST ヒューリスティックflag（`security_guarantee:false`/`heuristic_flags`）。
- T11.3 audited の監査記録・承認/許可ハッシュ。
- DoD: 既定で無効、設定時のみ動作。監査ログ確認。

### M12: Skill同梱 & スキーマ同期
- T12.1 `.claude/skills/bli/SKILL.md`（frontmatter + 3シナリオ定石）。
- T12.2 `reference/cli-schema.json` 生成（`help --json` キャッシュ）。
- T12.3 `schema_hash` 同期検証（hello/help/skill一致）。
- DoD: スナップショットテストでドリフトfail検出。

### M13: テスト網羅 & CI仕上げ
- T13.1 L2 matrix（5.0/4.4、`fail-fast:false`、5.0リリースゲート）。
- T13.2 `bl_rna` 契約テスト（operator実在・引数）。
- T13.3 golden数値検証スイート（3シナリオ+主要編集）。
- T13.4 L3 プロトコルE2E（framing/handshake/冪等性/タイムアウト/SESSION_BUSY）。
- T13.5 schema snapshot。
- DoD: 全層CI緑、5.0必須ゲート。

### M14: ドキュメント & 配布
- T14.1 README / インストール手順（pipx + addon zip）。
- T14.2 addon zip ビルド（`bli-core` vendoring コピー、`blender_manifest.toml`）。
- T14.3 `doctor` 導入支援（addon未導入時のガイド）。
- T14.4 mistakes-memo 運用開始。
- DoD: クリーン環境で「導入→ping→3シナリオ」が再現。

### 依存関係（要約）
```
M0 → M0.5 → M1 → M2(★) → M3 → M4 → {M5,M6,M7} → M8 → M9 → M10 → M11 → M12 → M13 → M14
                                   （M5-M7は相互に概ね並行可。M8はM3/M6に依存）
```

---

## 5. セキュリティ要件（spec §6 準拠・実装規模=堅牢）

- **信頼境界の明示**: プロセス/FS境界。プロセス内sandboxは提供しない（Non-Goal、README/SKILL明記）。
- **bind固定**: `127.0.0.1` のみ。`0.0.0.0` は設定でも起動拒否（ガード節）。
- **token認証**: `secrets.token_urlsafe(32)`、所有者限定権限、`hmac.compare_digest`、非HELLO/HTTP様式は即切断。
- **exec既定off**: off/audited/trusted、設定ファイル（ユーザ所有）でのみ昇格。CLIフラグ単体で緩めない。
- **監査**: メインスレッド実行口の全Python文字列を `audit/` に記録。save/export/importはパス明示、.blend上書きはbackup強制。
- **秘匿情報**: token を `.bli/`/リポジトリに置かない。`.gitignore` 雛形同梱。コード/エージェント定義/フックにシークレットを書かない（security-guardrails準拠）。
- **依存**: `npx -y`相当の未検証自動取得をしない。addon依存はvendoring、バージョン固定。

---

## 6. テスト計画（4層 + CIマトリクス）

| 層 | 範囲 | 実行 | ゲート |
|----|------|------|--------|
| L1 純ユニット | bli-core（schema/codec/validate/hash）, ドメインロジック（bpyモック） | `pytest`（bpy不要） | 全PR必須・カバレッジ計測 |
| L2 bpy統合 | `BpyGateway`/handlers/resolver を `blender --background --python` で | matrix 5.0/4.4 | nightly + リリース（5.0必須） |
| L3 プロトコルE2E | framing/HELLO/auth/冪等/タイムアウト/SESSION_BUSY/shutdown | `pytest`（実addon or モックサーバ） | 全PR必須 |
| L4 GUIスモーク | 常駐GUIでの目視・3シナリオ手動 | 手動 | リリース前チェックリスト |

- **golden数値検証**: 3シナリオ + 主要編集で、既知入力→期待出力（座標/角度/頂点数/面数）を許容誤差つきで固定。「無言の誤結果」を捕捉。
- **bl_rna 契約テスト**: operator 実在・引数集合を 5.0/4.4 で検証。論点3の差分を継続監視。
- **schema snapshot**: `schema_hash` と `cli-schema.json` のスナップショット。SSOTドリフトでfail。
- テスト容易性は `BpyGateway` 分離で担保（ドメインは bpy 非依存）。

---

## 7. 次アクション

1. 本 plan.md を承認 → `implement-feature`（tasks.md 生成 → サブエージェント実装）へ。
2. 実装は **M0 → M0.5 スパイク** から着手（`[要実機検証]` を最優先で消化）。
3. ブランチ `feature/m0-bootstrap` から開始（main直接禁止）。

## 8. 主要リスクと対策（再掲・実装視点）
- `Event.wait` 不安定 → `time.sleep` ポーリング切替を `BpyGateway` 配下に抽象化（M0.5で判定）。
- operator改名/Extensions移管 → `OperatorResolver` に局所化、CIの`bl_rna`契約で検出。
- 3MF/print3d 未導入 → `CAPABILITY_UNAVAILABLE` + STLフォールバック。
- vendoring 失敗 → addon zip ビルドにvendoringテストを含める（M14/CI）。
- タイムアウト後の実行継続 → 同一id冪等 + `request-status` + 終了コード2。
