# 過去のミスと対策（bli プロジェクト）

規約 `mistakes.md` に基づく実プロジェクトの記録。同じ失敗を繰り返さないため、やらかし・
落とし穴に気づいたら **Case / 状況 / 対策** で追記する。初期エントリは M0〜M14 で実際に
踏んだ罠（HANDOFF の「落とし穴」記録）から起こした。

---

## Blender / bpy API

**Case: operator の実在を `hasattr` で判定した**
状況：`hasattr(bpy.ops..., name)` は旧名の stub も True を返し、実体のない operator を誤検出する。
→ 対策：operator 実在は `get_rna_type()` が成功するかで判定する（M0.5 確定・`capability.operator_real`）。

**Case: `bpy.app.timers` がテストで発火せず固まったように見えた**
状況：`--background` では `bpy.app.timers` が発火しない。ディスパッチ pump も回らない。
→ 対策：background 実機テストはメインスレッド手動 pump で近似する（`smoke_ops.py`）。GUI 常駐
　での実発火は GUI スパイク / L4 手動検証で別途確認する。

**Case: `bpy.data.is_dirty` で未保存判定しようとした**
状況：dispatch（pump タイマ）文脈では save 後も reset されず、background では常時 True ＝信頼不可。
→ 対策：未保存追跡は自前の純Python `session_state` で行う（M9 open・mutate 前に pessimistic に modified）。

**Case: `wm.stl_export` の対象を `temp_override` で絞ろうとした**
状況：`wm.stl_export` は永続的な選択フラグ（`export_selected_objects`）を見るため、temp_override
　では対象を絞れない。
→ 対策：選択を save→set→restore で一時変更してから export し、非破壊に戻す（`export_stl`・§E8）。

**Case: glTF export で `GLTF_EMBEDDED` を指定した**
状況：`export_format` の有効値は両版とも `GLB` / `GLTF_SEPARATE` のみ。EMBEDDED は存在せず無効 enum で INTERNAL 化。
→ 対策：実機で enum 値をダンプして確定する。bli は GLB 単一固定（`--path` は `.glb` 必須）。

**Case: undo スタック端で `CANCELLED` を期待した**
状況：スタック端の `ed.undo()` は両版とも `RuntimeError('poll() failed...')` を投げる（CANCELLED ではない）。
→ 対策：`_step_undo_stack` の try/except で RuntimeError も break 扱いにし applied を頭打ちにする。

## Python / パッケージング

**Case: アドオンが `import bli_core` を解決できない前提を見落としていた**
状況：Blender 埋め込み Python（3.11・venv なし）は dev の uv workspace を知らない。配布物に
　bli-core を同梱しないと実機で import 失敗する。
→ 対策：配布 zip ビルド（`scripts/build_addon.py`）で bli-core を `vendored/bli_core/` へ同梱し、
　`_ensure_bli_core_on_path()` が `vendored/` を sys.path に載せる（M14）。検証は `python -S` 隔離で。

**Case: `tomllib` をどの Python でも使える前提にした**
状況：`tomllib` は 3.11+。アドオンは 3.11 で動くが、bli-core は 3.10 互換・依存ゼロを厳守する層。
→ 対策：tomllib 利用は addon 側（3.11）に閉じ込める。bli-core には外部依存も 3.11 専用 API も入れない。

## テスト / CI

**Case: `--help` の出力に文字列マッチするテストを書いた**
状況：rich の `--help` レンダリングは端末幅依存。CI（80桁）で改行が変わり偽陰性になった。
→ 対策：登録済み click オプション名（`param.opts`）を直接検証する。表示文字列に依存しない。

**Case: setup-blender@v5 が Blender 5.0 を解決できないことがある**
状況：CI の L2 マトリクスで 5.0 のバージョン解決が不確実。
→ 対策：5.0 を必須ゲート / 4.4 を continue-on-error にし、解決できない時は CI 側で version 調整する
　（実装ロジックは正）。M13 `test-blender.yml`。

**Case: Windows のソケット切断を正常系だけで扱った**
状況：Windows では切断が RST（ConnectionReset）になり得る。空 recv だけ想定すると落ちる。
→ 対策：テストは空 recv と RST の両方を許容する。

**Case: フェイク `bpy` を差し込んで `bli_addon.gateway` を直接 import するテストで、後片付けを
`sys.modules.pop("bli_addon.gateway", None)` だけにした**
状況：`from . import gateway`（ops.py 等の相対 import）は、親パッケージ `bli_addon` に既に
　`gateway` 属性があれば **sys.modules を経由せずそれを直接使う**（Python の import 属性キャッシュ）。
　sys.modules から pop するだけでは `bli_addon.__dict__["gateway"]` が残り、フェイク bpy を積んだ
　モジュールが後続テストへ漏れる。他テストは「bpy 無し＝ModuleNotFoundError」を前提にしており、
　`AttributeError`（フェイク bpy の未実装属性）に化けて誤検出しにくい形で壊れた。
→ 対策：後片付けは `sys.modules.pop("bli_addon.gateway", None)` **と**
　`sys.modules["bli_addon"].__dict__.pop("gateway", None)` の両方を行う（`test_gateway_targets.py`
　の `_forget_gateway_module()`）。同じ罠は他の bpy 依存サブモジュールをフェイクで直接テストする
　ときにも当てはまる（P1-1/P1-2 で同様のテストを書く際は要注意）。
