# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Quantized-mesh terrain (Cesium) from DEMs, through mago-3d-terrainer.

:func:`mago_mesh` stages the DEMs (reprojected to EPSG:4326 when needed,
optionally merged into a VRT), asks a *runner* to run mago-3d-terrainer on the
staging directory, and checks that the run left a readable ``layer.json``.

How the tool is started is the runner's business. Two are shipped:
:class:`LocalRunner` (the tool on this machine) and :class:`DockerRunner` (the
tool's container image). Anything with a ``run(input_dir, output_dir,
options)`` method is a runner, so a caller that starts containers some other
way (a Swarm volume, ``docker exec`` into a running container) brings its own.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from os import PathLike
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from dem2terrain import prepare
from dem2terrain._run import CancelCheck, check_cancel, run
from dem2terrain.errors import LayerJsonError

StrPath = str | PathLike[str]

#: mago-3d-terrainer 1.14.6, by digest, so the tool never changes under a pin.
DEFAULT_MAGO_IMAGE = (
    "gaia3d/mago-3d-terrainer@sha256:295829f934ec68c5a69f8fc251a10606d977ec5875937ed71163dafa3f33bb24"
)


@runtime_checkable
class MagoRunner(Protocol):
    """Runs mago-3d-terrainer on ``input_dir`` into ``output_dir``."""

    def run(
        self, input_dir: Path, output_dir: Path, options: Sequence[str]
    ) -> subprocess.CompletedProcess[str]: ...


class LocalRunner:
    """mago-3d-terrainer installed on this machine.

    ``command`` is how to start it: the default expects a ``mago-3d-terrainer``
    launcher on PATH; ``("java", "-jar", "/opt/mago-3d-terrainer.jar")`` runs
    the jar directly.
    """

    def __init__(self, command: Sequence[str] = ("mago-3d-terrainer",)) -> None:
        if not command:
            raise ValueError("command must not be empty")
        self.command = [str(c) for c in command]

    def run(
        self, input_dir: Path, output_dir: Path, options: Sequence[str]
    ) -> subprocess.CompletedProcess[str]:
        return run([*self.command, "-input", str(input_dir), "-output", str(output_dir), *options])


class DockerRunner:
    """mago-3d-terrainer from its container image, with ``docker run --rm``.

    ``mounts`` maps host directories to container directories (``-v
    host:container``); the input and output directories must lie under one of
    them, and are passed to the tool by their container paths. Without
    ``mounts``, the input and output directories themselves are mounted at
    ``/mago/input`` and ``/mago/output``. A host side that is not an absolute
    path is passed as it is, so a named volume works too; ``path_map`` then
    says where the caller sees that volume (``{"/data": "/workspace"}`` when
    the caller has the volume at ``/data`` and it is mounted at
    ``/workspace``). Paths are translated through ``path_map`` when it is
    given, through the absolute-path ``mounts`` otherwise.

    ``user`` is passed as ``--user`` (``"1000:1000"``), so the files written
    belong to that user rather than to root.
    """

    def __init__(
        self,
        image: str = DEFAULT_MAGO_IMAGE,
        mounts: Mapping[StrPath, str] | None = None,
        *,
        docker: str = "docker",
        user: str | None = None,
        path_map: Mapping[StrPath, str] | None = None,
        extra_args: Sequence[str] = (),
    ) -> None:
        self.image = image
        self.mounts = {str(k): str(v) for k, v in (mounts or {}).items()}
        self.docker = docker
        self.user = user
        self.path_map = {str(k): str(v) for k, v in (path_map or {}).items()}
        self.extra_args = [str(a) for a in extra_args]

    def _container_path(self, path: Path) -> str:
        best: tuple[int, str] | None = None
        resolved = Path(path).resolve()
        table = self.path_map or self.mounts
        for host, container in table.items():
            if not os.path.isabs(host):
                continue
            try:
                rel = resolved.relative_to(Path(host).resolve())
            except ValueError:
                continue
            depth = len(Path(host).resolve().parts)
            if best is None or depth > best[0]:
                parts = rel.parts
                best = (depth, str(PurePosixPath(container, *parts)))
        if best is None:
            raise ValueError(f"{path} is not under any of {sorted(table)}")
        return best[1]

    def command(self, input_dir: Path, output_dir: Path, options: Sequence[str]) -> list[str]:
        """The ``docker run`` command line, without running it."""

        cmd = [self.docker, "run", "--rm"]
        if self.user:
            cmd += ["--user", self.user]
        if self.mounts:
            for host, container in self.mounts.items():
                cmd += ["-v", f"{host}:{container}"]
            in_arg = self._container_path(input_dir)
            out_arg = self._container_path(output_dir)
        else:
            cmd += [
                "-v",
                f"{Path(input_dir).resolve()}:/mago/input",
                "-v",
                f"{Path(output_dir).resolve()}:/mago/output",
            ]
            in_arg, out_arg = "/mago/input", "/mago/output"
        cmd += [*self.extra_args, self.image, "-input", in_arg, "-output", out_arg, *options]
        return cmd

    def run(
        self, input_dir: Path, output_dir: Path, options: Sequence[str]
    ) -> subprocess.CompletedProcess[str]:
        return run(self.command(input_dir, output_dir, options))


def check_layer_json(out_dir: StrPath) -> dict[str, object]:
    """Read ``out_dir/layer.json`` and check it describes quantized-mesh tiles.

    Returns the parsed document; raises :class:`LayerJsonError` when it is
    missing, not JSON, or has no ``tiles`` template.
    """

    path = Path(out_dir) / "layer.json"
    if not path.is_file():
        raise LayerJsonError(f"no layer.json in {out_dir}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LayerJsonError(f"{path} does not parse: {exc}") from exc
    if not isinstance(doc, dict):
        raise LayerJsonError(f"{path} is not a JSON object")
    tiles = doc.get("tiles")
    if not isinstance(tiles, list) or not tiles or not all(isinstance(t, str) for t in tiles):
        raise LayerJsonError(f"{path} has no tiles template")
    return doc


def _stage_name(dem: Path, names: Sequence[str] | None, index: int) -> str:
    if names is not None:
        return names[index]
    return dem.stem


def mago_mesh(
    dem: StrPath | Sequence[StrPath],
    out_dir: StrPath,
    *,
    runner: MagoRunner,
    max_depth: int = 14,
    reproject_to_wgs84: bool = True,
    merge_to_vrt: bool = False,
    names: Sequence[str] | None = None,
    work_dir: StrPath | None = None,
    keep_input: bool = False,
    overwrite: bool = False,
    extra_options: Sequence[str] = (),
    cancel: CancelCheck | None = None,
) -> dict[str, object]:
    """Quantized-mesh terrain of one or more single-band DEMs into ``out_dir``.

    Each DEM is staged in ``work_dir`` as ``<name>.tif`` (``names`` or the
    file's stem): warped to EPSG:4326 (bilinear, its nodata kept) when
    ``reproject_to_wgs84`` and it is in another CRS, copied otherwise. With
    ``merge_to_vrt`` a ``merged.vrt`` over them is added. The runner then runs
    the tool with ``-maxDepth max_depth`` and ``extra_options`` on the staging
    directory, and ``layer.json`` is checked. ``work_dir`` defaults to a
    temporary directory, removed afterwards unless ``keep_input``.

    Returns the output directory, the parsed ``layer.json``, the staged inputs
    and the VRT path (or ``None``).
    """

    dems = [Path(dem)] if isinstance(dem, (str, PathLike)) else [Path(d) for d in dem]
    if not dems:
        raise ValueError("at least one DEM is needed")
    if names is not None and len(names) != len(dems):
        raise ValueError("names must have one entry per DEM")
    if max_depth < 1 or max_depth > 20:
        raise ValueError("max_depth must be between 1 and 20")
    if not isinstance(runner, MagoRunner):
        raise TypeError("runner must have a run(input_dir, output_dir, options) method")

    stage_names = [_stage_name(d, names, i) for i, d in enumerate(dems)]
    if len(set(stage_names)) != len(stage_names):
        raise ValueError(f"two DEMs would be staged under the same name: {stage_names}")
    infos = []
    for d in dems:
        if not d.exists():
            raise FileNotFoundError(d)
        info = prepare.raster_info(d)
        if info["bands"] != 1:
            raise ValueError(f"{d} must be single-band")
        infos.append(info)

    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        if not overwrite:
            raise FileExistsError(f"{out_dir} is not empty; pass overwrite=True to replace it")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    own_work_dir = work_dir is None
    stage = Path(tempfile.mkdtemp(prefix="dem2terrain_mago_")) if own_work_dir else Path(work_dir)
    if not own_work_dir:
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True, exist_ok=True)

    try:
        staged: list[Path] = []
        for d, info, name in zip(dems, infos, stage_names):
            check_cancel(cancel)
            dst = stage / f"{name}.tif"
            if reproject_to_wgs84 and info["epsg"] not in {"", "4326"}:
                prepare.reproject(
                    d,
                    dst,
                    "EPSG:4326",
                    output_format="GTiff",
                    overwrite=True,
                    dst_nodata=info["nodata"],
                )
            else:
                shutil.copy2(d, dst)
            staged.append(dst)

        vrt_path = prepare.build_vrt(staged, stage / "merged.vrt") if merge_to_vrt else None

        check_cancel(cancel)
        runner.run(stage, out_dir, ["-maxDepth", str(max_depth), *[str(o) for o in extra_options]])
        check_cancel(cancel)
        layer = check_layer_json(out_dir)
    finally:
        if not keep_input:
            shutil.rmtree(stage, ignore_errors=True)

    return {
        "output_dir": str(out_dir),
        "layer_json": str(out_dir / "layer.json"),
        "layer": layer,
        "inputs": [str(p) for p in staged],
        "vrt_path": str(vrt_path) if vrt_path is not None else None,
        "input_dir": str(stage),
        "input_kept": keep_input,
    }
