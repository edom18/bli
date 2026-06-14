# 次の作業 — M6「汎用編集」T6.4（modifier：add / remove / list / apply）【M6 最後】

最終更新: 2026-06-14 / 前提: M0–M5 と **M6 T6.1–T6.3 は main にマージ済み**（PR #3・#4・#5）。
作業ブランチ `feature/m6-modifier`（main 由来）作成済み。

> まず `.handoff/HANDOFF.md`（全体史 + 規約 + §6d M6 + **§6e 再利用パターン**）を読み、その後この1枚で T6.4 に着手する。
> 出典: `plan.md §4 M6 / §6.4` / `contracts/methods.md §汎用編集` / `spec.md §主要モディファイア`。
> **T6.4 完了で M6 完了** → 次は NEXT-M7.md を作成。

---

## 0. 着手前（コピペ可）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout feature/m6-modifier        # 既にいれば不要
uv sync
PYTHONUTF8=1 uv run pytest -q                       # 132 passed（ベースライン緑）
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
# 実機スモーク（任意・回帰確認）:
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background \
  --python packages/bli-addon/spikes/smoke_ops.py 2>&1 \
  | sed -n '/BLI_OPS_SMOKE_BEGIN/,/BLI_OPS_SMOKE_END/p'   # → OPS SMOKE OK
```

## 1. T6.4 スコープ（plan.md §4 / methods.md / spec.md）
| method | params | result | M | Mode |
|---|---|---|:-:|---|
| `modifier` | `--action add\|remove\|list\|apply` `--targets` `--type?` `--name?` `[type別params]` `--make-single-user?` | modifier状態 | ✓ | OBJECT |

- v1 必須 type: `MIRROR` / `SUBSURF` / `SOLIDIFY` / `DECIMATE` / `BOOLEAN`。

## 2. modifier API グラウンドトゥルース（着手前スパイクで 5.0.1/4.4.3 確認済み）
- `obj.modifiers.new(name, TYPE)` で全5種生成可。`obj.modifiers.remove(mod)` / 列挙は `obj.modifiers`（`.name` `.type`）。
- プロパティ（最小）: **MIRROR**=`use_axis[0..2]`(bool) / **SUBSURF**=`levels`,`render_levels`(int) / **SOLIDIFY**=`thickness`(float) / **DECIMATE**=`decimate_type`(既定 COLLAPSE),`ratio`(float) / **BOOLEAN**=`operation`(UNION/DIFFERENCE/INTERSECT),`object`(operand)。
- **apply** は `bpy.ops.object.modifier_apply(modifier=name)` を `temp_override(active_object/object/selected_objects)` 下で → `{'FINISHED'}`。mir 適用で頂点 8→16（mesh データへ焼き込み）。
- **重要**: modifier は **オブジェクト単位**（`obj.modifiers`）。add/remove/list は mesh データを触らない＝**共有 mesh ガード不要**。**apply のみ** mesh へ焼き込む＝`_guard_shared_mesh`（`--make-single-user`）が要る（apply-transform と同じ）。

## 3. キックオフ確定判断（自走のため既定で確定。要見直しなら後述）
1. **action = `--action` ENUM**（add/remove/list/apply）。material と一貫（positional 不可）。
2. **type 別 params は最小1つ**（schema は全て任意・ops で action/type 別に検証）:
   - MIRROR=`--axis X|Y|Z`（既定 X→該当 use_axis のみ True）/ SUBSURF=`--levels INT`（既定 1）/ SOLIDIFY=`--thickness FLOAT`（既定 0.01）/ DECIMATE=`--ratio FLOAT`（既定 0.5）/ BOOLEAN=`--operation UNION|DIFFERENCE|INTERSECT`（既定 DIFFERENCE）+`--with`（operand object 名・**boolean add で必須**）。
3. **add**: `--type` 必須。modifier 名は `--name` 省略時 Blender 既定。該当 type の param を設定。応答に作成名 + 一覧。
4. **remove / apply**: `--name` 必須（どの modifier か）。
5. **apply**: 破壊的（mesh へ焼き込み）→ `_guard_shared_mesh`（共有 mesh は `--make-single-user` 必須）。operator は gateway `run_operator` 経由。
6. **list**: targets のみ。`[{name, type, …主要プロパティ}]`。
7. **silent ignore しない**: add で type に無関係な type-param が来たら USER_INPUT で弾く（material の color-on-assign と同方針）。type-param は add 専用（remove/apply/list で来たら弾く）。`--with` の operand は `require_single` で解決（0件 E_TARGET_NOT_FOUND）。

## 4. 実装手順（推奨順・T6.1–6.3 と同じ流儀）
### A. SSOT（bli-core/definitions.py）
- `command("modifier", …, params=(action[ENUM req add/remove/list/apply], targets[STR req], type[ENUM MIRROR/…], name[STR], axis[ENUM X/Y/Z], levels[INT], thickness[FLOAT], ratio[FLOAT], operation[ENUM UNION/DIFFERENCE/INTERSECT], with_object[STR], make_single_user[BOOL default False]), mutates=True, required_mode=OBJECT)`。FLOAT は有限性が `schema._check_type` で効く（既存）。
### B. gateway.py（**bpy 接点は gateway 集約**・add/remove/list は bpy.data 直接 / apply は run_operator）
- `add_modifier(obj, mod_type, *, name=None, axis/levels/thickness/ratio/operation=None, operand=None) -> dict`（`obj.modifiers.new` + type別プロパティ設定・boolean は `mod.object=operand`）。
- `remove_modifier(obj, name) -> None`（`obj.modifiers.get`→無ければ E_TARGET_NOT_FOUND→`remove`）。
- `list_modifiers(obj) -> list[dict]`（name/type + type別の主要値）。
- `apply_modifier(obj, name) -> dict`（`run_operator(bpy.ops.object.modifier_apply, obj, modifier=name)`・無効名は事前に E_TARGET_NOT_FOUND）。
- `modifiers_fingerprint(obj)`（name/type 列の決定的ハッシュ）。`require_modifier(obj, name)` で未発見集約。
### C. ops.py（`_modifier` ハンドラ + `_BPY_HANDLERS` 登録）
- `_validate`→action 別 `_require_input`（targets 必須 / add は type 必須・boolean は with 必須 / remove・apply は name 必須 / type-param は add 専用・type 整合）→ lazy import gateway → `_check_mode` → `require_single`(targets)。
- apply のみ `_guard_shared_mesh(gateway, obj, params)`（add/remove/list は不要）。boolean operand は `gateway.require_single(with_object)`。`_ok("modifier", data, fingerprint=gateway.modifiers_fingerprint(obj))`。
### D. CLI（bli/main.py）
- `modifier` サブコマンド（`--action`/`--targets`/`--type`/`--name`/`--axis`/`--levels`/`--thickness`/`--ratio`/`--operation`/`--with`/`--make-single-user`/`--id`）。
### E. テスト & 検証
- L1（bpy 不要）: `test_ops_dispatch.py` に modifier の param 検証（action/type の enum・必須漏れ・boolean の with 必須・type 不整合 param・remove/apply の name 必須）。`test_cli_help.py` に発見 + 各 ENUM/INT/FLOAT の exit4。`test_models_parity.py` は自動。
- 実機 smoke: `spikes/smoke_ops.py` に add（各type最小）→ list → apply（共有ガード）→ remove の golden。`PYTHONUTF8=1` + Blender 5.0/4.4。

## 5. 必ず守る規約（HANDOFF §8 / §6e 再掲）
- `bli-core` 純Python・依存ゼロ・3.10 互換。**AST guard**: `bpy.ops.*()` は gateway のみ（apply の `modifier_apply` は `run_operator` 経由）。add/remove/list は `bpy.data` 直接で gateway 集約。
- ops は gateway/bpy を遅延 import（param/前提検証は bpy 前に＝`_require_input`）。数値の有限性は `schema._check_type`。
- 破壊的（apply）のみ共有ガード。出力は決定的順序。
- ruff modernization / format / pyright（新規エラー 0）を緑にしてから commit。

## 6. 仕上げ（T6.1–6.3 と同じ運用）
1. 機能ごとに日本語コミット（feat/fix・Co-Authored-By 付与）。`git add -A` 不使用・意図単位。
2. `feature/m6-modifier` を push → `gh pr create --base main`。
3. レビュー: Codex（`@codex review`）が**利用上限**の間は **サブエージェント・セルフレビュー**（`software-design-reviewer` + 敵対的 correctness の `general-purpose` + 仕様/テスト適合）で代替。バイアスを避けるため各エージェントに前置きを与えず差分を白紙からレビューさせる。指摘対応→push のループ。マージはユーザー判断。
4. マージ後: **M6 完了**。HANDOFF §6d 進捗表を M6 ✅ に更新し、`.handoff/NEXT-M7.md`（メッシュ編集 bmesh一次）を新規作成。NEXT-M6.md は役割終了。

## 7. 参照
- `specs/blender-cli-core/contracts/methods.md`（汎用編集 + `modifier --type`）/ `plan.md §4 M6` / `spec.md §主要モディファイア`
- 実装参考（T6.1–6.3）: `gateway.run_operator`/`require_single`/`apply_transform`（apply の operator 経路）/`create_material`/`_target_slot_index`、`ops._material`/`_guard_shared_mesh`/`_require_input`、`bli/main.py` の `_parse_vec`/`material`、`spikes/smoke_ops.py` の `ensure_*`。
- 後続: M7（メッシュ編集 bmesh一次）= `mesh recalc-normals`/`merge-by-distance`（stable）/ `extrude`/`bevel`/`inset`（experimental）/ `boolean`/`decimate`（heavy）。
