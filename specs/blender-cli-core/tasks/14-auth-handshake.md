# HELLO + token認証

| 項目 | 内容 |
|------|------|
| マイルストーン | M2 |
| 依存 | 13 |
| テスト層 | L3 |
| 状態 | 未着手 |

## 目的・成果物
- HELLO往復(最初の有効フレーム必須)
- hmac.compare_digest 照合・不一致即切断
- HTTP/WS様式 即切断
- protocol_version MAJOR fail-fast

## 完了定義 (DoD)
- 不正token/非HELLO/HTTP拒否
- hello-ok 応答(schema_hash/capabilities/session_uid)

## 実行サブタスク
- [ ] hello + token照合
- [ ] HTTP様式ガード
- [ ] protocol_version 検証
- [ ] E2E(成功/失敗)

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
