# session_lock + RequestRegistry冪等

| 項目 | 内容 |
|------|------|
| マイルストーン | M2 |
| 依存 | 13 |
| テスト層 | L3 |
| 状態 | 未着手 |

## 目的・成果物
- session_lock(fail-fast SESSION_BUSY)
- RequestRegistry(id→{state,event,result,ts}, TTL)
- 同一id冪等

## 完了定義 (DoD)
- 2本目 SESSION_BUSY
- 同一id再実行されない
- TTL掃除

## 実行サブタスク
- [ ] session_lock
- [ ] RequestRegistry
- [ ] 冪等処理
- [ ] ユニット(冪等/BUSY)

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
