"""Spike V: 常駐 GUI Blender で「現在の状態をキャプチャ」する手段の可否調査（実地FB #1）。

`--background` では GUI 描画/screenshot ができない。**GUI モード**で実行する:
    "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe" \
        --python packages/bli-addon/spikes/capture_spike.py

UI 構築後にタイマで各手法を試し、結果を BLI_CAPTURE_SPIKE_BEGIN/END マーカ間に出して quit する。
実際のデプロイは「常駐 GUI Blender + アドオン（メインスレッドの dispatch）」なので、ここで通る
手法が capture コマンドの実装候補になる。候補:
  1) screen.screenshot       … Blender ウィンドウ全体（GUI 依存・最も「見たまま」）
  2) screen.screenshot_area  … 指定エリア（VIEW_3D ビューポートのみ）
  3) gpu offscreen draw_view3d … ウィンドウ非依存でビューポート相当を offscreen 描画
  4) render.render(write_still) … カメラからのレンダ（--background でも可・低速・見た目はレンダ）
"""

import os
import tempfile

import bpy  # type: ignore

OUT = os.path.join(tempfile.gettempdir(), "bli_capture_spike")
os.makedirs(OUT, exist_ok=True)

results: list[tuple[str, bool, int, str]] = []


def _png_info(path: str) -> tuple[bool, int]:
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return True, os.path.getsize(path)
    return False, 0


def _record(name: str, path: str, err: str = "") -> None:
    ok, size = _png_info(path) if not err else (False, 0)
    results.append((name, ok, size, err))


def _find_area(area_type: str = "VIEW_3D"):
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == area_type:
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                return win, area, region
    return None, None, None


def try_window_screenshot() -> None:
    path = os.path.join(OUT, "1_window_screenshot.png")
    try:
        bpy.ops.screen.screenshot(filepath=path)  # 全ウィンドウ
        _record("screen.screenshot (full window)", path)
    except Exception as e:  # spike: 何が失敗するか観測する
        _record("screen.screenshot (full window)", path, repr(e))


def try_area_screenshot() -> None:
    path = os.path.join(OUT, "2_area_screenshot.png")
    win, area, region = _find_area("VIEW_3D")
    if area is None:
        _record("screen.screenshot_area (VIEW_3D)", path, "VIEW_3D area not found")
        return
    try:
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.ops.screen.screenshot_area(filepath=path)
        _record("screen.screenshot_area (VIEW_3D)", path)
    except Exception as e:  # spike: 何が失敗するか観測する
        _record("screen.screenshot_area (VIEW_3D)", path, repr(e))


def try_offscreen() -> None:
    path = os.path.join(OUT, "3_offscreen.png")
    _win, area, region = _find_area("VIEW_3D")
    if area is None:
        _record("gpu offscreen draw_view3d", path, "VIEW_3D area not found")
        return
    try:
        import gpu  # type: ignore

        space = area.spaces.active
        rv3d = space.region_3d
        w, h = 320, 240
        offscreen = gpu.types.GPUOffScreen(w, h)
        with offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.1, 0.1, 0.1, 1.0))
            offscreen.draw_view3d(
                bpy.context.scene,
                bpy.context.view_layer,
                space,
                region,
                rv3d.view_matrix,
                rv3d.window_matrix,
                do_color_management=True,
            )
            buffer = fb.read_color(0, 0, w, h, 4, 0, "UBYTE")
        offscreen.free()
        # Buffer -> bpy image -> PNG（pixels は 0..1 float / 上下反転に注意）
        buffer.dimensions = w * h * 4
        img = bpy.data.images.new("bli_spike_offscreen", w, h)
        img.pixels = [v / 255.0 for v in buffer]
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        _record("gpu offscreen draw_view3d", path)
    except Exception as e:  # spike: 何が失敗するか観測する
        _record("gpu offscreen draw_view3d", path, repr(e))


def try_render() -> None:
    path = os.path.join(OUT, "4_render.png")
    try:
        scene = bpy.context.scene
        if scene.camera is None:
            cam_data = bpy.data.cameras.new("BLISpikeCam")
            cam = bpy.data.objects.new("BLISpikeCam", cam_data)
            scene.collection.objects.link(cam)
            cam.location = (6.0, -6.0, 4.0)
            cam.rotation_euler = (1.1, 0.0, 0.785)
            scene.camera = cam
        scene.render.filepath = path
        scene.render.image_settings.file_format = "PNG"
        scene.render.resolution_x = 320
        scene.render.resolution_y = 240
        bpy.ops.render.render(write_still=True)
        _record("render.render camera (write_still)", path)
    except Exception as e:  # spike: 何が失敗するか観測する
        _record("render.render camera (write_still)", path, repr(e))


def try_gateway() -> None:
    """本番コード（addon gateway）の capture_* を GUI で直接検証（numpy save/temp_override/save-restore）。

    背景 smoke（--background）では viewport/screen が動かないため、production パスの GUI 検証はここで行う。
    """
    import sys

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    for pkg in ("bli-core", "bli-addon"):
        p = os.path.join(root, "packages", pkg, "src")
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        from bli_addon import gateway  # type: ignore
    except Exception as e:  # spike: import 可否も観測
        for src in ("viewport", "screen", "render"):
            results.append((f"gateway.capture_{src}", False, 0, f"import失敗: {e!r}"))
        return
    cases = (
        (
            "gateway.capture_viewport",
            "gw_viewport.png",
            lambda p: gateway.capture_viewport(p, 320, 240),
        ),
        ("gateway.capture_screen", "gw_screen.png", lambda p: gateway.capture_screen(p)),
        (
            "gateway.capture_render",
            "gw_render.png",
            lambda p: gateway.capture_render(p, 320, 240, None),
        ),
    )
    for name, fname, fn in cases:
        path = os.path.join(OUT, fname)
        try:
            fn(path)
            _record(name, path)
        except Exception as e:  # spike: 何が失敗するか観測
            _record(name, path, repr(e))


def try_command() -> None:
    """本番ハンドラ ops.dispatch("capture") を GUI で叩き、offload_file + PNG 実寸抽出まで通す。

    gateway 単体テスト（try_gateway）に対し、ops._capture の成功パス全体（退避・content-address・
    PNG 解像度抽出・descriptor 組成）を production 経路で検証する。info は _capture で未使用のため None。
    """
    import sys

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    for pkg in ("bli-core", "bli-addon"):
        p = os.path.join(root, "packages", pkg, "src")
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        from bli_addon import ops  # type: ignore
    except Exception as e:  # spike: import 可否も観測
        results.append(("ops.dispatch capture", False, 0, f"import失敗: {e!r}"))
        return
    cases = (
        ("viewport", {"source": "viewport", "width": 320, "height": 240}),
        ("render", {"source": "render"}),
    )
    for src, params in cases:
        try:
            resp = ops.dispatch("capture", params, None)
            d = resp["data"]
            ok = bool(d.get("path")) and os.path.exists(d["path"]) and d.get("size", 0) > 0
            results.append((f"ops.dispatch capture {src}", ok, d.get("size", 0), ""))
            print(
                f"  [cmd:{src}] {d.get('width')}x{d.get('height')} "
                f"sha={str(d.get('sha256'))[:8]} path={d.get('path')}"
            )
        except Exception as e:  # spike: 何が失敗するか観測
            results.append((f"ops.dispatch capture {src}", False, 0, repr(e)))


def run() -> None:
    print("=== BLI_CAPTURE_SPIKE_BEGIN ===")
    print("blender:", bpy.app.version_string, "background:", bpy.app.background)
    print("windows:", len(bpy.context.window_manager.windows))
    try_window_screenshot()
    try_area_screenshot()
    try_offscreen()
    try_render()
    try_gateway()
    try_command()
    for name, ok, size, err in results:
        print(f"  [{'OK' if ok else 'NG'}] {name} size={size} {err}")
    print("OUT:", OUT)
    print("=== BLI_CAPTURE_SPIKE_END ===")
    bpy.ops.wm.quit_blender()


if bpy.app.background:
    # --background では screenshot/offscreen は不可だが、render だけは観測できる。
    print("[warn] --background で実行中: screenshot/offscreen は失敗する想定（GUI モード推奨）")
    run()
else:
    # GUI: UI 構築後に実行（startup 直後は window/area 未確定のことがある）。
    bpy.app.timers.register(run, first_interval=1.5)
