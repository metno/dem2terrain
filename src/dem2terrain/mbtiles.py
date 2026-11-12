# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

"""An XYZ PNG tile tree to and from an MBTiles file (rows stored in TMS order)."""

from __future__ import annotations

import shutil
import sqlite3
from os import PathLike
from pathlib import Path

StrPath = str | PathLike[str]


def xyz_to_tms_y(z: int, y: int) -> int:
    """XYZ tile row to TMS tile row at zoom ``z`` (the flip is its own inverse)."""

    return (1 << z) - 1 - y


tms_to_xyz_y = xyz_to_tms_y


def pack_xyz_dir_to_mbtiles(
    tiles_dir: StrPath,
    mbtiles_path: StrPath,
    minzoom: int,
    maxzoom: int,
    bounds: tuple[float, float, float, float],
    *,
    description: str = "Terrain RGB tiles",
) -> Path:
    """Write every ``{z}/{x}/{y}.png`` under ``tiles_dir`` into a new MBTiles file."""

    tiles_dir = Path(tiles_dir)
    mbtiles_path = Path(mbtiles_path)
    if mbtiles_path.exists():
        mbtiles_path.unlink()
    conn = sqlite3.connect(str(mbtiles_path))
    try:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE metadata (name TEXT, value TEXT);
            CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);
            CREATE UNIQUE INDEX tile_index on tiles (zoom_level, tile_column, tile_row);
            """
        )
        metadata = {
            "name": mbtiles_path.stem,
            "type": "baselayer",
            "version": "1.0.0",
            "description": description,
            "format": "png",
            "scheme": "tms",
            "minzoom": str(minzoom),
            "maxzoom": str(maxzoom),
            "bounds": ",".join(str(v) for v in bounds),
        }
        cur.executemany("INSERT INTO metadata(name, value) VALUES(?, ?)", metadata.items())

        def _numeric(p: Path) -> int:
            return int(p.name) if p.name.isdigit() else -1

        for z_dir in sorted(tiles_dir.iterdir(), key=_numeric):
            if not z_dir.is_dir() or not z_dir.name.isdigit():
                continue
            z = int(z_dir.name)
            for x_dir in sorted(z_dir.iterdir(), key=_numeric):
                if not x_dir.is_dir() or not x_dir.name.isdigit():
                    continue
                x = int(x_dir.name)
                for y_file in x_dir.glob("*.png"):
                    if not y_file.stem.isdigit():
                        continue
                    cur.execute(
                        "INSERT INTO tiles(zoom_level, tile_column, tile_row, tile_data) VALUES(?, ?, ?, ?)",
                        (z, x, xyz_to_tms_y(z, int(y_file.stem)), y_file.read_bytes()),
                    )
        conn.commit()
    finally:
        conn.close()
    return mbtiles_path


def unpack_mbtiles_to_xyz(mbtiles_path: StrPath, out_dir: StrPath) -> Path:
    """Write every tile of an MBTiles file out as ``{z}/{x}/{y}.png`` (replacing ``out_dir``)."""

    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(mbtiles_path))
    try:
        for z, x, y_tms, blob in conn.execute(
            "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"
        ):
            dst = out_dir / str(z) / str(x)
            dst.mkdir(parents=True, exist_ok=True)
            (dst / f"{tms_to_xyz_y(int(z), int(y_tms))}.png").write_bytes(blob)
    finally:
        conn.close()
    return out_dir
