# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Abrundungen und Fasen eines Fusion-Entwurfs: welche Kanten welcher Körper, mit welchem Maß.

Zusammenhang der Objekte (Namen laut Schema), an echten Daten ermittelt:
  DcFilletEdgeFeature  verweist auf eine oder mehrere DcFilletEdgeData (je Kantengruppe mit eigenem Radius)
  DcChamferFeature     verweist auf DcChamferData (je Kantengruppe)
  DcFilletEdgeData / DcChamferData  verweisen auf ein ModelDataRef (die Kanten) und auf ihre Maße
                       (DcDoubleParameter → Parameter mit Rolle „Radius“ bzw. „Distance“ …)
  ModelDataRef         ab Byte 6 eine Verweisliste (Anzahl + je 11 Byte) auf BrepRef
  BrepRef              ein Verweis auf eine Kante oder Fläche: nennt das Benennungsrezept (ASMRecipe), den
                       Körper (DcBody) und eine zwischengespeicherte Kopie der Geometrie (ETCached…)
  ETCachedLineSeg3d    ab Byte 6: Startpunkt und Vektor zum Endpunkt (je 3 Doubles, cm)
  ETCachedCircleArc    ab Byte 6: Mittelpunkt, Normale, Bezugsrichtung (je 3 Doubles), Radius, Start- und
                       Endwinkel (rad) – wie CircArc3D in Skizzen
  ETCachedSpline3d     ab Byte 8 wie die Kurvendaten eines Skizzen-Splines: Grad, Toleranz, dann Knoten, Gewichte
                       und Kontrollpunkte als Felder „Anzahl, Anzahl, 8, Daten“
Die zwischengespeicherte Geometrie steht in Modellkoordinaten (die beigefügte ETCachedMatrix3d ist in den
Beispielen fast immer als Einheitsmatrix markiert). Flächenverweise tragen statt Geometrie nur ETCachedTopology;
sie lassen sich hierüber noch nicht auflösen.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from . import segment, sketches
from .segment import Directory

_WINDOW = 600                   # Suchbereich für Verweise ab einem Abschnittsanfang
_LIST_OFFSET = 6                # Lage der Verweisliste im Abschnitt von ModelDataRef
_GEOMETRY_OFFSET = 6            # Lage der Zahlen im Abschnitt der zwischengespeicherten Geometrie
_SPLINE_OFFSET = 8              # Lage des Grads im Abschnitt eines zwischengespeicherten Splines
_MAX_EDGES = 5000               # Plausibilitätsgrenze für eine Verweisliste


@dataclass
class CachedEdge:
    """Zwischengespeicherte Geometrie einer Kante (cm, Modellkoordinaten)."""
    kind: str                   # "line", "arc" oder "spline"
    values: tuple[float, ...]   # Linie: Start, Vektor; Bogen: Mitte, Normale, Bezug, Radius, Start-/Endwinkel
    spline: object = None       # nur bei "spline": sketches.SketchSpline

    def to_edge(self, scale: float = 10.0):
        """Die Kante als Form des Geometriekerns (Vorgabe: cm → mm); None, wenn sie sich nicht bilden lässt."""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Trsf
        try:
            if self.kind == "line":
                start, end = self.points()[0], self.points()[2]
                edge = BRepBuilderAPI_MakeEdge(gp_Pnt(*start), gp_Pnt(*end)).Edge()
            elif self.kind == "arc":
                centre, normal, reference = self.values[0:3], self.values[3:6], self.values[6:9]
                radius, first, last = self.values[9:12]
                axes = gp_Ax2(gp_Pnt(*centre), gp_Dir(*normal), gp_Dir(*reference))
                edge = BRepBuilderAPI_MakeEdge(gp_Circ(axes, radius), first, last).Edge()
            else:
                edge = BRepBuilderAPI_MakeEdge(sketches._spline_curve(self.spline)).Edge()
        except Exception:
            return None
        if scale != 1.0:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
            transform = gp_Trsf()
            transform.SetScaleFactor(scale)
            edge = BRepBuilderAPI_Transform(edge, transform, True).Shape()
        return edge

    def points(self) -> list[tuple[float, float, float]]:
        """Anfang, Mitte und Ende der Kante – zum Wiederfinden im eigenen Körper."""
        if self.kind == "spline":
            curve = sketches._spline_curve(self.spline)
            first, last = curve.FirstParameter(), curve.LastParameter()
            return [tuple(getattr(curve.Value(t), axis)() for axis in ("X", "Y", "Z"))
                    for t in (first, (first + last) / 2.0, last)]
        if self.kind == "line":
            start, vector = self.values[0:3], self.values[3:6]
            return [tuple(s + v * t for s, v in zip(start, vector)) for t in (0.0, 0.5, 1.0)]
        centre, normal, reference = self.values[0:3], self.values[3:6], self.values[6:9]
        radius, first, last = self.values[9:12]
        # zweite Achse der Bogenebene: Normale × Bezugsrichtung
        other = (normal[1] * reference[2] - normal[2] * reference[1],
                 normal[2] * reference[0] - normal[0] * reference[2],
                 normal[0] * reference[1] - normal[1] * reference[0])
        result = []
        for angle in (first, (first + last) / 2.0, last):
            cos, sin = math.cos(angle), math.sin(angle)
            result.append(tuple(c + radius * (cos * r + sin * o) for c, r, o in zip(centre, reference, other)))
        return result


@dataclass
class EdgeSet:
    """Eine Kantengruppe mit gemeinsamem Maß."""
    bodies: list[int] = field(default_factory=list)            # DcBody-IDs der beteiligten Körper
    edges: list[CachedEdge] = field(default_factory=list)
    unreadable: int = 0                                        # Kanten ohne lesbare Geometrie (z. B. Splines)
    values: dict[str, float] = field(default_factory=dict)     # Rolle des Parameters -> Wert (cm bzw. rad)


@dataclass
class FusionEdgeFeature:
    feature_id: int
    kind: str                   # "fillet" oder "chamfer"
    sets: list[EdgeSet] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Sind alle Kanten lesbar und gehören zu bekannten Körpern?"""
        return bool(self.sets) and all(s.edges and s.bodies and not s.unreadable for s in self.sets)


def _cached_edge(name: str, bulk: bytes, start: int) -> CachedEdge | None:
    """Liest die zwischengespeicherte Geometrie einer Kante; None bei anderer Art oder unplausiblen Zahlen."""
    if name == "ETCachedLineSeg3d":
        values = struct.unpack_from("<6d", bulk, start + _GEOMETRY_OFFSET)
        kind = "line"
    elif name == "ETCachedCircleArc":
        values = struct.unpack_from("<12d", bulk, start + _GEOMETRY_OFFSET)
        kind = "arc"
    elif name == "ETCachedSpline3d":
        return _cached_spline(bulk, start + _SPLINE_OFFSET)
    else:
        return None
    if not all(math.isfinite(value) and abs(value) < 1e6 for value in values):
        return None
    return CachedEdge(kind, values)


def _cached_spline(bulk: bytes, start: int) -> CachedEdge | None:
    """Zwischengespeicherter Spline (Aufbau wie die Kurvendaten eines Skizzen-Splines)."""
    try:
        (degree,) = struct.unpack_from("<I", bulk, start)
        if not 1 <= degree <= 11:
            return None
        knots, cursor = sketches._array(bulk, start + 12)
        weights, cursor = sketches._array(bulk, cursor)
        flat, _cursor = sketches._array(bulk, cursor, per_element=3)
    except (ValueError, struct.error):
        return None
    poles = tuple(tuple(flat[i:i + 3]) for i in range(0, len(flat), 3))
    if not weights:
        weights = (1.0,) * len(poles)           # ohne Gewichte: gewöhnlicher (nicht rationaler) Spline
    if len(poles) != len(weights) or len(knots) != len(poles) + degree + 1 or not sketches._plausible(flat):
        return None
    spline = sketches.SketchSpline(degree, tuple(knots), tuple(weights), poles, 0)
    try:
        sketches._spline_curve(spline)
    except Exception:
        return None                             # vom Geometriekern abgelehnte Kurvendaten
    return CachedEdge("spline", (), spline)


def read_edge_features(bulk: bytes, directory: Directory, sections: dict[int, list[int]],
                       schema: dict[str, dict]) -> dict[int, FusionEdgeFeature]:
    """Alle Abrundungen und Fasen: Feature-ID -> Kantengruppen mit Körpern, Geometrie und Maßen."""
    classes = directory.class_of()
    name_of = {guid: info.get("name") for guid, info in schema.items()}

    def class_name(object_id: int) -> str | None:
        entry = classes.get(object_id)
        return name_of.get(entry.guid) if entry else None

    def ids(name: str) -> set[int]:
        return set(sketches._ids_of(directory, schema, name))

    def references(object_id: int, targets: set[int]) -> list[int]:
        found: list[int] = []
        for start in sections.get(object_id, ()):
            found.extend(sketches._references(bulk, start, start + _WINDOW, targets))
        return list(dict.fromkeys(found))       # Reihenfolge behalten, doppelte Nennungen weglassen

    features = segment._subclass_ids(directory, schema, "DcFeature")
    model_refs, brep_refs, bodies = ids("ModelDataRef"), ids("BrepRef"), ids("DcBody")
    parameters, parameter_values = ids("DcDoubleParameter"), ids("Parameter")
    cached = {object_id for object_id, entry in classes.items()
              if (name_of.get(entry.guid) or "").startswith("ETCached")}

    def parameter(parameter_id: int) -> segment.FeatureParameter | None:
        """Der Parameter hinter einem DcDoubleParameter (Rolle, Ausdruck, Wert)."""
        for value_id in references(parameter_id, parameter_values):
            for start in sections.get(value_id, ()):
                try:
                    return segment._parameter_at(bulk, start, features)[1]
                except (ValueError, struct.error):
                    continue
        return None

    def edge_list(model_ref: int) -> list[int]:
        """Die BrepRef-Verweise eines ModelDataRef (Verweisliste ab Byte 6)."""
        for start in sections.get(model_ref, ()):
            try:
                (count,) = struct.unpack_from("<I", bulk, start + _LIST_OFFSET)
                if 0 < count <= _MAX_EDGES:
                    listed = segment.read_reference_list(bulk, start + _LIST_OFFSET)
                    found = [object_id for object_id in listed if object_id in brep_refs]
                    if found:
                        return found
            except (ValueError, struct.error):
                continue
        return []

    def edge_set(data_id: int) -> EdgeSet:
        result = EdgeSet()
        for parameter_id in references(data_id, parameters):
            found = parameter(parameter_id)
            if found is not None:
                result.values.setdefault(found.role, found.value)
        for model_ref in references(data_id, model_refs)[:1]:
            for brep in edge_list(model_ref):
                for body in references(brep, bodies)[:1]:
                    if body not in result.bodies:
                        result.bodies.append(body)
                geometry = None
                for target in references(brep, cached):
                    for start in sections.get(target, ())[:1]:
                        geometry = _cached_edge(class_name(target), bulk, start)
                    if geometry is not None:
                        break
                if geometry is None:
                    result.unreadable += 1
                else:
                    result.edges.append(geometry)
        return result

    found: dict[int, FusionEdgeFeature] = {}
    for kind, feature_class, data_class in (("fillet", "DcFilletEdgeFeature", "DcFilletEdgeData"),
                                            ("chamfer", "DcChamferFeature", "DcChamferData")):
        data_ids = ids(data_class)
        for feature_id in ids(feature_class):
            feature = FusionEdgeFeature(feature_id, kind)
            for data_id in references(feature_id, data_ids):
                feature.sets.append(edge_set(data_id))
            found[feature_id] = feature
    return found
