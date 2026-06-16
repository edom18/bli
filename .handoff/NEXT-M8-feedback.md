# 次の作業 — M8 実地フィードバック対応ワークストリーム（残: PR-4 → PR-5 → T8.5）

最終更新: 2026-06-16 / 前提: **M0–M7 + M8 T8.1–T8.4 完了。実地フィードバック PR-1〜PR-3 main マージ済み（PR #13/#14/#15）**。
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
| **PR-4** | **#4 基準指定整列** | ⬜ **次** | 下記 §2（kickoff 判断あり・支柱問題の本丸） |
| PR-5 | #3 undo 公開 | ⬜ | 下記 §3 |

→ **PR-4・PR-5 後 → T8.5 print-export（§4）→ M8 完了 → M9**。

---

## 2. PR-4: 基準指定整列（FB #4・支柱問題の本丸）

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

---

## 3. PR-5: undo 公開（FB #3）
- **概要**: `gateway.push_undo`（既存・`gateway.py:74`）を CLI コマンド `bli undo` として露出。可逆性を「直前 transform の自力再構築」に頼らせず、試行錯誤の安全性を上げる。
- **注意点**:
  - `push_undo` は undo **境界を積む**だけ。実際に1ステップ戻すには `bpy.ops.ed.undo()` が必要 → これは bpy.ops なので **gateway 経由（run_operator）**。
  - **background では undo stack 挙動が不定**（M0.5: `ed.undo_push` は OK だが `ed.undo` の実発火は GUI 前提）。GUI スパイク（capture と同様 `blender.exe --python`）で挙動確認推奨。
  - 冪等性/状態: undo は「直前の dispatch を戻す」意味づけが曖昧になりやすい。スコープ（何ステップ・何を戻すか）を kickoff で確認。読み取り専用ではない（状態を変える）。
- **kickoff 判断**: `undo` の意味（1ステップ固定 / メッセージ指定 / セッション境界）と、GUI 必須にするか（background は CAPABILITY/PRECONDITION 縮退）。

---

## 4. その後: T8.5 print-export（M8 完了）
- `print-export`（stl / 3mf）。**3mf は標準で実体なし → STL フォールバック hint**（research.md D11/付録）。stl=`wm.stl_export`（M0.5 確定・capability.RESOLVERS）。
- `global_scale` は print-setup の表示単位/`scale_length` から**一本算出**（T8.3 で設計済み）。
- 完了で **M8 完了 → `.handoff/NEXT-M9.md`（ファイルI/O）を作成**（未作成）。

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
