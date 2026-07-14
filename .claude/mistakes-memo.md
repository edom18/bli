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

**Case: モジュール→パッケージ分割で、関数内 lazy import の相対深さを 1 箇所だけ変換し漏らした**
状況：P2-4 の gateway/ パッケージ化で `from . import exec_runner`（gateway/core.py・関数内）を
　`from .. import` へ変え忘れ、`bli_addon.gateway.exec_runner` を探して ModuleNotFoundError。
　関数内 import は実行時まで評価されず、かつ当該 glue は pytest では fake gateway 経由のため
　797 全緑のまま素通りし、実機 smoke の exec-python（audited 実行）だけが INTERNAL 化した。
→ 対策：①分割後は `grep -rn "from \. import" <pkg>/` で親レベル モジュール参照の残りを機械確認する
　（ops/ 分割の指示には入れて 0 件・gateway/ 側の指示に入れ忘れた）。②恒久ガードとして
　`test_package_relative_imports.py`（L1）が level=1 相対 import の参照先＝同一パッケージ内
　サブモジュール実在を AST で強制する。bpy 不要で全 lazy import を検証できる。

## レビュー運用（マルチエージェント）

**Case: レビュー中の未コミット修正が、Codex finder の巻き添え revert で消えた**
状況：P2-4 レビュー Round 2 で、orchestrator がレビュー指摘の修正を作業ツリーに書いた（未コミット）
　まま Codex finder を並走させていた。Codex CLI が read-only 指示に反して同じ 3 ファイルへ勝手に
　パッチを当て、それを検知した finder エージェントが `git restore` で HEAD へ戻した際、
　orchestrator の未コミット修正まで区別なく消えた（修正は再適用で復旧・実害はやり直し工数のみ）。
→ 対策：①レビュー修正は**ゲート緑を確認したら即コミット**してから次のエージェントを起動する
　（未コミット状態で外部 CLI を走らせるエージェントと並走しない）。②外部 CLI（Codex 等）を使う
　finder は起動前後で `git status --porcelain` を突き合わせ、**自分が作っていない変更を restore
　しない**（見つけたら報告のみ）。③長時間ハングした finder を待つ間に修正を進める場合も①を守る。

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

**Case: smoke golden が巨大 modifier を残置し、後続テストで depsgraph 評価が走ってメモリ爆発**
状況：P2-3 の clamp 検証 golden で `BEVEL segments=10000→1000`（rna clamp）の modifier をテスト用
　キューブに残したまま次の golden へ進んだ。追加時点では未評価だが、後続の BOOLEAN modifier 追加が
　依存グラフ評価を誘発 → 12 辺 × 1000 セグメントの BEVEL 評価でジオメトリ爆発（--background の
　blender.exe が数 GB 級のメモリを消費・ユーザーが Task Manager で発見）→ RPC TIMEOUT で smoke FAIL。
→ 対策：smoke で「重い評価を誘発し得るパラメータ（SUBSURF levels・BEVEL segments・ARRAY count 等）」
　を設定した modifier は、**assert 完了後すぐ remove** する（評価は遅延＝残すと後続のどこで評価が
　走るか予測できない）。極端値の clamp 検証は applied_props の読み戻しで完結し、評価は不要。
