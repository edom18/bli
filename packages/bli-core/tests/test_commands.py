"""Command/Param レジストリのユニット（L1）。"""

from __future__ import annotations

import pytest

from bli_core import commands as c
from bli_core.commands import load_definitions
from bli_core.types import Mode, ParamType


def test_load_definitions_populates_registry():
    cmds = load_definitions()
    assert "ping" in cmds
    assert "set-origin" in cmds
    assert cmds["set-origin"].mutates is True
    assert cmds["set-origin"].required_mode is Mode.OBJECT


def test_get_command():
    load_definitions()
    assert c.get_command("ping") is not None
    assert c.get_command("does-not-exist") is None


def test_set_origin_params():
    load_definitions()
    cmd = c.get_command("set-origin")
    names = {p.name for p in cmd.params}
    assert {"targets", "to", "x", "y", "z"} <= names
    to = next(p for p in cmd.params if p.name == "to")
    assert to.type is ParamType.ENUM
    assert to.required is True
    assert to.choices == ["geometry", "cursor", "world"]


def test_duplicate_registration_raises():
    with pytest.raises(ValueError):
        c.command("ping", "dup")  # 既に登録済み
