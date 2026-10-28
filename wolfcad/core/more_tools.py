# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Weitere Fusion-Werkzeuge (Runde 58): Prägen, Steg, Konturflansch, Aufreißen, Ebene entlang Pfad, Achse senkrecht
zur Fläche, Gelenkursprung, Starre Gruppe und Leinwand.

Alle Werkzeuge sind gewöhnliche Timeline-Schritte (siehe features.Feature) und werden unten für das Laden
gespeicherter Entwürfe angemeldet.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from OCP.BRepAdaptor import BRepAdaptor_CompCurve, BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon,
                                BRepBuilderAPI_MakeWire, BRepBuilderAPI_Transform)
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.GC import GC_MakeArcOfCircle
from OCP.GCPnts import GCPnts_AbscissaPoint
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane
from OCP.TopAbs import TopAbs_EDGE, TopAbs_IN, TopAbs_REVERSED, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec

from . import features as ft
from .features import ALL_BODIES, BuildState, Feature, FeatureError

EMBOSS_MODES = ("Prägen (erhaben)", "Gravieren (vertieft)")


# --------------------------------------------------------------------------- Hilfsfunktionen ---

def _vec(point: gp_Pnt) -> gp_Vec:
    return gp_Vec(point.X(), point.Y(), point.Z())


def _shift(shape, vector: gp_Vec):
    """Die Form um einen Vektor verschoben (als Kopie)."""
    move = gp_Trsf()
    move.SetTranslation(vector)
    return BRepBuilderAPI_Transform(shape, move, True).Shape()


def _reach(state: BuildState, extra=()) -> float:
    """Eine Länge, die sicher durch das ganze Modell reicht (für „durch alles“-Prismen)."""
    from .solids import _bounds
    size = 100.0
    if state.bodies:
        bounds = _bounds(state.body)
        size += 2 * math.dist(bounds[:3], bounds[3:])
    for point in extra:
        size += abs(point.X()) + abs(point.Y()) + abs(point.Z())
    return size


def _solid_volume(shape) -> float:
    from .solids import _volume
    return _volume(shape) if TopExp_Explorer(shape, TopAbs_SOLID).More() else 0.0


def owner_of(state: BuildState, body: str, points: list, edges: bool = False) -> str:
    """Bei „Alle“ (Vorgabe, wenn das Werkzeug ohne Auswahl gestartet wurde): der Körper, an dem der erste
    Merkpunkt liegt – eine Fläche bzw. mit `edges` eine Kante. Sonst bleibt der genannte Körper."""
    if body != ALL_BODIES or not points:
        return body
    point_of = ft.edge_point if edges else ft.face_point
    for name, shape in state.bodies.items():
        items = ft.unique_edges(shape) if edges else ft.faces_of(shape)
        if any(math.dist(point_of(item), points[0][:3]) < ft.SELECTION_TOLERANCE for item in items):
            return name
    return body


def _face_at(state: BuildState, body: str, points: list, label: str = "Fläche"):
    body = owner_of(state, body, points)
    if body not in state.bodies:
        raise ft.missing_body(body)
    if not points:
        raise FeatureError(f"Bitte zuerst eine {label} anklicken")
    return TopoDS.Face(ft.find_by_points(ft.faces_of(state.bodies[body]), ft.face_point, points[:1], label)[0])


def _outward(face) -> gp_Dir:
    """Außennormale einer ebenen Fläche (beachtet ihre Ausrichtung im Körper)."""
    normal = BRepAdaptor_Surface(face).Plane().Axis().Direction()
    return normal.Reversed() if face.Orientation() == TopAbs_REVERSED else normal


# --------------------------------------------------------------------------------- Prägen ---

@dataclass
class Emboss(Feature):
    """Prägen (Fusion: Prägen): das Profil einer Skizze wird auf eine Fläche des Körpers übertragen und dort um die
    Tiefe erhaben aufgesetzt oder vertieft eingraviert.

    Ebene Flächen: das Profil wird senkrecht zur Skizze auf die Fläche projiziert. Zylinderflächen: das Profil wird
    von der Skizze zur Zylinderachse hin projiziert (nicht abgerollt – breite Schriftzüge werden zum Rand hin
    schmaler, wie bei einer Projektion).
    """

    sketch: str = ""
    body: str = ALL_BODIES
    face_points: list = field(default_factory=list)
    depth: float = 1.0
    mode: str = EMBOSS_MODES[0]

    KIND = "Prägen"

    def apply(self, state: BuildState) -> None:
        if self.depth <= 0:
            raise FeatureError("Die Tiefe muss größer als 0 sein")
        if self.mode not in EMBOSS_MODES:
            raise FeatureError(f"Unbekannte Art '{self.mode}'")
        profile = state.solid_profiles.get(self.sketch) or state.profiles.get(self.sketch)
        if profile is None:
            raise FeatureError(f"Die Skizze '{self.sketch}' hat kein geschlossenes Profil")
        body = owner_of(state, self.body, self.face_points)     # „Alle“: Körper mit der Fläche
        face = _face_at(state, body, self.face_points)
        frame = state.frames[self.sketch]
        normal = gp_Vec(frame.Direction().XYZ())
        raised = self.mode == EMBOSS_MODES[0]
        surface = BRepAdaptor_Surface(face)
        reach = _reach(state, [frame.Location()])
        if surface.GetType() == GeomAbs_Plane:
            outward = gp_Vec(_outward(face).XYZ())
            if abs(outward.Dot(normal)) < 1e-6:
                raise FeatureError("Die Skizze steht senkrecht zur Fläche – das Profil träfe sie nur als Linie")
            # Profil durch alles, dann auf die Schicht über (bzw. unter) der Fläche beschränkt
            tool = BRepPrimAPI_MakePrism(_shift(profile, normal * -reach), normal * (2 * reach)).Shape()
            base = surface.Plane().Location()
            start = base if raised else base.Translated(outward * -self.depth)
            sheet = BRepBuilderAPI_MakeFace(gp_Pln(start, gp_Dir(outward.XYZ())), -reach, reach, -reach, reach).Face()
            slab = BRepPrimAPI_MakePrism(sheet, outward * self.depth).Shape()
        elif surface.GetType() == GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            radius, axis = cylinder.Radius(), cylinder.Axis()
            if abs(gp_Vec(axis.Direction().XYZ()).Dot(normal)) > 0.99:
                raise FeatureError("Die Skizze muss seitlich zum Zylinder liegen (nicht quer zu seiner Achse)")
            if not raised and self.depth >= radius:
                raise FeatureError("Die Tiefe ist größer als der Zylinderradius")
            # nur von der Skizze bis zur Achse projizieren: sonst träfe das Profil auch die Rückseite
            distance = gp_Vec(frame.Location(), axis.Location()).Dot(normal)
            if abs(distance) < 1e-6:
                raise FeatureError("Die Skizze liegt auf der Zylinderachse – bitte eine Ebene neben dem Zylinder")
            tool = BRepPrimAPI_MakePrism(profile, normal * distance).Shape()
            low, high = (radius, radius + self.depth) if raised else (radius - self.depth, radius)
            foot = gp_Ax2(axis.Location().Translated(gp_Vec(axis.Direction().XYZ()) * -reach), axis.Direction())
            ring = ft._boolean(BRepAlgoAPI_Cut, BRepPrimAPI_MakeCylinder(foot, high, 2 * reach).Shape(),
                               BRepPrimAPI_MakeCylinder(foot, low, 2 * reach).Shape())
            slab = ring
        else:
            raise FeatureError("Prägen geht auf ebenen Flächen und Zylinderflächen")
        piece = ft._boolean(BRepAlgoAPI_Common, tool, slab)
        if _solid_volume(piece) < 1e-9:
            raise FeatureError("Das Profil trifft die gewählte Fläche nicht")
        operation = BRepAlgoAPI_Fuse if raised else BRepAlgoAPI_Cut
        state.bodies[body] = ft._boolean(operation, state.bodies[body], piece)


# ----------------------------------------------------------------------------------- Steg ---

@dataclass
class Web(Feature):
    """Steg (Fusion: Steg): jede offene Linie der Skizze wird zu einer Wand mit der Dicke (je zur Hälfte auf beiden
    Seiten der Linie), die senkrecht zur Skizze um die Tiefe hochsteht; berührte Körper werden damit vereinigt."""

    sketch: str = ""
    thickness: float = 2.0
    depth: float = 10.0
    flip: bool = False              # zur anderen Seite der Skizzenebene

    KIND = "Steg"

    def apply(self, state: BuildState) -> None:
        if self.sketch not in state.curves or self.sketch not in state.frames:
            raise FeatureError(f"Skizze '{self.sketch}' fehlt oder enthält keine Linien")
        if self.thickness <= 0 or self.depth <= 0:
            raise FeatureError("Dicke und Tiefe müssen größer als 0 sein")
        normal = gp_Vec(state.frames[self.sketch].Direction().XYZ()) * (-1.0 if self.flip else 1.0)
        walls = []
        explorer = TopExp_Explorer(state.curves[self.sketch], TopAbs_EDGE)
        while explorer.More():
            edge = TopoDS.Edge(explorer.Current())
            explorer.Next()
            curve = BRepAdaptor_Curve(edge)
            first, last = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
            if first.Distance(last) < 1e-9:
                raise FeatureError("Ein Steg braucht offene Linien (keinen geschlossenen Kreis)")
            if curve.GetType() == GeomAbs_Line:
                # gerade Wand: Querschnitt (Dicke × Tiefe) entlang der Linie geschoben
                along = gp_Vec(first, last)
                across = normal.Crossed(along)
                across.Normalize()
                corner = first.Translated(across * (-self.thickness / 2))
                polygon = BRepBuilderAPI_MakePolygon(corner, corner.Translated(across * self.thickness),
                                                     corner.Translated(across * self.thickness + normal * self.depth),
                                                     corner.Translated(normal * self.depth), True)
                section = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
                walls.append(BRepPrimAPI_MakePrism(section, along).Shape())
            else:
                # Bogen/Spline: Fläche hochziehen und zu beiden Seiten aufdicken
                from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
                sheet = BRepPrimAPI_MakePrism(edge, normal * self.depth).Shape()
                halves = []
                for side in (1.0, -1.0):
                    thick = BRepOffsetAPI_MakeThickSolid()
                    thick.MakeThickSolidBySimple(sheet, side * self.thickness / 2)
                    thick.Build()
                    if not thick.IsDone():
                        raise FeatureError("Die gekrümmte Linie ließ sich nicht zur Wand aufdicken")
                    halves.append(thick.Shape())
                walls.append(ft._boolean(BRepAlgoAPI_Fuse, *halves))
        if not walls:
            raise FeatureError(f"Skizze '{self.sketch}' enthält keine Linien")
        merged = walls[0]
        for wall in walls[1:]:
            merged = ft._boolean(BRepAlgoAPI_Fuse, merged, wall)
        ft._combine(state, merged, "Vereinigen")


# ----------------------------------------------------------------------------- Blech: Kontur ---

def _chain_points(state: BuildState, sketch: str) -> list[gp_Pnt]:
    """Die Ecken eines offenen, unverzweigten Linienzugs aus geraden Linien, in Reihenfolge."""
    if sketch not in state.curves:
        raise FeatureError(f"Skizze '{sketch}' fehlt oder enthält keine Linien")
    segments = []
    explorer = TopExp_Explorer(state.curves[sketch], TopAbs_EDGE)
    while explorer.More():
        curve = BRepAdaptor_Curve(TopoDS.Edge(explorer.Current()))
        explorer.Next()
        if curve.GetType() != GeomAbs_Line:
            raise FeatureError("Der Konturflansch braucht einen Linienzug aus geraden Linien (Ecken werden gebogen)")
        segments.append((curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())))
    key = lambda p: (round(p.X(), 5), round(p.Y(), 5), round(p.Z(), 5))
    neighbours: dict = {}
    points: dict = {}
    for a, b in segments:
        for one, other in ((a, b), (b, a)):
            neighbours.setdefault(key(one), []).append(key(other))
            points[key(one)] = one
    ends = [k for k, found in neighbours.items() if len(found) == 1]
    if len(ends) != 2 or any(len(found) > 2 for found in neighbours.values()):
        raise FeatureError("Der Konturflansch braucht einen offenen, unverzweigten Linienzug")
    order, previous = [ends[0]], None
    while len(order) <= len(segments):
        following = [k for k in neighbours[order[-1]] if k != previous]
        if not following:
            break
        previous = order[-1]
        order.append(following[0])
    if len(order) != len(segments) + 1:
        raise FeatureError("Der Linienzug ist nicht zusammenhängend")
    return [points[k] for k in order]


@dataclass
class ContourFlange(Feature):
    """Konturflansch (Fusion: Konturflansch): ein offener Linienzug der Skizze wird zum Blechprofil – gerade Stücke
    werden Platten, Ecken Biegungen mit dem inneren Biegeradius; das Profil wird senkrecht zur Skizze um die
    Breite gezogen. Das Teil ist ein Blechteil mit Abwicklung (Zuschnittlänge über den K-Faktor)."""

    sketch: str = ""
    width: float = 20.0
    thickness: float = 1.0
    bend_radius: float = 1.0
    k_factor: float = 0.44
    flip: bool = False              # Dicke zur anderen Seite des Linienzugs
    flip_width: bool = False        # Breite zur anderen Seite der Skizzenebene

    KIND = "Konturflansch"

    def apply(self, state: BuildState) -> None:
        from .sheetmetal import _new_sheet
        if self.thickness <= 0 or self.width <= 0:
            raise FeatureError("Blechdicke und Breite müssen größer als 0 sein")
        if self.bend_radius < 0 or not 0.0 <= self.k_factor <= 1.0:
            raise FeatureError("Biegeradius ≥ 0 und K-Faktor zwischen 0 und 1")
        corners3 = _chain_points(state, self.sketch)
        frame = state.frames[self.sketch]
        origin, u_axis, v_axis = frame.Location(), gp_Vec(frame.XDirection().XYZ()), gp_Vec(frame.YDirection().XYZ())
        normal = gp_Vec(frame.Direction().XYZ())
        to2 = lambda p: (gp_Vec(origin, p).Dot(u_axis), gp_Vec(origin, p).Dot(v_axis))
        to3 = lambda x, y: origin.Translated(u_axis * x + v_axis * y)
        points = [to2(p) for p in corners3]
        t, radius, side = self.thickness, self.bend_radius, -1.0 if self.flip else 1.0
        directions = []
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            length = math.hypot(x2 - x1, y2 - y1)
            directions.append(((x2 - x1) / length, (y2 - y1) / length, length))
        left = lambda d: (-d[1], d[0])          # linke Normale einer Richtung in der Skizzenebene
        # Je Ecke: Drehwinkel, Seite der Biegemitte, Radius am Linienzug und Abstand der Tangentenpunkte
        bends = []
        for index in range(1, len(points) - 1):
            a, b = directions[index - 1], directions[index]
            turn = math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1])
            if abs(turn) < 1e-9:
                bends.append(None)
                continue
            if abs(abs(turn) - math.pi) < 1e-6:
                raise FeatureError("Ein Linienzug darf nicht in sich zurücklaufen (180°-Ecke)")
            centre_side = 1.0 if turn > 0 else -1.0
            # Liegt das Blech auf der Seite der Biegemitte, ist der Linienzug die Außenseite der Biegung
            on_line = radius + t if centre_side == side else radius
            bends.append({"turn": turn, "centre_side": centre_side, "r": on_line,
                          "tangent": on_line * math.tan(abs(turn) / 2)})
        # gerade Stücke zwischen den Tangentenpunkten
        straight = []
        for index, (dx, dy, length) in enumerate(directions):
            cut_start = bends[index - 1]["tangent"] if index > 0 and bends[index - 1] else 0.0
            cut_end = bends[index]["tangent"] if index < len(bends) and bends[index] else 0.0
            rest = length - cut_start - cut_end
            if rest < -1e-9:
                raise FeatureError("Ein Stück des Linienzugs ist für den Biegeradius zu kurz")
            start = (points[index][0] + dx * cut_start, points[index][1] + dy * cut_start)
            straight.append((start, max(rest, 0.0)))

        def offset(p, d):
            n = left(d)
            return (p[0] + n[0] * side * t, p[1] + n[1] * side * t)

        # Umriss des Profils: Linienzugseite vorwärts, Ende, Gegenseite rückwärts, Anfang
        line_side, other_side = [], []      # Einträge ("line", a, b) bzw. ("arc", a, mitte, b)
        for index, ((sx, sy), length) in enumerate(straight):
            d = directions[index]
            end = (sx + d[0] * length, sy + d[1] * length)
            if length > 1e-9:
                line_side.append(("line", (sx, sy), end))
                other_side.append(("line", offset((sx, sy), d), offset(end, d)))
            if index < len(bends) and bends[index]:
                bend, following = bends[index], directions[index + 1]
                n = left(d)
                centre = (end[0] + n[0] * bend["centre_side"] * bend["r"], end[1] + n[1] * bend["centre_side"] * bend["r"])
                after = straight[index + 1][0]
                for chain, first, last in ((line_side, end, after), (other_side, offset(end, d), offset(after, following))):
                    chord = ((first[0] + last[0]) / 2 - centre[0], (first[1] + last[1]) / 2 - centre[1])
                    size = math.hypot(*chord)
                    reach = math.dist(first, centre)
                    middle = (centre[0] + chord[0] / size * reach, centre[1] + chord[1] / size * reach)
                    chain.append(("arc", first, middle, last))

        def edge(item, backwards=False):
            spots = [to3(*p) for p in item[1:]]
            if backwards:
                spots.reverse()
            if item[0] == "line":
                return BRepBuilderAPI_MakeEdge(*spots).Edge()
            return BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(*spots).Value()).Edge()

        wire = BRepBuilderAPI_MakeWire()
        for item in line_side:
            wire.Add(edge(item))
        last_line = line_side[-1]
        wire.Add(BRepBuilderAPI_MakeEdge(to3(*last_line[-1]), to3(*other_side[-1][-1])).Edge())
        for item in reversed(other_side):
            wire.Add(edge(item, backwards=True))
        wire.Add(BRepBuilderAPI_MakeEdge(to3(*other_side[0][1]), to3(*line_side[0][1])).Edge())
        if not wire.IsDone():
            raise FeatureError("Das Blechprofil ließ sich nicht schließen")
        across = normal * (-self.width if self.flip_width else self.width)
        solid = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire.Wire()).Face(), across).Shape()

        # Aufbau für die Abwicklung: Platten mit ihrer Lage im Flachen, flache Biegestreifen, Biegelinien
        d0 = directions[0]
        flat_dir = u_axis * d0[0] + v_axis * d0[1]
        thick_dir = u_axis * (left(d0)[0] * side) + v_axis * (left(d0)[1] * side)
        flat_start = to3(*straight[0][0])
        plates, strips, bend_lines = [], [], []
        along, total_turn = 0.0, 0.0
        for index, ((sx, sy), length) in enumerate(straight):
            d = directions[index]
            if length > 1e-9:
                a, b = (sx, sy), (sx + d[0] * length, sy + d[1] * length)
                quad = BRepBuilderAPI_MakePolygon(to3(*a), to3(*b), to3(*offset(b, d)), to3(*offset(a, d)), True)
                plate = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(quad.Wire()).Face(), across).Shape()
                turn = gp_Trsf()
                turn.SetRotation(gp_Ax1(to3(*a), gp_Dir(normal.XYZ())), -total_turn)
                move = gp_Trsf()
                move.SetTranslation(gp_Vec(to3(*a), flat_start.Translated(flat_dir * along)))
                plates.append((plate, move.Multiplied(turn)))
                along += length
            if index < len(bends) and bends[index]:
                bend = bends[index]
                allowance = abs(bend["turn"]) * (radius + self.k_factor * t)
                corner = flat_start.Translated(flat_dir * along)
                quad = BRepBuilderAPI_MakePolygon(corner, corner.Translated(flat_dir * allowance),
                                                  corner.Translated(flat_dir * allowance + thick_dir * t),
                                                  corner.Translated(thick_dir * t), True)
                strips.append((BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(quad.Wire()).Face(), across).Shape(),
                               gp_Trsf()))
                middle = corner.Translated(flat_dir * (allowance / 2))
                line = tuple((p.X(), p.Y(), p.Z()) for p in (middle, middle.Translated(across)))
                bend_lines.append((line, gp_Trsf(), math.degrees(abs(bend["turn"]))))
                along += allowance
                total_turn += bend["turn"]
        name = state.add_body(solid)
        sheet_frame = tuple((v.X(), v.Y(), v.Z()) for v in (flat_start, gp_Dir(thick_dir.XYZ()), gp_Dir(flat_dir.XYZ())))
        state.sheets[name] = {**_new_sheet(t, radius, self.k_factor, plates[0][0], sheet_frame),
                              "plates": plates, "strips": strips, "bends": bend_lines, "folded": solid}


@dataclass
class Rip(Feature):
    """Aufreißen (Fusion: Aufreißen): ein Blechteil wird an einer äußeren Kante zwischen zwei Wänden mit einem
    Spalt aufgetrennt (z. B. ein geschlossener Kasten, damit er sich abwickeln lässt). Die erste der beiden Wände an
    der Kante wird um die Blechdicke plus Spalt gekürzt."""

    body: str = ALL_BODIES
    edge_points: list = field(default_factory=list)
    gap: float = 0.5

    KIND = "Aufreißen"

    def apply(self, state: BuildState) -> None:
        from .analysis import face_normal
        from .sheetmetal import _edge_faces
        body = owner_of(state, self.body, self.edge_points, edges=True)    # „Alle“: Blech mit der Kante
        if body not in state.bodies:
            raise ft.missing_body(body)
        if body not in state.sheets:
            raise FeatureError("Aufreißen geht nur an Blechteilen")
        if not self.edge_points:
            raise FeatureError("Bitte die Kante zwischen zwei Blechwänden anklicken")
        if self.gap <= 0:
            raise FeatureError("Der Spalt muss größer als 0 sein")
        thickness = state.sheets[body]["thickness"]
        shape = state.bodies[body]
        for found in ft.find_by_points(ft.unique_edges(shape), ft.edge_point, self.edge_points, "Kante"):
            edge = TopoDS.Edge(found)
            curve = BRepAdaptor_Curve(edge)
            if curve.GetType() != GeomAbs_Line:
                raise FeatureError("Aufreißen geht an geraden Kanten")
            faces = _edge_faces(shape, edge)
            if len(faces) != 2:
                raise FeatureError("An der Kante müssen genau zwei Wände anliegen")
            first, second = (gp_Vec(*face_normal(face)) for face in faces)
            if first.Crossed(second).Magnitude() < 1e-6:
                raise FeatureError("Die beiden Flächen an der Kante sind parallel – das ist keine Ecke")
            start, end = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
            probe = start.Translated(gp_Vec(start, end) * 0.5 + (first + second) * (-thickness * 0.25))
            if BRepClass3d_SolidClassifier(shape, probe, 1e-7).State() != TopAbs_IN:
                raise FeatureError("Bitte die äußere Kante der Ecke wählen")
            along = gp_Vec(start, end)
            along.Normalize()
            begin = start.Translated(along * -1e-3)
            # Querschnitt: Eckquadrat plus Spalt in die erste Wand hinein
            a, b = first * -thickness, second * -(thickness + self.gap)
            polygon = BRepBuilderAPI_MakePolygon(begin, begin.Translated(a), begin.Translated(a + b),
                                                 begin.Translated(b), True)
            slot = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(polygon.Wire()).Face(),
                                         along * (start.Distance(end) + 2e-3)).Shape()
            shape = ft._boolean(BRepAlgoAPI_Cut, shape, slot)
        state.bodies[body] = shape


# ------------------------------------------------------------------------------ Konstruieren ---

@dataclass
class PlaneAlongPath(Feature):
    """Ebene entlang Pfad (Fusion: Ebene entlang Pfad): senkrecht zum Pfad einer Skizze an der Stelle `position`
    (0 = Anfang, 1 = Ende, gemessen entlang der Länge)."""

    path: str = ""
    position: float = 0.5

    KIND = "Ebene entlang Pfad"

    def apply(self, state: BuildState) -> None:
        from .solids import _path_wire
        if not 0.0 <= self.position <= 1.0:
            raise FeatureError("Die Stelle liegt zwischen 0 (Anfang) und 1 (Ende)")
        curve = BRepAdaptor_CompCurve(TopoDS.Wire(_path_wire(state, self.path)))
        length = GCPnts_AbscissaPoint.Length_s(curve)
        if length < 1e-9:
            raise FeatureError("Der Pfad hat keine Länge")
        parameter = GCPnts_AbscissaPoint(curve, length * self.position, curve.FirstParameter()).Parameter()
        point, tangent = gp_Pnt(), gp_Vec()
        curve.D1(parameter, point, tangent)
        if tangent.Magnitude() < 1e-12:
            raise FeatureError("An dieser Stelle hat der Pfad keine Richtung")
        state.planes[self.name] = gp_Ax2(point, gp_Dir(tangent.XYZ()))


@dataclass
class AxisNormalToFace(Feature):
    """Achse senkrecht zur Fläche (Fusion: Achse senkrecht zur Fläche im Punkt): durch die angeklickte Stelle,
    entlang der Flächennormale dort."""

    body: str = ""
    point: list = field(default_factory=list)       # angeklickte Stelle [x, y, z]

    KIND = "Achse senkrecht zur Fläche"

    def apply(self, state: BuildState) -> None:
        from .extras import face_normal_at
        if self.body not in state.bodies:
            raise ft.missing_body(self.body) if self.body else FeatureError("Bitte eine Stelle auf einer Fläche anklicken")
        if len(self.point) != 3:
            raise FeatureError("Bitte eine Stelle auf einer Fläche anklicken")
        face = ft.nearest_face(state.bodies[self.body], self.point)
        if face is None:
            raise FeatureError("An dieser Stelle liegt keine Fläche mehr")
        found = face_normal_at(face, self.point)
        state.axes[self.name] = (tuple(found[:3]), tuple(found[3:]))


# ----------------------------------------------------------------------------- Zusammenfügen ---

@dataclass
class JointOrigin(Feature):
    """Gelenkursprung (Fusion: Gelenkursprung): ein fester Bezug (Punkt und Richtung) an einem Körper, z. B. an
    einer Stelle ohne passende Fläche. Er wandert mit dem Körper; ein Gelenk kann ihn statt einer Fläche nutzen."""

    body: str = ""
    point: list = field(default_factory=list)       # [x, y, z, nx, ny, nz]
    offset: float = 0.0                             # entlang der Richtung verschoben
    flip: bool = False

    KIND = "Gelenkursprung"

    def apply(self, state: BuildState) -> None:
        if self.body not in state.bodies:
            raise ft.missing_body(self.body) if self.body else FeatureError("Bitte eine Stelle am Körper anklicken")
        if len(self.point) != 6:
            raise FeatureError("Bitte eine Stelle am Körper anklicken")
        direction = gp_Vec(*self.point[3:])
        if direction.Magnitude() < 1e-12:
            raise FeatureError("Die Richtung des Gelenkursprungs fehlt")
        direction.Normalize()
        if self.flip:
            direction.Reverse()
        place = gp_Pnt(*self.point[:3]).Translated(direction * self.offset)
        state.joint_origins[self.name] = {"body": self.body, "point": (place.X(), place.Y(), place.Z()),
                                          "normal": (direction.X(), direction.Y(), direction.Z())}


@dataclass
class RigidGroup(Feature):
    """Starre Gruppe (Fusion: Starre Gruppe): die Körper bewegen sich ab hier gemeinsam (Gelenke, Verschieben)."""

    bodies: list = field(default_factory=list)

    KIND = "Starre Gruppe"

    def apply(self, state: BuildState) -> None:
        missing = [name for name in self.bodies if name not in state.bodies]
        if missing:
            raise ft.missing_body(missing[0])
        if len(set(self.bodies)) < 2:
            raise FeatureError("Für eine starre Gruppe mindestens zwei Körper anklicken")
        state.rigid_groups[self.name] = list(dict.fromkeys(self.bodies))


# ----------------------------------------------------------------------------------- Einfügen ---

@dataclass
class Canvas(Feature):
    """Leinwand (Fusion: Leinwand): ein Bild als halbdurchsichtige Vorlage auf einer Ebene, zum Nachzeichnen. Das
    Bild wird im Entwurf gespeichert (PNG, Base64); es erzeugt keine Geometrie.

    Seit Runde 81 auch auf einer ebenen Körperfläche (`frame`, wie bei Bohrung und Skizze): Mitte, Drehung und
    Breite gelten dann in dieser Fläche, `offset` hebt das Bild von ihr ab.
    """

    image: str = ""                 # PNG-Daten als Base64
    aspect: float = 1.0             # Höhe / Breite des Bildes
    plane: str = "XY"
    offset: float = 0.0
    center_x: float = 0.0
    center_y: float = 0.0
    width: float = 100.0
    angle: float = 0.0              # Drehung in der Ebene (°)
    opacity: float = 0.5
    flip: bool = False              # spiegeln (Bild von der Rückseite)
    frame: list = field(default_factory=list)   # gewählte ebene Körperfläche (Ursprung, Normale, x-Richtung)

    KIND = "Leinwand"

    def parameters(self) -> list:
        """Bilddaten und Seitenverhältnis stehen nicht im Eigenschaftenfeld (sie kommen aus der Bilddatei)."""
        return [item for item in super().parameters() if item.name not in ("image", "aspect")]

    def apply(self, state: BuildState) -> None:
        if not self.image:
            raise FeatureError("Die Leinwand hat kein Bild (bitte über „Leinwand“ eine Bilddatei wählen)")
        if self.width <= 0 or self.aspect <= 0:
            raise FeatureError("Die Breite muss größer als 0 sein")
        if not 0.0 <= self.opacity <= 1.0:
            raise FeatureError("Die Deckkraft liegt zwischen 0 und 1")
        from .sketch import frame_axes
        axes = frame_axes(self.plane, self.offset, self.frame)     # Hauptebene oder gewählte Körperfläche
        x_dir, y_dir = gp_Vec(axes.XDirection().XYZ()), gp_Vec(axes.YDirection().XYZ())
        cos, sin = math.cos(math.radians(self.angle)), math.sin(math.radians(self.angle))
        across, up = x_dir * cos + y_dir * sin, y_dir * cos - x_dir * sin
        if self.flip:
            across.Reverse()
        centre = axes.Location().Translated(x_dir * self.center_x + y_dir * self.center_y)
        half_w, half_h = across * (self.width / 2), up * (self.width * self.aspect / 2)
        # Ecken in der Reihenfolge links unten, rechts unten, rechts oben, links oben (Bildkoordinaten)
        corners = [centre.Translated(-half_w - half_h), centre.Translated(half_w - half_h),
                   centre.Translated(half_w + half_h), centre.Translated(half_h - half_w)]
        state.canvases[self.name] = {"corners": [(p.X(), p.Y(), p.Z()) for p in corners], "image": self.image,
                                     "opacity": float(self.opacity)}


# In das Verzeichnis für das Laden gespeicherter Entwürfe eintragen
for _feature_class in (Emboss, Web, ContourFlange, Rip, PlaneAlongPath, AxisNormalToFace, JointOrigin, RigidGroup,
                       Canvas):
    ft.FEATURE_CLASSES[_feature_class.KIND] = _feature_class
