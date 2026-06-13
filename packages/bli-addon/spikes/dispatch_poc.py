"""ディスパッチ安定性 PoC（T0.5.3 / research.md 論点1）。

別スレッドが queue にリクエストを積み、メインスレッドが drain して bpy を読む。
受信スレッドは threading.Event.wait(timeout) で待つ。GIL 競合/ハングを観測する。

注: --background では bpy.app.timers が tick しないため、本 PoC は
「メインスレッドが drain ループを回す」形で timer の代わりを手動再現する。
実際の bpy.app.timers 発火（GUI）は L4 手動スモークで別途確認する。
"""

import queue
import threading
import time

import bpy  # type: ignore

N = 500
TIMEOUT = 5.0
q: "queue.Queue" = queue.Queue()
results = {"done": 0, "timeouts": 0, "max_latency": 0.0, "errors": 0}
stop = threading.Event()


class Slot:
    __slots__ = ("event", "result")

    def __init__(self):
        self.event = threading.Event()
        self.result = None


def drain_once():
    """メインスレッドで queue を処理（timer 相当）。bpy を読む。"""
    while True:
        try:
            slot, payload = q.get_nowait()
        except queue.Empty:
            return
        try:
            # メインスレッドで bpy にアクセス（GIL 解放/再取得の競合を誘発）
            n_obj = len(bpy.data.objects)
            slot.result = {"echo": payload, "n_objects": n_obj}
        except Exception as e:
            slot.result = {"error": str(e)}
            results["errors"] += 1
        finally:
            slot.event.set()


def producer():
    for i in range(N):
        slot = Slot()
        t0 = time.perf_counter()
        q.put((slot, i))
        if slot.event.wait(TIMEOUT):
            lat = time.perf_counter() - t0
            results["max_latency"] = max(results["max_latency"], lat)
            results["done"] += 1
        else:
            results["timeouts"] += 1
    stop.set()


def main():
    t = threading.Thread(target=producer, daemon=True)
    start = time.perf_counter()
    t.start()
    # メインスレッドの drain ループ（timer 相当、固定間隔）
    while not stop.is_set():
        drain_once()
        time.sleep(0.005)
    drain_once()
    elapsed = time.perf_counter() - start
    t.join(timeout=5)
    print("=== BLI_DISPATCH_POC_BEGIN ===")
    print(
        f"version={bpy.app.version_string} N={N} done={results['done']} "
        f"timeouts={results['timeouts']} errors={results['errors']} "
        f"max_latency_ms={results['max_latency'] * 1000:.2f} elapsed_s={elapsed:.2f}"
    )
    verdict = "STABLE" if results["timeouts"] == 0 and results["errors"] == 0 else "UNSTABLE"
    print(f"verdict={verdict}")
    print("=== BLI_DISPATCH_POC_END ===")


if __name__ == "__main__":
    main()
