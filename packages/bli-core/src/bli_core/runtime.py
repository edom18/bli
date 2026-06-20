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
OUTPUTS_DIRNAME = "outputs"

# タイムアウト調整（spec §7）。サーバの主スレッド実行ウォッチドッグ（DISPATCH_TIMEOUT）が
# クライアントのソケット読み取り猶予（CLIENT_READ_TIMEOUT）より「先に」発火しなければならない。
# そうでないとクライアントが先に切れ、retryable な TIMEOUT(exit2) ではなく
# CONNECTION(exit3) になり、request-status による後追い回収が成立しない。
# 不変条件: CLIENT_READ_TIMEOUT > DISPATCH_TIMEOUT（往復ぶんのマージンを確保）。
DISPATCH_TIMEOUT = 30.0
CLIENT_READ_TIMEOUT = 40.0

# 非同期 job（M10・spec §7）。heavy コマンドは accepted 即返し、CLI は既定で job-wait（request-status
# ポーリング）して最終結果まで自動待機する（エージェントには同期に見える）。auto-wait の総待機上限と
# ポーリング間隔。`--async` 時は即返、`job-wait --timeout` で上限を上書きできる。
# request-status は LOCK_FREE で受信スレッド処理＝重量 job がメインスレッドを塞いでもポーリングは応答する。
JOB_WAIT_TIMEOUT = 1800.0  # 既定 auto-wait 上限（30分・重量 import を見込んだ余裕）
JOB_POLL_INTERVAL = 0.5  # ポーリング間隔（秒）
# ポーリング中に許容する連続の接続失敗回数（瞬断/サーバ再接続に強くする・超過で CONNECTION）。
JOB_POLL_MAX_CONNECT_FAILS = 10

# ウォッチドッグ（M10 T10.3・spec §7 line 337）。重量ネイティブ処理は bpy のメインスレッドを
# 1回の blocking 呼び出しで占有し中断できない＝その間 pump タイマが発火せずメインが固まる。これを
# **検知して観測可能にする**（実行は止めない・通知のみ）。pump タイマが生存印 last_pump_ts を更新し、
# 別スレッド監視が「今 − last_pump_ts > 閾値」で unresponsive を判定する。受信スレッドが lock-free に
# 読んで request-status / doctor 応答へ載せる（メインを待たない＝固まっていても観測できる）。
# 閾値は DISPATCH_TIMEOUT(30s) の2倍＝通常の同期ジョブのタイムアウト待機を誤検知しない安全側。
WATCHDOG_UNRESPONSIVE_THRESHOLD = 60.0  # pump がこの秒数停止したら unresponsive とみなす
WATCHDOG_POLL_INTERVAL = 5.0  # 監視スレッドのチェック間隔（秒）

# duplicate の複製数上限（暴走で Blender を固めるのを防ぐ）。CLI（送信前）/ サーバ（ops）双方が
# この単一定数を参照し、上限のマジックナンバー散在と片側欠落を防ぐ。
MAX_DUPLICATE_COUNT = 1000

# capture（実地FB #1）の出力解像度。巨大値で Blender を固めない上限と、有意な最小値。
# 既定は省略時の viewport/render の幅・高さ。CLI（送信前）/ ops 双方が参照する。
CAPTURE_MIN_DIM = 16
CAPTURE_MAX_DIM = 4096
CAPTURE_DEFAULT_WIDTH = 1024
CAPTURE_DEFAULT_HEIGHT = 768

# undo/redo（実地FB #3）の一度に戻す/進める段数上限。巨大値での暴走を防ぐ。
# CLI（送信前）/ ops 双方が参照する。
MAX_UNDO_STEPS = 100


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


def outputs_dir() -> Path:
    """出力退避（output_ref）の保存先（`BLI_STATE_DIR/outputs` 既定・git 非管理）。

    アドオン（書込）と CLI（読込）が同じ場所を参照する。テストは env で差し替える。
    """
    d = user_state_dir() / OUTPUTS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d
