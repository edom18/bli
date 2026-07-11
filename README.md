# bli (Blender CLI)

Blender を **CLI から汎用操作する基盤**（AIエージェント向け）。
生成・変形・メッシュ編集・マテリアル・シーン整理・ファイル I/O まで、Blender の一般的な操作を
構造化サブコマンドで安全に自律実行できる。原点変更・直立補正・3Dプリンタ対応（単位/健全性チェック/
STL書き出し）は代表的なユースケース例。Unity 等ゲームエンジン連携（import → 変形・修復・原点調整 →
FBX/GLB export）にも使える。
MCPのトークン非効率を避け、CLIファーストで設計（参照: hatayama/unity-cli-loop）。

## 構成（monorepo / uv workspace）
- `packages/bli-core` — SSOT。コマンド定義・プロトコルcodec・エラー（純Python・依存ゼロ）。
- `packages/bli-cli` — Typer製CLIクライアント（`bli`）。
- `packages/bli-addon` — Blenderアドオン（TCPサーバ + bpy実行）。

## 状態
v1 実装中。M0〜M13 完了（基盤〜情報取得〜編集〜メッシュ〜3シナリオ〜ファイルI/O〜非同期job〜
exec-python〜Skill同梱〜テスト&CI）。M14（ドキュメント & 配布）進行中。仕様: `specs/blender-cli-core/`。

## インストール
bli は **CLI（`bli`）** と **Blender アドオン（常駐 TCP サーバ）** の 2 つを導入する。

### 1. CLI（`bli`）
グローバルツールとして導入する（uv が workspace の `bli-core` も解決する）:
```bash
uv tool install --from packages/bli-cli bli-cli   # `bli` が PATH に入る
```
> リポジトリ内なら追加導入なしで `uv run bli <command>` でも実行できる（開発時はこちら）。

### 2. Blender アドオン（配布 zip）
アドオンは Blender 埋め込み Python（3.11 系・venv なし）で動くため、dev の `bli-core` を
知らない。配布 zip に `bli-core` を**同梱（vendoring）**してから導入する。

1. 配布 zip をビルド（`bli-core` を `vendored/` へ同梱・決定的ビルド）:
   ```bash
   uv run python scripts/build_addon.py        # → dist/bli_server-<ver>.zip
   ```
2. Blender を起動 → **Edit > Preferences > Add-ons** → 右上 **Install from Disk…** →
   `dist/bli_server-<ver>.zip` を選ぶ（5.0 / 4.4 両対応の legacy add-on 形式）。
3. 一覧の **bli (Blender CLI) server** にチェックを入れて有効化する。
   - 有効化で `127.0.0.1:9876` 待受 + 接続情報がユーザローカル（git 非管理）に書き出される:
     - Windows: `%LOCALAPPDATA%\bli\connection.json` / `session.token`
     - macOS/Linux: `$XDG_STATE_HOME/bli`（既定 `~/.local/state/bli`）
4. 疎通を確認する:
   ```bash
   bli doctor    # 接続情報の有無・到達性（未到達なら導入ガイドを表示）
   bli ping      # Blender バージョン・capabilities
   ```

接続は `127.0.0.1` 固定 + トークン認証。CLI はこのファイルを自動で読む（`BLI_STATE_DIR` で差し替え可）。

> 開発時は GUI 起動とアドオン register を 1 コマンドで行うヘルパが使える（zip 導入は不要）:
> ```bash
> "/c/Program Files/Blender Foundation/Blender 5.0/blender.exe" --python scripts/launch_blender_gui.py
> ```

## 開発
```bash
uv sync                       # 依存解決
uv run ruff check .           # lint
uv run ruff format --check .  # format チェック
uv run pytest                 # L1/L3 テスト
python scripts/check_no_raw_bpy_ops.py packages/bli-addon/src   # 生bpy.ops禁止チェック
python scripts/build_addon.py                                   # 配布 zip をビルド
```

## Claude Code と連携して使う
専用の Claude Code Skill を同梱済み（`.claude/skills/bli/`・M12）。Claude Code は Skill の
手順に沿って Bash ツールで `bli <command>` を実行し、常駐 Blender を駆動する。

- **コマンド発見はローカル完結**（アドオン/Blender 不要・SSOT から生成）。エージェントはまず
  これでカタログを取得すると確実:
  ```bash
  bli list-commands --json                # コマンド一覧 + schema_hash
  bli help --command set-origin --json    # 個別コマンドの params/スキーマ
  ```
  Skill 同梱の `.claude/skills/bli/reference/cli-schema.json`（全コマンド + schema_hash）も参照できる。
- 実行例:
  ```bash
  bli scene-info
  bli set-origin --targets Cube --to geometry
  bli export --format gltf --path out.glb
  ```
- 終了コード（spec §8）: `0`=成功 / `1`=確定失敗 / `2`=TIMEOUT_PENDING（`bli request-status --id <id>`
  で後追い）/ `3`=接続不能 / `4`=入力エラー。

### Claude Code 側の設定（任意・許可プロンプトを減らす）
`.claude/settings.json` の `allow` に bli の実行を **スコープ付きで** 追加する（ワイルドカード `Bash(*)` は使わない）:
```json
{ "permissions": { "allow": ["Bash(bli *)", "Bash(uv run bli *)"] } }
```
`CLAUDE.md` に「Blender 操作は `bli` を使う。まず `bli list-commands --json` で発見する」と書いておくと、エージェントが迷わない。

## セキュリティ
信頼境界はOSプロセス/FS境界。`exec-python` は既定 off（`restricted` で AST ブロックリスト検査つき自走可・
有効化は `bli policy --action set --mode restricted`）。`127.0.0.1` 固定 + トークン認証。
詳細: `specs/blender-cli-core/spec.md` §6。
