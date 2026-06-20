"""Spike IX: メインスレッド応答性ウォッチドッグが常駐サーバ(GUI)で機能するか（M10 T10.3）。

本番モジュール `bli_addon.watchdog` と `bli_addon.dispatcher` を **そのまま** 使って検証する。
NEXT-M10.md §3-2 / §4 T10.3 の確認事項に答える:
  1. GUI 常駐で pump タイマ（dispatcher.install_timer(on_tick=watchdog.mark_alive)）が発火し、
     生存印 last_pump_ts を更新するか＝アイドル時は responsive を維持する
     （--background では bpy.app.timers が発火しないため GUI 必須）。
  2. **重量 op（メインスレッド占有）中に pump タイマが止まり**、last_pump_ts が進まなくなるか。
     実 heavy op（decimate）でメインが実際に固まる時間を測り、閾値（既定 60s）の妥当性を確認する。
  3. メイン占有中に **別スレッド（受信スレッド相当）** が watchdog.snapshot() で unresponsive を
     観測できるか＝request-status がレンダ/重量中も「固まっている」を返せることの裏付け。
  4. 占有解除後に pump タイマが再開し responsive へ回復するか。

注（GIL）: 本スパイクの「占有」は time.sleep（メインスレッド・GIL 解放）で再現する。bpy.app.timers が
発火するかは **メインがイベントループへ戻るか** だけで決まり、sleep でも native op でも戻らない＝
heartbeat の凍結は同条件で再現できる。実 native op が GIL を握り続ける場合に別スレッド観測が遅れる点は
research §E13 に注記する（観測自体は占有解除後にも成立）。

実行（GUI モード・--background 不可）:
    "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe" \
        --python packages/bli-addon/spikes/watchdog_spike.py
    "C:/Program Files/Blender Foundation/Blender 4.4/blender.exe" \
        --python packages/bli-addon/spikes/watchdog_spike.py
"""

import os
import sys
import threading
import time

import bpy  # type: ignore

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for pkg in ("bli-core", "bli-addon"):
    p = os.path.join(root, "packages", pkg, "src")
    if p not in sys.path:
        sys.path.insert(0, p)

from bli_addon import watchdog  # noqa: E402
from bli_addon.dispatcher import Dispatcher  # noqa: E402

# スパイクは短い閾値で速く回す（本番は runtime.WATCHDOG_UNRESPONSIVE_THRESHOLD=60s）。
THRESHOLD = 1.5
POLL = 0.1
BLOCK_SECS = 4.0  # 閾値を十分超える占有時間

_dispatcher = Dispatcher()

STATE = {
    "phase": 0,
    "results": [],
    "deadline": None,
    "poll_stop": None,
    "idle_age": None,
    "real_op_secs": None,
    "real_op_frozen_age": None,
    "block_frozen": False,
    "observed_unresponsive": False,
    "observed_responsive_after": False,
    "samples": 0,
}


def _record(name, ok, detail=""):
    STATE["results"].append((name, ok, detail))


def _poll():
    """別スレッド（受信スレッド相当）: watchdog.snapshot() を高頻度サンプリングして観測する。"""
    stop = STATE["poll_stop"]
    while not stop.is_set():
        STATE["samples"] += 1
        snap = watchdog.snapshot()
        if snap["responsive"] is False:
            STATE["observed_unresponsive"] = True
        elif STATE["observed_unresponsive"]:
            STATE["observed_responsive_after"] = True
        time.sleep(0.05)


def _make_heavy_mesh():
    """decimate が tick 間隔（20ms）を十分超える時間かかる程度の高ポリ ico を作って返す。"""
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=7)  # ~1.3M 面（decimate apply が数百ms〜）
    obj = bpy.context.active_object
    m = obj.modifiers.new("Dec", "DECIMATE")
    m.ratio = 0.05
    return obj, m


def _run_real_heavy_op():
    """実 heavy op（decimate apply）でメインスレッドが固まる時間を測る（閾値妥当性の根拠）。"""
    try:
        obj, m = _make_heavy_mesh()
        before_ts = watchdog._last_pump_ts  # op 開始前の最後の生存印
        t0 = time.time()
        # apply は modifier_apply（メインスレッド・native）。temp_override で対象を確定。
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier=m.name)
        dur = time.time() - t0
        # op 直後（次の tick がまだ発火していない）に生存印を読む。op 中に tick が一度も発火していなければ
        # last_pump_ts は before_ts のまま＝メインがイベントループへ戻らず timer が止まったことの直接証拠。
        STATE["real_op_secs"] = dur
        STATE["real_op_frozen_age"] = time.time() - watchdog._last_pump_ts
        STATE["block_frozen"] = watchdog._last_pump_ts == before_ts
    except Exception as e:
        _record("実 heavy op（decimate apply）", False, repr(e))


def _driver():
    if STATE["deadline"] is not None and time.monotonic() > STATE["deadline"]:
        _record("watchdog: forced finish (timed out)", False)
        _finish()
        return None

    ph = STATE["phase"]
    if ph == 0:
        # 別スレッド観測を開始し、アイドル状態で pump タイマが heartbeat を進めていることを確認。
        STATE["poll_stop"] = threading.Event()
        t = threading.Thread(target=_poll, name="spike-poll", daemon=True)
        t.start()
        STATE["poll_thread"] = t
        snap = watchdog.snapshot()
        STATE["idle_age"] = snap["last_pump_age"]
        _record(
            "アイドル時 pump タイマが heartbeat を更新（GUI で timer 発火）",
            snap["responsive"] and snap["last_pump_age"] is not None,
            f"last_pump_age={snap['last_pump_age']}",
        )
        STATE["phase"] = 1
        return 0.2

    if ph == 1:
        # 実 heavy op（decimate）でメインを固める＝real-op duration と heartbeat 凍結を計測。
        _run_real_heavy_op()
        STATE["phase"] = 2
        return 0.2

    if ph == 2:
        # 制御された占有（sleep > 閾値）でメインを固め、別スレッドが unresponsive を観測できるか。
        # この sleep 中は _driver も dispatcher._tick も発火しない（メイン占有）＝heartbeat 凍結。
        time.sleep(BLOCK_SECS)
        STATE["phase"] = 3
        return 0.2

    if ph == 3:
        # 占有解除後＝tick が再開し responsive へ回復するはず。少し待ってから判定する。
        snap = watchdog.snapshot()
        _record(
            "実 heavy op 中に pump タイマが停止（heartbeat 凍結）",
            STATE["block_frozen"],
            f"real_op_secs={STATE['real_op_secs']} frozen_age={STATE['real_op_frozen_age']}",
        )
        _record(
            "占有中に別スレッドが unresponsive を観測（request-status 観測性の裏付け）",
            STATE["observed_unresponsive"],
            f"samples={STATE['samples']} threshold={THRESHOLD}s block={BLOCK_SECS}s",
        )
        _record(
            "占有解除後 pump タイマ再開で responsive へ回復",
            snap["responsive"],
            f"last_pump_age={snap['last_pump_age']} "
            f"observed_responsive_after={STATE['observed_responsive_after']}",
        )
        _finish()
        return None

    return None


def _setup():
    watchdog.reset()
    watchdog.start(threshold=THRESHOLD, poll_interval=POLL)
    _dispatcher.install_timer(on_tick=watchdog.mark_alive)  # 本番 register と同じ配線
    _record("watchdog.start + install_timer(on_tick=mark_alive)", True)


def _finish():
    if STATE["poll_stop"] is not None:
        STATE["poll_stop"].set()
    print("=== BLI_WATCHDOG_SPIKE_BEGIN ===")
    print("blender:", bpy.app.version_string, "background:", bpy.app.background)
    print("main_thread:", threading.main_thread().name)
    print(f"params: threshold={THRESHOLD}s poll={POLL}s block={BLOCK_SECS}s (prod threshold=60s)")
    all_ok = True
    for name, ok, detail in STATE["results"]:
        all_ok = all_ok and ok
        print(f"  [{'OK' if ok else 'NG'}] {name} {detail}")
    print("RESULT:", "WATCHDOG SPIKE OK" if all_ok else "WATCHDOG SPIKE FAIL")
    print("=== BLI_WATCHDOG_SPIKE_END ===")
    sys.stdout.flush()  # パイプ出力は block-buffered＝quit 前に必ず flush する
    _dispatcher.remove_timer()
    watchdog.stop()
    bpy.ops.wm.quit_blender()


def run():
    STATE["deadline"] = time.monotonic() + 60.0
    _setup()
    bpy.app.timers.register(_driver, first_interval=1.0, persistent=True)


if bpy.app.background:
    print("[warn] --background では bpy.app.timers が発火しないため検証不能（GUI モード必須）")
    print("=== BLI_WATCHDOG_SPIKE_BEGIN ===")
    print("  [NG] background mode: untestable")
    print("=== BLI_WATCHDOG_SPIKE_END ===")
else:
    bpy.app.timers.register(run, first_interval=1.0)
