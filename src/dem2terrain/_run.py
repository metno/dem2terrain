# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Running external tools and checking for cancellation."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from dem2terrain.errors import Cancelled, CommandError

CancelCheck = Callable[[], bool]


def run(args: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``args``; raise :class:`CommandError` with the output's tail on a non-zero exit."""

    args = [str(a) for a in args]
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise CommandError(args, proc.returncode, proc.stdout or "", proc.stderr or "")
    return proc


def check_cancel(cancel: CancelCheck | None) -> None:
    """Raise :class:`Cancelled` when the caller's check says so."""

    if cancel is not None and cancel():
        raise Cancelled("cancelled")
