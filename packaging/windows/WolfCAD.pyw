# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Start von WolfCAD aus der Windows-Installation (Gegenstück zu packaging/AppRun beim AppImage).

Aufbau des Installationsordners:  python\\ (eigene Laufzeit)  app\\wolfcad\\ (Programm)  crealityprint\\ (Slicer).
Gestartet wird mit pythonw.exe (ohne Konsolenfenster); dort gibt es keine Standardausgabe, deshalb werden
stdout/stderr auf „nichts“ umgelenkt – Fehler landen trotzdem im Protokoll (siehe wolfcad/__main__.py).
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

os.environ.setdefault("WOLFCAD_CREALITY", str(HERE / "crealityprint"))   # mitgelieferter Slicer
os.environ["PYTHONNOUSERSITE"] = "1"                                     # gilt für Hilfsprozesse
sys.path.insert(0, str(HERE / "app"))

from wolfcad.__main__ import main  # noqa: E402  (erst nach dem Suchpfad)

sys.exit(main())
