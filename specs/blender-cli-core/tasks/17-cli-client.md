# CLIクライアント + connection.json + init/ping

| 項目 | 内容 |
|------|------|
| マイルストーン | M2 |
| 依存 | 12 |
| テスト層 | L3 |
| 状態 | 未着手 |

## 目的・成果物
- bli/client.py(connection.json解決 flag>env>file>9876, frame送受, token提示)
- bli/main.py(Typer, --json/--id, 終了コード)
- init/ping/doctor(最小)
- human/json 出力

## 完了定義 (DoD)
- ping が hello→echo 往復
- 終了コード 0/3/4
- connection.json 解決

## 実行サブタスク
- [ ] client(接続/HELLO/送受)
- [ ] Typer app + フラグ + 終了コード
- [ ] init/ping/doctor
- [ ] human/json 出力

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
