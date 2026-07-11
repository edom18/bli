"""exec-python の監査ログ（M11 T11.3）。spec §280「防止でなく検知・追跡」。

サンドボックスを提供しない（§459）代償として、メインスレッドの単一実行口を通る exec の **試行を
すべて `audit/` に追記**する。trusted/restricted/audited の実行も、off/audited-unlisted/
restricted-blocked の拒否も記録する＝事後追跡の証跡。`BLI_STATE_DIR/audit/exec.jsonl`
（JSONL・1行1イベント・policy.toml と同じ信頼域）。

bpy 非依存（純Python）＝pytest 可。書込は best-effort（失敗しても exec 自体は止めない＝可用性優先・
ただし呼び出し側は戻り値 False を `audit_ok=false` として応答に載せ、証跡欠落を観測可能にする）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from bli_core import runtime

AUDIT_LOG_FILENAME = "exec.jsonl"


def code_sha256(code: str) -> str:
    """exec コード文字列の sha256（16進・小文字）。許可ハッシュ照合と監査記録に使う。"""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


@dataclasses.dataclass
class AuditEntry:
    """1 件の exec 監査イベント。"""

    ts: str  # ISO8601 UTC
    mode: str  # off | restricted | audited | trusted
    decision: (
        str  # executed | rejected:off | rejected:audited-unlisted | rejected:restricted-blocked
    )
    code_sha256: str | None
    code_len: int | None
    heuristic_flags: list[str]
    source: str  # "code" | "file:<path>"
    # restricted の拒否理由（exec_restricted.scan_blocked の結果・P1-1）。None は「検査対象外の
    # 経路」＝to_dict で省略し、既存 JSONL 行のスキーマを変えない（blocked: [] は「検査して通過」）。
    blocked: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        if d.get("blocked") is None:
            del d["blocked"]
        return d


def make_entry(
    *,
    mode: str,
    decision: str,
    source: str,
    code_sha256: str | None = None,
    code_len: int | None = None,
    heuristic_flags: list[str] | None = None,
    blocked: list[str] | None = None,
) -> AuditEntry:
    """監査エントリを組み立てる（ts は UTC now）。"""
    return AuditEntry(
        ts=datetime.now(timezone.utc).isoformat(),
        mode=mode,
        decision=decision,
        code_sha256=code_sha256,
        code_len=code_len,
        heuristic_flags=heuristic_flags or [],
        source=source,
        blocked=blocked,
    )


def record(entry: AuditEntry) -> bool:
    """エントリを audit ログへ追記する。成功で True / 失敗で False（best-effort・例外は投げない）。"""
    try:
        path = runtime.audit_dir() / AUDIT_LOG_FILENAME
        line = json.dumps(entry.to_dict(), ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except OSError:
        return False


def read_entries() -> list[dict[str, Any]]:
    """audit ログを読み出す（テスト/検証用）。不在は空リスト。壊れた行（手書き混入等）は skip する。"""
    path = runtime.audit_dir() / AUDIT_LOG_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 第三者が壊れた行を書いても読取は落ちない（堅牢化）
    return entries
