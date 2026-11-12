# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import numpy as np
import pytest
from conftest import NODATA, read, write_dem
from osgeo import gdal

from dem2terrain import bounds_wgs84, build_vrt, clamp, fill_nodata, reproject, smooth
from dem2terrain import prepare
from dem2terrain.errors import CommandError, SmoothingUnavailable


def test_reproject_to_web_mercator(tmp_path, utm_dem):
    out = reproject(utm_dem, tmp_path / "m.tif", "EPSG:3857", threads=2)
    ds = gdal.Open(str(out))
    assert "Pseudo-Mercator" in ds.GetProjectionRef() or "3857" in ds.GetProjectionRef()


def test_reproject_keeps_nodata_when_asked(tmp_path, utm_dem):
    out = reproject(
        utm_dem, tmp_path / "g.tif", "EPSG:4326", output_format="GTiff", overwrite=True, dst_nodata=NODATA
    )
    assert gdal.Open(str(out)).GetRasterBand(1).GetNoDataValue() == NODATA
    assert prepare.raster_info(out)["epsg"] == "4326"


def test_fill_nodata_closes_the_hole(tmp_path, utm_dem):
    before = read(utm_dem)
    assert (before == NODATA).sum() == 16
    after = read(fill_nodata(utm_dem, tmp_path / "f.tif"))
    assert (after == NODATA).sum() == 0
    assert 50.0 < after[21, 31] < 1000.0


def test_clamp(tmp_path):
    data = np.array([[-5.0, 10.0, 3000.0, np.nan]], dtype="float32")
    dem = write_dem(tmp_path / "c.tif", data, epsg=4326, origin=(0, 1), pixel=0.1, nodata=None)
    out = clamp(dem, tmp_path / "cl.tif", 0.0, 2000.0)
    np.testing.assert_array_equal(read(out)[0], [0.0, 10.0, 2000.0, 0.0])
    assert gdal.Open(str(out)).GetRasterBand(1).GetNoDataValue() == 0
    with pytest.raises(ValueError):
        clamp(dem, tmp_path / "x.tif", 3.0, 2.0)


def test_build_vrt_merges(tmp_path, wgs84_dem):
    east = write_dem(
        tmp_path / "east.tif", np.full((60, 80), 7.0), epsg=4326, origin=(15.04, 78.2), pixel=0.0005
    )
    vrt = build_vrt([wgs84_dem, east], tmp_path / "m.vrt")
    ds = gdal.Open(str(vrt))
    assert (ds.RasterXSize, ds.RasterYSize) == (160, 60)
    with pytest.raises(ValueError):
        build_vrt([], tmp_path / "e.vrt")


def test_smooth_refuses_without_its_tool(tmp_path, utm_dem, monkeypatch):
    monkeypatch.setattr(prepare.shutil, "which", lambda name: None)
    with pytest.raises(SmoothingUnavailable):
        smooth(utm_dem, tmp_path / "s.tif", 1.0)
    with pytest.raises(ValueError):
        smooth(utm_dem, tmp_path / "s.tif", -1.0)


def test_a_failing_tool_raises_command_error(tmp_path):
    with pytest.raises(CommandError) as err:
        fill_nodata(tmp_path / "missing.tif", tmp_path / "o.tif")
    assert err.value.returncode != 0 and err.value.args_run[0] == "gdal_fillnodata.py"


def test_bounds_wgs84(utm_dem, wgs84_dem):
    minlon, minlat, maxlon, maxlat = bounds_wgs84(wgs84_dem)
    assert (minlon, maxlat) == pytest.approx((15.0, 78.2))
    assert (maxlon, minlat) == pytest.approx((15.04, 78.17))
    minlon, minlat, maxlon, maxlat = bounds_wgs84(utm_dem)
    assert 14.9 < minlon < maxlon < 15.1 and 78.3 < minlat < maxlat < 78.5


def test_raster_info(utm_dem):
    assert prepare.raster_info(utm_dem) == {"bands": 1, "epsg": "32633", "nodata": NODATA}


def test_open_does_not_change_the_global_exception_mode(utm_dem):
    before = gdal.GetUseExceptions()
    prepare.raster_info(utm_dem)
    assert gdal.GetUseExceptions() == before
    with pytest.raises(ValueError):
        prepare.raster_info(utm_dem.parent / "nope.tif")
    assert gdal.GetUseExceptions() == before


def test_fill_nodata_leaves_nothing_in_the_working_directory(tmp_path, utm_dem, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    cwd.chmod(0o500)
    monkeypatch.chdir(cwd)
    try:
        fill_nodata(utm_dem, tmp_path / "f.tif")
    finally:
        cwd.chmod(0o700)
    assert list(cwd.iterdir()) == []
