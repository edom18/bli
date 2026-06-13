"""共有 enum（ParamType / Mode / Stability）。data-model.md §1。純Python。"""

from __future__ import annotations

from enum import Enum


class ParamType(str, Enum):
    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    ENUM = "enum"
    VEC3 = "vec3"
    PATH = "path"


class Mode(str, Enum):
    OBJECT = "OBJECT"
    EDIT = "EDIT"
    ANY = "ANY"


class Stability(str, Enum):
    STABLE = "stable"
    EXPERIMENTAL = "experimental"
