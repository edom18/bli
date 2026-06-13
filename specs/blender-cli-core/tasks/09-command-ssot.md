# Command/Param dataclass + @command 登録

| 項目 | 内容 |
|------|------|
| マイルストーン | M1 |
| 依存 | 02 |
| テスト層 | L1 |
| 状態 | 未着手 |

## 目的・成果物
- bli_core/types.py(ParamType/Mode/Stability enum)
- bli_core/commands.py(Param, Command, @command レジストリ)
- 代表コマンド最小登録

## 完了定義 (DoD)
- @command でレジストリ登録
- data-model.md §1 と一致
- 純Python・依存ゼロ

## 実行サブタスク
- [ ] types.py
- [ ] Param/Command dataclass
- [ ] @command + レジストリ
- [ ] 代表登録 + ユニット

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
