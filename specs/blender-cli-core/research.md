# bli (Blender CLI) — 技術調査統合 (research.md)

| 項目 | 内容 |
|------|------|
| 対象 | spec.md (blender-cli-core v1) の plan.md 策定土台 |
| 対象バージョン | Blender 5.0 主軸 / 4.4 ベストエフォート |
| 作成日 | 2026-06-13 |
| 入力 | 4本の技術調査（bpy timer/context API・import/export & print3d・Pydantic SSOT・Skill/packaging/CI） |
| 凡例 | 各論点を **Decision / Rationale / Alternatives considered** の3点でまとめる。`[要実機検証]` は plan.md で初期スパイク確認。 |

> 注: 調査は公式ドキュメント中心だが、一部 API ドキュメントが HTTP 403 で取得できず、5.0/4.4 の正確な引数差は実機未確証の箇所がある。該当は本文で `[要実機検証]` と明示する。

---

## 論点1: メインスレッド直列実行とタイムアウトの確定方針

### Decision
`bpy.app.timers` に固定間隔のディスパッチャを1本だけ登録する。受信スレッドは `queue.Queue` でリクエストを投入し、`threading.Event.wait(timeout)` で結果を待つ。timer コールバックは必ず `try/except/finally` で結果スロットを埋めて `Event.set()` する。`Event.wait()` は **受信スレッド側でのみ** 使い、**timer コールバック内では一切ブロックしない**。

擬似コード（確定方針）:

```python
# --- アドオン起動時（メインスレッド） ---
request_queue: queue.Queue = queue.Queue()
registry: dict[str, ResultSlot] = {}   # id -> {state, event, result}

def dispatcher():
    """timer。メインスレッドで固定間隔ポーリング。自己解除しない。"""
    try:
        while True:
            try:
                req = request_queue.get_nowait()
            except queue.Empty:
                break
            slot = registry[req.id]
            try:
                slot.result = run_handler(req)      # BpyGateway 経由で bpy 実行
                slot.state = "DONE"
            except Exception as e:                  # 全例外を握る
                slot.result = to_error(e)
                slot.state = "FAILED"
            finally:
                slot.event.set()                    # 必ず set（無言ハング防止）
    except Exception:
        log_exception()                             # timer 自体は絶対に死なせない
    return TIMER_INTERVAL                            # 例: 0.02〜0.05。Noneを返さない

bpy.app.timers.register(dispatcher, persistent=True)

# --- 受信スレッド（バックグラウンド） ---
def handle_request(req):
    slot = ResultSlot(event=threading.Event(), state="PENDING")
    registry[req.id] = slot
    request_queue.put(req)
    if slot.event.wait(timeout=READ_DEADLINE):      # 無限待ち禁止
        return slot.result                          # DONE / FAILED
    else:
        slot.state = "RUNNING"                       # 実体は走り続ける可能性
        return timeout_pending(req.id)               # 終了コード2 / request-status で後追い
```

タイムアウトは「キャンセル」ではなく「後追い回収」。クライアント timeout 後も bpy オペレータは中断不可で走り続けるため、同一 `id` の `RequestRegistry`（PENDING/RUNNING/DONE/FAILED, TTL揮発）で二重実行を防ぐ。

### Rationale
- `bpy.app.timers.register/unregister` はメインスレッドからのみ呼び出し可能。コールバックもメインスレッドで実行される。バックグラウンドスレッドから `bpy` を直接叩くと `RuntimeError` / クラッシュ。`queue.Queue` 越しの投入が公式推奨パターン。
- timer コールバックの戻り値 `None` は自己解除を意味するため返さない。`float` 秒を返して固定間隔で再スケジュールする。
- 調査では「`threading.Event.wait()` は Blender 内部の `BPy_BEGIN/END_ALLOW_THREADS`（GIL 解放/再取得）と競合し race condition の懸念」という指摘があった。ただし spec.md は受信スレッド側での `Event.wait(timeout)` を確定方針としている。**timer コールバック内でブロッキング待ちをしない**こと、**`Event.wait` は必ず timeout 付き**であることでデッドロック面を回避する。生 `time.sleep`+ポーリングはより保守的だが CPU 効率が落ちる。

### Alternatives considered
- **time.sleep + dict ポーリング**（調査の代替案）: `Event` の GIL 競合懸念を完全回避するが、レイテンシと CPU 効率が悪い。→ 不採用（spec の `Event.wait(timeout)` を維持）。ただし論点として残し、E2E で `Event` 方式の安定性を測る。
- **Condition / Semaphore**: 同じブロッキング問題を持ち、複数リクエストの順序保証が弱い。→ 不採用。
- **`bpy.app.handlers` 駆動**: 粒度が粗く（ファイルロード時点等）、汎用ディスパッチに不向き。→ 補助用途（load_pre 等のフック）に限定。

### `[要実機検証]`（plan.md: 実装初期スパイク）
- 受信スレッドの `Event.wait(timeout)` が 5.0/4.4 で長時間安定動作するか（GIL 競合の実害有無）。万一不安定なら `time.sleep` ポーリングへフォールバックする分岐を `BpyGateway`/ディスパッチ層に用意。
- `TIMER_INTERVAL` の最適値（応答性 vs アイドル CPU）。

---

## 論点2: temp_override / run_operator ラッパ / poll / undo_push

### Decision
すべての `bpy.ops` 呼び出しを **`run_operator()` ラッパ経由** に統一する（生呼び出しは CI の AST チェックで禁止）。ラッパは次を行う:

1. `ensure_context()` で `bpy.context.copy()` をベースに必要メンバ（`area`/`region`/`window`/`active_object`/`selected_objects`/`scene`/`view_layer`）を上書きした override dict を合成。
2. `op.poll(override)` を先行評価。False なら **実行せず** 原因（`no_active_object` 等）を構造化エラー化。
3. `with bpy.context.temp_override(**override):` 内で実行。
4. 戻り値 set を判定: **`'FINISHED' in result` のみ成功**。`'CANCELLED'` / `'RUNNING_MODAL'` は明示失敗化。
5. 1コマンド = 1 Undoステップ（`bpy.ops.ed.undo_push(message=...)` をコマンド境界で最小限）。

擬似コード:

```python
def run_operator(op, *, override=None, message=None, **kwargs) -> dict:
    ov = ensure_context(override)
    if not op.poll(ov):                              # poll 先行
        return error("E_PRECONDITION", cause=diagnose_poll(ov))
    with bpy.context.temp_override(**ov):
        result = op(**kwargs)                        # set が返る
    if 'FINISHED' not in result:                     # ==ではなく in
        return error("E_OPERATOR", cause=sorted(result))
    if message:
        bpy.ops.ed.undo_push(message=message)        # keyword-only
    return ok(result)
```

### Rationale
- **Blender 4.0+ で旧 `override_context=` dict 方式は廃止**。`bpy.context.temp_override()` context manager が 4.4/5.0 共通の唯一の正解。`bpy.context.copy()` ベースで「上書きしたメンバだけ差し替え、他は現在値維持」が安全・デバッグ容易。
- `bpy.ops.*()` の戻り値は **set**。`{'FINISHED'}` との `==` 比較は複合フラグ（`RUNNING_MODAL` 等）混入時に誤判定するため `in` 演算子で判定する。
- `poll()` は override を受け取れる。事前評価で「実行不可」を例外でなく構造化エラーに落とせる（spec の `E_MODE_MISMATCH` / `no_active_object` 分離に直結）。
- `bpy.ops.ed.undo_push(*, message=...)` は keyword-only。**内部用 API 扱い**で過剰呼び出しは undo 履歴破損リスク。`bpy.ops` 操作は自動で undo ステージを作るため、push は **コマンド境界の最小限**にとどめる。
- 5.0 限定で `temp_override(...) as ctx: ctx.logging_set(True)` により override 中にアクセスされたコンテキストメンバをログ出力できる。ラッパのデバッグモードで活用可能。

### Alternatives considered
- **例外ハンドリングのみ（poll 省略）**: 原因と症状の分離が雑になり `cause` を埋められない。→ 不採用。
- **複数 `temp_override` ネスト**: merge されず可読性も低下。→ 単一 override dict 合成に統一。
- **`bpy.ops` 全面回避（bpy.data / bmesh 直接）**: 複製・モディファイア・mesh 編集では spec も `bpy.data`/`bmesh` 直接を一次手段とする方針。ただし print3d・I/O・origin_set 等は operator が唯一手段のため `run_operator` を残す。

### `[要実機検証]`
- `undo_push` は内部 API のため 5.0/4.4 でメッセージ引数・挙動が一致するか。→ スパイクで `bl_rna` introspection と実呼び出しを確認。
- origin_set 等で `temp_override` に最低限必要なメンバ集合（4.4 と 5.0 で差があるか）。

---

## 論点3: import/export & 3D-Print Toolbox のオペレータ名と能力検出

### Decision
バージョン番号でなく **能力検出（capability-based dispatch）** で operator を解決する。`OperatorResolver` に「候補リスト（優先順）＋引数マップ」を持たせ、`bl_rna` / `dir(bpy.ops.<ns>)` / `addon_utils` で実在を確認して選ぶ。確定マッピング:

| 形式 | 5.0 推奨 | 4.4 | 解決方針 |
|------|----------|-----|----------|
| **STL** | `wm.stl_export` / `wm.stl_import` | 新旧併存（`wm.stl_*` 優先、旧 `export_mesh.stl`/`import_mesh.stl`） | 新→旧フォールバック |
| **OBJ** | `export_scene.obj` / `import_scene.obj`（ネイティブ） | 同左 | 4.0+ でネイティブ。単一候補 |
| **glTF/GLB** | `export_scene.gltf` / `import_scene.gltf`（bundled） | 同左 | `export_format='GLB'`。単一候補 |
| **FBX import** | `wm.fbx_import`（C++、5.0 既定） | `import_scene.fbx`（Python）。`wm.fbx_import` は 4.5+ で導入 `[要実機検証]` | 新(`wm.fbx_import`)→旧(`import_scene.fbx`) |
| **FBX export** | `export_scene.fbx` | 同左 | `wm.fbx_export` は無い。単一候補 |
| **3MF** | Extension `io_mesh_3mf`（`import_mesh.3mf`/`export_mesh.3mf`）`[要実機検証]` | 同左 | addon 有効化必須。無ければ `CAPABILITY_UNAVAILABLE` |
| **3D Print** | Extension `print3d_toolbox`（旧 `object_print3d_utils`）`[要実機検証]` | 同左 | addon 有効化必須 |

能力検出ヘルパ:

```python
def has_operator(path: str) -> bool:      # 例: "wm.stl_export"
    ns, name = path.split(".")
    return hasattr(getattr(bpy.ops, ns, None), name)

def ensure_addon(module: str) -> bool:    # 例: "print3d_toolbox"
    if module in bpy.context.preferences.addons:
        return True
    try:
        import addon_utils
        addon_utils.enable(module, default_set=True)
        return module in bpy.context.preferences.addons
    except Exception:
        return False
```

print3d 主要 operator（`mesh.print3d_*` 名前空間）: `print3d_check_all` / `print3d_check_solid` / `print3d_check_non_manifold` / `print3d_check_intersections` / `print3d_check_degenerate` / `print3d_check_distorted` / `print3d_clean_non_manifold` 等。

3MF/FBX の可否と代替:
- **3MF**: コアに無く Extensions（4.2 LTS 以降）。未導入なら `print-export --format stl` に誘導（大半のプリンタは STL 受容）。
- **FBX**: import は 5.0 で C++ 版が既定、4.4 は Python 版。export は両版とも `export_scene.fbx`。能力検出で吸収。

### Rationale
- 4.0 で OBJ/PLY、5.0 で STL 旧アドオンが legacy 化。`wm.*` 系ネイティブ operator へ移行が進む。番号ハードコードは将来のマイナー改名に追従できないため `OperatorResolver` に局所化（spec §9 と一致）。
- 3MF・3D Print Toolbox は 4.2 LTS 以降 **Extensions プラットフォームへ移管**され既定バンドルされない。実行前の `addon_utils.enable` と `CAPABILITY_UNAVAILABLE` 応答が必須。
- 単位は **`global_scale` を唯一の真実**とし、`scene.unit_settings.scale_length` は検証専用（1000倍ずれ防止）。export 時の幾何スケールは operator の `global_scale` が支配する。

### Alternatives considered
- **バージョン番号分岐 (`if bpy.app.version >= (5,0)`)**: 内部改名・Extensions 移管に脆い。→ 禁止（spec D8/§9）。
- **3MF を自前実装/fork**: 過剰。STL フォールバックで v1 要件は満たせる。→ Deferred。
- **FBX を旧 Python importer 固定**: 5.0 の高速 C++ importer の利点を捨てる。→ 新優先フォールバック採用。

### `[要実機検証]`（最重要 / 初期スパイク必須）
- `wm.stl_export` / `wm.stl_import` の **正確な引数集合**（旧 `export_mesh.stl` との差）。API ドキュメントが 403 で未確証。
- 4.4 で `wm.fbx_import` が backport されているか（4.5+ のみの可能性）。
- 3MF operator の正確な namespace（`import_mesh.3mf` か別名か）と addon module 名。
- `print3d_toolbox` の確定 module 名（`object_print3d_utils` からの改名）と各 operator 名・property API（`scene.print_3d`）。
- → plan.md: 「能力検出スパイク」を最初のタスクに置き、5.0/4.4 実機で `bl_rna` をダンプして `OperatorResolver` の候補表を確定させる。

---

## 論点4: SSOT（コマンド定義）の確定方針 ← 最重要・明確結論

### Decision
**ハイブリッド SSOT を採用する。**

- **唯一の情報源 = 共有コア `bli-core` の純 Python `dataclass` 定義**（pip 依存ゼロ）。
- **CLI 側（別プロセス、システム Python 3.10/3.11+）は Pydantic v2 可**。`bli-core` の dataclass をラップしてリッチ検証・`model_json_schema()` 生成に使う。
- **アドオン側（Blender 埋め込み Python）は Pydantic 禁止**。`bli-core` を vendoring 同梱し、純 Python の `validate_from_dict()` / `to_json_schema()` で検証。
- **`Pydantic をアドオンに同梱しない`** ことを確定結論とする。

共有コアの物理配置（monorepo）:

```
blender-auto-cli/
├── pyproject.toml                 # uv workspace root
├── packages/
│   ├── bli-core/                  # ★SSOT。dependencies = []（純Python）
│   │   ├── pyproject.toml
│   │   └── src/bli_core/
│   │       ├── commands.py        # @command + dataclass 定義（唯一の真実）
│   │       ├── schema.py          # to_json_schema() / validate_from_dict()
│   │       └── types.py           # Literal/enum 共有
│   ├── bli-cli/                   # CLI。pydantic 可
│   │   └── src/bli/...            # Pydantic ラッパ → model_json_schema()
│   └── bli-addon/                 # アドオン。pydantic 不可
│       ├── blender_manifest.toml
│       └── src/bli_addon/
│           └── vendored/bli_core/ # ★ビルド時に bli-core をコピー同梱
└── ...
```

ビルド時に `bli-core` を `bli-addon/.../vendored/bli_core/` へコピーし、アドオン側は `sys.path` 追加で読む。`schema_hash`（SHA256）を hello/help に載せ、CLI 生成スキーマとコア定義のドリフトを CI のスナップショットテストで検出（spec §11）。

### Rationale
- **致命的制約**: Pydantic v2 は `pydantic-core`（Rust/PyO3 ネイティブ拡張）に依存し、**純 Python フォールバックを持たない**。Blender 埋め込み Python に同梱すると glibc/OS/Python 版に密結合した ABI 互換性リスクが高い。Blender 4.2+ は user `site-packages` を `sys.path` から外したため、依存解決もさらに不安定。→ アドオンに Pydantic を入れない、が確定。
- **dataclass を唯一の真実**にすれば、CLI（Pydantic）とアドオン（純 Python）が同一定義を参照でき DRY を満たす。`typing.get_type_hints()` + `dataclasses.fields()` で JSON Schema を自前生成できる。
- CLI は別プロセス・通常 Python なので Pydantic の表現力をフル活用できる。spec §11 の「Pydantic v2 + `@command` で SSOT、`help --json` は `model_json_schema()` 生成」と整合させつつ、**コア定義は dataclass、Pydantic は CLI 側ラッパ**という層分けで実機制約を解消する。
- Python 3.10（CLI 環境想定）と 3.11/3.13（Blender）間は構文互換良好。ただし `tomllib`（3.11+）/`typing.Self`（3.11+）は `bli-core` で使わず 3.10 互換に保つ。

### Alternatives considered
- **全 Pydantic（アドオンにも同梱）**: `pydantic-core` の ABI 互換性リスクが過大。Blender バージョン更新で wheel 破損の可能性。→ **不採用（最重要結論）**。
- **全 attrs / marshmallow（純 Python）**: アドオン側は安全だが CLI 側の検証/スキーマ生成の表現力で Pydantic に劣る。→ 不採用。
- **msgspec**: ネイティブ拡張のため Pydantic と同じ ABI 問題。→ 不採用。
- **YAML/JSON 外部宣言を SSOT**: コード生成の手間と同期リスク。型安全が弱い。→ 不採用。
- **`bl_rna` introspection を SSOT**: operator スキーマは実機由来で有用だが、CLI コマンド定義の SSOT には不向き（起動コスト・キャッシュ必要）。→ 能力検出には使うが SSOT にはしない。

### `[要実機検証]`
- `bli-core` の vendoring を `blender_manifest.toml` 配布物に正しく含められるか（Extensions/zip 双方）。
- `to_json_schema()` 自前生成器が `model_json_schema()`（Pydantic）出力と意味的に一致するか（ドリフトテストで担保）。

---

## 論点5: Claude Skill 配布形式 と monorepo 構成

### Decision
Claude Code Skill を **`.claude/skills/bli/SKILL.md`** にリポジトリ同梱（git 追跡）し、自動発見させる。SKILL.md は **YAML frontmatter + Markdown 本文**の二部構成。`bli --help --json` で得た CLI スキーマを `reference/cli-schema.json` にキャッシュ同梱し、毎回 CLI を叩かずに済ませる。

frontmatter（確定形）:

```yaml
---
name: bli
description: |
  Blender自動化CLI (bli)。エージェント向けに構造化コマンドで
  原点変更・直立補正・3Dプリンタ対応などを実行。
  詳細は bli help --json でオンデマンド取得。
allowed-tools: [Bash, Read, Glob]
---
```

monorepo 構成（uv workspace / src-layout / PEP 621）— spec の3層（CLI / core / addon）と一致:

```
blender-auto-cli/
├── .claude/skills/bli/
│   ├── SKILL.md                   # frontmatter + 3シナリオ定石
│   └── reference/cli-schema.json  # help --json のキャッシュ
├── packages/
│   ├── bli-core/                  # 共有 dataclass SSOT（依存ゼロ）
│   ├── bli-cli/                   # Typer CLI（entry point: bli = "bli.main:app"）
│   └── bli-addon/                 # Blender addon（TCPサーバ + bpy）
├── .github/workflows/test-blender.yml
├── pyproject.toml                 # [tool.uv.workspace] members=[...]
└── uv.lock
```

### Rationale
- Skill は起動時に frontmatter の `name`/`description` のみプリロードし、関連プロンプト時に本文を遅延ロード。MCP の常時スキーマロードよりトークン効率が高く、spec の「オンデマンド発見」コンセプトに合致。
- `.claude/skills/` を git 追跡すればチーム全体に自動配布される。Skill 本文（定石）と `help --json`（詳細スキーマ）を同一 SSOT（`bli-core`）から生成し `schema_hash` で同期検証。
- uv workspace は単一 venv に全パッケージを解決でき、`bli-core` の workspace 参照で DRY を実現。`[project.scripts] bli = "bli.main:app"` で pipx 配布（spec 確定）に直結。Typer がサブコマンド階層を表現。

### Alternatives considered
- **`.claude/commands/`（旧スラッシュコマンド）**: 互換はあるが遅延ロード/メタデータ分離の恩恵が薄い。→ Skill を一次。
- **AGENTS.md に全コマンド記載**: 起動時全文ロードでトークン非効率。→ Skill の遅延ロードを優先。
- **MCP ブリッジを一次発見導線**: ツールスキーマ常時ロードでトークン大（spec の non-goal 背景）。→ 将来の薄いクライアントとしてのみ予約。
- **単一パッケージ（非 monorepo）**: addon と CLI の依存・Python 版が異なるため分離が自然。→ workspace 採用。

### `[要実機検証]`
- 最新の Claude Code が認識する frontmatter フィールド（`disable-model-invocation` / `allowed-tools` 等）の正確な仕様。導入時に最新ドキュメントで確認。
- `cli-schema.json` キャッシュの鮮度管理（`schema_hash` 不一致時の再生成導線）。

---

## 論点6: CI で `blender --background` を 5.0+4.4 マトリクス実行

### Decision
GitHub Actions の matrix で **Blender 5.0 × 4.4 を並行**実行する。`BradyAJohnston/setup-blender` 系 action で各バージョンを取得・キャッシュし、`blender --background --python <test>` で L2（bpy 統合）テストを回す。L1 純ユニット（bpy モック）は全 PR ゲート、L2 は 5.0/4.4 マトリクス（nightly）、リリースゲートで 5.0 成功を必須化（spec §11）。

```yaml
name: test-blender
on: [push, pull_request]
jobs:
  unit:                                    # L1: 全PRゲート（bpyモック）
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv sync && uv run pytest packages/bli-cli packages/bli-core

  bpy-integration:                         # L2: 5.0 + 4.4 マトリクス
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest]
        blender-version: ["4.4", "5.0"]    # 5.0 主軸 / 4.4 ベストエフォート
    steps:
      - uses: actions/checkout@v4
      - uses: BradyAJohnston/setup-blender@v5
        with:
          version: ${{ matrix.blender-version }}
      - name: Capability dump + contract tests
        run: |
          blender --background --python packages/bli-addon/tests/dump_capabilities.py
          blender --background --python packages/bli-addon/tests/test_contract.py
```

L2 テストでは `bl_rna` 契約テスト（operator 実在・引数集合）とシナリオのゴールデン数値検証を行い「無言の誤結果」を捕捉。

### Rationale
- `setup-blender` action が自動 DL・キャッシュ（初回〜数分、以降数十秒）し、複数バージョン並行が低コスト。5.0/4.4 の API 差を `bl_rna` 契約テストで自動検出でき、論点3の `[要実機検証]` 項目を CI で継続監視できる。
- `--background` は GUI 不要でヘッドレス実行可能。spec の「GUI 自動テストは手動スモークに留める（L4）」と整合し、L2 は operator/契約レベルに絞る。
- `fail-fast: false` で 4.4（ベストエフォート）失敗が 5.0（主軸）ジョブを巻き込まない。リリースゲートは 5.0 成功のみ必須。

### Alternatives considered
- **Xvfb で GUI 込みテスト**: 安定再現が難しい（spec non-goal）。→ `--background` に限定、GUI は手動 L4。
- **Docker 自前イメージで Blender 固定**: 柔軟だが setup action のキャッシュ/メンテ性に劣る。→ action 採用、必要時のみ Docker 検討。
- **TCP E2E を L2 に混在**: framing/handshake/冪等性は bpy 不要の L3 として分離した方がデバッグしやすい。→ L3 を別ジョブ化。

### `[要実機検証]`
- `setup-blender` action が 5.0 正式版・4.4 LTS を解決できるか（バージョン文字列の正確な指定）。
- ヘッドレスで 3MF/print3d Extension を CI 内から `addon_utils.enable` 可能か（拡張取得経路）。可否で論点3のテスト範囲が変わる。

---

## 実装計画への含意（plan.md へのインプット）

- **最初のスパイク = 能力検出ダンプ**: 5.0/4.4 実機で `bl_rna` をダンプし、STL/FBX/3MF/print3d の確定 operator 名・引数集合・addon module 名を `OperatorResolver` 候補表として固める。論点3の `[要実機検証]` を全消化する初期タスクに置く。
- **ディスパッチ層の安定化スパイク**: `Event.wait(timeout)` 方式を 5.0/4.4 で長時間検証。不安定なら `time.sleep` ポーリングへ切替可能な抽象を `BpyGateway` 配下に用意。
- **`run_operator()` ラッパを最初期に実装**: poll 先行 + `temp_override` + `'FINISHED' in result` + 最小 `undo_push`。生 `bpy.ops` 禁止を AST チェックで CI 強制。
- **SSOT は層分けで確定**: `bli-core` = 純 Python dataclass（唯一の真実）、CLI = Pydantic ラッパ、addon = vendoring + 自前検証。**Pydantic はアドオンに入れない**を不変条件として plan.md に明記。
- **monorepo を uv workspace で初期化**: `packages/{bli-core,bli-cli,bli-addon}` + `.claude/skills/bli/`。`bli-core` のビルド時 vendoring コピー手順を build スクリプト化。
- **CI は 3 段**: L1（全 PR・bpy モック）/ L2（5.0+4.4 マトリクス・`bl_rna` 契約＋ゴールデン）/ L3（TCP E2E）。リリースゲートで 5.0 必須。`schema_hash` スナップショットで SSOT ドリフトを fail。
- **単位は `global_scale` 一本化**: `scale_length` は検証専用。export 系コマンドのパラメータ設計に反映。
- **3MF/print3d 未導入時の縮退**: `CAPABILITY_UNAVAILABLE`（hint 付き）＋ STL フォールバック導線を I/O・print コマンドに組み込む。
- **未確証リスクの扱い**: 本書の `[要実機検証]` は plan.md の「Phase 0 スパイク」として独立タスク化し、確定後に各コマンド実装へ着手する依存順にする。

---

## 付録: 実機検証結果（M0.5 スパイク / 2026-06-13）

実機: Blender **5.0.1**（Python 3.11.13）/ **4.4.3**。Windows 11。
ダンプ生データ: `packages/bli-addon/spikes/out/capabilities-{5-0,4-4}.json`。

### A. operator 実体マッピング（確定）

| 形式 | 5.0 | 4.4 | 確定方針 |
|------|-----|-----|----------|
| STL export | `wm.stl_export` ✅real | ✅real | **両対応 `wm.stl_export`**（旧 `export_mesh.stl` は両方 stub） |
| STL import | `wm.stl_import` ✅real | ✅real | **両対応 `wm.stl_import`** |
| OBJ export | `wm.obj_export` ✅real | ✅real | **両対応 `wm.obj_export`**（旧 `export_scene.obj` は両方 stub）→ research 本文の訂正 |
| OBJ import | `wm.obj_import` ✅real | ✅real | **両対応 `wm.obj_import`** |
| glTF | `export_scene.gltf` / `import_scene.gltf` ✅real | ✅real | 両対応。`export_format='GLB'` |
| FBX export | `export_scene.fbx` ✅real | ✅real | 両対応（`wm.fbx_export` は両方 stub） |
| FBX import | `wm.fbx_import` ✅real | ❌stub | **唯一の版差**: `wm.fbx_import`(5.0)→`import_scene.fbx`(両対応) フォールバック |
| 3MF | `*_mesh.3mf` ❌stub | ❌stub | 標準に実体なし。**STL フォールバック**。要 addon（M8で調査） |
| print3d | `mesh.print3d_*` ❌stub | ❌stub | 既定で未提供。`enable("print3d_toolbox"/"object_print3d_utils")` 両方 False。**実モジュールid不明 → M8 で Extensions id を特定** |

> **重要な実装規約**: operator の実在判定は `hasattr(bpy.ops.ns, name)` では不十分（旧名が stub として残り True を返す）。必ず `op.get_rna_type()` 成功で「実体あり」を判定する。`OperatorResolver` はこの方式を採用する（research 論点3 の `has_operator` を修正）。

### B. origin_set / transform_apply（確定）
- `object.origin_set`: props = `type`(ENUM), `center`(ENUM)。5.0/4.4 同一。
- `object.transform_apply`: props = location/rotation/scale/properties/`isolate_users`。**`isolate_users` は 4.4 にも存在**（5.0 新ではない＝本文訂正）。
- background でも `origin_set.poll()=True`、`temp_override(active_object, selected_objects, object)` で `origin_set` / `transform_apply` が `{'FINISHED'}`。**temp_override 最小メンバ = {active_object, selected_objects, object}**。
- 直接行列フォールバック（`obj.data.transform(Matrix.Translation(delta))` + `matrix_world.translation`）で world 原点指定が機能。
- `ed.undo_push(message=...)` は 5.0 で正常動作。

### C. ディスパッチ安定性（論点1 の確証）
- PoC（別スレッド→queue→メイン drain→bpy 読み→`Event.wait(timeout)`）を N=500 実行。
- 5.0: done=500 / timeouts=0 / errors=0 / max latency 5.69ms。**STABLE**。
- 4.4: done=500 / timeouts=0 / errors=0 / max latency 7.11ms。**STABLE**。
- 結論: `threading.Event.wait(timeout)` 方式は両版で安定。**GIL 競合の実害は観測されず**、`time.sleep` フォールバックは v1 で不要（抽象は残すが既定は Event 方式）。
- 注意: 本 PoC は `--background` のため `bpy.app.timers` の実発火ではなく、メインスレッドの手動 drain ループで近似した。**GUI 常駐時の `bpy.app.timers` 実発火は M2 の実機スモーク / L4 で別途確認する**。

### D. 単位
- 既定 METRIC / scale_length=1.0 / METERS（5.0/4.4 同一）。3Dプリント時は mm へ設定（M8）。

### E. bmesh-on-data メッシュ編集（M7 T7.1 スパイク / 2026-06-15・5.0.1/4.4.3 同値）
- **OBJECT モードのまま** mesh データを編集できる（edit mode トグル不要・context 非依存）。フロー: `bmesh.new()` → `bm.from_mesh(obj.data)` → `bmesh.ops.<op>(bm, ...)` → `bm.to_mesh(obj.data)` → `bm.free()` → `obj.data.update()`。`bpy.context.mode == "OBJECT"` のまま完了。
- `bmesh.ops.recalc_face_normals(bm, faces=bm.faces)` で法線を一貫化（巻き順修正・outward）。`bmesh.ops.reverse_faces(bm, faces=bm.faces)` で内向き化（`--inside`）。両 op とも 5.0.1/4.4.3 に存在。
- **flipped 統計**（操作前後で法線の向きが反転した面数）は決定的: clean cube を outward recalc → flipped=0 / 1 面だけ不整合 → flipped=1 / clean を inside → flipped=6（全面）。recalc は面を増減しないので index 対応で数えられる。
- `bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=...)` の **戻り値は None**（dict ではない）。マージ数は頂点数 before/after の差で算出する。重複頂点 1 個 + dist=0.001 → merged=1（9→8）/ cube に dist=3.0 → 全頂点 collapse（8→2）。
- スパイク: `packages/bli-addon/spikes/bmesh_spike.py`（`BLI_BMESH_SPIKE_BEGIN/END`）。両版で出力一致。
