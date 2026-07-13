"""straighten（直立補正）ハンドラ（ops/ 分割 P2-4）。

元 ops.py の該当セクションをそのまま移設（挙動変更なし）。`_is_nonzero_vec` は元 ops.py では
共通ヘルパ節（`_require_input` 等の並び）にあったが、align-vector（straighten 専用）でしか
使われないため単一利用先のこちらへ移設する（P2-4 分割方針）。
"""

from __future__ import annotations

from typing import Any

from ..handlers import ServerInfo
from ._shared import _check_mode, _command, _guard_shared_mesh, _ok, _require_input, _validate


def _is_nonzero_vec(vec: Any) -> bool:
    """ベクトルが（ほぼ）ゼロでないか（純Python・bpy 到達前のゼロベクトル弾き用）。

    schema 検証済みで vec は有限値の3要素。正規化が不定になるゼロ近傍を弾く（align-vector）。
    """
    return sum(float(c) * float(c) for c in vec) > 1e-12


def _straighten(params: dict[str, Any], info: ServerInfo) -> dict[str, Any]:
    cmd = _command("straighten")
    _validate(cmd, params)
    method = str(params["method"])
    # --- op 専用 param の presence ガード（別 method に渡されたら silent ignore せず弾く・§6e）---
    # axis は world-align/reference（合わせる local 軸）と angle（回転 world 軸）で有効。
    if "axis" in params:
        _require_input(
            method in ("world-align", "reference", "angle"),
            symptom="--axis は world-align / reference / angle のときのみ有効です",
            remediation="該当 method で使うか --axis を外してください",
        )
    # up_hint は pca 専用（符号決定の切替）。
    if "up_hint" in params:
        _require_input(
            method == "pca",
            symptom="--up-hint は pca のときのみ有効です",
            remediation="pca で使うか --up-hint を外してください",
        )
    # degrees は angle 専用。
    if "degrees" in params:
        _require_input(
            method == "angle",
            symptom="--degrees は angle のときのみ有効です",
            remediation="angle で使うか --degrees を外してください",
        )
    # from_dir/to_dir は align-vector 専用。
    for key in ("from_dir", "to_dir"):
        if key in params:
            _require_input(
                method == "align-vector",
                symptom=f"--{key.replace('_', '-')} は align-vector のときのみ有効です",
                remediation=f"align-vector で使うか --{key.replace('_', '-')} を外してください",
            )
    # reference/ref_axis は reference 専用。
    for key in ("reference", "ref_axis"):
        if key in params:
            _require_input(
                method == "reference",
                symptom=f"--{key.replace('_', '-')} は reference のときのみ有効です",
                remediation=f"reference で使うか --{key.replace('_', '-')} を外してください",
            )

    # --- method 別の必須 param（schema は method 非依存なのでここで検証・bpy 到達前）---
    if method == "angle":
        _require_input(
            "axis" in params and "degrees" in params,
            symptom="angle には --axis（回転軸）と --degrees（角度）が必要です",
            remediation="--axis X|Y|Z と --degrees を指定してください",
        )
    elif method == "align-vector":
        _require_input(
            "from_dir" in params,
            symptom="align-vector には --from-dir（揃えたい現在の方向）が必要です",
            remediation="--from-dir x,y,z を指定してください（--to-dir 省略時は up へ）",
        )
        # ゼロベクトルは normalized が不定で整列を決定できない（bpy 到達前に弾く）。
        _require_input(
            _is_nonzero_vec(params["from_dir"]),
            symptom="--from-dir がゼロベクトルです",
            remediation="長さのある方向ベクトルを指定してください",
        )
        if "to_dir" in params:
            _require_input(
                _is_nonzero_vec(params["to_dir"]),
                symptom="--to-dir がゼロベクトルです",
                remediation="長さのある方向ベクトルを指定してください（省略時は up）",
            )
    elif method == "reference":
        _require_input(
            "reference" in params,
            symptom="reference には --reference（基準オブジェクト名）が必要です",
            remediation="--reference <obj> を指定してください",
        )

    dry = bool(params.get("dry_run", False))
    bake = bool(params.get("bake_rotation", False))
    # dry-run（何も書き込まない）と bake（mesh 焼き込み）は矛盾。silent ignore せず弾く（§6e・
    # axis/up_hint と同流儀）。以降 bake が真なら dry は偽が保証される。
    _require_input(
        not (dry and bake),
        symptom="--dry-run と --bake-rotation は同時指定できません",
        remediation="計画確認は --dry-run のみ、焼き込みは --bake-rotation のみで実行してください",
    )

    from .. import gateway  # lazy: bpy 依存

    _check_mode(cmd, gateway.current_mode())
    obj = gateway.require_single(str(params["targets"]), regex=bool(params.get("regex", False)))
    # method 別の前提（非対応型は INTERNAL でなく E_PRECONDITION）。
    reference_obj = None
    if method == "pca":
        gateway.require_mesh(obj)  # 頂点分布が必要
    elif method == "floor":
        gateway.require_geometry(obj)  # bbox が必要
    elif method == "reference":
        # 基準オブジェクトを解決（任意の型でよい・matrix_world だけ使う）。自己参照は弾く。
        reference_obj = gateway.require_single(str(params["reference"]))
        _require_input(
            reference_obj.name != obj.name,
            symptom="--reference に対象自身は指定できません",
            remediation="対象とは別のオブジェクトを --reference に指定してください",
        )

    if bake:  # dry と排他済み（上で弾く）→ bake は常に実適用
        # bake は回転を mesh データへ焼き込む破壊的操作。焼き込み先（mesh）と共有 mesh ガードを
        # **補正（obj 回転）より前**に検証する（失敗時に obj を回転させたまま残さない・§6e）。
        gateway.require_mesh(obj)
        _guard_shared_mesh(gateway, obj, params)

    data = gateway.straighten_object(
        obj,
        method=method,
        up_axis=str(params.get("up_axis", "+Z")),
        axis=str(params["axis"]) if "axis" in params else None,
        up_hint=str(params.get("up_hint", "auto")),
        degrees=float(params["degrees"]) if "degrees" in params else None,
        from_dir=params.get("from_dir"),
        to_dir=params.get("to_dir"),
        reference_obj=reference_obj,
        # ref_axis 省略時は up_axis（参照側の「up」軸方向へ合わせるのが既定）。
        ref_axis=str(params.get("ref_axis", params.get("up_axis", "+Z"))),
        # dry-run は push_undo しない・bake も apply の undo に委ねるため、どちらも message なし。
        message=None if (bake or dry) else f"straighten {method}",
        dry_run=dry,
    )
    if bake:
        # 回転を mesh へ焼き込む（apply-transform rotation 経路を再利用）。焼き込み後は object
        # 回転が 0 になり頂点が回転する。共有ガードは上で実施済み（undo 境界は apply が作る）。
        baked = gateway.apply_transform(
            obj, location=False, rotation=True, scale=False, message=f"straighten {method} bake"
        )
        data["baked"] = True
        data["rotation_euler_deg"] = baked["rotation_euler_deg"]
    else:
        data["baked"] = False
    # fingerprint は操作の本質に合わせる（§6e）。bake は回転を mesh データへ焼き込む（頂点座標が
    # 変わる）→ 法線込みの mesh_fingerprint で頂点数不変でも幾何変化を検出する（mesh 編集系と一貫・
    # require_mesh 通過後で MESH 限定が保証される）。非 bake / dry-run は object transform のみ
    # （dry-run は復元済みで不変）→ bbox 込みの object_fingerprint（set-origin/transform と同流儀）。
    fp = gateway.mesh_fingerprint(obj) if bake else gateway.object_fingerprint(obj)
    return _ok("straighten", data, fingerprint=fp)
