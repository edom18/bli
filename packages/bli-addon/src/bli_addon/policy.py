"""exec-python のポリシー読取（M11 T11.1・R-A）。spec §276 / §284 / §459。

exec の mode（off|audited|trusted）の **真実源はユーザローカル policy.toml**
（`BLI_STATE_DIR/policy.toml`・OS 所有者限定・git 非管理）。サーバ（このアドオン）だけがこれを読む。

**ここが M11 の肝**: CLI が送る mode は無視する＝CLI フラグ単体では昇格できない。リポジトリ内の
`.bli/config.toml` に `mode = "trusted"` を commit しても昇格しない（サーバは config.toml を読まない）。
昇格はユーザが自分の OS アカウントの policy.toml を編集したときだけ成立する（spec §276）。

fail-closed: ファイル不在・パース失敗・不正な mode 値はすべて "off"（無効）へ丸める。

bpy 非依存（標準 `tomllib` は addon の Blender Python 3.11+ / テストの 3.12 にあり）＝pytest 可。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomllib

from bli_core import runtime

POLICY_FILENAME = "policy.toml"
VALID_MODES = ("off", "audited", "trusted")
DEFAULT_MODE = "off"


def policy_path() -> Path:
    """ユーザローカル policy.toml のパス（`BLI_STATE_DIR/policy.toml`）。"""
    return runtime.user_state_dir() / POLICY_FILENAME


def _load_policy() -> dict[str, Any]:
    """policy.toml を辞書で返す。不在/パース失敗は空 dict（fail-closed の起点）。"""
    path = policy_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, UnicodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_exec_mode() -> str:
    """policy.toml の `[exec] mode` を返す。不在/不正は "off"（fail-closed・R-A）。

    CLI が送るどんな値とも独立＝この関数だけが exec の可否を決める真実源。
    """
    exec_section = _load_policy().get("exec")
    if isinstance(exec_section, dict):
        mode = exec_section.get("mode")
        if mode in VALID_MODES:
            return str(mode)
    return DEFAULT_MODE
