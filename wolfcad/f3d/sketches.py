# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Liest die Skizzen eines Fusion-Entwurfs aus den Segmentdaten.

Beteiligte Klassen (Namen laut Schema) und ihr an echten Daten ermittelter Aufbau:
  CoordinateSystem: 7 Byte Kopf, dann 3×4-Matrix zeilenweise (Drehung und Ursprung
                    der Skizzenebene; Welt = Matrix · Skizzenpunkt), Längen in cm
  MSketch:          enthält einen Verweis auf sein CoordinateSystem. Objekte werden je
                    Klassenebene abschnittsweise gespeichert; im Abschnitt der Basisklasse,
                    also kurz vor dem MSketch-Abschnitt, steht der Verweis auf das
                    zugehörige Timeline-Feature (DcSketch).
  Line3D:           Attributtabelle, Startpunkt, Vektor zum Endpunkt (je 3 Doubles), …
  CircArc3D:        Attributtabelle, Mittelpunkt, Normale, Bezugsrichtung (je 3 Doubles),
                    Radius, Startwinkel, Endwinkel
Kurven verweisen kurz hinter ihrer Geometrie auf das MSketch, zu dem sie gehören.

Attributtabelle: 6 Byte Kopf, Anzahl, je Eintrag Schlüssel und Typname (ASCII mit
4-Byte-Länge) und ein 8-Byte-Wert.

  ConstrainedSpline3D: Die Attributtabelle steht im Abschnitt der Klasse selbst, die
                    Kurvendaten im Abschnitt der Basisklasse Spline3D ab Byte 67:
                    Grad (4 Byte), Toleranz (Double), dann drei Felder im Format
                    "Anzahl, Anzahl, 8, Daten": Knoten, Gewichte, Kontrollpunkte
                    (bei den Kontrollpunkten drei Doubles je Element).

Ellipsenbögen werden noch nicht gelesen.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from .segment import Directory

_MATRIX_OFFSET = 7
# Suchfenster (Byte) für Verweise hinter dem Objektanfang bzw. hinter der Geometrie
_LINK_WINDOW = 400
# Länge des Basisklassen-Abschnitts vor einem MSketch, in dem das Feature genannt wird
_BASE_WINDOW = 60
_LINE_SIZE, _ARC_SIZE = 48, 96
_TAG_KEY = "crv_primary_id"
_SPLINE_OFFSET = 67      # Beginn der Kurvendaten im Spline3D-Abschnitt
_SPLINE_LIMIT = 100_000  # größte plausible Zahl von Knoten oder Kontrollpunkten

Point = tuple[float, float, float]


@dataclass(frozen=True)
class SketchLine:
    start: Point
    end: Point
    tag: int = 0        # Kennnummer der Kurve in der Skizze (crv_primary_id)


@dataclass(frozen=True)
class SketchArc:
    center: Point
    normal: Point
    reference: Point    # Richtung, von der aus die Winkel zählen
    radius: float
    start_angle: float
    end_angle: float
    tag: int = 0        # Kennnummer der Kurve in der Skizze (crv_primary_id)


@dataclass(frozen=True)
class SketchSpline:
    degree: int
    knots: tuple[float, ...]        # vollständiger Knotenvektor (mit Wiederholungen)
    weights: tuple[float, ...]
    poles: tuple[Point, ...]
    tag: int = 0        # Kennnummer der Kurve in der Skizze (crv_primary_id)


@dataclass
class FusionSketch:
    feature_id: int                 # ID des DcSketch-Features
    matrix: tuple[float, ...]       # 12 Werte: 3 Zeilen zu je (Drehung x, y, z, Ursprung)
    lines: list[SketchLine] = field(default_factory=list)
    arcs: list[SketchArc] = field(default_factory=list)
    splines: list[SketchSpline] = field(default_factory=list)


def _ids_of(directory: Directory, schema: dict[str, dict], name: str) -> list[int]:
    return [object_id for entry in directory.classes
            if schema.get(entry.guid, {}).get("name") == name for object_id in entry.ids]


def _attributes(bulk: bytes, position: int) -> tuple[dict[str, int], int]:
    """Liest die Attributtabelle an `position`: (Schlüssel -> Wert, Position dahinter)."""
    (count,) = struct.unpack_from("<I", bulk, position + 6)
    if count > 32:
        raise ValueError("Attributtabelle unplausibel")
    cursor = position + 10
    values: dict[str, int] = {}
    for _ in range(count):
        texts = []
        for _text in range(2):      # Schlüssel und Typname
            (length,) = struct.unpack_from("<I", bulk, cursor)
            if length > 64:
                raise ValueError("Attributname unplausibel")
            texts.append(bulk[cursor + 4:cursor + 4 + length].decode("ascii", errors="replace"))
            cursor += 4 + length
        (values[texts[0]],) = struct.unpack_from("<Q", bulk, cursor)
        cursor += 8
    return values, cursor


def _references(bulk: bytes, start: int, end: int, targets: set[int]):
    """Alle Verweise (Byte 1, 8-Byte-ID, 2 Nullbytes) auf Zielobjekte im Bereich."""
    # Ein Verweis ist 11 Byte lang; der letzte mögliche beginnt 11 Byte vor dem Datenende
    for position in range(max(start, 0), min(end, len(bulk) - 10)):
        if bulk[position] == 1 and bulk[position + 9:position + 11] == b"\x00\x00":
            (target,) = struct.unpack_from("<Q", bulk, position + 1)
            if target in targets:
                yield target


def _first_reference(bulk: bytes, start: int, targets: set[int]) -> int | None:
    """Erster Verweis auf eines der Zielobjekte im Suchfenster hinter `start`."""
    return next(_references(bulk, start, start + _LINK_WINDOW, targets), None)


def _plausible(values) -> bool:
    return all(math.isfinite(value) and abs(value) < 1e6 for value in values)


def read_sketches(bulk: bytes, directory: Directory, sections: dict[int, list[int]],
                  schema: dict[str, dict]) -> dict[int, FusionSketch]:
    """Alle Skizzen des Entwurfs: ID des DcSketch-Features -> Skizze (Längen in cm).

    `sections` ist der Abschnittsindex (segment.index_sections). Da die Reihenfolge
    der Abschnitte vom Stand der Datei abhängt, wird jeweils in allen Abschnitten
    eines Objekts gesucht.
    """
    def starts(object_id: int | None) -> list[int]:
        return sections.get(object_id, []) if object_id is not None else []

    def linked(object_id: int | None, targets: set[int]) -> int | None:
        """Erster Verweis auf eines der Ziele hinter einem Abschnittsanfang des Objekts."""
        for start in starts(object_id):
            target = _first_reference(bulk, start, targets)
            if target is not None:
                return target
        return None

    def matrix_of(system_id: int | None) -> tuple[float, ...] | None:
        """Ebenenmatrix: der Abschnitt, in dem bei Byte 7 drei Einheitsvektoren stehen."""
        for start in starts(system_id):
            try:
                matrix = struct.unpack_from("<12d", bulk, start + _MATRIX_OFFSET)
            except struct.error:
                continue
            rows_are_unit = all(abs(sum(matrix[row * 4 + column] ** 2 for column in range(3)) - 1) < 1e-6
                                for row in range(3))
            if _plausible(matrix) and rows_are_unit:
                return matrix
        return None

    def geometry(object_id: int) -> tuple[dict[str, int], int] | None:
        """Attributtabelle der Kurve und Position der Geometriedaten dahinter."""
        for start in starts(object_id):
            try:
                attributes, position = _attributes(bulk, start)
            except (ValueError, struct.error, IndexError):
                continue
            if _TAG_KEY in attributes:
                return attributes, position
        return None

    systems = set(_ids_of(directory, schema, "CoordinateSystem"))
    model_sketches = set(_ids_of(directory, schema, "MSketch"))
    features = set(_ids_of(directory, schema, "DcSketch"))

    # Timeline-Feature -> Modellskizze: das Feature verweist auf seine Skizze. In manchen
    # Dateien steht der Verweis stattdessen kurz vor einem Abschnitt der Modellskizze.
    feature_of: dict[int, int] = {}
    for feature_id in features:
        model_id = linked(feature_id, model_sketches)
        if model_id is not None:
            feature_of.setdefault(model_id, feature_id)
    for model_id in model_sketches - set(feature_of):
        for start in starts(model_id):
            before = list(_references(bulk, start - _BASE_WINDOW, start, features))
            if before:
                feature_of[model_id] = before[-1]
                break

    by_model: dict[int, FusionSketch] = {}
    for model_id, feature_id in feature_of.items():
        matrix = matrix_of(linked(model_id, systems))
        if matrix is not None:
            by_model[model_id] = FusionSketch(feature_id, matrix)

    def owner(geometry_end: int) -> FusionSketch | None:
        model_id = _first_reference(bulk, geometry_end, model_sketches)
        return by_model.get(model_id) if model_id is not None else None

    for line_id in _ids_of(directory, schema, "Line3D"):
        found = geometry(line_id)
        if found is None:
            continue
        attributes, start_at = found
        try:
            values = struct.unpack_from("<6d", bulk, start_at)
        except struct.error:
            continue
        sketch = owner(start_at + _LINE_SIZE)
        if sketch is not None and _plausible(values):
            start = values[0:3]
            end = tuple(start[i] + values[3 + i] for i in range(3))
            sketch.lines.append(SketchLine(start, end, attributes.get(_TAG_KEY, 0)))

    for arc_id in _ids_of(directory, schema, "CircArc3D"):
        found = geometry(arc_id)
        if found is None:
            continue
        attributes, start_at = found
        try:
            values = struct.unpack_from("<12d", bulk, start_at)
        except struct.error:
            continue
        sketch = owner(start_at + _ARC_SIZE)
        if sketch is not None and _plausible(values) and values[9] > 0:
            sketch.arcs.append(SketchArc(values[0:3], values[3:6], values[6:9],
                                         values[9], values[10], values[11],
                                         attributes.get(_TAG_KEY, 0)))

    _read_splines(bulk, directory, schema, owner)
    return {sketch.feature_id: sketch for sketch in by_model.values()}


def _array(bulk: bytes, position: int, per_element: int = 1) -> tuple[tuple[float, ...], int]:
    """Liest ein Feld "Anzahl, Anzahl, 8, Daten" mit `per_element` Doubles je Element.

    Gibt die Werte und die Position hinter dem Feld zurück.
    """
    count, repeated, size = struct.unpack_from("<3I", bulk, position)
    if count != repeated or size != 8 or not 0 < count <= _SPLINE_LIMIT:
        raise ValueError("Feld passt nicht zum erwarteten Aufbau")
    values = struct.unpack_from(f"<{count * per_element}d", bulk, position + 12)
    return values, position + 12 + 8 * count * per_element


def _read_splines(bulk: bytes, directory: Directory, schema: dict[str, dict], owner) -> None:
    """Hängt die Splines an ihre Skizzen; `owner` liefert zur Position die Skizze."""
    from . import segment   # hier geladen, um einen Ringimport zu vermeiden

    def sections(name: str) -> dict[int, int]:
        guid = segment.class_guid(directory, schema, name)
        return segment.find_sections(bulk, directory, guid) if guid else {}

    tagged = sections("ConstrainedSpline3D")
    for spline_id, position in sections("Spline3D").items():
        try:
            start = position + _SPLINE_OFFSET
            (degree,) = struct.unpack_from("<I", bulk, start)
            if not 1 <= degree <= 11:
                continue
            knots, cursor = _array(bulk, start + 12)
            weights, cursor = _array(bulk, cursor)
            flat, cursor = _array(bulk, cursor, per_element=3)    # x, y, z je Kontrollpunkt
            attributes = _attributes(bulk, tagged[spline_id])[0] if spline_id in tagged else {}
        except (ValueError, struct.error):
            continue
        poles = tuple(flat[i:i + 3] for i in range(0, len(flat), 3))
        sketch = owner(cursor)
        consistent = len(poles) == len(weights) == len(knots) - degree - 1
        if sketch is not None and consistent and _plausible(flat) and _plausible(knots):
            sketch.splines.append(SketchSpline(degree, knots, weights, poles,
                                               attributes.get(_TAG_KEY, 0)))


def curve_edges(sketch: FusionSketch, tags: set[int] | None = None) -> list:
    """Kanten der Skizzenkurven in Skizzenkoordinaten (cm); optional nur bestimmte Kennnummern."""
    # Geometriekern erst hier laden, damit das reine Lesen ohne ihn auskommt
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    edges = []
    for line in sketch.lines:
        if tags is not None and line.tag not in tags:
            continue
        start, end = gp_Pnt(*line.start), gp_Pnt(*line.end)
        if start.Distance(end) > 1e-9:
            edges.append(BRepBuilderAPI_MakeEdge(start, end).Edge())
    for arc in sketch.arcs:
        if tags is not None and arc.tag not in tags:
            continue
        try:
            axes = gp_Ax2(gp_Pnt(*arc.center), gp_Dir(*arc.normal), gp_Dir(*arc.reference))
            if arc.end_angle - arc.start_angle > 1e-9:
                edges.append(BRepBuilderAPI_MakeEdge(gp_Circ(axes, arc.radius),
                                                     arc.start_angle, arc.end_angle).Edge())
        except Exception:
            continue    # entartete Richtungen oder Radien
    for spline in sketch.splines:
        if tags is not None and spline.tag not in tags:
            continue
        try:
            edges.append(BRepBuilderAPI_MakeEdge(_spline_curve(spline)).Edge())
        except Exception:
            continue    # vom Geometriekern abgelehnte Kurvendaten
    return edges


def _spline_curve(spline: SketchSpline):
    """Baut die NURBS-Kurve; der Knotenvektor wird in Werte und Vielfachheiten zerlegt."""
    from OCP.collections import Array1_double, Array1_gp_Pnt, Array1_int
    from OCP.Geom import Geom_BSplineCurve
    from OCP.gp import gp_Pnt

    values: list[float] = []
    multiplicities: list[int] = []
    for knot in spline.knots:
        if values and abs(knot - values[-1]) < 1e-12:
            multiplicities[-1] += 1
        else:
            values.append(knot)
            multiplicities.append(1)

    poles = Array1_gp_Pnt(1, len(spline.poles))
    weights = Array1_double(1, len(spline.poles))
    for index, (pole, weight) in enumerate(zip(spline.poles, spline.weights), start=1):
        poles.SetValue(index, gp_Pnt(*pole))
        weights.SetValue(index, weight)
    knots, mults = Array1_double(1, len(values)), Array1_int(1, len(values))
    for index, (value, multiplicity) in enumerate(zip(values, multiplicities), start=1):
        knots.SetValue(index, value)
        mults.SetValue(index, multiplicity)
    return Geom_BSplineCurve(poles, weights, knots, mults, spline.degree)


def placement(sketch: FusionSketch):
    """Abbildung Skizzenkoordinaten (cm) -> Weltkoordinaten (mm).

    Löst eine Ausnahme aus, wenn die Ebenenmatrix keine reine Drehung/Verschiebung ist.
    """
    from OCP.gp import gp_Trsf

    plane = gp_Trsf()
    plane.SetValues(*sketch.matrix)
    scale = gp_Trsf()
    scale.SetScaleFactor(10.0)
    return scale.Multiplied(plane)


def to_shape(sketch: FusionSketch):
    """Kurven der Skizze als Kantenverbund in Weltkoordinaten und Millimetern.

    Gibt None zurück, wenn die Ebenenmatrix keine reine Drehung/Verschiebung ist
    oder die Skizze keine darstellbaren Kurven enthält.
    """
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.TopoDS import TopoDS_Compound

    edges = curve_edges(sketch)
    if not edges:
        return None
    builder, compound = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(compound)
    for edge in edges:
        builder.Add(compound, edge)
    try:
        return BRepBuilderAPI_Transform(compound, placement(sketch), True).Shape()
    except Exception:
        return None
