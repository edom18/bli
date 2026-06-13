# ruff/pyright/pre-commit

| 項目 | 内容 |
|------|------|
| マイルストーン | M0 |
| 依存 | 02 |
| テスト層 | — |
| 状態 | 未着手 |

## 目的・成果物
- ruff 設定(lint+format, target py310)
- pyright 設定
- pre-commit(任意)

## 完了定義 (DoD)
- uv run ruff check . が通る
- ruff format --check 整合
- 空コードで型チェック緑

## 実行サブタスク
- [ ] ruff 設定([tool.ruff])
- [ ] pyright/mypy 設定
- [ ] ruff check/format 確認

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
