# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Startwerte der Werkzeuge (Runde 88, Nutzervorgabe): jedes Werkzeug startet bei 0 – erst Ziehen am Pfeil oder
Eintippen bewirkt etwas. Wo 0 keinen Sinn ergibt, gilt der neutrale Wert (Skalierfaktor 1, Anzahl 1).

Solange ein Schritt neutral ist, ändert er das Modell nicht und meldet keinen Fehler (`is_neutral`, von der Timeline
übersprungen); wird er neutral bestätigt, nimmt das Hauptfenster ihn nicht in die Timeline (wie die Extrusion ohne
Länge seit Runde 60). Ausnahmen ohne „neutral“: Konstruktionsebene und Fläche teilen – dort ist 0 eine gültige Lage.
"""
from __future__ import annotations


def _table() -> dict:
    """Werkzeugart (KIND) -> {Feld: Startwert}; erst beim Aufruf gebaut (vermeidet Ringimporte)."""
    from . import extras as ex
    from . import features as ft
    from . import meshes as ms
    from . import more_tools as mt
    from . import sheetmetal as sm
    from . import sketch as sk
    from . import solids
    from . import surfaces as sf
    by_class = {
        ft.Extrude: {"distance": 0.0, "distance2": 0.0, "taper": 0.0},
        solids.Revolve: {"angle": 0.0},
        ft.Fillet: {"radius": 0.0},
        ft.Chamfer: {"distance": 0.0, "distance2": 0.0},
        solids.Shell: {"thickness": 0.0},
        solids.Draft: {"angle": 0.0},
        solids.PressPull: {"distance": 0.0},
        solids.MoveBody: {"move_x": 0.0, "move_y": 0.0, "move_z": 0.0, "angle": 0.0, "angle_x": 0.0,
                          "angle_y": 0.0, "angle_z": 0.0, "scale": 1.0},
        solids.MoveFace: {"move_x": 0.0, "move_y": 0.0, "move_z": 0.0, "rotate_angle": 0.0},
        solids.SplitBody: {"offset": 0.0},
        solids.Hole: {"diameter": 0.0},
        solids.Rib: {"thickness": 0.0},
        solids.RectangularPattern: {"count": 1.0},
        solids.CircularPattern: {"count": 1.0},
        solids.ScaleBody: {"factor": 1.0, "factor_y": 0.0, "factor_z": 0.0},     # 0 = wie der Faktor
        sk.ConstructionPlane: {"offset": 0.0, "tilt": 0.0},
        ex.SplitFace: {"offset": 0.0},
        ex.Torus: {"diameter": 0.0},
        ex.Coil: {"turns": 0.0},
        ex.Pipe: {"diameter": 0.0},
        ex.Hem: {"length": 0.0},
        mt.Emboss: {"depth": 0.0},
        mt.Web: {"depth": 0.0},
        mt.ContourFlange: {"width": 0.0},
        sm.EdgeFlange: {"length": 0.0},
        sm.SketchBend: {"angle": 0.0},
        sf.SurfaceExtrude: {"distance": 0.0},
        sf.SurfaceRevolve: {"angle": 0.0},
        sf.OffsetSurface: {"distance": 0.0},
        sf.Thicken: {"thickness": 0.0},
        sf.ExtendSurface: {"distance": 0.0},
        ms.MeshSmooth: {"iterations": 0},
    }
    return {cls.KIND: values for cls, values in by_class.items()}


_TABLE: dict | None = None
# Hier ist der Startwert eine gültige Lage (Ebene durch den Ursprung bzw. durch die Fläche) – kein „neutral“
_ALWAYS_ACTIVE = {"Ebene", "Fläche teilen"}


def table() -> dict:
    global _TABLE
    if _TABLE is None:
        _TABLE = _table()
    return _TABLE


def apply_start(feature) -> None:
    """Setzt die Werte eines gerade über die Oberfläche gestarteten Werkzeugs auf ihren Start (0 bzw. neutral)."""
    for name, value in table().get(feature.KIND, {}).items():
        if hasattr(feature, name):
            setattr(feature, name, type(getattr(feature, name))(value))


def is_neutral(feature) -> bool:
    """Bewirkt der Schritt mit seinen jetzigen Werten noch nichts (alle Startwerte unverändert)?"""
    values = table().get(feature.KIND)
    if not values or feature.KIND in _ALWAYS_ACTIVE:
        return False
    if feature.KIND == "Teilen" and (not getattr(feature, "frame", None) or getattr(feature, "tool_points", None)
                                     or getattr(feature, "tool", "")):
        return False            # an einer Hauptebene oder mit Trennfläche teilt schon der Startwert
    if feature.KIND == "Extrusion" and getattr(feature, "extent", None) not in _distance_extents():
        return False            # „bis Fläche“/„durch alles“ brauchen keine Länge
    if feature.KIND == "Verschieben" and getattr(feature, "copy", False):
        return False            # Kopie an Ort und Stelle ist schon eine Wirkung
    return all(abs(float(getattr(feature, name, value)) - float(value)) < 1e-12 for name, value in values.items())


def _distance_extents() -> tuple:
    from . import features as ft
    return tuple(extent for number, extent in enumerate(ft.EXTENTS) if number not in (3, 4))
