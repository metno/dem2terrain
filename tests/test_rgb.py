# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from conftest import decode, read, synthetic_heights, write_dem

import dem2terrain
from dem2terrain import (
    Cancelled,
    SmoothingUnavailable,
    encode_terrain_rgb,
    pack_xyz_dir_to_mbtiles,
    terrain_rgb,
    unpack_mbtiles_to_xyz,
)
from dem2terrain.mbtiles import tms_to_xyz_y, xyz_to_tms_y


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*.png"))
    }


def test_encoding_round_trips_to_a_tenth_of_a_metre(tmp_path, wgs84_dem):
    out = encode_terrain_rgb(wgs84_dem, tmp_path / "rgb.tif")
    rgb = read(out)
    assert rgb.shape == (3, 60, 80) and rgb.dtype == np.uint8
    heights = synthetic_heights().astype("float32").astype("float64")
    decoded = decode(rgb)
    # 0.1 m steps, computed in Float32: within one step of the source.
    assert np.abs(decoded - heights).max() <= 0.1 + 1e-3


def test_known_values(tmp_path):
    data = np.array([[0.0, 1.0, 8848.8, -10.5]], dtype="float32")
    dem = write_dem(tmp_path / "v.tif", data, epsg=4326, origin=(0.0, 1.0), pixel=0.1, nodata=None)
    rgb = read(encode_terrain_rgb(dem, tmp_path / "rgb.tif"))
    # (0 + 10000) / 0.1 = 100000 = 1*65536 + 134*256 + 160
    assert tuple(rgb[:, 0, 0]) == (1, 134, 160)
    np.testing.assert_allclose(decode(rgb)[0], [0.0, 1.0, 8848.8, -10.5], atol=0.1)


def test_terrain_rgb_writes_xyz_tiles(tmp_path, utm_dem):
    result = terrain_rgb(utm_dem, tmp_path / "out", minzoom=10, maxzoom=13, workers=1)
    tiles = Path(result["tiles_dir"])
    assert result["backend"] == "directory" and result["encoding"] == "mapbox"
    assert result["workers"] == 1 and result["mbtiles"] is None
    for z in range(10, 14):
        pngs = list((tiles / str(z)).rglob("*.png"))
        assert pngs, f"no tiles at zoom {z}"
    tile = next((tiles / "13").rglob("*.png"))
    arr = read(tile)
    assert arr.shape[1:] == (256, 256) and arr.shape[0] in {3, 4}
    minlon, minlat, maxlon, maxlat = result["bounds"]
    assert 14.9 < minlon < maxlon < 15.1 and 78.3 < minlat < maxlat < 78.5


def test_tiles_decode_to_the_clamped_heights(tmp_path, utm_dem):
    result = terrain_rgb(utm_dem, tmp_path / "out", minzoom=14, maxzoom=14, workers=1, max_elev=500.0)
    decoded = []
    for png in Path(result["tiles_dir"]).rglob("*.png"):
        arr = read(png)
        # gdal2tiles writes RGBA; the alpha band marks the cells with data.
        decoded.append(decode(arr[:3])[arr[3] == 255])
    values = np.concatenate(decoded)
    assert values.min() >= 0.0
    # gdal2tiles resamples each byte of the encoding on its own (its default
    # is "average"), so where a byte carries over between neighbours the
    # decoded height jumps by up to one G step (25.6 m). Kept as the service
    # has it; here it is a few tenths of a percent of the pixels.
    assert np.median(values) < 500.0
    assert (values > 500.1).mean() < 0.01
    assert values.max() < 500.0 + 25.6


def test_the_same_dem_gives_the_same_bytes(tmp_path, utm_dem):
    a = terrain_rgb(utm_dem, tmp_path / "a", minzoom=11, maxzoom=12, workers=2)
    b = terrain_rgb(utm_dem, tmp_path / "b", minzoom=11, maxzoom=12, workers=1)
    da, db = _tree_digest(Path(a["tiles_dir"])), _tree_digest(Path(b["tiles_dir"]))
    assert da and da == db


def test_mbtiles_backend(tmp_path, utm_dem):
    plain = terrain_rgb(utm_dem, tmp_path / "plain", minzoom=11, maxzoom=12, workers=1)
    packed = terrain_rgb(utm_dem, tmp_path / "packed", minzoom=11, maxzoom=12, workers=1, mbtiles=True)
    assert packed["backend"] == "mbtiles" and packed["tiles_dir"] is None
    assert not (tmp_path / "packed" / "tiles").exists()
    with sqlite3.connect(packed["mbtiles"]) as conn:
        meta = dict(conn.execute("SELECT name, value FROM metadata"))
        count = conn.execute("SELECT count(*) FROM tiles").fetchone()[0]
    assert meta["scheme"] == "tms" and meta["minzoom"] == "11" and meta["maxzoom"] == "12"
    plain_digest = _tree_digest(Path(plain["tiles_dir"]))
    assert count == len(plain_digest)
    unpacked = unpack_mbtiles_to_xyz(packed["mbtiles"], tmp_path / "unpacked")
    assert _tree_digest(unpacked) == plain_digest


def test_pack_flips_rows_to_tms(tmp_path):
    (tmp_path / "t" / "3" / "2").mkdir(parents=True)
    (tmp_path / "t" / "3" / "2" / "1.png").write_bytes(b"png")
    mb = pack_xyz_dir_to_mbtiles(tmp_path / "t", tmp_path / "x.mbtiles", 3, 3, (0, 0, 1, 1))
    with sqlite3.connect(mb) as conn:
        assert conn.execute("SELECT zoom_level, tile_column, tile_row FROM tiles").fetchall() == [(3, 2, 6)]
    assert xyz_to_tms_y(3, 1) == 6 and tms_to_xyz_y(3, 6) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minzoom": 5, "maxzoom": 4},
        {"maxzoom": 23},
        {"tile_size": 300},
        {"workers": -1},
        {"min_elev": 10.0, "max_elev": 5.0},
        {"smooth": True, "smooth_sigma": 0.0},
    ],
)
def test_bad_arguments_fail_before_any_output(tmp_path, utm_dem, kwargs):
    with pytest.raises(ValueError):
        terrain_rgb(utm_dem, tmp_path / "out", **kwargs)
    assert not (tmp_path / "out").exists()


def test_ungeoreferenced_dem_is_refused(tmp_path):
    from osgeo import gdal

    path = tmp_path / "plain.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(path), 4, 4, 1, gdal.GDT_Float32)
    ds = None
    with pytest.raises(ValueError, match="georeferenced"):
        terrain_rgb(path, tmp_path / "out")


def test_smoothing_without_its_tool_is_a_clear_error(tmp_path, utm_dem, monkeypatch):
    monkeypatch.setattr(dem2terrain.prepare, "smoothing_available", lambda: False)
    with pytest.raises(SmoothingUnavailable, match="gdal_filter.py"):
        terrain_rgb(utm_dem, tmp_path / "out", smooth=True)
    assert not (tmp_path / "out").exists()


def test_smoothing_is_skipped_without_preprocess(tmp_path, utm_dem, monkeypatch):
    monkeypatch.setattr(dem2terrain.prepare, "smoothing_available", lambda: False)
    result = terrain_rgb(
        utm_dem, tmp_path / "out", smooth=True, preprocess=False, minzoom=11, maxzoom=11, workers=1
    )
    assert list(Path(result["tiles_dir"]).rglob("*.png"))


def test_cancel_stops_the_run(tmp_path, utm_dem):
    calls = []

    def cancel():
        calls.append(1)
        return len(calls) >= 2

    with pytest.raises(Cancelled):
        terrain_rgb(utm_dem, tmp_path / "out", minzoom=11, maxzoom=11, workers=1, cancel=cancel)
    assert len(calls) == 2


def test_existing_output_is_kept_unless_overwrite(tmp_path, utm_dem):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    with pytest.raises(FileExistsError):
        terrain_rgb(utm_dem, out)
    assert (out / "keep.txt").exists()
    terrain_rgb(utm_dem, out, overwrite=True, minzoom=11, maxzoom=11, workers=1)
    assert not (out / "keep.txt").exists()
