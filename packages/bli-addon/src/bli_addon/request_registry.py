"""RequestRegistry（冪等性）。data-model.md §4 / spec §7。bpy 非依存。

同一 id の再送は再実行せず保存結果を返す。実行中は IN_PROGRESS。
TTL でメモリ常駐エントリを掃除する（再起動で揮発 = v1 Non-Goal）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

PENDING = "PENDING"
RUNNING = "RUNNING"
DONE = "DONE"
FAILED = "FAILED"


@dataclass
class _Entry:
    state: str = PENDING
    result: dict[str, Any] | None = None
    ts: float = field(default_factory=time.time)


class RequestRegistry:
    def __init__(self, ttl: float = 600.0) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def _purge(self, now: float) -> None:
        expired = [k for k, e in self._entries.items() if now - e.ts > self._ttl]
        for k in expired:
            del self._entries[k]

    def begin(self, rid: str) -> tuple[str, dict[str, Any] | None]:
        """実行可否を判定する。

        戻り値:
          ("new", None)         -> 初回。呼び出し側が実行し complete() する。
          ("cached", result)    -> 既に DONE/FAILED。保存結果を返す。
          ("in_progress", None) -> 実行中（RUNNING）。IN_PROGRESS を返す。
        """
        now = time.time()
        with self._lock:
            self._purge(now)
            entry = self._entries.get(rid)
            if entry is not None:
                if entry.state in (DONE, FAILED):
                    return "cached", entry.result
                return "in_progress", None
            self._entries[rid] = _Entry(state=RUNNING, ts=now)
            return "new", None

    def complete(self, rid: str, result: dict[str, Any], ok: bool) -> None:
        with self._lock:
            entry = self._entries.get(rid)
            if entry is None:
                entry = _Entry()
                self._entries[rid] = entry
            entry.state = DONE if ok else FAILED
            entry.result = result
            entry.ts = time.time()

    def status(self, rid: str) -> str | None:
        with self._lock:
            entry = self._entries.get(rid)
            return entry.state if entry else None

    def lookup(self, rid: str) -> tuple[str | None, dict[str, Any] | None]:
        """状態と保存結果を返す（request-status 用）。未知/TTL超過なら (None, None)。

        status のみをポーリングする経路でも TTL 掃除が効くよう、読み取り前に purge する
        （begin() と同じ扱い。さもないと未確定エントリが恒久的に残る）。
        """
        now = time.time()
        with self._lock:
            self._purge(now)
            entry = self._entries.get(rid)
            if entry is None:
                return None, None
            return entry.state, entry.result
