# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dem2terrain import (
    DEFAULT_MAGO_IMAGE,
    Cancelled,
    CommandError,
    DockerRunner,
    LayerJsonError,
    LocalRunner,
    check_layer_json,
    mago_mesh,
)
from dem2terrain import prepare

FAKE = [sys.executable, str(Path(__file__).with_name("fake_mago.py"))]


def _args(out: Path) -> dict:
    return json.loads((out / "args.json").read_text())


def test_local_runner_stages_and_checks(tmp_path, utm_dem, wgs84_dem):
    work = tmp_path / "stage"
    result = mago_mesh(
        [utm_dem, wgs84_dem],
        tmp_path / "out",
        runner=LocalRunner(FAKE),
        names=["a", "b"],
        work_dir=work,
        keep_input=True,
        max_depth=9,
    )
    seen = _args(tmp_path / "out")
    assert seen["argv"] == ["-input", str(work), "-output", str(tmp_path / "out"), "-maxDepth", "9"]
    assert seen["inputs"] == ["a.tif", "b.tif"]
    assert result["layer"]["format"] == "quantized-mesh-1.0"
    assert result["vrt_path"] is None and result["input_kept"] is True
    # The UTM DEM was warped to EPSG:4326 with its nodata; the EPSG:4326 one copied unchanged.
    assert prepare.raster_info(work / "a.tif") == {"bands": 1, "epsg": "4326", "nodata": -9999.0}
    assert (work / "b.tif").read_bytes() == wgs84_dem.read_bytes()


def test_merge_to_vrt_and_cleanup(tmp_path, utm_dem, wgs84_dem):
    result = mago_mesh([utm_dem, wgs84_dem], tmp_path / "out", runner=LocalRunner(FAKE), merge_to_vrt=True)
    assert _args(tmp_path / "out")["inputs"] == ["merged.vrt", "utm_dem.tif", "wgs84_dem.tif"]
    assert result["vrt_path"].endswith("merged.vrt")
    assert not Path(result["input_dir"]).exists()


def test_no_reproject_copies(tmp_path, utm_dem):
    result = mago_mesh(
        utm_dem, tmp_path / "out", runner=LocalRunner(FAKE), reproject_to_wgs84=False, keep_input=True
    )
    assert Path(result["inputs"][0]).read_bytes() == utm_dem.read_bytes()


@pytest.mark.parametrize("mode", ["missing", "broken"])
def test_bad_layer_json_is_an_error(tmp_path, utm_dem, monkeypatch, mode):
    monkeypatch.setenv("FAKE_MAGO_LAYER", mode)
    with pytest.raises(LayerJsonError):
        mago_mesh(utm_dem, tmp_path / "out", runner=LocalRunner(FAKE))


def test_tool_failure_is_a_command_error(tmp_path, utm_dem, monkeypatch):
    monkeypatch.setenv("FAKE_MAGO_RC", "3")
    with pytest.raises(CommandError) as err:
        mago_mesh(utm_dem, tmp_path / "out", runner=LocalRunner(FAKE))
    assert err.value.returncode == 3


def test_check_layer_json(tmp_path):
    with pytest.raises(LayerJsonError):
        check_layer_json(tmp_path)
    (tmp_path / "layer.json").write_text(json.dumps({"tiles": []}))
    with pytest.raises(LayerJsonError, match="tiles"):
        check_layer_json(tmp_path)
    (tmp_path / "layer.json").write_text(json.dumps([1]))
    with pytest.raises(LayerJsonError):
        check_layer_json(tmp_path)
    (tmp_path / "layer.json").write_text(json.dumps({"tiles": ["{z}/{x}/{y}.terrain"]}))
    assert check_layer_json(tmp_path)["tiles"] == ["{z}/{x}/{y}.terrain"]


def test_arguments_are_checked_first(tmp_path, utm_dem, wgs84_dem):
    runner = LocalRunner(FAKE)
    with pytest.raises(ValueError, match="max_depth"):
        mago_mesh(utm_dem, tmp_path / "o", runner=runner, max_depth=0)
    with pytest.raises(ValueError, match="same name"):
        mago_mesh([utm_dem, utm_dem], tmp_path / "o", runner=runner)
    with pytest.raises(ValueError, match="one entry per DEM"):
        mago_mesh([utm_dem, wgs84_dem], tmp_path / "o", runner=runner, names=["x"])
    with pytest.raises(ValueError):
        mago_mesh([], tmp_path / "o", runner=runner)
    with pytest.raises(TypeError):
        mago_mesh(utm_dem, tmp_path / "o", runner=object())
    assert not (tmp_path / "o").exists()


def test_multiband_is_refused(tmp_path):
    from osgeo import gdal

    path = tmp_path / "rgb.tif"
    gdal.GetDriverByName("GTiff").Create(str(path), 4, 4, 3, gdal.GDT_Byte)
    with pytest.raises(ValueError, match="single-band"):
        mago_mesh(path, tmp_path / "o", runner=LocalRunner(FAKE))


def test_cancel_before_the_run(tmp_path, utm_dem):
    with pytest.raises(Cancelled):
        mago_mesh(utm_dem, tmp_path / "out", runner=LocalRunner(FAKE), cancel=lambda: True)
    assert not (tmp_path / "out" / "args.json").exists()


def test_existing_output_needs_overwrite(tmp_path, utm_dem):
    out = tmp_path / "out"
    out.mkdir()
    (out / "old").write_text("x")
    with pytest.raises(FileExistsError):
        mago_mesh(utm_dem, out, runner=LocalRunner(FAKE))
    mago_mesh(utm_dem, out, runner=LocalRunner(FAKE), overwrite=True)
    assert not (out / "old").exists()


def test_docker_command_default_mounts(tmp_path):
    r = DockerRunner(user="1000:1000")
    cmd = r.command(tmp_path / "in", tmp_path / "out", ["-maxDepth", "14"])
    assert cmd == [
        "docker", "run", "--rm", "--user", "1000:1000",
        "-v", f"{(tmp_path / 'in').resolve()}:/mago/input",
        "-v", f"{(tmp_path / 'out').resolve()}:/mago/output",
        DEFAULT_MAGO_IMAGE, "-input", "/mago/input", "-output", "/mago/output", "-maxDepth", "14",
    ]  # fmt: skip
    assert "@sha256:" in DEFAULT_MAGO_IMAGE


def test_docker_command_translates_through_mounts(tmp_path):
    (tmp_path / "data" / "in").mkdir(parents=True)
    r = DockerRunner("img:1", {tmp_path / "data": "/workspace", tmp_path / "data" / "in": "/deep"})
    cmd = r.command(tmp_path / "data" / "in" / "x", tmp_path / "data" / "out" / "y", [])
    assert cmd[-4:] == ["-input", "/deep/x", "-output", "/workspace/out/y"]
    with pytest.raises(ValueError, match="not under"):
        r.command(tmp_path / "elsewhere", tmp_path / "data", [])


def test_docker_command_named_volume_with_path_map(tmp_path):
    r = DockerRunner("img:1", {"stack_data_store": "/workspace"}, path_map={tmp_path: "/workspace"})
    cmd = r.command(tmp_path / "mago-input" / "n", tmp_path / "mago-terrain" / "n", [])
    assert ["-v", "stack_data_store:/workspace"] == cmd[3:5]
    assert cmd[-4:] == ["-input", "/workspace/mago-input/n", "-output", "/workspace/mago-terrain/n"]


def _image_available(image: str) -> bool:
    if not shutil.which("docker"):
        return False
    proc = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    return proc.returncode == 0


@pytest.mark.docker
@pytest.mark.skipif(
    not _image_available(os.environ.get("DEM2TERRAIN_MAGO_IMAGE", DEFAULT_MAGO_IMAGE)),
    reason="docker or the mago-3d-terrainer image is not available",
)
def test_docker_runner_real_tool(tmp_path, utm_dem):
    image = os.environ.get("DEM2TERRAIN_MAGO_IMAGE", DEFAULT_MAGO_IMAGE)
    runner = DockerRunner(image, user=f"{os.getuid()}:{os.getgid()}")
    result = mago_mesh(utm_dem, tmp_path / "out", runner=runner, max_depth=12)
    assert result["layer"]["format"] == "quantized-mesh-1.0"
    assert list((tmp_path / "out").rglob("*.terrain"))
