# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Erzeugt OpenCascade-Flächen aus den Flächen-Datensätzen einer ASM-Datei.

Feldpositionen (nach den drei gemeinsamen Kopf-Feldern):
  plane-surface:  Ursprung, Normale, u-Richtung
  cone-surface:   Mittelpunkt, Achse, Hauptachsenvektor, Achsverhältnis, 2 Flags,
                  Sinus und Kosinus des halben Öffnungswinkels (Sinus 0 = Zylinder)
  sphere-surface: Mittelpunkt, Radius, u-Richtung, Polrichtung
  torus-surface:  Mittelpunkt, Achse, großer Radius, kleiner Radius, u-Richtung
  spline-surface: Unterblock mit NURBS-Darstellung (siehe nurbs.py)
"""

from __future__ import annotations

import math

from OCP.Geom import (
    Geom_ConicalSurface, Geom_CylindricalSurface, Geom_Plane, Geom_SphericalSurface,
    Geom_Surface, Geom_SurfaceOfLinearExtrusion, Geom_ToroidalSurface,
)
from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt

from . import nurbs
from .sab import Record, Sub

# Vorzeichen, mit dem der Kegelradius entlang der Achse wächst (an echten Daten geprüft)
CONE_SIGN = 1.0


def _length(vector) -> float:
    return math.sqrt(sum(c * c for c in vector))


def build(record: Record, blocks: nurbs.BlockTable) -> tuple[Geom_Surface | None, bool]:
    """Gibt die Fläche zum Datensatz zurück und ob ihre Normale nach innen zeigt.

    "Hohle" Grundflächen (negativer Kosinus bei Zylinder/Kegel, negativer Radius
    bei Kugel und Torus) haben in der Datei eine zur Achse bzw. zum Mittelpunkt
    zeigende Normale. OpenCascade kennt nur die nach außen zeigende Variante,
    deshalb wird das als Kennzeichen zurückgegeben. Ohne passende Fläche: None.
    """
    surface, inward = _build(record, blocks)
    return surface, inward


def _build(record: Record, blocks: nurbs.BlockTable) -> tuple[Geom_Surface | None, bool]:
    f = record.fields
    kind = record.name

    if kind == "plane-surface":
        return Geom_Plane(gp_Ax3(gp_Pnt(*f[3]), gp_Dir(*f[4]), gp_Dir(*f[5]))), False

    if kind == "cone-surface":
        center, axis, major, ratio, sine, cosine = f[3], f[4], f[5], f[6], f[9], f[10]
        if abs(ratio - 1.0) > 1e-9:
            return None, False  # elliptische Zylinder/Kegel kennt OpenCascade nicht als Grundform
        frame = gp_Ax3(gp_Pnt(*center), gp_Dir(*axis), gp_Dir(*major))
        radius = _length(major)
        if abs(sine) < 1e-12:
            return Geom_CylindricalSurface(frame, radius), cosine < 0
        return Geom_ConicalSurface(frame, math.atan(CONE_SIGN * sine / cosine), radius), cosine < 0

    if kind == "sphere-surface":
        center, radius, u_dir, pole = f[3], f[4], f[5], f[6]
        return Geom_SphericalSurface(gp_Ax3(gp_Pnt(*center), gp_Dir(*pole), gp_Dir(*u_dir)), abs(radius)), radius < 0

    if kind == "torus-surface":
        center, axis, major, minor, u_dir = f[3], f[4], f[5], f[6], f[7]
        return Geom_ToroidalSurface(gp_Ax3(gp_Pnt(*center), gp_Dir(*axis), gp_Dir(*u_dir)),
                                    abs(major), abs(minor)), minor < 0

    if kind == "spline-surface":
        block = blocks.top_block(record)
        if block is None:
            return None, False
        # Feld 3 ist das Richtungsflag der Fläche: False kehrt die Normale um
        return nurbs.surface_at(*block) or _extrusion(*block, blocks), f[3] is False

    return None, False


def _extrusion(fields: list, start: int, blocks: nurbs.BlockTable) -> Geom_Surface | None:
    """Extrusionsfläche ('cyl_spl_sur') aus Profilkurve und Richtung.

    Manche dieser Blöcke enthalten keine NURBS-Näherung der Fläche. Dann wird
    sie aus der eingebetteten Profilkurve und dem ersten Vektor des Blocks
    (der Extrusionsrichtung) konstruiert.
    """
    if fields[start + 1] != "cyl_spl_sur" or fields[start + 3] != "intcurve":
        return None
    inner = next((i for i in range(start + 1, len(fields))
                  if isinstance(fields[i], Sub) and fields[i].open), None)
    if inner is None:
        return None
    profile = nurbs.curve_at(*blocks.resolve(fields, inner))
    direction = next((fields[p] for p in nurbs._own_level(fields, start)
                      if isinstance(fields[p], tuple)), None)
    if profile is None or direction is None or _length(direction) < 1e-12:
        return None
    return Geom_SurfaceOfLinearExtrusion(profile, gp_Dir(*direction))
