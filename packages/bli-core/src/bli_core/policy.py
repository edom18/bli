"""exec-python のポリシー読取（M11 T11.1・R-A / P1-1 で bli-core へ移設）。spec §276 / §284 / §459。

exec の mode（off|restricted|audited|trusted）の **真実源はユーザローカル policy.toml**
（`BLI_STATE_DIR/policy.toml`・OS 所有者限定・git 非管理）。サーバ（アドオン）だけがこれを読んで
実行可否を決める。CLI の `bli policy` ヘルパも表示/編集に同じ読取ロジックを使うため、
addon 専用だった読取を純 Python の bli-core へ移設した（`bli_addon.policy` は互換再エクスポート）。

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

from . import runtime

POLICY_FILENAME = "policy.toml"
# restricted は P1-1（設計レビュー 2026-07-11 G0）で追加: AST ブロックリスト検査つき自走
# （Blender API は全面許可・プロセス/ネットワーク/削除系等を検出したら EXEC_BLOCKED_RESTRICTED）。
VALID_MODES = ("off", "restricted", "audited", "trusted")
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


def read_allow_hashes() -> frozenset[str]:
    """policy.toml の `[exec] allow_hashes`（許可コードの sha256・小文字16進）を返す（M11 T11.3・R-B）。

    audited モードはここに一致する sha256 のコードだけ自走実行する。不在/不正は空集合（fail-closed）。
    要素は小文字に正規化し、文字列でないものは無視する。
    """
    exec_section = _load_policy().get("exec")
    if not isinstance(exec_section, dict):
        return frozenset()
    raw = exec_section.get("allow_hashes")
    if not isinstance(raw, list):
        return frozenset()
    # コピペ事故（前後空白・大文字）で沈黙して自走しない事態を減らすため strip + lower で正規化する。
    return frozenset(h.strip().lower() for h in raw if isinstance(h, str))
