"""BpyGateway 状態キャプチャ: viewport/screen/render（gateway/ 分割 P2-4）。

元 gateway.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

from typing import Any

import bpy  # type: ignore

from bli_core.errors import ErrorCategory, ErrorCode

from .core import _op_error, run_operator

# ---- 状態キャプチャ（実地フィードバック #1 / Spike V で両版確認）----
#
# viewport = gpu offscreen draw_view3d（UI なし・解像度指定可）/ screen = screenshot_area で
# ビューポート領域そのまま / render = カメラからレンダ。screen/viewport は GUI 必須（--background
# では window/area が無く E_PRECONDITION）。生 bpy.ops は run_operator 経由（AST guard）。


def _find_view3d() -> tuple[Any, Any, Any, Any]:
    """最初の VIEW_3D エリアの (window, area, region(WINDOW), space) を返す（無ければ全 None）。"""
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                return win, area, region, area.spaces.active
    return None, None, None, None


def capture_viewport(path: str, width: int, height: int) -> dict[str, Any]:
    """ビューポート相当を gpu offscreen で描画し PNG 保存（UI なし・解像度指定可）。"""
    if bpy.app.background:
        raise _op_error(
            ErrorCode.E_PRECONDITION, "viewport キャプチャには GUI が必要です（--background 不可）"
        )
    _win, area, region, space = _find_view3d()
    if area is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            "3Dビューポートが見つかりません（GUI に VIEW_3D を開いてください）",
        )

    import gpu  # type: ignore  # lazy: bpy 依存（GUI GPU コンテキスト）
    import numpy as np  # type: ignore  # lazy: Blender 同梱（§E4）

    rv3d = space.region_3d
    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        with offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.05, 0.05, 0.05, 1.0))
            offscreen.draw_view3d(
                bpy.context.scene,
                bpy.context.view_layer,
                space,
                region,
                rv3d.view_matrix,
                rv3d.window_matrix,
                do_color_management=True,
            )
            buffer = fb.read_color(0, 0, width, height, 4, 0, "UBYTE")
    finally:
        offscreen.free()
    buffer.dimensions = width * height * 4
    arr = np.asarray(buffer, dtype=np.float32) / 255.0
    img = bpy.data.images.new("bli_capture_tmp", width, height, alpha=True)
    try:
        img.pixels.foreach_set(arr.ravel())
        img.filepath_raw = path
        img.file_format = "PNG"
        try:
            img.save()
        except RuntimeError as e:  # 保存失敗は INTERNAL でなく業務エラーへ
            raise _op_error(ErrorCode.E_OPERATOR, f"画像の保存に失敗しました: {e}") from e
    finally:
        bpy.data.images.remove(img)  # 一時 datablock を残さない（例外時も）
    return {"width": width, "height": height}


def capture_screen(path: str) -> dict[str, Any]:
    """ビューポート領域そのまま（シェーディング/ギズモ込み）を screenshot_area で PNG 保存。"""
    if bpy.app.background:
        raise _op_error(
            ErrorCode.E_PRECONDITION, "screen キャプチャには GUI が必要です（--background 不可）"
        )
    win, area, region, _space = _find_view3d()
    if area is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            "3Dビューポートが見つかりません（GUI に VIEW_3D を開いてください）",
        )
    run_operator(
        bpy.ops.screen.screenshot_area,
        extra_override={"window": win, "area": area, "region": region},
        filepath=path,
    )
    return {"width": area.width, "height": area.height}


def capture_render(
    path: str, width: int, height: int, camera_name: str | None = None
) -> dict[str, Any]:
    """シーンカメラからレンダして PNG 保存（render 設定は save/restore で非破壊）。"""
    scene = bpy.context.scene
    if camera_name is not None:
        cam = bpy.data.objects.get(camera_name)
        if cam is None or cam.type != "CAMERA":
            raise _op_error(
                ErrorCode.E_TARGET_NOT_FOUND,
                f"カメラが見つかりません: {camera_name}",
                category=ErrorCategory.USER_INPUT,
            )
    else:
        cam = scene.camera
    if cam is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            "render にはカメラが必要です（--camera 指定、またはシーンに active camera を設定）",
        )
    r = scene.render
    saved = (
        r.filepath,
        r.image_settings.file_format,
        r.resolution_x,
        r.resolution_y,
        r.resolution_percentage,
        scene.camera,
    )
    try:
        scene.camera = cam
        r.filepath = path
        r.image_settings.file_format = "PNG"
        r.resolution_x = width
        r.resolution_y = height
        r.resolution_percentage = 100
        run_operator(bpy.ops.render.render, write_still=True)
    finally:
        (
            r.filepath,
            r.image_settings.file_format,
            r.resolution_x,
            r.resolution_y,
            r.resolution_percentage,
            scene.camera,
        ) = saved
    return {"width": width, "height": height, "camera": cam.name}
