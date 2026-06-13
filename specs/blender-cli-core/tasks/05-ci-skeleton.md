# CIスケルトン(lint + L1枠)

| 項目 | 内容 |
|------|------|
| マイルストーン | M0 |
| 依存 | 03,04 |
| テスト層 | — |
| 状態 | 未着手 |

## 目的・成果物
- .github/workflows/test-blender.yml(unit + lint + ast-guard)
- L2(matrix 5.0/4.4)/L3 枠

## 完了定義 (DoD)
- YAML構文妥当
- unitジョブが ruff+pytest+ast-guard

## 実行サブタスク
- [ ] unit ジョブ定義
- [ ] bpy-integration matrix 枠(5.0/4.4)
- [ ] ローカル等価コマンド確認

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
