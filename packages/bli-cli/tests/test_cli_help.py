"""help / list-commands（SSOT生成・ローカル完結）と送信前ローカル検証の CLI テスト（L1）。

addon に接続しない経路のみ。bad な入力は client.call より前に exit 4 で弾けること、
help/list-commands が SSOT から schema_hash 付きで生成されることを検証する。
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

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
    assert set(schema["properties"]) == {"type", "regex"}
    assert "required" not in schema  # type/regex は任意


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
    assert set(schema["properties"]) == {"targets", "linked", "count", "offset"}
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
    assert set(schema["properties"]) == {"action", "targets", "name", "color", "make_single_user"}
    assert schema["required"] == ["action"]  # targets/name は action 別に ops 側で必須化
    color = schema["properties"]["color"]
    assert color["type"] == "array" and color["minItems"] == 4 and color["maxItems"] == 4


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
    assert {"action", "targets", "type", "name", "axis", "levels", "thickness", "ratio"} <= set(
        schema["properties"]
    )
    assert set(schema["required"]) == {"action", "targets"}
    # type/operation/axis は ENUM（choices が出る）、levels は INT
    assert schema["properties"]["type"]["enum"] == [
        "MIRROR",
        "SUBSURF",
        "SOLIDIFY",
        "DECIMATE",
        "BOOLEAN",
    ]
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
        "method",
        "up_axis",
        "axis",
        "bake_rotation",
        "make_single_user",
    }
    assert set(schema["required"]) == {"targets", "method"}
    assert schema["properties"]["method"]["enum"] == ["reset", "world-align", "pca", "floor"]
    assert schema["properties"]["up_axis"]["enum"] == ["+Z", "-Z", "+Y", "-Y", "+X", "-X"]
    assert schema["properties"]["axis"]["enum"] == ["X", "Y", "Z"]
    # axis は world-align 専用で presence-sensitive → schema default を持たない（§6e）。
    assert "default" not in schema["properties"]["axis"]
    # up_axis は既定 +Z を持つ（非 presence-sensitive・spec『既定 +Z』）。
    assert schema["properties"]["up_axis"]["default"] == "+Z"


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


def test_modifier_bad_type_local_validation():
    res = runner.invoke(
        app, ["modifier", "--action", "add", "--targets", "Cube", "--type", "BOGUS", "--json"]
    )
    assert res.exit_code == 4
    assert "INVALID_PARAMS" in res.output


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


def test_list_commands_excludes_unimplemented_by_default():
    # 発見系は未実装コマンド（exec-python）を広告しない（transform は M6 で実装済み）
    data = json.loads(runner.invoke(app, ["list-commands", "--json"]).output)
    names = {c["name"] for c in data["commands"]}
    assert "exec-python" not in names
    assert "set-origin" in names
    assert "transform" in names  # M6 T6.1 で実装済みになった


def test_list_commands_all_includes_unimplemented():
    data = json.loads(runner.invoke(app, ["list-commands", "--all", "--json"]).output)
    by_name = {c["name"]: c for c in data["commands"]}
    assert "exec-python" in by_name
    assert by_name["exec-python"]["implemented"] is False


def test_help_excludes_unimplemented_by_default():
    data = json.loads(runner.invoke(app, ["help", "--json"]).output)
    assert "exec-python" not in data["commands"]
    data_all = json.loads(runner.invoke(app, ["help", "--all", "--json"]).output)
    assert "exec-python" in data_all["commands"]


def test_help_command_introspects_unimplemented():
    # 個別 introspection は未実装でも可（implemented=False を明示）
    res = runner.invoke(app, ["help", "--command", "exec-python", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["command"]["implemented"] is False


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
