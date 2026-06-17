"""M9 T9.3 save（.blend 保存）実機確認（着手前スパイク / NEXT-M9 §2.3）。

`blender --background --python save_spike.py` で実行（5.0.1 / 4.4.3 両版）。

確認事項:
- `wm.save_as_mainfile` / `wm.save_mainfile` の引数集合（filepath/copy/check_existing/compress/relative_remap）。
- **`.blend1` backup 挙動**: 上書き時に Blender が自動で `.blend1` を作るか（preferences `save_version` 既定値依存）。
  background でも作られるか。→ backup を `--backup` で確実に制御するため「明示コピー方式」が要るか判断。
- 未保存時の `bpy.data.filepath`（空文字）。save_as_mainfile 後に filepath が更新されるか。
- background で save が機能するか（GUI 不要のはず）。
"""

import os
import tempfile

import bpy  # type: ignore


def report(label, fn):
    try:
        r = fn()
        print(f"[OK] {label}: {r}")
        return r
    except Exception as e:
        print(f"[ERR] {label}: {type(e).__name__}: {e}")
        return None


def op_real(path):
    ns, _, name = path.partition(".")
    group = getattr(bpy.ops, ns, None)
    if group is None or not hasattr(group, name):
        return False
    try:
        getattr(group, name).get_rna_type()
        return True
    except Exception:
        return False


def rna_props(path):
    ns, _, name = path.partition(".")
    rna = getattr(getattr(bpy.ops, ns), name).get_rna_type()
    out = {}
    for prop in rna.properties:
        if prop.identifier == "rna_type":
            continue
        try:
            default = prop.default if hasattr(prop, "default") else None
        except Exception:
            default = "?"
        out[prop.identifier] = f"{prop.type}={default}"
    return out


def main():
    print("=== BLI_SAVE_SPIKE_BEGIN ===")
    print("version", bpy.app.version_string)
    print("background", bpy.app.background)

    report("op_real wm.save_as_mainfile", lambda: op_real("wm.save_as_mainfile"))
    report("op_real wm.save_mainfile", lambda: op_real("wm.save_mainfile"))
    report("rna wm.save_as_mainfile", lambda: rna_props("wm.save_as_mainfile"))

    # 未保存時の filepath（空文字のはず）。
    print("initial bpy.data.filepath repr:", repr(bpy.data.filepath))

    # preferences の save_version（backup の世代数・既定 1 のはず）。
    def save_version():
        return bpy.context.preferences.filepaths.save_version

    report("preferences.filepaths.save_version", save_version)

    tmpdir = tempfile.mkdtemp(prefix="bli_save_spike_")
    target = os.path.join(tmpdir, "scene.blend")

    # 1回目の保存（新規ファイル）。
    def save1():
        bpy.ops.wm.save_as_mainfile(filepath=target)
        return {
            "exists": os.path.exists(target),
            "size": os.path.getsize(target) if os.path.exists(target) else None,
            "filepath_after": repr(bpy.data.filepath),
        }

    report("save_as_mainfile (新規)", save1)

    # 2回目の保存（上書き）→ .blend1 backup が出来るか。
    def save2_overwrite():
        bpy.ops.wm.save_as_mainfile(filepath=target)
        backup = target + "1"  # scene.blend1
        return {
            "blend1_exists": os.path.exists(backup),
            "dir": sorted(os.listdir(tmpdir)),
        }

    report("save_as_mainfile (上書き2回目・.blend1?)", save2_overwrite)

    # save_mainfile（現在ファイルへ保存・filepath 既定）。
    def save_current():
        bpy.ops.wm.save_mainfile()
        return {"dir": sorted(os.listdir(tmpdir))}

    report("save_mainfile (現在ファイルへ)", save_current)

    # copy=True（現在ファイルを変えずにコピー保存）。
    def save_copy():
        copy_path = os.path.join(tmpdir, "copy.blend")
        bpy.ops.wm.save_as_mainfile(filepath=copy_path, copy=True)
        return {
            "copy_exists": os.path.exists(copy_path),
            "filepath_unchanged": bpy.data.filepath == target,  # copy は current を変えない
        }

    report("save_as_mainfile copy=True", save_copy)

    # save_version=0 で .blend1 backup が抑止されるか（--backup=False を native 機構で実現できるか）。
    def save_version_zero_suppresses():
        prefs = bpy.context.preferences.filepaths
        saved = prefs.save_version
        nb_dir = tempfile.mkdtemp(prefix="bli_save_spike_nb_")
        nb_target = os.path.join(nb_dir, "nb.blend")
        try:
            prefs.save_version = 0
            bpy.ops.wm.save_as_mainfile(filepath=nb_target)  # 新規
            bpy.ops.wm.save_as_mainfile(
                filepath=nb_target
            )  # 上書き（save_version=0 で backup 無し?）
        finally:
            prefs.save_version = saved
        return {
            "blend1_exists": os.path.exists(nb_target + "1"),  # False のはず（backup 抑止）
            "restored_save_version": prefs.save_version,
            "dir": sorted(os.listdir(nb_dir)),
        }

    report("save_version=0 で backup 抑止?", save_version_zero_suppresses)

    # save_version=2 で世代が増えるか（参考・.blend2 まで保持されるか）。
    def save_version_two():
        prefs = bpy.context.preferences.filepaths
        saved = prefs.save_version
        v2_dir = tempfile.mkdtemp(prefix="bli_save_spike_v2_")
        v2_target = os.path.join(v2_dir, "v2.blend")
        try:
            prefs.save_version = 2
            for _ in range(3):
                bpy.ops.wm.save_as_mainfile(filepath=v2_target)
        finally:
            prefs.save_version = saved
        return {"dir": sorted(os.listdir(v2_dir))}

    report("save_version=2 で世代", save_version_two)

    print("=== BLI_SAVE_SPIKE_END ===")


if __name__ == "__main__":
    main()
