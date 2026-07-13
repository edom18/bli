"""material ハンドラ（PBR/テクスチャ含む・ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

from typing import Any

from ..handlers import ServerInfo
from ._shared import _check_mode, _command, _guard_shared_mesh, _ok, _require_input, _validate

# material create 専用の presence-sensitive パラメータ（P2-3 で PBR/テクスチャへ拡張・G5）。
# 他 action で渡されたら silent ignore せず弾く（color の従来ガードの一般化）。
_MATERIAL_CREATE_ONLY_PARAMS: tuple[str, ...] = (
    "color",
    "metallic",
    "roughness",
    "emission",
    "emission_strength",
    "alpha",
    "texture",
    "pack_texture",
)


def _material(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("material")
    _validate(cmd, params)
    action = str(params["action"])
    targets = params.get("targets")
    name = params.get("name")
    color = params.get("color")

    # 条件付き必須を bpy 到達前に検証する（schema は action 非依存で targets/name 任意）。
    _require_input(
        targets is not None,
        symptom="対象(--targets)が必要です",
        remediation="--targets を指定してください",
    )
    if action in ("assign", "create"):
        _require_input(
            name is not None,
            symptom=f"{action} には --name が必要です",
            remediation="--name を指定してください",
        )
    # create 専用 param（color/PBR/テクスチャ）は他 action で渡されたら弾く。
    given_create_only = [k for k in _MATERIAL_CREATE_ONLY_PARAMS if params.get(k) is not None]
    _require_input(
        action == "create" or not given_create_only,
        symptom=(
            " / ".join("--" + k.replace("_", "-") for k in given_create_only)
            + " は create のときのみ有効です"
        ),
        remediation="create で使うか該当オプションを外してください",
    )
    # 数値範囲と依存関係を bpy 到達前に検証する（rna の silent clamp に頼らない・§6e）。
    for key in ("metallic", "roughness", "alpha"):
        if params.get(key) is not None:
            _require_input(
                0.0 <= float(params[key]) <= 1.0,
                symptom=f"--{key} は 0.0〜1.0 で指定してください（指定: {params[key]}）",
                remediation=f"--{key} を 0.0〜1.0 にしてください",
            )
    if params.get("emission_strength") is not None:
        _require_input(
            params.get("emission") is not None,
            symptom="--emission-strength は --emission と併用してください",
            remediation="--emission r,g,b,a も指定してください",
        )
        _require_input(
            float(params["emission_strength"]) >= 0.0,
            symptom=f"--emission-strength は 0 以上で指定してください（指定: {params['emission_strength']}）",
            remediation="--emission-strength を 0 以上にしてください",
        )
    if params.get("pack_texture") is not None:
        _require_input(
            params.get("texture") is not None,
            symptom="--pack-texture は --texture と併用してください",
            remediation="--texture <path> も指定してください",
        )
    texture = params.get("texture")
    if texture is not None:
        import os

        # CLI/Blender の CWD 差を吸収するため abspath 正規化（import と同じ流儀）。
        texture = os.path.abspath(str(texture))
        _require_input(
            os.path.isfile(texture),
            symptom=f"テクスチャ画像が見つかりません: {texture}",
            remediation="存在する画像ファイルのパスを指定してください",
        )

    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(targets), regex=bool(params.get("regex", False)))
    gateway.require_material_support(obj)

    if action == "list":
        data = {"name": obj.name, "action": "list", "materials": gateway.list_object_materials(obj)}
        return _ok("material", data, fingerprint=gateway.material_fingerprint(obj))

    # assign は既存マテリアルを **状態変更（単一ユーザ化）の前に** 解決する。見つからない名で
    # 先に mesh を単一ユーザ化してから失敗すると、エラー後にシーン状態が変わる（Codex P2）。
    # 未発見エラーは gateway.require_material に集約（require_single と同じ流儀。設計レビュー P2）。
    #
    # 共有 mesh ガード（Codex P2-A）は書き込み先が DATA slot / 空スロット append のときのみ
    # （OBJECT リンク slot は object 限定書き込みで共有 mesh を触らない＝false-positive 回避）。
    # ガード（--make-single-user 時は不可逆な単一ユーザ化）は **失敗し得る処理を全て通過した後**
    # に実行する＝失敗時に mesh を分離しない: assign はマテリアル解決後 / create は P2-3 で
    # texture・pack・Principled 欠如により失敗し得るため create_material 成功後（レビュー R2-A）。
    mat = None
    extras: dict[str, Any] = {}
    if action == "assign":  # 既存マテリアルのみ。無ければ E_TARGET_NOT_FOUND＝create と責務分離
        mat = gateway.require_material(str(name))
        if gateway.material_write_touches_mesh_data(obj):
            _guard_shared_mesh(gateway, obj, params)
    else:  # create
        mat, extras = gateway.create_material(
            str(name),
            list(color) if color is not None else None,
            metallic=float(params["metallic"]) if params.get("metallic") is not None else None,
            roughness=float(params["roughness"]) if params.get("roughness") is not None else None,
            emission=list(params["emission"]) if params.get("emission") is not None else None,
            emission_strength=(
                float(params["emission_strength"])
                if params.get("emission_strength") is not None
                else None
            ),
            alpha=float(params["alpha"]) if params.get("alpha") is not None else None,
            texture_path=texture,
            pack_texture=bool(params.get("pack_texture", False)),
        )
        try:
            if gateway.material_write_touches_mesh_data(obj):
                _guard_shared_mesh(gateway, obj, params)
        except BaseException:
            # ガード失敗（--make-single-user なしの共有 mesh 等）で作りたて material/image を
            # 残さない（レビュー R2-A）。JsonRpcError 限定にすると想定外例外（MemoryError 等）で
            # リークする＝gateway.create_material/_apply_material_extras と同じ捕捉幅に揃え、
            # 必ず再送出する（methods.md の無条件アトミック性・レビュー R3-A・4 finder 収束）。
            gateway.discard_created_material(mat, extras)
            raise

    slot = gateway.assign_material(obj, mat)
    gateway.push_undo(f"material {action}")
    data = {
        "name": obj.name,
        "action": action,
        "material": mat.name,
        "slot": slot,
        "materials": gateway.list_object_materials(obj),
        **extras,
    }
    return _ok("material", data, fingerprint=gateway.material_fingerprint(obj))
