# bli (Blender CLI) — 機能仕様書 (v1 / core foundation)

| 項目 | 内容 |
|------|------|
| プロダクト | bli（Blender CLI）— AIエージェントがCLIでBlenderを自律操作するツール |
| CLIコマンド名 | `bli` |
| 対象バージョン | Blender 5.0 主軸 / 4.4 ベストエフォート |
| ステータス | Draft（レビュー待ち） |
| 作成日 | 2026-06-13 |
| 参照 | hatayama/unity-cli-loop（uLoop）の「CLIファースト」思想を踏襲 |

---

## 1. 概要（目的・背景）

### 背景
- ユーザはモデリング初心者。原点・傾き・3Dプリンタ対応などの細かい調整が難しい。
- これらをAIエージェントに依頼して代行させたい。
- 既存のMCP方式はツールスキーマを常時ロードするためトークン消費が大きく非効率。

### 目的
- AIエージェントが **CLI** 経由でBlenderを自律操作できる基盤を作る。
- ユーザはBlender GUIで結果を **目視** しながら、エージェントに自然言語で指示を出す。
- トークン効率を重視（オンデマンドの `--help` 発見、大きな出力はファイル退避）。

### コンセプト
- 常駐したBlender(GUI)にCLIクライアントが接続し、編集を即時反映する。
- 頻出操作は構造化サブコマンドで安全・予測可能に提供する。

---

## 2. スコープ（やる / やらない）

### v1でやること
- 常駐アドオン（TCPサーバ）＋ Python製CLIクライアント（`bli`）。
- 接続・診断: `init` / `doctor` / `ping`（hello handshake）。
- 情報取得: `scene-info` / `object-info` / `list-objects` / `capture`（画像・実地FB #1）。
- 汎用編集: `select` / `transform` / `apply-transform` / `duplicate` / `modifier` / `material` / `delete`。
- 状態操作: `undo` / `redo`（実地FB #3・GUI 必須）。
- ファイルI/O: `save` / `open` / `import` / `export`。
- シナリオ1（原点変更）: `set-origin`。
- シナリオ2（直立補正）: `straighten`。
- シナリオ3（3Dプリンタ対応）: `print-check` / `print-repair` / `print-setup` / `print-export`。
- 逃げ道: `exec-python`（**既定 off**）。
- ジョブ制御: `request-status` / `job-status` / `job-wait`。
- セキュリティモデル（トークン認証・127.0.0.1固定）。
- 構造化JSON出力＋終了コード＋出力descriptor化。
- バージョン能力検出（番号ハードコード分岐の禁止）。

### v1でやらないこと（Non-Goals）
- headless / リモート / コンテナ運用（将来。v1は同一ホスト・同一OSユーザ前提）。
- 並行実行・複数in-flight（bpyメインスレッド専有のため構造的に単一直列）。
- MCPブリッジ（将来、薄い単一クライアントとして直列投入する契約のみ予約）。
- **プロセス内サンドボックス**（悪意あるコードからの保護は提供しない。§6参照）。
- 再起動を跨ぐ永続冪等性（`RequestRegistry`はメモリ常駐・TTL揮発）。
- GUI自動テスト（Xvfb等は安定再現困難なため手動スモークに留める）。

---

## 3. アーキテクチャ（関心事の分離）

```
[AIエージェント]
     │ シェル実行（自然言語→コマンド）
     ▼
[CLIクライアント  bli]  ── Python 3.11+ / Typer（別プロセス）
     │ ① CLI層        : 引数解析・検証・終了コード・JSON整形
     │ ② プロトコル層  : framing / handshake / JSON-RPCサブセット
     ▼  TCP (127.0.0.1) 長さ接頭辞付きJSON
[Blenderアドオン（常駐サーバ）]
     │ ③ 受信層       : listen / 認証 / セッションロック（別スレッド）
     │ ④ ディスパッチ層: bpy.app.timers でメインスレッドにキュー投入
     │ ⑤ ドメインハンドラ層: bpy非依存のロジック（純粋・テスト容易）
     │ ⑥ BpyGateway   : bpy への唯一の接点
     │ ⑦ Adapter層    : 能力検出でバージョン差を吸収
     ▼
[Blender 本体 / bpy]
```

### 依存方向（依存性逆転）
- ⑤ドメインハンドラは bpy・通信・Typer に **依存しない**。
- bpy へのアクセスは ⑥BpyGateway に集約し、テスト時に差し替え可能にする。
- これにより将来の headless 系統ともドメインロジックを共有できる。

### 主要コンポーネント（責務）
| 層 | 責務 | bpy依存 |
|----|------|:------:|
| CLI層 | 入力検証・出力整形・終了コード | × |
| プロトコル層 | フレーミング・認証・RPC | × |
| 受信層（アドオン） | listen・トークン照合・セッション制御 | × |
| ディスパッチ層 | timerでメインスレッド直列実行・結果待ち合わせ | △ |
| ドメインハンドラ層 | 操作ロジック（origin算出等） | × |
| BpyGateway | bpyの読み書き・operator実行ラッパ | ◯ |
| Adapter層 | 能力検出・operator解決・バージョン分岐の局所化 | ◯ |

---

## 4. CLIインターフェース仕様（コマンド面）

### 共通テンプレート
```
bli <command> [--targets <name|regex>] [options] [--json] [--id <uuid>] [--dry-run]
```
- `--json` : 構造化出力（既定は人間可読。エージェントは常に`--json`推奨）。
- `--id` : リクエストID（UUIDv4）。**省略時はCLIが自動採番**。リトライ時は同一IDを再利用。
- `--targets` : 操作対象を明示。コマンド間で選択状態に依存しない（§7）。
- `--dry-run` : 変更を行わず影響を返す（対応コマンドのみ）。

### 接続・診断
| コマンド | 概要 |
|----------|------|
| `bli init` | 設定生成・セッショントークン発行・`connection.json` 書き込み |
| `bli doctor` | 環境診断（Blender検出・アドオン導入・ポート・バージョン・能力） |
| `bli ping` | hello handshake を行い protocol_version / Blender版 / 能力一覧を返す |

### 情報取得（読み取り専用）
| コマンド | 概要 |
|----------|------|
| `bli scene-info [--depth N]` | シーン階層・オブジェクト一覧・単位設定。大きい場合は退避 |
| `bli list-objects [--type MESH\|...] [--regex <pat>]` | 条件フィルタで一覧 |
| `bli object-info --targets <name>` | 寸法・頂点数・トランスフォーム・bbox・材質・モディファイア |
| `bli capture [--source viewport\|screen\|render] [--width N] [--height N] [--camera <name>]` | 現在の状態を画像取得（PNG をファイル出力しパスを返す・実地FB #1） |

> **`capture`（実地フィードバック #1）**: エージェントが現状を視覚確認する手段。`viewport`（gpu offscreen・UI なし・解像度指定可・既定）/ `screen`（ビューポート領域そのまま）/ `render`（カメラ）。読み取り専用（render 設定は save/restore で非破壊）。PNG は `outputs_dir`（git 非管理）へ。`viewport`/`screen` は GUI 必須。

### 汎用編集
| コマンド | 概要 |
|----------|------|
| `bli select --targets <name\|regex> [--type ...] [--active <name>]` | 選択操作 |
| `bli transform --targets <name> [--location x,y,z] [--rotation x,y,z(deg)] [--scale x,y,z] [--mode set\|delta]` | 位置/回転/拡縮 |
| `bli apply-transform --targets <name> [--location] [--rotation] [--scale]` | トランスフォーム適用 |
| `bli duplicate --targets <name> [--linked] [--count N] [--offset x,y,z]` | 複製（`bpy.data`直接） |
| `bli modifier --action add\|remove\|list\|apply --targets <name> [--type <T>] [--name] [型別params] [--make-single-user]` | モディファイア操作（add は --type 必須 / 型別: MIRROR=--axis, SUBSURF=--levels, SOLIDIFY=--thickness, DECIMATE=--ratio, BOOLEAN=--operation+--with / apply は破壊的・共有meshは--make-single-user） |
| `bli material --action assign\|create\|list [--targets <name>] [--name] [--color r,g,b,a] [--make-single-user]` | マテリアル操作（create=作成+割当 / list は slot/name/link/base_color / 共有mesh DATA slotは--make-single-user） |
| `bli delete --targets <name>` | 削除（既定でバックアップ／確認セマンティクス） |

#### 主要モディファイア（`modifier --type`）
- v1必須: `MIRROR` / `SUBSURF` / `SOLIDIFY` / `DECIMATE` / `BOOLEAN`。
- `add`（パラメータ付き）/ `remove` / `list` / `apply` をサポート。

### メッシュ編集（bmesh 一次 / 単一 `mesh` コマンド + `--op`）
- **bmesh-on-data** を基本とする（`from_mesh`→`bmesh.ops`→`to_mesh`・**OBJECT モードのまま** mesh データを編集＝`bpy.ops` の context 依存を回避。5.0.1/4.4.3 で確認済み）。当面 Mode=OBJECT（EDIT モード実機は L4）。
- material/modifier と同じく **単一 `mesh` コマンド + `--op` ENUM**（操作ごとに別コマンドにはしない）。op 別 params は schema 上は任意・サーバが op 別に検証する（条件付き必須・無効 param は弾く・op 専用 param は schema default を持たせない）。stability はコマンド単位なので（experimental op を含むため）`mesh` 全体を experimental とする。
- v1 の op（**T7.1–7.3 実装済み＝M7 完了**: recalc-normals / merge-by-distance / extrude / bevel / inset / boolean / decimate）:
```
bli mesh --op recalc-normals     --targets <name> [--inside]
bli mesh --op merge-by-distance  --targets <name> [--distance <f>]   # 既定 0.0001・0 以上
bli mesh --op extrude  --targets <name> --offset x,y,z               # 全 face を region 押し出し
bli mesh --op bevel    --targets <name> --width <f> [--segments N]   # 全 edge を bevel（既定 seg=1）
bli mesh --op inset    --targets <name> --thickness <f>              # 全 face を個別 inset
bli mesh --op boolean  --targets <name> --with <other> --operation UNION|DIFFERENCE|INTERSECT
bli mesh --op decimate --targets <name> --ratio <f>                  # 破壊的削減（編集確定）
```
- T7.2: `extrude --offset` は **world 空間**ベクトル（move/duplicate と一貫・matrix_world で world→local 変換）。`bevel --width` / `inset --thickness` はスカラ量のため **mesh ローカル単位**。extrude offset / bevel width / inset thickness は op 別に**必須**（bevel segments は任意・既定1・1〜100 で暴走防止）。選択は v1 では全 geometry（`--faces` 等の高度なセレクタは Deferred）。inset は閉じた mesh の全 face で `inset_region` が no-op のため `inset_individual` を使う。結果の `delta` は before→after の符号付き増減（decimate/boolean でも一貫）。
- T7.3（heavy）: `boolean` / `decimate` は **bmesh に相当 op が無い**ため（スパイク確認）、BOOLEAN/DECIMATE モディファイアを追加して `modifier_apply` で焼き込む（bmesh-on-data ではなく modifier 経由・生 bpy.ops は gateway のみ）。boolean は `--operation`（UNION/DIFFERENCE/INTERSECT）と `--with`（相手 mesh）が**必須**・相手の world 位置は Blender が解決・相手は read-only・自己参照/非 mesh は弾く。decimate は `--ratio`（0..1）が**必須**（COLLAPSE）。両者 heavy 候補だが同期実行（非同期 job は M10）。
- mesh データを直接書き換える破壊的操作のため、共有 mesh は `--make-single-user` 必須（apply 系と同じ）。非 mesh 型は `E_PRECONDITION`。

### 状態操作: undo / redo（実地フィードバック #3）
```
bli undo [--steps N]   # 直前の操作を元に戻す（既定 1・1〜100）
bli redo [--steps N]   # 元に戻した操作をやり直す（既定 1・1〜100）
```
- グローバル undo スタック（ユーザーの GUI 操作も含む）を `--steps` 段だけ戻す/進める。可逆性を「直前 transform の自力再構築」に頼らせない（試行錯誤の安全性向上）。
- 実機は bare `bpy.ops.ed.undo()`/`ed.redo()` を steps 回（GUI で context override 不要・研究 §E7・5.0.1/4.4.3 確認）。`applied` は実適用段数（スタック端で頭打ち）。
- **GUI 必須**で `--background` は `E_PRECONDITION` 縮退（本番は常駐 GUI Blender）。`steps` 範囲外は `INVALID_PARAMS`。上限は `runtime.MAX_UNDO_STEPS`（暴走防止）。

### シナリオ1: 原点変更
```
bli set-origin --targets <name> --to geometry|cursor|world
    [--center median|bounds]      # geometry時
    [--x <f> --y <f> --z <f>]     # world時（ワールド座標）
    [--make-single-user]          # 共有meshデータ時のみ明示で許可
```

### シナリオ2: 直立補正
```
bli straighten --targets <name> --method reset|world-align|pca|floor|angle|align-vector|reference
    [--up-axis +Z|-Z|+Y|...]      # 既定 +Z
    [--axis X|Y|Z]                # world-align/reference=合わせる local 軸（省略時は最近軸を自動）
                                  #   / angle=回転する world 軸（必須）
    [--up-hint auto|current]      # pca時の符号決定（current=現在 up 寄り・上下反転防止／実地FB #5）
    [--degrees D]                 # angle時（world 軸 axis まわりの回転量・符号で向き／実地FB #4）
    [--from-dir x,y,z]            # align-vector時（揃えたい現在の world 方向／実地FB #4）
    [--to-dir x,y,z]             # align-vector時（目標 world 方向・省略時は up）
    [--reference <name>]          # reference時（基準にする別オブジェクト／実地FB #4）
    [--ref-axis +Z|-Z|...]        # reference時（参照側の signed local 軸・省略時は up-axis）
    [--dry-run]                   # 適用せず計画（回転/tilt_from_up_deg）のみ返す（非破壊／実地FB #2）
    [--bake-rotation]             # 回転を mesh データに適用して焼き込む（--dry-run と排他）
    [--make-single-user]          # bake時の共有meshデータを明示で許可
```

### シナリオ3: 3Dプリンタ対応
```
bli print-check  --targets <name> [--manifold] [--normals] [--thin --min-thickness <mm>]
                   [--intersect] [--degenerate] [--save-to <path>]
bli print-repair --targets <name> [--make-manifold] [--recalc-normals] [--remove-degenerate]
bli print-setup  [--unit mm|m] [--scene]
bli print-export --targets <name> --format stl|3mf --path <file> [--ascii] [--scale <f>] [--no-apply-modifiers]
```

### ファイルI/O
| コマンド | 概要 |
|----------|------|
| `bli save [--path <file.blend>] [--backup]` | 保存（上書きは既定でバックアップ） |
| `bli open --path <file.blend> [--force]` | ファイルを開く（シーン全体を置換・未保存の bli 変更があれば `--force` 必須） |
| `bli import --format obj\|fbx\|gltf\|stl\|3mf --path <file>` | インポート |
| `bli export --format obj\|fbx\|gltf\|stl\|3mf --path <file> [--use-selection]` | エクスポート |

> v1必須フォーマット: **stl / obj / gltf(glb) / 3mf / fbx**。各形式の operator 差・改名は Adapter層（§9）で吸収する。

### 逃げ道・発見・ジョブ
| コマンド | 概要 |
|----------|------|
| `bli exec-python --code <str>\|--file <p>` | 構造化で表現不能な操作。**既定off**（§6） |
| `bli help --json` / `bli list-commands --json` | コマンドスキーマの機械可読出力（発見用） |
| `bli request-status --id <id>` | タイムアウト後の決着確認（§7） |
| `bli job-status --id <jid>` / `bli job-wait --id <jid> [--timeout]` | 非同期重量ジョブの状態取得 |

#### コマンド発見の導線（トークン効率の要）
- **一次**: リポジトリに **Claude Code Skill** を同梱（`.claude/skills/bli/`）。エージェントが自動発見し、概要・代表例・3シナリオの定石を最小トークンで取得。
- **二次**: `bli help --json` / `bli list-commands --json` で各コマンドの詳細スキーマをオンデマンド取得（ツール非依存）。
- スキルとhelp出力は同じSSOT（Pydanticモデル）から生成し、`schema_hash` でドリフトを検出（§11）。

---

## 5. 通信プロトコル仕様

### トランスポート / フレーミング
- `127.0.0.1` 固定の TCP。`0.0.0.0` は設定でも禁止（起動時ガードで拒否）。
- フレーム形式: `[4byte big-endian length (struct.pack(">I", n))][UTF-8 JSON n bytes]`。
- `recv_exactly` でストリーム再構成（部分読込・パケット連結に非依存）。
- `MAX_FRAME_BYTES`（既定16MiB）超は一括確保せず即エラー＋close。
- 受信 `recv` に `READ_TIMEOUT`（既定30s）。受信スレッドは try/finally で確実に close。

### ハンドシェイク（HELLO）
- 業務コマンドの前に HELLO 往復を必須化。
- クライアントは先頭フレームでトークンを提示（§6）。
- サーバは `protocol_version`・Blender版・能力一覧・`schema_hash` を返す。
- `protocol_version` はリリース版と独立の SemVer。MAJOR不一致は fail-fast、MINOR差は能力ネゴシエーション。
- 先頭バイトがHTTP/WebSocket様式なら即切断（DNS rebinding対策）。

### RPC（JSON-RPC 2.0 サブセット）
- 必須フィールド: `jsonrpc` / `method` / `params` / `id`。
- `id` はクライアントが UUIDv4 採番。
- 通知（notification）・バッチは v1 非対応（受信時 `-32600`）。
- 「JSON-RPC 2.0 準拠」ではなく「Request/Response サブセット」と明記。

### セッション制御
- `session_lock`（`threading.Lock`）で単一アクティブセッションを保証。
- 直列化粒度は「コマンド単位」ではなく「セッション（接続ライフタイム）単位」。
- 2本目接続は `acquire(blocking=False)` 失敗 → **`SESSION_BUSY` を返して即切断（fail-fast、既定）**。
- bind失敗（`EADDRINUSE`）は二重起動と判定。

---

## 6. セキュリティモデル

### 信頼境界の宣言
- 信頼境界は **OSプロセス / ファイルシステム境界**。
- Pythonインタプリタ内にサンドボックスは存在しない。
- ソケットに接続できるクライアントは **Blender権限で任意コード実行可能** とみなす。
- よって本プロダクトは「悪意あるエージェントへのプロセス内sandbox」を提供しない（Non-Goal）。

### トークン認証（必須）
- 起動時に `secrets.token_urlsafe(32)` でセッショントークンを生成。
- 所有者限定権限でファイル保存（`connection.json` と分離）。
- 全接続は HELLO で提示。`hmac.compare_digest` で定数時間比較。不一致は即切断。
- 最初の有効フレームが HELLO でない接続は切断。

### exec-python（3モード・既定 off）
| モード | 挙動 | 既定 |
|--------|------|:---:|
| `off` | exec-python を無効化。呼ぶと `EXEC_DISABLED` を返す | **✓** |
| `audited` | 全コードを `audit/` に記録。承認ゲート or 許可ハッシュで自走 | |
| `trusted` | 無制限実行。明示有効化（設定）が前提 | |
- モードは **ユーザ所有の設定ファイルでのみ昇格可能**。CLIフラグ単体では緩められない。
- AST検査は「安全保証」ではなく **ヒューリスティックなフラグ付け**。レスポンスに `security_guarantee: false` と `heuristic_flags` を含める。
- 3シナリオは構造化サブコマンドのみで完遂可能（exec不要）。

### 監査・ロギング
- メインスレッドの単一実行口を通る全Python文字列を `audit/` に記録（防止でなく検知・追跡）。
- `save` / `open` / `export` / `import` は対象パスを明示ログ。`.blend` 上書きは既定でバックアップ強制。`open` はシーン全体を置換するため、未保存の bli 変更があれば `--force` を要求（誤って未保存作業を破棄しない）。

### 設定・トークンの配置（ハイブリッド）
| 種別 | 配置 | git |
|------|------|:---:|
| セッショントークン | ユーザローカル（OS設定ディレクトリ / 所有者限定権限） | 非管理 |
| `connection.json`（ポート/PID/protocol_version） | ユーザローカル（Blenderインスタンス単位） | 非管理 |
| 権限ポリシー（exec mode等）・プロジェクト設定 | プロジェクトローカル `.bli/` | 管理可 |
- トークンを `.bli/` やリポジトリに置かない（誤コミット防止）。
- `.bli/` には `.gitignore` 雛形を同梱し、`outputs/` / `audit/` / 機微情報を除外。

---

## 7. 実行モデル（スレッド / タイムアウト / 状態）

### スレッド制御
- 受信はバックグラウンドスレッド、bpy実行は **メインスレッドのみ**。
- ディスパッチ: `bpy.app.timers` に登録したコールバックがキューをポーリングし、メインで直列実行。
- timer は `return None` で自己解除せず **固定間隔で再登録**。内部で全例外を握る。
- 結果待ち合わせ: `threading.Event`。受信スレッドは `Event.wait(timeout)` のみ（無限待ち禁止）。
- timerコールバックは try/except/finally で **必ず result_slot を埋めて `Event.set()`**。
- 受信ループは `select.select(timeout=0.5)` のポーリング型でシャットダウン可能に。

### シャットダウン手順
1. 停止フラグ設定 → 2. timer unregister → 3. in-flight 全 Event に `SERVER_SHUTTING_DOWN` を set → 4. listen socket close → 5. 接続socket `shutdown(SHUT_RDWR)`＋close → 6. `join(timeout)`。
- フックは `unregister` / `atexit` / `load_pre` に多重防御。
- サーバ状態はリロード対象外のシングルトンに保持し、`register` 冒頭で既存を必ず shutdown（二重listen防止）。
- listen socket は `SO_REUSEADDR`（Windowsでは `SO_EXCLUSIVEADDRUSE`/`SO_REUSEPORT` は使わない）。

### タイムアウト（キャンセルではなく後追い回収）
- 「実行開始済みのbpyオペレータはキャンセル不可」を明記。
- クライアントの timeout は **失敗確定ではない**。
- 決着確認は `request-status --id` RPC で行う。
- 終了コードに `2 = TIMEOUT_PENDING`（未決）を新設し、`1 = 確定失敗` と区別。
- read/write timeout と job watchdog timeout を二段構えで分離。

### 冪等性（C18: 最重要）
- 全リクエストに `id` を必須付与。**再試行は同一 `id` を再利用**する規約。
- サーバは `RequestRegistry`（`id → {state, result, ts}`, state ∈ PENDING/RUNNING/DONE/FAILED, TTL既定600s）を保持。
- 既知IDが DONE/FAILED → 再実行せず保存結果を返す。RUNNING → `IN_PROGRESS` を即返す。
- 結果ファイルは `outputs/<id>.json` でID単位命名し、書き込みを冪等化。

### 状態管理ポリシー
- コマンド間で 選択 / アクティブ / モード を保証しない。
- 対象は `--targets <name|session_uid>` で明示必須。session_uid を優先解決。
- 実行直前（timerデキュー後）に再解決し、`select_set` / active を再設定。
- `required_mode`（OBJECT/EDIT/ANY）を実行直前に検証。不一致は自動遷移せず `E_MODE_MISMATCH`。
- 1コマンド = 1 Undoステップ（`undo_push`）。
- レスポンスに状態フィンガープリントを付与。乖離時 `W_STATE_DRIFT`。

### 重量処理（ユーザ選択: ガードなし）
- 事前見積りによる **実行拒否は行わない**（ユーザ判断）。
- 代わりに観測性で守る:
  - 重量ジョブ（import/export/print-check/大規模処理）は受信時 `job_id` を採番し `status=accepted` を即返。`job-status` / `job-wait` で追跡。
  - レンダリング中は `render_init`/`render_complete` ハンドラで busy 管理し `BUSY_RENDERING` を即拒否（キューに積まない）。
  - heartbeat watchdog で `MAIN_THREAD_UNRESPONSIVE` を検知・**通知のみ**（実行は止めない／kill しない＝重量ネイティブ処理は中断不能）。pump タイマが生存印を更新し、別スレッド監視が「閾値(既定 60s=DISPATCH_TIMEOUT の2倍)を超えて未更新」で unresponsive を判定。**lock-free な観測系**（`request-status`/`job-status`/`doctor` 応答の `watchdog`/`main_thread_responsive`）に載せる＝メインが固まっていても受信スレッドが読んで返す（M10 T10.3・研究 §E13）。
- 注意: チャンク化不能なネイティブC処理（importer/exporter/print3d内部）は実行中GUIが固まり得る（残存リスク）。

### bpy.ops フォールバック方針
- 一次手段: `ensure_context`（`temp_override` 合成）→ 標準オペレータ。
- すべての `bpy.ops` は `run_operator()` ラッパ経由必須（生呼び出しはCIのASTチェックで禁止）。
  - 成功判定: 戻り値が `{'FINISHED'}` のみ成功。`{'CANCELLED'}` は明示失敗化。
  - 実行前に `poll()` を先行評価し、原因（`no_active_object` 等）と症状を分離。
- 手計算フォールバックは「単一ユーザ・モディファイアなし・親なし」に限定。それ以外は破壊より構造化エラー優先。
- 複製・モディファイア等は可能な限り `bpy.data` 直接操作（`copy()` + `data.copy()` + link）。
- origin系は `obj.data.users >= 2` をプリチェックしブロック。`--make-single-user` 明示時のみ実行。
- 単位は `global_scale` を唯一の真実とし、`scale_length` は検証専用（1000倍ずれ防止）。

---

## 8. データ要件・出力 / エラーハンドリング

### 出力 descriptor 化
- 大きな出力は生パスでなく descriptor を返す:
  ```json
  {"output_ref": {"id":"<id>", "transport":"inline|shared-fs", "path":"...",
                   "size":123, "sha256":"...", "encoding":"utf-8", "schema":"scene-info/v1"}}
  ```
- `INLINE_THRESHOLD`（既定64KiB）未満はインライン。
- 書き込みは temp → `os.replace()` でアトミック。CLIは sha256 検証。不一致は `STALE_OUTPUT`。
- 退避先 `outputs/` のGC: TTL 24h / 200件 / 200MiB の古い順削除。配下パス検証必須。

### 構造化エラースキーマ
```json
{"error": {
  "category": "PRECONDITION|USER_INPUT|ENVIRONMENT|INTERNAL",
  "kind": "E_MODE_MISMATCH",
  "cause": "no_active_object",
  "userVisibleSymptom": "対象が選択されていません",
  "codeBug": false,
  "retryable": true,
  "remediation": "--targets で対象を明示してください",
  "tracebackRef": "outputs/<id>.trace.txt"
}}
```
- raw traceback は INTERNAL のみファイル退避し、参照のみ返す。
- 状態変更系コマンドは事後検証 `verified: bool` を同梱。

### エラーコード表（抜粋）
`BUSY_RENDERING` / `MAIN_THREAD_UNRESPONSIVE` / `REQUEST_CANCELLED` / `TIMEOUT` /
`SERVER_SHUTTING_DOWN` / `NO_RESPONSE` / `CONNECTION_RESET` / `SESSION_BUSY` /
`IN_PROGRESS` / `E_MODE_MISMATCH` / `E_TARGET_NOT_FOUND` / `W_STATE_DRIFT` /
`CAPABILITY_UNAVAILABLE` / `STALE_OUTPUT` / `PROTOCOL_VERSION_MISMATCH` /
`SCHEMA_MISMATCH` / `EXEC_DISABLED` / `INVALID_PARAMS(-32602)`

### 終了コード表
| コード | 意味 |
|:---:|------|
| 0 | 成功 |
| 1 | 確定失敗（業務エラー） |
| 2 | TIMEOUT_PENDING（未決。`request-status`で要確認）/ `BUSY_RENDERING`（レンダ中で未受理・retryable・レンダ後に再試行）（M10 T10.2） |
| 3 | 接続不能 / アドオン未起動 / 認証失敗 |
| 4 | 入力エラー（CLI引数・スキーマ不一致） |

---

## 9. バージョン戦略

- **5.0 主軸 / 4.4 ベストエフォート**。CIの契約テストが通る版のみ公式サポート。
- バージョン番号のハードコード分岐を **禁止**。能力検出（capability-based dispatch）を採用。
- `CapabilityRegistry` を起動時に `bl_rna` / `addon_utils` から実測構築。
- `OperatorResolver`（候補リスト＋引数マップ）で Adapter層に分岐を局所化。
- 例: STLエクスポータ `export_mesh.stl`（旧）⇄ `wm.stl_export`（新）、`object_print3d_utils` のExtensions移管に追従。
- 能力不在は `CAPABILITY_UNAVAILABLE`（missing / hint付き）。
- 未検証マイナーは preflight で警告（遮断はしない）。

---

## 10. 3シナリオの受け入れ基準

### S1: 原点変更
- `--to geometry --center bounds`: バウンディングボックス中心に原点が移動する。
- `--to cursor`: 3Dカーソル位置に原点が移動する。
- `--to world --x 0 --y 0 --z 0`: 指定ワールド座標に原点が移動し、メッシュの見た目位置は不変。
- 共有meshは `--make-single-user` 無しでは `E_PRECONDITION` を返す（破壊しない）。
- 完了条件: `object.matrix_world.translation` が期待値に一致（許容誤差付きゴールデン検証）。

### S2: 直立補正
- `--method world-align --up-axis +Z`: 最も近い主軸を +Z に合わせ、傾きが除去される。
- `--method floor`: 最下点が Z=0 平面に接地する。
- `--method reset`: 回転がクリアされる。
- `--bake-rotation` 指定時は回転が適用（焼き込み）される。
- 完了条件: 補正後のローカル+Z軸とワールド+Zの角度が閾値内（ゴールデン検証）。
- **v1 実装注記（実地フィードバック #5/#2/#6）**: `--method pca` の主成分は符号不定。`--up-hint current` は**現在の up に近い側**を + に選び、土台が重いスキャン物体でも**上下反転しない**（既定 `auto` は重心方向で符号決定）。pca 結果は `tilt_from_up_deg`（up からの傾き角・符号非依存の鋭角）を返す。`--dry-run` は**適用→読取→厳密復元**で副作用なく計画（回転・`tilt_from_up_deg`）を返す（非破壊計測にも使える・`--bake-rotation` とは排他）。完了条件（追加）: 重心が下に偏る tilt 物体で `pca --up-hint current` が反転せず傾きを除去（principal_world.z>0）、`--dry-run` 前後で transform 不変かつ計画＝実適用（ゴールデン検証・両版同値）。
- **基準指定 method（実地フィードバック #4・支柱問題）**: 実地検証では傾きが mesh 形状に焼き込まれ、基準にしたい「支柱」が同一メッシュの一部だったため、エージェントは補正回転を手計算し `transform --mode delta` で迂回適用した。これを解消するため、エージェントが算出した補正を straighten 経由（dry-run/bake/共有ガードの作法込み）で安全に適用する3 method を追加する。`--method angle`（world 軸 `--axis` まわりに `--degrees` 回転）/ `--method align-vector`（`--from-dir` を `--to-dir`〔省略時 up〕へ最小回転で合わせる＝向きを数値で与えれば同一メッシュ内の基準でも整列可）/ `--method reference`（参照オブジェクトの軸方向へ合わせる）。完了条件（追加）: angle の Z 45° 回転で `rotation_euler_deg≈[0,0,45]`、align-vector で `from_world_after≈up`・`angle_deg≈傾き`、reference で対象の整列軸 world 方向が参照軸方向に一致（world up とは異なる・ゴールデン検証・両版同値）。**v1 未保証**: align-vector の最小回転は up 周りの yaw を保存しない（向き不定・pca/world-align と同様）。部分ジオメトリ PCA（頂点サブセット基準）は別 PR へ繰越（部分指定方法の決定が必要）。

### S3: 3Dプリンタ対応
- `print-check`: 非多様体・反転法線・薄壁・自己交差・退化面の件数を構造化で返す。
  - **v1 実装注記（T8.4）**: 非多様体・反転法線・退化面は **bmesh 自前計算で常時提供**（print3d 非依存）。薄壁（`--thin`）・自己交差（`--intersect`）は **print3d Toolbox 依存**で、未導入環境（M0.5/§E6 で 5.0.1・4.4.3 とも実体なし）では要求時に **`CAPABILITY_UNAVAILABLE`** を返す（黙って件数 0 にせず能力欠如を明示）。print3d が Extensions で導入されれば自動的に有効化される設計。
- `print-repair`: bmesh 自前で可能な範囲を修復し（make-manifold=穴埋め/重複マージ/loose 除去・recalc-normals・remove-degenerate）、修復前後の差分を報告。**完全修復は保証しない**旨を明記（穴形状により埋めきれない）。
- `print-setup --unit mm`: 単位がmmに設定される。
- `print-export --format stl`: STL が出力され、ファイルが生成される（パス/サイズ/sha256/三角形数を返す）。
  - **v1 実装注記（T8.5）**: 対象1個の mesh を `wm.stl_export`（両版同一・研究 §E8）で world 焼き出力。スケールは `global_scale` 一本化（`--scale` 既定 1.0・`use_scene_unit=False` 固定で `scale_length` を出力へ反映させない＝1000倍ずれ防止・`scale_length` は結果に報告して検証可能に）。`--ascii` で ASCII STL。`--apply-modifiers`（既定 on）。**`--format 3mf` は両版とも実体なし（§E8）→ `CAPABILITY_UNAVAILABLE` + STL フォールバック hint**（黙って STL に差し替えない）。読み取り専用（選択は save/restore で非破壊）。
- 完了条件: `print-check` の致命カテゴリ（非多様体）件数が repair 後に減少し、export ファイルが存在。

---

## 11. テスト・品質方針（概要）

- bpy呼び出しを `BpyGateway` に集約し、ドメインロジックを bpy 非依存に。
- 4層テスト:
  - **L1 純ユニット**: 全PRゲート・カバレッジ計測（bpyモック）。
  - **L2 bpy統合**: `blender --background` で 5.0 ＋ 4.4 マトリクス（nightly）。
  - **L3 プロトコルE2E**: framing / handshake / 冪等性 / タイムアウト。
  - **L4 GUIスモーク**: 手動。
- `bl_rna` 契約テスト＋シナリオのゴールデン数値検証で「無言の誤結果」を捕捉。
- コマンド定義は Pydantic v2 + `@command` デコレータで **SSOT化**。`help --json` は `model_json_schema()` から自動生成。
- `schema_hash`（SHA256）を hello / help に載せ、不一致は `SCHEMA_MISMATCH`。スナップショットテストでCI fail。
- リリースゲートで 5.0 ジョブ成功を必須化。

---

## 12. 残存リスク（受容済み）

- **タイムアウト後の実行継続**: bpyは中断不可。「失敗扱いだが実体は完走」が起こり得る → 同一ID冪等化＋`request-status`で構造的に二重実行を防止。
- **ユーザ手動操作の割り込み**: 常駐GUIゆえ選択/モード変更は排除不能 → 明示ターゲット＋実行直前再解決＋フィンガープリントで検知。
- **重量処理のGUIフリーズ**: ガードなし選択のため、巨大モデルで一時フリーズし得る → watchdog通知と非同期jobで観測性を確保。
- **同一OSユーザの悪性コード**: トークン読取可能なら接続可 → OS権限境界に帰着。exec既定off・trustedは明示時のみ。
- **バージョン振る舞い差**: `bl_rna`に現れない内部アルゴリズム変更は契約テストで捕捉不可 → ゴールデン数値検証。

---

## 13. 未確定事項

1. ~~**プロダクト名 / CLIコマンド名**~~ → **確定: `bli`（Blender CLI）**（D9）。
2. ~~**アドオン配布形態**~~ → **確定: 手動zip一次（`doctor`で導入支援）、Extensionsは後続**（D10）。
3. **CLI配布**: `pipx install` を一次とする（確定）。
4. ~~**import/export対応フォーマット**~~ → **確定: stl / obj / gltf(glb) / 3mf / fbx すべてv1必須**（D11）。
5. ~~**設定ファイル/トークンの置き場所**~~ → **確定: ハイブリッド（token/connection.json=ユーザローカル・git非管理, policy=プロジェクトローカル `.bli/`）**（D14）。

> 実装時に確定可（Deferred）: `connection.json` の厳密スキーマ、modifier/mesh各コマンドの個別パラメータ詳細、メッシュ選択指定子の表現力、Extensions移行時期。

---

## 付録A: 確定済み判断ログ

| # | 判断 | 内容 |
|---|------|------|
| D1 | 接続方式 | 常駐Blender(GUI) ＋ アドオンTCPサーバ ← CLIクライアント |
| D2 | 対象バージョン | 5.0主軸 ＋ 4.4ベストエフォート |
| D3 | コマンド方式 | ハイブリッド（構造化主軸 ＋ exec逃げ道） |
| D4 | v1スコープ | 基盤を厚く（汎用操作を幅広く＋3シナリオ） |
| D5 | exec-python既定 | **off**（audited/trustedは設定で昇格） |
| D6 | 同時接続 | fail-fast（`SESSION_BUSY`で即拒否） |
| D7 | 重量ガード | なし（拒否せず watchdog通知＋非同期job） |
| D8 | バージョンサポート定義 | CI契約テストが通る版のみ公式、5.0主軸 |
| D9 | プロダクト/コマンド名 | `bli`（Blender CLI） |
| D10 | アドオン配布 | 手動zip一次（doctor支援）、Extensionsは後続 |
| D11 | 対応フォーマット | stl / obj / gltf(glb) / 3mf / fbx（全てv1必須） |
| D12 | コマンド発見 | Claude Code Skill同梱 ＋ `help --json` |
| D13 | 編集カバレッジ | オブジェクト操作 ＋ 主要モディファイア ＋ メッシュ編集 |
| D14 | 設定配置 | ハイブリッド（token=ユーザローカル, policy=`.bli/`） |

---

## 付録B: 明確化された仕様 (Clarifications)

### Session 2026-06-13
- Q: プロダクト/CLIコマンド名 → A: **`bli`（Blender CLI）**。「bLoop」はuLoopと酷似のため不採用。
- Q: アドオン配布の一次導線 → A: **手動zipを一次**（`doctor`で導入支援）。Extensions対応は後続。
- Q: import/export 必須フォーマット → A: **stl / obj / gltf(glb) / 3mf / fbx**（fbxもv1必須）。
- Q: エージェントのコマンド発見導線 → A: **Claude Code Skill として同梱**（`.claude/skills/bli/`）。詳細は `help --json` でオンデマンド取得。
- Q: v1汎用編集のカバレッジ → A: **オブジェクト操作 ＋ 主要モディファイア ＋ メッシュ編集（編集モード操作）**。
- Q: 設定/トークン/connection.json の配置 → A: **ハイブリッド**（token/connection.json=ユーザローカル・git非管理, policy/設定=プロジェクトローカル `.bli/`）。
