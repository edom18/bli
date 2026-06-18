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


def test_is_heavy_request_whole_command():
    # import/export/print-check/print-repair はコマンド全体が heavy（M10）。
    cmds = load_definitions()
    assert c.is_heavy_request(cmds["import"], {"format": "stl", "path": "x.stl"}) is True
    assert c.is_heavy_request(cmds["export"], {"format": "stl", "path": "x.stl"}) is True
    assert c.is_heavy_request(cmds["print-check"], {"targets": "Cube"}) is True
    assert c.is_heavy_request(cmds["print-repair"], {"targets": "Cube"}) is True


def test_is_heavy_request_mesh_op_dependent():
    # mesh は boolean/decimate（heavy_ops）だけ heavy。軽い op は heavy でない。
    cmds = load_definitions()
    assert c.is_heavy_request(cmds["mesh"], {"op": "boolean", "targets": "A"}) is True
    assert c.is_heavy_request(cmds["mesh"], {"op": "decimate", "targets": "A"}) is True
    assert c.is_heavy_request(cmds["mesh"], {"op": "recalc-normals", "targets": "A"}) is False
    assert c.is_heavy_request(cmds["mesh"], {"op": "merge-by-distance", "targets": "A"}) is False
    assert c.is_heavy_request(cmds["mesh"], {"targets": "A"}) is False  # op 欠落も非 heavy


def test_is_heavy_request_light_commands():
    # 情報取得/軽量編集は heavy でない。
    cmds = load_definitions()
    assert c.is_heavy_request(cmds["scene-info"], {}) is False
    assert c.is_heavy_request(cmds["transform"], {"targets": "Cube"}) is False
    assert c.is_heavy_request(cmds["save"], {}) is False
