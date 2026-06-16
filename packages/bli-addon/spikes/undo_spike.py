"""Spike VI: 常駐 GUI Blender で undo/redo の可否・必要 context を調査（実地FB #3）。

`bpy.ops.ed.undo()` は GUI 前提で `--background` では不定（M0.5）。本番デプロイは
「常駐 GUI Blender + アドオン（メインスレッドの dispatch）」なので、ここで通る手法が
`bli undo`/`bli redo` の実装候補になる。**GUI モード**で実行する:
    "C:/Program Files/Blender Foundation/Blender 5.0/blender.exe" \
        --python packages/bli-addon/spikes/undo_spike.py

観測したいこと:
  1) production の dispatch（transform で直接プロパティ + ed.undo_push）後に ed.undo() が
     実際に状態を巻き戻すか（gateway は operator でなく直接代入 + push_undo を使う）。
  2) ed.undo() は bare 呼び出しで効くか、temp_override(window/screen/area/region) が要るか。
  3) ed.redo() で再適用されるか。
  4) --steps 相当（複数回 undo）で N 段戻るか。
結果は BLI_UNDO_SPIKE_BEGIN/END マーカ間に出して quit する。
"""

import os
import sys

import bpy  # type: ignore

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for pkg in ("bli-core", "bli-addon"):
    p = os.path.join(root, "packages", pkg, "src")
    if p not in sys.path:
        sys.path.insert(0, p)

results: list[tuple[str, bool, str]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def _find_window_area(area_type: str = "VIEW_3D"):
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == area_type:
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                return win, area, region
    return None, None, None


def _cube_x() -> float:
    return round(float(bpy.data.objects["Cube"].location.x), 4)


def _ensure_cube_origin() -> None:
    cube = bpy.data.objects.get("Cube")
    if cube is None:
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        bpy.context.active_object.name = "Cube"
        cube = bpy.data.objects["Cube"]
    cube.location = (0.0, 0.0, 0.0)
    bpy.ops.ed.undo_push(message="spike baseline")


def _undo_bare(n: int = 1) -> None:
    for _ in range(n):
        bpy.ops.ed.undo()


def _redo_bare(n: int = 1) -> None:
    for _ in range(n):
        bpy.ops.ed.redo()


def _undo_override(n: int = 1) -> None:
    win, area, region = _find_window_area()
    override = {"window": win, "screen": win.screen, "area": area, "region": region}
    with bpy.context.temp_override(**override):
        for _ in range(n):
            bpy.ops.ed.undo()


def try_dispatch_then_undo() -> None:
    """production dispatch（transform 直接代入 + push_undo）→ ed.undo() で巻き戻るか。"""
    try:
        from bli_addon import ops  # type: ignore
    except Exception as e:  # spike: import 可否も観測
        _record("import ops", False, repr(e))
        return
    _ensure_cube_origin()
    base = _cube_x()
    ops.dispatch("transform", {"targets": "Cube", "location": [5.0, 0.0, 0.0]}, None)
    moved = _cube_x()
    _record("dispatch transform moved", moved == 5.0, f"x={moved}")

    # (A) bare ed.undo()
    try:
        _undo_bare(1)
        after = _cube_x()
        _record("ed.undo() bare reverts", after == base, f"x={after} (base={base})")
    except Exception as e:  # spike: 失敗内容を観測
        _record("ed.undo() bare reverts", False, repr(e))

    # (A') bare で戻らなかった場合に override を試す
    if _cube_x() != base:
        try:
            _undo_override(1)
            after = _cube_x()
            _record("ed.undo() override reverts", after == base, f"x={after}")
        except Exception as e:  # spike: 失敗内容を観測
            _record("ed.undo() override reverts", False, repr(e))

    # (B) ed.redo() で再適用
    try:
        _redo_bare(1)
        after = _cube_x()
        _record("ed.redo() bare reapplies", after == 5.0, f"x={after}")
    except Exception as e:  # spike: 失敗内容を観測
        _record("ed.redo() bare reapplies", False, repr(e))


def try_multi_step() -> None:
    """複数段 undo（--steps N 相当）: 2回移動 → 2段 undo で原点へ戻るか。"""
    try:
        from bli_addon import ops  # type: ignore
    except Exception as e:  # spike
        _record("import ops (multi)", False, repr(e))
        return
    _ensure_cube_origin()
    base = _cube_x()
    ops.dispatch("transform", {"targets": "Cube", "location": [3.0, 0.0, 0.0]}, None)
    ops.dispatch("transform", {"targets": "Cube", "location": [7.0, 0.0, 0.0]}, None)
    _record("two moves -> x=7", _cube_x() == 7.0, f"x={_cube_x()}")
    try:
        _undo_bare(2)
        after = _cube_x()
        _record("ed.undo() x2 -> base", after == base, f"x={after} (base={base})")
    except Exception as e:  # spike
        _record("ed.undo() x2 -> base", False, repr(e))


def try_stack_end_and_matrix() -> None:
    """スタック端の終端挙動（CANCELLED か RuntimeError か）と undo 直後の matrix_world 確定を観測。

    本番 undo_steps は端を「FINISHED 以外で break」＋「RuntimeError も break」で正規化している。
    その前提（端で例外を投げる版があっても INTERNAL 化しない）を実機で固める（セルフレビュー P2）。
    """
    try:
        from bli_addon import ops  # type: ignore
    except Exception as e:  # spike
        _record("import ops (end)", False, repr(e))
        return
    _ensure_cube_origin()
    ops.dispatch("transform", {"targets": "Cube", "location": [4.0, 0.0, 0.0]}, None)
    try:
        ret = tuple(sorted(bpy.ops.ed.undo()))
    except RuntimeError as e:  # spike: 観測
        _record("undo after move", False, repr(e))
        return
    # undo 直後に matrix_world.translation が確定値（=0）を返すか（fingerprint は matrix_world を使う）。
    mx = round(float(bpy.data.objects["Cube"].matrix_world.translation.x), 4)
    _record("matrix_world fresh after undo", mx == 0.0, f"mw.x={mx} ret={ret}")
    # スタックを使い切るまで undo し、終端の戻り値 / 例外を観測する。
    terminal = None
    raised = None
    try:
        for _ in range(200):
            res = tuple(sorted(bpy.ops.ed.undo()))
            if "FINISHED" not in res:
                terminal = res
                break
    except RuntimeError as e:  # spike: 端で raise する版か観測
        raised = repr(e)
    _record("stack-end terminal observed", True, f"terminal={terminal} raised={raised}")


def run() -> None:
    print("=== BLI_UNDO_SPIKE_BEGIN ===")
    print("blender:", bpy.app.version_string, "background:", bpy.app.background)
    print("windows:", len(bpy.context.window_manager.windows))
    try_dispatch_then_undo()
    try_multi_step()
    try_stack_end_and_matrix()
    for name, ok, detail in results:
        print(f"  [{'OK' if ok else 'NG'}] {name} {detail}")
    print("=== BLI_UNDO_SPIKE_END ===")
    bpy.ops.wm.quit_blender()


if bpy.app.background:
    print("[warn] --background で実行中: ed.undo は不定（GUI モード推奨）")
    run()
else:
    bpy.app.timers.register(run, first_interval=1.5)
