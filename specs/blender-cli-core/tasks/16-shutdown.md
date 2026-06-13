# shutdown手順 + フック

| 項目 | 内容 |
|------|------|
| マイルストーン | M2 |
| 依存 | 13 |
| テスト層 | L3 |
| 状態 | 未着手 |

## 目的・成果物
- shutdown(フラグ→unregister→in-flight解放→close→join)
- unregister/atexit/load_pre 多重防御
- register冒頭で既存stop

## 完了定義 (DoD)
- 停止でリーク無し
- in-flight待ち解放

## 実行サブタスク
- [ ] shutdown シーケンス
- [ ] フック登録
- [ ] シングルトン二重起動防止
- [ ] リーク無し確認

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
