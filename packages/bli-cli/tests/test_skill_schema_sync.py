"""Claude Code Skill のスキーマキャッシュ同期テスト（M12 T12.3）。

`.claude/skills/bli/reference/cli-schema.json` が SSOT（bli-core 定義）と一致することを強制する。
SSOT を変えてキャッシュを再生成し忘れると **ここで fail** する（schema snapshot・ドリフト検出）。
再生成: `uv run python scripts/generate_cli_schema.py`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from bli_core.commands import load_definitions
from bli_core.schema import schema_hash

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_FILE = ROOT / ".claude" / "skills" / "bli" / "reference" / "cli-schema.json"
SKILL_FILE = ROOT / ".claude" / "skills" / "bli" / "SKILL.md"

# 生成スクリプトの build_document を再利用してドリフトを完全比較する。
sys.path.insert(0, str(ROOT / "scripts"))
import generate_cli_schema  # noqa: E402


def test_skill_files_exist():
    assert SKILL_FILE.exists(), "SKILL.md が無い（M12 T12.1）"
    assert SCHEMA_FILE.exists(), (
        "cli-schema.json が無い（uv run python scripts/generate_cli_schema.py）"
    )


def test_cached_schema_hash_matches_ssot():
    cached = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert cached["schema_hash"] == schema_hash(load_definitions()), (
        "cli-schema.json の schema_hash が SSOT とずれています。"
        "uv run python scripts/generate_cli_schema.py で再生成してください。"
    )


def test_cached_schema_is_fully_regenerated():
    # schema_hash に出ない to_json_schema の変化まで拾うため、再生成結果と完全一致を要求する。
    cached = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert cached == generate_cli_schema.build_document(), (
        "cli-schema.json が SSOT から再生成した内容と一致しません。"
        "uv run python scripts/generate_cli_schema.py で再生成してください。"
    )


def test_cached_schema_lists_all_implemented_commands():
    cached = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    cached_names = {c["name"] for c in cached["commands"]}
    live_impl = {name for name, c in load_definitions().items() if c.implemented}
    assert live_impl <= cached_names, "実装済みコマンドがキャッシュに漏れています"


def test_skill_frontmatter_present():
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---"), "SKILL.md に frontmatter が無い"
    assert "name: bli" in text
    assert "description:" in text
