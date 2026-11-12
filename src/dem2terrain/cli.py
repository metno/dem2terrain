# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Command line: ``dem2terrain rgb`` and ``dem2terrain mago``."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from collections.abc import Sequence


def _parser() -> argparse.ArgumentParser:
    from dem2terrain import DEFAULT_MAGO_IMAGE, __version__

    p = argparse.ArgumentParser(prog="dem2terrain", description="DEM GeoTIFF to terrain tiles")
    p.add_argument("--version", action="version", version=f"dem2terrain {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    rgb = sub.add_parser("rgb", help="terrain-RGB PNG tiles (Mapbox encoding)")
    rgb.add_argument("dem")
    rgb.add_argument("out_dir")
    rgb.add_argument("--minzoom", type=int, default=6)
    rgb.add_argument("--maxzoom", type=int, default=14)
    rgb.add_argument("--tile-size", type=int, default=256, choices=(256, 512))
    rgb.add_argument("--mbtiles", action="store_true", help="pack the tiles into terrain.mbtiles")
    rgb.add_argument("--workers", type=int, default=0, help="0: one per CPU")
    rgb.add_argument("--no-preprocess", action="store_true", help="skip fill, clamp and smooth")
    rgb.add_argument("--min-elev", type=float, default=0.0)
    rgb.add_argument("--max-elev", type=float, default=2000.0)
    rgb.add_argument("--no-fill-nodata", action="store_true")
    rgb.add_argument("--smooth", action="store_true")
    rgb.add_argument("--smooth-sigma", type=float, default=1.0)
    rgb.add_argument("--overwrite", action="store_true")

    mago = sub.add_parser("mago", help="quantized-mesh terrain through mago-3d-terrainer")
    mago.add_argument("dem", nargs="+", help="one or more single-band DEMs")
    mago.add_argument("out_dir")
    how = mago.add_mutually_exclusive_group()
    how.add_argument(
        "--image",
        default=DEFAULT_MAGO_IMAGE,
        help="run the tool from this container image (the default)",
    )
    how.add_argument("--local", metavar="COMMAND", help='run the tool installed here, e.g. "java -jar mago.jar"')
    mago.add_argument("--user", help="docker --user (default: the current uid:gid)")
    mago.add_argument("--max-depth", type=int, default=14)
    mago.add_argument("--no-reproject", action="store_true")
    mago.add_argument("--merge-to-vrt", action="store_true")
    mago.add_argument("--keep-input", action="store_true")
    mago.add_argument("--overwrite", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "rgb":
        from dem2terrain import terrain_rgb

        result = terrain_rgb(
            args.dem,
            args.out_dir,
            minzoom=args.minzoom,
            maxzoom=args.maxzoom,
            tile_size=args.tile_size,
            mbtiles=args.mbtiles,
            workers=args.workers,
            preprocess=not args.no_preprocess,
            min_elev=args.min_elev,
            max_elev=args.max_elev,
            fill_nodata=not args.no_fill_nodata,
            smooth=args.smooth,
            smooth_sigma=args.smooth_sigma,
            overwrite=args.overwrite,
        )
    else:
        from dem2terrain import DockerRunner, LocalRunner, mago_mesh

        if args.local:
            runner = LocalRunner(shlex.split(args.local))
        else:
            user = args.user
            if user is None and hasattr(os, "getuid"):
                user = f"{os.getuid()}:{os.getgid()}"
            runner = DockerRunner(args.image, user=user)
        result = mago_mesh(
            args.dem,
            args.out_dir,
            runner=runner,
            max_depth=args.max_depth,
            reproject_to_wgs84=not args.no_reproject,
            merge_to_vrt=args.merge_to_vrt,
            keep_input=args.keep_input,
            overwrite=args.overwrite,
        )
        result["layer"] = {k: result["layer"].get(k) for k in ("format", "tiles", "bounds", "projection")}

    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
