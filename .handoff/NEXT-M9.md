# 次の作業 — M9「ファイルI/O」: 残り **T9.4 open**（M9 最後・最高リスク）

最終更新: 2026-06-17 / 前提: **M0–M8 完了 + M9 T9.1〜T9.3 完了（PR #21/#22/#23 main マージ済み）**。残るは **T9.4 open** のみ。
> まず `.handoff/HANDOFF.md`（全体史 + 規約 + §6e 再利用パターン + **§6i M9 確定事項**）を読む。M9 のグラウンドトゥルースは `research.md §E9`（export/import）/ `§E10`（save）。出典: `spec.md §ファイルI/O` / `contracts/methods.md §ファイルI/O`（export/import/save の result 注記行は実装に同期済み）。全体俯瞰は `.handoff/ROADMAP.md`。

## 0. 着手前（コピペ可）
```bash
cd "D:/MyDesktop/PythonProjects/blender-auto-cli"
git checkout main && git pull origin main          # PR #23 マージ後
git checkout -b feature/m9-open                     # T9.4 は単独サブPR
uv sync
PYTHONUTF8=1 uv run pytest -q                       # 284 passed（T9.3 まで）を確認
uv run ruff check . && uv run ruff format --check .
PYTHONUTF8=1 uv run python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src
# 実機スモーク（両版・export/import/save golden 含む）:
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --background \
  --python packages/bli-addon/spikes/smoke_ops.py 2>&1 \
  | sed -n '/BLI_OPS_SMOKE_BEGIN/,/BLI_OPS_SMOKE_END/p'   # → OPS SMOKE OK
```

## 1. M9 スコープと状態（methods.md §ファイルI/O / spec.md）
| タスク | コマンド | params | 概要 | 状態 |
|---|---|---|---|:--:|
| T9.1 | `export` | `--format obj\|fbx\|gltf\|stl\|3mf` `--path` `--targets?` `--use-selection?` | 多形式 export（print-export の一般化・3mf は CAPABILITY） | ✅ PR #21 |
| T9.2 | `import` | `--format obj\|fbx\|gltf\|stl\|3mf` `--path` | 取込オブジェクト一覧（前後 diff） | ✅ PR #22 |
| T9.3 | `save` | `--path?` `--backup?` | .blend 保存（上書きは既定 backup） | ✅ PR #23 |
| T9.4 | `open` | `--path` | .blend を開く（**常駐サーバ生存を要検証＝最高リスク**） | ⬜ 次はここ |

- **サブPR分割**（各 PR は独立3視点セルフレビュー済み）。T9.1〜T9.3 は §6i に確定要約。
- **T9.1〜T9.3 で確立した M9 共通基盤**（T9.4 でも踏襲）: `capability.RESOLVERS`（import/export 全形式の確定 operator）/ `gateway._resolve_op`（"ns.name"→operator）/ 能力解決を対象解決より前 / 3mf=CAPABILITY_UNAVAILABLE / path は `os.path.abspath` 正規化 / 出力先・入力 dir/file 存在は bpy 到達前に USER_INPUT / 副作用後の I/O 失敗は E_OPERATOR（INTERNAL にしない）。
- **DoD（T9.1〜T9.3 達成済み）**: 各形式で往復（export→import で world bbox 一致の golden・両版同値）。save は .blend1 backup の決定的制御。

## 2. 着手前に必須のスパイク（M0.5 的・両版・research.md に §E9 として残す）
NEXT-M8 §2 と同じ流儀。`spikes/*_spike.py` + `BLI_*_SPIKE_BEGIN/END`。
1. **export 各形式の引数集合（最重要・形式ごとに異なる）**: `wm.obj_export` / `export_scene.gltf` / `export_scene.fbx` の rna プロパティをダンプし、**selection 制御の param 名**（stl=`export_selected_objects` / obj=`export_selected_objects`? / gltf=`use_selection` / fbx=`use_selection`）・scale・`filepath`/`check_existing` を確定。→ OperatorResolver に**形式別の引数マップ**を持たせる必要があるか判断（print-export は STL 単体だったが M9 は多形式）。`print_export_spike.py` の `rna_props` を流用できる。
2. **import 各形式 + 取込結果の特定**: `wm.obj_import` / `import_scene.gltf` / `wm.stl_import` / FBX import（**`wm.fbx_import`(5.0)→`import_scene.fbx`(両対応)** の版差・§付録C）。**import 前後の `bpy.data.objects` 差分**で「取り込んだオブジェクト」を特定する方式を確認。3mf import は両版 stub（§E8 と同様）→ CAPABILITY。
3. **`open`/`save` の常駐サーバ安全性（最高リスク）**: `wm.open_mainfile(filepath=...)` は**シーン全体を置換**する。常駐 Blender + TCP サーバ + Dispatcher timer が `open_mainfile` 後も生存するか（ハンドラ/タイマ/`bpy.app.timers` が外れないか）を GUI スパイクで検証。外れるなら open 後に再登録が要る（`__init__.register` 相当）。`save_as_mainfile` の backup（`.blend1`）挙動と `check_existing`/`copy` も確認。
### T9.1〜T9.3 で消化済みのスパイク（参考・再実行不要）
- `spikes/fileio_spike.py`（§E9）: export/import 各形式の引数集合・selection param 形式別・往復 bbox・FBX import 版差・3mf stub。
- `spikes/save_spike.py`（§E10）: save_as_mainfile 引数・save_version backup 制御・magic 版差。

## 2. T9.4 open — 最高リスク（着手前に必須の GUI 残留スパイク）
`wm.open_mainfile(filepath=...)` は **シーン全体（.blend 全体）を置換**する。常駐 GUI サーバでの核心リスク:
- **`bpy.app.timers`（Dispatcher の pump タイマ）は file load で解除される**公算が高い → open 後にリクエストがハングする。
- 非 persistent な `bpy.app.handlers` も file load でクリアされる。
- TCP サーバ（Python スレッド）自体は bpy 状態と無関係なので生存見込み。

**必須 GUI スパイク `spikes/open_spike.py`（`blender.exe --python` で GUI 起動・両版・`BLI_OPEN_SPIKE_BEGIN/END`）**:
1. `bpy.app.timers.register` でダミー timer を登録 → `wm.open_mainfile(filepath=一時.blend)` → **timer が生存しているか**（`bpy.app.timers.is_registered`）を確認。
2. `bpy.app.handlers.load_post` に `persistent=True` ハンドラを登録 → open → 発火/生存するか確認（再登録の足がかり）。
3. 実際の addon（`bli_addon.__init__.register`）を有効化した状態で open → Dispatcher timer / server が生きているか（無理なら GUI 内で手動 register→open→ping 相当）。
4. `open_mainfile` の引数（`load_ui`/`use_scripts` 等）と、開いた後の `bpy.data.filepath` / scene 差し替えを確認。
→ **timer/handler が外れるなら、open 後に `load_post(persistent=True)` で Dispatcher timer（+必要なら server）を再登録する設計**（`__init__.register` の timer 部分を再利用）。

## 3. T9.4 キックオフ判断ポイント（着手時にユーザー確認・推奨を併記）
1. **常駐再登録**: スパイク結果次第。timer が外れるなら `load_post`(persistent) で `install_timer` 相当を再登録（server スレッドは生存見込みなので timer のみで足りるか確認）。推奨: スパイクで「外れる」と確定したら再登録ハンドラを `__init__.register` で常設。
2. **open の result**: open はセッション破壊的なので、確認系（`scene_summary` 相当 or 粗い fingerprint + 開いた path）を返す。推奨: `{path(絶対), scene, objects_count}` + fingerprint=`scene_state_fingerprint`。required_mode は ANY（open は現状モードに依存しない／開いた後 OBJECT になる）。mutates=True。
3. **入力検証**: `--path` 必須・`.blend` 必須（save と対称）・ファイル存在は bpy 到達前 USER_INPUT・abspath 正規化。壊れ .blend は E_OPERATOR（INTERNAL にしない）。
4. **smoke**: open はセッション破壊的 → 一時 .blend に `save`→`open`→`scene-info` の最小往復（background でも open は可）。GUI 常駐の timer 生存は GUI スパイクが担保（background smoke では timer を使わず手動 pump のため検証不可＝§8 の落とし穴と同じ）。

## 4. 実装手順（T9.1〜T9.3 と同じ流儀）
- **A. SSOT**: `definitions.py` に `open`（mutates=True・required_mode 要検討＝ANY 推奨・stable）。`--path` 必須。
- **B. gateway**: `open_blend(path)`（`run_operator(bpy.ops.wm.open_mainfile, filepath=path, ...)`・生 bpy.ops は gateway のみ）+ 必要なら `__init__` に `load_post` 再登録ハンドラ。
- **C. ops**: `_open`（path 空/.blend 以外/不在を bpy 前 USER_INPUT → abspath → open → 確認系 result）。`current_filepath`（既存・T9.3）を再利用。
- **D. CLI**: `open` サブコマンド（`--path`・human 出力）。
- **E. テスト/smoke**: L1（path 必須/.blend 以外/空）+ GUI スパイク（timer 生存）+ background smoke（save→open→scene-info 往復）。

## 5. 必ず守る規約（HANDOFF §8 / §6e）
- bli-core 純Python・依存ゼロ。**AST guard**: 生 `bpy.ops` は gateway のみ（`run_operator` 経由）。
- ops は gateway/bpy を遅延 import。検証は bpy 前（`_require_input`）。能力欠如は `CAPABILITY_UNAVAILABLE`、非対応型は `E_PRECONDITION`（INTERNAL にしない）。
- ruff / format / pyright（新規 0）緑で commit。日本語コミット + prefix。main 直接禁止・PR 経由（マージはユーザー判断）。
- レビュー: Codex 上限時は **独立3視点セルフレビュー**（設計 / 敵対的 correctness / 仕様・テスト）。

## 6. 参照
- 実装参考（T9.1〜T9.3）: `gateway.save_blend`/`current_filepath`（T9.3・open の雛形＝preference/state 操作）/ `gateway.import_generic`（壊れファイル→E_OPERATOR の except Exception 防御）/ `gateway._resolve_op` / `ops._save`（path 解決→検証→実行→result の順）/ `__init__.register`（Dispatcher `install_timer`／open 後の再登録に流用）。
- グラウンドトゥルース: research.md **§E9**（export/import）/ **§E10**（save・magic 版差・save_version）/ §E8（print-export）/ 付録C（operator 確定値・FBX import 版差）。
- **繰越（M9 完了後 or 別途）**: NUL バイト等の病的 path で `os.path.abspath`/`isdir` が `ValueError` 未捕捉→INTERNAL 化の恐れ（export/import/save 共通・信頼境界）。大量取込時の count inline 表示。GLTF_SEPARATE 対応。
- 後続: M10 非同期 job（import/boolean/decimate の job 化・`--dry-run` 一般化）。M11 exec-python。M12 Skill 同梱。
