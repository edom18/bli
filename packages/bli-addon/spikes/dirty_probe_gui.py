"""GUI モードで bpy.data.is_dirty の遷移を確認する（open の未保存ガード設計用・使い捨て）。

background では is_dirty が常時 True で信頼できない（実測）。本番は常駐 GUI なので GUI 実機で
save/open 後に is_dirty が False に戻るか（=未保存ガードの判定材料に使えるか）を確かめる。
    "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe" \
        --python packages/bli-addon/spikes/dirty_probe_gui.py
"""

import os
import tempfile

import bpy  # type: ignore

R: list[str] = []


def _s(label: str) -> None:
    R.append(f"{label}: is_dirty={bpy.data.is_dirty} is_saved={bpy.data.is_saved}")


def run() -> None:
    # 注: open_mainfile を同一コールバック内で呼ぶと後続が走らない（GUI 実測）ため open は含めない。
    # is_dirty が「未保存ガードの判定材料」になるか、各種編集経路でどう変化するか観測する。
    _s("fresh")
    # (1) 直接 RNA 書き込みのみ（transform/set-origin-world が使う経路）
    bpy.data.objects[0].location.x += 1.0
    bpy.context.view_layer.update()
    _s("after direct write")
    # (2) 直接書き込み + ed.undo_push（gateway.push_undo がやること）
    bpy.data.objects[0].location.x += 1.0
    bpy.ops.ed.undo_push(message="probe")
    _s("after direct write + undo_push")
    # (3) 実 operator（origin_set 相当の本物の bpy.ops）
    bpy.ops.mesh.primitive_cube_add()
    _s("after real operator (primitive_add)")
    # (4) 保存で False に戻るか
    fd, tmp = tempfile.mkstemp(suffix=".blend")
    os.close(fd)
    bpy.ops.wm.save_as_mainfile(filepath=tmp)
    _s("after save")
    # (5) 保存後に operator 編集 → 再び True か
    bpy.ops.mesh.primitive_cube_add()
    _s("after operator post-save")
    try:
        os.remove(tmp)
    except OSError:
        pass
    print("=== BLI_DIRTY_PROBE_BEGIN ===")
    print("blender:", bpy.app.version_string, "background:", bpy.app.background)
    for line in R:
        print("  " + line)
    print("=== BLI_DIRTY_PROBE_END ===")
    bpy.ops.wm.quit_blender()


if bpy.app.background:
    run()
else:
    bpy.app.timers.register(run, first_interval=1.0)
