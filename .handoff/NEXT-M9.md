# 次の作業 — M9「ファイルI/O（save / open / import / export）」

最終更新: 2026-06-17 / 前提: **M0–M8 実装完了**（M8 T8.5 print-export = PR #20）。M9 は spec D11「stl/obj/gltf(glb)/3mf/fbx 全て v1 必須」の import/export と .blend の save/open。
> まず `.handoff/HANDOFF.md`（全体史 + 規約 + §6e 再利用パターン + §6g M8 確定事項・特に **§6g T8.5 print-export**）を読む。出典: `plan.md §4 M9` / `spec.md §ファイルI/O` / `contracts/methods.md §ファイルI/O`。全体俯瞰は `.handoff/ROADMAP.md`。

## 0. 着手前（コピペ可）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout main && git pull origin main          # PR #20 マージ後
git checkout -b feature/m9-fileio                   # またはサブPRごとに分割
uv sync
PYTHONUTF8=1 uv run pytest -q                       # 257 passed（T8.5 まで）を確認
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
# 実機スモーク（両版）:
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background \
  --python packages/bli-addon/spikes/smoke_ops.py 2>&1 \
  | sed -n '/BLI_OPS_SMOKE_BEGIN/,/BLI_OPS_SMOKE_END/p'   # → OPS SMOKE OK
```

## 1. M9 スコープ（methods.md §ファイルI/O / spec.md）
| タスク | コマンド | params | 概要 | 状態 |
|---|---|---|---|:--:|
| T9.1 | `export` | `--format obj\|fbx\|gltf\|stl\|3mf` `--path` `--use-selection?` | 多形式 export（print-export の一般化・3mf は CAPABILITY） | ⬜ |
| T9.2 | `import` | `--format obj\|fbx\|gltf\|stl\|3mf` `--path` | 取込オブジェクト一覧（前後 diff） | ⬜ |
| T9.3 | `save` | `--path?` `--backup?` | .blend 保存（上書きは既定 backup） | ⬜ |
| T9.4 | `open` | `--path` | .blend を開く（**常駐サーバへの影響を要検証**） | ⬜ |

- **サブPR分割推奨**（M6/M7/M8 と同様）。順序の推奨: **T9.1 export（T8.5 print-export の作法をそのまま一般化＝最も低リスク）→ T9.2 import → T9.3 save → T9.4 open（最高リスク）**。
- **DoD**: 各形式で往復（export→import で頂点数/bbox 一致の golden）。両版（5.0.1/4.4.3）同値。

## 2. 着手前に必須のスパイク（M0.5 的・両版・research.md に §E9 として残す）
NEXT-M8 §2 と同じ流儀。`spikes/*_spike.py` + `BLI_*_SPIKE_BEGIN/END`。
1. **export 各形式の引数集合（最重要・形式ごとに異なる）**: `wm.obj_export` / `export_scene.gltf` / `export_scene.fbx` の rna プロパティをダンプし、**selection 制御の param 名**（stl=`export_selected_objects` / obj=`export_selected_objects`? / gltf=`use_selection` / fbx=`use_selection`）・scale・`filepath`/`check_existing` を確定。→ OperatorResolver に**形式別の引数マップ**を持たせる必要があるか判断（print-export は STL 単体だったが M9 は多形式）。`print_export_spike.py` の `rna_props` を流用できる。
2. **import 各形式 + 取込結果の特定**: `wm.obj_import` / `import_scene.gltf` / `wm.stl_import` / FBX import（**`wm.fbx_import`(5.0)→`import_scene.fbx`(両対応)** の版差・§付録C）。**import 前後の `bpy.data.objects` 差分**で「取り込んだオブジェクト」を特定する方式を確認。3mf import は両版 stub（§E8 と同様）→ CAPABILITY。
3. **`open`/`save` の常駐サーバ安全性（最高リスク）**: `wm.open_mainfile(filepath=...)` は**シーン全体を置換**する。常駐 Blender + TCP サーバ + Dispatcher timer が `open_mainfile` 後も生存するか（ハンドラ/タイマ/`bpy.app.timers` が外れないか）を GUI スパイクで検証。外れるなら open 後に再登録が要る（`__init__.register` 相当）。`save_as_mainfile` の backup（`.blend1`）挙動と `check_existing`/`copy` も確認。
4. **3mf**: import/export とも両版 stub（§E8 確定）→ CAPABILITY_UNAVAILABLE + hint。

## 3. キックオフ判断ポイント（着手時にユーザー確認・推奨を併記）
1. **export のセレクタ**: print-export は単一（require_single）だったが、`export` は `--use-selection?` で**現在の選択集合**または**シーン全体**を出す（複数前提）。推奨: `--use-selection` 指定時は選択集合・省略時はシーン全体（各 exporter の selection param へ写像）。print-export との棲み分け（print-export=単一+global_scale 一本化の 3Dプリント特化 / export=汎用多形式）。
2. **export の scale**: print-export の `global_scale` 一本化を踏襲するか、汎用 export は scale 既定 1.0 で素通しか。推奨: 汎用 export は scale=1.0 既定（print-export が 3Dプリント用の scale 窓口）。
3. **import の結果報告**: 前後 diff で取込オブジェクト名一覧 + count。required_mode=OBJECT。大量取込は output_ref 退避（`_ok_offload`）。
4. **open の安全性**: 常駐サーバ生存をスパイクで確認後、open 後に必要なら timer/handler を再登録。`--background` での open/save は可（GUI 不要）だが、open はセッション破壊的なので確認系（fingerprint/scene-info）を返す。
5. **save の backup**: 既定で上書き時 `.blend1` backup（spec『上書きは既定でバックアップ』）。`--path` 省略時は現在の .blend へ（未保存なら USER_INPUT）。
6. **3mf**: import/export とも CAPABILITY_UNAVAILABLE（§E8・print-export と同じ縮退）。

## 4. 実装手順（M6/M7/M8 と同じ流儀・特に T8.5 を踏襲）
- **A. SSOT**: `definitions.py` に `export`/`import`/`save`/`open`（`implemented=True`・stable）。形式 ENUM。条件付き必須は ops で検証。
- **B. gateway/接点層**: `export_<generic>` / `import_<generic>`（**`resolve_export_operator`/`resolve_import_operator` で形式→operator 解決**・print-export の `export_stl` を一般化／形式別引数マップ）。生 bpy.ops は gateway のみ（`run_operator`）。import は前後 diff。open/save は `wm.open_mainfile`/`wm.save_as_mainfile`。
- **C. ops**: 各ハンドラ（検証 → 能力解決（対象非依存なので先）→ require_* → 破壊系ガード → 実行 → `_ok`）。`resolve_export_operator`（既存・gateway）/ `_capability_unavailable`（既存・ops）/ `_file_sha256_size`（既存・ops）を再利用。
- **D. CLI**: 各サブコマンド（`--format`/`--path`・human 出力）。
- **E. テスト/smoke**: L1（形式 ENUM/必須/不在 path）+ 実機 smoke（**各形式 export→import 往復で頂点数/bbox golden**・3mf=CAPABILITY・両版同値）。open はセッション破壊的なので smoke では一時 .blend に save→open→scene-info の最小往復。

## 5. 必ず守る規約（HANDOFF §8 / §6e）
- bli-core 純Python・依存ゼロ。**AST guard**: 生 `bpy.ops` は gateway のみ（`run_operator` 経由）。
- ops は gateway/bpy を遅延 import。検証は bpy 前（`_require_input`）。能力欠如は `CAPABILITY_UNAVAILABLE`、非対応型は `E_PRECONDITION`（INTERNAL にしない）。
- 出力ファイルは sha256/size + content-address fingerprint（print-export と同流儀）。
- ruff / format / pyright（新規 0）緑で commit。日本語コミット + prefix。main 直接禁止・PR 経由（マージはユーザー判断）。
- レビュー: Codex 上限時は **独立3視点セルフレビュー**（設計 / 敵対的 correctness / 仕様・テスト）。

## 6. 参照
- `plan.md §4 M9` / `spec.md §ファイルI/O` / `contracts/methods.md §ファイルI/O`
- 実装参考: **`gateway.export_stl`/`resolve_export_operator`（T8.5・export の雛形）** / `ops._print_export`（能力解決→ガード→ファイル統計の順） / `capability.py RESOLVERS`（import/export の確定 operator 候補・obj/gltf/fbx/stl ✅・3mf stub・FBX import 版差） / `output_ref`（大量取込の退避）。
- M0.5 グラウンドトゥルース: research.md 付録C（STL=`wm.stl_export/import`・OBJ=`wm.obj_export/import`・glTF=`export_scene.gltf`/`import_scene.gltf`・FBX export=`export_scene.fbx`・**FBX import 版差=`wm.fbx_import`(5.0)→`import_scene.fbx`**・3MF は両版 stub）。研究 §E8（print-export の確定値）。
- 後続: M10 非同期 job（import/boolean/decimate の job 化・heavy の正式対応・`--dry-run` 一般化）。M11 exec-python。M12 Skill 同梱。
