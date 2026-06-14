# 次の作業 — M6「汎用編集」T6.3（material：assign / create / list）

最終更新: 2026-06-14 / 前提: M0–M5 と **M6 T6.1（select/transform/apply-transform）・T6.2（duplicate/delete）は main にマージ済み**（PR #3・#4）。
作業ブランチ `feature/m6-material`（main 由来）作成済み。

> まず `.handoff/HANDOFF.md`（全体史 + 規約 + §6d M6 + **§6e 再利用パターン**）を読み、その後この1枚で T6.3 に着手する。
> 出典: `plan.md §4 M6 / §6.3` / `contracts/methods.md §汎用編集` / `spec.md §汎用編集`。

---

## 0. 着手前（コピペ可）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout feature/m6-material        # 既にいれば不要
uv sync
PYTHONUTF8=1 uv run pytest -q                       # 121 passed（ベースライン緑）
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
# 実機スモーク（任意・回帰確認）:
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background \
  --python packages/bli-addon/spikes/smoke_ops.py 2>&1 \
  | sed -n '/BLI_OPS_SMOKE_BEGIN/,/BLI_OPS_SMOKE_END/p'   # → OPS SMOKE OK
```
> ブランチ未作成なら: `git checkout main && git pull origin main && git checkout -b feature/m6-material`

## 1. M6 全体の確定方針（再掲）
- **サブPR分割**: T6.1 ✅ → T6.2 ✅ → **T6.3 material（今ここ）** → T6.4 modifier。各 PR を小さく緑に。
- T6.1/T6.2 で確立した**再利用パターンは必ず踏襲**（HANDOFF §6e）: `_guard_shared_mesh` / `_require_input` / `--targets` オプション / `resolve_targets`（不正regex は USER_INPUT 済み）/ presence-sensitive フラグは schema default なし / **数値の有限性はサーバ側 `schema._check_type` で弾く** / 暴走上限は bli-core 定数集約 / 出力は決定的順序 / bpy 接点は gateway 集約（AST guard）。

## 2. T6.3 スコープ（plan.md §4 / methods.md / spec §汎用編集）
| method | params | result | M | Mode |
|---|---|---|:-:|---|
| `material` | `--action assign\|create\|list` `--targets?` `--name?` `--color r,g,b,a?` | 材質状態 | ✓ | OBJECT |

- `list`: 対象オブジェクトのマテリアルスロット一覧（name + base color）を返す。**読み取り的**だが material コマンド全体は `mutates=True`（create/assign が変更するため）。
- `create`: 新規マテリアルを作る（`bpy.data.materials.new` + Principled BSDF の Base Color を `--color` で設定）。判断3次第で `--targets` に割り当てる。
- `assign`: 既存の名前付きマテリアルを対象に割り当てる（無ければエラー）。

## 3. 着手時に決める判断ポイント（T6.3 キックオフで確認）
1. **action の表現**: `--action`（ENUM: assign/create/list）に統一する（positional 不可・全 params は `--option` という確立方針）。
   - 推奨: `--action` ENUM。**要ユーザー確認なし想定だが念のため**。
2. **VEC4（RGBA）の導入**: `--color r,g,b,a` のため `ParamType.VEC4` を新設する。影響範囲は **bli-core**（`types.py`/`schema.py` `_JSON_TYPE`+`_check_type`〔**有限性チェック必須**〕）+ **CLI**（`models.py` `_PY_TYPE`=`tuple[float×4]` / `_parse_vec3` を `_parse_vecN` に一般化 or `_parse_vec4` 追加）。`test_models_parity.py` が全コマンド走査で自動検証。
   - 推奨: `VEC4` 追加 + `_parse_vec3`/`_parse_vec4` は共通ヘルパ `_parse_vec(name, raw, n)` に寄せる。
3. **create のセマンティクス**: `create` は `--targets` へ作成と同時に割り当てる（create-and-assign）か、スタンドアロン作成のみか。
   - 推奨: **create-and-assign**（`--targets` 必須・作成したマテリアルを対象へ付与）。schema を一様（targets を実質必須運用）にでき、UX も直感的。スタンドアロン作成は後続。
   - これに伴い `--targets` は **schema 上は任意**にし、`assign`/`list`/`create` は ops 側で `_require_input` により必須化（set-origin の条件付き必須と同じ流儀）。
4. **assign で名前未存在のとき**: `--name` のマテリアルが無ければ `E_TARGET_NOT_FOUND`（作成はしない＝create と責務分離）。
   - 推奨: 上記。`--color` は create 専用（assign では無視 or USER_INPUT）。
5. **マテリアルスロットの扱い**: assign/create を対象に付与する際、**先頭スロットを置換**するか **追記**するか。
   - 推奨: スロットが空なら追加、あれば **active スロットを置換**（明快で golden 化しやすい）。複数スロット運用は後続。
6. **color の範囲**: RGBA は各 [0,1] にクランプせず受理（HDR/>1 を許容）。ただし **nan/inf はサーバ側で拒否**（VEC4 を `_check_type` の有限性対象に含める）。
7. **非対応型**: `obj.data.materials` を持たない型（EMPTY/LIGHT/CAMERA 等）への assign/create/list は `E_PRECONDITION`。

> **重要（greenfield）**: material のノード API は research.md に実機ノートが無い。**着手直後に M0.5 的な小スパイク**で次を 5.0.1/4.4.3 両方で確認してから gateway を確定する:
> - `mat = bpy.data.materials.new(name)`; `mat.use_nodes = True`; Principled BSDF ノードの入力名（**"Base Color"**）と `inputs["Base Color"].default_value`（RGBA）の代入可否。
> - viewport 表示用 `mat.diffuse_color`（RGBA）の併用要否。
> - `obj.data.materials.append(mat)` / スロット置換（`obj.material_slots[idx].material = mat` or `obj.data.materials[idx] = mat`）の挙動。
> - 5.0 でノード名/入力が変わっていないか（番号分岐禁止・値で判定）。

## 4. 実装手順（推奨順・T6.1/T6.2 と同じ流儀）
### A. VEC4 基盤（bli-core + CLI）
- `types.py`: `ParamType.VEC4 = "vec4"`。
- `schema.py`: `_JSON_TYPE[VEC4]`（array/number/minItems=maxItems=4）+ `_check_type` の VEC4 分岐（長さ4・数値・`math.isfinite` 必須）。
- `models.py`: `_PY_TYPE[VEC4] = tuple[float, float, float, float]`。
- `bli/main.py`: `_parse_vec3` を `_parse_vec(name, raw, n)` に一般化（既存 `_parse_vec3` 呼び出しを温存しつつ）。`material --color` は n=4。
- **parity 確認**: `test_models_parity.py` は自動走査。VEC4 が anyOf/array で一致するか確認。

### B. material スパイク → gateway
- 上記スパイクで API を確定 → `gateway.py` に集約（**生 bpy.ops 不要・bpy.data 直接**なら gateway へ）:
  - `create_material(name, color) -> str`（use_nodes + Principled Base Color + diffuse_color）。
  - `assign_material(obj, mat) -> None`（スロット置換/追記・判断5）。
  - `list_materials(obj) -> list[dict]`（slot index / name / base_color）。
  - `material_fingerprint(...)`（drift 検証用・names + colors の決定的ハッシュ）。
  - 非対応型は `E_PRECONDITION`。
- AST guard: bpy.data 直接操作（new/append/slot 代入）は gateway に置けば OK。

### C. ops ハンドラ
- `ops._material`: `_validate` → action 別に `_require_input`（assign/list は targets 必須・create は targets 必須〔判断3〕・assign は name 必須・create は color 任意）→ lazy import gateway → `_check_mode` → action 分岐。`_ok("material", data, fingerprint=...)`。`_BPY_HANDLERS` に登録。

### D. CLI
- `bli/main.py`: `material` サブコマンド（`--action`/`--targets`/`--name`/`--color`/`--id`）。`--color` は `_parse_vec(…,4)`。human 出力は action 別に簡潔に。

### E. テスト & 検証
- L1（bpy 不要）: `test_ops_dispatch.py` に material の param 検証（action 不正/必須漏れ/color VEC4 型・要素数・nan-inf〔サーバ側〕）。`test_cli_help.py` に発見（list-commands/help に material）+ `--color` の exit4（要素数/nan-inf）。`test_models_parity.py` は自動。
- 実機 smoke: `spikes/smoke_ops.py` に create→assign→list の golden（既知 color の往復・list の slot 名/色・非対応型ガード）。`PYTHONUTF8=1` + Blender 5.0/4.4。

## 5. 必ず守る規約（HANDOFF §8 / §6e 再掲）
- `bli-core` 純Python・依存ゼロ・3.10 互換（Pydantic は CLI のみ）。VEC4 追加も純Python。
- **AST guard**: `bpy.ops.*()` は gateway のみ。material は `bpy.data` 直接（new/append/slot）なので gateway に置けば OK。
- ops は gateway/bpy を遅延 import（param/前提検証は bpy 前に＝`_require_input`）。**数値の有限性は `schema._check_type`**（VEC4 も対象）。
- ruff modernization / format / pyright（新規エラー 0）を緑にしてから commit。

## 6. 仕上げ（T6.1/T6.2 と同じ運用）
1. 機能ごとに日本語コミット（feat/fix/docs・Co-Authored-By 付与）。`git add -A` 不使用・意図単位。VEC4 基盤と material 本体を分けると読みやすい。
2. `feature/m6-material` を push → `gh pr create --base main`。
3. レビュー: Codex（`@codex review`）が**利用上限**の間は **サブエージェント・セルフレビュー**（`software-design-reviewer` + 敵対的 correctness 監査の `general-purpose`）で代替。指摘対応→push のループ。マージはユーザー判断。
4. マージ後: HANDOFF §6d 進捗表を更新し、この NEXT-M6.md を **T6.4（modifier）用**に更新。M6 完了後に NEXT-M7.md へ。

## 7. 参照
- `specs/blender-cli-core/contracts/methods.md`（汎用編集）/ `plan.md §4 M6` / `spec.md §汎用編集` / `data-model.md`
- 実装参考（T6.1/T6.2）: `gateway.duplicate_object`/`names_fingerprint`/`select_objects`、`ops._duplicate`/`_delete`/`_require_input`/`_guard_shared_mesh`、`bli/main.py` の `_parse_vec3`/`duplicate`、`runtime.MAX_DUPLICATE_COUNT`、`schema._check_type`（有限性）、`spikes/smoke_ops.py` の `ensure_*` セットアップ。
- 後続: T6.4 `modifier`（add/remove/list/apply: MIRROR/SUBSURF/SOLIDIFY/DECIMATE/BOOLEAN・型別 params は最小から）。
