# bli — データモデル設計 (data-model.md)

| 項目 | 内容 |
|------|------|
| 対象 | spec.md (blender-cli-core v1) / research.md を踏まえたエンティティ定義 |
| 位置づけ | SSOT は `bli-core` の純 Python `dataclass`。本書はその論理モデルを記述 |
| 作成日 | 2026-06-13 |

> 本プロダクトは DB を持たない。「エンティティ」= プロトコルメッセージ・コマンド定義・サーバ内状態・設定ファイルのスキーマを指す。

---

## 1. Command（コマンド定義 / SSOT）

`bli-core` の `dataclass`。CLI・アドオン・Skill・`help --json` の唯一の真実。

| フィールド | 型 | 説明 |
|-----------|----|----|
| `name` | str | RPC method 名（例: `set-origin`）。CLIサブコマンドと一致 |
| `summary` | str | 1行説明（help / Skill用） |
| `params` | list[Param] | パラメータ定義 |
| `mutates` | bool | 状態変更系か（True なら undo_push・backup・verified 対象） |
| `required_mode` | enum(OBJECT/EDIT/ANY) | 実行前提モード |
| `capability_deps` | list[str] | 必要 operator/addon（例: `print3d_toolbox`） |
| `is_heavy` | bool | 非同期 job 化対象か（import/export/print-check等） |
| `stability` | enum(stable/experimental) | 3シナリオ経路は stable 必達 |
| `result_schema` | dict | 戻り値スキーマ（JSON Schema） |

### Param
| フィールド | 型 | 説明 |
|-----------|----|----|
| `name` | str | 例: `to`, `targets` |
| `type` | enum(str/int/float/bool/enum/vec3/path) | 値型 |
| `required` | bool | 必須か |
| `default` | Any | 既定値 |
| `choices` | list \| None | enum の選択肢（例: geometry/cursor/world） |
| `help` | str | 説明 |

- 生成物: `to_json_schema()`（純Python）/ CLI 側は Pydantic で同等スキーマ。両者の `schema_hash`（SHA256）一致を CI で検証。

---

## 2. プロトコルメッセージ

### 2.1 Frame（トランスポート層）
```
[4byte big-endian uint32 length][UTF-8 JSON body (length bytes)]
```
- `MAX_FRAME_BYTES = 16 MiB`。超過は `PROTOCOL_FRAME_TOO_LARGE` で close。

### 2.2 HelloRequest（接続後の最初の有効フレーム・必須）
```json
{"type": "hello", "token": "<session-token>", "protocol_version": "1.0.0",
 "client": "bli-cli/0.1.0"}
```

### 2.3 HelloResponse
```json
{"type": "hello-ok", "protocol_version": "1.0.0", "blender_version": "5.0.0",
 "schema_hash": "<sha256>", "capabilities": ["wm.stl_export", "print3d_toolbox", ...],
 "session_uid": "<uuid>"}
```
- 不一致時: `PROTOCOL_VERSION_MISMATCH`（MAJOR差）/ 認証失敗は即切断。

### 2.4 RpcRequest（JSON-RPC 2.0 サブセット）
```json
{"jsonrpc": "2.0", "method": "set-origin", "id": "<uuidv4>",
 "params": {"targets": ["Cube"], "to": "geometry", "center": "bounds"}}
```
- 通知（id無し）・バッチは非対応 → `-32600`。

### 2.5 RpcResponse（成功）
```json
{"jsonrpc": "2.0", "id": "<uuidv4>",
 "result": {"success": true, "operation": "set-origin", "verified": true,
            "fingerprint": "<state-hash>", "output_ref": null, "data": {...}}}
```

### 2.6 RpcError（失敗）
```json
{"jsonrpc": "2.0", "id": "<uuidv4>",
 "error": {"code": -32000, "message": "E_PRECONDITION", "data": {<ErrorObject>}}}
```

---

## 3. ErrorObject（構造化エラー）

| フィールド | 型 | 説明 |
|-----------|----|----|
| `category` | enum | PRECONDITION / USER_INPUT / ENVIRONMENT / INTERNAL |
| `kind` | str | エラーコード（§spec §8 表）例: `E_MODE_MISMATCH` |
| `cause` | str | 機械可読原因（例: `no_active_object`） |
| `userVisibleSymptom` | str | 人間向け症状 |
| `codeBug` | bool | bli側バグか |
| `retryable` | bool | 同一idで再試行可能か |
| `remediation` | str | エージェント向け是正ヒント |
| `tracebackRef` | str \| None | INTERNAL時のみ。退避ファイル参照 |

---

## 4. RequestRegistry エントリ（冪等性 / サーバ内状態・揮発）

| フィールド | 型 | 説明 |
|-----------|----|----|
| `id` | str (uuidv4) | リクエストID。再試行は同一id再利用 |
| `state` | enum | PENDING / RUNNING / DONE / FAILED |
| `result` | RpcResponse \| None | 完了結果 |
| `event` | threading.Event | 受信スレッドの待ち合わせ用 |
| `ts` | float | 登録時刻（TTL判定。既定600s） |

- 状態遷移: PENDING →(dispatcher取得)→ RUNNING →(完了)→ DONE / FAILED。
- 既知id: DONE/FAILED は再実行せず保存結果返却。RUNNING は `IN_PROGRESS` 即返。
- 再起動で揮発（v1 Non-Goal: 永続化）。

---

## 5. OutputRef（巨大出力 descriptor）

| フィールド | 型 | 説明 |
|-----------|----|----|
| `id` | str | リクエストid |
| `transport` | enum | inline / shared-fs（将来 fetch-rpc） |
| `path` | str \| None | 退避ファイルパス（shared-fs時） |
| `size` | int | バイト数 |
| `sha256` | str | 整合性検証用 |
| `encoding` | str | utf-8 等 |
| `schema` | str | 例: `scene-info/v1` |

- `INLINE_THRESHOLD = 64 KiB` 未満はインライン。書込は temp→`os.replace()`。
- CLI は sha256 検証。不一致は `STALE_OUTPUT`。

---

## 6. ConnectionInfo（`connection.json` / ユーザローカル・git非管理）

| フィールド | 型 | 説明 |
|-----------|----|----|
| `host` | str | `127.0.0.1` 固定 |
| `port` | int | 既定 9876 |
| `pid` | int | Blenderプロセスのpid（stale判定用） |
| `protocol_version` | str | SemVer |
| `blender_version` | str | 例: `5.0.0` |
| `schema_hash` | str | SSOT同期検証 |
| `started_at` | str (ISO8601) | 起動時刻 |

- token は **別ファイル**（所有者限定権限）に分離。`connection.json` に token を書かない。

---

## 7. Config / Policy

### 7a. exec 権限ポリシー（ユーザローカル `BLI_STATE_DIR/policy.toml`・**真実源**）

サーバはこのファイルだけを読んで exec の可否を決める（R-A・spec §6）。読み書きのスキーマは
`bli_core.policy` に集約（読取 fail-closed / `bli policy --action set` の自動編集はこの2キー
以外があると拒否）。

| キー | 型 | 既定 | 説明 |
|------|----|----|----|
| `exec.mode` | enum | `off` | off / restricted / audited / trusted（spec §6・P1-1 で restricted 追加） |
| `exec.allow_hashes` | list[str] | `[]` | audited 用の許可コード sha256（小文字16進へ正規化して照合） |

### 7b. プロジェクト設定（プロジェクトローカル `.bli/config.toml`・git 管理可）

| キー | 型 | 既定 | 説明 |
|------|----|----|----|
| `exec.mode` | enum | `off` | **表示用ヒント**（サーバは読まない＝commit しても昇格しない。真実源は §7a） |
| `server.port` | int | 9876 | リッスンポート |
| `server.bind` | str | `127.0.0.1` | 変更不可（ガード） |
| `server.read_timeout` | float | 30.0 | recvタイムアウト秒 |
| `request.ttl` | float | 600 | RequestRegistry TTL |
| `outputs.inline_threshold` | int | 65536 | インライン上限 |
| `outputs.gc` | obj | 24h/200件/200MiB | 退避GC |
| `backup.on_overwrite` | bool | true | .blend上書き時バックアップ |

---

## 8. Capability / OperatorResolver（能力検出）

### CapabilityEntry
| フィールド | 型 | 説明 |
|-----------|----|----|
| `key` | str | 論理名（例: `export.stl`） |
| `candidates` | list[str] | 優先順 operator パス（例: `["wm.stl_export","export_mesh.stl"]`） |
| `resolved` | str \| None | 実機で解決された operator |
| `arg_map` | dict | 論理引数 → operator 引数 名のマップ |
| `addon` | str \| None | 必要 addon module（例: `print3d_toolbox`） |
| `available` | bool | 解決可否 |

- 起動時に `bl_rna` / `dir(bpy.ops.*)` / `addon_utils` で実測構築。
- 不在時は `CAPABILITY_UNAVAILABLE`（missing / hint）。

---

## 9. Target 解決（状態管理）

- 入力: `--targets <name>` + 任意 `--regex`（session_uid は当初案から未実装のまま廃止・設計レビュー
  2026-07-11 B2）。
- 解決順: `--regex` 省略時は**完全名一致のみ**（`bpy.data.objects.get(name)`）。`--regex` 明示時のみ
  正規表現照合（`re.search`）。かつては完全一致 0 件時に regex へ暗黙フォールバックしていたが、
  Blender の既定命名 `Cube.001` が `.`（regex の任意一文字）を含むため typo が別オブジェクトへ
  静かに誤マッチし得た。明示 opt-in に分離した。
- 完全一致 0 件・`--regex` が正規表現として解釈すると当たる場合は、`E_TARGET_NOT_FOUND` の
  症状文に一致件数と `--regex` 使用のヒントを添える。不正な正規表現（`--regex` 指定時のみ評価）は
  `E_PRECONDITION`（category=USER_INPUT）。
- 実行直前（dispatcher デキュー後）に再解決し `select_set` / active を再設定。
- 不在: `E_TARGET_NOT_FOUND`。複数該当: コマンドにより許可/拒否。
- レスポンスに状態フィンガープリント（選択/active/mode のハッシュ）を付与。乖離時 `W_STATE_DRIFT`。

---

## 10. エンティティ関連図（論理）

```
Command(SSOT) ──generates──> CLI(Pydantic) / help --json / Skill / addon検証
RpcRequest ──registry──> RequestRegistry(Entry) ──dispatcher──> Handler ──BpyGateway──> bpy
Handler ──uses──> OperatorResolver(Capability) / Target解決
Handler ──returns──> RpcResponse(result | OutputRef) / RpcError(ErrorObject)
ConnectionInfo/Token ──ユーザローカル / Config ──プロジェクトローカル(.bli/)
```
