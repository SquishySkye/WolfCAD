# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Wandelt gelesene ASM-Datensätze in OpenCascade-Körper um.

Ablauf je Körper: Topologie ablaufen (Körper → Klumpen → Schale → Fläche →
Schleife → Kante), jede Fläche aus ihrer Trägerfläche und ihren Randkurven
aufbauen, die Flächen vernähen und – wenn die Hülle geschlossen ist – zu einem
Volumenkörper machen. Flächen, die sich nicht aufbauen lassen, werden gezählt
und ausgelassen; der Körper bleibt dann eine offene Hülle.

Feldpositionen der Topologie-Datensätze (nach den drei gemeinsamen Kopf-Feldern):
  body:   Klumpen, Draht, Transformation
  lump:   nächster Klumpen, Schale, Körper
  shell:  nächste Schale, Unterschale, Fläche, Draht, Klumpen
  face:   nächste Fläche, Schleife, Schale, Unterschale, Trägerfläche, Richtungsflag
  loop:   nächste Schleife, erste Halbkante, Fläche
  coedge: nächste, vorige, Partner, Kante, Richtungsflag, Schleife
  edge:   Startpunkt, Startparameter, Endpunkt, Endparameter, Halbkante, Kurve, Richtungsflag

Richtungsflags: True bedeutet "vorwärts", False "umgekehrt" (an echten Daten ermittelt).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_Sewing, BRepBuilderAPI_Transform,
)
from OCP.BRepLib import BRepLib
from OCP.Geom import Geom_Circle, Geom_Curve, Geom_Ellipse, Geom_Line
from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.ShapeAnalysis import ShapeAnalysis_Shell
from OCP.ShapeFix import ShapeFix_Face, ShapeFix_Solid
from OCP.TopAbs import TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED, TopAbs_SHELL
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import (
    TopoDS, TopoDS_Compound, TopoDS_Edge, TopoDS_Face, TopoDS_Shape, TopoDS_Shell, TopoDS_Vertex,
    TopoDS_Wire,
)

from . import nurbs, surfaces
from .sab import Record, SabFile

_BODY_LUMP, _BODY_TRANSFORM = 3, 5
_LUMP_NEXT, _LUMP_SHELL = 3, 4
_SHELL_NEXT, _SHELL_FACE = 3, 5
_FACE_NEXT, _FACE_LOOP, _FACE_SURFACE, _FACE_FORWARD = 3, 4, 7, 8
_LOOP_NEXT, _LOOP_COEDGE = 3, 4
_COEDGE_NEXT, _COEDGE_PARTNER, _COEDGE_EDGE, _COEDGE_FORWARD, _COEDGE_LOOP = 3, 5, 6, 7, 8
_LOOP_FACE = 5
_EDGE_START, _EDGE_T0, _EDGE_END, _EDGE_T1, _EDGE_CURVE, _EDGE_FORWARD = 3, 4, 5, 6, 8, 9
_CURVE_FORWARD = 3     # nur bei intcurve-curve
_VERTEX_POINT = 5
_POINT_XYZ = 3

# Toleranzen in Modelleinheiten (Zentimeter). Die NURBS-Näherungen der Datei
# weichen bis etwa 1e-3 von der exakten Geometrie ab.
_POINT_TOLERANCE = 2e-3
_VERTEX_TOLERANCE = 1e-6
_SEW_TOLERANCE = 2e-3
# Schutz vor Endlosschleifen bei beschädigten Verkettungen
_CHAIN_LIMIT = 1_000_000
_TIMESTAMP_ATTRIBUTE = "Timestamp_attrib_def"


@dataclass
class ImportResult:
    shape: TopoDS_Shape     # Verbund aller Körper, in Millimetern
    # Einzelne Körper in Millimetern mit ihrem Zeitstempel (None = Körper ohne Zeitstempel).
    # Der Zeitstempel nennt den Schritt des Entwurfs, der den Körper zuletzt verändert hat;
    # Körper ohne Zeitstempel sind zwischengespeicherte Hilfs- und Zwischenkörper.
    parts: list = field(default_factory=list)
    bodies: int = 0
    solids: int = 0         # davon geschlossene Volumenkörper
    faces_total: int = 0
    faces_built: int = 0
    # Gesuchte benannte Flächen (siehe faces.py): Name -> [(Fläche in mm und Endlage, Namen der Nachbarflächen)]
    named_faces: dict = field(default_factory=dict)


class _Builder:
    """Hält die Zwischenergebnisse eines Importlaufs (Kanten werden mehrfach benutzt)."""

    def __init__(self, sab: SabFile):
        self.sab = sab
        self.named: dict = {}       # beim letzten Körper gemerkte benannte Flächen (siehe body)
        self.blocks = nurbs.BlockTable(sab)
        self.builder = BRep_Builder()
        self._edges: dict[int, tuple[TopoDS_Edge, bool] | None] = {}
        self._vertices: dict[int, tuple[TopoDS_Vertex, gp_Pnt]] = {}

    def _chain(self, first: Record | None, next_field: int):
        """Läuft eine über 'nächster'-Verweise verkettete Liste ab."""
        record, steps = first, 0
        while record is not None and steps < _CHAIN_LIMIT:
            yield record
            record = self.sab.get(record.fields[next_field])
            steps += 1

    # --- Kanten -----------------------------------------------------------

    def _vertex(self, ref) -> tuple[TopoDS_Vertex, gp_Pnt] | None:
        """Eckpunkt zum Verweis; wird nur einmal erzeugt, damit Kanten ihn teilen."""
        record = self.sab.get(ref)
        if record is None:
            return None
        if record.index not in self._vertices:
            point_record = self.sab.get(record.fields[_VERTEX_POINT])
            if point_record is None:
                return None
            point = gp_Pnt(*point_record.fields[_POINT_XYZ])
            vertex = TopoDS_Vertex()
            self.builder.MakeVertex(vertex, point, _VERTEX_TOLERANCE)
            self._vertices[record.index] = (vertex, point)
        return self._vertices[record.index]

    def _curve(self, record: Record) -> tuple[Geom_Curve | None, bool]:
        """Kurve des Datensatzes und ob sie in Richtung ihrer Definition gilt."""
        f = record.fields
        if record.name == "ellipse-curve":
            center, normal, major, ratio = f[3], f[4], f[5], f[6]
            radius = math.sqrt(sum(c * c for c in major))
            axes = gp_Ax2(gp_Pnt(*center), gp_Dir(*normal), gp_Dir(*major))
            if abs(ratio - 1.0) < 1e-12:
                return Geom_Circle(axes, radius), True
            return Geom_Ellipse(axes, radius, radius * ratio), True
        if record.name == "intcurve-curve":
            block = self.blocks.top_block(record)
            return (nurbs.curve_at(*block) if block else None), f[_CURVE_FORWARD] is True
        return None, True

    def edge(self, record: Record) -> tuple[TopoDS_Edge, bool] | None:
        """Kante zum Datensatz und ob sie in Richtung Start → Ende der Datei verläuft.

        Jede Kante wird nur einmal erzeugt; benachbarte Flächen teilen sie sich.
        """
        if record.index not in self._edges:
            try:
                self._edges[record.index] = self._make_edge(record)
            except Exception:
                # Einzelne nicht darstellbare Kanten sollen den Import nicht abbrechen
                self._edges[record.index] = None
        return self._edges[record.index]

    def _make_edge(self, record: Record) -> tuple[TopoDS_Edge, bool] | None:
        f = record.fields
        start, end = self._vertex(f[_EDGE_START]), self._vertex(f[_EDGE_END])
        curve_record = self.sab.get(f[_EDGE_CURVE])
        if start is None or end is None or curve_record is None:
            return None     # z. B. Kegelspitze: Halbkante ohne Kurve
        (start_vertex, start_point), (end_vertex, end_point) = start, end
        curve, curve_forward = self._curve(curve_record)

        if curve is None:
            # Geraden und nicht lesbare Kurven: direkte Verbindung der Endpunkte
            length = start_point.Distance(end_point)
            if length < 1e-9:
                return None
            direction = gp_Dir(gp_Vec(start_point, end_point))
            curve, first, last = Geom_Line(start_point, direction), 0.0, length
            forward = True
        else:
            # Kante und Kurve gleich gerichtet: Die Kante läuft von C(t0) nach C(t1).
            # Sonst läuft sie gegen die Kurve, von C(-t0) nach C(-t1).
            forward = (f[_EDGE_FORWARD] is True) == curve_forward
            sign = 1.0 if forward else -1.0
            first, last = sorted((sign * float(f[_EDGE_T0]), sign * float(f[_EDGE_T1])))
            if not self._range_fits(curve, first, last, start_point, end_point):
                if start_vertex.IsSame(end_vertex):
                    first, last = curve.FirstParameter(), curve.LastParameter()
                    if math.isinf(first) or math.isinf(last):
                        return None
                else:
                    # Parameter passen nicht (z. B. verschobene Periode): Endpunkte
                    # projizieren und die Richtung aus deren Lage bestimmen
                    at_start = self._project(curve, start_point)
                    at_end = self._project(curve, end_point)
                    if curve.IsPeriodic():
                        # Auf einem Kreis gibt es zwei Bögen zwischen den Punkten;
                        # die Laufrichtung der Kante legt fest, welcher gemeint ist.
                        period = curve.Period()
                        if forward:
                            first, last = at_start, at_end
                        else:
                            first, last = at_end, at_start
                        if last <= first:
                            last += period
                    else:
                        forward = at_start < at_end
                        first, last = sorted((at_start, at_end))
            if last - first < 1e-12:
                return None

        at_first, at_last = curve.Value(first), curve.Value(last)
        first_vertex, last_vertex = (start, end) if forward else (end, start)

        edge = TopoDS_Edge()
        self.builder.MakeEdge(edge, curve, _VERTEX_TOLERANCE)
        self.builder.Range(edge, first, last)
        # Die Eckpunkt-Toleranz muss die Abweichung der genäherten Kurve abdecken
        for (vertex, point), on_curve, orientation in (
                (first_vertex, at_first, TopAbs_FORWARD), (last_vertex, at_last, TopAbs_REVERSED)):
            self.builder.UpdateVertex(vertex, max(_VERTEX_TOLERANCE, 1.2 * point.Distance(on_curve)))
            self.builder.Add(edge, vertex.Oriented(orientation))
        return edge, forward

    @staticmethod
    def _range_fits(curve: Geom_Curve, first: float, last: float, start: gp_Pnt, end: gp_Pnt) -> bool:
        """Prüft, ob die Kurve an den Parametergrenzen die beiden Eckpunkte trifft."""
        if last - first < 1e-12:
            return False
        # Kreise und Ellipsen sind periodisch: jeder Parameterbereich ist zulässig
        if not curve.IsPeriodic():
            low, high = curve.FirstParameter(), curve.LastParameter()
            if first < low - 1e-9 or last > high + 1e-9:
                return False
        a, b = curve.Value(first), curve.Value(last)
        direct = max(a.Distance(start), b.Distance(end))
        swapped = max(a.Distance(end), b.Distance(start))
        return min(direct, swapped) < _POINT_TOLERANCE

    @staticmethod
    def _project(curve: Geom_Curve, point: gp_Pnt) -> float:
        projection = GeomAPI_ProjectPointOnCurve(point, curve)
        if projection.NbPoints() == 0:
            raise ValueError("Eckpunkt liegt nicht auf der Kurve")
        return projection.LowerDistanceParameter()

    # --- Flächen ----------------------------------------------------------

    def _wire(self, loop: Record, face_reversed: bool) -> TopoDS_Wire | None:
        """Rand einer Schleife; die Kanten erhalten die Umlaufrichtung aus der Datei.

        In der Datei umlaufen die Halbkanten die Flächennormale gegen den
        Uhrzeigersinn. OpenCascade bezieht die Richtung auf die Normale der
        Trägerfläche, deshalb wird bei umgekehrten Flächen jede Kante gedreht.
        """
        wire = TopoDS_Wire()
        self.builder.MakeWire(wire)
        first = self.sab.get(loop.fields[_LOOP_COEDGE])
        steps = added = 0
        for coedge in self._chain(first, _COEDGE_NEXT):
            if steps and coedge is first:
                break
            steps += 1
            edge_record = self.sab.get(coedge.fields[_COEDGE_EDGE])
            built = self.edge(edge_record) if edge_record else None
            if built is None:
                continue
            edge, forward = built
            along = forward == (coedge.fields[_COEDGE_FORWARD] is True)
            if face_reversed:
                along = not along
            self.builder.Add(wire, edge.Oriented(TopAbs_FORWARD if along else TopAbs_REVERSED))
            added += 1
        return wire if added else None

    def face(self, record: Record) -> TopoDS_Shape | None:
        surface_record = self.sab.get(record.fields[_FACE_SURFACE])
        surface, inward = surfaces.build(surface_record, self.blocks) if surface_record else (None, False)
        if surface is None:
            return None
        face = TopoDS_Face()
        self.builder.MakeFace(face, surface, _POINT_TOLERANCE)
        # Umgekehrt ist die Fläche, wenn genau eines zutrifft: Flag der Datei oder hohle Grundfläche
        reversed_face = (record.fields[_FACE_FORWARD] is False) != inward
        for loop in self._chain(self.sab.get(record.fields[_FACE_LOOP]), _LOOP_NEXT):
            wire = self._wire(loop, reversed_face)
            if wire is not None:
                self.builder.Add(face, wire)

        # ShapeFix sortiert die Kanten, ergänzt deren Kurven auf der Fläche und
        # fügt bei rundum geschlossenen Flächen (Zylinder) die fehlende Naht ein.
        fixer = ShapeFix_Face(face)
        fixer.Perform()
        fixed = fixer.Face()
        # Raumkurve und Kurve auf der Fläche weichen bei genäherten Flächen leicht
        # voneinander ab; die Toleranzen der Kanten müssen das abdecken.
        BRepLib.SameParameter_s(fixed, _VERTEX_TOLERANCE, True)
        return fixed.Reversed() if reversed_face else fixed

    # --- Körper -----------------------------------------------------------

    def _transform(self, body: Record) -> gp_Trsf | None:
        """Lage des Körpers; die Datei speichert die Drehmatrix zeilenweise (p' = p · M)."""
        record = self.sab.get(body.fields[_BODY_TRANSFORM])
        if record is None:
            return None
        rows, shift, scale = record.fields[3:6], record.fields[6], record.fields[7]
        transform = gp_Trsf()
        transform.SetValues(rows[0][0], rows[1][0], rows[2][0], shift[0],
                            rows[0][1], rows[1][1], rows[2][1], shift[1],
                            rows[0][2], rows[1][2], rows[2][2], shift[2])
        if abs(scale - 1.0) > 1e-12:
            scaling = gp_Trsf()
            scaling.SetScaleFactor(scale)
            transform = transform.Multiplied(scaling)
        return transform

    def neighbour_names(self, record: Record) -> frozenset:
        """Namen der Nachbarflächen: über die Schleifen und Halbkanten zur Partner-Halbkante und deren Fläche."""
        from .faces import attribute_name
        found = set()
        for loop in self._chain(self.sab.get(record.fields[_FACE_LOOP]), _LOOP_NEXT):
            first = self.sab.get(loop.fields[_LOOP_COEDGE])
            for steps, coedge in enumerate(self._chain(first, _COEDGE_NEXT)):
                # die Halbkanten einer Schleife bilden einen Ring: zurück am Anfang ist die Schleife durchlaufen
                # (_chain selbst endet erst an seiner Obergrenze – das machte das Einlesen anfangs extrem langsam)
                if steps and coedge is first:
                    break
                partner = self.sab.get(coedge.fields[_COEDGE_PARTNER])
                other_loop = self.sab.get(partner.fields[_COEDGE_LOOP]) if partner is not None else None
                other = self.sab.get(other_loop.fields[_LOOP_FACE]) if other_loop is not None else None
                if other is not None and other.index != record.index:
                    attribute = self.sab.get(other.fields[0])
                    name = attribute_name(attribute.fields) if attribute is not None else None
                    if name:
                        found.add(name)
        return frozenset(found)

    def body(self, record: Record, result: ImportResult, wanted: set | None = None) -> TopoDS_Shape | None:
        """Baut einen Körper. Flächen mit einem Namen aus `wanted` werden zusätzlich einzeln gemerkt
        (`self.named`: Name -> [(Fläche, Nachbarnamen)], noch in Körperlage – Endlage setzt import_bodies)."""
        from .faces import attribute_name
        self.named = {}
        sewing = BRepBuilderAPI_Sewing(_SEW_TOLERANCE)
        built = 0
        for lump in self._chain(self.sab.get(record.fields[_BODY_LUMP]), _LUMP_NEXT):
            for shell in self._chain(self.sab.get(lump.fields[_LUMP_SHELL]), _SHELL_NEXT):
                for face_record in self._chain(self.sab.get(shell.fields[_SHELL_FACE]), _FACE_NEXT):
                    result.faces_total += 1
                    try:
                        face = self.face(face_record)
                    except Exception:
                        face = None
                    if face is not None:
                        sewing.Add(face)
                        built += 1
                        if wanted:
                            attribute = self.sab.get(face_record.fields[0])
                            name = attribute_name(attribute.fields) if attribute is not None else None
                            if name in wanted:
                                self.named.setdefault(name, []).append((face, self.neighbour_names(face_record)))
        if not built:
            return None
        result.faces_built += built
        sewing.Perform()
        shape = sewing.SewedShape()

        solid = self._solid(shape)
        if solid is not None:
            shape = solid
            result.solids += 1
        try:
            transform = self._transform(record)
        except Exception:
            transform = None    # z. B. Spiegelung; Körper bleibt in seiner eigenen Lage
        if transform is not None:
            shape = BRepBuilderAPI_Transform(shape, transform, True).Shape()
            self.named = {name: [(BRepBuilderAPI_Transform(face, transform, True).Shape(), around)
                                 for face, around in entries] for name, entries in self.named.items()}
        return shape

    def _solid(self, shape: TopoDS_Shape) -> TopoDS_Shape | None:
        """Macht aus geschlossenen Hüllen Volumenkörper (None, wenn etwas offen bleibt)."""
        shells = [TopoDS.Shell(item) for item in _explore(shape, TopAbs_SHELL)]
        if not shells:
            # Eine einzelne rundum geschlossene Fläche (Kugel, Torus) ergibt beim
            # Nähen keine Hülle; sie wird hier von Hand in eine gepackt.
            shell = TopoDS_Shell()
            self.builder.MakeShell(shell)
            for item in _explore(shape, TopAbs_FACE):
                self.builder.Add(shell, item)
            shells = [shell]

        solids = []
        for shell in shells:
            analysis = ShapeAnalysis_Shell()
            # Die Prüfung muss angestoßen werden; erst danach sind freie Kanten bekannt
            analysis.CheckOrientedShells(shell, True, True)
            if analysis.HasFreeEdges():
                return None
            try:
                # ShapeFix richtet die Flächen so aus, dass das Volumen innen liegt
                fixer = ShapeFix_Solid()
                solids.append(fixer.SolidFromShell(shell))
            except Exception:
                return None
        if len(solids) == 1:
            return solids[0]
        # Mehrere Hüllen (getrennte Teile oder Hohlräume) bleiben vorerst einzelne Körper
        compound = TopoDS_Compound()
        self.builder.MakeCompound(compound)
        for solid in solids:
            self.builder.Add(compound, solid)
        return compound


def _explore(shape: TopoDS_Shape, kind):
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        yield explorer.Current()
        explorer.Next()


def import_bodies(sab: SabFile, progress: Callable[[int, int], bool] | None = None,
                  wanted: set | None = None) -> ImportResult:
    """Baut alle Körper der Datei auf und gibt sie als Verbund in Millimetern zurück.

    `progress(erledigt, gesamt)` wird nach jedem Körper aufgerufen; gibt die
    Funktion False zurück, bricht der Import ab und liefert das bisher Gebaute.
    `wanted`: Flächennamen, die der Entwurf nennt (Fläche löschen, Flächen versetzen …); diese Flächen landen
    zusätzlich einzeln in `named_faces`.
    """
    builder = _Builder(sab)
    compound = TopoDS_Compound()
    builder.builder.MakeCompound(compound)
    result = ImportResult(compound)
    scale = gp_Trsf()
    scale.SetScaleFactor(sab.header.unit_scale)

    bodies = sab.of_type("body")
    for number, record in enumerate(bodies, start=1):
        try:
            shape = builder.body(record, result, wanted)
        except Exception:
            shape = None
        if shape is not None:
            # Modelleinheit (bei Fusion Zentimeter) in Millimeter umrechnen
            shape = BRepBuilderAPI_Transform(shape, scale, True).Shape()
            for name, entries in builder.named.items():
                result.named_faces.setdefault(name, []).extend(
                    (BRepBuilderAPI_Transform(face, scale, True).Shape(), around) for face, around in entries)
            builder.builder.Add(compound, shape)
            result.parts.append((shape, body_timestamp(sab, record)))
            result.bodies += 1
        if progress is not None and progress(number, len(bodies)) is False:
            break
    return result


def body_timestamp(sab: SabFile, body: Record) -> int | None:
    """Zeitstempel eines Körpers aus seinem Attribut 'Timestamp_attrib_def' (None, wenn keines da ist)."""
    attribute, steps = sab.get(body.fields[0]), 0
    while attribute is not None and steps < 32:
        if _TIMESTAMP_ATTRIBUTE in attribute.fields and isinstance(attribute.fields[-1], float):
            return int(attribute.fields[-1])
        # Attribute eines Objekts sind über ihr viertes Feld verkettet
        following = attribute.fields[3] if len(attribute.fields) > 3 else None
        attribute, steps = sab.get(following), steps + 1
    return None
