# listen/recvスレッド/select

| 項目 | 内容 |
|------|------|
| マイルストーン | M2 |
| 依存 | 12 |
| テスト層 | L3 |
| 状態 | 未着手 |

## 目的・成果物
- bli_addon/server.py(127.0.0.1 listen, SO_REUSEADDR, select(0.5))
- 受信スレッド + try/finally close
- connection.json 原子的書込 + token別ファイル

## 完了定義 (DoD)
- bind成功/EADDRINUSE明示
- select ループ停止可能
- connection.json/token 生成

## 実行サブタスク
- [ ] server skeleton
- [ ] 受信スレッド + recv_exactly 連携
- [ ] connection.json + token(secrets)
- [ ] 起動/停止確認

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
