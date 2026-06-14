"""ランタイム定数の不変条件（L1）。"""

from __future__ import annotations

from bli_core import runtime


def test_client_read_timeout_has_margin_over_dispatch():
    # サーバの主スレッド watchdog は、クライアントのソケット読み取り猶予より先に発火する必要がある。
    # （でないと TIMEOUT(exit2) ではなく CONNECTION(exit3) になり request-status 回収が崩れる）
    assert runtime.CLIENT_READ_TIMEOUT > runtime.DISPATCH_TIMEOUT
