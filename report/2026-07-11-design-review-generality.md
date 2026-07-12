# bli 設計・実装レビュー — 汎用性評価と修正方針

| 項目 | 内容 |
|------|------|
| 日付 | 2026-07-11 |
| 対象 | main（c928010・v1 = M0–M14 実装完了時点） |
| 目的 | ①uLoop CLI と同様の「汎用ツール」として使えるかの評価 ②設計・実装の全体レビュー ③次セッションへの修正方針の提示 |
| 参照 | hatayama/unity-cli-loop（README/README_ja/CLAUDE.md/SECURITY.md/docs/architecture を調査） |

---

## 0. エグゼクティブサマリ

**基盤の設計・実装品質は高い。v1 として完成度が高く、作り直しは不要。**
プロトコル（長さ接頭辞 JSON-RPC + HELLO トークン認証）、メインスレッド直列ディスパッチ、
非同期 job + request-status 後追い回収、watchdog 観測、構造化エラー（remediation 付き）、
SSOT スキーマ + ドリフト検出、トークン効率の発見系（list-commands / help --json / cli-schema.json）——
いずれも uLoop の「CLI ファースト」思想を正しく移植しており、一部（認証・冪等性・構造化エラー）は uLoop より堅牢。

**一方で、懸念どおり「3シナリオ（原点変更・直立補正・3Dプリント）過適合」は実在する。** 原因は装飾的なものではなく、**2つの構造的な設計差**にある：

1. **汎用性の担保方法が uLoop と逆転している。**
   uLoop は「`execute-dynamic-code` が Editor 操作の **90% をカバーする主軸**。専用コマンドは頻出の開発ループ操作のみ」
   という最小ツール哲学で、動的コード実行は既定 **Restricted**（ブロックリスト方式・Unity API は許可）で**すぐ使える**。
   bli の `exec-python` は「逃げ道」と位置づけられ**既定 off**、中間の audited は **sha256 許可リスト方式**のため
   エージェントが都度生成するアドホックなコードとは根本的に相性が悪い（毎回 policy.toml の手編集が必要）。
   → 構造化コマンドで表現できない操作は**事実上すべて不可能**になり、構造化コマンド表面の偏りがそのまま製品の限界になる。

2. **構造化コマンド表面が3シナリオ周辺に偏り、汎用の基本手段が欠落している。**
   straighten は 7 メソッド・print-* は 4 コマンドと精緻な一方、**オブジェクトの新規作成（cube/light/camera 等）が一切ない**、
   **モード切替コマンドがない**（E_MODE_MISMATCH は「OBJECT モードに切り替えてください」と言うが手段を提供しない）、
   rename / 親子付け / コレクション操作 / 表示切替なし、マテリアルは Base Color のみ、モディファイアは 5 種固定、
   FBX/glTF エクスポートに軸・スケール指定なし（Unity 連携の要所）。

**「Unity プロジェクトでのモデリング依頼・モデル修正」への適合度**：既存モデルの調整（import → 変形・修復・原点調整 → export）は
概ね可能だが、「ゼロからのモデリング」は最初のオブジェクト生成で詰み、「マテリアル調整」「Unity 向け FBX 設定」も不足（§2）。

**修正は「増築＋再フレーミング」で足りる。** 優先度付きの修正方針を §4 に示す。
P1（既知バグ修正 + exec-python の再位置づけ + 欠落プリミティブ第1弾 + Unity 向け export 拡張）だけで「uLoop と同様に使える」水準に到達できる。
なお詳細レビューで正当性バグを 4 件検出した（§3b(3)：終了コードの写像漏れ・regex 誤マッチ・マテリアル一覧不整合・save の例外非対称。いずれも現物確認済み・小粒）。

---

## 1. uLoop CLI との比較

### 1a. アーキテクチャ対応表

| 観点 | uLoop CLI | bli | 評価 |
|---|---|---|---|
| 常駐サーバ | Unity Editor 内 TCP ブリッジサーバ | Blender アドオン TCP サーバ（127.0.0.1 固定） | 同等 |
| メインスレッド実行 | ツールは Unity メインスレッドで実行 | `bpy.app.timers` pump によるメイン直列ディスパッチ | 同等（bli は watchdog 観測付きでより丁寧） |
| 認証 | 記載なし（ポート自動検出のみ） | HELLO ハンドシェイク + セッショントークン（hmac 比較） | **bli が優位** |
| エラー | コンパイルエラー配列＋修正ヒント | 構造化 ErrorObject（category/kind/retryable/remediation）＋終了コード 0–4 | **bli が優位** |
| 重量処理 | WaitForDomainReload 等の待機 | 非同期 job（accepted 即返＋request-status 後追い・lock-free 観測） | **bli が優位** |
| 冪等性 | 記載なし | `--id` UUIDv4 + RequestRegistry | **bli が優位** |
| エージェント発見 | Skills インストール（`uloop skills install`）＋ SKILL.md | Claude Code Skill 同梱 + `help --json` + cli-schema.json（ドリフト検出テスト） | 同等 |
| **動的コード実行** | **主軸**（90% カバー）。Disabled / **Restricted（既定・ブロックリスト）** / FullAccess の3段階 | **逃げ道**（既定 off）。off / audited（sha256 許可リスト） / trusted | **uLoop が優位（＝最大の乖離点）** |
| 専用コマンドの哲学 | 「頻出の開発ループ操作のみ」17 個前後の最小構成 | 33 個。3シナリオ特化が厚く、汎用プリミティブに欠落 | **uLoop が優位** |
| カスタムツール拡張 | Schema/Response/Tool 3点セット＋属性で自動登録 | SSOT（definitions.py）はあるが、追加には 5–6 箇所の手作業（§3） | uLoop が優位 |
| ログ取得 | `get-logs`（正規表現・スタックトレース検索） | なし（capture=画像のみ） | uLoop が優位 |

### 1b. 哲学の核心差

uLoop README_ja の設計原則（調査エージェントによる確認済み引用）：

> 「`execute-dynamic-code` が Editor 操作の 90% をカバーするため、専用ツールはフレーム単位の入力シミュレーションや
> 繰り返し呼ばれる開発ループ操作にのみ存在する」

つまり uLoop の汎用性は「専用コマンドの網羅」ではなく「**任意コード実行という汎用ゲートウェイ＋頻出操作の個別最適化**」で担保されている。
bli は spec.md §1 に「ユーザはモデリング初心者。原点・傾き・3Dプリンタ対応などの細かい調整が難しい」と**個人ユースケースが目的として明記**され、
SKILL.md も「3つの中核シナリオ（このプロダクトの主目的）」「3シナリオは exec 不要で完遂できる」という構成——
汎用性の担保が「3シナリオの範囲内」に留まっている。**ここが「使いたいケースを前提にしすぎている」感覚の正体。**

### 1c. bli が維持すべき優位点（修正で壊さないこと）

- **サーバ側 policy.toml が exec の真実源（CLI からの昇格不可・fail-closed・監査ログ）**：uLoop の設定は `.uloop/settings.permissions.json`（Git 管理可＝リポジトリ由来で昇格し得る）であり、bli の「リポジトリに mode=trusted を commit しても昇格しない」設計の方が supply-chain 耐性が高い。**この原則は維持したまま**、モードの実用性だけを上げる（§4 P1-1）。
- 構造化エラー＋remediation、冪等 `--id`、job モデル、watchdog、schema_hash ドリフト検出、能力検出（バージョン番号分岐禁止）。

---

## 2. 汎用性評価 — Unity エンジニアのユースケーストレース

想定：「Unity プロジェクトで使うモデルの作成・修正を AI エージェントに依頼する」

| # | 依頼例 | 必要な操作 | v1 での可否 |
|---|---|---|---|
| U1 | 「この FBX のピボットを足元にして書き出し直して」 | import fbx → object-info(bbox) → set-origin → export fbx | **◯ 可能**（ただし FBX 軸・スケール設定不可 → G6） |
| U2 | 「モデルのポリゴン数を半分にして」 | mesh decimate | **◯ 可能** |
| U3 | 「傾いてるモデルをまっすぐにして」 | straighten | **◎ 得意**（7 メソッド） |
| U4 | 「プロトタイプ用に樽のモデルを作って」 | **オブジェクト生成**（cylinder 等）→ mesh 編集 → material | **✕ 最初の生成で詰む**（G1） |
| U5 | 「マテリアルをメタリックにして/テクスチャを貼って」 | material の metallic/roughness/画像テクスチャ | **✕ Base Color RGBA のみ**（G5） |
| U6 | 「シーンを整理して（名前変更・親子・コレクション分け）」 | rename / parent / collection | **✕ コマンドなし**（G3） |
| U7 | 「Unity に取り込める glTF/FBX で出して」 | export + 軸/スケール/選択オプション | **△ 形式は可・オプション不可**（G6） |
| U8 | 「ライトとカメラを置いて確認画像を出して」 | ライト/カメラ生成 → capture | **✕ 生成不可**・capture は◯（G1） |
| U9 | 「Edit モードのまま放置された .blend を操作して」 | モード切替 | **✕ E_MODE_MISMATCH で全滅・切替手段なし**（G2） |
| U10 | 上記すべての「構造化コマンド外」の操作 | exec-python | **✕ 既定 off・audited はアドホックコード不適**（G0） |

### ギャップ一覧（修正方針 §4 に対応）

- **G0 [最重要]** exec-python が汎用ゲートウェイとして機能していない（既定 off / audited=ハッシュ許可制はエージェント生成コードと非互換 / 有効化手順も手動 toml 編集のみ）。
- **G1** オブジェクト新規作成コマンドの不在（primitive/empty/light/camera/text。製品コマンドとしては皆無——spike スクリプト内にのみ存在）。
- **G2** モード切替コマンドの不在。全編集系が `required_mode=OBJECT` で E_MODE_MISMATCH を返すのに、remediation の「切り替えてください」を実行する手段が bli に存在しない。
- **G3** シーングラフ操作の不在：rename / parent-unparent / collection（作成・移動・リンク）/ hide・show。
- **G4** モディファイアが 5 種（MIRROR/SUBSURF/SOLIDIFY/DECIMATE/BOOLEAN）にハードコード（Blender は約 50 種）。
- **G5** マテリアルが Principled Base Color のみ。metallic/roughness/emission/alpha、画像テクスチャ割り当て不可。UV 系も皆無。
- **G6** export に形式別オプションがない。FBX の `axis_forward/axis_up`・スケール、glTF の設定等が指定不可（Unity/ゲームエンジン連携の要所）。
- **G7** Blender 側ログ・エラーの取得手段がない（uLoop の get-logs 相当。capture=画像のみ）。exec-python の stdout/stderr は返るが、operator 警告やシステムコンソールは見えない。
- **G8** 新規 .blend（空シーン）開始・シーン管理コマンドがない（open は既存ファイルのみ）。

### ドキュメント面の過適合（コードと独立に効いている）

- spec.md §1 目的：「ユーザはモデリング初心者。原点・傾き・3Dプリンタ対応などの細かい調整が難しい」
- SKILL.md：「## 3つの中核シナリオ（このプロダクトの主目的）」が本文の中心。frontmatter description も原点変更・直立補正・3Dプリンタ対応が先頭。
- ROADMAP：「M8（3シナリオ）はこのプロダクトの中核価値」
- エージェントは SKILL.md を最初に読むため、**この構成自体が「3シナリオ用ツール」としての振る舞いを再生産する**。コマンドを増やしても SKILL.md を直さない限り過適合は解消しない。

---

## 3. 設計・実装レビュー（品質）

### 3a. 良い点（維持）

- **レイヤリングが明確**：server（transport・bpy 非依存）→ dispatcher（メイン直列）→ ops（コマンドハンドラ）→ gateway（bpy 唯一の接点）。bli-core は純 Python・依存ゼロで SSOT/protocol/errors を共有し、addon へは vendoring で配布。依存方向も概ね健全でユーザーのアーキテクチャ規約（関心事の分離・依存性逆転）に沿う。
- **スレッド設計が丁寧**：セッションロック＋lock-free request-status（重量 job 中も観測可能）、レンダ中の mutating 拒否（キューに積まない）、dispatcher の BaseException 捕捉（settle 保証・pump 生存）、exec_runner の SystemExit 握り込み。
- **エラーモデルがエージェント最適**：category/kind/retryable/remediation。CLI 終了コード 0–4 の規約。TIMEOUT(exit 2) → request-status 後追いの決着回収。
- **プロトコル堅牢性**：フレーム上限 16MiB、HELLO 以外即切断（DNS rebinding 対策）、protocol MAJOR 検査、hmac.compare_digest。
- **presence-sensitive パラメータの扱い**（default を schema に出すと生成クライアントが誤送信する問題への対処）など、実地フィードバック起点の細かい判断が随所に記録されている。
- テスト 446 件 + 実機スモーク（spikes）+ CI の 2 バージョンマトリクス + schema_hash 同期テスト。

### 3b. 問題点

**(1) モノリス化（ユーザー規約「巨大なモジュールを作らない」に抵触）**

| ファイル | 行数 | 混在ドメイン |
|---|---|---|
| `packages/bli-addon/src/bli_addon/gateway.py` | 1,927 | 対象解決/transform/material/modifier/mesh/straighten 数学/print/IO/capture/exec |
| `packages/bli-addon/src/bli_addon/ops.py` | 1,819 | 全 26 ハンドラ＋検証ヘルパ＋exec 制御＋dispatch |
| `packages/bli-cli/src/bli/main.py` | 1,631 | 全 33 Typer コマンド＋human フォーマッタ＋job 待機 |

**(2) コマンド追加コストが高い（拡張性 = 汎用化の隘路）**

1 コマンド追加に必要な変更箇所：
①`bli-core/definitions.py`（SSOT 定義）②`ops.py`（ハンドラ＋ `_BPY_HANDLERS` 登録の 2 箇所）③`gateway.py`（bpy 関数）
④`main.py`（Typer コマンド＝**SSOT のパラメータを typer.Option で再定義**＋human フォーマッタ手書き）
⑤`scripts/generate_cli_schema.py` の出力再生成（cli-schema.json / SKILL・schema_hash 同期テスト）
⑥`specs/blender-cli-core/contracts/methods.md`（契約カタログ）⑦テスト（gateway 単体・ops 検証・smoke/e2e）。
**計 7–8 箇所のタッチポイント**。
なお `bli-cli/models.py` の `model_for()` は同じ SSOT から Pydantic 検証モデルを**動的生成できている**——
つまり生成パターンは実証済みで、Typer 側に適用されていないだけ（§4 P2-2 の根拠）。

**(3) 正当性バグ（現物確認済み・小粒で独立に修正可能）**

- **B1: 終了コードの写像漏れ**（`main.py:106-117` `_exit_code_for`）。docstring（main.py:5-6）と spec §8 は「exit 3 = 接続不能・**認証失敗**」「exit 2 = 未決/retryable」と約束するが、実装は `TIMEOUT`/`BUSY_RENDERING`→2、`INVALID_PARAMS`/USER_INPUT→4 以外を**すべて exit 1** に落とす。その結果:
  - `AUTH_FAILED`（server.py:243,248 発行 → client.py:83-84 で RpcRemoteError 化）が exit 1（正: 3）。
  - `SESSION_BUSY` / `IN_PROGRESS`（サーバは retryable=True を付与）が exit 1（正: 2 相当の retryable 扱い）。
  - 根本原因: ErrorObject が持つ `retryable` フラグを見ず、kind 文字列のハードコードで判定しているため、エラー種別を足すたび手動同期が必要な構造。**`retryable` 参照ベースへ書き換えるのが本筋**。
- **B2: `resolve_targets` の regex フォールバックが常時発動**（`gateway.py:159-178`）。`regex=False`（全呼び出し箇所の実態）でも完全名一致に失敗すると**無条件に正規表現照合**へ落ちる。Blender の既定命名 `Cube.001` は `.` を含むため、typo した `--targets` が別オブジェクトへ静かに誤マッチし得る。delete / apply-transform / modifier apply など破壊系で実害リスク。仕様（docstring）どおりの動作ではあるが、明示 `--regex` フラグへの分離が安全。
- **B3: `object_summary` のマテリアル一覧が slot.link を無視**（`gateway.py:272-274`）。`obj.data.materials` を直読みしており、slot.link（OBJECT/DATA）を尊重するよう修正済みの `list_object_materials`（gateway.py:762-779・Codex P2-B）と結果が食い違い得る。`object-info` と `material --action list` の不整合。
- **B4: `save_blend` の例外捕捉が `open_blend` と非対称**（`gateway.py:1583-1599`）。open は「OSError 等も入力起因」と `except Exception` → E_OPERATOR 写像（gateway.py:1613-1619）だが、save は `run_operator` の RuntimeError 捕捉のみ。ディスク容量不足・権限エラー（OSError）が INTERNAL(code_bug) に化ける。

**(4) その他の実装知見**

- `handlers.py` は M2 時代の骨組み（ping/echo）が残存。実体は ops.dispatch — 死につつあるコードパスの整理余地。
- EXEC_DISABLED の remediation 文言が「mode を **trusted** にしてください」と最も強い権限を直接案内している（中間モードを飛ばしている。ops.py:1060-1064）。
- CLI 側ボイラープレート重複: `_parse_vec` の try/except+exit が 4 箇所コピー（straighten/transform/mesh/material）、`--steps` 範囲チェックが undo/redo で完全重複。
- Windows ではトークンファイルの chmod 600 が実効しない（コード内で認知済み・localhost 限定なので v1 許容だが明記）。
- `BLI_STATE_DIR` 環境変数で policy.toml ごと差し替え可能（handoff 済みの既知論点・同一 OS ユーザ権限に帰着するため v1 許容）。
- 良い設計として特記: `_MODIFIER_TYPE_PARAMS`/`_MESH_OP_PARAMS` の「許可 param の和集合から diff を導出して弾く」方式は presence-sensitive 検証の良いパターン（削るべき重複ではない）。

---

## 4. 修正方針（優先度付き・次セッションへの発注書）

> 各項目は独立に着手可能。P1 を上から順に消化するのが推奨。
> **共通の受け入れ基準**: 既存 pytest 全通過・schema_hash 同期テスト更新・SKILL.md/cli-schema.json 再生成・両版（5.0/4.4）smoke 通過。

### P1-0. 既知バグの修正（§3b(3) B1–B4・小粒・1 PR でまとめて可）

- **B1**: `_exit_code_for` を「ErrorObject.retryable == true → exit 2 / category == USER_INPUT → exit 4 / AUTH_FAILED・PROTOCOL_VERSION_MISMATCH → exit 3 / それ以外 → exit 1」の**フラグ参照ベース**に書き換える（kind ハードコードの手動同期を廃止）。docstring・spec §8 との整合テストを追加。
- **B2**: `resolve_targets` の暗黙 regex フォールバックを廃止し、`--regex` 明示時のみ正規表現照合にする（互換性に注意: SKILL.md の「完全名 > 正規表現」記述と spec も同時更新。移行措置として「完全一致なし・regex なら N 件」のヒントをエラー remediation に載せると親切）。
- **B3**: `object_summary` のマテリアル取得を `list_object_materials` と同じ slot.link 尊重ロジックに統一（共通ヘルパ化）。
- **B4**: `save_blend` を `open_blend` と同流儀の `except Exception` → E_OPERATOR 写像へ（ディスク満杯/権限 OSError の INTERNAL 化防止）。

### P1-1. exec-python の再位置づけ：`restricted` モード新設（G0）

**これが最重要。** uLoop の「動的実行が 90% をカバーする」構造を bli にも成立させる。

- **方針**: policy の mode に `restricted` を追加（`off | restricted | audited | trusted`）。
  - `restricted` = AST 静的検査によるブロックリスト方式で自走実行。**bpy/bmesh/mathutils 等の Blender API は全面許可**。
    ブロック対象（uLoop Restricted 相当）: `subprocess`/`os.system` 等のプロセス起動、`socket`/`urllib`/`http` 等のネットワーク、
    `os.remove`/`shutil.rmtree` 等の削除系、`ctypes`、`importlib` 動的ロード、`open(..., "w")` の書込は outputs_dir 配下のみ許可（要検討）等。
  - 既存資産 `ast_heuristics.py`（現在は注意喚起のみ）をブロッカーへ昇格させる形で流用できる。
  - 検出時は `EXEC_BLOCKED_RESTRICTED`（新エラーコード）で「何がブロックされたか＋trusted への昇格手順」を remediation に返す。
  - **静的検査は完全でない**（getattr 迂回等）ことを spec/SKILL に明記し、`security_guarantee:false` は維持。位置づけは「事故防止＋監査」であり悪意対策でないこと（現行 spec §459 と同じ整理）を再確認する。
- **サーバ側 policy.toml が真実源・CLI 昇格不可・fail-closed・全試行監査、の原則は不変。**
- 導入 UX: `bli init` またはドキュメントで「エージェントに使わせるなら restricted を推奨」と案内。policy.toml 編集を安全に行う
  `bli policy show` / `bli policy set exec.mode restricted --yes`（人間の明示確認前提・サーバではなく**ユーザローカルファイルの編集ヘルパ**）を追加してもよい（エージェントが勝手に昇格しない設計は対話確認で担保）。
- EXEC_DISABLED の remediation を「restricted（推奨）→ trusted」の順の案内に修正。
- **受け入れ基準**: restricted で `bpy.ops.mesh.primitive_cube_add()` を含むコードが自走実行でき、`import subprocess` を含むコードが EXEC_BLOCKED_RESTRICTED で拒否され、双方が audit/exec.jsonl に記録される。

### P1-2. 欠落プリミティブ第1弾：add / mode / rename / parent / collection（G1, G2, G3）

- **`add`**: オブジェクト生成。`--type` = cube|uv-sphere|ico-sphere|cylinder|cone|plane|torus|empty|light|camera|text、
  `--name --location --rotation --scale`、light は `--light-type`（POINT/SUN/SPOT/AREA）、既存の run_operator/能力検出の流儀に乗せる。mutates=True・Mode.OBJECT。
- **`mode`**: `--to object|edit|sculpt|vertex-paint|weight-paint`（対象 active）。E_MODE_MISMATCH の remediation を「`bli mode --to object` を実行」に更新（自動遷移しない現行方針は維持しつつ、脱出手段を提供）。
- **`rename`**: `--target --name`（object/data の両方 or object のみか要決定・衝突時は Blender 準拠の .001 を結果に返す）。
- **`parent`**: `--targets --to <name>`／`--clear`（keep-transform 既定 on）。
- **`collection`**: `--action create|move|link|unlink|list --name --targets`。
- **受け入れ基準**: U4（樽の作成）が「add cylinder → mesh 編集 → material」の構造化コマンド列だけで開始できる。U9（Edit モード放置）から `bli mode --to object` で復帰できる。

### P1-3. Unity/ゲームエンジン向け export 拡張（G6）

- `export --format fbx` に `--axis-forward -Z 等 / --axis-up Y 等 / --scale / --apply-unit-scale / --embed-textures` を追加（exporter の対応パラメータへ写像・能力検出で版差吸収）。
- glTF は Unity 側インポータ前提の既定（GLB・+Y up はフォーマット仕様で固定）を明記。`--targets` 選択との組み合わせテスト。
- SKILL.md に「Unity 取り込みレシピ」（FBX: axis-forward=-Z, axis-up=Y, scale 1.0 → Unity 側 scale factor 1 等）を追記。
- **受け入れ基準**: bli で出した FBX/GLB を Unity が追加補正なしで正しい向き・スケールで読み込める設定例が SKILL.md に載る。

### P2-1. ドキュメント再フレーミング（過適合の解消はコードだけでは終わらない）

- **SKILL.md**: 「3つの中核シナリオ（このプロダクトの主目的）」→ 「## 代表レシピ」に降格し、
  本文の中心を「汎用ワークフロー: 発見（list-commands/help）→ 観察（scene-info/object-info/capture）→ 編集（add/transform/mesh/material）→ 検証（capture/print-check）→ 入出力（import/export/save）」に再構成。
  frontmatter description も「Blender を CLI から汎用操作する（モデリング・シーン編集・入出力・3Dプリント準備など）」へ。
- **spec.md §1**: 目的から個人属性（モデリング初心者・3シナリオ）を「代表ユースケース例」へ移し、
  ゴールを「AI エージェントが Blender の一般的な操作を CLI で安全に自律実行できる基盤」と再定義。
- **README**: 冒頭に汎用ツールとしての位置づけ＋ Unity 等 DCC 連携ユースケースを例示。
- **受け入れ基準**: SKILL.md を読んだエージェントが「これは 3 シナリオ専用ツール」と解釈しない構成になっている（シナリオはレシピ例として残る）。

### P2-2. CLI コマンドの SSOT 自動生成（拡張コスト削減・G1–G6 の増築を安くする）

- definitions.py から Typer コマンドを動的生成する共通ファクトリを導入（型写像: STR/PATH/INT/FLOAT/BOOL/ENUM/VEC3/VEC4 → Typer Option。VEC は既存 `_parse_vec` を共通適用）。
- **実証済みの前例あり**: `bli-cli/models.py` の `model_for()` が同じ SSOT から Pydantic 検証モデルを動的生成している。同じパターンを Typer に広げるだけで、set-origin の 6 パラメータが definitions.py と main.py で二重記述されている類の重複（15〜20 コマンド分）が消える。
- human フォーマッタだけコマンド別に登録できるフック（`HUMAN_FORMATTERS: dict[str, Callable]`）を残し、未登録は JSON 整形にフォールバック。
- 目標: 新コマンド追加の必須変更を「definitions.py + ops.py + gateway.py（+任意で human フォーマッタ）」の **3 箇所＋再生成** に削減（現状 7–8 箇所 → §3b(2)）。
- **受け入れ基準**: 既存 33 コマンドの CLI 挙動が回帰しない（ヘルプ文言・終了コード・JSON 出力のスナップショット比較）。

- 付随タスク: `specs/blender-cli-core/contracts/methods.md`（手書きの契約カタログ＝手動同期点の一つ）も
  cli-schema.json と同様に SSOT から生成（または生成物との diff テスト）へ寄せ、ドリフト検出の対象にする。

### P2-3. modifier / material の汎用化（G4, G5）

- `modifier add --type` を enum 固定から「任意 type（能力検出で検証）＋ `--props '<JSON>'`（key=value の汎用プロパティ設定・型は bpy の rna から検証）」へ拡張。よく使う 5 種の専用フラグは互換のため残す。
- `material` に `--metallic --roughness --emission --alpha`、`--texture <path>`（Base Color への Image Texture ノード接続・パッキング選択）を追加。
- **受け入れ基準**: BEVEL や ARRAY など未対応だったモディファイアが `--type BEVEL --props '{"width":0.1}'` で追加でき、メタリック値とテクスチャ付きマテリアルが FBX/GLB export に反映される。

### P2-4. モジュール分割（ユーザー規約準拠・保守性）

3 ファイルとも既に `# ---- ラベル ----` のセクション見出しでドメイン境界が明示されており、境界に沿った機械的な抽出で低リスクに分割できる。

- `gateway.py` → `gateway/` パッケージ（`__init__.py` で re-export すれば ops 側の `gateway.foo()` 呼び出しは無改修）:
  `core.py`（run_operator/undo/fingerprint/exec_user_code）/ `objects.py`（resolve_targets/summary/bbox）/
  `transforms.py` / `materials.py` / `modifiers.py` / `mesh.py` / `straighten.py`（**約 380 行の自己完結ブロック**・最初の切り出し候補）/
  `print3d.py` / `io.py` / `capture.py`。
- `ops.py` → `ops/` を同じドメイン境界でミラー（共通ヘルパは `ops/_common.py`、`__init__.py` は `_BPY_HANDLERS` 集約と `dispatch()` のみ）。
- `main.py` → `cli/` パッケージ（共通の `_emit/_rpc/_await_job/_parse_vec` 等は `cli/_common.py`）。P2-2 の自動生成導入と同時に縮小。
- 挙動変更なしの純リファクタリング。
- **受け入れ基準**: pytest 全通過・公開シンボルの import 互換（`from bli_addon import ops` / `gateway` 経由の参照を維持）・1 ファイル 500 行程度以下。

### P3. 将来（v1.x〜v2 で検討）

- **`logs` コマンド**（G7）: operator の report/警告・直近エラーの取得。uLoop の get-logs 相当。実装はアドオン側でリングバッファに `bpy.app.handlers`/report を蓄積。
- **`new`（空シーン開始）/ シーン管理**（G8）。
- **edit-mode 編集**（頂点/辺/面選択スコープの bmesh 操作）・UV 操作: 需要が確認できてから。restricted exec-python が入れば当面はそちらでカバー可能。
- **batch 実行**（1 接続で複数コマンド・スクリプトファイル）: CLI 起動オーバーヘッド削減。
- audited の運用改善（許可ハッシュの追加を対話承認で行う `bli policy allow <sha>` 等）。
- `handlers.py`（M2 骨組み）の整理。
- Extensions 形式配布・PyPI・CI artifact（既存 handoff の後続タスクどおり）。

### 修正しないこと（明示）

- **セキュリティ原則**（サーバ側 policy.toml 真実源・CLI 昇格不可・fail-closed・監査ログ・127.0.0.1 固定・トークン認証）は**変更しない**。restricted 追加はこの枠内で行う。
- print-* / straighten / set-origin は削除・縮小しない（「ドメインパック」としてそのまま価値がある。問題は偏りであって存在ではない）。
- 通信プロトコル・job モデル・エラーモデルは現状維持（十分に堅牢）。

---

## 5. 次セッションへの依頼テンプレート

> `report/2026-07-11-design-review-generality.md` の §4 に従って修正を実施してください。
> まず P1-0（既知バグ 4 件）→ P1-1（exec-python restricted モード）の順に着手し、spec（specs/blender-cli-core/）との整合・
> schema_hash 同期・SKILL.md 再生成・pytest 全通過を各 PR の DoD としてください。
> ブランチは feature/***（docs は docs/***）で切り、PR は P1-0 / P1-1 / P1-2 / P1-3 / P2-* を分割してください。

**注意**: 未マージブランチ `feature/capture-view`（6116e95・capture に計算ビュー＝任意視点 offscreen 描画を追加）が存在する。
capture 関連（gateway.py 末尾・definitions.py の capture）に触れる際は先にこのブランチの扱い（マージ or リベース）をユーザーに確認すること。
