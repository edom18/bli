"""Spike VII: 常駐 GUI Blender で `wm.open_mainfile` がディスパッチ機構を壊さないか検証（M9 T9.4・最高リスク）。

`open_mainfile` は **シーン全体（.blend 全体）を置換**する。本番デプロイは「常駐 GUI Blender +
アドオン」で、リクエストは次の経路で処理される:
    server 受信スレッド → Dispatcher.submit → メインスレッドの pump() が **bpy.app.timers の
    _tick コールバック内**で ops.dispatch → gateway → bpy.ops を実行。
つまり `open` を実装すると、`open_mainfile` は **pump タイマのコールバック内**から呼ばれる。
ここで懸念されるのは file load による以下のリセット（research.md §[要実機検証]）:
  - `bpy.app.timers`（Dispatcher の pump タイマ）が file load で解除されないか。
  - 非 persistent な `bpy.app.handlers` が file load でクリアされないか。
  - TCP サーバ（Python スレッド）が生存するか。

**Dispatcher は pump タイマを `persistent=True` で登録している**（dispatcher.py: install_timer）。
Blender API 上 persistent タイマは file load で解除されない設計なので、pump は生存する見込み。
本スパイクはそれを **実機で確定**し、外れるなら open 後の `load_post(persistent)` 再登録設計へ倒す。

**GUI モード**で実行する（`--background` では bpy.app.timers が発火せず検証不能）:
    "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe" \
        --python packages/bli-addon/spikes/open_spike.py

観測したいこと（BLI_OPEN_SPIKE_BEGIN/END マーカ間に出力して quit）:
  1) persistent=True タイマは open_mainfile を跨いで生存し ticking を続けるか。
  2) persistent=False タイマは open で解除される（ベースライン確認）か。
  3) `@persistent` load_post ハンドラは open で発火するか（再登録の足がかり）。
  4) 本番 addon を register した状態で open → Dispatcher pump タイマ / "bli-accept" サーバスレッドが生存するか。
  5) open 後に submit→pump の往復が成立するか（dispatch がまだ機能するかの end-to-end）。
  6) open_mainfile を **タイマ内から呼ぶ**（本番経路と同じ）こと自体が成功するか。
"""

import os
import sys
import tempfile
import threading
import time

import bpy  # type: ignore
from bpy.app.handlers import persistent  # type: ignore

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for pkg in ("bli-core", "bli-addon"):
    p = os.path.join(root, "packages", pkg, "src")
    if p not in sys.path:
        sys.path.insert(0, p)

# モジュールグローバルは file load から独立（bpy データではない）ため open を跨いで生存する。
S: dict = {
    "persistent_ticks": 0,
    "nonpersistent_ticks": 0,
    "load_post_fired": 0,
    "results": [],
    "phase": 0,
    "tmp_blend": None,
    "after_open": {},
    "submit_result": None,
    "submit_error": None,
    "submit_thread": None,
    "deadline": None,
}


def _record(name: str, ok: bool, detail: str = "") -> None:
    S["results"].append((name, ok, detail))


def _persistent_timer() -> float:
    S["persistent_ticks"] += 1
    return 0.05


def _nonpersistent_timer() -> float:
    S["nonpersistent_ticks"] += 1
    return 0.05


@persistent
def _load_post(*_args) -> None:
    S["load_post_fired"] += 1


def _server_alive() -> bool:
    """本番サーバの受信スレッド（server.py: name="bli-accept"）が生存しているか。"""
    return any(t.name == "bli-accept" and t.is_alive() for t in threading.enumerate())


def _dispatcher_timer_registered() -> bool:
    """本番 Dispatcher の pump タイマ（persistent）が登録されたままか。"""
    import bli_addon  # type: ignore

    d = getattr(bli_addon, "_dispatcher", None)
    if d is None or getattr(d, "_tick", None) is None:
        return False
    return bpy.app.timers.is_registered(d._tick)


def _setup() -> None:
    """ダミー timer / handler + 本番 addon を登録し、open する一時 .blend を用意する。"""
    bpy.app.timers.register(_persistent_timer, persistent=True)
    bpy.app.timers.register(_nonpersistent_timer, persistent=False)
    bpy.app.handlers.load_post.append(_load_post)

    import bli_addon  # type: ignore

    try:
        bli_addon.register()  # 本番の Dispatcher pump タイマ + TCP サーバを起動
        _record("addon register", True)
    except Exception as e:  # spike: register 失敗も観測
        _record("addon register", False, repr(e))

    # open 対象の一時 .blend を copy=True で書き出す（現セッションのファイルパスは変えない）。
    fd, tmp = tempfile.mkstemp(suffix=".blend")
    os.close(fd)
    try:
        bpy.ops.wm.save_as_mainfile(filepath=tmp, copy=True)
        _record("save temp .blend", True, tmp)
    except Exception as e:  # spike
        _record("save temp .blend", False, repr(e))
    S["tmp_blend"] = tmp


def _finish() -> None:
    print("=== BLI_OPEN_SPIKE_BEGIN ===")
    print("blender:", bpy.app.version_string, "background:", bpy.app.background)
    print("windows:", len(bpy.context.window_manager.windows))
    for name, ok, detail in S["results"]:
        print(f"  [{'OK' if ok else 'NG'}] {name} {detail}")
    print("=== BLI_OPEN_SPIKE_END ===")
    try:
        import bli_addon  # type: ignore

        bli_addon.unregister()
    except Exception:
        pass
    try:
        if S["tmp_blend"] and os.path.isfile(S["tmp_blend"]):
            os.remove(S["tmp_blend"])
    except Exception:
        pass
    bpy.ops.wm.quit_blender()


def _driver() -> float | None:
    # ウォッチドッグ: 何があっても期限超過で強制終了（GUI がハングして残らないように）。
    if S["deadline"] is not None and time.monotonic() > S["deadline"]:
        _record("watchdog: forced finish (timed out)", False)
        _finish()
        return None

    ph = S["phase"]
    if ph == 0:
        # open 前のベースライン（dummy / dispatcher / server が ticking・生存しているか）。
        _record(
            "pre: persistent timer ticking", S["persistent_ticks"] > 0, f"n={S['persistent_ticks']}"
        )
        _record("pre: server thread alive", _server_alive())
        _record("pre: dispatcher pump timer registered", _dispatcher_timer_registered())
        S["phase"] = 1
        return 0.3

    if ph == 1:
        # 本番経路と同じく **タイマコールバック内から** open_mainfile を呼ぶ。
        try:
            bpy.ops.wm.open_mainfile(filepath=S["tmp_blend"])
            _record("open_mainfile from timer FINISHED", True, S["tmp_blend"])
        except Exception as e:  # spike: タイマ内 open が拒否されるか観測
            _record("open_mainfile from timer FINISHED", False, repr(e))
        S["after_open"] = {
            "persistent_ticks": S["persistent_ticks"],
            "nonpersistent_ticks": S["nonpersistent_ticks"],
        }
        S["phase"] = 2
        return 1.0  # open 後にタイマを ticking させる猶予

    if ph == 2:
        persistent_advanced = S["persistent_ticks"] > S["after_open"]["persistent_ticks"]
        nonpersistent_frozen = S["nonpersistent_ticks"] == S["after_open"]["nonpersistent_ticks"]
        _record(
            "persistent timer survives open (keeps ticking)",
            persistent_advanced,
            f"{S['after_open']['persistent_ticks']} -> {S['persistent_ticks']}",
        )
        _record(
            "non-persistent timer dropped by open (frozen)",
            nonpersistent_frozen,
            f"{S['after_open']['nonpersistent_ticks']} -> {S['nonpersistent_ticks']}",
        )
        _record(
            "non-persistent timer unregistered after open",
            not bpy.app.timers.is_registered(_nonpersistent_timer),
        )
        _record(
            "load_post(persistent) fired on open",
            S["load_post_fired"] >= 1,
            f"count={S['load_post_fired']}",
        )
        _record("server thread alive after open", _server_alive())
        _record("dispatcher pump timer registered after open", _dispatcher_timer_registered())

        # end-to-end: open 後も submit→pump の往復が成立するか（pump タイマが生きていれば pong が返る）。
        # submit はメインスレッドを待つので別スレッドから呼ぶ（メインスレッドからだとデッドロック）。
        def _worker() -> None:
            import bli_addon  # type: ignore

            d = getattr(bli_addon, "_dispatcher", None)
            if d is None:
                S["submit_error"] = "no dispatcher"
                return
            try:
                S["submit_result"] = d.submit(lambda: "pong", timeout=3.0)
            except Exception as e:
                S["submit_error"] = repr(e)

        t = threading.Thread(target=_worker, name="spike-submit")
        t.start()
        S["submit_thread"] = t
        S["phase"] = 3
        return 0.5

    if ph == 3:
        t = S["submit_thread"]
        if t is not None and t.is_alive():
            return 0.2  # worker 完了待ち
        _record(
            "dispatch round-trip works after open (submit->pump)",
            S["submit_result"] == "pong",
            f"result={S['submit_result']!r} error={S['submit_error']!r}",
        )
        _finish()
        return None

    return None


def run() -> None:
    S["deadline"] = time.monotonic() + 25.0
    _setup()
    bpy.app.timers.register(_driver, first_interval=1.0, persistent=True)


if bpy.app.background:
    print(
        "[warn] --background では bpy.app.timers が発火しないため検証不能（GUI モードで実行してください）"
    )
    print("=== BLI_OPEN_SPIKE_BEGIN ===")
    print("  [NG] background mode: timer survival is untestable")
    print("=== BLI_OPEN_SPIKE_END ===")
else:
    bpy.app.timers.register(run, first_interval=1.0)
