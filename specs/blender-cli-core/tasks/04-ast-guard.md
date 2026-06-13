# 生 bpy.ops 禁止 AST lint

| 項目 | 内容 |
|------|------|
| マイルストーン | M0 |
| 依存 | 02 |
| テスト層 | L1 |
| 状態 | 未着手 |

## 目的・成果物
- scripts/check_no_raw_bpy_ops.py
- bli_addon 配下で run_operator 経由以外の bpy.ops.* を検出しエラー
- 許可リスト

## 完了定義 (DoD)
- 違反コードを fail
- 正常コードを pass

## 実行サブタスク
- [ ] AST Visitor で bpy.ops.<ns>.<op>( 検出
- [ ] run_operator/許可リストを除外
- [ ] 正常/違反テスト
- [ ] exit code

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
