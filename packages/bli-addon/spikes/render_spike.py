"""Spike VIII: render handler が常駐サーバ(GUI)で発火し busy フラグを制御できるか（M10 T10.2）。

本番モジュール `bli_addon.render_state` を **そのまま** 使って検証する（別コピーではない）。
NEXT-M10.md §3-1 の確認事項に答える:
  1. render_state.install() が登録する render_init/render_complete/render_cancel が GUI 常駐サーバで
     発火するか（--background では bpy.app.timers も handler も発火しないため GUI 必須）。
  2. render handler がどのスレッドで走るか（busy フラグの thread-safety 設計の根拠）。
  3. レンダ中に **別スレッド**（受信スレッド相当）から render_state.is_busy()=True を観測できるか
     ＝server が dispatch 前に BUSY_RENDERING を即拒否できることの裏付け。
  4. render_pre / render_post（フレーム単位）と render_init / render_complete（ジョブ単位）の発火。

注: @persistent handler が open_mainfile（bli open）を跨いで生存するかは **background smoke** で検証する
（GUI 内で timer から read_homefile/open を呼ぶと splash/再入で固まるため・本スパイクでは扱わない）。

実行（GUI モード・--background 不可）:
    "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe" \
        --python packages/bli-addon/spikes/render_spike.py
    "C:/Program Files/Blender Foundation/Blender 4.4/blender.exe" \
        --python packages/bli-addon/spikes/render_spike.py

モーダル(INVOKE)レンダで本番 F12 の脅威モデル（メインがイベントループに戻る間に受信スレッドが
dispatch しうる）を再現する。
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

from bli_addon import render_state  # noqa: E402

# レンダ中に呼ばれる handler のスレッド名を観察するためのフック（render_state とは別に併設）。
_HANDLER_LOG: list = []


def _probe_init(*_args) -> None:
    _HANDLER_LOG.append(("render_init", threading.current_thread().name))


def _probe_complete(*_args) -> None:
    _HANDLER_LOG.append(("render_complete", threading.current_thread().name))


def _probe_cancel(*_args) -> None:
    _HANDLER_LOG.append(("render_cancel", threading.current_thread().name))


def _probe_pre(*_args) -> None:
    _HANDLER_LOG.append(("render_pre", threading.current_thread().name))


def _probe_post(*_args) -> None:
    _HANDLER_LOG.append(("render_post", threading.current_thread().name))


_PROBES = [
    ("render_init", _probe_init),
    ("render_complete", _probe_complete),
    ("render_cancel", _probe_cancel),
    ("render_pre", _probe_pre),
    ("render_post", _probe_post),
]


def _install_probes() -> None:
    h = bpy.app.handlers
    for name, fn in _PROBES:
        getattr(h, name).append(fn)


def _remove_probes() -> None:
    h = bpy.app.handlers
    for name, fn in _PROBES:
        try:
            getattr(h, name).remove(fn)
        except ValueError:
            pass


STATE: dict = {
    "poll_observed_busy": False,
    "poll_samples": 0,
    "poll_stop": None,
    "poll_thread": None,
    "phase": 0,
    "render_done": False,
    "invoke_result": None,
    "deadline": None,
    "results": [],
}


def _record(name: str, ok: bool, detail: str = "") -> None:
    STATE["results"].append((name, ok, detail))


def _setup_render() -> None:
    scene = bpy.context.scene
    if scene.camera is None:
        cam_data = bpy.data.cameras.new("SpikeCam")
        cam_obj = bpy.data.objects.new("SpikeCam", cam_data)
        scene.collection.objects.link(cam_obj)
        scene.camera = cam_obj
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = 2560
    scene.render.resolution_y = 1440
    scene.render.resolution_percentage = 100
    scene.render.filepath = ""  # write_still=False


def _poll_busy() -> None:
    """受信スレッド相当: render_state.is_busy() を高頻度サンプリングして True を観測できるか。"""
    stop = STATE["poll_stop"]
    while not stop.is_set():
        STATE["poll_samples"] += 1
        if render_state.is_busy():
            STATE["poll_observed_busy"] = True
        if "render_complete" in [n for n, _ in _HANDLER_LOG]:
            STATE["render_done"] = True
        time.sleep(0.005)


def _setup() -> None:
    render_state.reset()
    render_state.install()  # 本番の handler 登録（@persistent）
    _install_probes()  # スレッド名観察用の併設フック
    _setup_render()
    _record("render_state.install()（@persistent handler 登録）", True)


def _finish() -> None:
    if STATE["poll_stop"] is not None:
        STATE["poll_stop"].set()
    print("=== BLI_RENDER_SPIKE_BEGIN ===")
    print("blender:", bpy.app.version_string, "background:", bpy.app.background)
    print("main_thread:", threading.main_thread().name)
    print("invoke_result:", STATE["invoke_result"])
    print("handler log:")
    for handler, tname in _HANDLER_LOG:
        print(f"    {handler} (thread={tname})")
    for name, ok, detail in STATE["results"]:
        print(f"  [{'OK' if ok else 'NG'}] {name} {detail}")
    print("=== BLI_RENDER_SPIKE_END ===")
    sys.stdout.flush()  # パイプ出力は block-buffered＝quit 前に必ず flush する
    _remove_probes()
    render_state.remove()
    bpy.ops.wm.quit_blender()


def _start_render() -> None:
    try:
        res = bpy.ops.render.render("INVOKE_DEFAULT", write_still=False)
        STATE["invoke_result"] = ",".join(res)
        if "RUNNING_MODAL" not in res:
            bpy.ops.render.render(write_still=False)  # フォールバック（ブロッキング）
        _record("render 実行", True, f"invoke={STATE['invoke_result']}")
    except Exception as e:
        _record("render 実行", False, repr(e))
        STATE["render_done"] = True


def _driver() -> float | None:
    if STATE["deadline"] is not None and time.monotonic() > STATE["deadline"]:
        _record("watchdog: forced finish (timed out)", False)
        _finish()
        return None

    ph = STATE["phase"]
    if ph == 0:
        STATE["poll_stop"] = threading.Event()
        t = threading.Thread(target=_poll_busy, name="spike-poll", daemon=True)
        t.start()
        STATE["poll_thread"] = t
        time.sleep(0.05)
        _start_render()
        STATE["phase"] = 1
        return 0.1

    if ph == 1:
        if not STATE["render_done"]:
            return 0.1
        STATE["poll_stop"].set()
        names = [n for n, _ in _HANDLER_LOG]
        init_threads = [tn for n, tn in _HANDLER_LOG if n == "render_init"]
        _record("render_init 発火", "render_init" in names)
        _record("render_complete 発火", "render_complete" in names)
        _record(
            "render handler のスレッド（busy thread-safety の根拠）",
            bool(init_threads),
            f"init_threads={init_threads} (main={threading.main_thread().name})",
        )
        _record("render 完了後 render_state.is_busy()=False", render_state.is_busy() is False)
        _record(
            "別スレッドがレンダ中 is_busy()=True を観測（dispatch 前拒否の裏付け）",
            STATE["poll_observed_busy"],
            f"poll_samples={STATE['poll_samples']}",
        )
        _record(
            "render_pre/render_post もジョブ内で発火",
            "render_pre" in names and "render_post" in names,
        )
        _finish()
        return None

    return None


def run() -> None:
    STATE["deadline"] = time.monotonic() + 45.0
    _setup()
    bpy.app.timers.register(_driver, first_interval=1.0, persistent=True)


if bpy.app.background:
    print(
        "[warn] --background では bpy.app.timers / render handler が発火しないため検証不能（GUI モード必須）"
    )
    print("=== BLI_RENDER_SPIKE_BEGIN ===")
    print("  [NG] background mode: untestable")
    print("=== BLI_RENDER_SPIKE_END ===")
else:
    bpy.app.timers.register(run, first_interval=1.0)
