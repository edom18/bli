# git init + uv workspace ルート

| 項目 | 内容 |
|------|------|
| マイルストーン | M0 |
| 依存 | なし |
| テスト層 | — |
| 状態 | 未着手 |

## 目的・成果物
- git repo + feature/m0-bootstrap
- ルート pyproject.toml ([tool.uv.workspace])
- .gitignore(.bli/,outputs/,token,__pycache__,*.blend1)
- README.md 雛形

## 完了定義 (DoD)
- uv sync が通る
- uv.lock 生成
- feature ブランチ上で作業

## 実行サブタスク
- [ ] ルート pyproject.toml に [tool.uv.workspace] members=packages/*
- [ ] .gitignore 作成(機微情報除外)
- [ ] README.md 雛形
- [ ] uv sync 実行確認

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
