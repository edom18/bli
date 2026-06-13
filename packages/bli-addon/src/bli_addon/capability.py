"""能力検出（M3 / research.md 論点3 + 付録A）。

operator の実在は `get_rna_type()` 成功で判定する（hasattr では旧名 stub を
誤検出するため不十分）。候補表は M0.5 実機ダンプで確定したもの。
"""

from __future__ import annotations

# 論理キー -> 優先順の候補 operator（M0.5 で 5.0/4.4 実機確定）
RESOLVERS: dict[str, list[str]] = {
    "export.stl": ["wm.stl_export"],
    "import.stl": ["wm.stl_import"],
    "export.obj": ["wm.obj_export"],
    "import.obj": ["wm.obj_import"],
    "export.gltf": ["export_scene.gltf"],
    "import.gltf": ["import_scene.gltf"],
    "export.fbx": ["export_scene.fbx"],
    "import.fbx": ["wm.fbx_import", "import_scene.fbx"],  # 5.0=新C++ / 4.4=旧
    "export.3mf": ["export_mesh.3mf"],  # 標準では未提供（addon要）
    "import.3mf": ["import_mesh.3mf"],
    "origin_set": ["object.origin_set"],
    "transform_apply": ["object.transform_apply"],
}


def operator_real(path: str) -> bool:
    """`get_rna_type()` 成功で operator 実体ありと判定する。"""
    import bpy  # type: ignore

    ns, _, name = path.partition(".")
    group = getattr(bpy.ops, ns, None)
    if group is None or not hasattr(group, name):
        return False
    try:
        getattr(group, name).get_rna_type()
        return True
    except Exception:
        return False


class CapabilityRegistry:
    """論理キー -> 実在 operator の解決をキャッシュする。"""

    def __init__(self) -> None:
        self._resolved: dict[str, str | None] = {}

    def resolve(self, key: str) -> str | None:
        if key in self._resolved:
            return self._resolved[key]
        chosen: str | None = None
        for cand in RESOLVERS.get(key, []):
            if operator_real(cand):
                chosen = cand
                break
        self._resolved[key] = chosen
        return chosen

    def available(self, key: str) -> bool:
        return self.resolve(key) is not None

    def list_capabilities(self) -> list[str]:
        """実在する operator のフラットな一覧（hello-ok の capabilities 用）。"""
        out: list[str] = []
        for cands in RESOLVERS.values():
            for cand in cands:
                if operator_real(cand):
                    out.append(cand)
                    break
        return sorted(set(out))
