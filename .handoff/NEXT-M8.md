# 次の作業 — M8「3シナリオ（中核価値）」

> **⚠️ 2026-06-16 更新**: T8.1–T8.4 完了（PR #10–#12）+ **実地フィードバック対応ワークストリーム PR-1〜5 完了**（PR #13/#14/#15/#17/#18・経緯は `.handoff/NEXT-M8-feedback.md`）。**残るは T8.5 print-export のみ＝これで M8 完了**。**次に着手すべきは T8.5（このファイルの §1 表 T8.5 行 + §2 スパイク3「print-export 3mf」+ §3 kickoff）**。全体俯瞰は `.handoff/ROADMAP.md`。

最終更新: 2026-06-15 / 前提: **M0–M7 完了**（M7 T7.3 boolean/decimate は PR マージ待ち＝マージで M7 完了）。**M8 = このプロダクトの中核価値**（spec の3シナリオ: 原点変更 / 直立補正 / 3Dプリンタ対応）。
> まず `.handoff/HANDOFF.md`（全体史 + 規約 + §6e 再利用パターン + §6f M7 確定事項）を読む。出典: `plan.md §4 M8` / `spec.md §シナリオ1–3 + §10 受け入れ基準` / `contracts/methods.md §シナリオ1–3`。

## 0. 着手前（コピペ可）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout main && git pull origin main          # PR #18 マージ後
git checkout -b feature/m8-print-export             # T8.5
uv sync
PYTHONUTF8=1 uv run pytest -q                       # 244 passed（実地FB PR-5 まで）を確認
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
# 実機スモーク（ops 一式・両版）:
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background \
  --python packages/bli-addon/spikes/smoke_ops.py 2>&1 \
  | sed -n '/BLI_OPS_SMOKE_BEGIN/,/BLI_OPS_SMOKE_END/p'   # → OPS SMOKE OK
```

## 1. M8 スコープ（plan.md §4 M8 / methods.md §シナリオ1–3 / spec.md）
| タスク | コマンド | 概要 | St | 状態 |
|---|---|---|:--:|---|
| T8.1 | `set-origin` | geometry/cursor/world・共有ガード・行列直接 | s | **✅ M3 で実装済み**（既存・S1 golden 緑）|
| T8.2 | `straighten` | reset / world-align / pca / floor・up-axis・bake-rotation | s | **✅ 完了**（PR #10 main マージ済み / research §E4 / HANDOFF §6g）|
| T8.3 | `print-setup` | unit=mm/m（表示単位のみ・非破壊）・global_scale は T8.5 で一本化 | s | **✅ 完了**（PR #11 main マージ済み / research §E5 / HANDOFF §6g）|
| T8.4 | `print-check` / `print-repair` | bmesh 自前 manifold/normals/degenerate・thin/intersect は CAPABILITY_UNAVAILABLE 縮退 | s | **✅ 完了**（feature/m8-print-check・PR 待ち / 3視点セルフレビュー済み / **print3d 再スパイク消化＝両版実体なし確定 §E6** / HANDOFF §6g）|
| T8.5 | `print-export` | stl / 3mf（3mf 不可→stl hint） | s | **← 次はここ**（未着手・M8 完了で M9 へ）|

- **DoD（plan.md）**: spec §10 受け入れ基準を **golden 数値**で満たす。3シナリオ経路は**全 stable**。
- **サブPR分割**（M6/M7 と同様・小さく緑に）推奨: T8.2 straighten → T8.3 print-setup → T8.4 print-check/repair → T8.5 print-export。
- **T8.1 は実装済み**（M3）。M8 では「3シナリオが stable で golden を満たす」ことの確認に含める（新規実装は不要・必要なら golden 追補のみ）。

## 2. 着手前に必須のスパイク（M0.5 的・5.0.1/4.4.3 両版）
NEXT-M7 §2 と同じ流儀で**着手直後に小スパイク**し research.md に確定値を残す（`spikes/*_spike.py` + `BLI_*_SPIKE_BEGIN/END`）。
1. **print3d の実モジュール id（最重要・M0.5 から繰越）**: M0.5 で `object_print3d_utils` / `print3d_toolbox` の `addon_utils.enable` が両版 False だった（research.md 付録）。Extensions 化された 5.0 での正しい id / enable 経路を特定する。不可なら `print-check`/`print-repair` は **`CAPABILITY_UNAVAILABLE` 縮退**で設計（capability.py の RESOLVERS / CapabilityRegistry を流用）。manifold/normals/degenerate チェックは print3d が無くても **bmesh で自前計算**できる範囲を切り分ける（non-manifold edge 数・面法線一貫性・縮退面）。
2. **straighten の数学**: `pca`（頂点分布の主成分→回転）/ `floor`（最小 z を接地）/ `world-align`（指定軸を up へ）/ `reset`（回転ゼロ）。mathutils（`Matrix`/`Vector`/共分散）で実装し、`--bake-rotation` は apply-transform 経路（M6）を再利用。接地 Z の golden（world bbox min.z=0）。
3. **print-export 3mf**: M0.5 で 3MF は標準で実体なし → STL フォールバック方針（research.md D11/付録）。`io_mesh_3mf` 有無で分岐し、不可は **STL hint**。stl は `wm.stl_export`（M0.5 確定・capability.py RESOLVERS）。
4. **print-setup unit**: `scene.unit_settings`（system/scale_length/length_unit）の mm 設定と `global_scale` の一本化。

## 3. キックオフ判断ポイント（着手時にユーザー確認・推奨を併記）
1. **straighten のセレクタ/対象**: 単一（`require_single`）か複数か。推奨: 単一（set-origin と対称）。
2. **pca の安定化**: 主成分の符号/軸対応の曖昧さ（PCA は符号不定）→ up-axis 指定で一意化。推奨: `--up-axis +Z` 既定 + 最大分散軸を up に割り当て、符号は重心からの偏りで決定（決定的 golden を作れる形に）。
3. **print-check の出力**: 大きい結果は output_ref 退避（M5 の `_ok_offload` 再利用）。推奨: チェック結果が閾値超なら退避。
4. **print3d 不在時の縮退**: bmesh 自前チェックでどこまでやるか。推奨: manifold/normals/degenerate は bmesh 自前（print3d 非依存で stable）、thin/intersect は print3d 依存（無ければ CAPABILITY_UNAVAILABLE）。
5. **print-repair の破壊性**: mesh を書き換える → 共有ガード（`_guard_shared_mesh`）+ fingerprint（`mesh_fingerprint`）。推奨: M7 と同じガード。
6. **stability**: 3シナリオは全 stable（DoD）。straighten/print-* は experimental ではなく stable で出す（golden で裏付け）。

## 4. 実装手順（M6/M7 と同じ流儀）
- **A. SSOT**: `definitions.py` に `straighten`/`print-setup`/`print-check`/`print-repair`/`print-export`（`implemented=True`・stable）。op/method 別の条件付き必須は ops で検証（material/modifier/mesh と同流儀）。
- **B. gateway/接点層**: bmesh 自前チェック（manifold/normals/degenerate）は `bmesh_ops.py` 系 / print3d operator 経路・能力検出は `gateway.py`（`run_operator` + capability）。straighten の行列計算は gateway（mathutils）。**生 bpy.ops は gateway のみ**。
- **C. ops**: 各ハンドラ（検証 → require_single → 前提（型/能力）→ 破壊系は `_guard_shared_mesh` → 実行 → `_ok`）。`_require_input`/`_guard_shared_mesh`/`_resolve_*` の再利用。
- **D. CLI**: 各サブコマンド（`_parse_vec`・human 出力）。
- **E. テスト/smoke**: L1（method/必須/型/有限性/発見）+ 実機 smoke（straighten 接地 Z golden・print-check の自前チェック golden・print-export 往復）。5.0.1/4.4.3 両版。

## 5. 必ず守る規約（HANDOFF §8 / §6e）
- bli-core 純Python・依存ゼロ。**AST guard**: 生 `bpy.ops` は gateway のみ。bmesh も gateway/bmesh_ops 集約。
- ops は gateway/bpy/bmesh を遅延 import。検証は bpy 前に（`_require_input`）。数値の有限性は `schema._check_type`。
- **非対応型/能力欠如は E_PRECONDITION / CAPABILITY_UNAVAILABLE**（INTERNAL にしない）。**破壊的 mesh 編集は共有ガード**。**fingerprint は操作の本質に合わせる**。
- ruff / format / pyright（新規 0）緑で commit。

## 6. 仕上げ（M6/M7 と同じ運用）
1. 機能ごとに日本語コミット（feat/fix・Co-Authored-By）。意図単位。
2. push → `gh pr create --base main`。
3. レビュー: Codex（`@codex review`）が**利用上限**なら **独立3視点のサブエージェント・セルフレビュー**（設計 / 敵対的 correctness / 仕様・テスト）で代替。指摘対応→push。マージはユーザー判断。
4. 各サブPR マージ後に HANDOFF 進捗を更新。**M8 完了で `.handoff/NEXT-M9.md`（ファイルI/O）へ**。

## 7. 参照
- `plan.md §4 M8` / `spec.md §シナリオ1–3 + §10 受け入れ基準` / `contracts/methods.md §シナリオ1–3`
- 実装参考: `gateway.origin_set`/`set_origin_world`（T8.1 既存）/ `apply_transform`（bake-rotation 再利用）/ `bmesh_ops`（mesh チェックの自前計算）/ `capability.py`（print3d 能力検出）/ `ops._ok_offload`（print-check 退避）/ `output_ref`（M5）。
- M0.5 グラウンドトゥルース: research.md 付録（STL=`wm.stl_export` / 3MF=STL フォールバック / print3d は両版 enable False＝要再スパイク）。
- 後続: M9 ファイルI/O（save/open/import/export）。M10 非同期 job（heavy の正式対応・boolean/decimate/import を job 化）。M11 exec-python。M12 Skill 同梱。
