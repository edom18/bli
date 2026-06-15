# 次の作業 — M7「メッシュ編集」（bmesh 一次）✅ 完了

> **🎉 M7 完了（T7.1–7.3 全て）。次は M8（3シナリオ中核価値）= `.handoff/NEXT-M8.md` を参照。** 本ファイルは M7 の作業履歴として残す。

最終更新: 2026-06-15 / 状態: **M7 T7.1–7.3 完了**（T7.1/T7.2 main マージ済み・T7.3 boolean/decimate は実装・独立3視点セルフレビュー済み＝PR マージ待ち）。確定事項は `.handoff/HANDOFF.md §6f`。
> まず `.handoff/HANDOFF.md`（全体史 + 規約 + §6e 再利用パターン + **§6f M7 T7.1–7.3 確定事項**）を読む。出典: `plan.md §4 M7` / `spec.md §メッシュ編集` / `contracts/methods.md §メッシュ編集`。

## ✅ T7.1–7.2 完了（確定事項・T7.3 も踏襲）
- **単一 `mesh` コマンド + `--op` ENUM**（material/modifier と一貫・SSOT 1コマンド）。**stability=experimental（コマンド単位）**。op 専用 param は **schema default なし**。
- **bmesh-on-data**（`bmesh.new()`→`from_mesh`→`bmesh.ops`→`to_mesh`→`free`→`data.update()`・OBJECT モードのまま・context 非依存）。bmesh ヘルパは **`bli_addon/bmesh_ops.py`**（gateway 同様の bpy 接点層・`bmesh.ops` のみで AST guard 対象外・`try/finally` で `bm.free()` 保証）。
- 当面 **Mode=OBJECT**。破壊的 mesh 編集は **全 op で `_guard_shared_mesh`**。非 mesh は `gateway.require_mesh` で **E_PRECONDITION**。fingerprint は **`mesh_fingerprint`（法線込み・符号付きゼロ正規化）**。
- **結果は `{<param>, delta, stats}`**（`delta`=符号付き増減＝decimate/boolean の削減も表せる・`added` ではない）。**ベクトル param（offset）は world 空間**（matrix_world で変換・duplicate と一貫）/ **スカラ量（width/thickness/ratio 等）は mesh ローカル単位**。
- スパイク手順は `spikes/bmesh_spike.py`（T7.1）/ `bmesh_spike_t72.py`（T7.2）、確定値は `research.md §E/§E2`。T7.3 着手時も小スパイクで該当手段を 5.0.1/4.4.3 確認してから確定すること。
- T7.3 着手: `git checkout main && git pull`（T7.2 PR マージ後）→ `git checkout -b feature/m7-mesh-heavy`。`--op` に boolean/decimate を追加。**要スパイク（§2）**: bmesh に直接 boolean は無い → BOOLEAN modifier + apply フォールバックか `bpy.ops.mesh.intersect_boolean`（edit）。decimate は DECIMATE modifier 適用が確実（gateway.apply_modifier 再利用可）。両者 heavy 候補（同期実行・非同期 job は M10）。**繰越（設計 P3）**: `_mesh` の op 別検証 `elif` 連鎖を per-op validator テーブルへ整理。

---

## 0. 着手前（コピペ可）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout main && git pull origin main         # PR #6 マージ後
git checkout -b feature/m7-mesh-stable             # T7.1 から
uv sync
PYTHONUTF8=1 uv run pytest -q                       # 151 passed（M6 まで）を確認
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
```

## 1. M7 スコープ（plan.md §4 / methods.md / spec.md）
`mesh` コマンド（サブ操作）。**bmesh 一次**（`bpy.ops` の context 依存を回避）。Mode=ANY。
| サブ操作 | params | result | St | サブPR |
|---|---|---|:--:|:--:|
| `mesh --op recalc-normals` | `--targets` `--inside?` | 法線統計 | ✅ | T7.1 完了 |
| `mesh --op merge-by-distance` | `--targets` `--distance?` | マージ頂点数 | ✅ | T7.1 完了 |
| `mesh --op extrude` | `--targets` `--offset`(world) | mesh統計(delta) | ✅ | T7.2 完了 |
| `mesh --op bevel` | `--targets` `--width` `--segments?` | mesh統計(delta) | ✅ | T7.2 完了 |
| `mesh --op inset` | `--targets` `--thickness` | mesh統計(delta) | ✅ | T7.2 完了 |
| `mesh boolean` | `--targets` `--with` `--op union\|difference\|intersect` | mesh統計 | e | T7.3 |
| `mesh decimate` | `--targets` `--ratio` | 削減後ポリ数 | e | T7.3 |

- **サブPR分割**（M6 と同様・小さく緑に）: **T7.1 stable**（recalc-normals / merge-by-distance）→ T7.2 experimental（extrude/bevel/inset）→ T7.3 heavy（boolean/decimate）。

## 2. 着手前に必須の M0.5 的スパイク（bmesh API・5.0.1/4.4.3）
research.md に bmesh のノートが無い。**着手直後に小スパイク**で次を両版確認してから gateway を確定する:
- `bmesh.new()` → `bm.from_mesh(obj.data)` → `bmesh.ops.<op>(bm, ...)` → `bm.to_mesh(obj.data)` → `bm.free()`（**object モードのまま** mesh データを編集＝edit mode トグル不要・context 非依存）。
- 各 op の正確な名前/引数: `recalc_face_normals(bm, faces=...)` / `remove_doubles(bm, verts=..., dist=...)` / `extrude_face_region` / `bevel`(offset/segments/affect) / `inset_individual` or `inset_region` / `decimate` 相当（無ければ DECIMATE modifier 適用にフォールバック）/ boolean（bmesh に直接 boolean が無ければ手段検討: BMesh boolean は無い → `mesh boolean` は BOOLEAN modifier + apply にフォールバックか、bpy.ops.mesh.intersect_boolean を edit mode で。要スパイク）。
- `obj.data.update()` / 法線再計算の要否。頂点/面数の before/after。

## 3. キックオフ判断ポイント（T7.1 着手時に確認・推奨を併記）
1. **bmesh-on-data（object モード）で統一**するか edit mode トグルか。
   - 推奨: **bmesh-on-data**（`from_mesh`/`to_mesh`・context 非依存で CLI/headless に最適・spec の「bmesh 一次」と一致）。
2. **セレクタ最小**: extrude/bevel/inset の対象を「全 face/全 geometry」に限定（v1）。部分選択は後続。
   - 推奨: 全 geometry（`--faces` 等は最小・将来拡張）。
3. **mesh boolean / decimate の実装手段**: bmesh に直接が無い場合 modifier 経由（add+apply）にフォールバックするか。
   - 推奨: スパイク結果次第。decimate は DECIMATE modifier 適用が確実。boolean は `bpy.ops.mesh.intersect_boolean`(edit) か BOOLEAN modifier+apply。**T7.3 着手時に確定**。
4. **heavy 判定（boolean/decimate）**: M7 は同期実行で可（非同期 job は M10）。重い場合の watchdog は既存 DISPATCH_TIMEOUT。
   - 推奨: 同期。巨大入力の上限ガード（modifier の levels と同方針）を検討。
5. **共有 mesh ガード**: mesh 編集は **mesh データを直接書き換える** → 破壊的。`_guard_shared_mesh`（`--make-single-user`）を全 mesh サブ操作に適用（apply-transform/modifier apply と同じ）。
   - 推奨: 全 mesh 編集でガード適用（共有 mesh は単一ユーザ化必須）。
6. **mode=ANY の扱い**: methods.md は ANY だが、bmesh-on-data なら OBJECT 固定でも実害なし。
   - 推奨: 当面 OBJECT（bmesh-on-data）。EDIT mode 実機は L4。

## 3.5 サブ操作の表現（`mesh` コマンド設計）
- material/modifier と同様に **`mesh --op <sub> --targets ...`**（`--op` ENUM: recalc-normals/merge-by-distance/extrude/bevel/inset/boolean/decimate）が一貫。サブごとの params は schema 任意・ops で op 別検証（条件付き必須）。stability はコマンド単位なので `mesh` 全体を experimental にするか、stable サブのみ別コマンドにするか要検討（推奨: `mesh` 1コマンド・experimental。stable な recalc/merge も同コマンド内）。
  - 別案: `mesh-recalc-normals` 等を個別コマンドにすれば stability を分離できる。**T7.1 着手時に確定**（推奨は単一 `mesh` コマンド + `--op`）。

## 4. 実装手順（M6 と同じ流儀・T7.1 から）
- **A. SSOT**: `definitions.py` に `mesh`（`--op` ENUM 必須 / `--targets` 必須 / 各 op の params 任意 / `--make-single-user`）。`implemented=True`、stability は方針次第。
- **B. gateway**: `bli_addon/bmesh_ops.py` 等に bmesh ヘルパ（`recalc_normals`/`merge_by_distance`/... ）を集約。**bmesh は gateway 配下**（AST guard は bpy.ops のみ対象だが、bpy 接点集約の方針に従う）。`mesh_stats(obj)`（verts/edges/faces）/ `mesh_fingerprint`。
- **C. ops**: `_mesh` ハンドラ（op 別検証 → `require_single` → mesh 型検証（`require_mesh` 相当・非mesh は E_PRECONDITION）→ `_guard_shared_mesh` → bmesh ヘルパ → `_ok`）。
- **D. CLI**: `mesh` サブコマンド（`--op` ほか）。
- **E. テスト/smoke**: L1（op/必須/型/有限性/発見）+ 実機 smoke（recalc/merge の golden = 法線・頂点数）。5.0/4.4。

## 5. 必ず守る規約（HANDOFF §8 / §6e）
- bli-core 純Python・依存ゼロ。**AST guard**: 生 `bpy.ops` は gateway のみ。bmesh も gateway 集約。
- ops は gateway/bpy/bmesh を遅延 import。検証は bpy 前に（`_require_input`）。数値の有限性は `schema._check_type`。
- **非対応型は E_PRECONDITION**（INTERNAL にしない）。**破壊的 mesh 編集は共有ガード**。**暴走しうる数値は範囲ガード**。**fingerprint は操作の本質に合わせる**（mesh 変化 → mesh 込み）。
- ruff / format / pyright（新規 0）緑で commit。

## 6. 仕上げ（M6 と同じ運用）
1. 機能ごとに日本語コミット（feat/fix・Co-Authored-By）。意図単位。
2. push → `gh pr create --base main`。
3. レビュー: Codex（`@codex review`）が**利用上限**なら **独立3視点のサブエージェント・セルフレビュー**（設計 / 敵対的 correctness / 仕様・テスト）でバイアスなく代替。指摘対応→push。マージはユーザー判断。
4. 各サブPR マージ後に HANDOFF 進捗を更新。M7 完了で NEXT-M8.md（3シナリオ中核価値）へ。

## 7. 参照
- `plan.md §4 M7` / `spec.md §メッシュ編集` / `contracts/methods.md §メッシュ編集`
- 実装参考（M6）: `gateway.apply_modifier`（operator 経路）/`require_modifier_support`/`add_modifier`、`ops._modifier`（op 別条件付き必須）/`_guard_shared_mesh`/`_require_input`、`bli/main.py` の `modifier`、`spikes/smoke_ops.py` の `ensure_*`。
- 後続: M8（3シナリオ: set-origin 済 / straighten / print-*）が中核価値。M9 ファイルI/O。M10 非同期 job（heavy の正式対応）。
