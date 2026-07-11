"""gateway.py の targets 解決（B2）/ materials 報告（B3）/ save エラー写像（B4）の L1 ユニット。

gateway.py は module 冒頭で `import bpy` するため、他のテスト（test_ops_dispatch.py 等）と同様に
pytest 環境（bpy 不在）では素の import が ModuleNotFoundError になる。ここでは resolve_targets /
object_summary / save_blend が bpy の**値**にしか依存しない（bpy.ops の生 operator 呼び出しに
依存しない）ことを利用し、最小限のフェイク bpy を `sys.modules["bpy"]` に差し込んで gateway を
直接 import し、bpy 無しで検証する（test_exec_ops.py の「gateway をスタブにする」流儀の逆＝
ここでは gateway 自体を検証対象にするため bpy 側をスタブにする）。

他テストは「bpy 無し＝ModuleNotFoundError」を前提にしているため、テスト後は必ず
`sys.modules` から bpy / bli_addon.gateway を除去し、他テストへの波及を防ぐ（fixture で保証）。
"""

from __future__ import annotations

import importlib
import sys
import types
from contextlib import contextmanager
from typing import Any

import pytest

from bli_core.errors import ErrorCode
from bli_core.protocol import JsonRpcError


class _FakeObjects:
    """bpy.data.objects の最小スタブ（get/iterate/remove のみ）。"""

    def __init__(self, objs: tuple[Any, ...] = ()) -> None:
        self._by_name = {o.name: o for o in objs}

    def get(self, name: str, default: Any = None) -> Any:
        return self._by_name.get(name, default)

    def __iter__(self):
        return iter(self._by_name.values())

    def remove(self, obj: Any, do_unlink: bool = True) -> None:
        self._by_name.pop(obj.name, None)


class _FakeVec3:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        self.x, self.y, self.z = x, y, z

    def __iter__(self):
        return iter((self.x, self.y, self.z))


class _FakeMatrixWorld:
    def __init__(self, translation: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
        self.translation = _FakeVec3(*translation)

    def copy(self) -> _FakeMatrixWorld:
        return _FakeMatrixWorld((self.translation.x, self.translation.y, self.translation.z))

    def inverted(self) -> _FakeMatrixWorld:
        # parent_set の keep_transform 分岐が呼べればよい（値の正しさは smoke/実機で検証）。
        return _FakeMatrixWorld((self.translation.x, self.translation.y, self.translation.z))


class _FakeMaterial:
    def __init__(self, name: str, diffuse_color: tuple[float, ...] = (0.8, 0.8, 0.8, 1.0)) -> None:
        self.name = name
        # use_nodes=False で _principled() を早期 None 化し、mathutils/ノード木を経由させない。
        self.use_nodes = False
        self.node_tree = None
        self.diffuse_color = diffuse_color


class _FakeSlot:
    def __init__(self, material: _FakeMaterial | None, link: str = "DATA") -> None:
        self.material = material
        self.link = link


class _FakeObj:
    """resolve_targets（.name のみ使用）と object_summary（B3）の双方に足る最小オブジェクト。

    type=MESH だが data=None にして object_summary の頂点数分岐（obj.data.vertices 等）を
    skip させる。bound_box は全隅同一にして world_bbox が None を返す退化経路を通し、
    mathutils（pytest 環境に無い）を経由させない。
    """

    def __init__(self, name: str, *, material_slots: tuple[_FakeSlot, ...] = ()) -> None:
        self.name = name
        self.type = "MESH"
        self.matrix_world = _FakeMatrixWorld()
        self.dimensions = _FakeVec3(1.0, 1.0, 1.0)
        self.rotation_mode = "XYZ"
        self.rotation_euler = (0.0, 0.0, 0.0)
        self.scale = (1.0, 1.0, 1.0)
        self.modifiers: list[Any] = []
        self.material_slots = list(material_slots)
        self.bound_box = [(0.0, 0.0, 0.0)] * 8
        self.data = None
        self.parent = None  # rename/parent（P1-2）テスト用（既定は無親）
        self.users_collection: list[Any] = []  # collection（P1-2）テスト用（既定は無所属）


class _FakeOperator:
    """bpy.ops.wm.save_as_mainfile 等の最小スタブ（poll/呼び出し結果/例外を差し替え可能）。"""

    def __init__(
        self,
        *,
        poll_ok: bool = True,
        result: set[str] | None = None,
        raises: BaseException | None = None,
        on_call: Any = None,
    ) -> None:
        self._poll_ok = poll_ok
        self._result = result if result is not None else {"FINISHED"}
        self._raises = raises
        self._on_call = on_call

    def poll(self) -> bool:
        return self._poll_ok

    def __call__(self, **kwargs: Any) -> set[str]:
        if self._on_call is not None:
            self._on_call(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._result


def _make_fake_bpy(
    objects: tuple[Any, ...] = (), collections: tuple[Any, ...] = ()
) -> types.ModuleType:
    bpy_mod = types.ModuleType("bpy")
    bpy_mod.data = types.SimpleNamespace(  # type: ignore[attr-defined]
        objects=_FakeObjects(objects), collections=_FakeObjects(collections)
    )

    class _FakeContext:
        def __init__(self) -> None:
            self.preferences = types.SimpleNamespace(
                filepaths=types.SimpleNamespace(save_version=1)
            )

        @contextmanager
        def temp_override(self, **kwargs: Any):
            yield

    bpy_mod.context = _FakeContext()  # type: ignore[attr-defined]
    bpy_mod.app = types.SimpleNamespace(background=False)  # type: ignore[attr-defined]
    bpy_mod.ops = types.SimpleNamespace(  # type: ignore[attr-defined]
        wm=types.SimpleNamespace(save_as_mainfile=_FakeOperator()),
        ed=types.SimpleNamespace(undo_push=lambda **kw: None),
    )
    return bpy_mod


def _forget_gateway_module() -> None:
    """bli_addon.gateway を sys.modules と親パッケージ属性の両方から除去する。

    `from . import gateway`（ops.py 等）は、`bli_addon` に `gateway` 属性が既にあれば
    sys.modules を経由せずそれを直接使う（Python の import 属性キャッシュ）。sys.modules から
    popするだけでは親パッケージの属性が残り、フェイク bpy を積んだこのモジュールが後続テストへ
    漏れ、「bpy 無し＝ModuleNotFoundError」前提の他テストを壊す。両方を消して完全に忘れさせる。
    """
    sys.modules.pop("bli_addon.gateway", None)
    bli_addon = sys.modules.get("bli_addon")
    if bli_addon is not None:
        bli_addon.__dict__.pop("gateway", None)


@pytest.fixture
def make_gateway(monkeypatch):
    """フェイク bpy を差し込んで bli_addon.gateway を新規 import するファクトリを返す。

    テスト後は bli_addon.gateway を確実に忘れさせる（他テストの
    「bpy 無し＝ModuleNotFoundError」前提を壊さないため）。
    """

    def _factory(objects: tuple[Any, ...] = (), collections: tuple[Any, ...] = ()) -> Any:
        fake_bpy = _make_fake_bpy(objects, collections=collections)
        monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
        _forget_gateway_module()
        return importlib.import_module("bli_addon.gateway")

    yield _factory
    _forget_gateway_module()


# ---- B2: resolve_targets / require_single / require_targets（正規表現の明示 opt-in）----


def test_resolve_targets_exact_match_only_no_implicit_regex_fallback(make_gateway):
    # 完全一致のみ: "Cube" は "Cube.001"（`.` は regex の任意一文字）に誤マッチしない
    # （暗黙 regex フォールバック廃止・設計レビュー 2026-07-11 B2）。
    gw = make_gateway((_FakeObj("Cube"), _FakeObj("Cube.001")))
    found = gw.resolve_targets("Cube")
    assert [o.name for o in found] == ["Cube"]


def test_resolve_targets_partial_name_no_match(make_gateway):
    gw = make_gateway((_FakeObj("Cube"),))
    assert gw.resolve_targets("Cub") == []  # 部分一致は完全名一致では拾わない


def test_resolve_targets_regex_true_matches_pattern(make_gateway):
    gw = make_gateway((_FakeObj("Cube"), _FakeObj("Cube.001"), _FakeObj("Sphere")))
    found = gw.resolve_targets(r"^Cube", regex=True)
    assert sorted(o.name for o in found) == ["Cube", "Cube.001"]


def test_resolve_targets_invalid_regex_is_user_input(make_gateway):
    gw = make_gateway((_FakeObj("Cube"),))
    with pytest.raises(JsonRpcError) as ei:
        gw.resolve_targets("[", regex=True)
    assert ei.value.message == ErrorCode.E_PRECONDITION
    assert ei.value.data.category == "USER_INPUT"


def test_resolve_targets_invalid_regex_when_not_requested_is_no_match(make_gateway):
    # regex=False（既定）では正規表現として解釈すらしない＝不正な regex 構文でも例外化しない。
    gw = make_gateway((_FakeObj("Cube"),))
    assert gw.resolve_targets("[") == []


def test_require_single_hint_suggests_regex_when_it_would_match(make_gateway):
    # 完全一致 0 件・regex として解釈すると当たる場合は --regex を案内する（N件付き）。
    gw = make_gateway((_FakeObj("Cube.001"),))
    with pytest.raises(JsonRpcError) as ei:
        gw.require_single(r"Cube\.")
    assert ei.value.message == ErrorCode.E_TARGET_NOT_FOUND
    assert ei.value.data.category == "USER_INPUT"
    assert "--regex" in ei.value.data.userVisibleSymptom
    assert "1" in ei.value.data.userVisibleSymptom


def test_require_single_hint_empty_when_regex_would_not_match(make_gateway):
    gw = make_gateway((_FakeObj("Sphere"),))
    with pytest.raises(JsonRpcError) as ei:
        gw.require_single("NoMatchAtAll")
    assert "--regex" not in ei.value.data.userVisibleSymptom


def test_require_single_hint_empty_when_regex_already_true(make_gateway):
    # 既に --regex 指定済みなら「--regex を指定してください」ヒントは冗長なので出さない。
    gw = make_gateway((_FakeObj("Sphere"),))
    with pytest.raises(JsonRpcError) as ei:
        gw.require_single("NoMatch", regex=True)
    assert "--regex" not in ei.value.data.userVisibleSymptom


def test_require_targets_regex_true_returns_multiple(make_gateway):
    gw = make_gateway((_FakeObj("Cube"), _FakeObj("Cube.001"), _FakeObj("Other")))
    found = gw.require_targets(r"^Cube", regex=True)
    assert sorted(o.name for o in found) == ["Cube", "Cube.001"]


def test_require_targets_exact_no_match_is_target_not_found(make_gateway):
    gw = make_gateway((_FakeObj("Cube"),))
    with pytest.raises(JsonRpcError) as ei:
        gw.require_targets("Cube.001")
    assert ei.value.message == ErrorCode.E_TARGET_NOT_FOUND
    assert ei.value.data.category == "USER_INPUT"


# ---- B3: object_summary の materials は list_object_materials（slot.link 尊重）経由 ----


def test_object_summary_materials_matches_list_object_materials(make_gateway):
    gw = make_gateway()
    mat = _FakeMaterial("Red")
    # OBJECT リンク slot（obj.data.materials には出ない/乖離し得る典型ケース）。
    slot = _FakeSlot(mat, link="OBJECT")
    obj = _FakeObj("Cube", material_slots=(slot,))
    summary = gw.object_summary(obj)
    assert summary["materials"] == [m["name"] for m in gw.list_object_materials(obj)]
    assert summary["materials"] == ["Red"]


def test_object_summary_materials_empty_slot_is_none(make_gateway):
    gw = make_gateway()
    obj = _FakeObj("Cube", material_slots=(_FakeSlot(None, link="DATA"),))
    summary = gw.object_summary(obj)
    assert summary["materials"] == [None]


def test_object_summary_materials_no_slots_is_empty_list(make_gateway):
    gw = make_gateway()
    obj = _FakeObj("Cube")
    summary = gw.object_summary(obj)
    assert summary["materials"] == []


# ---- B4: save_blend の例外写像（OSError→E_OPERATOR / JsonRpcError は二重ラップしない）----


def test_save_blend_oserror_maps_to_e_operator(make_gateway):
    gw = make_gateway()
    gw.bpy.ops.wm.save_as_mainfile = _FakeOperator(raises=OSError("disk full"))
    with pytest.raises(JsonRpcError) as ei:
        gw.save_blend("/tmp/x.blend", backup=True)
    assert ei.value.message == ErrorCode.E_OPERATOR
    # _op_error の既定カテゴリ（PRECONDITION）を継承する（open_blend と同流儀・明示指定なし）。
    assert ei.value.data.category == "PRECONDITION"
    assert "disk full" in ei.value.data.userVisibleSymptom
    # 失敗時も save_version は元の値へ復元される（try/finally）。
    assert gw.bpy.context.preferences.filepaths.save_version == 1


def test_save_blend_precondition_from_run_operator_not_double_wrapped(make_gateway):
    # run_operator 由来の JsonRpcError（poll() False）は save_blend の except Exception で
    # 再ラップされず、元の kind（E_PRECONDITION）のまま伝播する。
    gw = make_gateway()
    gw.bpy.ops.wm.save_as_mainfile = _FakeOperator(poll_ok=False)
    with pytest.raises(JsonRpcError) as ei:
        gw.save_blend("/tmp/x.blend", backup=True)
    assert ei.value.message == ErrorCode.E_PRECONDITION


def test_save_blend_runtime_error_from_operator_not_double_wrapped(make_gateway):
    # run_operator 自身が RuntimeError を E_OPERATOR の JsonRpcError に変換する（poll 後の実行時）。
    # save_blend はこれをそのまま伝播する（メッセージが二重に書き換わらない）。
    gw = make_gateway()
    gw.bpy.ops.wm.save_as_mainfile = _FakeOperator(raises=RuntimeError("boom"))
    with pytest.raises(JsonRpcError) as ei:
        gw.save_blend("/tmp/x.blend", backup=True)
    assert ei.value.message == ErrorCode.E_OPERATOR
    assert "boom" in ei.value.data.userVisibleSymptom


def test_save_blend_success_toggles_save_version_and_restores(make_gateway):
    gw = make_gateway()
    seen: dict[str, int] = {}

    def _on_call(_kwargs: dict) -> None:
        seen["during"] = gw.bpy.context.preferences.filepaths.save_version

    gw.bpy.ops.wm.save_as_mainfile = _FakeOperator(on_call=_on_call)
    gw.bpy.context.preferences.filepaths.save_version = 999  # 既存値（復元確認用の番兵）
    gw.save_blend("/tmp/x.blend", backup=False)
    assert seen["during"] == 0  # backup=False の間は 0 に一時上書き
    assert gw.bpy.context.preferences.filepaths.save_version == 999  # 復元済み


# ---- P1-2: rename 衝突実名 / parent 自己参照・循環・keep_transform / collection unlink 所属0・
# create 重複拒否（純ロジック部分・bpy operator 非依存）----


class _FakeNameRegistry:
    """bpy.data.objects の名前空間を模す最小レジストリ（衝突時 Blender の `.001` 付与を再現）。"""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def claim(self, requested: str, *, previous: str | None = None) -> str:
        if previous is not None:
            self._used.discard(previous)
        if requested not in self._used:
            self._used.add(requested)
            return requested
        i = 1
        while f"{requested}.{i:03d}" in self._used:
            i += 1
        final = f"{requested}.{i:03d}"
        self._used.add(final)
        return final


class _FakeRenamableObj(_FakeObj):
    """rename の「衝突時は実名（.001 等）を報告する」契約を検証するための obj.name=... 実装。

    通常の _FakeObj.name は素の属性のため衝突を再現できない。ここでは name をプロパティにし、
    共有 _FakeNameRegistry 経由で Blender の自動サフィックス付与を模す。
    """

    def __init__(self, name: str, registry: _FakeNameRegistry) -> None:
        self._registry = registry
        self._name: str | None = None
        super().__init__(name)

    @property
    def name(self) -> str:
        return self._name  # type: ignore[return-value]

    @name.setter
    def name(self, value: str) -> None:
        self._name = self._registry.claim(value, previous=self._name)


class _FakeCollectionObjects:
    """collection.objects の最小スタブ（link/unlink 呼び出しの記録のみ）。"""

    def __init__(self) -> None:
        self.unlinked: list[Any] = []
        self.linked: list[Any] = []

    def link(self, obj: Any) -> None:
        self.linked.append(obj)

    def unlink(self, obj: Any) -> None:
        self.unlinked.append(obj)


class _FakeCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.objects = _FakeCollectionObjects()
        self.children: list[Any] = []


# ---- rename: 衝突時は実名（.001 等）を報告する ----


def test_rename_object_reports_requested_name_when_free(make_gateway):
    gw = make_gateway()
    obj = _FakeObj("Cube")
    data = gw.rename_object(obj, "Barrel")
    assert data == {"old_name": "Cube", "new_name": "Barrel", "data_renamed": False}
    assert obj.name == "Barrel"


def test_rename_object_collision_reports_actual_suffixed_name(make_gateway):
    registry = _FakeNameRegistry()
    cube = _FakeRenamableObj("Cube", registry)
    _FakeRenamableObj("Sphere", registry)  # 既に "Sphere" を占有
    gw = make_gateway()
    data = gw.rename_object(cube, "Sphere")
    # 要求名 "Sphere" は衝突するため、Blender と同様に実名は "Sphere.001" になる。
    assert data == {"old_name": "Cube", "new_name": "Sphere.001", "data_renamed": False}
    assert cube.name == "Sphere.001"


def test_rename_object_with_data_renames_data_too(make_gateway):
    gw = make_gateway()
    obj = _FakeObj("Cube")
    obj.data = types.SimpleNamespace(name="CubeMesh")
    data = gw.rename_object(obj, "Barrel", with_data=True)
    assert data["data_renamed"] is True
    assert obj.data.name == "Barrel"


def test_rename_object_without_data_flag_leaves_data_name(make_gateway):
    gw = make_gateway()
    obj = _FakeObj("Cube")
    obj.data = types.SimpleNamespace(name="CubeMesh")
    data = gw.rename_object(obj, "Barrel", with_data=False)
    assert data["data_renamed"] is False
    assert obj.data.name == "CubeMesh"


# ---- parent: 自己参照 / 循環 は E_PRECONDITION（事前拒否・状態を汚さない）----


def test_parent_set_self_parent_is_precondition(make_gateway):
    gw = make_gateway()
    cube = _FakeObj("Cube")
    with pytest.raises(JsonRpcError) as ei:
        gw.parent_set([cube], cube)
    assert ei.value.message == ErrorCode.E_PRECONDITION
    assert ei.value.data.category == "PRECONDITION"
    assert cube.parent is None  # 拒否前に状態を変えない


def test_parent_set_circular_is_precondition(make_gateway):
    gw = make_gateway()
    a = _FakeObj("A")
    b = _FakeObj("B")
    b.parent = a  # B は既に A の子
    with pytest.raises(JsonRpcError) as ei:
        gw.parent_set([a], b)  # A を B の子にしようとする → 循環
    assert ei.value.message == ErrorCode.E_PRECONDITION
    assert a.parent is None  # 事前拒否＝実際には親子付けしない


def test_parent_set_assigns_parent_and_returns_results(make_gateway):
    gw = make_gateway()
    child = _FakeObj("Child")
    parent_obj = _FakeObj("Parent")
    results = gw.parent_set([child], parent_obj, keep_transform=False)
    assert child.parent is parent_obj
    assert results == [{"name": "Child", "parent": "Parent"}]


def test_parent_clear_keep_transform_restores_world(make_gateway):
    gw = make_gateway()
    child = _FakeObj("Child")
    parent_obj = _FakeObj("Parent")
    child.parent = parent_obj
    child.matrix_world = _FakeMatrixWorld((1.0, 2.0, 3.0))
    results = gw.parent_clear([child], keep_transform=True)
    assert child.parent is None
    assert results == [{"name": "Child", "parent": None}]
    restored = child.matrix_world.translation
    assert (restored.x, restored.y, restored.z) == (1.0, 2.0, 3.0)


def test_parent_clear_without_keep_transform_does_not_restore(make_gateway):
    gw = make_gateway()
    child = _FakeObj("Child")
    parent_obj = _FakeObj("Parent")
    child.parent = parent_obj
    original = _FakeMatrixWorld((5.0, 6.0, 7.0))
    child.matrix_world = original  # keep_transform=False なら再代入されず同一オブジェクトのまま
    gw.parent_clear([child], keep_transform=False)
    assert child.parent is None
    assert child.matrix_world is original


# ---- collection: unlink で所属0になる対象は全体を E_PRECONDITION（部分失敗させない）----


def test_unlink_from_collection_would_empty_membership_is_precondition(make_gateway):
    gw = make_gateway()
    col = _FakeCollection("Props")
    obj = _FakeObj("Cube")
    obj.users_collection = [col]  # 唯一の所属 collection
    with pytest.raises(JsonRpcError) as ei:
        gw.unlink_from_collection([obj], col)
    assert ei.value.message == ErrorCode.E_PRECONDITION
    assert col.objects.unlinked == []  # 全対象を検証してから外す＝部分的に状態を汚さない


def test_unlink_from_collection_succeeds_with_other_membership(make_gateway):
    gw = make_gateway()
    col = _FakeCollection("Props")
    other = _FakeCollection("Other")
    obj = _FakeObj("Cube")
    obj.users_collection = [col, other]
    results = gw.unlink_from_collection([obj], col)
    assert results == [{"name": "Cube", "unlinked": True}]
    assert col.objects.unlinked == [obj]


def test_unlink_from_collection_non_member_is_skipped_not_error(make_gateway):
    gw = make_gateway()
    col = _FakeCollection("Props")
    other = _FakeCollection("Other")
    obj = _FakeObj("Cube")
    obj.users_collection = [other]  # col には所属していない
    results = gw.unlink_from_collection([obj], col)
    assert results == [{"name": "Cube", "unlinked": False}]
    assert col.objects.unlinked == []


# ---- collection: create の重複拒否 ----


def test_create_collection_duplicate_is_precondition(make_gateway):
    gw = make_gateway(collections=(_FakeCollection("Props"),))
    with pytest.raises(JsonRpcError) as ei:
        gw.create_collection("Props")
    assert ei.value.message == ErrorCode.E_PRECONDITION


def test_require_collection_missing_is_target_not_found(make_gateway):
    gw = make_gateway()
    with pytest.raises(JsonRpcError) as ei:
        gw.require_collection("NoSuchCollection")
    assert ei.value.message == ErrorCode.E_TARGET_NOT_FOUND
    assert ei.value.data.category == "USER_INPUT"


# ---- P1-3: _fbx_operator_kwargs（export --format fbx の写像・純関数・bpy 非依存）----

_ALL_FBX_PROPS = {
    "axis_forward",
    "axis_up",
    "global_scale",
    "apply_unit_scale",
    "embed_textures",
    "path_mode",
}


def test_fbx_operator_kwargs_maps_bli_keys_to_operator_props(make_gateway):
    gw = make_gateway()
    kwargs = gw._fbx_operator_kwargs(
        {"axis_forward": "-Z", "axis_up": "Y", "scale": 2.0, "apply_unit_scale": False},
        _ALL_FBX_PROPS,
    )
    assert kwargs == {
        "axis_forward": "-Z",
        "axis_up": "Y",
        "global_scale": 2.0,
        "apply_unit_scale": False,
    }


def test_fbx_operator_kwargs_embed_textures_true_sets_path_mode_copy(make_gateway):
    gw = make_gateway()
    kwargs = gw._fbx_operator_kwargs({"embed_textures": True}, _ALL_FBX_PROPS)
    assert kwargs == {"embed_textures": True, "path_mode": "COPY"}


def test_fbx_operator_kwargs_embed_textures_false_does_not_set_path_mode(make_gateway):
    gw = make_gateway()
    kwargs = gw._fbx_operator_kwargs({"embed_textures": False}, _ALL_FBX_PROPS)
    assert kwargs == {"embed_textures": False}


def test_fbx_operator_kwargs_empty_options_is_empty(make_gateway):
    gw = make_gateway()
    assert gw._fbx_operator_kwargs({}, _ALL_FBX_PROPS) == {}


def test_fbx_operator_kwargs_missing_prop_raises_keyerror_not_silent_drop(make_gateway):
    # 写像先の operator プロパティが available_props に無ければ silent drop せず KeyError。
    gw = make_gateway()
    available = _ALL_FBX_PROPS - {"axis_forward"}
    with pytest.raises(KeyError) as ei:
        gw._fbx_operator_kwargs({"axis_forward": "-Z"}, available)
    assert ei.value.args[0] == "axis_forward"


def test_fbx_operator_kwargs_missing_path_mode_raises_keyerror(make_gateway):
    # embed_textures=True で path_mode プロパティが無ければ KeyError（COPY を付与できない）。
    gw = make_gateway()
    available = _ALL_FBX_PROPS - {"path_mode"}
    with pytest.raises(KeyError) as ei:
        gw._fbx_operator_kwargs({"embed_textures": True}, available)
    assert ei.value.args[0] == "path_mode"
