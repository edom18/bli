# 次の作業 — M8 実地フィードバック対応ワークストリーム（**PR-1〜5 完了 → 残: T8.5**）

最終更新: 2026-06-16 / 前提: **M0–M7 + M8 T8.1–T8.4 完了。実地フィードバック PR-1〜PR-5 全て main マージ済み（PR #13/#14/#15/#17/#18・docs は #16）**。
> **このワークストリームは完了。次に着手すべきは T8.5 print-export（§4）= `.handoff/NEXT-M8.md`。それで M8 完了 → M9。**
> まず `.handoff/HANDOFF.md`（全体史 + 規約 + §6e 再利用パターン）と `.handoff/ROADMAP.md`（俯瞰）を読む。
> 出典: `FEEDBACK-straighten-2026-06-15.md`（実地検証レポート・全7項目）。
> このワークストリームは「3シナリオを**エージェントが実際に使える**ようにする」差し込み。**feedback-first**（T8.5 print-export より前）。

## 0. 着手前（コピペ可）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout main && git pull origin main          # PR #15 まで反映済み
git checkout -b feature/m8-fb-reference             # PR-4（例）
uv sync
PYTHONUTF8=1 uv run pytest -q                       # 228 passed を確認（M8 fb PR-3 まで）
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src   # OK
# 実機 smoke（両版・background）:
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background \
  --python packages/bli-addon/spikes/smoke_ops.py 2>&1 \
  | sed -n '/BLI_OPS_SMOKE_BEGIN/,/BLI_OPS_SMOKE_END/p' | tail -3   # → OPS SMOKE OK
```

## 1. ワークストリーム状況
| PR | 対応（FB） | 状態 | 要点 |
|:--:|---|:--:|---|
| PR-1 | #7 横断クイックウィン | ✅ #13 | CLI UTF-8 出力固定（`_force_utf8_output`）・全 `--targets` に `--target` 別名・dimensions/bbox 文書化 |
| PR-2 | #5/#2/#6 straighten 根本修正 | ✅ #14 | pca `--up-hint current`（最小回転で反転防止）・`tilt_from_up_deg`・`--dry-run`（snapshot/restore で非破壊・bake と排他） |
| PR-3 | #1 capture | ✅ #15 | `capture --source viewport\|screen\|render`・PNG を outputs_dir に content-address 名・実解像度は PNG IHDR・GUI 必須（background は E_PRECONDITION）・`output_ref.offload_file` 集約 |
| PR-4 | #4 基準指定整列 | ✅ #17 | `straighten --method angle\|align-vector\|reference`（下記 §2）。支柱問題は align-vector（向きを数値指定）で実用解。部分 PCA は別 PR 繰越 |
| PR-5 | #3 undo/redo 公開 | ✅ #18 | `bli undo`/`redo --steps N`（下記 §3）。bare `ed.undo/redo`・GUI 必須（background は E_PRECONDITION）・スタック端 RuntimeError 頑健化（研究 §E7） |

→ **実地フィードバック PR-1〜5 完了。残るは T8.5 print-export（§4・着手書は `.handoff/NEXT-M8.md`）→ M8 完了 → M9**。

---

## 2. PR-4: 基準指定整列（FB #4・支柱問題の本丸）✅ **完了（PR #17 マージ済み）**

**確定スコープ（kickoff: ユーザー選択 (b)）+ 実装結果**: `straighten` に基準指定 method 3種を追加。
- **angle**: world 軸 `--axis X|Y|Z` まわりに `--degrees`（符号で向き）回転。
- **align-vector**: `--from-dir`(world) を `--to-dir`(world・**省略時 up**) へ最小回転で合わせる。**向きを数値で渡せば同一メッシュ内の支柱でも整列でき、`transform --mode delta` 手計算迂回を解消**（支柱問題への実用解）。
- **reference**: 参照 obj の `--ref-axis`(signed local・省略時 up_axis) world 方向へ、対象の `--axis`(local・省略時最近軸) を合わせる（`_world_align` の目標を up→参照軸へ差し替え）。
いずれも object 回転のみ・既存作法を継承（presence-sensitive 検証・bpy 前必須/ゼロベクトル/自己参照=USER_INPUT・gateway も None ガードで E_PRECONDITION・dry-run 厳密復元・bake 共有ガード・fingerprint 使い分け）。result: angle=`{axis,degrees}` / align-vector=`{from_dir,to_dir,from_world_after,angle_deg}` / reference=`{axis,aligned_world,reference,ref_axis,reference_world}`。両版 smoke golden 緑（angle Z45→[0,0,45] / align-vector tilt20°→+Z / reference は参照軸[sin25,0,cos25]へ整列し world up と区別）。**繰越**: 部分ジオメトリ PCA（頂点サブセット基準）は部分指定方法（頂点グループ/参照領域/world bbox）の決定が要るため別 PR。`straighten_object`/`_straighten` の if/elif 7 method テーブル化（非緊急）。詳細は spec.md §S2 / methods.md シナリオ2。

<details><summary>（参考）着手前の設計空間と kickoff 判断の記録</summary>

### 背景（FB §1–§5 の核心）
- 実地対象はスキャンメッシュ1個。傾きが **object 回転ではなく mesh 形状に焼き込まれている**（object rotation は綺麗な `[90,0,0]`）。
- 依頼の「支柱」は**別オブジェクトではなく同一メッシュの一部**。`straighten --method pca`（全体 PCA）では「支柱だけを基準」を表現できなかった。
- フィールドのエージェントは結局、補正回転を**手計算して `transform`（delta 回転）で適用**して回避した（PR-2 の up_hint/dry-run で最小回転自体は安全に出せるようになったが、「基準を指定する」手段はまだ無い）。

### 設計空間（FB の案）
1. **明示角度/ベクトル method**: `straighten --method angle`（`--axis X|Y|Z` + `--degrees D`）または `--method align-vector`（`--from x,y,z --to x,y,z`）。エージェントが**算出した補正を straighten 経由で安全に適用**（dry-run/bake/共有ガードの作法込み）。`transform` への迂回を不要にする。**最も実用的・低リスク**。
2. **参照オブジェクト整列**: `straighten --method reference --reference <obj> [--ref-axis ...]`。対象の向きを別オブジェクトの軸に合わせる。ガイド用の別オブジェクトがある場合に有効（同一メッシュ内の支柱は直接は解けない）。
3. **部分ジオメトリ PCA**（本丸だが最難）: 頂点サブセットで PCA。**非対話で部分をどう指定するか**が未解決の核心:
   - **頂点グループ**（`--vertex-group <name>`）: 名前で永続・決定的。ただしスキャンメッシュに既存グループは無い → 別途グループ作成手段が要る。
   - **参照オブジェクトの領域**: ガイドオブジェクトの bbox 内の頂点だけで PCA。
   - **world bbox 指定**: `--region xmin,..` 等。エージェントが領域を数値指定。

### kickoff 判断（次セッションで**ユーザー確認**・推奨併記）
- **Q1. PR-4 v1 スコープ**:
  - (a) **明示角度/ベクトル method のみ**（推奨・最小で「補正を安全に適用」を満たす・`transform` 迂回を解消）
  - (b) (a) + 参照オブジェクト整列（`--reference`）
  - (c) (a)(b) + 部分ジオメトリ PCA（本丸・ただし部分指定方法の決定が必要）
- **Q2.（c を採る場合）部分指定の方法**: 頂点グループ / 参照オブジェクト領域 / world bbox のどれを v1 に。
- **Q3. 既存 method との重複整理**: 明示角度は `transform --mode delta` の回転と機能が重なる。straighten 側に置く価値は「up-axis 文脈・tilt レポート・bake/共有ガードの一貫作法」。重複を許容するか、straighten 専用の付加価値（tilt 検証・接地連携）を明確化するか。
- 推奨: **(a) を v1 コア**にし、reference は安価なら同梱。部分 PCA（本丸）は「部分指定方法」の決定が要るため、Q2 の結論次第で PR-4 に含めるか別 PR に切る。

### 実装方針スケッチ（M6/M7/M8 と同流儀・§6e 厳守）
- **SSOT**: `definitions.py` の `straighten` に method を追加（`angle`/`align-vector`/`reference` 等）。method 別の条件付き必須 param は ops で検証（`--axis`/`--up-hint` が method 専用なのと同流儀・presence-sensitive）。
- **gateway**: `straighten_object` の method 分岐に追加（`gateway.py` の `_world_align`/`_principal_axis`/`_rotation_to`/`_apply_world_rotation` を再利用）。明示角度は `mathutils.Matrix.Rotation` or 軸+角で delta quaternion を作り `_apply_world_rotation`。align-vector は `_rotation_to(from_world, to_world)`。reference は参照 obj の matrix_world から目標軸を取る。**dry-run（PR-2 の snapshot/restore）と bake（共有ガード）の作法をそのまま継承**。
- **数値の有限性**は `schema._check_type`（VEC3/FLOAT は nan/inf 弾き済み）。角度・ベクトルも同様に。
- **CLI**: `straighten` に method 別オプション追加（presence で送信）。human 出力に新 method を反映。
- **テスト**: L1（method/必須 param 検証・bpy 到達前）+ 実機 smoke（5.0/4.4 両版・golden）。部分 PCA を入れるなら fixture（頂点グループ付き mesh 等）追加。
- **着手前スパイク**（必要なら）: 参照/頂点グループ API を 5.0.1/4.4.3 で確認し research.md に確定値。

### 参照（既存コード）
- `packages/bli-addon/src/bli_addon/gateway.py`: `straighten_object`(method 分岐) / `_principal_axis`(up_hint) / `_world_align` / `_rotation_to`(anti-parallel 決定化) / `_apply_world_rotation`(decompose→LocRotScale) / `_snapshot_transform`/`_restore_transform`(dry-run)。
- `packages/bli-addon/src/bli_addon/ops.py`: `_straighten`（method 別検証・dry/bake 排他・共有ガード）。
- `packages/bli-cli/src/bli/main.py`: `straighten` コマンド。
- `packages/bli-addon/spikes/smoke_ops.py`: `ensure_straighten_fixtures` / straighten smoke 群（StrPCADown 等を参考に新 fixture）。
- contracts/methods.md・spec.md のシナリオ2行（PR-2 で up_hint/dry-run/tilt 追記済み）。
</details>

---

## 3. PR-5: undo/redo 公開（FB #3）✅ **完了（PR #18 マージ済み）**

**確定（kickoff: ユーザー選択）+ 実装結果**: `bli undo` / `bli redo --steps N`（1〜`runtime.MAX_UNDO_STEPS`=100）。グローバル undo スタックを steps 段戻す/進める。
- **gateway** `undo_steps`/`redo_steps` = bare `bpy.ops.ed.undo()`/`ed.redo()` を steps 回（**GUI で context override 不要**・研究 §E7）。`_step_undo_stack` がスタック端を正規化（`FINISHED` 以外 **および RuntimeError** の両方を break＝端で applied 頭打ち・INTERNAL 化回避）。**スタック端は両版とも `RuntimeError('poll() failed, context is incorrect')` を投げる**（spike で確証）。`_require_gui_for_undo`=`bpy.app.background`→E_PRECONDITION（capture と同流儀）。`scene_state_fingerprint`（name/type/matrix_world の粗いシーン指標・mesh 内部編集は捉えない）。
- **ops** `_do_undo_redo` 共通ヘルパ（steps 範囲を bpy 前検証・上限 runtime 集約）。CLI も送信前に弾く（duplicate と同流儀）。`mutates=True`・`Mode.ANY`。result `{requested, applied}`。
- **GUI スパイク** `spikes/undo_spike.py`（GUI モード実行）で 5.0.1/4.4.3 確認（実巻き戻し/redo/複数段/matrix_world 確定/スタック端 RuntimeError）。background smoke は E_PRECONDITION 縮退・steps 範囲外 INVALID_PARAMS。
- **繰越**: redo スタックは新規操作で消える（Blender 仕様・v1 許容）。fingerprint は粗い（mesh 内部編集 undo は前後同値になり得る）。詳細は spec.md / methods.md「状態操作」/ research §E7。

---

## 4. ★次に着手: T8.5 print-export（これで M8 完了）
> **詳細な着手書・キックオフ判断は `.handoff/NEXT-M8.md`**（T8.5 行 + §2 スパイク3 + §3 kickoff）。
- `print-export`（stl / 3mf）。**3mf は標準で実体なし → STL フォールバック hint**（research.md D11/付録）。stl=`wm.stl_export`（M0.5 確定・capability.RESOLVERS）。
- `global_scale` は print-setup の表示単位/`scale_length` から**一本算出**（T8.3 で設計済み・unit_settings から導出）。
- 着手前スパイク: 3mf addon（`io_mesh_3mf`）有無の分岐と STL hint・`wm.stl_export` の引数（`global_scale`/`apply_modifiers`/`ascii`?）を 5.0.1/4.4.3 で確認し research に確定値（§E8）。
- ファイル書き出しは `outputs_dir` ではなくユーザー指定 `--path`（capture とは別系統・パス安全性に注意）。`--apply-transform` 等のオプションは kickoff で確定。
- 完了で **M8 完了 → `.handoff/NEXT-M9.md`（ファイルI/O save/open/import/export）を作成**（未作成）。M9 は print-export と実装が重なる領域があるため設計を引き継ぐ。

## 5. 必ず守る規約（HANDOFF §8 / §6e・再掲）
- bli-core 純Python・依存ゼロ。生 `bpy.ops` は gateway のみ（AST guard）。検証は bpy 前（`_require_input`）。
- 非対応型/能力欠如は E_PRECONDITION / CAPABILITY_UNAVAILABLE（INTERNAL にしない）。破壊的 mesh 編集は共有ガード。
- presence-sensitive な op 専用 param は schema default を持たせない。暴走しうる数値は範囲を bpy 前に弾き上限は runtime 定数に集約。
- fingerprint は操作の本質に合わせる（mesh 変化=mesh_fingerprint / object transform=object_fingerprint / 読み取り=対象 fingerprint）。
- ruff / format / pyright（新規 0）緑で commit。**新規 pyright を増やさない**（既存: `main.py` narrowing / `gateway.py:196` object_summary / `ops.py:386` _material）。

## 6. 仕上げ（M6/M7/M8 と同じ運用）
1. 機能ごとに**日本語コミット**（feat/fix・`Co-Authored-By: Claude ...`）。意図単位。
2. push → `gh pr create --base main`。Issue/マイルストーンは無し（PR のみ運用）。
3. レビュー: Codex 上限のため **独立3視点セルフレビュー**（agent type `spec-workflow:software-design-reviewer` 接頭辞必須 + general-purpose×2）→ P1 必修 / P2 対応 or 正当化 / P3 任意。
4. 実機 smoke は **5.0.1 / 4.4.3 両版**。GUI 必須機能は GUI スパイク（`blender.exe --python ...`・末尾 `wm.quit_blender()` 済み）。
5. マージはユーザー判断。各 PR マージ後に **HANDOFF / ROADMAP / この NEXT を更新**。
