# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import dem2terrain
from dem2terrain.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert dem2terrain.__version__ in capsys.readouterr().out


def test_rgb(tmp_path, utm_dem, capsys):
    assert main(["rgb", str(utm_dem), str(tmp_path / "o"), "--minzoom", "11", "--maxzoom", "11", "--workers", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["backend"] == "directory" and list(Path(out["tiles_dir"]).rglob("*.png"))


def test_mago_local(tmp_path, utm_dem, capsys):
    fake = f"{sys.executable} {Path(__file__).with_name('fake_mago.py')}"
    assert main(["mago", str(utm_dem), str(tmp_path / "o"), "--local", fake]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["layer"]["format"] == "quantized-mesh-1.0"
