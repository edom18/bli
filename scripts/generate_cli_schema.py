"""`.claude/skills/bli/reference/cli-schema.json` を SSOT から生成する（M12 T12.2）。

bli-core の定義（commands/schema）から全コマンドのメタ + JSON Schema + `schema_hash` を生成し、
Claude Code Skill が参照するキャッシュ（cli-schema.json）として書き出す。アドオン接続は不要
（ローカル完結）。**この JSON は手で編集しない**＝SSOT を変えたら本スクリプトで再生成する。

実行: `uv run python scripts/generate_cli_schema.py`
ドリフト検証: `tests/test_skill_schema_sync.py` が「キャッシュの schema_hash == ライブ SSOT」を強制する。
"""

from __future__ import annotations

import json
from pathlib import Path

from bli_core.commands import Command, load_definitions
from bli_core.schema import schema_hash, to_json_schema

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".claude" / "skills" / "bli" / "reference" / "cli-schema.json"


def _command_entry(cmd: Command) -> dict:
    return {
        "name": cmd.name,
        "summary": cmd.summary,
        "mutates": cmd.mutates,
        "required_mode": cmd.required_mode.value,
        "stability": cmd.stability.value,
        "is_heavy": cmd.is_heavy,
        "heavy_ops": list(cmd.heavy_ops),
        "capability_deps": list(cmd.capability_deps),
        "implemented": cmd.implemented,
        "schema": to_json_schema(cmd),
    }


def build_document() -> dict:
    """cli-schema.json の中身を組み立てる（決定的・name 昇順）。"""
    cmds = load_definitions()
    commands = [_command_entry(c) for c in sorted(cmds.values(), key=lambda c: c.name)]
    return {
        "schema_hash": schema_hash(cmds),
        "note": (
            "SSOT（bli-core definitions）から生成。手で編集しない。"
            "再生成: uv run python scripts/generate_cli_schema.py"
        ),
        "commands": commands,
    }


def main() -> None:
    doc = build_document()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys + indent で決定的な diff にする（SSOT 変更時だけ差分が出る）。末尾改行を付ける。
    OUTPUT.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {OUTPUT.relative_to(ROOT)} (schema_hash={doc['schema_hash'][:16]}…, {len(doc['commands'])} commands)"
    )


if __name__ == "__main__":
    main()
