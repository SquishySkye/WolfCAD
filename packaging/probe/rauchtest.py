# SPDX-License-Identifier: GPL-3.0-or-later
"""Rauchtest eines Pakets (AppImage oder Windows): mit der eingepackten Python-Laufzeit Programm laden, Fenster
öffnen, Körper bauen, Festigkeit rechnen (gmsh, scipy), Slicer finden und seine Bibliotheken prüfen; das nur lokale
Auslesewerkzeug für .f3d-Klassen darf im Paket nicht enthalten sein."""
import os, sys
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
print("Python:", sys.executable, sys.version.split()[0])
from PySide6.QtWidgets import QApplication
app = QApplication([])
from wolfcad.core import physics, solids
from wolfcad.gui import main_window as mw
from wolfcad.printing import slicer, profiles
w = mw.MainWindow(); w.show(); app.processEvents()
w._create(solids.Box(center_x=50, length=100, width=10, height=10, operation="Neuer Körper")); w._finish_live(True)
shape = next(iter(w.timeline.state.bodies.values()))
r = physics.static_stress(shape, physics.MATERIALS["Aluminium"], [[0.0, 0.0, 5.0]],
                          [physics.Load(physics.FORCE, [[100.0, 0.0, 5.0]], 100.0, (0, 0, -1))], size=4.0)
print("FEM ok, Spannung", round(r.max_stress, 1), "MPa")
print("Slicer:", profiles.install_dir(), slicer.executable().exists(), "fehlende Bibliotheken:", slicer.missing_libraries())
assert slicer.executable().exists() and not slicer.missing_libraries()
assert not (Path(slicer.__file__).parent.parent / "f3d" / "schema_extract.py").exists(), "Auslesewerkzeug im Paket"
print("geladene Fremdpakete vtk/matplotlib:", [m for m in sys.modules if m.split('.')[0] in ("vtk", "vtkmodules", "matplotlib")])
print("RAUCHTEST OK")
