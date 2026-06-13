# to_json_schema/validate/schema_hash

| 項目 | 内容 |
|------|------|
| マイルストーン | M1 |
| 依存 | 09 |
| テスト層 | L1 |
| 状態 | 未着手 |

## 目的・成果物
- bli_core/schema.py(to_json_schema, validate_from_dict, schema_hash)

## 完了定義 (DoD)
- スキーマがdraft2020妥当
- 型/必須/enum違反捕捉
- schema_hash 決定的

## 実行サブタスク
- [ ] to_json_schema
- [ ] validate_from_dict(ErrorObject)
- [ ] schema_hash(正規化+sha256)
- [ ] ユニット

## 参照
spec.md / plan.md(§4) / data-model.md / contracts/ / research.md
