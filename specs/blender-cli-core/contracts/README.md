# bli — APIコントラクト (contracts/)

bli の「API」は REST/GraphQL ではなく **localhost TCP 上の JSON-RPC 2.0 サブセット**。
本ディレクトリはその契約を定義する。

## ファイル
- `protocol.schema.json` — トランスポート/ハンドシェイク/RPCエンベロープの JSON Schema。
- `methods.md` — RPC メソッド（= CLIサブコマンド）カタログ。params/result/errors。

## 契約の原則
- メソッド定義の唯一の真実は `bli-core` の `dataclass`（data-model.md §1）。本書はその人間可読ビュー。
- `schema_hash`（全メソッド定義の SHA256）を hello/help に載せ、ドリフトを CI で検出。
- すべてのメソッドは `id`（UUIDv4）必須。再試行は同一id（冪等性）。
- 破壊系（`mutates=true`）は `verified` と `fingerprint` を result に含む。
- 大きな出力は `output_ref`（data-model.md §5）で返す。

## バージョニング
- `protocol_version` は SemVer。MAJOR不一致は fail-fast、MINOR差は能力ネゴシエーション。
- メソッド追加は MINOR、互換破壊は MAJOR。
