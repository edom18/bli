# timer+queue+Event.wait 安定性PoC

| 項目 | 内容 |
|------|------|
| マイルストーン | M0.5 |
| 依存 | 02 |
| テスト層 | L2(実機) |
| 状態 | 未着手 |

## 目的・成果物
- spikes/dispatch_poc.py(bpy.app.timers + queue + threading.Event)
- 別スレッド→メイン処理→Event.wait
- 長時間/多数回の観測

## 完了定義 (DoD)
- 5.0/4.4 で N回安定
- 不安定なら time.sleep 代替所見
- TIMER_INTERVAL 目安

## 実行サブタスク
- [ ] PoC実装
- [ ] 5.0 計測
- [ ] 4.4 計測
- [ ] research.md(論点1)反映 + フォールバック確定

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
