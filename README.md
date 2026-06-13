# bli (Blender CLI)

AIエージェントがCLI経由でBlenderを自律操作するツール。
MCPのトークン非効率を避け、CLIファーストで設計（参照: hatayama/unity-cli-loop）。

## 構成（monorepo / uv workspace）
- `packages/bli-core` — SSOT。コマンド定義・プロトコルcodec・エラー（純Python・依存ゼロ）。
- `packages/bli-cli` — Typer製CLIクライアント（`bli`）。
- `packages/bli-addon` — Blenderアドオン（TCPサーバ + bpy実行）。

## 状態
v1 実装中（M0→M2 縦切り）。仕様: `specs/blender-cli-core/`。

## 開発
```bash
uv sync                       # 依存解決
uv run ruff check .           # lint
uv run pytest                 # L1/L3 テスト
python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src   # 生bpy.ops禁止チェック
```

## セキュリティ
信頼境界はOSプロセス/FS境界。`exec-python` は既定 off。`127.0.0.1` 固定 + トークン認証。
詳細: `specs/blender-cli-core/spec.md` §6。
