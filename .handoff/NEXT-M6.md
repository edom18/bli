# 次の作業 — M6「汎用編集」T6.2（duplicate / delete）

最終更新: 2026-06-14 / 前提: M0–M5 と **M6 T6.1（select/transform/apply-transform）は main にマージ済み**（PR #3）。
作業ブランチ `feature/m6-dup-delete`（main 由来）作成済み。

> まず `.handoff/HANDOFF.md`（全体史 + 規約 + §6d M6 + **§6e 再利用パターン**）を読み、その後この1枚で T6.2 に着手する。
> 出典: `plan.md §4 M6` / `contracts/methods.md §汎用編集` / `spec.md` / `data-model.md`。

---

## 0. 着手前（コピペ可）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout feature/m6-dup-delete        # 既にいれば不要
uv sync
PYTHONUTF8=1 uv run pytest -q                       # 107 passed（ベースライン緑）
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
# 実機スモーク（任意・回帰確認）:
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background \
  --python packages/bli-addon/spikes/smoke_ops.py 2>&1 \
  | sed -n '/BLI_OPS_SMOKE_BEGIN/,/BLI_OPS_SMOKE_END/p'   # → OPS SMOKE OK
```
> ブランチ未作成なら: `git checkout main && git pull origin main && git checkout -b feature/m6-dup-delete`

## 1. M6 全体の確定方針（再掲）
- **サブPR分割**: T6.1 ✅ → **T6.2（今ここ）** → T6.3 material → T6.4 modifier。各 PR を小さく緑に。
- T6.1 で確立した**再利用パターンは必ず踏襲**（HANDOFF §6e）: `_guard_shared_mesh` / `_require_input` / `--targets` オプション / `resolve_targets`（不正regex は USER_INPUT 済み）/ presence-sensitive フラグは schema default なし / world 座標は matrix_world / 出力は決定的順序 / bpy 接点は gateway 集約（AST guard）。

## 2. T6.2 スコープ（plan.md §4 / methods.md）
| method | params | result | M | Mode |
|---|---|---|:-:|---|
| `duplicate` | `--targets` `--linked?` `--count?` `--offset(x,y,z)?` | 新オブジェクト名 | ✓ | OBJECT |
| `delete` | `--targets` `--backup?` | 削除結果 | ✓ | OBJECT |

- `duplicate`: `obj.copy()`（+ linked でなければ `obj.data.copy()`）→ コレクションに `link`。**生 bpy.ops 不要**＝gateway 内で `bpy.data` 直接操作。count 回複製し offset を累積。新規名一覧を返す。
- `delete`: `bpy.data.objects.remove(obj, do_unlink=True)`。**破壊的**。削除前サマリを backup として結果に残す。

## 3. 着手時に決める判断ポイント（T6.2 キックオフで確認）
1. **delete の確認 UX**（エージェント安全）: `--targets` 明示で即実行か、`--confirm`/`--yes` を必須にするか。
   - 推奨: 既定で実行しつつ結果に削除サマリ（backup）を必ず含める。誤爆防止を強めるなら `--confirm` 必須も可。**要ユーザー判断**。
2. **delete の backup 実体**: M6 では「削除前 object_summary を結果に返す」に留め、`.blend` バックアップは **M9（save）へ繰越**。
   - 推奨: 上記（M9 繰越）。spec の `backup.on_overwrite` は .blend 保存依存のため。
3. **対象数（単一/複数）**: duplicate・delete とも当面 `require_single`（1件）。複数（regex 一括）は後続。
   - 推奨: 単一から。`resolve_targets` で 0件→`E_TARGET_NOT_FOUND` / 複数→`E_PRECONDITION`（require_single 既存）。
4. **duplicate offset の空間と累積**: offset を world（matrix_world 経由・T6.1 と整合）か local（location）か。count>1 は `(i+1)*offset` を累積。
   - 推奨: world 空間（T6.1 の location と一貫）。VEC3 入力は `_parse_vec3`（nan/inf 弾き）を再利用。

## 4. 実装手順（推奨順・T6.1 と同じ流儀）
### A. duplicate
- `bli_core/definitions.py`: `command("duplicate", ..., params=(targets[req], linked[BOOL], count[INT], offset[VEC3]))`、`mutates=True`、`required_mode=Mode.OBJECT`。
  - `count` の既定/最小は要検討（既定1）。**presence-sensitive でない通常フラグ**なので `linked` は default=False で可。
- `gateway.py`: `duplicate_object(obj, *, linked, count, offset) -> list[str]`。`obj.copy()` / 非 linked は `new.data = obj.data.copy()`（data ありの型のみ）/ `obj.users_collection` の各 collection に link / world offset 累積 / `push_undo`。新規名を返す。
- `ops.py`: `_duplicate` ハンドラ + `_BPY_HANDLERS`。`_require_input` で count>=1 等を bpy 前に検証。`require_single` で対象解決。`_ok("duplicate", {"created": [...], "count": n})`。
- `bli/main.py`: `duplicate` サブコマンド（`--id` 冪等・`--offset` は `_parse_vec3`）。
- golden（smoke）: Cube を count=2 offset=(3,0,0) で複製→ 新規2個・名前・world location が +3,+6。

### B. delete
- `definitions.py`: `command("delete", ..., params=(targets[req], backup[BOOL?] or confirm[BOOL]))`、`mutates=True`、OBJECT。判断1次第。
- `gateway.py`: `delete_object(obj) -> None`（`bpy.data.objects.remove(obj, do_unlink=True)`）。削除前に `object_summary(obj)` を ops 側で取得。
- `ops.py`: `_delete` ハンドラ。`require_single` → 削除前サマリ取得 → （共有 mesh は delete では基本問題ないが、リンク解除のみ）→ `gateway.delete_object` → `_ok("delete", {"deleted": name, "backup": <summary>})`。
- `bli/main.py`: `delete` サブコマンド（`--id`・確認フラグは判断1）。
- golden（smoke）: 一時オブジェクトを作って delete → scene から消える・backup サマリが返る・存在しない名は `E_TARGET_NOT_FOUND`。
- **注意**: smoke で delete 検証用の使い捨てオブジェクトを **メインスレッドで** 用意（`ensure_*` パターン）。既存の golden（Cube/ShA/ShB 等の数）を壊さない名前・後始末に注意。

### C. テスト & 検証
- L1（bpy 不要）: `test_ops_dispatch.py` に duplicate/delete の param 検証（必須漏れ・型・count<1 など）。`test_models_parity.py` は全コマンド走査で自動対象（VEC3/INT/BOOL が parity を壊さないか確認）。
- CLI: `test_cli_help.py` に発見（list-commands/help に duplicate/delete）+ `_parse_vec3`（offset）/ count 検証の exit4。
- 実機 smoke: `spikes/smoke_ops.py` に duplicate/delete の golden。`PYTHONUTF8=1` + Blender 5.0/4.4。

## 5. 必ず守る規約（HANDOFF §8 / §6e 再掲）
- `bli-core` 純Python・依存ゼロ・3.10 互換（Pydantic は CLI のみ）。
- **AST guard**: `bpy.ops.*()` は gateway のみ。duplicate/delete は `bpy.data` 直接（copy/link/remove）なので gateway に置けば OK（生 ops 不要）。
- ops は gateway/bpy を遅延 import（param/前提検証は bpy 前に＝`_require_input`）。
- ruff modernization / format / pyright（新規エラー 0）を緑にしてから commit。

## 6. 仕上げ（T6.1 と同じ運用）
1. 機能ごとに日本語コミット（feat/fix/docs・Co-Authored-By 付与）。`git add -A` 不使用・意図単位。
2. `feature/m6-dup-delete` を push → `gh pr create --base main`。
3. レビュー: Codex（`@codex review`）が**利用上限**の間は **サブエージェント・セルフレビュー**（`software-design-reviewer` + 敵対的 correctness 監査の `general-purpose`）で代替。指摘対応→push のループ。マージはユーザー判断。
4. マージ後: HANDOFF §6d 進捗表を更新し、この NEXT-M6.md を **T6.3（material）用**に更新。M6 完了後に NEXT-M7.md へ。

## 7. 参照
- `specs/blender-cli-core/contracts/methods.md`（汎用編集）/ `plan.md §4 M6` / `data-model.md`
- 実装参考（T6.1）: `gateway.transform_object`/`select_objects`/`apply_transform`、`ops._guard_shared_mesh`/`_require_input`/`_transform`、`bli/main.py` の `_parse_vec3`/`_rpc`、`spikes/smoke_ops.py` の `ensure_*` セットアップ。
- 後続: T6.3 `material`（assign/create/list・color は VEC4 が要るため ParamType 拡張を検討）/ T6.4 `modifier`（add/remove/list/apply: MIRROR/SUBSURF/SOLIDIFY/DECIMATE/BOOLEAN・型別 params は最小から）。
