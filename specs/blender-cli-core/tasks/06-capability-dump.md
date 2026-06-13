# bl_rna/operator ダンプ(5.0/4.4実機)

| 項目 | 内容 |
|------|------|
| マイルストーン | M0.5 |
| 依存 | 02 |
| テスト層 | L2(実機) |
| 状態 | 未着手 |

## 目的・成果物
- packages/bli-addon/spikes/dump_capabilities.py
- blender --background --python で実行
- STL/OBJ/glTF/FBX/3MF/print3d の operator実在・引数・addon module を JSON 出力
- research.md 付録へ反映

## 完了定義 (DoD)
- 5.0/4.4 両方で成功
- OperatorResolver 候補表の確定値
- [要実機検証](論点3)消化

## 実行サブタスク
- [ ] dump スクリプト実装
- [ ] Blender 5.0 実行・JSON保存
- [ ] Blender 4.4 実行・JSON保存
- [ ] 確定表化し research.md 反映

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
