"""help / list-commands（SSOT生成・ローカル完結）と送信前ローカル検証の CLI テスト（L1）。

addon に接続しない経路のみ。bad な入力は client.call より前に exit 4 で弾けること、
help/list-commands が SSOT から schema_hash 付きで生成されることを検証する。
"""

from __future__ import annotations

import json

from typer.main import get_command
from typer.testing import CliRunner

import bli.main as main_mod
from bli.main import app

runner = CliRunner()


def test_list_commands_json():
    res = runner.invoke(app, ["list-commands", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert len(data["schema_hash"]) == 64
    names = {c["name"] for c in data["commands"]}
    assert {"ping", "set-origin", "scene-info", "request-status"} <= names
    so = next(c for c in data["commands"] if c["name"] == "set-origin")
    assert so["mutates"] is True
    assert so["required_mode"] == "OBJECT"


def test_list_objects_discoverable():
    # M5 で追加した list-objects が発見系（実装済み一覧）に出る
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    names = {c["name"] for c in data["commands"]}
    assert "list-objects" in names
    res = runner.invoke(app, ["help", "--command", "list-objects", "--json"])
    assert res.exit_code == 0
    schema = json.loads(res.output)["schema"]
    # 名前フィルタは name_regex（targets 系の BOOL `regex` と同名だと取り違えを誘発・R1-4 で改名）
    assert set(schema["properties"]) == {"type", "name_regex"}
    assert "required" not in schema  # type/name_regex は任意


def test_list_objects_name_regex_swallowed_option_is_loud_input_error():
    # `--regex --json` の値渡し忘れは click が --json を値として食い silent 空リストになる
    # （R1-4 の残存経路）。`--` 始まりの値は誤用として送信前に exit 4 で loud に弾く。
    res = runner.invoke(app, ["list-objects", "--regex", "--json"])
    assert res.exit_code == 4, res.output
    assert "オプションに見えます" in res.output


def test_m6_commands_discoverable():
    # M6 T6.1 の select/transform/apply-transform が実装済み一覧に出る
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    names = {c["name"] for c in data["commands"]}
    assert {"select", "transform", "apply-transform"} <= names


def test_m6_t62_commands_discoverable():
    # M6 T6.2 の duplicate/delete が実装済み一覧に出る
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    names = {c["name"] for c in data["commands"]}
    assert {"duplicate", "delete"} <= names
    # duplicate のスキーマ: offset は VEC3（任意・default なし）、count は INT
    schema = json.loads(runner.invoke(app, ["help", "--command", "duplicate", "--json"]).output)[
        "schema"
    ]
    assert set(schema["properties"]) == {"targets", "regex", "linked", "count", "offset"}
    assert schema["required"] == ["targets"]
    assert schema["properties"]["offset"]["type"] == "array"


def test_duplicate_bad_offset_exit_input():
    # 不正な --offset（3要素でない）は送信前に exit 4
    res = runner.invoke(app, ["duplicate", "--targets", "Cube", "--offset", "1,2", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_duplicate_nonfinite_offset_exit_input():
    # nan/inf は送信前に弾く（matrix を壊さない）
    res = runner.invoke(app, ["duplicate", "--targets", "Cube", "--offset", "inf,0,0", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_duplicate_count_below_min_exit_input():
    # --count<1 は送信前に exit 4
    res = runner.invoke(app, ["duplicate", "--targets", "Cube", "--count", "0", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_m6_t63_material_discoverable():
    # M6 T6.3 の material が実装済み一覧に出る + VEC4 color のスキーマ
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    names = {c["name"] for c in data["commands"]}
    assert "material" in names
    schema = json.loads(runner.invoke(app, ["help", "--command", "material", "--json"]).output)[
        "schema"
    ]
    assert set(schema["properties"]) == {
        "action",
        "targets",
        "regex",
        "name",
        "color",
        # P2-3 G5: PBR/テクスチャ（create 専用・presence-sensitive）
        "metallic",
        "roughness",
        "emission",
        "emission_strength",
        "alpha",
        "texture",
        "pack_texture",
        "make_single_user",
    }
    assert schema["required"] == ["action"]  # targets/name は action 別に ops 側で必須化
    color = schema["properties"]["color"]
    assert color["type"] == "array" and color["minItems"] == 4 and color["maxItems"] == 4
    emission = schema["properties"]["emission"]
    assert emission["type"] == "array" and emission["minItems"] == 4
    # PBR 系は presence-sensitive（default を schema に出さない・§6e）
    for key in ("metallic", "roughness", "alpha", "pack_texture", "texture"):
        assert "default" not in schema["properties"][key], key


def test_material_bad_color_vec4_exit_input():
    # 不正な --color（4要素でない）は送信前に exit 4
    res = runner.invoke(
        app,
        [
            "material",
            "--action",
            "create",
            "--targets",
            "Cube",
            "--name",
            "M",
            "--color",
            "1,0,0",
            "--json",
        ],
    )
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_material_nonfinite_color_exit_input():
    # nan/inf の color は送信前に弾く（色を壊さない）
    res = runner.invoke(
        app,
        [
            "material",
            "--action",
            "create",
            "--targets",
            "Cube",
            "--name",
            "M",
            "--color",
            "inf,0,0,1",
            "--json",
        ],
    )
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_material_bad_action_local_validation():
    # 不正な --action は送信前ローカル Pydantic 検証で exit 4
    res = runner.invoke(app, ["material", "--action", "bogus", "--targets", "Cube", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_m6_t64_modifier_discoverable():
    # M6 T6.4 の modifier が実装済み一覧に出る + スキーマ
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    names = {c["name"] for c in data["commands"]}
    assert "modifier" in names
    schema = json.loads(runner.invoke(app, ["help", "--command", "modifier", "--json"]).output)[
        "schema"
    ]
    assert {
        "action",
        "targets",
        "type",
        "props",
        "name",
        "axis",
        "levels",
        "thickness",
        "ratio",
    } <= set(schema["properties"])
    assert set(schema["required"]) == {"action", "targets"}
    # type は P2-3 で STR 化（任意 type・実在はサーバの rna 能力検出）＝enum を出さない。
    assert schema["properties"]["type"]["type"] == "string"
    assert "enum" not in schema["properties"]["type"]
    # props は JSON 文字列（STR・presence-sensitive で default なし）
    assert schema["properties"]["props"]["type"] == "string"
    assert "default" not in schema["properties"]["props"]
    # operation/axis は従来どおり ENUM、levels は INT
    assert schema["properties"]["operation"]["enum"] == ["UNION", "DIFFERENCE", "INTERSECT"]
    assert schema["properties"]["axis"]["enum"] == ["X", "Y", "Z"]
    assert schema["properties"]["levels"]["type"] == "integer"


def test_m7_mesh_discoverable():
    # M7 の mesh が実装済み一覧に出る + スキーマ（op 別 param に default なし）
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "mesh" in by_name
    assert by_name["mesh"]["stability"] == "experimental"  # コマンド単位の experimental
    schema = json.loads(runner.invoke(app, ["help", "--command", "mesh", "--json"]).output)[
        "schema"
    ]
    # T7.1（inside/distance）+ T7.2（offset/width/segments/thickness）+ T7.3（operation/with_object/ratio）
    assert set(schema["properties"]) == {
        "op",
        "targets",
        "regex",
        "inside",
        "distance",
        "offset",
        "width",
        "segments",
        "thickness",
        "operation",
        "with_object",
        "ratio",
        "make_single_user",
    }
    assert set(schema["required"]) == {"op", "targets"}
    assert schema["properties"]["op"]["enum"] == [
        "recalc-normals",
        "merge-by-distance",
        "extrude",
        "bevel",
        "inset",
        "boolean",
        "decimate",
    ]
    assert schema["properties"]["operation"]["enum"] == ["UNION", "DIFFERENCE", "INTERSECT"]
    # op 専用 param は schema default を持たない（別 op への誤送信を防ぐ・§6e）。
    for k in (
        "inside",
        "distance",
        "offset",
        "width",
        "segments",
        "thickness",
        "operation",
        "with_object",
        "ratio",
    ):
        assert "default" not in schema["properties"][k], k
    # offset は VEC3
    assert schema["properties"]["offset"]["type"] == "array"
    assert schema["properties"]["offset"]["minItems"] == 3


def test_m8_straighten_discoverable():
    # M8 T8.2 の straighten が実装済み一覧に出る + スキーマ（stable・enum・presence-sensitive axis）
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "straighten" in by_name
    assert by_name["straighten"]["stability"] == "stable"  # 3シナリオは全 stable（DoD）
    assert by_name["straighten"]["mutates"] is True
    schema = json.loads(runner.invoke(app, ["help", "--command", "straighten", "--json"]).output)[
        "schema"
    ]
    assert set(schema["properties"]) == {
        "targets",
        "regex",
        "method",
        "up_axis",
        "axis",
        "up_hint",
        "degrees",
        "from_dir",
        "to_dir",
        "reference",
        "ref_axis",
        "dry_run",
        "bake_rotation",
        "make_single_user",
    }
    assert set(schema["required"]) == {"targets", "method"}
    # 基準指定 method（angle/align-vector/reference）を追加（実地フィードバック #4）。
    assert schema["properties"]["method"]["enum"] == [
        "reset",
        "world-align",
        "pca",
        "floor",
        "angle",
        "align-vector",
        "reference",
    ]
    assert schema["properties"]["up_axis"]["enum"] == ["+Z", "-Z", "+Y", "-Y", "+X", "-X"]
    assert schema["properties"]["axis"]["enum"] == ["X", "Y", "Z"]
    # axis は world-align/reference/angle で有効・presence-sensitive → schema default を持たない（§6e）。
    assert "default" not in schema["properties"]["axis"]
    # up_axis は既定 +Z を持つ（非 presence-sensitive・spec『既定 +Z』）。
    assert schema["properties"]["up_axis"]["default"] == "+Z"
    # up_hint は pca 専用・presence-sensitive（default なし）・ENUM auto|current（実地フィードバック #5）。
    assert schema["properties"]["up_hint"]["enum"] == ["auto", "current"]
    assert "default" not in schema["properties"]["up_hint"]
    # 基準指定 method の op 専用 param は presence-sensitive（default なし・別 method への誤送信を弾く）。
    for key in ("degrees", "from_dir", "to_dir", "reference", "ref_axis"):
        assert "default" not in schema["properties"][key], key
    assert schema["properties"]["ref_axis"]["enum"] == ["+Z", "-Z", "+Y", "-Y", "+X", "-X"]
    # dry_run は通常モードフラグ（default False・実地フィードバック #2）。
    assert schema["properties"]["dry_run"]["default"] is False


def test_m8_print_setup_discoverable():
    # M8 T8.3 の print-setup が実装済み一覧に出る + スキーマ（stable・unit ENUM 既定 mm）
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "print-setup" in by_name
    assert by_name["print-setup"]["stability"] == "stable"  # 3シナリオは全 stable（DoD）
    assert by_name["print-setup"]["mutates"] is True
    schema = json.loads(runner.invoke(app, ["help", "--command", "print-setup", "--json"]).output)[
        "schema"
    ]
    assert set(schema["properties"]) == {"unit", "scene"}
    assert "required" not in schema  # unit は default あり・scene は任意
    assert schema["properties"]["unit"]["enum"] == ["mm", "m"]
    assert schema["properties"]["unit"]["default"] == "mm"


def test_print_setup_bad_unit_local_validation():
    # 不正な --unit は送信前ローカル Pydantic 検証で exit 4
    res = runner.invoke(app, ["print-setup", "--unit", "inch", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_m8_print_check_repair_discoverable():
    # M8 T8.4 の print-check / print-repair が実装済み一覧に出る + スキーマ
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert {"print-check", "print-repair"} <= set(by_name)
    assert by_name["print-check"]["stability"] == "stable"
    assert by_name["print-check"]["mutates"] is False  # 読み取り専用
    assert by_name["print-repair"]["stability"] == "stable"
    assert by_name["print-repair"]["mutates"] is True  # 破壊的

    chk = json.loads(runner.invoke(app, ["help", "--command", "print-check", "--json"]).output)[
        "schema"
    ]
    assert set(chk["properties"]) == {
        "targets",
        "regex",
        "manifold",
        "normals",
        "degenerate",
        "thin",
        "min_thickness",
        "intersect",
    }
    assert chk["required"] == ["targets"]
    # カテゴリ flag は presence-sensitive → schema default を持たない（§6e）。
    for k in ("manifold", "normals", "degenerate", "thin", "intersect"):
        assert "default" not in chk["properties"][k], k

    rep = json.loads(runner.invoke(app, ["help", "--command", "print-repair", "--json"]).output)[
        "schema"
    ]
    assert set(rep["properties"]) == {
        "targets",
        "regex",
        "make_manifold",
        "recalc_normals",
        "remove_degenerate",
        "make_single_user",
    }
    assert rep["required"] == ["targets"]
    # 修復フラグは presence-sensitive（default なし）/ make_single_user は通常フラグ（default あり）。
    for k in ("make_manifold", "recalc_normals", "remove_degenerate"):
        assert "default" not in rep["properties"][k], k
    assert rep["properties"]["make_single_user"]["default"] is False


def test_m8_print_export_discoverable():
    # M8 T8.5 の print-export が実装済み一覧に出る + スキーマ（read-only・format ENUM・scale 既定 1.0）
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "print-export" in by_name
    assert by_name["print-export"]["stability"] == "stable"  # 3シナリオは全 stable（DoD）
    assert by_name["print-export"]["mutates"] is False  # ファイルを書くだけ（シーンは変えない）
    schema = json.loads(runner.invoke(app, ["help", "--command", "print-export", "--json"]).output)[
        "schema"
    ]
    assert set(schema["properties"]) == {
        "targets",
        "regex",
        "format",
        "path",
        "ascii",
        "scale",
        "apply_modifiers",
    }
    assert set(schema["required"]) == {"targets", "format", "path"}
    assert schema["properties"]["format"]["enum"] == ["stl", "3mf"]
    # scale/ascii/apply_modifiers は通常の既定値を持つ（presence-sensitive ではない）。
    assert schema["properties"]["scale"]["default"] == 1.0
    assert schema["properties"]["ascii"]["default"] is False
    assert schema["properties"]["apply_modifiers"]["default"] is True


def test_print_export_bad_format_local_validation():
    # 不正な --format（obj 等）は送信前ローカル Pydantic 検証で exit 4
    res = runner.invoke(
        app, ["print-export", "--targets", "Cube", "--format", "obj", "--path", "out.stl", "--json"]
    )
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_m9_export_discoverable():
    # M9 T9.1 の export が実装済み一覧に出る + スキーマ（read-only・format ENUM 多形式・targets 任意）
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "export" in by_name
    assert by_name["export"]["stability"] == "stable"
    assert (
        by_name["export"]["mutates"] is False
    )  # ファイルを書くだけ（選択は save/restore で非破壊）
    schema = json.loads(runner.invoke(app, ["help", "--command", "export", "--json"]).output)[
        "schema"
    ]
    assert set(schema["properties"]) == {
        "format",
        "path",
        "targets",
        "regex",
        "use_selection",
        # P1-3: fbx 専用オプション（Unity 取込向け）。presence-sensitive なので default は持たない。
        "axis_forward",
        "axis_up",
        "scale",
        "apply_unit_scale",
        "embed_textures",
    }
    # targets は任意（--targets/--use-selection/シーン全体の3択）。required は format/path のみ。
    assert set(schema["required"]) == {"format", "path"}
    assert schema["properties"]["format"]["enum"] == ["obj", "fbx", "gltf", "stl", "3mf"]
    assert schema["properties"]["use_selection"]["default"] is False
    assert schema["properties"]["axis_forward"]["enum"] == ["X", "Y", "Z", "-X", "-Y", "-Z"]
    assert "default" not in schema["properties"]["scale"]  # presence-sensitive


def test_export_bad_format_local_validation():
    # 不正な --format（ply 等）は送信前ローカル Pydantic 検証で exit 4
    res = runner.invoke(app, ["export", "--format", "ply", "--path", "out.ply", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_export_whole_scene_no_targets_local_ok():
    # targets/use-selection 省略（シーン全体）はローカル検証を通過する（接続段で落ちる＝検証は通る）。
    res = runner.invoke(app, ["export", "--format", "stl", "--path", "out.stl", "--json"])
    assert res.exit_code != 4  # INVALID_PARAMS では落ちない（接続不能 exit 3 等になる）


def test_m9_import_discoverable():
    # M9 T9.2 の import が実装済み一覧に出る + スキーマ（mutates・format ENUM 多形式・format/path 必須）
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "import" in by_name
    assert by_name["import"]["stability"] == "stable"
    assert by_name["import"]["mutates"] is True  # シーンにオブジェクトを足す
    schema = json.loads(runner.invoke(app, ["help", "--command", "import", "--json"]).output)[
        "schema"
    ]
    assert set(schema["properties"]) == {"format", "path"}
    assert set(schema["required"]) == {"format", "path"}
    assert schema["properties"]["format"]["enum"] == ["obj", "fbx", "gltf", "stl", "3mf"]


def test_import_bad_format_local_validation():
    # 不正な --format（ply 等）は送信前ローカル Pydantic 検証で exit 4
    res = runner.invoke(app, ["import", "--format", "ply", "--path", "in.ply", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_m9_save_discoverable():
    # M9 T9.3 の save が実装済み一覧に出る + スキーマ（mutates・path 任意・backup 既定 True）
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "save" in by_name
    assert by_name["save"]["stability"] == "stable"
    assert by_name["save"]["mutates"] is True  # ファイル/セッション状態を変える副作用
    schema = json.loads(runner.invoke(app, ["help", "--command", "save", "--json"]).output)[
        "schema"
    ]
    assert set(schema["properties"]) == {"path", "backup"}
    assert "required" not in schema  # path/backup とも任意（backup は既定あり）
    assert schema["properties"]["backup"]["default"] is True


def test_save_no_args_local_ok():
    # path 省略（現在ファイルへ保存）はローカル検証を通過する（接続段で落ちる＝検証は通る）。
    res = runner.invoke(app, ["save", "--json"])
    assert res.exit_code != 4  # INVALID_PARAMS では落ちない（接続不能 exit 3 等になる）


def test_m9_open_discoverable():
    # M9 T9.4 の open が実装済み一覧に出る + スキーマ（mutates・path 必須・force 既定 False）。
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "open" in by_name
    assert by_name["open"]["stability"] == "stable"
    assert by_name["open"]["mutates"] is True  # シーン全体を置換する破壊的操作
    schema = json.loads(runner.invoke(app, ["help", "--command", "open", "--json"]).output)[
        "schema"
    ]
    assert set(schema["properties"]) == {"path", "force"}
    assert schema["required"] == ["path"]
    assert schema["properties"]["force"]["default"] is False


def test_open_missing_path_local_validation():
    # --path 必須。省略は送信前にローカルで弾かれる（Typer の必須オプション）。
    res = runner.invoke(app, ["open", "--json"])
    assert res.exit_code != 0


def test_open_existing_file_local_ok(tmp_path):
    # 妥当な params はローカル検証を通過する（接続段で落ちる＝検証は通る・exit 4 ではない）。
    f = tmp_path / "scene.blend"
    f.write_bytes(b"BLENDER-dummy")
    res = runner.invoke(app, ["open", "--path", str(f), "--json"])
    assert res.exit_code != 4


def test_m8_capture_discoverable():
    # 実地FB #1 の capture が実装済み一覧に出る + スキーマ（read-only・source ENUM 既定 viewport）
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "capture" in by_name
    assert by_name["capture"]["stability"] == "stable"
    assert by_name["capture"]["mutates"] is False  # 読み取り専用（save/restore で非破壊）
    schema = json.loads(runner.invoke(app, ["help", "--command", "capture", "--json"]).output)[
        "schema"
    ]
    assert set(schema["properties"]) == {"source", "width", "height", "camera"}
    assert "required" not in schema  # source は default あり・他は任意
    assert schema["properties"]["source"]["enum"] == ["viewport", "screen", "render"]
    assert schema["properties"]["source"]["default"] == "viewport"
    assert schema["properties"]["width"]["type"] == "integer"


def test_m8_undo_redo_discoverable():
    # 実地FB #3 の undo/redo が実装済み一覧に出る + スキーマ（mutates・steps INT 既定 1）。
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    for name in ("undo", "redo"):
        assert name in by_name, name
        assert by_name[name]["stability"] == "stable", name
        assert by_name[name]["mutates"] is True, name  # 状態を変える
        schema = json.loads(runner.invoke(app, ["help", "--command", name, "--json"]).output)[
            "schema"
        ]
        assert set(schema["properties"]) == {"steps"}, name
        assert "required" not in schema, name  # steps は default 1 で任意
        assert schema["properties"]["steps"]["type"] == "integer", name
        assert schema["properties"]["steps"]["default"] == 1, name


def test_capture_bad_source_local_validation():
    # 不正な --source は送信前ローカル Pydantic 検証で exit 4
    res = runner.invoke(app, ["capture", "--source", "bogus", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_straighten_bad_method_local_validation():
    # 不正な --method は送信前ローカル Pydantic 検証で exit 4
    res = runner.invoke(app, ["straighten", "--targets", "Cube", "--method", "bogus", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_straighten_bad_up_axis_local_validation():
    res = runner.invoke(
        app, ["straighten", "--targets", "Cube", "--method", "world-align", "--up-axis", "UP"]
    )
    assert res.exit_code == 4


def test_mesh_bad_op_local_validation():
    # 不正な --op は送信前ローカル Pydantic 検証で exit 4
    res = runner.invoke(app, ["mesh", "--op", "bogus", "--targets", "Cube", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_mesh_bad_offset_vec3_exit_input():
    # 不正な --offset（3要素でない）は送信前に exit 4
    res = runner.invoke(
        app, ["mesh", "--op", "extrude", "--targets", "Cube", "--offset", "1,2", "--json"]
    )
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_mesh_nonfinite_offset_exit_input():
    # nan/inf の offset は送信前に弾く（mesh を壊さない）
    res = runner.invoke(
        app, ["mesh", "--op", "extrude", "--targets", "Cube", "--offset", "inf,0,0", "--json"]
    )
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_modifier_bad_action_local_validation():
    res = runner.invoke(app, ["modifier", "--action", "bogus", "--targets", "Cube", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


# ---- 実地フィードバック #7: UTF-8 出力固定 / --target 別名 ----


def test_force_utf8_output_reconfigures_streams(monkeypatch):
    # _force_utf8_output が stdout/stderr を UTF-8 に張り替える（Windows CP932 化け対策）
    calls: list[str] = []

    class _FakeStream:
        def reconfigure(self, *, encoding: str) -> None:
            calls.append(encoding)

    monkeypatch.setattr(main_mod.sys, "stdout", _FakeStream())
    monkeypatch.setattr(main_mod.sys, "stderr", _FakeStream())
    main_mod._force_utf8_output()
    assert calls == ["utf-8", "utf-8"]


def test_force_utf8_output_skips_streams_without_reconfigure(monkeypatch):
    # reconfigure を持たない stream（リダイレクト/テスト capture 等）は黙ってスキップ＝例外なし
    class _NoReconfigure:
        pass

    monkeypatch.setattr(main_mod.sys, "stdout", _NoReconfigure())
    monkeypatch.setattr(main_mod.sys, "stderr", _NoReconfigure())
    main_mod._force_utf8_output()  # 例外を出さなければ OK


def test_target_singular_alias_registered():
    # --targets は単数別名 --target も受け付ける（エージェントが直感で打つ foot-gun 対策）。
    # help 出力のレンダリング（rich・端末幅依存）に頼らず、登録済みの click オプション名を直接検証する。
    cmd = get_command(app)
    object_info = cmd.commands["object-info"]  # type: ignore[attr-defined]
    targets_param = next(p for p in object_info.params if p.name == "targets")
    assert "--targets" in targets_param.opts
    assert "--target" in targets_param.opts


def test_modifier_arbitrary_type_passes_local_validation():
    # P2-3: type は STR 化＝ローカル（Pydantic）では弾かない。実在検証はサーバの rna 能力検出。
    # サーバ不在の CliRunner では CONNECTION(exit 3) に到達する＝送信前検証を通過した証拠。
    res = runner.invoke(
        app, ["modifier", "--action", "add", "--targets", "Cube", "--type", "BEVEL", "--json"]
    )
    assert res.exit_code == 3, res.output


def test_busy_rendering_maps_to_timeout_pending_exit():
    # BUSY_RENDERING（レンダ中の未受理・retryable）は exit 2 = TIMEOUT_PENDING へ写像する（M10 T10.2・R-B）。
    from bli_core.errors import ErrorCategory, ErrorCode, ExitCode

    err = {
        "message": ErrorCode.BUSY_RENDERING,
        "data": {"category": ErrorCategory.ENVIRONMENT, "retryable": True},
    }
    assert main_mod._exit_code_for(err) == ExitCode.TIMEOUT_PENDING


def test_exit_code_for_auth_failed_is_connection():
    # AUTH_FAILED は kind 判定で exit 3（接続不能の一種・設計レビュー 2026-07-11 B1）。
    from bli_core.errors import ErrorCode, ExitCode

    err = {"message": ErrorCode.AUTH_FAILED, "data": {"category": "ENVIRONMENT"}}
    assert main_mod._exit_code_for(err) == ExitCode.CONNECTION


def test_exit_code_for_protocol_version_mismatch_is_connection():
    # PROTOCOL_VERSION_MISMATCH も kind 判定で exit 3（同上）。
    from bli_core.errors import ErrorCode, ExitCode

    err = {"message": ErrorCode.PROTOCOL_VERSION_MISMATCH, "data": {"category": "ENVIRONMENT"}}
    assert main_mod._exit_code_for(err) == ExitCode.CONNECTION


def test_exit_code_for_retryable_true_beats_kind_is_timeout_pending():
    # retryable=True が真実源＝未知の kind でも retryable なら exit 2（サーバの kind 追加に追従不要）。
    from bli_core.errors import ExitCode

    err = {"message": "SOME_FUTURE_KIND", "data": {"category": "ENVIRONMENT", "retryable": True}}
    assert main_mod._exit_code_for(err) == ExitCode.TIMEOUT_PENDING


def test_exit_code_for_invalid_params_is_input():
    # kind==INVALID_PARAMS は exit 4。
    from bli_core.errors import ErrorCode, ExitCode

    err = {"message": ErrorCode.INVALID_PARAMS, "data": {"category": "USER_INPUT"}}
    assert main_mod._exit_code_for(err) == ExitCode.INPUT


def test_exit_code_for_user_input_category_is_input():
    # kind が INVALID_PARAMS でなくても category==USER_INPUT なら exit 4（E_TARGET_NOT_FOUND 等）。
    from bli_core.errors import ErrorCode, ExitCode

    err = {"message": ErrorCode.E_TARGET_NOT_FOUND, "data": {"category": "USER_INPUT"}}
    assert main_mod._exit_code_for(err) == ExitCode.INPUT


def test_exit_code_for_default_is_failure():
    # 上記いずれにも該当しない業務エラーは確定失敗として exit 1。
    from bli_core.errors import ErrorCode, ExitCode

    err = {
        "message": ErrorCode.E_OPERATOR,
        "data": {"category": "PRECONDITION", "retryable": False},
    }
    assert main_mod._exit_code_for(err) == ExitCode.FAILURE


def test_exit_code_for_missing_data_defaults_to_failure():
    # data が無い/dict でない防御（retryable/category を読めない）＝確定失敗 exit 1 に倒す。
    from bli_core.errors import ExitCode

    assert main_mod._exit_code_for({"message": "E_SOMETHING"}) == ExitCode.FAILURE
    assert main_mod._exit_code_for({"message": "E_SOMETHING", "data": None}) == ExitCode.FAILURE
    assert main_mod._exit_code_for({"message": "E_SOMETHING", "data": "not-a-dict"}) == (
        ExitCode.FAILURE
    )


def test_watchdog_suffix_responsive_is_empty():
    # 応答中 / watchdog 情報なし は注記を付けない（M10 T10.3）。
    assert main_mod._watchdog_suffix({"watchdog": {"responsive": True}}) == ""
    assert main_mod._watchdog_suffix({}) == ""
    assert main_mod._watchdog_suffix({"watchdog": None}) == ""


def test_watchdog_suffix_unresponsive_notes_age():
    # 応答なしのときだけ経過秒つきで注記する（実行は継続中＝固まっている可視化）。
    s = main_mod._watchdog_suffix({"watchdog": {"responsive": False, "last_pump_age": 99.0}})
    assert "応答なし" in s
    assert "99s" in s
    assert "実行は継続中" in s


def _rs(state, watchdog, job_id="job-x"):
    """request-status の (result, hello) を組み立てるテストヘルパ。"""
    data = {"id": job_id, "known": True, "state": state, "result": None, "watchdog": watchdog}
    if state == "DONE":
        data["result"] = {"result": {"success": True, "operation": "import", "data": {"count": 0}}}
    return ({"success": True, "operation": "request-status", "data": data}, {})


def test_await_job_warns_once_on_unresponsive_main_thread(monkeypatch, capsys):
    # auto-wait/job-wait ポーリング中に watchdog が unresponsive を返したら一度だけ stderr へ通知する
    # （M10 T10.3・P1: 既定の auto-wait でも固まりを可視化する）。request-status は lock-free。
    unresp = {"responsive": False, "unresponsive_since": 1.0, "last_pump_age": 99.0}
    polls = [
        _rs("RUNNING", unresp),
        _rs("RUNNING", unresp),  # 2回目も unresponsive だが通知は1回だけ
        _rs("DONE", {"responsive": True, "unresponsive_since": None}),
    ]
    calls = {"n": 0}

    def fake_call(method, params=None, **kwargs):
        i = calls["n"]
        calls["n"] += 1
        return polls[min(i, len(polls) - 1)]

    monkeypatch.setattr(main_mod.client, "call", fake_call)
    monkeypatch.setattr(
        main_mod.runtime, "JOB_POLL_INTERVAL", 0.0
    )  # ポーリング待ちを潰して即進める
    result = main_mod._await_job("job-x", json_out=True, port=None, timeout=30)
    err = capsys.readouterr().err
    assert result.get("operation") == "import"
    assert "メインスレッドが応答していません" in err
    assert (
        err.count("メインスレッドが応答していません") == 1
    )  # 通知は一度だけ（毎ポーリングで出さない）


def test_modifier_bad_operation_local_validation():
    # 不正な --operation は送信前ローカル Pydantic 検証で exit 4
    res = runner.invoke(
        app,
        ["modifier", "--action", "add", "--targets", "Cube", "--operation", "BOGUS", "--json"],
    )
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_transform_bad_vec3_exit_input():
    # 不正な --location（3要素でない）は送信前に exit 4
    res = runner.invoke(app, ["transform", "--targets", "Cube", "--location", "1,2", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_transform_bad_mode_local_validation():
    # 不正な --mode は送信前ローカル Pydantic 検証で exit 4
    res = runner.invoke(app, ["transform", "--targets", "Cube", "--mode", "bogus", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_transform_nonfinite_vec3_exit_input():
    # nan/inf は送信前に弾く（matrix を壊さない）
    for bad in ("nan,0,0", "inf,0,0"):
        res = runner.invoke(app, ["transform", "--targets", "Cube", "--location", bad, "--json"])
        assert res.exit_code == 4, bad
        assert "INVALID_PARAMS" in res.output


def test_apply_transform_flags_have_no_schema_default():
    # presence-sensitive な BOOL フラグは schema に default を出さない（Codex P2）。
    # default:false を広告すると、既定埋めクライアントが全 false を送ってしまう。
    res = runner.invoke(app, ["help", "--command", "apply-transform", "--json"])
    assert res.exit_code == 0
    props = json.loads(res.output)["schema"]["properties"]
    for ch in ("location", "rotation", "scale"):
        assert "default" not in props[ch], (ch, props[ch])


def test_help_all_json():
    res = runner.invoke(app, ["help", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert "set-origin" in data["commands"]
    assert data["commands"]["set-origin"]["title"] == "set-origin"


def test_help_one_json():
    res = runner.invoke(app, ["help", "--command", "set-origin", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["command"]["name"] == "set-origin"
    assert set(data["schema"]["required"]) == {"targets", "to"}


def test_help_unknown_command_exit_input():
    res = runner.invoke(app, ["help", "--command", "does-not-exist", "--json"])
    assert res.exit_code == 4


def test_local_validation_rejects_bad_enum_before_connect():
    # 不正な --to は送信前に exit 4（接続を試みない）
    res = runner.invoke(app, ["set-origin", "--targets", "Cube", "--to", "bogus", "--json"])
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


def test_schema_hash_matches_core():
    # CLI が出す schema_hash は bli-core の算出値と一致する
    from bli_core.commands import load_definitions
    from bli_core.schema import schema_hash

    res = runner.invoke(app, ["list-commands", "--json"])
    assert json.loads(res.output)["schema_hash"] == schema_hash(load_definitions())


# M11 T11.1 で exec-python が implemented=True 化＝実装済みコマンドだけが残った。発見系の
# 「未実装は広告しない・--all で出す」フィルタ機構自体は維持されるため、合成の未実装コマンドを
# 一時登録して機構を検証する（実コマンドの実装状況に依存しないテストにする）。
_FAKE_UNIMPL = "fake-unimpl-cmd"


def _register_fake_unimplemented():
    from bli_core.commands import COMMANDS, command, load_definitions

    load_definitions()
    if _FAKE_UNIMPL not in COMMANDS:
        command(_FAKE_UNIMPL, "テスト用の未実装コマンド", implemented=False)


def _unregister_fake_unimplemented():
    from bli_core.commands import COMMANDS

    COMMANDS.pop(_FAKE_UNIMPL, None)


def test_exec_python_is_discoverable_now():
    # M11: exec-python は implemented=True ＝発見系に出る（help --command も implemented:True）。
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "exec-python" in by_name
    res = json.loads(runner.invoke(app, ["help", "--command", "exec-python", "--json"]).output)
    assert res["command"]["implemented"] is True


def test_list_commands_excludes_unimplemented_by_default():
    _register_fake_unimplemented()
    try:
        data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
        names = {c["name"] for c in data["commands"]}
        assert _FAKE_UNIMPL not in names
        assert "set-origin" in names
        assert "exec-python" in names  # M11 T11.1 で実装済みになった
    finally:
        _unregister_fake_unimplemented()


def test_list_commands_all_includes_unimplemented():
    _register_fake_unimplemented()
    try:
        data = json.loads(runner.invoke(app, ["list-commands", "--all", "--json"]).output)
        by_name = {c["name"]: c for c in data["commands"]}
        assert _FAKE_UNIMPL in by_name
        assert by_name[_FAKE_UNIMPL]["implemented"] is False
    finally:
        _unregister_fake_unimplemented()


def test_help_excludes_unimplemented_by_default():
    _register_fake_unimplemented()
    try:
        data = json.loads(runner.invoke(app, ["help", "--json"]).output)
        assert _FAKE_UNIMPL not in data["commands"]
        data_all = json.loads(runner.invoke(app, ["help", "--all", "--json"]).output)
        assert _FAKE_UNIMPL in data_all["commands"]
    finally:
        _unregister_fake_unimplemented()


def test_help_command_introspects_unimplemented():
    # 個別 introspection は未実装でも可（implemented=False を明示）
    _register_fake_unimplemented()
    try:
        res = runner.invoke(app, ["help", "--command", _FAKE_UNIMPL, "--json"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert data["command"]["implemented"] is False
    finally:
        _unregister_fake_unimplemented()


def _fake_timeout_error():
    from bli import client as cli_client

    return cli_client.RpcRemoteError(
        {
            "message": "TIMEOUT",
            "data": {
                "category": "ENVIRONMENT",
                "userVisibleSymptom": "タイムアウト",
                "retryable": True,
            },
        }
    )


def test_timeout_exposes_supplied_id(monkeypatch):
    # --id 指定時: TIMEOUT(exit2) で その id を提示する
    from bli import client as cli_client

    def fake_call(method, params=None, *, port=None, request_id=None, timeout=None):
        raise _fake_timeout_error()

    monkeypatch.setattr(cli_client, "call", fake_call)
    res = runner.invoke(
        app, ["set-origin", "--targets", "Cube", "--to", "geometry", "--id", "my-id", "--json"]
    )
    assert res.exit_code == 2  # TIMEOUT_PENDING
    payload = json.loads(res.output)
    assert payload["kind"] == "TIMEOUT"
    assert payload["request_id"] == "my-id"


def test_timeout_exposes_generated_id(monkeypatch):
    # --id 省略時: CLI が生成した id を必ず提示する（後追い可能にする）
    from bli import client as cli_client

    seen = {}

    def fake_call(method, params=None, *, port=None, request_id=None, timeout=None):
        seen["id"] = request_id  # _rpc が生成した id が渡る
        raise _fake_timeout_error()

    monkeypatch.setattr(cli_client, "call", fake_call)
    res = runner.invoke(app, ["set-origin", "--targets", "Cube", "--to", "geometry", "--json"])
    assert res.exit_code == 2
    payload = json.loads(res.output)
    assert payload["request_id"]  # 非空
    assert payload["request_id"] == seen["id"]  # 送信に使った id と一致


def test_ping_timeout_maps_exit2_with_id(monkeypatch):
    # ping も実機では Dispatcher 経由 → TIMEOUT は exit2 + id 提示（_rpc と同じ写像）
    from bli import client as cli_client

    seen = {}

    def fake_call(method, params=None, *, port=None, request_id=None, timeout=None):
        seen["id"] = request_id
        raise _fake_timeout_error()

    monkeypatch.setattr(cli_client, "call", fake_call)
    res = runner.invoke(app, ["ping", "--json"])
    assert res.exit_code == 2  # 旧実装では exit1 / id なしだった
    payload = json.loads(res.output)
    assert payload["kind"] == "TIMEOUT"
    assert payload["request_id"] == seen["id"]


def test_m10_heavy_metadata_discoverable():
    # M10: heavy コマンドと mesh の heavy_ops が list-commands に出る（非同期 job 発見用）。
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    for name in ("import", "export", "print-check", "print-repair"):
        assert by_name[name]["is_heavy"] is True, name
    assert by_name["mesh"]["is_heavy"] is False
    assert by_name["mesh"]["heavy_ops"] == ["boolean", "decimate"]
    # job-status / job-wait（CLI ポーリング・request-status 上のシュガー）も発見できる。
    assert "job-status" in by_name
    assert "job-wait" in by_name
