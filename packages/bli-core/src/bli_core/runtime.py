"""ランタイム配置（connection.json / token）の共有ロジック。data-model.md §6/§7。

アドオン（書き込み）と CLI（読み取り）の双方が同じ場所を参照するため bli-core に置く。
トークンと connection.json は **ユーザローカル**（git 非管理）。
テストは環境変数 `BLI_STATE_DIR` で差し替える。
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876

CONNECTION_FILENAME = "connection.json"
TOKEN_FILENAME = "session.token"


def user_state_dir() -> Path:
    """OS 別のユーザローカル状態ディレクトリ（token/connection.json 用）。"""
    override = os.environ.get("BLI_STATE_DIR")
    if override:
        base = Path(override)
    elif os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        base = Path(root) / "bli"
    else:
        root = os.environ.get("XDG_STATE_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "state"
        )
        base = Path(root) / "bli"
    base.mkdir(parents=True, exist_ok=True)
    return base


def connection_path() -> Path:
    return user_state_dir() / CONNECTION_FILENAME


def token_path() -> Path:
    return user_state_dir() / TOKEN_FILENAME
