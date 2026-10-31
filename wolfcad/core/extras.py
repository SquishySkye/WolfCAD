# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Weitere Werkzeuge aus Fusion 360, die bisher fehlten (Abgleich Runde 57).

- Erstellen: Torus, Spule (Schraubenfeder), Rohr (Kreisquerschnitt entlang einer Skizze, auch hohl)
- Konstruieren: Konstruktionspunkt
- Ändern: Fläche teilen (an einer Ebene), Ausrichten (Körper Fläche an Fläche)
- Fläche: Regelfläche (gerade Verbindung zweier Kurvenzüge), Normale umkehren
- Netz: Ebenenschnitt, Trennen (in zusammenhängende Teile), Kombinieren, Normalen umkehren
- Blech: Saum (um 180° umgelegter Rand)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.BRepLib import BRepLib
from OCP.BRepPrimAPI import BRepPrimAPI_MakeTorus
from OCP.Geom import Geom_CylindricalSurface
from OCP.Geom2d import Geom2d_Line
from OCP.gp import gp_Ax1, gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Dir2d, gp_Pln, gp_Pnt, gp_Pnt2d, gp_Trsf, gp_Vec

from . import features as ft
from .features import ALL_BODIES, OPERATIONS, BuildState, Feature, FeatureError

AXIS_PLANES = ("XY", "XZ", "YZ")        # Achse des Torus/der Spule steht senkrecht auf dieser Hauptebene


def _frame(plane: str, x: float, y: float, z: float) -> gp_Ax2:
    """Lage mit Ursprung (x, y, z) und der Normale der Hauptebene als Achse."""
    if plane not in ft.PLANES:
        raise FeatureError(f"Unbekannte Ebene '{plane}'")
    normal, x_dir = ft.PLANES[plane]
    return gp_Ax2(gp_Pnt(x, y, z), gp_Dir(*normal), gp_Dir(*x_dir))


# --------------------------------------------------------------------------- Erstellen ---

@dataclass
class Torus(Feature):
    """Torus (Ring) um einen Mittelpunkt; die Ringachse steht senkrecht auf der gewählten Hauptebene."""

    plane: str = "XY"
    center_x: float = 0.0
    center_y: float = 0.0
    center_z: float = 0.0
    diameter: float = 40.0          # Durchmesser des Rings (Mitte des Querschnitts)
    section: float = 10.0           # Durchmesser des Querschnitts
    operation: str = OPERATIONS[0]

    KIND = "Torus"

    def tube(self) -> float:
        """Wirksamer Querschnitt: 0 heißt „automatisch ein Viertel des Rings“ (Runde 93). So zeigt schon der erste
        Zug am Ring (Start bei 0) einen Torus, der Querschnitt lässt sich danach am zweiten Pfeil einstellen."""
        return self.section if self.section > 0 else self.diameter / 4

    def apply(self, state: BuildState) -> None:
        section = self.tube()
        if self.diameter <= section or section <= 0:
            raise FeatureError("Der Ringdurchmesser muss größer als der Querschnitt sein")
        frame = _frame(self.plane, self.center_x, self.center_y, self.center_z)
        solid = BRepPrimAPI_MakeTorus(frame, self.diameter / 2, section / 2).Shape()
        ft._combine(state, solid, self.operation)


# Querschnitte von Spule und Rohr wie Fusion (Runde 96); Dreieck außen/innen: Spitze zeigt von der Achse weg bzw. hin
SECTIONS = ("Kreis", "Quadrat", "Dreieck (außen)", "Dreieck (innen)")
PIPE_SECTIONS = ("Kreis", "Quadrat", "Dreieck")
SECTION_PLACES = ("Mitte", "Innen", "Außen")     # Lage des Spulenquerschnitts zum Windungsdurchmesser
COIL_TYPES = ("Windungen und Steigung", "Windungen und Höhe", "Höhe und Steigung", "Spirale")
COIL_FIELDS = {"turns": COIL_TYPES[:2] + COIL_TYPES[3:], "pitch": (COIL_TYPES[0], COIL_TYPES[2], COIL_TYPES[3]),
               "height": COIL_TYPES[1:3]}


def section_wire(kind: str, centre: gp_Pnt, normal: gp_Dir, radial: gp_Dir, size: float):
    """Geschlossener Querschnitt der Größe `size` (Durchmesser bzw. Kantenlänge) um `centre`, in der Ebene
    senkrecht zu `normal`; `radial` gibt die Richtung „nach außen“ an (für Quadrat und Dreieck)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    if kind == "Kreis":
        return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(centre, normal), size / 2)).Edge()).Wire()
    u = gp_Vec(radial.XYZ())
    u = u - gp_Vec(normal.XYZ()) * u.Dot(gp_Vec(normal.XYZ()))
    if u.Magnitude() < 1e-9:
        u = gp_Vec(gp_Ax2(centre, normal).XDirection().XYZ())
    u.Normalize()
    v = gp_Vec(normal.XYZ()).Crossed(u)
    at = lambda a, b: centre.Translated(u * a + v * b)
    half = size / 2
    if kind == "Quadrat":
        corners = [at(-half, -half), at(half, -half), at(half, half), at(-half, half)]
    elif kind in ("Dreieck", "Dreieck (außen)", "Dreieck (innen)"):
        # gleichseitiges Dreieck mit Kantenlänge `size`, Schwerpunkt in der Mitte, Spitze nach außen bzw. innen
        sign = -1.0 if kind == "Dreieck (innen)" else 1.0
        high = size * math.sqrt(3) / 2
        corners = [at(sign * 2 * high / 3, 0.0), at(-sign * high / 3, half), at(-sign * high / 3, -half)]
    else:
        raise FeatureError(f"Unbekannter Querschnitt '{kind}'")
    polygon = BRepBuilderAPI_MakePolygon()
    for corner in corners:
        polygon.Add(corner)
    polygon.Close()
    return polygon.Wire()


def spiral_wire(frame: gp_Ax2, radius: float, pitch: float, turns: float):
    """Flache Spirale (Fusion: Spule „Spirale“): beginnt beim Radius und wächst je Windung um die Steigung."""
    from OCP.GeomAPI import GeomAPI_PointsToBSpline
    from OCP.OCP.collections import Array1_gp_Pnt as TColgp_Array1OfPnt
    count = max(16, int(turns * 48))
    points = TColgp_Array1OfPnt(1, count + 1)
    origin, x_dir, y_dir = frame.Location(), gp_Vec(frame.XDirection().XYZ()), gp_Vec(frame.YDirection().XYZ())
    for number in range(count + 1):
        angle = 2 * math.pi * turns * number / count
        r = radius + pitch * angle / (2 * math.pi)
        points.SetValue(number + 1, origin.Translated(x_dir * (r * math.cos(angle)) + y_dir * (r * math.sin(angle))))
    curve = GeomAPI_PointsToBSpline(points, 3, 8).Curve()
    return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(curve).Edge()).Wire()


def helix_wire(frame: gp_Ax2, radius: float, pitch: float, turns: float):
    """Schraubenlinie auf einem Zylinder um die Achse von `frame` (Anfang bei Winkel 0 in x-Richtung)."""
    surface = Geom_CylindricalSurface(gp_Ax3(frame), radius)
    # in der Abwicklung des Zylinders (Winkel, Höhe) ist die Schraubenlinie eine Gerade
    direction = gp_Dir2d(2 * math.pi, pitch)
    length = turns * math.hypot(2 * math.pi, pitch)
    edge = BRepBuilderAPI_MakeEdge(Geom2d_Line(gp_Pnt2d(0.0, 0.0), direction), surface, 0.0, length).Edge()
    wire = BRepBuilderAPI_MakeWire(edge).Wire()
    BRepLib.BuildCurves3d_s(wire)
    return wire


@dataclass
class Coil(Feature):
    """Spule (Fusion: Spule): Kreisquerschnitt entlang einer Schraubenlinie – z. B. eine Feder."""

    plane: str = "XY"
    center_x: float = 0.0
    center_y: float = 0.0
    center_z: float = 0.0
    diameter: float = 30.0          # Windungsdurchmesser (Mitte des Drahts)
    pitch: float = 8.0              # Steigung je Windung
    turns: float = 5.0
    section: float = 3.0            # Drahtdurchmesser
    operation: str = OPERATIONS[0]
    # --- seit Runde 96 (Fusion-Funktionen) ---
    coil_type: str = COIL_TYPES[0]
    height: float = 40.0            # Höhe der Spule (bei „… und Höhe“ / „Höhe und Steigung“)
    section_type: str = SECTIONS[0]
    section_place: str = SECTION_PLACES[0]

    KIND = "Spule"

    def geometry(self) -> tuple[float, float]:
        """(Steigung, Windungen) nach der Art der Spule."""
        if self.coil_type not in COIL_TYPES:
            raise FeatureError(f"Unbekannte Art '{self.coil_type}'")
        if self.coil_type == COIL_TYPES[1]:
            if self.turns <= 0:
                raise FeatureError("Mindestens ein Stück Windung")
            return self.height / self.turns, self.turns
        if self.coil_type == COIL_TYPES[2]:
            if self.pitch <= 0:
                raise FeatureError("Die Steigung muss größer als 0 sein")
            return self.pitch, self.height / self.pitch
        return self.pitch, self.turns

    def apply(self, state: BuildState) -> None:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
        pitch, turns = self.geometry()
        if self.section <= 0 or self.diameter <= self.section or turns <= 0:
            raise FeatureError("Windungsdurchmesser > Drahtdurchmesser > 0 und mindestens ein Stück Windung")
        if pitch <= self.section:
            raise FeatureError("Die Steigung muss größer als der Drahtdurchmesser sein (sonst berühren sich die Windungen)")
        if self.section_type not in SECTIONS or self.section_place not in SECTION_PLACES:
            raise FeatureError("Unbekannter Querschnitt bzw. unbekannte Lage")
        frame = _frame(self.plane, self.center_x, self.center_y, self.center_z)
        # Lage des Querschnitts: mittig auf dem Windungsdurchmesser, ganz innerhalb oder ganz außerhalb
        radius = self.diameter / 2 + {"Mitte": 0.0, "Innen": -self.section / 2, "Außen": self.section / 2}[
            self.section_place]
        spiral = self.coil_type == COIL_TYPES[3]
        spine = spiral_wire(frame, radius, pitch, turns) if spiral else helix_wire(frame, radius, pitch, turns)
        # Querschnitt am Anfang der Kurve, senkrecht zu ihrer Richtung
        start = gp_Pnt(frame.Location().XYZ() + gp_Vec(frame.XDirection()).XYZ() * radius)
        tangent = gp_Vec(frame.YDirection()) * (2 * math.pi * radius) + \
            (gp_Vec(frame.XDirection()) * pitch if spiral else gp_Vec(frame.Direction()) * pitch)
        profile = section_wire(self.section_type, start, gp_Dir(tangent), frame.XDirection(), self.section)
        pipe = BRepOffsetAPI_MakePipeShell(spine)
        pipe.SetMode(frame.Direction())         # feste Bezugsrichtung: der Querschnitt verdreht sich nicht
        pipe.Add(profile)
        pipe.Build()
        if not pipe.IsDone() or not pipe.MakeSolid():
            raise FeatureError("Die Spule ließ sich nicht bilden")
        ft._combine(state, pipe.Shape(), self.operation)


@dataclass
class Pipe(Feature):
    """Rohr (Fusion: Rohr): Kreisquerschnitt entlang der Kurven einer Skizze; mit Wandstärke hohl."""

    path: str = ""
    diameter: float = 10.0
    wall: float = 0.0               # 0 = voll
    operation: str = OPERATIONS[0]
    section_type: str = PIPE_SECTIONS[0]        # Querschnitt wie Fusion: Kreis, Quadrat oder Dreieck (Runde 96)

    KIND = "Rohr"

    def apply(self, state: BuildState) -> None:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        from OCP.BRepBuilderAPI import BRepBuilderAPI_RightCorner
        from .solids import _path_wire
        if self.diameter <= 0 or self.wall < 0 or 2 * self.wall >= self.diameter:
            raise FeatureError("Durchmesser > 0 und Wandstärke kleiner als der halbe Durchmesser")
        if self.section_type not in PIPE_SECTIONS:
            raise FeatureError(f"Unbekannter Querschnitt '{self.section_type}'")
        spine = _path_wire(state, self.path)
        curve = BRepAdaptor_CompCurve(spine)
        start, tangent = gp_Pnt(), gp_Vec()
        curve.D1(curve.FirstParameter(), start, tangent)

        across = gp_Ax2(start, gp_Dir(tangent)).XDirection()

        def swept(radius: float):
            pipe = BRepOffsetAPI_MakePipeShell(spine)
            # an Knicken des Pfads treffen sich die Stücke auf Gehrung (sonst endet das Rohr am ersten Knick)
            pipe.SetTransitionMode(BRepBuilderAPI_RightCorner)
            # Größe: Durchmesser beim Kreis, Kantenlänge bei Quadrat und Dreieck
            pipe.Add(section_wire(self.section_type, start, gp_Dir(tangent), across, 2 * radius))
            pipe.Build()
            if not pipe.IsDone() or not pipe.MakeSolid():
                raise FeatureError("Das Rohr ließ sich entlang dieses Pfads nicht bilden (zu enge Kurve?)")
            return pipe.Shape()

        solid = swept(self.diameter / 2)
        if self.wall > 0:
            solid = ft._boolean(BRepAlgoAPI_Cut, solid, swept(self.diameter / 2 - self.wall))
        ft._combine(state, solid, self.operation)


# ------------------------------------------------------------------------- Konstruieren ---

@dataclass
class ConstructionPoint(Feature):
    """Konstruktionspunkt (Fusion: Punkt): eine feste Stelle im Raum – z. B. an einer Ecke, in der Mitte eines
    Kreises oder frei eingegeben. Er erzeugt keine Geometrie; Skizzen und Messen können ihn fangen."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    KIND = "Punkt"

    def apply(self, state: BuildState) -> None:
        state.points[self.name] = (float(self.x), float(self.y), float(self.z))


# ------------------------------------------------------------------------------- Ändern ---

@dataclass
class SplitFace(Feature):
    """Fläche teilen (Fusion: Fläche teilen): die gewählten Flächen werden an einer Ebene in zwei Flächen
    geteilt; der Körper bleibt ein Körper (z. B. um nur einen Teil einzufärben oder zu verschieben)."""

    body: str = ALL_BODIES
    face_points: list = field(default_factory=list)
    plane: str = "XY"
    offset: float = 0.0

    KIND = "Fläche teilen"

    def apply(self, state: BuildState) -> None:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
        from OCP.BRepFeat import BRepFeat_SplitShape
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        if not self.face_points:
            raise FeatureError("Vor dem Werkzeug die Flächen anklicken, die geteilt werden sollen")
        axes = ft._plane_axes(self.plane, self.offset)
        cutter = BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3(axes)), -1e5, 1e5, -1e5, 1e5).Face()
        changed = False
        for name in ft.select_bodies(state, self.body):
            body = state.bodies[name]
            faces = ft.find_by_points(ft.faces_of(body), ft.face_point, self.face_points, "Fläche")
            splitter = BRepFeat_SplitShape(body)
            count = 0
            for face in faces:
                section = BRepAlgoAPI_Section(face, cutter)
                section.Build()
                explorer = TopExp_Explorer(section.Shape(), TopAbs_EDGE)
                while explorer.More():
                    splitter.Add(TopoDS.Edge(explorer.Current()), TopoDS.Face(face))
                    count += 1
                    explorer.Next()
            if not count:
                continue
            splitter.Build()
            if not splitter.IsDone():
                raise FeatureError("Die Fläche ließ sich an dieser Ebene nicht teilen")
            state.bodies[name] = splitter.Shape()
            changed = True
        if not changed:
            raise FeatureError("Die Ebene schneidet keine der gewählten Flächen")


def face_normal_at(face, point) -> list:
    """Punkt und nach außen zeigende Normale einer Körperfläche an der angeklickten Stelle: [x, y, z, nx, ny, nz]
    (für Ausrichten; beachtet die Ausrichtung der Fläche im Körper, auch bei gekrümmten Flächen)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.TopoDS import TopoDS
    face = TopoDS.Face(face)
    uv = ShapeAnalysis_Surface(BRep_Tool.Surface_s(face)).ValueOfUV(gp_Pnt(*point), 1e-6)
    where, normal = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal(uv.X(), uv.Y(), where, normal)
    if normal.Magnitude() < 1e-12:
        raise FeatureError("An dieser Stelle hat die Fläche keine eindeutige Normale")
    normal.Normalize()
    return [where.X(), where.Y(), where.Z(), normal.X(), normal.Y(), normal.Z()]


@dataclass
class Align(Feature):
    """Ausrichten (Fusion: Ausrichten): der Körper wird so gedreht und verschoben, dass seine gewählte Fläche
    (Punkt + Normale) auf der Zielfläche liegt – Flächen gegeneinander („Rücken an Rücken“), mit Umkehren gleich
    ausgerichtet."""

    body: str = ""
    source: list = field(default_factory=list)      # [x, y, z, nx, ny, nz] auf dem Körper
    target: list = field(default_factory=list)      # [x, y, z, nx, ny, nz] auf dem Ziel
    flip: bool = False

    KIND = "Ausrichten"

    def apply(self, state: BuildState) -> None:
        if self.body not in state.bodies:
            raise ft.missing_body(self.body)
        if len(self.source) != 6 or len(self.target) != 6:
            raise FeatureError("Erst die Fläche am Körper, dann die Zielfläche anklicken")
        a_point, a_normal = gp_Pnt(*self.source[:3]), gp_Vec(*self.source[3:])
        b_point, b_normal = gp_Pnt(*self.target[:3]), gp_Vec(*self.target[3:])
        wanted = b_normal if self.flip else b_normal.Reversed()     # Rücken an Rücken: Normalen entgegen
        turn = gp_Trsf()
        axis = a_normal.Crossed(wanted)
        angle = a_normal.Angle(wanted)
        if axis.Magnitude() > 1e-12:
            turn.SetRotation(gp_Ax1(a_point, gp_Dir(axis)), angle)
        elif angle > 1.0:                       # genau entgegengesetzt: um eine beliebige Querachse wenden
            side = gp_Vec(1, 0, 0) if abs(a_normal.X()) < 0.9 else gp_Vec(0, 1, 0)
            turn.SetRotation(gp_Ax1(a_point, gp_Dir(a_normal.Crossed(side))), math.pi)
        shift = gp_Trsf()
        shift.SetTranslation(gp_Vec(a_point, b_point))
        state.move_body(self.body, shift.Multiplied(turn))


# ------------------------------------------------------------------------------- Fläche ---

@dataclass
class RuledSurface(Feature):
    """Regelfläche (Fusion: Regelfläche): verbindet zwei Kurvenzüge Punkt für Punkt mit geraden Linien."""

    sketch: str = ""
    sketch2: str = ""

    KIND = "Regelfläche"

    def apply(self, state: BuildState) -> None:
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
        from .solids import _path_wire
        if not self.sketch or not self.sketch2 or self.sketch == self.sketch2:
            raise FeatureError("Bitte zwei verschiedene Skizzen wählen")
        ruled = BRepOffsetAPI_ThruSections(False, True)         # Fläche, gerade Verbindungen
        ruled.AddWire(_path_wire(state, self.sketch))
        ruled.AddWire(_path_wire(state, self.sketch2))
        ruled.Build()
        if not ruled.IsDone():
            raise FeatureError("Die beiden Kurvenzüge lassen sich nicht verbinden (beide offen oder beide geschlossen)")
        state.add_surface(ruled.Shape())


@dataclass
class ReverseNormal(Feature):
    """Normale umkehren (Fusion: Normale umkehren): Vorder- und Rückseite des Flächenkörpers tauschen
    (wichtig z. B. für die Richtung von Aufdicken und Versatz)."""

    surface: str = ""

    KIND = "Normale umkehren"

    def apply(self, state: BuildState) -> None:
        if self.surface not in state.surfaces:
            raise FeatureError("Bitte einen Flächenkörper wählen")
        state.surfaces[self.surface] = state.surfaces[self.surface].Reversed()


# --------------------------------------------------------------------------------- Netz ---

def cut_mesh(triangles: np.ndarray, origin: np.ndarray, normal: np.ndarray) -> np.ndarray:
    """Teil des Netzes auf der Seite gegen die Normale; geschnittene Dreiecke werden an der Ebene beschnitten.

    Ganz mit numpy gerechnet (je Fall auf einmal): ein Eckpunkt innen ergibt ein kleineres Dreieck, zwei Eckpunkte
    innen ein Viereck aus zwei Dreiecken. Die Umlaufrichtung bleibt erhalten.
    """
    triangles = np.asarray(triangles, dtype=float).reshape(-1, 3, 3)
    side = (triangles - origin) @ normal                 # > 0: wegschneiden
    inside = side <= 0
    count = inside.sum(axis=1)
    kept = [triangles[count == 3]]
    for number in (1, 2):
        chosen = triangles[count == number]
        if not len(chosen):
            continue
        sides, flags = side[count == number], inside[count == number]
        # so drehen, dass der „besondere“ Eckpunkt vorne steht (bei 1: der innere, bei 2: der äußere)
        special = np.argmax(flags if number == 1 else ~flags, axis=1)
        order = (special[:, None] + np.arange(3)[None, :]) % 3
        rows = np.arange(len(chosen))[:, None]
        a, b, c = (chosen[rows, order][:, k] for k in range(3))
        sa, sb, sc = (sides[rows, order][:, k] for k in range(3))
        ab = a + (b - a) * (sa / (sa - sb))[:, None]      # Schnittpunkte auf den Kanten a–b und a–c
        ac = a + (c - a) * (sa / (sa - sc))[:, None]
        if number == 1:
            kept.append(np.stack([a, ab, ac], axis=1))
        else:
            # a liegt außen: übrig bleibt das Viereck ab, b, c, ac
            kept.append(np.stack([ab, b, c], axis=1))
            kept.append(np.stack([ab, c, ac], axis=1))
    return np.concatenate(kept).reshape(-1, 3, 3)


@dataclass
class MeshPlaneCut(Feature):
    """Netz an einer Ebene schneiden (Fusion: Ebenenschnitt); mit „Schließen“ wird die Schnittöffnung gefüllt."""

    mesh: str = ""
    plane: str = "XY"
    offset: float = 0.0
    flip: bool = False              # die andere Seite behalten
    fill: bool = True

    KIND = "Netz schneiden"

    def apply(self, state: BuildState) -> None:
        from .meshes import _mesh_of, repair
        triangles = _mesh_of(state, self.mesh)
        axes = ft._plane_axes(self.plane, self.offset)
        normal = np.array([axes.Direction().X(), axes.Direction().Y(), axes.Direction().Z()])
        origin = np.array([axes.Location().X(), axes.Location().Y(), axes.Location().Z()])
        result = cut_mesh(np.asarray(triangles, dtype=float), origin, -normal if self.flip else normal)
        if not len(result):
            raise FeatureError("Auf dieser Seite der Ebene liegt nichts vom Netz")
        if self.fill:
            result, _report = repair(result)         # die Schnittöffnung ist ein Loch: wird geschlossen
        state.meshes[self.mesh] = result


def components(triangles: np.ndarray) -> list[np.ndarray]:
    """Zusammenhängende Teile eines Netzes (über gemeinsame Ecken)."""
    from .meshes import _indexed
    points, faces = _indexed(np.asarray(triangles, dtype=float))
    parent = list(range(len(points)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b, c in faces:
        parent[root(b)] = root(a)
        parent[root(c)] = root(a)
    groups: dict = {}
    for number, (a, _b, _c) in enumerate(faces):
        groups.setdefault(root(a), []).append(number)
    return [points[faces[numbers]] for numbers in groups.values()]


@dataclass
class MeshSeparate(Feature):
    """Netz trennen (Fusion: Trennen): jeder zusammenhängende Teil wird ein eigener Netzkörper."""

    mesh: str = ""

    KIND = "Netz trennen"

    def apply(self, state: BuildState) -> None:
        from .meshes import _mesh_of
        parts = components(_mesh_of(state, self.mesh))
        if len(parts) < 2:
            raise FeatureError("Das Netz besteht nur aus einem Teil")
        del state.meshes[self.mesh]
        for part in parts:
            state.add_mesh(part)


@dataclass
class MeshCombine(Feature):
    """Netze kombinieren: mehrere Netzkörper werden einer."""

    meshes: list = field(default_factory=list)

    KIND = "Netze kombinieren"

    def apply(self, state: BuildState) -> None:
        from .meshes import _mesh_of
        if len(self.meshes) < 2:
            raise FeatureError("Bitte mindestens zwei Netzkörper anklicken")
        parts = [_mesh_of(state, name) for name in self.meshes]
        for name in self.meshes:
            del state.meshes[name]
        state.add_mesh(np.concatenate(parts))


@dataclass
class MeshReverse(Feature):
    """Normalen umkehren (Netz): Innen und Außen tauschen (z. B. nach einem falsch herum exportierten Scan)."""

    mesh: str = ""

    KIND = "Netznormalen umkehren"

    def apply(self, state: BuildState) -> None:
        from .meshes import _mesh_of
        state.meshes[self.mesh] = np.asarray(_mesh_of(state, self.mesh))[:, [0, 2, 1]]


# --------------------------------------------------------------------------------- Blech ---

@dataclass
class Hem(Feature):
    """Saum (Fusion: Saum): der Blechrand wird um 180° umgelegt; `gap` ist der lichte Abstand zwischen Blech und
    umgelegtem Teil (innerer Biegeradius = halber Abstand)."""

    body: str = ALL_BODIES
    edge_points: list = field(default_factory=list)
    length: float = 6.0
    gap: float = 0.5
    flip: bool = False

    KIND = "Saum"

    def apply(self, state: BuildState) -> None:
        from .sheetmetal import EdgeFlange
        if self.gap <= 0:
            raise FeatureError("Der Abstand muss größer als 0 sein")
        EdgeFlange(name=self.name, body=self.body, edge_points=self.edge_points, length=self.length, angle=180.0,
                   bend_radius=self.gap / 2, flip=self.flip, relief=False).apply(state)


# In das Verzeichnis für das Laden gespeicherter Entwürfe eintragen
for _feature_class in (Torus, Coil, Pipe, ConstructionPoint, SplitFace, Align, RuledSurface, ReverseNormal,
                       MeshPlaneCut, MeshSeparate, MeshCombine, MeshReverse, Hem):
    ft.FEATURE_CLASSES[_feature_class.KIND] = _feature_class

