# packages 3分割の雛形

| 項目 | 内容 |
|------|------|
| マイルストーン | M0 |
| 依存 | 01 |
| テスト層 | — |
| 状態 | 未着手 |

## 目的・成果物
- packages/bli-core (src/bli_core, 依存ゼロ, py>=3.10)
- packages/bli-cli (src/bli, entry: bli, deps: typer,pydantic)
- packages/bli-addon (src/bli_addon, blender_manifest.toml, vendored/)
- 各 pyproject.toml(PEP621)

## 完了定義 (DoD)
- 3パッケージが uv workspace で解決
- bli-core は dependencies=[]
- 各 __init__.py に __version__

## 実行サブタスク
- [ ] bli-core パッケージ作成(src-layout)
- [ ] bli-cli パッケージ作成(entry point)
- [ ] bli-addon パッケージ作成 + blender_manifest.toml 雛形
- [ ] vendored/ + .gitkeep
- [ ] uv sync で解決確認

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
