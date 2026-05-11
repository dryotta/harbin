"""Tests for harbin.fleet.artifacts — including defensive path validation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from harbin.fleet.artifacts import ArtifactManager


@pytest.fixture
def arts(tmp_path):
    root = tmp_path / "arts"
    root.mkdir()
    return ArtifactManager(root=root, store=AsyncMock(), default_retention="30d")


def test_location_for_normal_inputs(arts) -> None:
    p = arts.location_for(fleet_name="my-fleet", task_label="morning", short_id="ab12cd")
    assert p.parts[-3:] == ("my-fleet", "morning", "ab12cd")


@pytest.mark.parametrize(
    "bad",
    [
        "../escape",
        "..",
        ".",
        "with/slash",
        "with\\backslash",
        "",
    ],
)
def test_location_for_rejects_unsafe(arts, bad) -> None:
    with pytest.raises(ValueError):
        arts.location_for(fleet_name=bad, task_label="t", short_id="x12345")
    with pytest.raises(ValueError):
        arts.location_for(fleet_name="ok", task_label=bad, short_id="x12345")
    with pytest.raises(ValueError):
        arts.location_for(fleet_name="ok", task_label="t", short_id=bad)


@pytest.mark.asyncio
async def test_prepare_creates_dir(arts) -> None:
    p = await arts.prepare(fleet_name="f", task_label="adhoc", short_id="abc123")
    assert p.exists()


def test_remove_fleet_tree_safe_rejects_bad_name(arts) -> None:
    # Should not raise — just log and skip.
    arts.remove_fleet_tree("../danger")
    assert (arts.root.parent / "danger").exists() is False


def test_remove_fleet_tree_removes_tree(arts) -> None:
    p = arts.root / "myfleet" / "adhoc" / "x"
    p.mkdir(parents=True)
    (p / "x.txt").write_text("x", encoding="utf-8")
    arts.remove_fleet_tree("myfleet")
    assert not (arts.root / "myfleet").exists()


def test_is_inside(arts, tmp_path) -> None:
    dock = tmp_path / "dock"
    dock.mkdir()
    inside = dock / "out" / "j"
    inside.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert ArtifactManager.is_inside(inside, dock) is True
    assert ArtifactManager.is_inside(outside, dock) is False
