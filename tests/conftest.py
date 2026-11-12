# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from osgeo import gdal, osr

NODATA = -9999.0


def write_dem(
    path: Path,
    data: np.ndarray,
    *,
    epsg: int,
    origin: tuple[float, float],
    pixel: float,
    nodata: float | None = NODATA,
) -> Path:
    """A single-band Float32 GeoTIFF with a north-up geotransform."""

    rows, cols = data.shape
    ds = gdal.GetDriverByName("GTiff").Create(str(path), cols, rows, 1, gdal.GDT_Float32)
    ds.SetGeoTransform([origin[0], pixel, 0.0, origin[1], 0.0, -pixel])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.WriteArray(data.astype("float32"))
    ds = None
    return path


def read(path: Path) -> np.ndarray:
    ds = gdal.Open(str(path))
    return ds.ReadAsArray()


def synthetic_heights(rows: int = 60, cols: int = 80) -> np.ndarray:
    """A smooth hill from about 50 m to 950 m."""

    y, x = np.mgrid[0:rows, 0:cols]
    r2 = ((x - cols / 2) / (cols / 3)) ** 2 + ((y - rows / 2) / (rows / 3)) ** 2
    return 50.0 + 900.0 * np.exp(-r2)


@pytest.fixture
def utm_dem(tmp_path: Path) -> Path:
    """A 80 x 60 DEM at 10 m in UTM 33N (Svalbard), with a 4 x 4 nodata hole."""

    data = synthetic_heights()
    data[20:24, 30:34] = NODATA
    return write_dem(tmp_path / "utm_dem.tif", data, epsg=32633, origin=(500000.0, 8700000.0), pixel=10.0)


@pytest.fixture
def wgs84_dem(tmp_path: Path) -> Path:
    """The same hill in EPSG:4326, about 3 x 2 km."""

    return write_dem(
        tmp_path / "wgs84_dem.tif", synthetic_heights(), epsg=4326, origin=(15.0, 78.2), pixel=0.0005
    )


def decode(rgb: np.ndarray) -> np.ndarray:
    """Terrain-RGB (bands first) back to metres."""

    r, g, b = (rgb[i].astype("float64") for i in range(3))
    return -10000.0 + (r * 65536.0 + g * 256.0 + b) * 0.1
