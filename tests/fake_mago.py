# dem2terrain — DEM GeoTIFF to terrain tiles
# Copyright (C) 2026 Norwegian Meteorological Institute (MET Norway)
# Author: Massimo Di Stefano <massimods@met.no>
# SPDX-License-Identifier: GPL-3.0-or-later

"""A stand-in for mago-3d-terrainer: records what it was given, writes a layer.json.

FAKE_MAGO_LAYER=missing writes none; FAKE_MAGO_LAYER=broken writes one that
does not parse; FAKE_MAGO_RC=<n> exits with n.
"""

import json
import os
import sys
from pathlib import Path

argv = sys.argv[1:]
opts = dict(zip(argv[::2], argv[1::2]))
out = Path(opts["-output"])
inp = Path(opts["-input"])
(out / "args.json").write_text(json.dumps({"argv": argv, "inputs": sorted(p.name for p in inp.iterdir())}))
rc = int(os.environ.get("FAKE_MAGO_RC", "0"))
mode = os.environ.get("FAKE_MAGO_LAYER", "ok")
if mode == "ok":
    (out / "layer.json").write_text(
        json.dumps({"tilejson": "2.1.0", "format": "quantized-mesh-1.0", "tiles": ["{z}/{x}/{y}.terrain"]})
    )
elif mode == "broken":
    (out / "layer.json").write_text("{not json")
sys.exit(rc)
