# framing + JSON-RPC codec + HELLO

| 項目 | 内容 |
|------|------|
| マイルストーン | M1 |
| 依存 | 11 |
| テスト層 | L1 |
| 状態 | 未着手 |

## 目的・成果物
- bli_core/protocol.py(recv_exactly, encode/decode_frame 4byte BE+JSON, MAX_FRAME)
- Rpc/Hello メッセージ型
- contracts/protocol.schema.json 整合

## 完了定義 (DoD)
- frame往復が部分読込耐性
- MAX_FRAME超で例外
- JSON-RPCサブセット検証

## 実行サブタスク
- [ ] encode/decode_frame + recv_exactly(bytes I/O 抽象)
- [ ] Rpc/Hello 型
- [ ] JSON-RPC検証(-32600/-32601/-32602)
- [ ] ユニット(往復/部分/超過/不正)

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
