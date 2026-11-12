# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

"""dem2terrain: a single-band DEM in, terrain tiles out.

Two encoders:

- :func:`terrain_rgb` writes Mapbox terrain-RGB PNG tiles (an XYZ tree or an
  MBTiles file), for MapLibre ``raster-dem`` sources;
- :func:`mago_mesh` writes quantized-mesh terrain (``layer.json`` plus
  ``.terrain`` tiles, for Cesium) through mago-3d-terrainer, started by a
  runner the caller chooses (:class:`LocalRunner`, :class:`DockerRunner`, or
  its own).
"""

from dem2terrain.errors import Cancelled, CommandError, LayerJsonError, SmoothingUnavailable
from dem2terrain.mago import (
    DEFAULT_MAGO_IMAGE,
    DockerRunner,
    LocalRunner,
    MagoRunner,
    check_layer_json,
    mago_mesh,
)
from dem2terrain.mbtiles import pack_xyz_dir_to_mbtiles, unpack_mbtiles_to_xyz
from dem2terrain.prepare import (
    bounds_wgs84,
    build_vrt,
    clamp,
    fill_nodata,
    reproject,
    smooth,
)
from dem2terrain.rgb import encode_terrain_rgb, terrain_rgb

__version__ = "0.1.0"

__all__ = [
    "Cancelled",
    "CommandError",
    "DEFAULT_MAGO_IMAGE",
    "DockerRunner",
    "LayerJsonError",
    "LocalRunner",
    "MagoRunner",
    "SmoothingUnavailable",
    "__version__",
    "bounds_wgs84",
    "build_vrt",
    "check_layer_json",
    "clamp",
    "encode_terrain_rgb",
    "fill_nodata",
    "mago_mesh",
    "pack_xyz_dir_to_mbtiles",
    "reproject",
    "smooth",
    "terrain_rgb",
    "unpack_mbtiles_to_xyz",
]
