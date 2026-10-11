"""Tests the GLB/FBX routing logic in scripts/blender_rig_export.py WITHOUT a
real Blender (bpy is mocked). This cannot validate the actual import/weight-
transfer behavior -- that needs real Blender+Sollumz, unavailable on this
sandbox (see docs/architecture-decisions.md point 9) -- it only locks down
that the right bpy operator is dispatched per file extension and that
unsupported extensions fail loudly instead of silently doing nothing.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "blender_rig_export.py"


@pytest.fixture
def fake_bpy_module(monkeypatch):
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.ops = MagicMock()
    fake_bpy.context = MagicMock()
    fake_bpy.data = MagicMock()
    fake_bpy.context.selected_objects = ["sentinel-selected-objects"]
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    return fake_bpy


def _load_module(fake_bpy_module):
    import importlib.util
    spec = importlib.util.spec_from_file_location("blender_rig_export", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fbx_routes_to_fbx_importer(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    result = module.import_source_mesh("/tmp/whatever.fbx")

    fake_bpy_module.ops.import_scene.fbx.assert_called_once_with(filepath="/tmp/whatever.fbx", use_anim=False)
    fake_bpy_module.ops.import_scene.gltf.assert_not_called()
    assert result == ["sentinel-selected-objects"]


def test_glb_routes_to_gltf_importer(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    result = module.import_source_mesh("/tmp/whatever.glb")

    fake_bpy_module.ops.import_scene.gltf.assert_called_once_with(filepath="/tmp/whatever.glb")
    fake_bpy_module.ops.import_scene.fbx.assert_not_called()
    assert result == ["sentinel-selected-objects"]


def test_gltf_extension_also_routes_to_gltf_importer(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    module.import_source_mesh("/tmp/whatever.gltf")
    fake_bpy_module.ops.import_scene.gltf.assert_called_once()


def test_unsupported_extension_raises(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    with pytest.raises(RuntimeError, match="Unsupported source mesh extension"):
        module.import_source_mesh("/tmp/whatever.obj")


def test_case_insensitive_extension(fake_bpy_module):
    module = _load_module(fake_bpy_module)
    module.import_source_mesh("/tmp/WHATEVER.GLB")
    fake_bpy_module.ops.import_scene.gltf.assert_called_once()
