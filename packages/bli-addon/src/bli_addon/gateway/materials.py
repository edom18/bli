"""BpyGateway マテリアル（PBR/テクスチャ含む・gateway/ 分割 P2-4）。

元 gateway.py の該当セクションをそのまま移設（挙動変更なし）。
"""

from __future__ import annotations

from typing import Any

import bpy  # type: ignore

from bli_core.errors import ErrorCategory, ErrorCode

from .core import _digest16, _op_error

# ---- マテリアル（M6 T6.3 / 生 bpy.ops 不要・bpy.data 直接。M0.5 スパイクで 5.0/4.4 確認済み）----


def _principled(mat: Any) -> Any:
    """マテリアルの Principled BSDF ノードを返す（無ければ None）。"""
    if not mat.use_nodes or mat.node_tree is None:
        return None
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def _base_color(mat: Any) -> list[float] | None:
    """マテリアルの Base Color（RGBA）を返す（取得不可は None）。"""
    if mat is None:
        return None
    bsdf = _principled(mat)
    if bsdf is not None:
        bc = bsdf.inputs.get("Base Color")
        if bc is not None:
            return [round(v, 6) for v in bc.default_value]
    return [round(v, 6) for v in mat.diffuse_color]


def require_material_support(obj: Any) -> None:
    """materials を持てない型（EMPTY/LIGHT/CAMERA 等）は E_PRECONDITION で弾く。"""
    if obj.data is None or not hasattr(obj.data, "materials"):
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"マテリアル操作は mesh/curve 等のデータを持つ型のみ対応（type={obj.type}）",
        )


def find_material(name: str) -> Any | None:
    """名前でマテリアルを解決する（完全一致・無ければ None）。"""
    return bpy.data.materials.get(name)


def require_material(name: str) -> Any:
    """名前でマテリアルを解決する。無ければ E_TARGET_NOT_FOUND（require_single と同じ流儀）。

    対象未発見エラーの生成を gateway に集約する（ops は薄く保つ）。
    """
    mat = bpy.data.materials.get(name)
    if mat is None:
        raise _op_error(
            ErrorCode.E_TARGET_NOT_FOUND,
            f"マテリアルが見つかりません: {name}（既存名を指定するか create で作成）",
            category=ErrorCategory.USER_INPUT,
        )
    return mat


def _require_principled(mat: Any) -> Any:
    """Principled BSDF ノードを返す（無ければ E_PRECONDITION・silent drop しない）。

    PBR/テクスチャ設定（P2-3 G5）は Principled 前提。use_nodes 直後の既定構成なら必ず
    存在する（両版スパイク確定）ため、無い場合は想定外構成として明示的に失敗させる。
    """
    bsdf = _principled(mat)
    if bsdf is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            "Principled BSDF ノードが見つかりません（use_nodes 構成が想定外）",
        )
    return bsdf


def _principled_input(bsdf: Any, input_name: str) -> Any:
    """Principled の入力ソケットを返す（無ければ E_PRECONDITION・silent drop しない）。

    入力名は両版（5.0.1/4.4.3）で同一（Metallic/Roughness/Alpha/Emission Color/
    Emission Strength・P2-3 スパイク確定）。欠如＝想定外ビルドとして明示的に失敗させる。
    """
    sock = bsdf.inputs.get(input_name)
    if sock is None:
        raise _op_error(
            ErrorCode.E_PRECONDITION,
            f"Principled BSDF に '{input_name}' 入力がありません（想定外のビルド/構成）",
        )
    return sock


def _load_texture_image(path: str) -> Any:
    """画像を読み込む（存在チェックは ops 済み・壊れ画像等は E_OPERATOR/USER_INPUT へ写像）。"""
    try:
        return bpy.data.images.load(path)
    except (RuntimeError, OSError) as e:
        raise _op_error(
            ErrorCode.E_OPERATOR,
            f"テクスチャ画像を読み込めません: {path}（{e}）",
            category=ErrorCategory.USER_INPUT,
        ) from e


def create_material(
    name: str,
    color: list[float] | None,
    *,
    metallic: float | None = None,
    roughness: float | None = None,
    emission: list[float] | None = None,
    emission_strength: float | None = None,
    alpha: float | None = None,
    texture_path: str | None = None,
    pack_texture: bool = False,
) -> tuple[Any, dict[str, Any]]:
    """新規マテリアルを作り (mat, extras) を返す（use_nodes + Principled・P2-3 で PBR/テクスチャ対応）。

    name は既存と衝突すると Blender が name.001 等に自動採番する（戻り値の mat.name が真）。
    color(RGBA) 指定時は Principled の Base Color とビューポート表示色の双方へ反映する
    （texture 併用時は Base Color 入力にノードが接続されるため、color はビューポート表示色
    としてのみ有効＝methods.md に明記）。extras は結果報告用:
    `{"principled": {設定した入力の実値}, "texture": {image, path, packed}}`（未設定キーは省略）。
    emission 指定時に emission_strength 省略なら 1.0 を明示設定する（既定 strength 0 のビルド
    でも発光が silent に無効化されないように）。
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    if color is not None:
        rgba = (float(color[0]), float(color[1]), float(color[2]), float(color[3]))
        # color は M6 からの互換で意図的に soft-skip（bsdf/Base Color 欠如でも diffuse_color は
        # 設定＝ビューポート表示は成立）。PBR/テクスチャ（P2-3）は _principled_input で strict
        # （E_PRECONDITION）＝この非対称は意図的（レビュー R1-8 で明文化）。
        bsdf = _principled(mat)
        if bsdf is not None:
            bc = bsdf.inputs.get("Base Color")
            if bc is not None:
                bc.default_value = rgba
        mat.diffuse_color = rgba

    extras: dict[str, Any] = {}
    try:
        extras = _apply_material_extras(
            mat,
            metallic=metallic,
            roughness=roughness,
            emission=emission,
            emission_strength=emission_strength,
            alpha=alpha,
            texture_path=texture_path,
            pack_texture=pack_texture,
        )
    except BaseException:
        # 失敗時に作りかけの material を残さない（add_modifier の props と同じアトミック流儀）。
        # JsonRpcError 限定にすると想定外例外（OSError 等）でリークする＝_add_then_apply の
        # BaseException ロールバックと同じ広さで捕捉し、必ず再送出する（レビュー R2-B）。
        bpy.data.materials.remove(mat)
        raise
    return mat, extras


def discard_created_material(mat: Any, extras: dict[str, Any]) -> None:
    """create 後の後段失敗（共有 mesh ガード等）で作りたて material と texture image を撤去する。

    material の撤去だけでは独立 ID の Image は消えない（R1-2 と同根）。extras["texture"]["image"]
    は create_material が返した実名（重複採番後）なので name 引きで安全に解決できる（レビュー R2-A）。
    """
    tex = extras.get("texture")
    if isinstance(tex, dict):
        img = bpy.data.images.get(str(tex.get("image", "")))
        if img is not None:
            bpy.data.images.remove(img)
    bpy.data.materials.remove(mat)


def _apply_material_extras(
    mat: Any,
    *,
    metallic: float | None,
    roughness: float | None,
    emission: list[float] | None,
    emission_strength: float | None,
    alpha: float | None,
    texture_path: str | None,
    pack_texture: bool,
) -> dict[str, Any]:
    """PBR/テクスチャ設定を適用し extras（結果報告用）を返す（create_material 専用）。"""
    extras: dict[str, Any] = {}
    principled_applied: dict[str, Any] = {}
    needs_bsdf = (
        any(v is not None for v in (metallic, roughness, emission, alpha))
        or texture_path is not None
    )
    if needs_bsdf:
        bsdf = _require_principled(mat)
        for input_name, key, value in (
            ("Metallic", "metallic", metallic),
            ("Roughness", "roughness", roughness),
            ("Alpha", "alpha", alpha),
        ):
            if value is not None:
                sock = _principled_input(bsdf, input_name)
                sock.default_value = float(value)
                principled_applied[key] = float(sock.default_value)
        if emission is not None:
            ec = _principled_input(bsdf, "Emission Color")
            ec.default_value = (
                float(emission[0]),
                float(emission[1]),
                float(emission[2]),
                float(emission[3]),
            )
            es = _principled_input(bsdf, "Emission Strength")
            es.default_value = float(emission_strength if emission_strength is not None else 1.0)
            principled_applied["emission_color"] = [float(v) for v in ec.default_value]
            principled_applied["emission_strength"] = float(es.default_value)
        if texture_path is not None:
            img = _load_texture_image(texture_path)
            try:
                nt = mat.node_tree
                tex = nt.nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.location = (-320.0, 260.0)  # Principled の左（GUI で開いても重ならない位置）
                nt.links.new(tex.outputs["Color"], _principled_input(bsdf, "Base Color"))
                if pack_texture:
                    try:
                        img.pack()
                    except (RuntimeError, OSError) as e:
                        # pack はディスク上の元画像を再読込し得る＝load と同じく OSError も
                        # 入力起因として写像する（_load_texture_image と対称・レビュー R2-B）。
                        raise _op_error(
                            ErrorCode.E_OPERATOR,
                            f"テクスチャのパックに失敗しました: {e}",
                            category=ErrorCategory.USER_INPUT,
                        ) from e
            except BaseException:
                # material 撤去（呼び出し元）だけでは独立 ID の Image は消えない＝ロード済み
                # image を orphan として残さない（screenshot_area の一時 datablock 破棄と同じ
                # 流儀・レビュー R1-2）。想定外例外でもリークさせないため BaseException で
                # 捕捉し必ず再送出する（レビュー R2-B）。
                bpy.data.images.remove(img)
                raise
            extras["texture"] = {
                "image": img.name,
                "path": texture_path,
                "packed": img.packed_file is not None,
            }
    if principled_applied:
        extras["principled"] = principled_applied
    return extras


def _target_slot_index(obj: Any) -> int | None:
    """assign/create が書き込むスロット index を返す（None = 空スロットで append が必要）。

    `material_write_touches_mesh_data`（ガード判定）と `assign_material`（実書き込み）が
    **同一の書き込み先**を見るための単一窓口。両者が別々に active_material_index をクランプして
    ズレると「ガードが見る slot」と「実際に書く slot」が食い違い、共有 mesh への意図しない波及を
    招くため、ここに集約する（設計レビュー P2）。
    """
    mats = obj.data.materials
    if len(mats) == 0:
        return None
    idx = obj.active_material_index
    if idx < 0 or idx >= len(mats):
        idx = 0
    return idx


def material_write_touches_mesh_data(obj: Any) -> bool:
    """assign/create の付与がメッシュデータ（共有され得る）を書き換えるか判定する（Codex P2）。

    空スロット（append で DATA slot を新設）か、書き込み先スロットが DATA リンクなら True。
    OBJECT リンクなら object 限定の書き込みで共有 mesh を触らないため False（共有ガード不要・
    --make-single-user による不要な分離も避ける）。書き込み先は `_target_slot_index` で
    assign_material と一致させる。
    """
    idx = _target_slot_index(obj)
    if idx is None:
        return True  # append は DATA slot を作る（共有 mesh に波及し得る）
    return obj.material_slots[idx].link == "DATA"


def assign_material(obj: Any, mat: Any) -> int:
    """mat を obj に付与する（空スロットなら append・あれば書き込み先スロットを置換）。

    付与したスロット index を返す（判断: active 置換・空なら追加。複数スロット運用は後続）。
    書き込みは `material_slots[idx].material` 経由で **slot.link を尊重**する（OBJECT リンクの
    slot では object 側、DATA リンクでは mesh データ側へ正しく反映する。Codex P2-B）。共有 mesh
    の DATA slot 置換が兄弟へ波及する件は呼び出し側（ops._guard_shared_mesh）が単一ユーザ化で防ぐ。
    書き込み先 index は `_target_slot_index`（material_write_touches_mesh_data と共有）で決める。
    """
    idx = _target_slot_index(obj)
    if idx is None:
        obj.data.materials.append(mat)  # 新規スロット作成は data 経由（DATA リンクで生成される）
        return 0
    obj.material_slots[idx].material = mat
    return idx


def list_object_materials(obj: Any) -> list[dict[str, Any]]:
    """obj のマテリアルスロット一覧（slot index / name / link / base_color）を返す。

    実効スロット（slot.link 尊重）を `material_slots` 経由で読む。OBJECT リンクの slot では
    object 側のマテリアルを報告する（data.materials を直接見ると乖離する。Codex P2-B）。
    """
    out: list[dict[str, Any]] = []
    for i, slot in enumerate(obj.material_slots):
        mat = slot.material
        out.append(
            {
                "slot": i,
                "name": mat.name if mat is not None else None,
                "link": slot.link,
                "base_color": _base_color(mat),
            }
        )
    return out


def material_fingerprint(obj: Any) -> str:
    """obj のマテリアル状態の決定的フィンガープリント（material の drift 検証用）。"""
    return _digest16({"name": obj.name, "materials": list_object_materials(obj)})
