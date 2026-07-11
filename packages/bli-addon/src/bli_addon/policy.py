"""exec-python のポリシー読取（互換再エクスポート）。実体は bli_core.policy（P1-1 で移設）。

CLI の `bli policy`（表示/編集ヘルパ）とサーバが同じ fail-closed 読取ロジックを共有するため、
読取の実体を純 Python の bli-core へ移した。既存の `bli_addon.policy` 参照（ops/spikes/tests）は
このモジュール経由でそのまま動く。セマンティクス（真実源はユーザローカル policy.toml・
CLI からの昇格不可・fail-closed）は不変。詳細は bli_core/policy.py の docstring を参照。
"""

from __future__ import annotations

from bli_core.policy import (
    DEFAULT_MODE,
    POLICY_FILENAME,
    VALID_MODES,
    policy_path,
    read_allow_hashes,
    read_exec_mode,
)

__all__ = [
    "DEFAULT_MODE",
    "POLICY_FILENAME",
    "VALID_MODES",
    "policy_path",
    "read_allow_hashes",
    "read_exec_mode",
]
