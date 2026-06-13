# run_operator/temp_override/undo_push 実機確認

| 項目 | 内容 |
|------|------|
| マイルストーン | M0.5 |
| 依存 | 02 |
| テスト層 | L2(実機) |
| 状態 | 未着手 |

## 目的・成果物
- spikes/op_spike.py
- temp_override で origin_set 等
- poll先行 / FINISHED 判定 / undo_push 挙動(5.0/4.4)

## 完了定義 (DoD)
- temp_override 必要メンバ集合
- undo_push 引数互換
- [要実機検証](論点2)消化

## 実行サブタスク
- [ ] op_spike 実装
- [ ] 5.0 実行
- [ ] 4.4 実行
- [ ] 所見を research.md 反映

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
