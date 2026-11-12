# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Terrain-RGB (Mapbox encoding) PNG tiles from a DEM.

The elevation ``h`` in metres is stored as ``(h + 10000) / 0.1`` split over the
three bytes of a pixel; a reader gets it back as
``-10000 + (R * 65536 + G * 256 + B) * 0.1``.
"""

from __future__ import annotations

import os
import shutil
from os import PathLike
from pathlib import Path
from tempfile import TemporaryDirectory

from dem2terrain import prepare
from dem2terrain._run import CancelCheck, check_cancel, run
from dem2terrain.errors import SmoothingUnavailable
from dem2terrain.mbtiles import pack_xyz_dir_to_mbtiles

StrPath = str | PathLike[str]

# One gdal_calc expression per output band; non-finite cells become 0 (nodata).
_RGB_CALC = (
    "where(isfinite(A),clip(floor((A+10000.0)/0.1/65536.0),0,255),0)",
    "where(isfinite(A),clip(floor(mod((A+10000.0)/0.1,65536)/256.0),0,255),0)",
    "where(isfinite(A),clip(floor(mod((A+10000.0)/0.1,256)),0,255),0)",
)

TILES_DIRNAME = "tiles"
MBTILES_FILENAME = "terrain.mbtiles"


def encode_terrain_rgb(dem: StrPath, dst: StrPath, *, work_dir: StrPath | None = None) -> Path:
    """Write the 3-band Byte terrain-RGB GeoTIFF of ``dem`` (tiled, DEFLATE)."""

    with TemporaryDirectory(prefix="dem2terrain_rgb_", dir=work_dir) as tmp:
        tmp_path = Path(tmp)
        bands = [tmp_path / "r.tif", tmp_path / "g.tif", tmp_path / "b.tif"]
        for calc, band in zip(_RGB_CALC, bands):
            run(
                [
                    "gdal_calc.py",
                    "-A",
                    str(dem),
                    f"--calc={calc}",
                    "--outfile",
                    str(band),
                    "--type",
                    "Byte",
                    "--NoDataValue=0",
                ]
            )
        run(
            [
                "gdal_merge.py",
                "-separate",
                "-o",
                str(dst),
                *[str(b) for b in bands],
                "-co",
                "TILED=YES",
                "-co",
                "COMPRESS=DEFLATE",
            ]
        )
    return Path(dst)


def _prepare_out_dir(out_dir: Path, overwrite: bool) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        if not overwrite:
            raise FileExistsError(f"{out_dir} is not empty; pass overwrite=True to replace it")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)


def terrain_rgb(
    dem: StrPath,
    out_dir: StrPath,
    *,
    minzoom: int = 6,
    maxzoom: int = 14,
    tile_size: int = 256,
    mbtiles: bool = False,
    workers: int = 0,
    preprocess: bool = True,
    min_elev: float = 0.0,
    max_elev: float = 2000.0,
    fill_nodata: bool = True,
    smooth: bool = False,
    smooth_sigma: float = 1.0,
    overwrite: bool = False,
    cancel: CancelCheck | None = None,
) -> dict[str, object]:
    """Terrain-RGB tiles of a single-band DEM.

    Steps: warp to EPSG:3857 (bilinear); with ``preprocess``, fill nodata
    (when ``fill_nodata``), clamp to ``[min_elev, max_elev]`` and smooth (when
    ``smooth``); encode; cut XYZ PNG tiles with ``gdal2tiles.py`` into
    ``out_dir/tiles``, or pack them into ``out_dir/terrain.mbtiles`` when
    ``mbtiles``. ``workers`` 0 means one per CPU. ``cancel`` is called between
    steps; when it returns true, :class:`~dem2terrain.Cancelled` is raised.

    Returns a description: the backend, the path written, the zooms, the tile
    size, the workers used and the DEM's bounds in EPSG:4326.
    """

    if minzoom < 0 or maxzoom > 22 or minzoom > maxzoom:
        raise ValueError("Invalid minzoom/maxzoom")
    if tile_size not in {256, 512}:
        raise ValueError("tile_size must be 256 or 512")
    if workers < 0:
        raise ValueError("workers must be >= 0")
    if preprocess and float(min_elev) > float(max_elev):
        raise ValueError("min_elev must be <= max_elev")
    if preprocess and smooth:
        if float(smooth_sigma) <= 0:
            raise ValueError("smooth_sigma must be > 0")
        if not prepare.smoothing_available():
            raise SmoothingUnavailable(
                f"smoothing needs {prepare.SMOOTH_TOOL} on PATH, and it is not installed here"
            )

    dem = Path(dem)
    if not dem.exists():
        raise FileNotFoundError(dem)
    if not prepare.is_georeferenced(dem):
        raise ValueError("Terrain tiles require a georeferenced raster with projection and geotransform")

    workers_used = workers if workers > 0 else max(1, os.cpu_count() or 1)
    out_dir = Path(out_dir)
    _prepare_out_dir(out_dir, overwrite)
    tiles_dir = out_dir / TILES_DIRNAME
    tiles_dir.mkdir()

    with TemporaryDirectory(prefix="dem2terrain_") as tmp:
        tmp_path = Path(tmp)
        check_cancel(cancel)
        current = prepare.reproject(dem, tmp_path / "dem_3857.tif", "EPSG:3857", threads=workers_used)

        if preprocess and fill_nodata:
            check_cancel(cancel)
            current = prepare.fill_nodata(current, tmp_path / "dem_3857_filled.tif")
        if preprocess:
            check_cancel(cancel)
            current = prepare.clamp(current, tmp_path / "dem_3857_clamped.tif", min_elev, max_elev)
        if preprocess and smooth:
            check_cancel(cancel)
            current = prepare.smooth(current, tmp_path / "dem_3857_smoothed.tif", smooth_sigma)

        check_cancel(cancel)
        rgb_tif = encode_terrain_rgb(current, tmp_path / "terrain_rgb.tif", work_dir=tmp_path)

        check_cancel(cancel)
        run(
            [
                "gdal2tiles.py",
                "--xyz",
                f"--zoom={minzoom}-{maxzoom}",
                f"--tilesize={tile_size}",
                f"--processes={workers_used}",
                "--tiledriver=PNG",
                str(rgb_tif),
                str(tiles_dir),
            ]
        )

    bounds = prepare.bounds_wgs84(dem)
    result: dict[str, object] = {
        "encoding": "mapbox",
        "backend": "directory",
        "tiles_dir": str(tiles_dir),
        "mbtiles": None,
        "minzoom": minzoom,
        "maxzoom": maxzoom,
        "tile_size": tile_size,
        "workers": workers_used,
        "bounds": bounds,
    }
    if mbtiles:
        mbtiles_path = out_dir / MBTILES_FILENAME
        pack_xyz_dir_to_mbtiles(tiles_dir, mbtiles_path, minzoom, maxzoom, bounds)
        shutil.rmtree(tiles_dir)
        result.update(backend="mbtiles", tiles_dir=None, mbtiles=str(mbtiles_path))
    return result
