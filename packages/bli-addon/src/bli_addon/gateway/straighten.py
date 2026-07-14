"""BpyGateway 直立補正（straighten: reset/world-align/pca/floor/angle/align-vector/reference・gateway/ 分割 P2-4）。

元 gateway.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

import math
from typing import Any

import bpy  # type: ignore

from bli_core.errors import ErrorCode

from .core import _op_error, push_undo
from .objects import _rotation_euler_deg, world_bbox

# ---- 直立補正（M8 T8.2 / straighten・シナリオ2）----
#
# メソッド: reset（回転を identity に）/ world-align（指定 local 軸を world up へ最小回転で合わせる）
# / pca（頂点分布の最大分散軸を up へ）/ floor（up 方向の最下点を接地）/ angle（world 軸まわりに
# 指定角回転）/ align-vector（from_dir を to_dir へ最小回転）/ reference（参照 obj の軸方向へ合わせる）。
# floor 以外は **object 回転のみ**変更（mesh 非破壊・共有 mesh でも安全）。floor は平行移動のみ。
# pca は mesh 頂点が必要。angle/align-vector/reference は基準指定（エージェント算出の補正を安全に適用・
# transform 迂回の解消・実地フィードバック #4）。`--bake-rotation` の mesh 焼き込みは呼び出し側（ops）が
# apply_transform 経路（共有ガード付き）で行う。研究 §E4 で 5.0.1/4.4.3 確認済み。
# matrix_world は読み取り前に view_layer.update() で最新化する（background での stale 対策・§E4）。

_AXIS_VECTORS: dict[str, tuple[float, float, float]] = {
    "+X": (1.0, 0.0, 0.0),
    "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0),
    "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0),
    "-Z": (0.0, 0.0, -1.0),
}
_LOCAL_AXIS_LETTERS = ("X", "Y", "Z")
_LOCAL_AXIS_UNIT: dict[str, tuple[float, float, float]] = {
    "X": (1.0, 0.0, 0.0),
    "Y": (0.0, 1.0, 0.0),
    "Z": (0.0, 0.0, 1.0),
}
# pca: この値以下の最大固有値（分散）は主成分を決められない（点が一致/直線退化）。
_PCA_MIN_VARIANCE = 1e-12
# pca: 原点→重心 の射影がこの閾値以下なら符号が不定（中心対称）→ 正準符号でtie-break。
_PCA_SIGN_EPS = 1e-9
# rotation_difference が anti-parallel（真逆）で軸不定になる閾値。
_ANTIPARALLEL_EPS = 1e-9


def require_geometry(obj: Any) -> None:
    """bbox を持たない型（EMPTY/LIGHT/CAMERA 等）は E_PRECONDITION で弾く（floor 用）。

    require_mesh/require_material_support と同じ流儀。world_bbox は退化（全隅同一）を None で
    返すので、それを接地不能の判定に使う（番号分岐せず値で判定）。
    """
    if world_bbox(obj) is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"接地補正にはジオメトリ（bbox）が必要です（type={obj.type}）",
        )


def _reset_rotation(obj: Any) -> None:
    """rotation_mode に依らず回転を identity にする（QUATERNION/AXIS_ANGLE 対応）。"""
    rmode = obj.rotation_mode
    if rmode == "QUATERNION":
        obj.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    elif rmode == "AXIS_ANGLE":
        obj.rotation_axis_angle = (0.0, 0.0, 0.0, 1.0)  # angle=0 → identity（軸は任意）
    else:
        obj.rotation_euler = (0.0, 0.0, 0.0)


def _local_axis_world(obj: Any, signed_axis: str) -> Any:
    """signed local 軸（"+Z"/"-X" 等）の world 方向（正規化・scale 除去）を返す。"""
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    sign = -1.0 if signed_axis[0] == "-" else 1.0
    base = Vector(_LOCAL_AXIS_UNIT[signed_axis[-1]]) * sign
    return (obj.matrix_world.to_quaternion() @ base).normalized()


def _apply_world_rotation(obj: Any, delta_quat: Any) -> None:
    """delta_quat を world 回転へ前合成し、原点・スケール不変で書き戻す（§E4）。

    decompose→LocRotScale で loc/scale を保ったまま回転だけ差し替える。親付きでも matrix_world
    setter が親逆行列を考慮するため world 空間で正しく整列する。
    """
    from mathutils import Matrix  # type: ignore  # lazy: bpy 依存

    loc, rot, scale = obj.matrix_world.decompose()
    obj.matrix_world = Matrix.LocRotScale(loc, delta_quat @ rot, scale)


def _rotation_to(cur: Any, target: Any) -> Any:
    """cur を target へ重ねる最小回転 quaternion（anti-parallel を決定的に扱う）。

    `Vector.rotation_difference` は cur と target が **真逆**のとき軸が不定（垂直な任意軸まわり
    180°）で版/数値依存に揺れる。整列軸（cur→target）は乗るが直交2軸（見た目の向き）が
    非決定になり golden/fingerprint がぶれる。anti-parallel を検出したら target に直交する
    **固定の**軸まわり 180° を返して決定化する。
    """
    from mathutils import Quaternion, Vector  # type: ignore  # lazy: bpy 依存

    if cur.dot(target) < -1.0 + _ANTIPARALLEL_EPS:
        # target と平行でない決定的な基準ベクトルとの外積で垂直軸を作る。
        ref = Vector((1.0, 0.0, 0.0)) if abs(target.x) < 0.9 else Vector((0.0, 1.0, 0.0))
        perp = target.cross(ref).normalized()
        return Quaternion(perp, math.pi)
    return cur.rotation_difference(target)


def _min_up_projection(obj: Any, up: Any) -> float:
    """bbox 8隅を up 方向へ射影した最小値（floor の接地量と min_up 報告の単一窓口・DRY）。"""
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    return min((obj.matrix_world @ Vector(c)).dot(up) for c in obj.bound_box)


def _world_align(obj: Any, up: Any, axis: str | None) -> str:
    """指定（または up に最も近い）local 軸を up へ最小回転で合わせ、合わせた signed 軸を返す。

    axis 指定時はその local 軸（± のうち up に近い向き）。省略時は ±X/±Y/±Z の6方向から up に
    最も近い signed 軸を自動選択する（spec『最も近い主軸を合わせる』）。
    """
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    if axis is not None:
        wd = (obj.matrix_world.to_quaternion() @ Vector(_LOCAL_AXIS_UNIT[axis])).normalized()
        sign = "+"
        if wd.dot(up) < 0.0:  # 反対向きの方が近ければ符号反転
            wd = -wd
            sign = "-"
        cur, chosen = wd, sign + axis
    else:
        best: tuple[float, Any, str] | None = None
        for letter in _LOCAL_AXIS_LETTERS:
            base = (
                obj.matrix_world.to_quaternion() @ Vector(_LOCAL_AXIS_UNIT[letter])
            ).normalized()
            for sign, scalar in (("+", 1.0), ("-", -1.0)):
                wd = base * scalar
                d = wd.dot(up)
                if best is None or d > best[0]:
                    best = (d, wd, sign + letter)
        if best is None:  # 3軸×2符号で必ず確定（防御・-O でも安全に）
            raise _op_error(ErrorCode.E_PRECONDITION, "world-align の軸を決定できません")
        _, cur, chosen = best
    _apply_world_rotation(obj, _rotation_to(cur, up))
    return chosen


def _principal_axis(obj: Any, *, up: Any = None, up_hint: str = "auto") -> tuple[Any, list[float]]:
    """world 空間頂点分布の最大分散軸（principal）と固有値（昇順）を返す。

    共分散（対称 3x3）を numpy.linalg.eigh で分解し最大固有値の固有ベクトルを主成分とする
    （numpy は Blender 同梱・§E4）。PCA は符号不定なので符号を一意化する:
    - `up_hint="auto"`（既定）: **原点→重心 方向**に揃える（重心が偏る側を + に・決定的）。
    - `up_hint="current"`: 主成分のうち **up に近い向き**を + にする（principal·up>=0）。ベースが重い
      スキャン物体で重心が下に寄り「下」を + と誤判定→上下反転する問題を防ぐ（実地フィードバック #5）。
    分散が無い（点が一致/退化）場合は E_PRECONDITION。
    """
    import numpy as np  # type: ignore  # lazy: Blender 同梱（§E4）
    from mathutils import Vector  # type: ignore

    mw = obj.matrix_world
    verts = [mw @ v.co for v in obj.data.vertices]
    n = len(verts)
    if n < 2:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"pca には2頂点以上が必要です（頂点数={n}）",
        )
    cx = sum(v.x for v in verts) / n
    cy = sum(v.y for v in verts) / n
    cz = sum(v.z for v in verts) / n
    sxx = syy = szz = sxy = sxz = syz = 0.0
    for v in verts:
        dx, dy, dz = v.x - cx, v.y - cy, v.z - cz
        sxx += dx * dx
        syy += dy * dy
        szz += dz * dz
        sxy += dx * dy
        sxz += dx * dz
        syz += dy * dz
    cov = np.array([[sxx, sxy, sxz], [sxy, syy, syz], [sxz, syz, szz]]) / n
    eigvals, eigvecs = np.linalg.eigh(cov)  # 昇順固有値・正規直交固有ベクトル
    if float(eigvals[2]) <= _PCA_MIN_VARIANCE:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            "頂点分布に広がりが無く主成分を決定できません（pca には立体的な mesh が必要）",
        )
    principal = Vector((float(eigvecs[0, 2]), float(eigvecs[1, 2]), float(eigvecs[2, 2])))
    if up_hint == "current" and up is not None:
        # 現在の up に近い向きを + にする（principal·up>=0）→ up へ最小回転で合わせ反転を防ぐ。
        # principal⊥up（傾き≈90°）の退化は重心方向で tie-break して決定性を保つ。ここでの d は
        # 正規化ベクトル同士の内積（射影距離ではない）。_PCA_SIGN_EPS(1e-9) 流用は「ほぼ真の直交
        # （≈90°）」だけを退化扱いにする閾値として機能する（値域は異なるが両者とも ≈0 判定）。
        d = principal.dot(up)
        near_perp = abs(d) <= _PCA_SIGN_EPS
        centroid_below = (Vector((cx, cy, cz)) - mw.translation).dot(principal) < 0.0
        if d < -_PCA_SIGN_EPS or (near_perp and centroid_below):
            principal = -principal
    else:
        # auto: 原点→重心 方向に揃える（重心が偏る側を +）。重心が原点に一致（中心対称・射影 ≈ 0）
        # の退化時は符号が不定になるため、主成分の最大成分を正にする正準符号で tie-break する
        # （決定的・5.0/4.4 同値を保つ）。
        offset = (Vector((cx, cy, cz)) - mw.translation).dot(principal)
        if offset < -_PCA_SIGN_EPS:
            principal = -principal
        elif abs(offset) <= _PCA_SIGN_EPS:
            comps = (principal.x, principal.y, principal.z)
            dominant = max(range(3), key=lambda i: abs(comps[i]))
            if comps[dominant] < 0.0:
                principal = -principal
    return principal.normalized(), [round(float(x), 8) for x in eigvals]


def _floor(obj: Any, up: Any) -> list[float]:
    """up 方向の最下点を up=0 平面へ接地する（平行移動のみ）。適用した world 移動量を返す。"""
    from mathutils import Matrix  # type: ignore

    shift = -_min_up_projection(obj, up) * up
    obj.matrix_world = Matrix.Translation(shift) @ obj.matrix_world
    return [round(shift.x, 6), round(shift.y, 6), round(shift.z, 6)]


def _angle_rotate(obj: Any, axis: str, degrees: float) -> dict[str, Any]:
    """world 軸 axis（X/Y/Z）まわりに degrees 度回転する delta を前合成する（基準指定・#4）。

    エージェントが算出した補正回転を straighten 経由で安全に適用する method。符号は degrees に
    含む（X/Y/Z は無符号）。_apply_world_rotation で原点・スケール不変・親付きでも正しく整列する。
    """
    from mathutils import Quaternion, Vector  # type: ignore  # lazy: bpy 依存

    # X/Y/Z の単位ベクトルは world/local 共通なので _LOCAL_AXIS_UNIT を流用（ここでは world 軸）。
    world_axis = Vector(_LOCAL_AXIS_UNIT[axis])
    _apply_world_rotation(obj, Quaternion(world_axis, math.radians(degrees)))
    return {"axis": axis, "degrees": round(float(degrees), 6)}


def _align_vector(obj: Any, from_dir: Any, to_dir: Any) -> dict[str, Any]:
    """from_dir(world) を to_dir(world) へ重ねる最小回転を前合成する（基準指定・#4 の本命）。

    エージェントが計測した「現在の向き」→「目標の向き」を直接渡せる。同一メッシュ内の支柱など
    別オブジェクト化できない基準でも、向きを数値で与えれば straighten の作法（dry-run/bake/共有
    ガード）で安全に適用できる。anti-parallel は _rotation_to が決定化する。
    """
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    src = Vector(from_dir).normalized()
    dst = Vector(to_dir).normalized()
    delta = _rotation_to(src, dst)
    _apply_world_rotation(obj, delta)
    after = (delta @ src).normalized()
    # from→to のなす角（入力ベクトル由来）。anti-parallel 決定化後の実回転量とは概念的に別だが
    # 通常ケースでは一致する。呼び出し側が補正の妥当性を即チェックできる目安として返す。
    angle = math.degrees(math.acos(max(-1.0, min(1.0, src.dot(dst)))))
    return {
        "from_dir": [round(v, 6) for v in src],
        "to_dir": [round(v, 6) for v in dst],
        "from_world_after": [round(v, 6) for v in after],
        "angle_deg": round(angle, 4),
    }


def _reference_align(obj: Any, ref_obj: Any, ref_axis: str, axis: str | None) -> dict[str, Any]:
    """参照 obj の ref_axis(signed local)の world 方向へ、対象の axis(local)を合わせる（#4）。

    world-align の「合わせる目標」を world up から **参照オブジェクトの軸方向** へ差し替えただけ
    （_world_align をそのまま再利用）。ガイド用の別オブジェクトの向きに揃えたい場合に使う。
    axis 省略時は対象の最近 signed local 軸を自動選択（world-align と同じ挙動）。
    """
    target_dir = _local_axis_world(ref_obj, ref_axis)
    chosen = _world_align(obj, target_dir, axis)
    return {
        "reference": ref_obj.name,
        "ref_axis": ref_axis,
        "reference_world": [round(v, 6) for v in target_dir],
        "axis": chosen,
    }


def _snapshot_transform(obj: Any) -> dict[str, Any]:
    """transform チャンネルの完全スナップショット（dry-run の厳密復元用）。

    補正は matrix_world / 回転チャンネル経由で loc/rot/scale を書き換える。全表現（euler/
    quaternion/axis_angle）と mode/loc/scale を raw 値で控え、restore で厳密に戻す（matrix_world
    の再代入だと decompose の微小ドリフトが乗るため raw チャンネルを使う）。
    """
    return {
        "mode": obj.rotation_mode,
        "location": tuple(obj.location),
        "rotation_euler": tuple(obj.rotation_euler),
        "rotation_quaternion": tuple(obj.rotation_quaternion),
        "rotation_axis_angle": tuple(obj.rotation_axis_angle),
        "scale": tuple(obj.scale),
    }


def _restore_transform(obj: Any, snap: dict[str, Any]) -> None:
    """_snapshot_transform の状態へ厳密に戻す（全チャンネルを raw 値で復元）。"""
    obj.location = snap["location"]
    obj.rotation_euler = snap["rotation_euler"]
    obj.rotation_quaternion = snap["rotation_quaternion"]
    obj.rotation_axis_angle = snap["rotation_axis_angle"]
    obj.scale = snap["scale"]
    obj.rotation_mode = snap["mode"]


def straighten_object(
    obj: Any,
    *,
    method: str,
    up_axis: str = "+Z",
    axis: str | None = None,
    up_hint: str = "auto",
    degrees: float | None = None,
    from_dir: Any = None,
    to_dir: Any = None,
    reference_obj: Any = None,
    ref_axis: str | None = None,
    message: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """直立補正を実行し、補正結果（回転/接地/整列の golden 値）を返す。

    reset/world-align/pca/angle/align-vector/reference は object 回転のみ・floor は平行移動のみ
    変更する（mesh 非破壊）。`--bake-rotation` の mesh 焼き込みは呼び出し側（ops）が apply_transform
    経路で行う。matrix_world は読み取り前に view_layer.update() で最新化する（§E4 の stale 対策）。
    `dry_run=True` は適用→レポート読取→**厳密復元**で、副作用なく計画値を返す（push_undo もしない・
    実地フィードバック #2）。pca は `up_hint` で符号決定を切り替え、`tilt_from_up_deg`（up からの
    傾き角・符号非依存の鋭角）を併せて返す（#5/#6）。angle/align-vector/reference はエージェントが
    算出した補正を straighten 経由で安全に適用する基準指定 method（transform 迂回の解消・#4）。
    """
    from mathutils import Vector  # type: ignore  # lazy: bpy 依存

    bpy.context.view_layer.update()  # §E4: rotation 直接設定後の stale を避ける
    up = Vector(_AXIS_VECTORS[up_axis])
    data: dict[str, Any] = {"name": obj.name, "method": method, "up_axis": up_axis}
    snap = _snapshot_transform(obj) if dry_run else None

    if method == "reset":
        _reset_rotation(obj)
    elif method == "world-align":
        data["axis"] = _world_align(obj, up, axis)
    elif method == "pca":
        principal, eigvals = _principal_axis(obj, up=up, up_hint=up_hint)
        delta = _rotation_to(principal, up)
        _apply_world_rotation(obj, delta)
        data["eigenvalues"] = eigvals
        data["principal_world"] = [round(v, 6) for v in principal]
        data["principal_world_after"] = [round(v, 6) for v in (delta @ principal).normalized()]
        # up からの傾き角（鋭角・符号非依存）。呼び出し側が補正の妥当性を即チェックできる。
        data["tilt_from_up_deg"] = round(
            math.degrees(math.acos(min(1.0, abs(principal.dot(up))))), 4
        )
    elif method == "angle":
        if axis is None or degrees is None:  # ops が必須を保証するが gateway も防御（型も絞る）
            raise _op_error(ErrorCode.E_PRECONDITION, "angle には axis と degrees が必要です")
        data.update(_angle_rotate(obj, axis, degrees))
    elif method == "align-vector":
        if from_dir is None:  # ops が必須を保証するが gateway も防御（INTERNAL 化を避ける・§6e）
            raise _op_error(ErrorCode.E_PRECONDITION, "align-vector には from_dir が必要です")
        # to_dir 省略時は up（「現在の向きを up へ立てる」が既定・#4）。
        data.update(_align_vector(obj, from_dir, to_dir if to_dir is not None else tuple(up)))
    elif method == "reference":
        if reference_obj is None or ref_axis is None:  # 同上（ops 保証 + gateway 防御）
            raise _op_error(ErrorCode.E_PRECONDITION, "reference には参照オブジェクトが必要です")
        data.update(_reference_align(obj, reference_obj, ref_axis, axis))
    elif method == "floor":
        data["floor_offset"] = _floor(obj, up)
    else:  # method は ENUM 検証済みのため到達不能（新 method の分岐漏れ検出の防御）。
        raise _op_error(ErrorCode.E_PRECONDITION, f"未対応の straighten method: {method}")

    bpy.context.view_layer.update()  # 補正後の matrix_world を確定

    data["rotation_euler_deg"] = _rotation_euler_deg(obj)
    if method in ("world-align", "reference"):
        # 合わせた軸の world 方向（world-align は ≈ up / reference は ≈ reference_world・DoD の整列 golden）
        data["aligned_world"] = [round(v, 6) for v in _local_axis_world(obj, data["axis"])]
    if world_bbox(obj) is not None:  # up 方向の最下点（bbox があれば常時・floor 後は ≈0）
        data["min_up"] = round(_min_up_projection(obj, up), 6)
    data["dry_run"] = dry_run

    if (
        snap is not None
    ):  # dry_run のときのみ snapshot を取る → 厳密復元（副作用なし・push_undo もしない）
        _restore_transform(obj, snap)
        bpy.context.view_layer.update()
    elif message:
        push_undo(message)
    return data
