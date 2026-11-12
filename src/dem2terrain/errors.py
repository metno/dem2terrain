# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

"""The exceptions dem2terrain raises besides ValueError and FileExistsError."""

from __future__ import annotations


class CommandError(RuntimeError):
    """An external tool (a GDAL utility, mago-3d-terrainer, docker) exited non-zero."""

    def __init__(self, args: list[str], returncode: int, stdout: str, stderr: str) -> None:
        self.args_run = list(args)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        tail = (stderr or stdout or "").strip()[-2000:]
        super().__init__(f"Command failed (rc={returncode}): {' '.join(args)}\n{tail}")


class Cancelled(RuntimeError):
    """The caller's ``cancel`` callable returned true between two steps."""


class SmoothingUnavailable(RuntimeError):
    """Smoothing was asked for, but the filter tool it runs is not installed."""


class LayerJsonError(RuntimeError):
    """A quantized-mesh run finished without a readable ``layer.json``."""
