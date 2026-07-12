"""behavior スナップショットのケース表（P2-2 移行ガード）。

各ケースは human / --json の両モードで実行される（snapshot_lib.behavior_document）。
canned response は「human フォーマッタが読むキー」を含む最小の domain result を書く
（ops.py の実応答形に似せるが、スナップショットの目的は CLI 側の回帰検出）。

対象: RPC を送る全コマンド + job-wait。human フォーマッタの分岐（straighten の method 別 /
mesh の op 別 / material・modifier・collection の action 別など）は分岐ごとに 1 ケース置く。
CLI ローカル完結の doctor / init / policy / list-commands / help は既存テストが厚く、
ファクトリ移行の対象外のため含めない。

エラー経路は「CLI 側手書きロジック」（_parse_vec / 範囲チェック / 排他 / --name-regex ガード）
を中心に固定する（サーバエラーの写像は test_cli_help.py の _exit_code_for テストが担う）。
"""

from __future__ import annotations

from pathlib import Path

from snapshot_lib import Case

# 冪等 --id を明示できるコマンドは固定 UUID を渡す（stdout の決定性・省略経路は uuid fake が担保）
FIXED_ID = "11111111-2222-4333-8444-555555555555"

# exec-python --file ケース用の固定スクリプト（内容は 1 行・機械非依存でスナップショットに焼けるように
# behavior_cases.py 側にはファイル内容を書かず、実行時に CLI が読む）。
EXEC_SNIPPET_PATH = str(Path(__file__).parent / "data" / "exec_snippet.py")

CASES: list[Case] = [
    # ---- ping（手書き維持だが _call_or_exit を共有するため固定）----
    Case(
        "ping/ok",
        ["ping"],
        [{"result": {"operation": "ping", "data": {"pong": True}}}],
    ),
    Case(
        "ping/connect-error",
        ["ping"],
        [
            {
                "connect_error": "接続不能 127.0.0.1:9876: [Errno 111] refused（アドオンが起動していない可能性）"
            }
        ],
    ),
    # ---- scene-info ----
    Case(
        "scene-info/basic",
        ["scene-info"],
        [
            {
                "result": {
                    "operation": "scene-info",
                    "data": {
                        "scene": "Scene",
                        "object_count": 2,
                        "objects": [
                            {"name": "Cube", "type": "MESH"},
                            {"name": "Light", "type": "LIGHT"},
                        ],
                    },
                }
            }
        ],
    ),
    Case(
        "scene-info/depth",
        ["scene-info", "--depth", "2"],
        [
            {
                "result": {
                    "operation": "scene-info",
                    "data": {"scene": "Scene", "object_count": 0, "objects": []},
                }
            }
        ],
    ),
    # ---- set-origin ----
    Case(
        "set-origin/geometry",
        [
            "set-origin",
            "--targets",
            "Cube",
            "--to",
            "geometry",
            "--center",
            "bounds",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "set-origin",
                    "data": {"name": "Cube", "to": "geometry", "origin_world": [0.0, 0.0, 0.5]},
                }
            }
        ],
    ),
    Case(
        "set-origin/world-xyz",
        [
            "set-origin",
            "--target",
            "Cube",
            "--to",
            "world",
            "--x",
            "1.5",
            "--z",
            "0.25",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "set-origin",
                    "data": {"name": "Cube", "to": "world", "origin_world": [1.5, 0.0, 0.25]},
                }
            }
        ],
    ),
    Case(
        "set-origin/invalid-enum-local",
        ["set-origin", "--targets", "Cube", "--to", "bogus"],
        local_only=True,
    ),
    # ---- undo / redo（CLI 側範囲チェック）----
    Case(
        "undo/ok",
        ["undo", "--steps", "2", "--id", FIXED_ID],
        [{"result": {"operation": "undo", "data": {"requested": 2, "applied": 2}}}],
    ),
    Case("undo/steps-out-of-range", ["undo", "--steps", "0"], local_only=True),
    # ---- transform（_parse_vec）----
    Case(
        "transform/set",
        [
            "transform",
            "--targets",
            "Cube",
            "--location",
            "1,2,3",
            "--scale",
            "2,2,2",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "transform",
                    "data": {
                        "name": "Cube",
                        "location": [1.0, 2.0, 3.0],
                        "rotation_euler_deg": [0.0, 0.0, 0.0],
                        "scale": [2.0, 2.0, 2.0],
                    },
                }
            }
        ],
    ),
    Case(
        "transform/bad-vec-local",
        ["transform", "--targets", "Cube", "--location", "1,2"],
        local_only=True,
    ),
    # ---- straighten（method 別 human 分岐: floor）----
    Case(
        "straighten/floor",
        ["straighten", "--targets", "Cube", "--method", "floor", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "straighten",
                    "data": {
                        "name": "Cube",
                        "method": "floor",
                        "up_axis": "+Z",
                        "min_up": -0.5,
                        "floor_offset": 0.5,
                    },
                }
            }
        ],
    ),
    # ---- export（async accepted 即返し）----
    Case(
        "export/async-accepted",
        ["export", "--format", "fbx", "--path", "out.fbx", "--id", FIXED_ID, "--async"],
        [{"result": {"operation": "export", "accepted": True, "job_id": FIXED_ID}}],
    ),
    # ---- request-status（watchdog 注記）----
    Case(
        "request-status/unresponsive",
        ["request-status", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "request-status",
                    "data": {
                        "id": FIXED_ID,
                        "state": "RUNNING",
                        "known": True,
                        "watchdog": {"responsive": False, "last_pump_age": 42.3},
                    },
                }
            }
        ],
    ),
    # ---- request-status（responsive=True・unresponsive と対）----
    Case(
        "request-status/responsive",
        ["request-status", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "request-status",
                    "data": {
                        "id": FIXED_ID,
                        "state": "RUNNING",
                        "known": True,
                        "watchdog": {"responsive": True, "last_pump_age": 0.1},
                    },
                }
            }
        ],
    ),
    # ---- job-status（watchdog なし＝応答性注記なし分岐。method は request-status を送る）----
    Case(
        "job-status/no-watchdog",
        ["job-status", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "request-status",
                    "data": {"id": FIXED_ID, "state": "RUNNING", "known": True},
                }
            }
        ],
    ),
    # ---- list-objects ----
    Case(
        "list-objects/type-name-regex",
        ["list-objects", "--type", "MESH", "--name-regex", "Cu.*"],
        [
            {
                "result": {
                    "operation": "list-objects",
                    "data": {"objects": [{"name": "Cube", "type": "MESH"}], "count": 1},
                }
            }
        ],
    ),
    # --name-regex の値渡し忘れガード（click が次の `--` 始まりトークンを値として食う事故を防ぐ）。
    # プレースホルダは `--json`（run_case が json モードで自動付与する値）ではなく `--foo` を使う
    # ＝ human/json どちらのモードでも argv 上で `--name-regex` の直後に来るのは常に `--foo` になる。
    Case(
        "list-objects/name-regex-value-guard-local",
        ["list-objects", "--name-regex", "--foo"],
        local_only=True,
    ),
    # ---- object-info ----
    Case(
        "object-info/regex-targets",
        ["object-info", "--targets", "Cube", "--regex"],
        [
            {
                "result": {
                    "operation": "object-info",
                    "data": {
                        "name": "Cube",
                        "type": "MESH",
                        "location": [0.0, 0.0, 0.0],
                        "dimensions": [2.0, 2.0, 2.0],
                    },
                }
            }
        ],
    ),
    # ---- straighten（method 別 human 分岐。floor は既存ケース）----
    Case(
        "straighten/world-align",
        [
            "straighten",
            "--targets",
            "Cube",
            "--method",
            "world-align",
            "--axis",
            "Z",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "straighten",
                    "data": {
                        "name": "Cube",
                        "method": "world-align",
                        "up_axis": "+Z",
                        "axis": "Z",
                        "aligned_world": [0.0, 0.0, 1.0],
                        "rotation_euler_deg": [0.0, 0.0, 0.0],
                    },
                }
            }
        ],
    ),
    Case(
        "straighten/reference",
        [
            "straighten",
            "--targets",
            "Cube",
            "--method",
            "reference",
            "--axis",
            "X",
            "--reference",
            "Other",
            "--ref-axis",
            "+Z",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "straighten",
                    "data": {
                        "name": "Cube",
                        "method": "reference",
                        "up_axis": "+Z",
                        "axis": "X",
                        "reference": "Other",
                        "ref_axis": "+Z",
                        "aligned_world": [1.0, 0.0, 0.0],
                        "rotation_euler_deg": [0.0, 0.0, 90.0],
                    },
                }
            }
        ],
    ),
    Case(
        "straighten/pca",
        [
            "straighten",
            "--targets",
            "Cube",
            "--method",
            "pca",
            "--up-hint",
            "current",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "straighten",
                    "data": {
                        "name": "Cube",
                        "method": "pca",
                        "up_axis": "+Z",
                        "tilt_from_up_deg": 5.0,
                        "principal_world_after": [0.0, 0.0, 1.0],
                        "rotation_euler_deg": [1.0, 2.0, 3.0],
                    },
                }
            }
        ],
    ),
    Case(
        "straighten/angle",
        [
            "straighten",
            "--targets",
            "Cube",
            "--method",
            "angle",
            "--axis",
            "Z",
            "--degrees",
            "45",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "straighten",
                    "data": {
                        "name": "Cube",
                        "method": "angle",
                        "up_axis": "+Z",
                        "axis": "Z",
                        "degrees": 45.0,
                        "rotation_euler_deg": [0.0, 0.0, 45.0],
                        "baked": False,
                    },
                }
            }
        ],
    ),
    Case(
        "straighten/align-vector",
        [
            "straighten",
            "--targets",
            "Cube",
            "--method",
            "align-vector",
            "--from-dir",
            "1,0,0",
            "--to-dir",
            "0,0,1",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "straighten",
                    "data": {
                        "name": "Cube",
                        "method": "align-vector",
                        "up_axis": "+Z",
                        "from_dir": [1.0, 0.0, 0.0],
                        "from_world_after": [0.0, 0.0, 1.0],
                        "angle_deg": 90.0,
                        "rotation_euler_deg": [0.0, 90.0, 0.0],
                        "baked": False,
                    },
                }
            }
        ],
    ),
    Case(
        "straighten/reset-default",
        ["straighten", "--targets", "Cube", "--method", "reset", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "straighten",
                    "data": {
                        "name": "Cube",
                        "method": "reset",
                        "up_axis": "+Z",
                        "rotation_euler_deg": [0.0, 0.0, 0.0],
                        "baked": False,
                    },
                }
            }
        ],
    ),
    Case(
        "straighten/dry-run",
        ["straighten", "--targets", "Cube", "--method", "reset", "--dry-run", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "straighten",
                    "data": {
                        "name": "Cube",
                        "method": "reset",
                        "up_axis": "+Z",
                        "dry_run": True,
                        "rotation_euler_deg": [0.0, 0.0, 0.0],
                        "baked": False,
                    },
                }
            }
        ],
    ),
    Case(
        "straighten/from-dir-vec-error-local",
        ["straighten", "--targets", "Cube", "--method", "align-vector", "--from-dir", "1,2"],
        local_only=True,
    ),
    # ---- capture ----
    Case(
        "capture/viewport-default",
        ["capture"],
        [
            {
                "result": {
                    "operation": "capture",
                    "data": {
                        "source": "viewport",
                        "width": 1920,
                        "height": 1080,
                        "path": "/tmp/viewport.png",
                        "size": 45231,
                    },
                }
            }
        ],
    ),
    Case(
        "capture/render-camera",
        ["capture", "--source", "render", "--camera", "Cam"],
        [
            {
                "result": {
                    "operation": "capture",
                    "data": {
                        "source": "render",
                        "camera": "Cam",
                        "width": 1920,
                        "height": 1080,
                        "path": "/tmp/render.png",
                        "size": 98765,
                    },
                }
            }
        ],
    ),
    # ---- redo ----
    Case(
        "redo/ok",
        ["redo", "--steps", "3", "--id", FIXED_ID],
        [{"result": {"operation": "redo", "data": {"requested": 3, "applied": 3}}}],
    ),
    # ---- print-setup ----
    Case(
        "print-setup/mm",
        ["print-setup", "--unit", "mm", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "print-setup",
                    "data": {
                        "scene": "Scene",
                        "unit": "mm",
                        "unit_settings": {"system": "METRIC", "length_unit": "MILLIMETERS"},
                        "changed": True,
                    },
                }
            }
        ],
    ),
    # ---- print-check ----
    Case(
        "print-check/all-omitted",
        ["print-check", "--targets", "Cube", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "print-check",
                    "data": {
                        "name": "Cube",
                        "checks": {
                            "is_printable": True,
                            "non_manifold_edges": 0,
                            "flipped_normals": 0,
                            "degenerate_faces": 0,
                        },
                    },
                }
            }
        ],
    ),
    Case(
        "print-check/thin-min-thickness",
        ["print-check", "--targets", "Cube", "--thin", "--min-thickness", "0.8", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "print-check",
                    "data": {"name": "Cube", "checks": {"is_printable": False, "thin_walls": 3}},
                }
            }
        ],
    ),
    # ---- print-repair ----
    Case(
        "print-repair/manifold-normals",
        [
            "print-repair",
            "--targets",
            "Cube",
            "--make-manifold",
            "--recalc-normals",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "print-repair",
                    "data": {
                        "name": "Cube",
                        "applied": ["make_manifold", "recalc_normals"],
                        "fixed": {"non_manifold_edges": 2, "flipped_normals": 1},
                        "after": {"is_printable": True},
                    },
                }
            }
        ],
    ),
    # ---- print-export ----
    Case(
        "print-export/stl",
        [
            "print-export",
            "--targets",
            "Cube",
            "--format",
            "stl",
            "--path",
            "out.stl",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "print-export",
                    "data": {
                        "name": "Cube",
                        "format": "stl",
                        "path": "out.stl",
                        "size": 1024,
                        "triangles": 12,
                        "global_scale": 1.0,
                    },
                }
            }
        ],
    ),
    # ---- export ----
    Case(
        "export/whole-scene",
        ["export", "--format", "obj", "--path", "out.obj", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "export",
                    "data": {
                        "format": "obj",
                        "path": "out.obj",
                        "size": 2048,
                        "sha256": "deadbeef" * 8,
                    },
                }
            }
        ],
    ),
    Case(
        "export/fbx-options",
        [
            "export",
            "--targets",
            "Cube",
            "--format",
            "fbx",
            "--path",
            "out.fbx",
            "--axis-forward=-Z",
            "--axis-up",
            "Y",
            "--scale",
            "1.0",
            "--no-apply-unit-scale",
            "--embed-textures",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "export",
                    "data": {
                        "format": "fbx",
                        "path": "out.fbx",
                        "exported_objects": ["Cube"],
                        "size": 4096,
                        "sha256": "cafebabe" * 8,
                        "fbx_options": {
                            "axis_forward": "-Z",
                            "axis_up": "Y",
                            "scale": 1.0,
                            "apply_unit_scale": False,
                            "embed_textures": True,
                        },
                    },
                }
            }
        ],
    ),
    Case(
        "export/use-selection-gltf",
        ["export", "--format", "gltf", "--path", "out.glb", "--use-selection", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "export",
                    "data": {
                        "format": "gltf",
                        "path": "out.glb",
                        "exported_objects": ["Cube", "Sphere"],
                        "size": 8192,
                        "sha256": "12345678" * 8,
                    },
                }
            }
        ],
    ),
    # ---- import ----
    Case(
        "import/obj",
        ["import", "--format", "obj", "--path", "in.obj", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "import",
                    "data": {
                        "format": "obj",
                        "imported": [{"name": "Cube"}, {"name": "Cube.001"}],
                        "count": 2,
                    },
                }
            }
        ],
    ),
    # ---- save ----
    Case(
        "save/path-backed-up",
        ["save", "--path", "out.blend", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "save",
                    "data": {
                        "path": "out.blend",
                        "size": 102400,
                        "backed_up": True,
                        "backup_path": "out.blend1",
                    },
                }
            }
        ],
    ),
    Case(
        "save/path-omitted",
        ["save", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "save",
                    "data": {"path": "current.blend", "size": 51200, "backed_up": False},
                }
            }
        ],
    ),
    # ---- open ----
    Case(
        "open/force-discard",
        ["open", "--path", "x.blend", "--force", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "open",
                    "data": {
                        "path": "x.blend",
                        "scene": "Scene",
                        "object_count": 5,
                        "discarded_unsaved": True,
                    },
                }
            }
        ],
    ),
    # ---- exec-python ----
    Case(
        "exec-python/full",
        ["exec-python", "--code", "print(1)", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "exec-python",
                    "data": {
                        "stdout": "1\n",
                        "stderr": "[warn] deprecation\n",
                        "result_repr": "2",
                        "heuristic_flags": ["network_call", "file_write"],
                        "audit_ok": False,
                    },
                }
            }
        ],
    ),
    Case(
        "exec-python/minimal",
        ["exec-python", "--code", "pass", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "exec-python",
                    "data": {
                        "stdout": "",
                        "stderr": "",
                        "result_repr": None,
                        "heuristic_flags": [],
                        "audit_ok": True,
                    },
                }
            }
        ],
    ),
    Case(
        "exec-python/both-specified-local",
        ["exec-python", "--code", "print(1)", "--file", "somefile.py"],
        local_only=True,
    ),
    Case("exec-python/neither-specified-local", ["exec-python"], local_only=True),
    Case(
        "exec-python/from-file",
        ["exec-python", "--file", EXEC_SNIPPET_PATH, "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "exec-python",
                    "data": {
                        "stdout": "hello from file\n",
                        "stderr": "",
                        "result_repr": None,
                        "heuristic_flags": [],
                        "audit_ok": True,
                    },
                }
            }
        ],
    ),
    # ---- select ----
    Case(
        "select/regex-type-active",
        [
            "select",
            "--targets",
            "Cube.*",
            "--regex",
            "--type",
            "MESH",
            "--active",
            "Cube",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "select",
                    "data": {"count": 2, "selected": ["Cube", "Cube.001"], "active": "Cube"},
                }
            }
        ],
    ),
    # ---- apply-transform ----
    Case(
        "apply-transform/location-scale",
        ["apply-transform", "--targets", "Cube", "--location", "--scale", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "apply-transform",
                    "data": {
                        "name": "Cube",
                        "scale": [1.0, 1.0, 1.0],
                        "dimensions": [2.0, 2.0, 2.0],
                    },
                }
            }
        ],
    ),
    # ---- duplicate ----
    Case(
        "duplicate/count-offset-linked",
        [
            "duplicate",
            "--targets",
            "Cube",
            "--count",
            "3",
            "--offset",
            "1,0,0",
            "--linked",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "duplicate",
                    "data": {
                        "source": "Cube",
                        "created": ["Cube.001", "Cube.002", "Cube.003"],
                        "count": 3,
                    },
                }
            }
        ],
    ),
    Case(
        "duplicate/count-zero-local",
        ["duplicate", "--targets", "Cube", "--count", "0"],
        local_only=True,
    ),
    # ---- delete ----
    Case(
        "delete/ok",
        ["delete", "--targets", "Cube", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "delete",
                    "data": {
                        "deleted": "Cube",
                        "backup": {"type": "MESH", "location": [0.0, 0.0, 0.0]},
                    },
                }
            }
        ],
    ),
    # ---- material ----
    Case(
        "material/list",
        ["material", "--action", "list", "--targets", "Cube", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "material",
                    "data": {
                        "action": "list",
                        "name": "Cube",
                        "materials": [
                            {"slot": 0, "name": "Red", "base_color": [1.0, 0.0, 0.0, 1.0]}
                        ],
                    },
                }
            }
        ],
    ),
    Case(
        "material/assign",
        ["material", "--action", "assign", "--targets", "Cube", "--name", "Red", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "material",
                    "data": {"action": "assign", "material": "Red", "name": "Cube", "slot": 0},
                }
            }
        ],
    ),
    Case(
        "material/create-color",
        [
            "material",
            "--action",
            "create",
            "--targets",
            "Cube",
            "--name",
            "NewMat",
            "--color",
            "1,0,0,1",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "material",
                    "data": {"action": "create", "material": "NewMat", "name": "Cube", "slot": 1},
                }
            }
        ],
    ),
    Case(
        "material/color-vec-error-local",
        [
            "material",
            "--action",
            "create",
            "--targets",
            "Cube",
            "--name",
            "NewMat",
            "--color",
            "1,0",
        ],
        local_only=True,
    ),
    # ---- modifier ----
    Case(
        "modifier/list",
        ["modifier", "--action", "list", "--targets", "Cube", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "modifier",
                    "data": {
                        "action": "list",
                        "name": "Cube",
                        "modifiers": [{"name": "Mirror", "type": "MIRROR"}],
                    },
                }
            }
        ],
    ),
    Case(
        "modifier/add",
        [
            "modifier",
            "--action",
            "add",
            "--targets",
            "Cube",
            "--type",
            "MIRROR",
            "--axis",
            "X",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "modifier",
                    "data": {
                        "action": "add",
                        "name": "Cube",
                        "modifier": {"name": "Mirror", "type": "MIRROR"},
                    },
                }
            }
        ],
    ),
    Case(
        "modifier/apply",
        [
            "modifier",
            "--action",
            "apply",
            "--targets",
            "Cube",
            "--name",
            "Mirror",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "modifier",
                    "data": {"action": "apply", "name": "Cube", "applied": "Mirror"},
                }
            }
        ],
    ),
    Case(
        "modifier/remove",
        [
            "modifier",
            "--action",
            "remove",
            "--targets",
            "Cube",
            "--name",
            "Mirror",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "modifier",
                    "data": {"action": "remove", "name": "Cube", "removed": "Mirror"},
                }
            }
        ],
    ),
    # ---- add ----
    Case(
        "add/cube-full",
        [
            "add",
            "--type",
            "cube",
            "--location",
            "0,0,1",
            "--rotation",
            "0,0,45",
            "--scale",
            "1,1,2",
            "--name",
            "Box",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "add",
                    "data": {"type": "cube", "name": "Box", "location": [0.0, 0.0, 1.0]},
                }
            }
        ],
    ),
    Case(
        "add/light-sun",
        ["add", "--type", "light", "--light-type", "SUN", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "add",
                    "data": {"type": "light", "name": "Sun", "location": [0.0, 0.0, 0.0]},
                }
            }
        ],
    ),
    Case(
        "add/location-vec-error-local",
        ["add", "--type", "cube", "--location", "1,2"],
        local_only=True,
    ),
    # ---- mode ----
    Case(
        "mode/to-object-no-targets",
        ["mode", "--to", "object", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "mode",
                    "data": {"from_mode": "EDIT", "to_mode": "OBJECT", "active": "Cube"},
                }
            }
        ],
    ),
    Case(
        "mode/to-edit-targets",
        ["mode", "--to", "edit", "--targets", "Cube", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "mode",
                    "data": {"from_mode": "OBJECT", "to_mode": "EDIT", "active": "Cube"},
                }
            }
        ],
    ),
    # ---- rename ----
    Case(
        "rename/with-data",
        ["rename", "--targets", "Cube", "--name", "Box", "--with-data", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "rename",
                    "data": {"old_name": "Cube", "new_name": "Box", "data_renamed": True},
                }
            }
        ],
    ),
    # ---- parent ----
    Case(
        "parent/set-multi",
        ["parent", "--targets", "A,B", "--to", "Root", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "parent",
                    "data": {
                        "action": "set",
                        "results": [
                            {"name": "A", "parent": "Root"},
                            {"name": "B", "parent": "Root"},
                        ],
                    },
                }
            }
        ],
    ),
    Case(
        "parent/clear",
        ["parent", "--targets", "A,B", "--clear", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "parent",
                    "data": {
                        "action": "clear",
                        "results": [{"name": "A", "parent": None}, {"name": "B", "parent": None}],
                    },
                }
            }
        ],
    ),
    # ---- collection ----
    Case(
        "collection/list",
        ["collection", "--action", "list", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "collection",
                    "data": {
                        "action": "list",
                        "collections": [{"name": "Collection", "objects": 3}],
                    },
                }
            }
        ],
    ),
    Case(
        "collection/create",
        ["collection", "--action", "create", "--name", "Props", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "collection",
                    "data": {"action": "create", "name": "Props"},
                }
            }
        ],
    ),
    Case(
        "collection/move",
        [
            "collection",
            "--action",
            "move",
            "--name",
            "Props",
            "--targets",
            "Cube",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "collection",
                    "data": {
                        "action": "move",
                        "collection": "Props",
                        "results": [{"name": "Cube"}],
                    },
                }
            }
        ],
    ),
    # ---- mesh ----
    Case(
        "mesh/recalc-normals",
        ["mesh", "--op", "recalc-normals", "--targets", "Cube", "--inside", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "mesh",
                    "data": {
                        "op": "recalc-normals",
                        "name": "Cube",
                        "faces": 6,
                        "flipped": 2,
                        "inside": True,
                    },
                }
            }
        ],
    ),
    Case(
        "mesh/merge-by-distance",
        [
            "mesh",
            "--op",
            "merge-by-distance",
            "--targets",
            "Cube",
            "--distance",
            "0.001",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "mesh",
                    "data": {
                        "op": "merge-by-distance",
                        "name": "Cube",
                        "merged": 3,
                        "before": 10,
                        "after": 7,
                    },
                }
            }
        ],
    ),
    Case(
        "mesh/extrude",
        ["mesh", "--op", "extrude", "--targets", "Cube", "--offset", "0,0,1", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "mesh",
                    "data": {
                        "op": "extrude",
                        "name": "Cube",
                        "delta": {"vertices": 4, "edges": 4, "polygons": 1},
                        "stats": {"vertices": 12, "edges": 16, "polygons": 7},
                    },
                }
            }
        ],
    ),
    Case(
        "mesh/bevel",
        [
            "mesh",
            "--op",
            "bevel",
            "--targets",
            "Cube",
            "--width",
            "0.1",
            "--segments",
            "2",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "mesh",
                    "data": {
                        "op": "bevel",
                        "name": "Cube",
                        "delta": {"vertices": 8, "edges": 12, "polygons": 6},
                        "stats": {"vertices": 20, "edges": 30, "polygons": 18},
                    },
                }
            }
        ],
    ),
    Case(
        "mesh/boolean",
        [
            "mesh",
            "--op",
            "boolean",
            "--targets",
            "Cube",
            "--operation",
            "UNION",
            "--with",
            "Other",
            "--id",
            FIXED_ID,
        ],
        [
            {
                "result": {
                    "operation": "mesh",
                    "data": {
                        "op": "boolean",
                        "name": "Cube",
                        "operation": "UNION",
                        "with_object": "Other",
                        "delta": {"vertices": -2, "edges": -3, "polygons": -1},
                        "stats": {"vertices": 10, "edges": 13, "polygons": 5},
                    },
                }
            }
        ],
    ),
    Case(
        "mesh/decimate",
        ["mesh", "--op", "decimate", "--targets", "Cube", "--ratio", "0.5", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "mesh",
                    "data": {
                        "op": "decimate",
                        "name": "Cube",
                        "ratio": 0.5,
                        "delta": {"vertices": -100, "edges": -150, "polygons": -50},
                        "stats": {"vertices": 50, "edges": 75, "polygons": 25},
                    },
                }
            }
        ],
    ),
    Case(
        "mesh/offset-vec-error-local",
        ["mesh", "--op", "extrude", "--targets", "Cube", "--offset", "1,2"],
        local_only=True,
    ),
    # ---- job-wait（DONE・human=None の汎用メッセージ）----
    Case(
        "job-wait/done-generic",
        ["job-wait", "--id", FIXED_ID],
        [
            {
                "result": {
                    "operation": "request-status",
                    "data": {
                        "id": FIXED_ID,
                        "state": "DONE",
                        "known": True,
                        "result": {
                            "jsonrpc": "2.0",
                            "id": FIXED_ID,
                            "result": {
                                "operation": "export",
                                "data": {"path": "out.fbx", "size": 100},
                            },
                        },
                    },
                }
            }
        ],
    ),
    # ---- auto-wait（heavy コマンドの同期待機。accepted → request-status DONE → 通常提示）----
    Case(
        "import/auto-wait-sync",
        ["import", "--format", "obj", "--path", "in.obj", "--id", FIXED_ID],
        [
            {"result": {"operation": "import", "accepted": True, "job_id": FIXED_ID}},
            {
                "result": {
                    "operation": "request-status",
                    "data": {
                        "id": FIXED_ID,
                        "state": "DONE",
                        "known": True,
                        "result": {
                            "jsonrpc": "2.0",
                            "id": FIXED_ID,
                            "result": {
                                "operation": "import",
                                "data": {
                                    "format": "obj",
                                    "imported": [{"name": "Cube"}],
                                    "count": 1,
                                },
                            },
                        },
                    },
                }
            },
        ],
    ),
    # ---- サーバエラー写像（retryable→exit2 / INVALID_PARAMS→exit4）----
    Case(
        "scene-info/timeout-retryable",
        ["scene-info"],
        [
            {
                "error": {
                    "message": "TIMEOUT",
                    "data": {"retryable": True, "userVisibleSymptom": "処理がタイムアウトしました"},
                }
            }
        ],
    ),
    Case(
        "scene-info/invalid-params",
        ["scene-info", "--depth", "2"],
        [
            {
                "error": {
                    "message": "INVALID_PARAMS",
                    "data": {"category": "USER_INPUT", "userVisibleSymptom": "depth が不正です"},
                }
            }
        ],
    ),
]
