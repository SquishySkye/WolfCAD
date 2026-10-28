# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""NURBS-Kurven und -Flächen aus den Unterblöcken der ASM-Datensätze.

Freiformgeometrie steht in geschachtelten Unterblöcken `{ 'typname' version … }`.
Jeder Block erhält beim Öffnen eine fortlaufende Nummer (ab 0, über die ganze
Datei gezählt); `{ 'ref' N }` verweist auf den Block mit der Nummer N.

Jeder Block enthält direkt auf seiner eigenen Ebene (nicht in tieferen Blöcken)
eine NURBS-Darstellung seiner Geometrie:
  Kurve:  'nubs'|'nurbs', Grad, Schließung, Knotenzahl, (Knoten, Vielfachheit)…, Pole
  Fläche: 'nubs'|'nurbs', GradU, GradV, SchließungU, SchließungV, SingulärU, SingulärV,
          KnotenzahlU, KnotenzahlV, KnotenU…, KnotenV…, Pole (V läuft außen, U innen)
Bei 'nurbs' hat jeder Pol zusätzlich ein Gewicht.
"""

from __future__ import annotations

from OCP.collections import Array1_double, Array1_gp_Pnt, Array1_int, Array2_double, Array2_gp_Pnt
from OCP.Geom import Geom_BSplineCurve, Geom_BSplineSurface
from OCP.gp import gp_Pnt

from .sab import Record, SabFile, Sub

_SPLINE_KEYS = ("nubs", "nurbs")


class BlockTable:
    """Verzeichnis aller Unterblöcke einer Datei für das Auflösen von 'ref N'."""

    def __init__(self, sab: SabFile):
        # Nummer -> (Felder des Datensatzes, Index des öffnenden Sub-Markers)
        self._blocks: list[tuple[list, int]] = []
        for record in sab.records:
            fields = record.fields
            for position, value in enumerate(fields):
                if isinstance(value, Sub) and value.open and fields[position + 1] != "ref":
                    self._blocks.append((fields, position))

    def resolve(self, fields: list, position: int) -> tuple[list, int]:
        """Folgt `{ 'ref' N }` bis zum eigentlichen Block (Felder, Startindex)."""
        while fields[position + 1] == "ref":
            fields, position = self._blocks[fields[position + 2]]
        return fields, position

    def top_block(self, record: Record) -> tuple[list, int] | None:
        """Der äußerste Unterblock eines Datensatzes, Verweise bereits aufgelöst."""
        for position, value in enumerate(record.fields):
            if isinstance(value, Sub) and value.open:
                return self.resolve(record.fields, position)
        return None


def _own_level(fields: list, start: int):
    """Liefert die Positionen aller Werte, die direkt im Block ab `start` liegen."""
    depth = 0
    for position in range(start, len(fields)):
        value = fields[position]
        if isinstance(value, Sub):
            depth += 1 if value.open else -1
            if depth == 0:
                return
        elif depth == 1:
            yield position


def _spline_keys(fields: list, start: int) -> list[int]:
    """Positionen aller NURBS-Schlüsselwörter direkt auf der Ebene des Blocks."""
    return [p for p in _own_level(fields, start) if fields[p] in _SPLINE_KEYS]


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _knots(fields: list, position: int, count: int, degree: int):
    """Liest `count` Paare (Knoten, Vielfachheit) und passt die Enden an.

    ASM speichert an den Enden die Vielfachheit 'Grad'; OpenCascade erwartet
    für eingespannte Splines 'Grad + 1'. Rückgabe: Knoten, Vielfachheiten,
    Polzahl in dieser Richtung, Position hinter den Knoten.
    """
    values = fields[position:position + 2 * count:2]
    mults = list(fields[position + 1:position + 2 * count:2])
    poles = sum(mults) - degree + 1
    mults[0] += 1
    mults[-1] += 1
    knot_array, mult_array = Array1_double(1, count), Array1_int(1, count)
    for i in range(count):
        knot_array.SetValue(i + 1, float(values[i]))
        mult_array.SetValue(i + 1, mults[i])
    return knot_array, mult_array, poles, position + 2 * count


def _floats(fields: list, position: int, count: int) -> list[float] | None:
    raw = fields[position:position + count]
    if len(raw) < count or not all(isinstance(v, float) for v in raw):
        return None
    return raw


def curve_at(fields: list, start: int) -> Geom_BSplineCurve | None:
    """NURBS-Kurve des Blocks, der bei `fields[start]` beginnt (oder None)."""
    for key in _spline_keys(fields, start):
        curve = _curve(fields, key)
        if curve is not None:
            return curve
    return None


def _curve(fields: list, key: int) -> Geom_BSplineCurve | None:
    rational = fields[key] == "nurbs"
    degree, count = fields[key + 1], fields[key + 3]
    # Bei einer Kurve folgt auf Grad, Schließung und Knotenzahl direkt der erste Knotenwert
    if not (_is_int(degree) and _is_int(count) and isinstance(fields[key + 4], float)):
        return None
    knots, mults, pole_count, position = _knots(fields, key + 4, count, degree)

    width = 4 if rational else 3
    raw = _floats(fields, position, width * pole_count)
    if raw is None:
        return None
    poles, weights = Array1_gp_Pnt(1, pole_count), Array1_double(1, pole_count)
    for i in range(pole_count):
        chunk = raw[width * i:width * (i + 1)]
        poles.SetValue(i + 1, gp_Pnt(*chunk[:3]))
        weights.SetValue(i + 1, chunk[3] if rational else 1.0)
    return Geom_BSplineCurve(poles, weights, knots, mults, degree)


def surface_at(fields: list, start: int) -> Geom_BSplineSurface | None:
    """NURBS-Fläche des Blocks, der bei `fields[start]` beginnt (oder None)."""
    for key in _spline_keys(fields, start):
        surface = _surface(fields, key)
        if surface is not None:
            return surface
    return None


def _surface(fields: list, key: int) -> Geom_BSplineSurface | None:
    rational = fields[key] == "nurbs"
    degree_u, degree_v = fields[key + 1], fields[key + 2]
    # Rationale Flächen nennen nach den Graden die rationale Richtung ('u', 'v', 'both')
    head = key + 4 if rational and isinstance(fields[key + 3], str) else key + 3
    count_u, count_v = fields[head + 4], fields[head + 5]
    if not all(_is_int(v) for v in (degree_u, degree_v, count_u, count_v)):
        return None  # an dieser Stelle steht eine Kurve, keine Fläche
    knots_u, mults_u, poles_u, position = _knots(fields, head + 6, count_u, degree_u)
    knots_v, mults_v, poles_v, position = _knots(fields, position, count_v, degree_v)

    width = 4 if rational else 3
    raw = _floats(fields, position, width * poles_u * poles_v)
    if raw is None:
        return None
    poles = Array2_gp_Pnt(1, poles_u, 1, poles_v)
    weights = Array2_double(1, poles_u, 1, poles_v)
    for v in range(poles_v):
        for u in range(poles_u):
            offset = width * (v * poles_u + u)
            poles.SetValue(u + 1, v + 1, gp_Pnt(*raw[offset:offset + 3]))
            weights.SetValue(u + 1, v + 1, raw[offset + 3] if rational else 1.0)
    return Geom_BSplineSurface(poles, weights, knots_u, knots_v, mults_u, mults_v,
                               degree_u, degree_v)
