# 次の作業 — M6「汎用編集（オブジェクト）」

最終更新: 2026-06-14 / 前提: M0–M5 は main にマージ済み。M6 を `feature/m6-edit` で実装中。

> まず `.handoff/HANDOFF.md`（全体史 + 規約 + §6d M6）を読み、その後この1枚で続きに着手する。
> 出典: `plan.md §4 M6` / `spec.md §コマンド表` / `contracts/methods.md §汎用編集` / `data-model.md`。

---

## 0. 確定した方針（着手時にユーザー判断で確定）
- **M6 はサブPRに分割**して進める（1コマンド群=1PR 目安）。順序: **T6.1 → T6.2 → T6.3 → T6.4**。
- `transform --mode delta` の **scale は乗算**（component-wise `*=`）、location/rotation は加算。
- `select` は **実装する**（`select_set` + `view_layer.objects.active`）。他コマンドは従来どおり `--targets` で独立解決し、select に依存しない。

## 1. 進捗
| タスク | 内容 | 状態 |
|---|---|---|
| **T6.1** | `select` / `transform` / `apply-transform` | ✅ 実装完了（feature/m6-edit・PR予定） |
| T6.2 | `duplicate`（`copy()`+`data.copy()`+link）/ `delete`（backup/確認） | 🔜 次 |
| T6.3 | `material`（assign / create / list） | 未 |
| T6.4 | `modifier`（add / remove / list / apply：MIRROR/SUBSURF/SOLIDIFY/DECIMATE/BOOLEAN） | 未 |

## 2. T6.1 実装サマリ（完了・参照用）
- `definitions.py`: `transform` を `implemented=True` 化。`select`（targets/type/active）・`apply-transform`（targets + location/rotation/scale の BOOL・全省略=全適用）を追加。
- `gateway.py`: `transform_object`（直接プロパティ・op不要・度→ラジアン・delta は loc/rot 加算 / scale 乗算）/ `apply_transform`（`transform_apply` を `isolate_users=True`）/ `select_objects`（`select_set`+active 直接）。
- `ops.py`: `_select`/`_transform`/`_apply_transform` + `_BPY_HANDLERS`。`bli/main.py`: 3サブコマンド（`--id` 付き）+ `_parse_vec3`。
- テスト: ops 検証 +5 / CLI +3、未実装例を `exec-python` に差し替え。**pytest=103**。smoke に transform/apply/select golden。5.0.1/4.4.3 実機 OK。

## 3. 次タスク T6.2–6.4 の設計メモ（着手時に詰める）
### T6.2 duplicate / delete
- `duplicate`: `--targets --linked? --count? --offset(x,y,z)?`。実体は `bpy.data` 直接（`obj.copy()` / linked でなければ `obj.data = obj.data.copy()` / `collection.objects.link`）。生 ops 不要 → gateway 内で完結。新オブジェクト名一覧を返す。count>1 は offset 累積。
- `delete`: `--targets --backup?`。**破壊的**。spec は「既定でバックアップ/確認セマンティクス」。CLI 非対話なので確認は `--yes` 等のフラグ設計を要判断（最小: backup=現状態の fingerprint/サマリを結果に残す or .blend バックアップは M9 の save 依存で繰越）。`bpy.data.objects.remove(obj)`。
- 判断: ①delete の確認 UX（フラグ必須にするか）②backup の実体（M6 では「削除前サマリ返却」に留め、.blend バックアップは M9 へ繰越が筋）。

### T6.3 material
- `material assign|create|list --targets --name? --color(r,g,b,a)?`。サブアクション制（set-origin の `to` のような ENUM `action`）。
  - `list`: obj の material slot 名一覧。`create`: 新規 material（name/color）。`assign`: 既存/新規 material を slot へ。
  - color は VEC4 が要るが現状 ParamType に VEC4 無し → 要追加 or r,g,b,a を個別 float。**要判断**（VEC4 を schema/Pydantic/CLI parse に足すのが筋）。

### T6.4 modifier
- `modifier add|remove|list|apply --targets --type? [params]`。type: MIRROR/SUBSURF/SOLIDIFY/DECIMATE/BOOLEAN。
  - `add`: `obj.modifiers.new(name, type)` + 主要 params（例 SUBSURF levels / SOLIDIFY thickness / DECIMATE ratio / MIRROR axis / BOOLEAN object+operation）。
  - `apply`: `bpy.ops.object.modifier_apply`（gateway 経由・shared mesh 注意）。`remove`: `obj.modifiers.remove`。`list`: 名前/型/主要params。
  - 判断: modifier 個別 params の表現（type ごとに必要な param が違う → 緩い dict か type 別 schema か）。spec §443「modifier 各コマンドの個別パラメータ詳細」は Deferred。最小実装＋主要 param から。

## 4. 必ず守る規約（HANDOFF §8 再掲）
- **bli-core は純Python・依存ゼロ / 3.10 互換**（Pydantic は CLI のみ）。
- **AST guard**: `bpy.ops.*()` は `gateway.py` のみ。duplicate/select は `bpy.data`/`select_set` 直接（op不要）。modifier apply は run_operator 経由。
- **ops は gateway/bpy を遅延 import**（pytest で bpy 無しでも param/mode 検証に到達できる構造を維持）。
- ruff modernization（`X | None` / `@cache` 等）に注意。CI 緑にしてから commit。
- schema_hash は新コマンド追加で変わる（ピン留めテスト無し・CLI/addon は同一 SSOT から算出で一致）。

## 5. 仕上げ（前回 PR と同じ運用）
1. T6.1 を PR 化（feature/m6-edit → main）。日本語コミット（feat/fix/docs・Co-Authored-By）。
2. `gh pr create --base main` → Codex（`@codex review`）→ 指摘対応 → push → 再依頼ループ。マージはユーザー判断。
3. T6.2 以降は **新しい feature ブランチ**（例 `feature/m6-dup-delete`）を main から切って繰り返す。
4. 各 PR マージ後に HANDOFF §6d / この NEXT-M6.md の進捗表を更新。M6 完了後に NEXT-M7.md へ。

## 6. 参照
- `specs/blender-cli-core/plan.md §4 M6` / `contracts/methods.md §汎用編集`（params/result）
- 実装参考: `gateway.transform_object` / `apply_transform` / `select_objects`、`ops._transform` 等、`bli/main.py` の `_parse_vec3` / `_rpc`。
