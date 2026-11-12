# dem2terrain

A single-band DEM (GeoTIFF or anything GDAL reads) in, terrain tiles out:

- **terrain-RGB** PNG tiles in the Mapbox encoding, as an XYZ tree or an
  MBTiles file, for MapLibre `raster-dem` sources;
- **quantized-mesh** terrain (`layer.json` plus `.terrain` tiles) for Cesium,
  made by [mago-3d-terrainer](https://github.com/Gaia3D/mago-3d-terrainer),
  which runs on this machine or from its container image.

Before either, the DEM can be prepared: reprojected, merged with others into a
VRT, its nodata holes filled, its heights clamped, smoothed.

Licence: GPL-3.0-or-later (see `LICENSE`).
© 2026 Norwegian Meteorological Institute (MET Norway).

## Install

```sh
pip install "dem2terrain @ git+https://github.com/metno/dem2terrain@v0.1.0"
```

dem2terrain calls GDAL's command-line utilities (`gdalwarp`, `gdalbuildvrt`,
`gdal_fillnodata.py`, `gdal_calc.py`, `gdal_merge.py`, `gdal2tiles.py`) and
its Python bindings (`osgeo`). Neither has a wheel, so they come from the
system's GDAL (on Debian or Ubuntu: `apt install gdal-bin python3-gdal`). The
quantized-mesh path also needs mago-3d-terrainer: Docker and its image, or the
tool itself.

## Terrain-RGB

```python
from dem2terrain import terrain_rgb

result = terrain_rgb("dem.tif", "out/", minzoom=6, maxzoom=14)
# out/tiles/{z}/{x}/{y}.png; with mbtiles=True, out/terrain.mbtiles instead
```

The steps: warp to EPSG:3857 (bilinear); fill nodata (`gdal_fillnodata.py
-md 5 -si 2`); clamp to `[min_elev, max_elev]` (default 0–2000 m), with
non-finite cells set to 0; encode; cut the tiles with `gdal2tiles.py --xyz`.
`preprocess=False` skips the fill, the clamp and the smoothing; `fill_nodata`
and `smooth` turn those two on and off.

The encoding: `value = (h + 10000) / 0.1`, stored as `R = value / 65536`,
`G = (value mod 65536) / 256`, `B = value mod 256` (each floored); a reader gets
the height back as `h = -10000 + (R * 65536 + G * 256 + B) * 0.1`.

Smoothing runs `gdal_filter.py -gauss`. That script does not come with GDAL;
without it on PATH, `smooth=True` raises `SmoothingUnavailable` before any work
is done.

Known limit: `gdal2tiles.py` resamples each band of the encoded raster on its
own (its default is `average`), so on pixels where a byte of the encoding
carries over between neighbours the decoded height can be off by up to one
G step (25.6 m). It is a small share of the pixels (under 1.5 % on the test
DEM) and is kept as it is in this release, so tiles stay identical to the ones
already published.

## Quantized mesh

```python
from dem2terrain import DockerRunner, LocalRunner, mago_mesh

result = mago_mesh(["a.tif", "b.tif"], "terrain/", runner=DockerRunner(), max_depth=14)
result["layer"]["format"]   # "quantized-mesh-1.0"
```

Each DEM is staged as `<name>.tif`: warped to EPSG:4326 (its nodata kept) when
it is in another CRS, copied when not; `merge_to_vrt=True` adds a `merged.vrt`
over them. The runner runs the tool on the staging directory, and afterwards
`layer.json` must exist and parse (`LayerJsonError` otherwise).

Runners:

- `DockerRunner(image, mounts, *, user=None, path_map=None)`: `docker run --rm`.
  The default image is mago-3d-terrainer 1.14.6 by digest
  (`dem2terrain.DEFAULT_MAGO_IMAGE`). Without `mounts`, the staging and output
  directories are mounted themselves; with `mounts` (host → container),
  paths are translated through them, and `path_map` covers a named volume the
  caller sees at another path.
- `LocalRunner(command)`: the tool on this machine, by default a
  `mago-3d-terrainer` launcher on PATH, or e.g.
  `("java", "-jar", "mago-3d-terrainer-1.14.6-release.jar")`.
- Your own: any object with `run(input_dir, output_dir, options)`.

## Cancelling

`terrain_rgb` and `mago_mesh` take `cancel`, a callable checked between steps;
when it returns true, `Cancelled` is raised.

## Command line

```sh
dem2terrain rgb dem.tif out/ --minzoom 6 --maxzoom 14 [--mbtiles]
dem2terrain mago dem.tif terrain/ [--image IMAGE | --local "java -jar mago.jar"] [--max-depth 14]
```

Both print the result as JSON.

## Tests

```sh
python -m venv --system-site-packages .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest -q
```

The test that runs the real tool is skipped unless Docker and the image
(`docker pull` the digest in `DEFAULT_MAGO_IMAGE`) are there.
