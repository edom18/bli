"""Spike VII-b: 1つの dispatch ジョブ内で open_mainfile + 結果構築 + return が成立するか（M9 T9.4・核心）。

open_spike は「open 後に *別ジョブ* が pump される」ことを確認した。だが本番の `_open` は
**1つのジョブ**が `open_mainfile` を呼び、その後に scene_summary 等で **結果を構築して return** する
（server スレッドはこの戻り値を待つ）。`open_mainfile` がコールバック残りの実行を中断すると、
ジョブの戻り値が server に返らずタイムアウトする恐れがある（GUI で open を非永続タイマ内から呼ぶと
後続の print/quit が走らなかった実測あり）。本スパイクで「open を含むジョブが値を返す」ことを実機確定する。

    "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe" \
        --python packages/bli-addon/spikes/open_job_spike.py

経路は本番と同一: worker スレッド → Dispatcher.submit(job) → メインの pump（_tick タイマ内）が
job を実行（open_mainfile → 結果 dict を return）。worker がその dict を受け取れれば OK。
"""

import os
import sys
import tempfile
import threading
import time

import bpy  # type: ignore

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for pkg in ("bli-core", "bli-addon"):
    p = os.path.join(root, "packages", pkg, "src")
    if p not in sys.path:
        sys.path.insert(0, p)

S: dict = {
    "phase": 0,
    "results": [],
    "tmp": None,
    "job_result": None,
    "job_error": None,
    "job_thread": None,
    "pong_result": None,
    "pong_error": None,
    "pong_thread": None,
    "deadline": None,
}


def _record(name: str, ok: bool, detail: str = "") -> None:
    S["results"].append((name, ok, detail))


def _dispatcher():
    import bli_addon  # type: ignore

    return getattr(bli_addon, "_dispatcher", None)


def _setup() -> None:
    import bli_addon  # type: ignore

    try:
        bli_addon.register()
        _record("addon register", True)
    except Exception as e:  # spike
        _record("addon register", False, repr(e))
    fd, tmp = tempfile.mkstemp(suffix=".blend")
    os.close(fd)
    # 区別がつくよう、open 対象 .blend には追加オブジェクトを入れて保存する（copy=True で現状非変更）。
    bpy.ops.mesh.primitive_uv_sphere_add()
    bpy.ops.wm.save_as_mainfile(filepath=tmp, copy=True)
    S["tmp"] = tmp


def _worker_open() -> None:
    """本番 _open と同型: open_mainfile → 結果 dict を return するジョブを submit する。"""
    d = _dispatcher()
    if d is None:
        S["job_error"] = "no dispatcher"
        return

    def job():
        bpy.ops.wm.open_mainfile(filepath=S["tmp"])
        # open 後に結果を構築（本番は scene_summary / scene_state_fingerprint 等）。
        return {
            "filepath": bpy.data.filepath,
            "objects": len(bpy.data.objects),
            "marker": "RESULT_AFTER_OPEN",
        }

    try:
        S["job_result"] = d.submit(job, timeout=8.0)
    except Exception as e:
        S["job_error"] = repr(e)


def _worker_pong() -> None:
    """open ジョブの後、pump がまだ生きていて後続ジョブを処理できるか。"""
    d = _dispatcher()
    if d is None:
        S["pong_error"] = "no dispatcher"
        return
    try:
        S["pong_result"] = d.submit(lambda: "pong", timeout=4.0)
    except Exception as e:
        S["pong_error"] = repr(e)


def _finish() -> None:
    print("=== BLI_OPEN_JOB_SPIKE_BEGIN ===")
    print("blender:", bpy.app.version_string, "background:", bpy.app.background)
    for name, ok, detail in S["results"]:
        print(f"  [{'OK' if ok else 'NG'}] {name} {detail}")
    print("=== BLI_OPEN_JOB_SPIKE_END ===")
    try:
        import bli_addon  # type: ignore

        bli_addon.unregister()
    except Exception:
        pass
    try:
        if S["tmp"] and os.path.isfile(S["tmp"]):
            os.remove(S["tmp"])
    except Exception:
        pass
    bpy.ops.wm.quit_blender()


def _driver() -> float | None:
    if S["deadline"] is not None and time.monotonic() > S["deadline"]:
        _record("watchdog: forced finish (timed out)", False)
        _finish()
        return None

    ph = S["phase"]
    if ph == 0:
        t = threading.Thread(target=_worker_open, name="spike-open-job")
        t.start()
        S["job_thread"] = t
        S["phase"] = 1
        return 0.2

    if ph == 1:
        if S["job_thread"] is not None and S["job_thread"].is_alive():
            return 0.2  # open ジョブ完了待ち
        res = S["job_result"]
        ok = isinstance(res, dict) and res.get("marker") == "RESULT_AFTER_OPEN"
        _record(
            "dispatched job: open_mainfile + build result + return",
            ok,
            f"result={res!r} error={S['job_error']!r}",
        )
        # 期待: 開いた .blend を指す filepath / sphere を含む objects 数。
        if isinstance(res, dict):
            _record(
                "  opened filepath points to temp .blend",
                res.get("filepath") == S["tmp"],
                str(res.get("filepath")),
            )
            _record(
                "  objects loaded from opened file",
                (res.get("objects") or 0) >= 1,
                f"n={res.get('objects')}",
            )
        t = threading.Thread(target=_worker_pong, name="spike-pong")
        t.start()
        S["pong_thread"] = t
        S["phase"] = 2
        return 0.2

    if ph == 2:
        if S["pong_thread"] is not None and S["pong_thread"].is_alive():
            return 0.2
        _record(
            "pump still processes jobs after an open-job",
            S["pong_result"] == "pong",
            f"result={S['pong_result']!r} error={S['pong_error']!r}",
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
    print("=== BLI_OPEN_JOB_SPIKE_BEGIN ===")
    print("  [NG] background mode: untestable")
    print("=== BLI_OPEN_JOB_SPIKE_END ===")
else:
    bpy.app.timers.register(run, first_interval=1.0)
