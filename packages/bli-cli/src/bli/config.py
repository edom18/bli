"""プロジェクトローカル設定（`.bli/`）の雛形生成。spec §6 / D14。

トークン/connection.json はユーザローカル（runtime）に置く。`.bli/` には
権限ポリシー（exec mode 等）とプロジェクト設定のみを置き、機微情報は置かない。
"""

from __future__ import annotations

from pathlib import Path

CONFIG_TOML = """# bli プロジェクト設定（.bli/config.toml）
# トークン/connection.json はここに置かない（ユーザローカル・git非管理）。

[exec]
# 注意（M11・R-A）: この mode は **表示用ヒント** に過ぎず、サーバ（Blender アドオン）は読まない。
# exec の真実源は **ユーザローカルの policy.toml**（BLI_STATE_DIR/policy.toml・OS 所有者限定）。
# リポジトリに mode=trusted を commit しても昇格しない。実際に有効化するには自分の OS アカウントの
# policy.toml に [exec] mode を書く（CLI フラグ単体では昇格できない・spec §276/§459）。
#   policy.toml の例:
#     [exec]
#     mode = "trusted"                       # off | audited | trusted
#     # audited のときは許可した sha256 のコードだけ自走実行する（R-B）:
#     # allow_hashes = ["<exec 応答の code_sha256>", ...]
#   exec の試行はすべて BLI_STATE_DIR/audit/exec.jsonl に記録される（防止でなく検知・§280）。
mode = "off"          # off | audited | trusted（既定 off・表示用）

[server]
port = 9876
bind = "127.0.0.1"    # 変更不可（127.0.0.1 固定）
read_timeout = 30.0

[outputs]
inline_threshold = 65536
"""

GITIGNORE = """# bli ランタイム・機微情報は git 管理しない
*.token
session.token
connection.json
outputs/
audit/
"""


def write_project_scaffold(cwd: Path, force: bool = False) -> list[str]:
    """`.bli/` に config.toml と .gitignore を作成する。作成したパス一覧を返す。"""
    created: list[str] = []
    bli_dir = cwd / ".bli"
    bli_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        bli_dir / "config.toml": CONFIG_TOML,
        bli_dir / ".gitignore": GITIGNORE,
    }
    for path, content in targets.items():
        if path.exists() and not force:
            continue
        path.write_text(content, encoding="utf-8")
        created.append(str(path))
    return created
