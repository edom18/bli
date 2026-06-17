# bli (Blender CLI)

AIエージェントがCLI経由でBlenderを自律操作するツール。
MCPのトークン非効率を避け、CLIファーストで設計（参照: hatayama/unity-cli-loop）。

## 構成（monorepo / uv workspace）
- `packages/bli-core` — SSOT。コマンド定義・プロトコルcodec・エラー（純Python・依存ゼロ）。
- `packages/bli-cli` — Typer製CLIクライアント（`bli`）。
- `packages/bli-addon` — Blenderアドオン（TCPサーバ + bpy実行）。

## 状態
v1 実装中。M0〜M9（基盤〜情報取得〜編集〜メッシュ〜3シナリオ〜ファイルI/O）まで完了。仕様: `specs/blender-cli-core/`。

## 開発
```bash
uv sync                       # 依存解決
uv run ruff check .           # lint
uv run pytest                 # L1/L3 テスト
python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src   # 生bpy.ops禁止チェック
```

## Claude Code と連携して使う
Claude Code から `bli` コマンドを実行して、常駐 Blender を操作する。
（専用の Claude Code Skill 同梱（`.claude/skills/bli/`）は M12 で予定。それまでは以下の手順。）

### 1. Blender 側でアドオン（TCPサーバ）を起動する
GUI の Blender 内 Python でアドオンを register する。開発時は同梱ヘルパが使える:
```bash
# GUI 起動と同時に bli アドオンを登録（127.0.0.1:9876 で待受）
"/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" \
  --python scripts/launch_blender_gui.py
```
起動すると接続情報が **ユーザローカル**（git 非管理）に書き出される:
- Windows: `%LOCALAPPDATA%\bli\connection.json` / `session.token`
- macOS/Linux: `$XDG_STATE_HOME/bli`（既定 `~/.local/state/bli`）

接続は `127.0.0.1` 固定 + トークン認証。CLI はこのファイルを自動で読む（`BLI_STATE_DIR` で差し替え可）。

### 2. 疎通を確認する
別シェルから:
```bash
uv run bli doctor    # connection.json/token の有無・アドオン到達性
uv run bli ping      # Blender バージョン・capabilities を表示
```

### 3. Claude Code から操作する
Claude Code は Bash ツールで `bli <command>` を実行して Blender を駆動する。

- **コマンド発見はローカル完結**（アドオン/Blender 不要・SSOTから生成）。エージェントはまずこれでカタログを取得すると確実:
  ```bash
  uv run bli list-commands --json                # コマンド一覧 + schema_hash
  uv run bli help --command set-origin --json    # 個別コマンドの params/スキーマ
  ```
- 実行例:
  ```bash
  uv run bli scene-info
  uv run bli set-origin --targets Cube --to geometry
  uv run bli export --format gltf --path out.glb
  ```
- 終了コード（spec §8）: `0`=成功 / `1`=確定失敗 / `2`=TIMEOUT_PENDING（`bli request-status --id <id>` で後追い）/ `3`=接続不能 / `4`=入力エラー。

### Claude Code 側の設定（任意・許可プロンプトを減らす）
`.claude/settings.json` の `allow` に bli の実行を **スコープ付きで** 追加する（ワイルドカード `Bash(*)` は使わない）:
```json
{ "permissions": { "allow": ["Bash(uv run bli *)"] } }
```
`CLAUDE.md` に「Blender 操作は `bli` を使う。まず `bli list-commands --json` で発見する」と書いておくと、エージェントが迷わない。

## セキュリティ
信頼境界はOSプロセス/FS境界。`exec-python` は既定 off。`127.0.0.1` 固定 + トークン認証。
詳細: `specs/blender-cli-core/spec.md` §6。
