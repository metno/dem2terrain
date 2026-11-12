# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

"""DEM preparation: reproject, merge to VRT, fill nodata, clamp, smooth.

Each step runs the GDAL command-line utility it has always run, with the same
arguments, so a DEM prepared here is the same file, byte for byte, as one
prepared by the service code these steps come from (on the same GDAL).
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from os import PathLike
from pathlib import Path

from dem2terrain._run import run
from dem2terrain.errors import SmoothingUnavailable

StrPath = str | PathLike[str]

#: The Gaussian filter :func:`smooth` runs. It is not one of GDAL's own
#: utilities, so it is looked up on PATH at call time.
SMOOTH_TOOL = "gdal_filter.py"


def reproject(
    src: StrPath,
    dst: StrPath,
    t_srs: str = "EPSG:3857",
    *,
    resampling: str = "bilinear",
    threads: int | None = None,
    dst_nodata: float | None = None,
    output_format: str | None = None,
    overwrite: bool = False,
) -> Path:
    """Warp ``src`` to ``t_srs`` with ``gdalwarp``.

    ``threads`` turns on multi-threaded warping (``GDAL_NUM_THREADS``,
    ``-multi``, ``NUM_THREADS``), as the terrain-RGB path does.
    """

    cmd: list[str] = ["gdalwarp"]
    if threads:
        cmd += ["--config", "GDAL_NUM_THREADS", str(threads)]
    cmd += ["-t_srs", t_srs, "-r", resampling]
    if threads:
        cmd += ["-multi", "-wo", f"NUM_THREADS={threads}"]
    if output_format:
        cmd += ["-of", output_format]
    if overwrite:
        cmd += ["-overwrite"]
    if dst_nodata is not None:
        cmd += ["-dstnodata", str(dst_nodata)]
    cmd += [str(src), str(dst)]
    run(cmd)
    return Path(dst)


def build_vrt(sources: Sequence[StrPath], dst: StrPath) -> Path:
    """Merge ``sources`` into one VRT mosaic with ``gdalbuildvrt``."""

    if not sources:
        raise ValueError("build_vrt needs at least one source")
    run(["gdalbuildvrt", "-overwrite", str(dst), *[str(p) for p in sources]])
    return Path(dst)


def fill_nodata(
    src: StrPath,
    dst: StrPath,
    *,
    max_distance: int = 5,
    smoothing_iterations: int = 2,
) -> Path:
    """Interpolate over nodata holes with ``gdal_fillnodata.py``.

    The tool writes its work files into the current directory, so it runs in
    the output's directory rather than wherever the caller happens to be.
    """

    dst_path = Path(dst).absolute()
    run(
        [
            "gdal_fillnodata.py",
            "-md",
            str(max_distance),
            "-si",
            str(smoothing_iterations),
            str(Path(src).absolute()),
            str(dst_path),
        ],
        cwd=dst_path.parent,
    )
    return Path(dst)


def clamp(src: StrPath, dst: StrPath, min_elev: float, max_elev: float) -> Path:
    """Clip elevations to ``[min_elev, max_elev]``; non-finite cells become 0 (nodata)."""

    min_val = float(min_elev)
    max_val = float(max_elev)
    if min_val > max_val:
        raise ValueError("min_elev must be <= max_elev")
    run(
        [
            "gdal_calc.py",
            "-A",
            str(src),
            f"--calc=where(isfinite(A),clip(A,{min_val},{max_val}),0)",
            "--outfile",
            str(dst),
            "--type",
            "Float32",
            "--NoDataValue=0",
        ]
    )
    return Path(dst)


def smoothing_available() -> bool:
    """Whether :func:`smooth` can run here."""

    return shutil.which(SMOOTH_TOOL) is not None


def smooth(src: StrPath, dst: StrPath, sigma: float = 1.0) -> Path:
    """Gaussian smoothing with ``gdal_filter.py -gauss``.

    Raises :class:`SmoothingUnavailable` when that tool is not on PATH: it is
    not shipped with GDAL, and no other filter is substituted for it, since a
    different filter would change the tiles.
    """

    sigma = float(sigma)
    if sigma <= 0:
        raise ValueError("smooth_sigma must be > 0")
    if not smoothing_available():
        raise SmoothingUnavailable(
            f"smoothing needs {SMOOTH_TOOL} on PATH, and it is not installed here"
        )
    run([SMOOTH_TOOL, "-gauss", "-sigma", str(sigma), str(src), str(dst)])
    return Path(dst)


def _open(path: StrPath):
    from osgeo import gdal

    # Scoped, so the caller's process-wide exception setting is left alone.
    try:
        with gdal.ExceptionMgr(useExceptions=True):
            ds = gdal.Open(str(path))
    except RuntimeError as exc:
        raise ValueError(f"Unable to open raster: {path}") from exc
    if ds is None:
        raise ValueError(f"Unable to open raster: {path}")
    return ds


def is_georeferenced(path: StrPath) -> bool:
    """Whether the raster has both a geotransform and a projection."""

    ds = _open(path)
    return ds.GetGeoTransform(can_return_null=True) is not None and bool(
        (ds.GetProjectionRef() or "").strip()
    )


def raster_info(path: StrPath) -> dict[str, object]:
    """Band count, EPSG code (``""`` when none is identified) and band-1 nodata."""

    from osgeo import osr

    ds = _open(path)
    epsg = ""
    wkt = ds.GetProjectionRef() or ""
    if wkt.strip():
        srs = osr.SpatialReference()
        srs.ImportFromWkt(wkt)
        try:
            srs.AutoIdentifyEPSG()
        except RuntimeError:
            pass
        epsg = srs.GetAuthorityCode(None) or ""
    nodata = ds.GetRasterBand(1).GetNoDataValue() if ds.RasterCount else None
    return {"bands": ds.RasterCount, "epsg": str(epsg), "nodata": nodata}


def bounds_wgs84(path: StrPath) -> tuple[float, float, float, float]:
    """Bounds in EPSG:4326 (lon/lat order) from the raster's four corners.

    A raster without georeferencing gets the whole web-mercator world.
    """

    from osgeo import osr

    ds = _open(path)
    gt = ds.GetGeoTransform(can_return_null=True)
    wkt = ds.GetProjectionRef() or ""
    if gt is None or not wkt.strip():
        return -180.0, -85.0, 180.0, 85.0

    width = ds.RasterXSize
    height = ds.RasterYSize

    def px_to_geo(px: float, py: float) -> tuple[float, float]:
        return gt[0] + px * gt[1] + py * gt[2], gt[3] + px * gt[4] + py * gt[5]

    src = osr.SpatialReference()
    src.ImportFromWkt(wkt)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(4326)
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tx = osr.CoordinateTransformation(src, dst)

    corners = [px_to_geo(0, 0), px_to_geo(width, 0), px_to_geo(0, height), px_to_geo(width, height)]
    ll = [tx.TransformPoint(x, y) for x, y in corners]
    lons = [p[0] for p in ll]
    lats = [p[1] for p in ll]
    return min(lons), min(lats), max(lons), max(lats)
