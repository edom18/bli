# 次の作業 — M5「情報取得」キックオフ

> **状態: M5 実装完了。`feature/m5-info` に2コミット → PR #2 レビュー中（2026-06-14）。**
> 判断3点は確定（type=freeform STR / GC は M10 へ繰越 / CLI 既定=参照のみ・`--fetch` で展開）。
> 実装サマリ・繰越は HANDOFF §6c を参照。マージ後はこのファイルを M6 用に更新 or 削除する。
> 以下は着手時のキックオフ資料（参照用に残置）。

最終更新: 2026-06-14 / 前提: PR #1 で M0–M4 が main にマージ済み（`uv run pytest` = 79 passed）。

> まず `.handoff/HANDOFF.md`（全体史 + 規約）を読み、その後この1枚で M5 に着手する。
> 出典: `specs/blender-cli-core/plan.md §4 M5` / `spec.md §(コマンド表)` / `data-model.md §5 OutputRef` / `contracts/methods.md`。

---

## 0. 着手前（コピペ可）
作業ブランチ `feature/m5-info`（main 由来）は本資料と一緒に作成済み。新規セッションはこの上で継続する。
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout feature/m5-info        # 別ブランチにいる場合。既にいれば不要
uv sync
PYTHONUTF8=1 uv run pytest -q                 # 79 passed を確認（ベースライン緑）
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
```
> ブランチ未作成なら: `git checkout main && git pull origin main && git checkout -b feature/m5-info`

## 1. ゴール（plan.md §4 M5）
- **T5.1** `scene-info`: 既存の inline 返却に加え、**大きい結果はファイル退避（output_ref）**。temp→`os.replace()`・sha256・CLI 側検証。
- **T5.2** `list-objects`（新規）/ `object-info` に **bbox** 追加。
- **DoD**: 既知シーン（`--background` 既定の Cube/Light/Camera）に対する **golden 数値検証**が緑。L1/L3 + 実機 smoke を追加。

## 2. 現状（M3 で既にあるもの / M5 で足すもの）
| 項目 | 現状 | M5 ですること |
|---|---|---|
| `scene-info` | `ops._scene_info` + `gateway.scene_summary`（inline のみ・envelope の `output_ref` は常に None） | 64KiB 超で output_ref 退避 |
| `object-info` | `ops._object_info` + `gateway.object_summary`（loc/dims/rot/scale/verts/polys/mesh_users/modifiers/materials） | **bbox（world min/max）追加** |
| `list-objects` | **無し** | 新規（type/regex フィルタ一覧） |
| `output_ref` | envelope フィールドは存在（`ops._ok` で常に None） | 退避機構を実装し設定 |

## 3. タスク分割（推奨順）

### A. `object-info` に bbox 追加（小さく確実）
- `gateway.object_summary`（`packages/bli-addon/src/bli_addon/gateway.py`）に world bbox を追加。
  - `corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]`（8隅をワールド変換）。
  - `bbox = {"min": [round(min(x),6)...], "max": [...], "size":[max-min...]}`。
  - `mathutils.Vector` は lazy import（既存の `set_origin_world` と同様）。
- golden: 既定 Cube は world bbox min=[-1,-1,-1] max=[1,1,1] size=[2,2,2]。
- 影響: `object_fingerprint` は `object_summary` を内包するので **fingerprint が変わる**（テストでハードコードしている値があれば更新。smoke_ops は fp を表示するだけなので可）。

### B. `list-objects`（新規コマンド）
- `bli_core/definitions.py`: `command("list-objects", ..., params=(type?, regex?), required_mode=Mode.OBJECT)`。
  - `type`: `ParamType.STR`（MESH/CURVE/EMPTY/LIGHT/CAMERA 等の freeform）か ENUM。**要判断**（freeform 推奨：版差・将来型に強い）。
  - `regex`: `ParamType.STR`（名前パターン）。
- `gateway.py`: `list_objects(type_filter, regex) -> list[dict]`（軽量サマリ: name/type/location 程度。重い object_summary 全部は不要かは判断）。
- `ops.py`: `_list_objects` ハンドラ + `_BPY_HANDLERS` に登録。param 検証 + mode 検証は既存パターン踏襲。
- `bli/main.py`: `list-objects` サブコマンド（`_rpc` 利用）。
- golden: 既定シーンで type=MESH → ["Cube"] のみ等。

### C. `scene-info` の output_ref 退避（M5 の山場）
- 仕様（data-model §5 / spec §出力退避）:
  - `INLINE_THRESHOLD = 64 KiB`。未満は従来どおり inline。超過は shared-fs 退避。
  - OutputRef = `{id, transport: "inline"|"shared-fs", path, size, sha256, encoding:"utf-8", schema:"scene-info/v1"}`。
  - 書込は **temp → `os.replace()`** でアトミック。退避先 `outputs/<id>.json`（**gitignore 済み**・配下パス検証必須）。
  - CLI は sha256 検証 → 不一致は **`STALE_OUTPUT`**（ErrorCode に既存）。
  - GC: TTL 24h / 200件 / 200MiB（**最小実装 or 後続でも可**。最初は退避 + 検証を優先）。
- 配置案（純Python・bli-core 共有が要るため）:
  - `bli_core/output_ref.py`（新規・純Python）: `INLINE_THRESHOLD`、`sha256_of(bytes)`、`build_descriptor(...)`、`maybe_offload(id, schema, data, writer) -> (inline|None, output_ref|None)`、CLI 用 `load_verified(output_ref) -> data`。
  - `bli_core/runtime.py`: `outputs_dir()`（`BLI_STATE_DIR/outputs` 既定・テストは env 差替）。`_atomic_write` 相当は server.py にあるので共通化を検討。
  - `ops._scene_info`: 結果 JSON が閾値超なら退避し、envelope の `data=None`/`output_ref=<desc>` に。閾値未満は従来どおり `data=<...>`/`output_ref=None`。
  - `bli/main.py`（`_rpc` か scene-info 側）: `output_ref.transport=="shared-fs"` のとき path を読み sha256 検証 → 失敗は `STALE_OUTPUT` 扱いで exit 1（or 専用）。`--json` では output_ref を素通しし、人間向けは要約 + パス表示。
- **判断ポイント（着手時に決める）**: ①`type` を ENUM/STR どちらにするか ②output_ref の GC を M5 でやるか後続か ③CLI は退避ファイルを常に読むか「参照だけ返す」か（エージェント向けはオンデマンド取得が本来。既定は参照を返し `--inline`/`--fetch` で展開、が筋）。

### D. テスト & 検証（各タスクで緑を維持）
- L1: `output_ref` の閾値/ sha256 / 退避往復（bli-core 純Python・bpy不要で書ける）。`list-objects` の param 検証（ops は bpy 遅延 import なので検証パスは bpy 無しで到達可）。
- L3: 退避を伴う scene-info の E2E（synthetic 大データを inject、または閾値を小さく monkeypatch）。
- 実機: `spikes/smoke_ops.py` を拡張（list-objects / object-info bbox の golden）。`PYTHONUTF8=1` + Blender 5.0/4.4 両方。
- **parity テスト**（`test_models_parity.py`）は全 COMMANDS を走査するので list-objects も自動対象。新 param 型が parity を壊さないか確認。

## 4. 必ず守る規約（HANDOFF §8 再掲）
- **bli-core は純Python・依存ゼロ / 3.10 互換**（Pydantic は CLI のみ）。`output_ref.py` も純Python。
- **AST guard**: `bpy.ops.*()` は `gateway.py` のみ。bbox/list-objects は `bpy.data`/`matrix_world` 読みなので gateway に置けば OK（生 ops 不要）。
- **ops は gateway/bpy を遅延 import**（pytest で bpy 無しでも param/mode 検証に到達できる構造を維持）。
- ruff modernization に注意（`X | None` / `@cache` / `zip(strict=)` 等は CI 緑にしてから commit）。
- schema_hash は list-objects 追加で変わる（ピン留めテストは無いので問題なし。CLI/addon は同一 SSOT から算出で一致）。

## 5. 仕上げ（前回 PR と同じ運用）
1. 機能ごとに日本語コミット（feat/fix/docs prefix・Co-Authored-By 付与）。`git add -A` 不使用・意図単位。
2. `feature/m5-info` を push → `gh pr create --base main`。
3. Codex レビュー（`@codex review`）→ 指摘対応 → push → 再依頼のループ。マージはユーザー判断。
4. 完了後、この `NEXT-M5.md` を次マイルストーン用に更新 or 削除し、HANDOFF.md の進捗表を更新。

## 6. 参照
- `specs/blender-cli-core/plan.md §4`（ロードマップ）/ `tasks.md`（M0–M4 実績 + 追補ログ）
- `specs/blender-cli-core/data-model.md §5 OutputRef`・`spec.md`（出力退避 / コマンド表）
- `contracts/methods.md`（scene-info / list-objects / object-info の params・result）
- 実装参考: `gateway.object_summary` / `scene_summary`、`ops._scene_info` / `_object_info`、`bli/main.py` の `_rpc`/`_call_or_exit`。
