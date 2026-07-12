"""手書き契約カタログ methods.md と SSOT (definitions.py) のドリフト検出（L1）。

methods.md は散文の価値が高いため生成はしない。ここでは構造的事実（コマンド名/param の
CLI オプション表記）が SSOT と methods.md の間でずれていないかだけを突き合わせる。
"""

from __future__ import annotations

import re
from pathlib import Path

from bli_core.commands import load_definitions

ROOT = Path(__file__).resolve().parents[3]
METHODS_MD = ROOT / "specs" / "blender-cli-core" / "contracts" / "methods.md"

_BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")

# Param 名 -> CLI オプション名の例外表（kebab-case 化だけでは表せないもの）。
# with_object は modifier/mesh の BOOLEAN で相手 mesh を指定する --with（--with-object ではない）。
_PARAM_NAME_OVERRIDES = {"with_object": "with"}


def _param_option(param_name: str) -> str:
    """Param.name を methods.md 上の CLI オプション表記（--xxx）へ変換する。"""
    base = _PARAM_NAME_OVERRIDES.get(param_name, param_name)
    return "--" + base.replace("_", "-")


def _read_methods_md() -> str:
    return METHODS_MD.read_text(encoding="utf-8")


def test_all_implemented_commands_appear_in_methods_md():
    """load_definitions() の implemented=True な全コマンド名が methods.md に載っている。"""
    text = _read_methods_md()
    cmds = load_definitions()
    missing = sorted(
        name for name, cmd in cmds.items() if cmd.implemented and f"`{name}`" not in text
    )
    assert not missing, (
        "SSOT に実装済みのコマンドが methods.md に見つかりません。"
        "methods.md への追記が必要です（該当セクションの表に `コマンド名` を追加）: "
        + ", ".join(missing)
    )


def test_methods_md_table_command_names_exist_in_ssot():
    """methods.md の表の先頭セルに現れるコマンド名は SSOT（+CLI ローカルコマンド）に存在する。

    改名/削除の取り残し（SSOT には無いのに methods.md にだけ残っている名前）を検出する。
    """
    text = _read_methods_md()
    cmds = load_definitions()
    known = set(cmds.keys()) | {"help", "list-commands"}

    unknown: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 2:
            continue
        first_cell = cells[1]
        for token in _BACKTICK_TOKEN_RE.findall(first_cell):
            if token not in known:
                unknown.add(token)

    assert not unknown, (
        "methods.md の表にある名前が SSOT に存在しません（改名/削除の取り残しの疑い）: "
        + ", ".join(sorted(unknown))
    )


def test_all_implemented_params_have_cli_option_in_methods_md():
    """implemented な各コマンドの各 Param の CLI オプション表記が methods.md 全文に現れる。"""
    text = _read_methods_md()
    cmds = load_definitions()

    missing = []
    for name, cmd in cmds.items():
        if not cmd.implemented:
            continue
        for param in cmd.params:
            option = _param_option(param.name)
            if option not in text:
                missing.append(f"{name}.{option}")

    assert not missing, (
        "SSOT のパラメータに対応する CLI オプション表記が methods.md に見つかりません。"
        "methods.md への追記が必要です（該当コマンドの params 列または直下の prose に追加）: "
        + ", ".join(missing)
    )
