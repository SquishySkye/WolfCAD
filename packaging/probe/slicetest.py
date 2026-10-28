# SPDX-License-Identifier: GPL-3.0-or-later
"""Echtes Slicen mit dem mitgelieferten Creality Print (Paket-Laufzeit, leere Umgebung): Würfel → G-Code."""
import subprocess, sys, tempfile
from pathlib import Path
from wolfcad.core import features as ft, meshes, solids
from wolfcad.printing import gcode, plate, profiles, slicer
state = ft.BuildState()
solids.Box(length=20, width=20, height=10).apply(state)
bed = plate.Plate(350, 350, 350)
item = bed.add("Würfel", meshes.tessellate(next(iter(state.bodies.values()))))
library = profiles.Library()
printer = "Creality K2 Plus 0.4 nozzle"
folder = Path(tempfile.mkdtemp())
job = slicer.prepare(folder, [("Würfel", item.placed(), 1)], library.profile("machine", printer),
                     library.profile("process", library.default_process(printer)),
                     [library.profile("filament", library.default_filament(printer))])
run = subprocess.run(job.arguments, env=slicer.cli_environment(), capture_output=True, timeout=300)
print("Slicer:", job.arguments[0], "Rückgabe", run.returncode)
text = job.gcode.read_text(errors="replace") if job.gcode.exists() else ""
print("G-Code:", len(text.splitlines()), "Zeilen; Schichten:", text.count(";LAYER_CHANGE"))
assert run.returncode == 0 and text.count(";LAYER_CHANGE") >= 40
print("SLICETEST OK")
