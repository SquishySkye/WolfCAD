# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Eigene Volumen-Werkzeuge nach dem Vorbild von Fusion 360: Grundkörper, Drehen,
Verschieben, Kombinieren, Spiegeln und Schale.

Alle Längen sind Millimeter, Winkel Grad. Körper werden über ihren Namen gewählt;
"Alle" steht für alle veränderbaren Körper.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from OCP.Bnd import Bnd_Box
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Skin
from OCP.GeomAbs import GeomAbs_Intersection
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_GTransform, BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeSolid,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_RightCorner, BRepBuilderAPI_Transform,
)
from OCP.BRepLib import BRepLib
from OCP.Geom import Geom_CylindricalSurface
from OCP.Geom2d import Geom2d_Line
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepOffsetAPI import (
    BRepOffsetAPI_DraftAngle, BRepOffsetAPI_MakeOffsetShape, BRepOffsetAPI_MakePipeShell,
    BRepOffsetAPI_MakeThickSolid,
    BRepOffsetAPI_ThruSections,
)
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeHalfSpace,
    BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeRevol, BRepPrimAPI_MakeSphere,
)
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Dir2d, gp_GTrsf, gp_Pln, gp_Pnt, gp_Pnt2d, gp_Trsf, gp_Vec, gp_XYZ
from OCP.TopAbs import (
    TopAbs_EDGE, TopAbs_FACE, TopAbs_IN, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID, TopAbs_WIRE,
)
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Iterator, TopoDS_Shape
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.TopExp import TopExp_Explorer
from OCP.collections import List_TopoDS_Shape

from . import exchange
from . import features as ft
from . import sketch
from .features import ALL_BODIES, OPERATIONS, BuildState, Feature, FeatureError

AXES = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}
REVOLVE_AXES = ("X-Achse der Skizze", "Y-Achse der Skizze")
NO_SKETCH = "(keine)"       # Auswahl für eine nicht benutzte, wahlfreie Skizze
COMBINE_OPERATIONS = ("Vereinigen", "Ausschneiden", "Schnittmenge")
MOVE_PIVOTS = ("Körpermitte", "Ursprung")      # Drehpunkt beim Verschieben (Drehen und Skalieren)
_PATTERN_LIMIT = 500    # größte Zahl von Exemplaren eines Musters
# Offene Seite einer Schale: Bezeichnung -> Richtung, in der die entfernte Fläche liegt
OPENINGS = {
    "Oben (+Z)": (0, 0, 1), "Unten (−Z)": (0, 0, -1), "Rechts (+X)": (1, 0, 0),
    "Links (−X)": (-1, 0, 0), "Hinten (+Y)": (0, 1, 0), "Vorne (−Y)": (0, -1, 0),
}


def _in_plane(axes: gp_Ax2, x: float, y: float) -> gp_Pnt:
    """Punkt mit den Ebenenkoordinaten (x, y) im Raum."""
    point = (gp_Vec(axes.Location().XYZ()) + gp_Vec(axes.XDirection().XYZ()) * x
             + gp_Vec(axes.YDirection().XYZ()) * y)
    return gp_Pnt(point.XYZ())


@dataclass
class Box(Feature):
    """Quader auf einer Ebene, mittig um (center_x, center_y)."""

    plane: str = "XY"
    offset: float = 0.0
    center_x: float = 0.0
    center_y: float = 0.0
    length: float = 40.0
    width: float = 20.0
    height: float = 10.0
    operation: str = OPERATIONS[0]
    frame: list = field(default_factory=list)   # Lage der Körperfläche, auf der er aufgezogen wurde (statt Hauptebene)
    base: str = ft.NO_BASE      # Konstruktionsebene, auf der er steht – er wandert mit, wenn sie sich ändert

    KIND = "Quader"

    def axes(self, state: BuildState) -> gp_Ax2:
        """Die Ebene, auf der der Quader steht: Hauptebene, gemerkte Körperfläche oder Konstruktionsebene."""
        return sketch.placed_axes(state, self.plane, self.offset, self.frame, self.base)

    def apply(self, state: BuildState) -> None:
        if self.length <= 0 or self.width <= 0 or abs(self.height) < 1e-9:
            raise FeatureError("Länge und Breite müssen größer als 0 sein, die Höhe ungleich 0")
        axes = self.axes(state)
        corner = _in_plane(axes, self.center_x - self.length / 2, self.center_y - self.width / 2)
        if self.height < 0:     # negative Höhe: der Quader wächst unter die Ebene
            corner = corner.Translated(gp_Vec(axes.Direction().XYZ()) * self.height)
        frame = gp_Ax2(corner, axes.Direction(), axes.XDirection())
        solid = BRepPrimAPI_MakeBox(frame, self.length, self.width, abs(self.height)).Shape()
        ft._combine(state, solid, self.operation)


@dataclass
class Cylinder(Feature):
    """Zylinder, dessen Grundkreis auf einer Ebene liegt."""

    plane: str = "XY"
    offset: float = 0.0
    center_x: float = 0.0
    center_y: float = 0.0
    diameter: float = 20.0
    height: float = 10.0
    operation: str = OPERATIONS[0]
    frame: list = field(default_factory=list)   # Lage der Körperfläche, auf der er aufgezogen wurde (statt Hauptebene)
    base: str = ft.NO_BASE      # Konstruktionsebene, auf der er steht – er wandert mit, wenn sie sich ändert

    KIND = "Zylinder"

    def axes(self, state: BuildState) -> gp_Ax2:
        """Die Ebene, auf der der Zylinder steht: Hauptebene, gemerkte Körperfläche oder Konstruktionsebene."""
        return sketch.placed_axes(state, self.plane, self.offset, self.frame, self.base)

    def apply(self, state: BuildState) -> None:
        if self.diameter <= 0 or abs(self.height) < 1e-9:
            raise FeatureError("Der Durchmesser muss größer als 0 sein, die Höhe ungleich 0")
        axes = self.axes(state)
        direction = axes.Direction() if self.height > 0 else axes.Direction().Reversed()
        frame = gp_Ax2(_in_plane(axes, self.center_x, self.center_y), direction)
        solid = BRepPrimAPI_MakeCylinder(frame, self.diameter / 2, abs(self.height)).Shape()
        ft._combine(state, solid, self.operation)


@dataclass
class Sphere(Feature):
    """Kugel um einen Mittelpunkt im Raum."""

    center_x: float = 0.0
    center_y: float = 0.0
    center_z: float = 0.0
    diameter: float = 20.0
    operation: str = OPERATIONS[0]
    # Konstruktionsebene, auf der die Kugel sitzt: der Mittelpunkt zählt dann in den Richtungen der Ebene
    # (x, y in der Ebene, z längs ihrer Normale) und wandert mit ihr
    base: str = ft.NO_BASE

    KIND = "Kugel"

    def centre(self, state: BuildState) -> gp_Pnt:
        """Der Mittelpunkt im Raum – frei oder bezogen auf die Konstruktionsebene `base`."""
        if self.base in ("", ft.NO_BASE):
            return gp_Pnt(self.center_x, self.center_y, self.center_z)
        axes = sketch.placed_axes(state, "XY", 0.0, [], self.base)
        return _in_plane(axes, self.center_x, self.center_y).Translated(gp_Vec(axes.Direction().XYZ()) * self.center_z)

    def apply(self, state: BuildState) -> None:
        if self.diameter <= 0:
            raise FeatureError("Der Durchmesser muss größer als 0 sein")
        centre = self.centre(state)
        solid = BRepPrimAPI_MakeSphere(centre, self.diameter / 2).Shape()
        ft._combine(state, solid, self.operation)


@dataclass
class Revolve(Feature):
    """Dreht das Profil einer Skizze um eine Achse der Skizzenebene."""

    sketch: str = ""
    axis: str = REVOLVE_AXES[1]
    angle: float = 360.0
    operation: str = OPERATIONS[0]
    regions: str = ft.PROFILE_MODES[0]
    region_points: list = field(default_factory=list)   # je gewähltem Bereich ein Punkt in seinem Inneren
    extent: str = "Eine Seite"      # wie Fusion: eine Seite, symmetrisch oder zwei Seiten (REVOLVE_EXTENTS)
    angle2: float = 90.0            # Winkel der zweiten Seite (nur „Zwei Seiten“)

    KIND = "Drehen"

    def apply(self, state: BuildState) -> None:
        if self.sketch not in state.profiles or self.sketch not in state.frames:
            raise FeatureError(f"Skizze '{self.sketch}' ist nicht (mehr) vorhanden")
        if not 0 < abs(self.angle) <= 360:
            raise FeatureError("Der Winkel muss zwischen 0 und 360 Grad liegen")
        frame = state.frames[self.sketch]
        if self.axis in state.axes:
            # um eine Konstruktionsachse (z. B. eine Körperkante oder die Achse einer Bohrung)
            point, direction = state.axes[self.axis]
            around = gp_Ax1(gp_Pnt(*point), gp_Dir(*direction))
        elif self.axis in REVOLVE_AXES:
            direction = frame.XDirection() if self.axis == REVOLVE_AXES[0] else frame.YDirection()
            around = gp_Ax1(frame.Location(), direction)
        else:
            raise FeatureError(f"Achse '{self.axis}' ist nicht (mehr) vorhanden")
        if self.extent not in REVOLVE_EXTENTS:
            raise FeatureError(f"Unbekannte Richtung '{self.extent}'")
        profile = ft.profile_of(state, self.sketch, self.regions, self.region_points)
        total, back = self.angle, 0.0
        if self.extent == REVOLVE_EXTENTS[1]:
            back = self.angle / 2                   # symmetrisch: zur Hälfte auf jede Seite
        elif self.extent == REVOLVE_EXTENTS[2]:
            if not 0 < abs(self.angle2) <= 360 or abs(self.angle) + abs(self.angle2) > 360 + 1e-9:
                raise FeatureError("Beide Winkel zusammen dürfen höchstens 360 Grad sein")
            back, total = abs(self.angle2), abs(self.angle) + abs(self.angle2)
        if back:
            # Profil um den rückwärtigen Winkel zurückdrehen, dann in einem Zug über den ganzen Winkel drehen
            turn = gp_Trsf()
            turn.SetRotation(around, -math.radians(back))
            profile = BRepBuilderAPI_Transform(profile, turn, True).Shape()
        maker = BRepPrimAPI_MakeRevol(profile, around, math.radians(total))
        try:
            solid = maker.Shape()
        except Exception as error:
            raise FeatureError("Das Profil darf die Drehachse nicht schneiden") from error
        if not TopExp_Explorer(solid, TopAbs_SOLID).More():
            raise FeatureError("Beim Drehen entstand kein Volumen (Profil liegt auf der Achse?)")
        ft._combine(state, solid, self.operation)


def _outline(state: BuildState, sketch_name: str):
    """Äußerer Umriss des (ersten) Profilbereichs einer Skizze als geschlossener Draht."""
    if sketch_name not in state.profiles:
        raise FeatureError(f"Skizze '{sketch_name}' fehlt oder hat kein geschlossenes Profil")
    faces = ft.faces_of(ft.profile_of(state, sketch_name, ft.PROFILE_MODES[0]))
    if not faces:
        raise FeatureError(f"Skizze '{sketch_name}' hat kein geschlossenes Profil")
    return BRepTools.OuterWire_s(faces[0])


def _inner_wires(state: BuildState, sketch_name: str) -> list:
    """Die Löcher des (ersten) Profilbereichs einer Skizze als geschlossene Drähte."""
    face = ft.faces_of(ft.profile_of(state, sketch_name, ft.PROFILE_MODES[0]))[0]
    outer = BRepTools.OuterWire_s(face)
    wires = []
    explorer = TopExp_Explorer(face, TopAbs_WIRE)
    while explorer.More():
        if not explorer.Current().IsSame(outer):
            wires.append(TopoDS.Wire(explorer.Current()))
        explorer.Next()
    return wires


def _path_wire(state: BuildState, sketch_name: str):
    """Die Kurven einer Skizze als ein zusammenhängender Draht (Pfad für Sweep)."""
    if sketch_name in state.curves:
        source = state.curves[sketch_name]
    elif sketch_name in state.profiles:
        return _outline(state, sketch_name)     # Rechteck-/Kreisskizze: ihr Umriss ist der Pfad
    else:
        raise FeatureError(f"Pfadskizze '{sketch_name}' ist nicht (mehr) vorhanden")
    edges = List_TopoDS_Shape()
    explorer = TopExp_Explorer(source, TopAbs_EDGE)
    while explorer.More():
        edges.Append(explorer.Current())
        explorer.Next()
    maker = BRepBuilderAPI_MakeWire()
    maker.Add(edges)
    if not maker.IsDone():
        raise FeatureError("Der Pfad muss ein zusammenhängender Linienzug ohne Verzweigung sein")
    return maker.Wire()


SWEEP_PLACEMENTS = ("Wie gezeichnet", "An den Pfadanfang setzen")
# Führungsschiene wie in Fusion („Pfad + Führungsschiene“): das Profil wächst/schrumpft so, dass es die Schiene
# berührt, oder es wird nur nach ihr ausgerichtet (gedreht), behält aber seine Größe
GUIDE_MODES = ("Skalieren", "Nur ausrichten")


def _to_path_start(profile, normal: tuple, spine) -> gp_Trsf:
    """Bewegung, die ein Profil mittig an den Anfang des Pfads setzt und quer zu seiner Richtung dreht."""
    properties = GProp_GProps()
    BRepGProp.LinearProperties_s(profile, properties)
    centre = properties.CentreOfMass()
    walker = BRepTools_WireExplorer(TopoDS.Wire(spine))
    curve = BRepAdaptor_Curve(walker.Current())
    reversed_edge = walker.Current().Orientation() == TopAbs_REVERSED
    start, tangent = gp_Pnt(), gp_Vec()
    curve.D1(curve.LastParameter() if reversed_edge else curve.FirstParameter(), start, tangent)
    if reversed_edge:
        tangent.Reverse()
    if tangent.Magnitude() < 1e-12:
        raise FeatureError("Die Richtung des Pfads an seinem Anfang ließ sich nicht bestimmen")
    move = gp_Trsf()
    facing = gp_Vec(*normal)
    axis = facing.Crossed(tangent)
    if axis.Magnitude() > 1e-9:
        # um den Profilmittelpunkt drehen, bis die Profilnormale längs des Pfads zeigt
        move.SetRotation(gp_Ax1(centre, gp_Dir(axis)), facing.Angle(tangent))
    shift = gp_Trsf()
    shift.SetTranslation(gp_Vec(centre, start))
    return shift.Multiplied(move)


def face_middle(face) -> tuple[tuple, tuple]:
    """Ein Punkt mitten auf einer (auch gekrümmten) Fläche und dort die Normale nach außen."""
    surface = BRepAdaptor_Surface(face)
    u = (surface.FirstUParameter() + surface.LastUParameter()) / 2
    v = (surface.FirstVParameter() + surface.LastVParameter()) / 2
    point, along_u, along_v = gp_Pnt(), gp_Vec(), gp_Vec()
    surface.D1(u, v, point, along_u, along_v)
    normal = along_u.Crossed(along_v)
    if normal.Magnitude() < 1e-12:
        raise FeatureError("An dieser Fläche ließ sich keine Richtung bestimmen")
    normal.Normalize()
    if face.Orientation() == TopAbs_REVERSED:
        normal.Reverse()
    return (point.X(), point.Y(), point.Z()), (normal.X(), normal.Y(), normal.Z())


@dataclass
class ConstructionAxis(Feature):
    """Konstruktionsachse: eine Gerade durch zwei Punkte (z. B. eine Körperkante oder die Achse einer Bohrung).

    Sie erzeugt keine Geometrie; Drehen und Kreismuster können sich um sie drehen.
    """

    start: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    end: list = field(default_factory=lambda: [0.0, 0.0, 10.0])

    KIND = "Achse"

    def apply(self, state: BuildState) -> None:
        direction = [b - a for a, b in zip(self.start, self.end)]
        length = math.sqrt(sum(c * c for c in direction))
        if length < 1e-9:
            raise FeatureError("Die beiden Punkte der Achse fallen zusammen")
        state.axes[self.name] = (tuple(float(c) for c in self.start), tuple(c / length for c in direction))


def axis_of_face(face) -> tuple[list, list] | None:
    """Achse einer zylindrischen oder kegeligen Fläche als zwei Punkte (über ihre Länge); None bei anderen Flächen."""
    surface = BRepAdaptor_Surface(face)
    kind = surface.GetType()
    if kind == GeomAbs_Cylinder:
        axis = surface.Cylinder().Axis()
    elif kind == GeomAbs_Cone:
        axis = surface.Cone().Axis()
    else:
        return None
    origin, along = axis.Location(), axis.Direction()
    low, high = surface.FirstVParameter(), surface.LastVParameter()
    if kind == GeomAbs_Cone:
        # beim Kegel misst der Parameter längs der Mantellinie: auf die Achse umrechnen
        factor = math.cos(surface.Cone().SemiAngle())
        low, high = low * factor, high * factor
    point = lambda t: [origin.X() + along.X() * t, origin.Y() + along.Y() * t, origin.Z() + along.Z() * t]
    return point(low), point(high)


@dataclass
class Sweep(Feature):
    """Zieht das Profil einer Skizze entlang des Linienzugs einer zweiten Skizze.

    Das Profil sollte am Anfang des Pfads und quer zu ihm liegen. Ecken im Pfad werden
    scharf ausgeführt. Löcher im Profil ergeben Hohlräume entlang des Pfads (z. B. ein Rohr).
    """

    sketch: str = ""
    path: str = ""
    operation: str = OPERATIONS[0]
    placement: str = SWEEP_PLACEMENTS[0]        # Profil lassen, wo es gezeichnet ist, oder an den Pfadanfang setzen
    guide: str = NO_SKETCH                      # Skizze mit der Führungsschiene (NO_SKETCH: nur Pfad)
    guide_mode: str = GUIDE_MODES[0]

    KIND = "Sweep"

    def apply(self, state: BuildState) -> None:
        if self.sketch == self.path:
            raise FeatureError("Profil und Pfad müssen verschiedene Skizzen sein")
        if self.placement not in SWEEP_PLACEMENTS:
            raise FeatureError(f"Unbekannte Lage des Profils '{self.placement}'")
        if self.guide_mode not in GUIDE_MODES:
            raise FeatureError(f"Unbekannte Art der Führung '{self.guide_mode}'")
        rail = None
        if self.guide not in ("", NO_SKETCH):
            if self.guide in (self.sketch, self.path):
                raise FeatureError("Die Führungsschiene braucht eine eigene Skizze (nicht Profil oder Pfad)")
            rail = _path_wire(state, self.guide)
        profile, spine = _outline(state, self.sketch), _path_wire(state, self.path)
        holes = _inner_wires(state, self.sketch)
        if self.placement == SWEEP_PLACEMENTS[1]:
            # Profil mittig an den Anfang des Pfads setzen und quer zu ihm drehen
            move = _to_path_start(profile, state.normals[self.sketch], spine)
            profile = TopoDS.Wire(BRepBuilderAPI_Transform(profile, move, True).Shape())
            holes = [TopoDS.Wire(BRepBuilderAPI_Transform(hole, move, True).Shape()) for hole in holes]
        solid = self._swept(spine, profile, rail, self.guide_mode)
        if solid is None or not TopExp_Explorer(solid, TopAbs_SOLID).More():
            raise FeatureError("Beim Sweep entstand kein Volumen (Profil zu groß für die Kurven des Pfads?"
                               + (" Berührt das Profil am Anfang die Führungsschiene?)" if rail else ")"))
        # Löcher im Profil: jedes wird ebenfalls entlang des Pfads gezogen und herausgeschnitten
        for hole in holes:
            core = self._swept(spine, hole, rail, self.guide_mode)
            if core is None:
                raise FeatureError("Ein Loch im Profil ließ sich nicht entlang des Pfads ziehen")
            solid = ft._boolean(BRepAlgoAPI_Cut, solid, core)
        ft._combine(state, solid, self.operation)

    @staticmethod
    def _swept(spine, wire, rail=None, mode: str = GUIDE_MODES[0]):
        """Volumen eines geschlossenen Drahts, entlang des Pfads gezogen (None, wenn es nicht gelingt).

        Mit Führungsschiene: beim Skalieren bleibt die Schiene Rand der Fläche (das Profil wächst mit,
        gleiche Längenanteile auf Pfad und Schiene gehören zusammen); sonst richtet sie das Profil nur aus.
        """
        from OCP.BRepFill import BRepFill_ContactOnBorder, BRepFill_NoContact
        try:
            pipe = BRepOffsetAPI_MakePipeShell(spine)
            pipe.SetTransitionMode(BRepBuilderAPI_RightCorner)
            if rail is not None:
                if mode == GUIDE_MODES[0]:
                    pipe.SetMode(rail, True, BRepFill_ContactOnBorder)
                else:
                    pipe.SetMode(rail, False, BRepFill_NoContact)
            pipe.Add(wire)
            pipe.Build()
            return pipe.Shape() if pipe.IsDone() and pipe.MakeSolid() else None
        except Exception as error:
            raise FeatureError("Der Sweep ließ sich mit diesem Profil und Pfad nicht bilden") from error


@dataclass
class Loft(Feature):
    """Erhebung: verbindet die Profile von zwei oder drei Skizzen zu einem Körper.

    Die Skizzen liegen üblicherweise auf versetzten Ebenen; verbunden wird in der
    angegebenen Reihenfolge.
    """

    sketch: str = ""
    sketch2: str = ""
    sketch3: str = NO_SKETCH
    operation: str = OPERATIONS[0]

    KIND = "Erhebung"

    def apply(self, state: BuildState) -> None:
        names = [name for name in (self.sketch, self.sketch2, self.sketch3) if name and name != NO_SKETCH]
        if len(names) < 2 or len(set(names)) != len(names):
            raise FeatureError("Die Erhebung braucht mindestens zwei verschiedene Skizzen")
        try:
            maker = BRepOffsetAPI_ThruSections(True, False)     # Volumen, weiche Übergänge
            for name in names:
                maker.AddWire(_outline(state, name))
            maker.CheckCompatibility(True)
            maker.Build()
            solid = maker.Shape() if maker.IsDone() else None
        except FeatureError:
            raise
        except Exception as error:
            raise FeatureError("Die Erhebung ließ sich zwischen diesen Profilen nicht bilden") from error
        if solid is None or not TopExp_Explorer(solid, TopAbs_SOLID).More():
            raise FeatureError("Bei der Erhebung entstand kein Volumen (liegen die Profile in einer Ebene?)")
        ft._combine(state, solid, self.operation)


HOLE_HEADS = ("Ohne Senkung", "Zylindersenkung", "Kegelsenkung")


@dataclass
class Hole(Feature):
    """Bohrung senkrecht zu einer Ebene: auf der beim Aufruf gewählten Körperfläche oder,
    ohne Auswahl, auf einer Hauptebene. Tiefe 0 bohrt durch alle getroffenen Körper."""

    plane: str = "XY"
    offset: float = 0.0
    frame: list = field(default_factory=list)   # Lage der gewählten Fläche (siehe sketch.frame_axes)
    position_x: float = 0.0
    position_y: float = 0.0
    diameter: float = 6.0
    depth: float = 0.0
    base: str = ft.NO_BASE      # Konstruktionsebene, von der aus gebohrt wird (statt Hauptebene/Fläche)
    head: str = "Ohne Senkung"  # Senkung für den Schraubenkopf (siehe HOLE_HEADS)
    head_diameter: float = 11.0
    head_depth: float = 6.0     # Tiefe der Zylindersenkung
    head_angle: float = 90.0    # Senkwinkel der Kegelsenkung in Grad (ganzer Öffnungswinkel)
    # Gewindebohrung wie in Fusion: ohne, nach Durchmesser oder eine Normgröße (die dann den Durchmesser bestimmt)
    tapped: str = "Ohne Gewinde"
    # Wie in Fusion: eine Bohrung an jedem Punkt dieser Skizze (NO_SKETCH: eine Bohrung an position_x/-y)
    points_sketch: str = NO_SKETCH

    # neu angelegt ohne Fläche/Skizzenpunkte: wartet auf den Klick auf eine Fläche (nicht im Ursprung bohren)
    require_pick: bool = False

    KIND = "Bohrung"

    def parameters(self) -> list:
        return [item for item in super().parameters() if item.name != "require_pick"]

    def bore(self) -> float:
        """Durchmesser der Bohrung: bei einer gewählten Gewindegröße deren Nenndurchmesser."""
        if self.tapped in THREAD_PITCHES:
            return float(self.tapped[1:].split(" × ")[0].replace(",", "."))
        return self.diameter

    def apply(self, state: BuildState) -> None:
        if self.require_pick and not self.frame and self.base == ft.NO_BASE \
                and self.points_sketch == NO_SKETCH:
            raise FeatureError(ft.PICK_FACES_MESSAGE)
        if self.points_sketch not in ("", NO_SKETCH):
            self._apply_at_points(state)
            return
        if self.tapped not in TAP_OPTIONS:
            raise FeatureError(f"Unbekannte Gewindeart '{self.tapped}'")
        diameter = self.bore()
        if diameter <= 0 or self.depth < 0:
            raise FeatureError("Der Durchmesser muss größer als 0 sein, die Tiefe nicht negativ")
        axes = sketch.placed_axes(state, self.plane, self.offset, self.frame, self.base)
        start = _in_plane(axes, self.position_x, self.position_y)
        into = axes.Direction().Reversed()      # gebohrt wird gegen die Flächennormale
        depth = self.depth
        if depth == 0:
            # Durchgangsbohrung: lang genug für das ganze Modell, etwas über der Fläche beginnend
            bounds = [_bounds(body) for body in state.bodies.values()]
            if not bounds:
                raise FeatureError("Für die Bohrung fehlt ein vorhandener Körper")
            reach = 2 * max(math.dist(b[:3], b[3:]) for b in bounds) + 2
            if self.frame:
                # Auf einer Körperfläche: in den Körper hinein, knapp über der Fläche beginnend
                depth = reach
                start = start.Translated(gp_Vec(axes.Direction().XYZ()) * 1.0)
            else:
                # Von einer Haupt- oder Konstruktionsebene aus: durch alles, auf beiden Seiten der Ebene
                depth = 2 * reach
                start = start.Translated(gp_Vec(axes.Direction().XYZ()) * reach)
        tool = BRepPrimAPI_MakeCylinder(gp_Ax2(start, into), diameter / 2, depth).Shape()
        ft._combine(state, tool, "Ausschneiden")
        if self.tapped != TAP_OPTIONS[0]:
            self._tap(state, start, into, diameter)
        if self.head not in HOLE_HEADS:
            raise FeatureError(f"Unbekannte Senkung '{self.head}'")
        if self.head != HOLE_HEADS[0]:
            if self.head_diameter <= diameter:
                raise FeatureError("Der Senkdurchmesser muss größer sein als der Bohrungsdurchmesser")
            # Die Senkung beginnt an der Fläche selbst; ein Stück darüber sorgt für einen sauberen Schnitt
            surface = _in_plane(axes, self.position_x, self.position_y)
            above = surface.Translated(gp_Vec(axes.Direction().XYZ()) * 1.0)
            if self.head == HOLE_HEADS[1]:
                if self.head_depth <= 0:
                    raise FeatureError("Die Senktiefe muss größer als 0 sein")
                sink = BRepPrimAPI_MakeCylinder(gp_Ax2(above, into), self.head_diameter / 2,
                                                self.head_depth + 1.0).Shape()
            else:
                if not 10 <= self.head_angle <= 170:
                    raise FeatureError("Der Senkwinkel muss zwischen 10 und 170 Grad liegen")
                # Kegelsenkung: je mm Tiefe wird der Radius um tan(halber Senkwinkel) kleiner;
                # 1 mm über der Fläche beginnt der Kegel entsprechend breiter
                slope = math.tan(math.radians(self.head_angle / 2))
                deep = (self.head_diameter - diameter) / 2 / slope
                sink = BRepPrimAPI_MakeCone(gp_Ax2(above, into), self.head_diameter / 2 + slope,
                                            diameter / 2, deep + 1.0).Shape()
            ft._combine(state, sink, "Ausschneiden")

    def _apply_at_points(self, state: BuildState) -> None:
        """Bohrt an jedem Skizzenpunkt der gewählten Skizze, senkrecht zu deren Ebene."""
        points = state.sketch_points.get(self.points_sketch)
        if not points:
            raise FeatureError(f"Die Skizze '{self.points_sketch}' enthält keine Skizzenpunkte")
        axes = state.frames[self.points_sketch]
        normal, xdir = axes.Direction(), axes.XDirection()
        bounds = [_bounds(body) for body in state.bodies.values()]
        if not bounds:
            raise FeatureError("Für die Bohrung fehlt ein vorhandener Körper")
        reach = 2 * max(math.dist(b[:3], b[3:]) for b in bounds) + 2
        lay = lambda point: [*point, normal.X(), normal.Y(), normal.Z(), xdir.X(), xdir.Y(), xdir.Z()]
        for where in points:
            # dieselbe Bohrung, aber auf einer gedachten Fläche durch den Punkt (Lage wie eine Körperfläche)
            single = replace(self, points_sketch=NO_SKETCH, base=ft.NO_BASE, offset=0.0, position_x=0.0,
                             position_y=0.0, frame=lay(where))
            if self.depth == 0:
                # Durchgang: auf beiden Seiten der Skizzenebene durch alles (die Skizze kann auch unter
                # dem Körper liegen); eine Senkung sitzt danach wie gewohnt an der Skizzenebene
                far = tuple(c + n * reach for c, n in zip(where, (normal.X(), normal.Y(), normal.Z())))
                replace(single, frame=lay(far), depth=2 * reach, head=HOLE_HEADS[0]).apply(state)
                if self.head == HOLE_HEADS[0]:
                    continue
                single = replace(single, depth=1e-3, tapped=TAP_OPTIONS[0])
            single.apply(state)

    def _tap(self, state: BuildState, start, into, diameter: float) -> None:
        """Schneidet das Innengewinde in die Wand der eben gebohrten Bohrung (jedes getroffenen Körpers)."""
        size = self.tapped if self.tapped in THREAD_PITCHES else THREAD_SIZES[0]
        axis = gp_Vec(into.XYZ())
        walls = []
        for name, body in state.bodies.items():
            for face in ft.faces_of(body):
                surface = BRepAdaptor_Surface(TopoDS.Face(face))
                if surface.GetType() != GeomAbs_Cylinder or abs(surface.Cylinder().Radius() - diameter / 2) > 1e-6:
                    continue
                # gleiche Achse: parallel und durch den Startpunkt der Bohrung
                cylinder = surface.Cylinder().Axis()
                offset = gp_Vec(start, cylinder.Location())
                if gp_Vec(cylinder.Direction().XYZ()).Crossed(axis).Magnitude() < 1e-9 and \
                        offset.Crossed(axis).Magnitude() < 1e-6:
                    walls.append((name, list(ft.face_point(face))))
        if not walls:
            raise FeatureError("Die Bohrung trifft keinen Körper – dort lässt sich kein Gewinde schneiden")
        for name, point in walls:
            Thread(body=name, face_points=[point], size=size).apply(state)


@dataclass
class MoveBody(Feature):
    """Verschiebt, dreht und skaliert Körper (wie Fusions „Verschieben/Kopieren“).

    Gedreht und skaliert wird um den Drehpunkt: die Mitte der gewählten Körper (neu angelegte Schritte) oder – wie
    in Entwürfen vor Runde 81 – den Ursprung. Reihenfolge: skalieren, drehen, verschieben.
    """

    body: str = ALL_BODIES
    move_x: float = 0.0
    move_y: float = 0.0
    move_z: float = 0.0
    axis: str = "Z"
    angle: float = 0.0
    copy: bool = False          # wie Fusion „Kopie erstellen“: das Original bleibt stehen
    # Vorgabe „Ursprung“: so rechnen gespeicherte Entwürfe ohne dieses Feld wie bisher. Die Werkzeugleiste legt neue
    # Schritte mit „Körpermitte“ an (drehen/skalieren an Ort und Stelle, wie in Fusion).
    pivot: str = MOVE_PIVOTS[1]
    scale: float = 1.0          # gleichmäßig skalieren (1 = unverändert)
    # Runde 82: wie Fusion um jede Achse drehen (Grad, um die Weltachsen durch den Drehpunkt). Gedreht wird erst um X,
    # dann Y, dann Z, danach wie bisher um `axis` mit `angle` (alte Schritte haben nur diesen einen Winkel).
    angle_x: float = 0.0
    angle_y: float = 0.0
    angle_z: float = 0.0

    KIND = "Verschieben"

    def parameters(self) -> list:
        """Ohne den Winkel, der dieselbe Achse wie `axis`/`angle` dreht – sonst stünde im Eigenschaftenfeld zweimal
        „Drehen um Z“ (neue Schritte drehen um Z über `angle`)."""
        doubled = f"angle_{self.axis.lower()}"
        if getattr(self, doubled, 0.0):
            return super().parameters()     # wirkt mit (z. B. nach dem Umstellen der Achse): dann zeigen
        return [item for item in super().parameters() if item.name != doubled]

    def transform(self, state: BuildState, chosen: list[str]) -> gp_Trsf:
        """Gesamte Bewegung: skalieren und drehen um den Drehpunkt, dann verschieben."""
        if self.axis not in AXES:
            raise FeatureError(f"Unbekannte Achse '{self.axis}'")
        if self.pivot not in MOVE_PIVOTS:
            raise FeatureError(f"Unbekannter Drehpunkt '{self.pivot}'")
        if self.scale <= 0:
            raise FeatureError("Der Skalierfaktor muss größer als 0 sein")
        centre = gp_Pnt(0, 0, 0)
        if self.pivot == MOVE_PIVOTS[0] and chosen:
            centre = bodies_centre([state.bodies[name] for name in chosen])
        scaling = gp_Trsf()
        if abs(self.scale - 1.0) > 1e-12:
            scaling.SetScale(centre, self.scale)
        rotation = gp_Trsf()
        for axis, angle in (("X", self.angle_x), ("Y", self.angle_y), ("Z", self.angle_z), (self.axis, self.angle)):
            if abs(angle) > 1e-12:
                turn = gp_Trsf()
                turn.SetRotation(gp_Ax1(centre, gp_Dir(*AXES[axis])), math.radians(angle))
                rotation = turn.Multiplied(rotation)        # die spätere Drehung wirkt auf das schon Gedrehte
        shift = gp_Trsf()
        shift.SetTranslation(gp_Vec(self.move_x, self.move_y, self.move_z))
        return shift.Multiplied(rotation.Multiplied(scaling))

    def apply(self, state: BuildState) -> None:
        chosen = list(ft.select_bodies(state, self.body))
        transform = self.transform(state, chosen)
        if self.copy:
            # Kopien an der neuen Stelle, die Originale bleiben (Gruppen bewegen sich dabei nicht)
            for name in chosen:
                state.add_body(BRepBuilderAPI_Transform(state.bodies[name], transform, True, True).Shape())
            return
        # Körper einer starren Gruppe gehen mit (auch über mehrere Gruppen hinweg)
        for name in chosen:
            for members in state.rigid_groups.values():
                if name in members:
                    chosen += [member for member in members if member in state.bodies and member not in chosen]
        for name in chosen:
            state.move_body(name, transform)        # gefärbte Flächen wandern mit


def bodies_centre(shapes: list) -> gp_Pnt:
    """Gemeinsamer Volumenschwerpunkt mehrerer Körper (Drehpunkt beim Verschieben)."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    total = GProp_GProps()
    for shape in shapes:
        single = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, single)
        total.Add(single)
    return total.CentreOfMass()


@dataclass
class CombineBodies(Feature):
    """Kombinieren wie in Fusion: ein Zielkörper wird mit einem oder mehreren Werkzeugkörpern verrechnet; die
    Werkzeugkörper entfallen (außer bei „Werkzeugkörper behalten“)."""

    target: str = ""
    tool: str = ""              # ältere Entwürfe: genau ein Werkzeugkörper (neu: `tools`)
    operation: str = COMBINE_OPERATIONS[0]
    keep_tool: bool = False     # wie Fusion „Werkzeuge behalten“: die Werkzeugkörper bleiben stehen
    tools: list = field(default_factory=list)   # Werkzeugkörper (in der Ansicht nacheinander angeklickt)

    KIND = "Kombinieren"

    def parameters(self) -> list:
        return [item for item in super().parameters() if item.name != "tool"]

    def tool_names(self) -> list[str]:
        """Die Werkzeugkörper: die neue Liste, sonst der eine Körper älterer Entwürfe."""
        return list(self.tools) if self.tools else ([self.tool] if self.tool else [])

    def apply(self, state: BuildState) -> None:
        operations = {"Vereinigen": BRepAlgoAPI_Fuse, "Ausschneiden": BRepAlgoAPI_Cut,
                      "Schnittmenge": BRepAlgoAPI_Common}
        if self.operation not in operations:
            raise FeatureError(f"Unbekannte Operation '{self.operation}'")
        if not self.target:
            raise FeatureError("Zielkörper anklicken, danach die Werkzeugkörper")
        tools = self.tool_names()
        if not tools:
            raise FeatureError("Jetzt die Werkzeugkörper anklicken, die mit dem Zielkörper verrechnet werden")
        if self.target in tools:
            raise FeatureError("Ziel- und Werkzeugkörper müssen verschieden sein")
        for name in (self.target, *tools):
            if name not in state.bodies or name in state.frozen:
                raise FeatureError(f"Körper '{name}' ist nicht vorhanden oder nicht veränderbar")
        result = state.bodies[self.target]
        for name in tools:
            result = ft._boolean(operations[self.operation], result, state.bodies[name])
        if not self.keep_tool:
            for name in tools:
                del state.bodies[name]
        if TopExp_Explorer(result, TopAbs_SOLID).More():
            state.bodies[self.target] = result
        else:
            del state.bodies[self.target]   # nichts übrig geblieben


@dataclass
class MirrorBody(Feature):
    """Spiegelt Körper an einer Hauptebene; die Kopie wird ein neuer Körper oder mit dem
    Original vereinigt."""

    body: str = ALL_BODIES
    plane: str = "YZ"
    offset: float = 0.0
    operation: str = OPERATIONS[0]
    frame: list = field(default_factory=list)   # gewählte ebene Körperfläche als Spiegelebene (wie Teilen)
    base: str = ft.NO_BASE      # Konstruktionsebene als Spiegelebene

    KIND = "Spiegeln"

    def apply(self, state: BuildState) -> None:
        if self.operation not in OPERATIONS[:2]:
            raise FeatureError("Spiegeln erzeugt einen neuen Körper oder vereinigt mit dem Original")
        # wie Fusion: an einer Hauptebene, einer gewählten ebenen Körperfläche oder einer Konstruktionsebene
        axes = sketch.placed_axes(state, self.plane, self.offset, self.frame, self.base)
        mirror = gp_Trsf()
        mirror.SetMirror(gp_Ax2(axes.Location(), axes.Direction()))
        names = ft.select_bodies(state, self.body)
        if self.operation != "Vereinigen":
            _check_body_count(state, len(names))
        for name in names:
            # Zerlegung mitkopieren: die Anzeige muss die Kopie nicht neu zerlegen
            copy = BRepBuilderAPI_Transform(state.bodies[name], mirror, True, True).Shape()
            if self.operation == "Vereinigen":
                state.bodies[name] = ft._boolean(BRepAlgoAPI_Fuse, state.bodies[name], copy)
            else:
                state.add_body(copy)


# Obergrenze der Körperzahl: Spiegeln/Muster mit „Neuer Körper“ auf „Alle“ verdoppeln bei jeder Wiederholung die
# Körper – nach wenigen Schritten wären es Tausende und das Programm hinge minutenlang (Zufallstest Startwert 46)
MAX_BODIES = 1000


def _check_body_count(state: BuildState, new: int) -> None:
    """Meldet einen Fehler, bevor ein Werkzeug mehr als MAX_BODIES Körper erzeugen würde."""
    if len(state.bodies) + new > MAX_BODIES:
        raise FeatureError(f"Das ergäbe {len(state.bodies) + new} Körper (höchstens {MAX_BODIES}) – "
                           f"bitte einen einzelnen Körper statt „Alle“ wählen oder „Vereinigen“")


PATTERN_OBJECTS = ("Körper", "Features")
# Felder, die nur zu einem Objekttyp gehören (das Formular zeigt nur die passenden)
PATTERN_OBJECT_FIELDS = {"body": ("Körper",), "operation": ("Körper",), "source_feature": ("Features",),
                         "compute": ("Features",)}
# Berechnung wie in Fusion: „Angepasst“ rechnet das Feature an jeder Stelle neu (es passt sich der Geometrie dort an,
# z. B. „Bis Fläche“ oder eine Durchgangsbohrung bei anderer Wandstärke); „Identisch“ kopiert nur seine Wirkung
PATTERN_COMPUTE = ("Angepasst", "Identisch")


def _pattern(feature, state: BuildState, transforms: list) -> None:
    """Führt ein Muster aus: Körper kopieren oder die Wirkung eines Features wiederholen."""
    if feature.object_type not in PATTERN_OBJECTS:
        raise FeatureError(f"Unbekannter Objekttyp '{feature.object_type}'")
    if feature.object_type == PATTERN_OBJECTS[0]:
        _copies(state, feature.body, transforms, feature.operation)
    elif getattr(feature, "compute", PATTERN_COMPUTE[0]) == PATTERN_COMPUTE[0]:
        _feature_recomputed(state, feature.source_feature, transforms)
    else:
        _feature_copies(state, feature.source_feature, transforms)


def _feature_recomputed(state: BuildState, source: str, transforms: list) -> None:
    """Rechnet das Feature `source` an jeder Musterstelle neu (Fusion: Berechnung „Angepasst“).

    Statt das Feature selbst zu verschieben (seine Eingaben – Skizze, Fläche, Ebene – lassen sich nicht allgemein
    verschieben), wird das Modell um die Gegenbewegung verschoben, das Feature dort unverändert ausgeführt und
    das Ergebnis zurückbewegt. Für das Feature sieht das aus, als läge es an der Musterstelle.
    """
    if source not in state.history:
        raise FeatureError("Für „Muster von Features“ ein früheres Feature wählen, das Körper verändert hat")
    original = state.history[source][2]
    for transform in transforms:
        back = transform.Inverted()
        shifted = state.copy()
        shifted.bodies = {name: BRepBuilderAPI_Transform(body, back, True).Shape() for name, body in state.bodies.items()}
        try:
            original.apply(shifted)
        except FeatureError as error:
            raise FeatureError(f"An einer Musterstelle scheitert '{source}': {error}") from error
        for name, body in shifted.bodies.items():
            moved = BRepBuilderAPI_Transform(body, transform, True, True).Shape()     # mit Zerlegung
            if name in state.bodies:
                if name not in state.frozen:
                    state.bodies[name] = moved
            else:
                state.add_body(moved)       # das Feature hat dort einen neuen Körper erzeugt


def _feature_copies(state: BuildState, source: str, transforms: list) -> None:
    """Wiederholt, was das Feature `source` am Modell geändert hat, an jeder Musterstelle.

    Die Wirkung ergibt sich aus dem Körperstand vor und nach dem Feature: weggenommenes Material
    (z. B. eine Bohrung, eine Tasche) wird an der verschobenen Stelle ebenfalls weggenommen,
    hinzugefügtes (z. B. ein Zapfen) dort ebenfalls angefügt. Hat das Feature einen neuen Körper
    erzeugt, entsteht je Stelle eine Kopie als neuer Körper.
    """
    if source not in state.history:
        raise FeatureError("Für „Muster von Features“ ein früheres Feature wählen, das Körper verändert hat")
    before, after = state.history[source][:2]
    removed, added, created = [], [], []
    for name, shape in after.items():
        if name not in before:
            created.append(shape)
            continue
        if shape is before[name]:
            continue                    # dieser Körper blieb unverändert
        cut = ft._boolean(BRepAlgoAPI_Cut, before[name], shape)
        grown = ft._boolean(BRepAlgoAPI_Cut, shape, before[name])
        if _volume(cut) > 1e-9:
            removed.append((name, cut))
        if _volume(grown) > 1e-9:
            added.append((name, grown))
    for name, shape in before.items():
        if name not in after:
            raise FeatureError(f"'{source}' hat Körper entfernt oder vereinigt – das lässt sich nicht als Muster wiederholen")
    if not (removed or added or created):
        raise FeatureError(f"'{source}' hat am Modell nichts verändert")
    _check_body_count(state, len(created) * len(transforms))
    for transform in transforms:
        move = lambda shape: BRepBuilderAPI_Transform(shape, transform, True, True).Shape()     # mit Zerlegung
        for name, material in removed:
            if name in state.bodies and name not in state.frozen:
                state.bodies[name] = ft._boolean(BRepAlgoAPI_Cut, state.bodies[name], move(material))
        for name, material in added:
            if name in state.bodies and name not in state.frozen:
                state.bodies[name] = ft._boolean(BRepAlgoAPI_Fuse, state.bodies[name], move(material))
        for shape in created:
            state.add_body(move(shape))


def _copies(state: BuildState, body: str, transforms: list, operation: str) -> None:
    """Erzeugt je Körper und Abbildung eine Kopie: als neue Körper oder mit dem Original vereinigt."""
    if operation not in OPERATIONS[:2]:
        raise FeatureError("Ein Muster erzeugt neue Körper oder vereinigt mit dem Original")
    names = ft.select_bodies(state, body)
    if operation != "Vereinigen":
        _check_body_count(state, len(names) * len(transforms))
    for name in names:
        original = state.bodies[name]
        for transform in transforms:
            # Zerlegung mitkopieren: bei hunderten Musterkopien entfällt das Neuzerlegen für die Anzeige
            copy = BRepBuilderAPI_Transform(original, transform, True, True).Shape()
            if operation == "Vereinigen":
                state.bodies[name] = ft._boolean(BRepAlgoAPI_Fuse, state.bodies[name], copy)
            else:
                state.add_body(copy)


PATH_ORIENTATIONS = ("Gleich ausgerichtet", "Am Pfad ausgerichtet")


@dataclass
class PathPattern(Feature):
    """Muster entlang einer Kurve: Kopien gleichmäßig verteilt auf dem Pfad einer Skizze.

    Die erste Kopie ist das Original am Pfadanfang; „Am Pfad ausgerichtet“ dreht jede Kopie mit der Richtung
    des Pfads mit (wie in Fusion).
    """

    object_type: str = PATTERN_OBJECTS[0]  # Körper oder Features (wie in Fusion: „Objekttyp“)
    body: str = ALL_BODIES
    source_feature: str = ""            # bei „Features“: das Feature, dessen Wirkung wiederholt wird
    compute: str = PATTERN_COMPUTE[0]   # bei „Features“: je Stelle neu berechnen oder Wirkung kopieren
    path: str = ""
    count: float = 5.0
    orientation: str = PATH_ORIENTATIONS[0]
    operation: str = OPERATIONS[0]

    KIND = "Muster entlang Pfad"

    def apply(self, state: BuildState) -> None:
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        from OCP.GCPnts import GCPnts_UniformAbscissa
        count = round(self.count)
        if count < 2:
            raise FeatureError("Das Muster braucht mindestens zwei Exemplare")
        if count > _PATTERN_LIMIT:
            raise FeatureError(f"Das Muster ist auf {_PATTERN_LIMIT} Exemplare begrenzt")
        if self.orientation not in PATH_ORIENTATIONS:
            raise FeatureError(f"Unbekannte Ausrichtung '{self.orientation}'")
        curve = BRepAdaptor_CompCurve(TopoDS.Wire(_path_wire(state, self.path)))
        sampler = GCPnts_UniformAbscissa(curve, count, curve.FirstParameter(), curve.LastParameter())
        if not sampler.IsDone() or sampler.NbPoints() < 2:
            raise FeatureError("Auf diesem Pfad lassen sich die Kopien nicht verteilen")

        def place(parameter: float) -> tuple:
            point, tangent = gp_Pnt(), gp_Vec()
            curve.D1(parameter, point, tangent)
            return point, tangent

        origin, first_tangent = place(sampler.Parameter(1))
        transforms = []
        for number in range(2, sampler.NbPoints() + 1):
            point, tangent = place(sampler.Parameter(number))
            move = gp_Trsf()
            move.SetTranslation(gp_Vec(origin, point))
            if self.orientation == PATH_ORIENTATIONS[1]:
                # um den Pfadpunkt drehen, bis die Anfangsrichtung des Pfads in seine Richtung dort zeigt
                axis = first_tangent.Crossed(tangent)
                if axis.Magnitude() > 1e-9:
                    turn = gp_Trsf()
                    turn.SetRotation(gp_Ax1(point, gp_Dir(axis)), first_tangent.Angle(tangent))
                    move = turn.Multiplied(move)
            transforms.append(move)
        _pattern(self, state, transforms)


@dataclass
class DeleteFace(Feature):
    """Fläche löschen wie in Fusion: entfernt gewählte Flächen (z. B. Verrundungen, Fasen, Bohrungen) und schließt
    die Lücke mit den Nachbarflächen."""

    body: str = ALL_BODIES
    face_points: list = field(default_factory=list)     # Merkpunkte der gewählten Flächen

    KIND = "Fläche löschen"

    def apply(self, state: BuildState) -> None:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
        if not self.face_points:
            raise FeatureError("Vor dem Werkzeug die Flächen anklicken, die entfernt werden sollen")
        names = ft.select_bodies(state, self.body)
        if self.body == ALL_BODIES:
            # Flächen an mehreren Körpern (Entf mit Rahmenauswahl): je Körper nur die Merkpunkte, die dort liegen
            grouped = {}
            for marker in self.face_points:
                owner = next((name for name in names if any(
                    math.dist(ft.face_point(face), marker[:3]) <= ft.SELECTION_TOLERANCE
                    for face in ft.faces_of(state.bodies[name]))), None)
                if owner is None:
                    raise FeatureError("Eine gewählte Fläche ist im Modell nicht mehr vorhanden")
                grouped.setdefault(owner, []).append(marker)
        else:
            grouped = {name: self.face_points for name in names}
        for name, markers in grouped.items():
            faces = ft.find_by_points(ft.faces_of(state.bodies[name]), ft.face_point, markers, "Fläche")
            state.bodies[name] = remove_faces(state.bodies[name], faces)


@dataclass
class MoveFace(Feature):
    """Fläche verschieben wie in Fusion (Verschieben mit Objekttyp „Flächen“): gewählte Flächen wandern um
    (move_x, move_y, move_z), die Nachbarflächen passen sich an.

    Bilden die Flächen eine geschlossene Form im Körper (Bohrung, Tasche, Zapfen, Rippe …), wird diese Form
    als Ganzes versetzt (und gedreht). Eine ebene Außenfläche (z. B. die Oberseite eines Quaders) wird durch ihre
    verschobene bzw. gekippte Ebene ersetzt; ein Anteil der Verschiebung in ihrer eigenen Ebene ändert an ihr nichts.
    Gedreht wird um eine Achse durch die Mitte der gewählten Flächen, danach verschoben.
    """

    body: str = ALL_BODIES
    face_points: list = field(default_factory=list)     # Merkpunkte der gewählten Flächen
    move_x: float = 0.0
    move_y: float = 0.0
    move_z: float = 5.0
    rotate_axis: str = "Z"
    rotate_angle: float = 0.0

    KIND = "Fläche verschieben"

    def transform(self) -> gp_Trsf:
        """Drehung um die Achse durch die Mitte der gewählten Flächen, dann Verschiebung."""
        if self.rotate_axis not in AXES:
            raise FeatureError(f"Unbekannte Drehachse '{self.rotate_axis}'")
        centre = gp_Pnt(*[sum(p[i] for p in self.face_points) / len(self.face_points) for i in range(3)])
        turn = gp_Trsf()
        turn.SetRotation(gp_Ax1(centre, gp_Dir(*AXES[self.rotate_axis])), math.radians(self.rotate_angle))
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(self.move_x, self.move_y, self.move_z))
        return move.Multiplied(turn)

    def apply(self, state: BuildState) -> None:
        if not self.face_points:
            raise FeatureError("Vor dem Werkzeug die Flächen anklicken, die verschoben werden sollen")
        shift = gp_Vec(self.move_x, self.move_y, self.move_z)
        turning = abs(self.rotate_angle % 360.0) > 1e-9
        if shift.Magnitude() < 1e-9 and not turning:
            raise FeatureError("Verschiebung und Drehung dürfen nicht beide 0 sein")
        move = self.transform()
        for name in ft.select_bodies(state, self.body):
            body = state.bodies[name]
            faces = ft.find_by_points(ft.faces_of(body), ft.face_point, self.face_points, "Fläche")
            filled = _without_faces(body, faces)
            hollow = solid = None
            if filled is not None:
                # Die Form, die die Flächen bilden: was beim Entfernen der Flächen dazukommt (z. B. die Bohrung)
                # bzw. wegfällt (z. B. ein Zapfen) – sie wird versetzt wieder eingesetzt
                hollow = ft._boolean(BRepAlgoAPI_Cut, filled, body)
                solid = ft._boolean(BRepAlgoAPI_Cut, body, filled)
                if _volume(hollow) <= 1e-9 and _volume(solid) <= 1e-9:
                    filled = None       # nichts geschlossen (z. B. Außenfläche): wie eine Außenfläche behandeln
            if filled is not None:
                result = filled
                if _volume(hollow) > 1e-9:
                    result = ft._boolean(BRepAlgoAPI_Cut, result, BRepBuilderAPI_Transform(hollow, move, True).Shape())
                if _volume(solid) > 1e-9:
                    result = ft._boolean(BRepAlgoAPI_Fuse, result, BRepBuilderAPI_Transform(solid, move, True).Shape())
            else:
                # Außenflächen: nur ebene Flächen, die längs ihrer Normale wandern (wie Drücken/Ziehen)
                if any(sketch.face_frame(face) is None for face in faces):
                    raise FeatureError("Diese gekrümmte Fläche lässt sich nicht verschieben – sie umschließt keine "
                                       "eigene Form im Körper")
                result = body
                for face in faces:
                    normal = gp_Vec(*sketch.face_frame(face)[3:6])
                    if turning:
                        result = _tilted_face(result, face, normal, move)
                        continue
                    along = shift.Dot(normal)
                    if abs(along) < 1e-9:
                        raise FeatureError("Die Verschiebung liegt in der Ebene der Fläche und ändert an ihr nichts")
                    prism = BRepPrimAPI_MakePrism(face, normal * along).Shape()
                    result = ft._boolean(BRepAlgoAPI_Fuse if along > 0 else BRepAlgoAPI_Cut, result, prism)
            if not TopExp_Explorer(result, TopAbs_SOLID).More() or _volume(result) <= 1e-9:
                raise FeatureError("Das Verschieben würde den Körper vollständig entfernen")
            merged = ShapeUpgrade_UnifySameDomain(result, True, True, False)
            merged.Build()
            state.bodies[name] = merged.Shape()


def _tilted_face(body, face, normal: gp_Vec, move: gp_Trsf):
    """Ersetzt eine ebene Außenfläche durch ihre bewegte (gekippte) Ebene – nur in der Säule über und unter ihr.

    In der Säule wird der Körper erst weit nach außen aufgefüllt, dann alles jenseits der neuen Ebene entfernt.
    Teile des Körpers außerhalb der Säule bleiben unberührt.
    """
    reach = 2 * math.dist(_bounds(body)[:3], _bounds(body)[3:]) + 10.0
    centre = gp_Pnt(*ft.face_point(face))
    plane = gp_Pln(centre, gp_Dir(normal.XYZ())).Transformed(move)
    new_normal = gp_Vec(plane.Axis().Direction().XYZ())
    if new_normal.Dot(normal) <= 1e-6:
        raise FeatureError("Die Fläche würde so weit gedreht, dass sie nach innen zeigt (höchstens knapp 90°)")
    # die Säule ist so weit wie die gekippte Ebene über der Fläche ausweichen kann: gerade, längs der alten Normale
    start = BRepBuilderAPI_Transform(face, _translation(normal * -reach), True).Shape()
    column = BRepPrimAPI_MakePrism(start, normal * (2 * reach)).Shape()
    grown = ft._boolean(BRepAlgoAPI_Fuse, body, BRepPrimAPI_MakePrism(face, normal * reach).Shape())
    keep = plane.Location().Translated(new_normal * -1.0)       # die Seite der neuen Ebene, die im Körper liegt
    beyond_half = BRepPrimAPI_MakeHalfSpace(BRepBuilderAPI_MakeFace(plane).Face(), keep).Solid()
    outside = ft._boolean(BRepAlgoAPI_Cut, column, beyond_half)     # Säule jenseits der neuen Ebene
    return ft._boolean(BRepAlgoAPI_Cut, grown, outside)


def _translation(vector: gp_Vec) -> gp_Trsf:
    move = gp_Trsf()
    move.SetTranslation(vector)
    return move


def _without_faces(body, faces):
    """Der Körper ohne die Flächen, Lücke von den Nachbarn geschlossen (wie „Fläche löschen“); None, wenn das
    nicht geht (z. B. bei einer Außenfläche eines Quaders)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
    remover = BRepAlgoAPI_Defeaturing()
    remover.SetShape(body)
    for face in faces:
        remover.AddFaceToRemove(face)
    remover.SetRunParallel(True)
    try:
        remover.Build()
    except Exception:
        return None
    result = remover.Shape() if remover.IsDone() else None
    if result is None or result.IsNull() or not BRepCheck_Analyzer(result).IsValid() \
            or not TopExp_Explorer(result, TopAbs_SOLID).More():
        return None
    return result


TARGET_FACE = "Fläche eines anderen Körpers"   # Ersatz durch eine (auch gekrümmte) Körperfläche


@dataclass
class ReplaceFace(Feature):
    """Fläche ersetzen wie in Fusion: eine ebene Körperfläche wird durch eine Konstruktionsebene oder durch eine
    (auch gekrümmte) Fläche eines anderen Körpers ersetzt – der Körper wächst bis zu ihr oder wird an ihr
    abgeschnitten (z. B. eine schräge oder gewölbte Oberseite)."""

    body: str = ALL_BODIES
    face_points: list = field(default_factory=list)     # Merkpunkt der zu ersetzenden Fläche
    target: str = ft.NO_BASE                            # Konstruktionsebene oder TARGET_FACE
    target_body: str = ""                               # bei TARGET_FACE: Körper mit der Ersatzfläche
    target_points: list = field(default_factory=list)   # … und deren Merkpunkt

    KIND = "Fläche ersetzen"

    def apply(self, state: BuildState) -> None:
        if not self.face_points:
            raise FeatureError("Vor dem Werkzeug die Fläche anklicken, die ersetzt werden soll")
        if self.target == TARGET_FACE:
            self._replace_by_face(state)
            return
        if self.target not in state.planes:
            raise FeatureError("Als Ersatz eine Konstruktionsebene oder die Fläche eines anderen Körpers wählen")
        plane = state.planes[self.target]
        for name in ft.select_bodies(state, self.body):
            body = state.bodies[name]
            face = ft.find_by_points(ft.faces_of(body), ft.face_point, self.face_points[:1], "Fläche")[0]
            frame = sketch.face_frame(face)
            if frame is None:
                raise FeatureError("Fläche ersetzen geht nur an ebenen Flächen")
            normal = gp_Vec(*frame[3:6])
            reach = 2 * math.dist(_bounds(body)[:3], _bounds(body)[3:]) + 10.0
            # erst die Fläche weit nach außen ziehen, dann alles jenseits der Ersatzebene abschneiden
            grown = ft._boolean(BRepAlgoAPI_Fuse, body, BRepPrimAPI_MakePrism(face, normal * reach).Shape())
            # behalten wird die Seite der Ebene, die der alten Fläche den Rücken kehrt (entgegen ihrer Außenrichtung)
            facing = normal.Dot(gp_Vec(plane.Direction().XYZ()))
            if abs(facing) < 1e-9:
                raise FeatureError("Die Ersatzebene steht senkrecht zur Fläche – so lässt sie sich nicht ersetzen")
            keep = plane.Location().Translated(gp_Vec(plane.Direction().XYZ()) * (-1.0 if facing > 0 else 1.0))
            half = BRepPrimAPI_MakeHalfSpace(BRepBuilderAPI_MakeFace(gp_Pln(plane.Location(), plane.Direction())).Face(),
                                             keep).Solid()
            result = ft._boolean(BRepAlgoAPI_Common, grown, half)
            if not TopExp_Explorer(result, TopAbs_SOLID).More():
                raise FeatureError("Die Ersatzebene schneidet den Körper nicht sinnvoll")
            merged = ShapeUpgrade_UnifySameDomain(result, True, True, False)
            merged.Build()
            state.bodies[name] = merged.Shape()

    def _replace_by_face(self, state: BuildState) -> None:
        """Ersatz durch eine Körperfläche: die Fläche wird über ihre Ränder hinaus verlängert, der Körper weit
        aufgezogen und an ihr zerteilt; weg fällt das Stück jenseits der Ersatzfläche."""
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
        if self.target_body not in state.bodies or not self.target_points:
            raise FeatureError("Die Ersatzfläche ist nicht (mehr) vorhanden – zwei Flächen wählen: erst die zu "
                               "ersetzende, dann die Ersatzfläche an einem anderen Körper")
        if self.target_body in ft.select_bodies(state, self.body):
            raise FeatureError("Die Ersatzfläche muss an einem anderen Körper liegen")
        replacement = ft.find_by_points(ft.faces_of(state.bodies[self.target_body]), ft.face_point,
                                        self.target_points[:1], "Fläche")[0]
        for name in ft.select_bodies(state, self.body):
            body = state.bodies[name]
            face = ft.find_by_points(ft.faces_of(body), ft.face_point, self.face_points[:1], "Fläche")[0]
            frame = sketch.face_frame(face)
            if frame is None:
                raise FeatureError("Fläche ersetzen geht nur an ebenen Flächen")
            normal = gp_Vec(*frame[3:6])
            reach = 2 * math.dist(_bounds(body)[:3], _bounds(body)[3:]) + 2 * math.dist(
                _bounds(state.bodies[self.target_body])[:3], _bounds(state.bodies[self.target_body])[3:]) + 10.0
            grown = ft._boolean(BRepAlgoAPI_Fuse, body, BRepPrimAPI_MakePrism(face, normal * reach).Shape())
            sheet = extended_face(replacement, reach)
            tools = List_TopoDS_Shape()
            tools.Append(sheet)
            shapes = List_TopoDS_Shape()
            shapes.Append(grown)
            splitter = BRepAlgoAPI_Splitter()
            splitter.SetArguments(shapes)
            splitter.SetTools(tools)
            splitter.Build()
            if not splitter.IsDone():
                raise FeatureError("Die Ersatzfläche ließ sich nicht mit dem Körper verschneiden")
            pieces = []
            explorer = TopExp_Explorer(splitter.Shape(), TopAbs_SOLID)
            while explorer.More():
                pieces.append(explorer.Current())
                explorer.Next()
            # weg fällt das Stück, in dem das äußerste Ende des Aufzugs liegt (jenseits der Ersatzfläche)
            tip = gp_Pnt(*ft.face_point(face)).Translated(normal * (reach - 1.0))
            kept = [piece for piece in pieces
                    if BRepClass3d_SolidClassifier(piece, tip, 1e-6).State() != TopAbs_IN]
            if len(pieces) < 2 or not kept:
                raise FeatureError("Die Ersatzfläche trennt den aufgezogenen Körper nicht – reicht sie über die "
                                   "ganze Fläche?")
            result = kept[0]
            for piece in kept[1:]:
                result = ft._boolean(BRepAlgoAPI_Fuse, result, piece)
            merged = ShapeUpgrade_UnifySameDomain(result, True, True, False)
            merged.Build()
            state.bodies[name] = merged.Shape()


@dataclass
class RectangularPattern(Feature):
    """Wiederholt Körper in einem Raster entlang zweier Achsen (Anzahl jeweils einschließlich Original)."""

    object_type: str = PATTERN_OBJECTS[0]  # Körper oder Features (wie in Fusion: „Objekttyp“)
    body: str = ALL_BODIES
    source_feature: str = ""            # bei „Features“: das Feature, dessen Wirkung wiederholt wird
    compute: str = PATTERN_COMPUTE[0]   # bei „Features“: je Stelle neu berechnen oder Wirkung kopieren
    axis: str = "X"
    count: float = 3.0
    spacing: float = 20.0
    second_axis: str = "Y"
    second_count: float = 1.0
    second_spacing: float = 20.0
    operation: str = OPERATIONS[0]

    KIND = "Rechteckiges Muster"

    def apply(self, state: BuildState) -> None:
        first, second = int(round(self.count)), int(round(self.second_count))
        if first < 1 or second < 1 or first * second < 2:
            raise FeatureError("Das Muster braucht insgesamt mindestens zwei Exemplare")
        if first * second > _PATTERN_LIMIT:
            raise FeatureError(f"Das Muster ist auf {_PATTERN_LIMIT} Exemplare begrenzt")
        if self.axis not in AXES or self.second_axis not in AXES:
            raise FeatureError("Unbekannte Achse")
        transforms = []
        for i in range(first):
            for j in range(second):
                if i == j == 0:
                    continue    # das Original selbst
                shift = gp_Trsf()
                shift.SetTranslation(gp_Vec(*AXES[self.axis]) * (i * self.spacing)
                                     + gp_Vec(*AXES[self.second_axis]) * (j * self.second_spacing))
                transforms.append(shift)
        _pattern(self, state, transforms)


@dataclass
class CircularPattern(Feature):
    """Wiederholt Körper auf einem Kreis um eine Achse durch den Ursprung.

    Bei 360 Grad werden die Exemplare gleichmäßig auf den Vollkreis verteilt, sonst
    liegen das erste und das letzte an den Enden des Winkels.
    """

    object_type: str = PATTERN_OBJECTS[0]  # Körper oder Features (wie in Fusion: „Objekttyp“)
    body: str = ALL_BODIES
    source_feature: str = ""            # bei „Features“: das Feature, dessen Wirkung wiederholt wird
    compute: str = PATTERN_COMPUTE[0]   # bei „Features“: je Stelle neu berechnen oder Wirkung kopieren
    axis: str = "Z"
    count: float = 6.0
    angle: float = 360.0
    operation: str = OPERATIONS[0]

    KIND = "Kreisförmiges Muster"

    def apply(self, state: BuildState) -> None:
        count = int(round(self.count))
        if count < 2:
            raise FeatureError("Das Muster braucht mindestens zwei Exemplare")
        if count > _PATTERN_LIMIT:
            raise FeatureError(f"Das Muster ist auf {_PATTERN_LIMIT} Exemplare begrenzt")
        if (self.axis not in AXES and self.axis not in state.axes) or not 0 < abs(self.angle) <= 360:
            raise FeatureError("Achse unbekannt oder Winkel außerhalb von 0 bis 360 Grad")
        # Drehachse: eine der Hauptachsen durch den Ursprung oder eine Konstruktionsachse
        point, direction = state.axes.get(self.axis, ((0.0, 0.0, 0.0), AXES.get(self.axis)))
        full = abs(abs(self.angle) - 360) < 1e-9
        step = self.angle / count if full else self.angle / (count - 1)
        transforms = []
        for index in range(1, count):
            rotation = gp_Trsf()
            rotation.SetRotation(gp_Ax1(gp_Pnt(*point), gp_Dir(*direction)), math.radians(index * step))
            transforms.append(rotation)
        _pattern(self, state, transforms)


@dataclass
class Shell(Feature):
    """Höhlt Körper aus. Entfernt werden die gewählten Flächen; ohne Auswahl die Fläche
    auf der angegebenen Seite."""

    body: str = ALL_BODIES
    thickness: float = 2.0
    opening: str = "Oben (+Z)"
    face_points: list = field(default_factory=list)     # Merkpunkte der gewählten Flächen
    direction: str = "Innen"    # wie Fusion: Wand nach innen, nach außen oder beidseitig (siehe SHELL_DIRECTIONS)
    require_pick: bool = False  # neu angelegt: erst mit gewählten Flächen wirksam (ältere Entwürfe: wie früher)

    KIND = "Schale"

    def parameters(self) -> list:
        return [item for item in super().parameters() if item.name != "require_pick"]

    def apply(self, state: BuildState) -> None:
        if self.thickness <= 0:
            raise FeatureError("Die Wandstärke muss größer als 0 sein")
        if self.require_pick and not self.face_points:
            raise FeatureError(ft.PICK_FACES_MESSAGE)
        if self.opening not in OPENINGS:
            raise FeatureError(f"Unbekannte Seite '{self.opening}'")
        if self.direction not in SHELL_DIRECTIONS:
            raise FeatureError(f"Unbekannte Richtung '{self.direction}'")
        direction = OPENINGS[self.opening]
        for name in ft.select_bodies(state, self.body):
            body = state.bodies[name]
            removed = List_TopoDS_Shape()
            if self.face_points:
                for face in ft.find_by_points(ft.faces_of(body), ft.face_point, self.face_points, "Fläche"):
                    removed.Append(face)
            else:
                removed.Append(_outermost_face(body, direction))
            # Eine Wand, die dicker ist als der halbe Körper, kann es nicht geben. Der
            # Geometriekern stürzt bei solchen Werten ab, statt einen Fehler zu melden.
            bounds = _bounds(body)
            if 2 * self.thickness >= min(bounds[i + 3] - bounds[i] for i in range(3)):
                raise FeatureError("Schale nicht möglich – Wandstärke ist für die Geometrie zu groß")
            faces = [removed_face for removed_face in removed]
            if self.direction != SHELL_DIRECTIONS[0]:
                # nach außen (bzw. zur Hälfte): erst um die Wand vergrößern, dann die große Form nach innen aushöhlen
                grow = self.thickness if self.direction == SHELL_DIRECTIONS[1] else self.thickness / 2
                grown = _grown(body, faces, grow)
                if grown is None:
                    raise FeatureError("Schale nach außen nicht möglich – der Körper lässt sich nicht vergrößern")
                body, faces = grown
                removed = List_TopoDS_Shape()
                for face in faces:
                    removed.Append(face)
            result = _shell_by_offset(body, removed, self.thickness) or _shell_by_cavity(
                body, faces, self.thickness)
            if result is None:
                raise FeatureError("Schale nicht möglich – Wandstärke ist für die Geometrie zu groß "
                                   "oder die offene Fläche ist nicht eben")
            state.bodies[name] = result


@dataclass
class Rib(Feature):
    """Rippe: füllt den Raum zwischen den Linien einer Skizze und dem Körper mit einer Wand.

    Jede offene Linie, jeder Bogen oder Spline der Skizze wird in der Skizzenebene quer zu sich bis an den Körper
    verlängert und bekommt die angegebene Dicke (je zur Hälfte auf beiden Seiten der Ebene).
    Die Seite, auf der der Körper liegt, wird selbst gefunden.
    """

    sketch: str = ""
    thickness: float = 3.0

    KIND = "Rippe"

    def apply(self, state: BuildState) -> None:
        if self.sketch not in state.curves or self.sketch not in state.frames:
            raise FeatureError(f"Skizze '{self.sketch}' fehlt oder enthält keine Linien")
        if self.thickness <= 0:
            raise FeatureError("Die Dicke muss größer als 0 sein")
        if not state.bodies:
            raise FeatureError("Für eine Rippe fehlt ein Körper, an den sie anschließt")
        normal = gp_Vec(state.frames[self.sketch].Direction().XYZ())
        bounds = _bounds(state.body)
        reach = 2 * math.dist(bounds[:3], bounds[3:]) + 100.0      # sicher durch das ganze Modell
        ribs = []
        explorer = TopExp_Explorer(state.curves[self.sketch], TopAbs_EDGE)
        while explorer.More():
            edge = TopoDS.Edge(explorer.Current())
            explorer.Next()
            curve = BRepAdaptor_Curve(edge)
            start, end = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
            if start.Distance(end) < 1e-9:
                raise FeatureError("Eine Rippe braucht offene Linien oder Bögen (keinen geschlossenen Kreis)")
            # Bei Bögen und Splines zählt die Sehne als Richtung; angesetzt wird in der Kurvenmitte
            middle = curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2)
            ribs.append(self._piece(state.body, edge, start, end, normal, reach, middle))
        if not ribs:
            raise FeatureError(f"Skizze '{self.sketch}' enthält keine Linien")
        for rib in ribs:
            ft._combine(state, rib, "Vereinigen")

    def _piece(self, body, edge, start: gp_Pnt, end: gp_Pnt, normal: gp_Vec, reach: float, middle: gp_Pnt):
        """Das Rippenstück zu einer Linie oder Kurve: die Wand zwischen ihr und dem Körper."""
        along = gp_Vec(start, end)
        across = normal.Crossed(along)          # in der Skizzenebene, quer zur Linie bzw. Sehne
        across.Normalize()
        best = None
        for sign in (1.0, -1.0):
            # Fläche in der Skizzenebene, um die halbe Dicke zurückgesetzt und dann aufgedickt
            sheet = BRepPrimAPI_MakePrism(edge, across * (sign * reach)).Shape()
            back = gp_Trsf()
            back.SetTranslation(normal * (-self.thickness / 2))
            sheet = BRepBuilderAPI_Transform(sheet, back, True).Shape()
            slab = BRepPrimAPI_MakePrism(sheet, normal * self.thickness).Shape()
            pieces = ft._boolean(BRepAlgoAPI_Cut, slab, body)
            probe = middle.Translated(across * (sign * 1e-3))
            pieces_explorer = TopExp_Explorer(pieces, TopAbs_SOLID)
            while pieces_explorer.More():
                piece = pieces_explorer.Current()
                pieces_explorer.Next()
                if BRepClass3d_SolidClassifier(piece, probe, 1e-7).State() != TopAbs_IN:
                    continue
                # Reicht das Stück an der Linie bis ans Ende der Wand, ist auf dieser Seite kein Körper
                volume = _volume(piece)
                if volume < 0.5 * _volume(slab) and (best is None or volume < best[0]):
                    best = (volume, piece)
        if best is None:
            raise FeatureError("Die Rippe trifft den Körper nicht – die Linie muss zum Körper hin offen sein")
        return best[1]


@dataclass
class Component(Feature):
    """Nimmt einen Körper in eine Komponente auf (Bauteil einer Baugruppe).

    Mehrere Schritte mit demselben Komponentennamen bilden zusammen eine Komponente.
    Werkzeuge mit Körperwahl können danach die ganze Komponente ansprechen, und Gelenke
    bewegen alle ihre Körper gemeinsam.
    """

    body: str = ""
    component: str = ""     # Name der Komponente (leer: der Name dieses Schritts)
    bodies: list = field(default_factory=list)      # weitere Körper derselben Komponente
    parent: str = ft.NO_BASE    # übergeordnete Komponente: macht diese zur Unterbaugruppe

    KIND = "Komponente"

    def apply(self, state: BuildState) -> None:
        name = self.component.strip() or self.name
        if name in state.bodies or name == ALL_BODIES:
            raise FeatureError(f"'{name}' ist schon der Name eines Körpers – bitte einen anderen wählen")
        if self.parent not in ("", ft.NO_BASE):
            # Kein Kreis: die übergeordnete Komponente darf nicht selbst unter dieser hängen
            above, seen = self.parent, set()
            while above and above not in seen:
                if above == name:
                    raise FeatureError("Eine Komponente kann nicht ihre eigene Unterbaugruppe sein")
                seen.add(above)
                above = state.component_parents.get(above, "")
            state.component_parents[name] = self.parent
        for body in dict.fromkeys([self.body, *self.bodies]):
            if body not in state.bodies:
                raise ft.missing_body(body)
            state.components[body] = name


# Farbname -> Rot, Grün, Blau (0 … 1) für das Erscheinungsbild von Körpern
BODY_COLORS = {
    "Grau": (0.62, 0.66, 0.72), "Weiß": (0.92, 0.93, 0.95), "Schwarz": (0.16, 0.17, 0.19),
    "Rot": (0.82, 0.24, 0.22), "Orange": (0.93, 0.52, 0.16), "Gelb": (0.92, 0.80, 0.22),
    "Grün": (0.30, 0.68, 0.36), "Blau": (0.24, 0.48, 0.86), "Violett": (0.58, 0.38, 0.80),
    "Messing": (0.78, 0.63, 0.32), "Kupfer": (0.75, 0.45, 0.30), "Stahl": (0.48, 0.52, 0.58),
}


APPEARANCE_SCOPES = ("Ganzer Körper", "Nur gewählte Flächen")


def nearest_colour(rgb: tuple) -> str:
    """Farbe für einen Farbwert (Rot/Grün/Blau von 0 bis 1): der Name der passenden Programmfarbe oder,
    wenn keine genau passt, der Farbwert selbst als "#rrggbb"."""
    name = min(BODY_COLORS, key=lambda known: math.dist(BODY_COLORS[known], rgb))
    if math.dist(BODY_COLORS[name], rgb) <= 0.01:
        return name
    return "#" + "".join(f"{max(0, min(255, round(part * 255))):02x}" for part in rgb)


def colour_rgb(colour: str) -> tuple | None:
    """Rot/Grün/Blau (0 bis 1) einer Farbe: benannte Programmfarbe oder "#rrggbb"; None, wenn unbekannt."""
    if colour in BODY_COLORS:
        return BODY_COLORS[colour]
    if len(colour) == 7 and colour.startswith("#"):
        try:
            return tuple(int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
        except ValueError:
            return None
    return None


@dataclass
class Appearance(Feature):
    """Erscheinungsbild: färbt Körper (oder eine ganze Komponente) in der Ansicht ein."""

    body: str = ALL_BODIES
    color: str = "Blau"
    # Wie in Fusion gilt die Farbe dem ganzen Körper, wenn man nicht ausdrücklich nur Flächen färbt
    scope: str = APPEARANCE_SCOPES[0]
    face_points: list = field(default_factory=list)     # Merkpunkte der beim Aufruf gewählten Flächen

    KIND = "Farbe"

    def apply(self, state: BuildState) -> None:
        if self.color not in BODY_COLORS:
            raise FeatureError(f"Unbekannte Farbe '{self.color}'")
        if self.scope not in APPEARANCE_SCOPES:
            raise FeatureError(f"Unbekannte Auswahl '{self.scope}'")
        names = ft.select_bodies(state, self.body)
        if self.scope == APPEARANCE_SCOPES[1]:
            if not self.face_points:
                raise FeatureError("Für „nur gewählte Flächen“ muss vor dem Werkzeug eine Fläche angeklickt sein")
            # Nur die gewählten Flächen: sie müssen jetzt vorhanden sein und behalten die Farbe,
            # solange es an dieser Stelle eine Fläche gibt
            name = names[0]
            ft.find_by_points(ft.faces_of(state.bodies[name]), ft.face_point, self.face_points, "Fläche")
            kept = [entry for entry in state.face_colors.get(name, []) if list(entry[0]) not in self.face_points]
            state.face_colors[name] = kept + [(tuple(point), self.color) for point in self.face_points]
            return
        for name in names:
            state.colors[name] = self.color
            state.face_colors.pop(name, None)       # die Körperfarbe überdeckt frühere Flächenfarben


JOINT_KINDS = ("Starr", "Drehgelenk", "Schieber")
JOINT_FACING = ("Flächen gegeneinander", "Flächen gleich gerichtet")


def _joint_frame(body, point: list, kind: str = "face") -> tuple[gp_Pnt, gp_Dir]:
    """Bezugspunkt und Richtung der gemerkten Fläche oder Kante.

    Ebene Fläche: Schwerpunkt und Außennormale; Zylinderfläche: Punkt auf der Achse und
    Achsrichtung; Kreiskante: Mittelpunkt und Achse (z. B. der Rand einer Bohrung);
    gerade Kante: ihre Mitte und ihre Richtung.
    """
    if kind == "vertex":
        # Eckpunkt: nur ein Ort, keine eigene Richtung – ausgerichtet wird an der Hochachse
        from OCP.BRep import BRep_Tool
        found = min((BRep_Tool.Pnt_s(vertex) for vertex in ft.unique_vertices(body)),
                    key=lambda place: place.Distance(gp_Pnt(*point)), default=None)
        if found is None or found.Distance(gp_Pnt(*point)) > ft.SELECTION_TOLERANCE:
            raise FeatureError("Der gewählte Eckpunkt ist nicht mehr vorhanden")
        return found, gp_Dir(0, 0, 1)
    if kind == "edge":
        edge = TopoDS.Edge(ft.find_by_points(ft.unique_edges(body), ft.edge_point, [point], "Kante")[0])
        curve = BRepAdaptor_Curve(edge)
        if curve.GetType() == GeomAbs_Circle:
            circle = curve.Circle()
            return circle.Location(), circle.Axis().Direction()
        if curve.GetType() == GeomAbs_Line:
            return gp_Pnt(*ft.edge_point(edge)), curve.Line().Direction()
        raise FeatureError("Als Bezug eines Gelenks taugen nur gerade Kanten und Kreiskanten")
    face = TopoDS.Face(ft.find_by_points(ft.faces_of(body), ft.face_point, [point], "Fläche")[0])
    surface = BRepAdaptor_Surface(face)
    centre = gp_Pnt(*ft.face_point(face))
    if surface.GetType() == GeomAbs_Plane:
        normal = surface.Plane().Axis().Direction()
        return centre, normal.Reversed() if face.Orientation() == TopAbs_REVERSED else normal
    if surface.GetType() == GeomAbs_Cylinder:
        axis = surface.Cylinder().Axis()
        along = gp_Vec(axis.Location(), centre).Dot(gp_Vec(axis.Direction().XYZ()))
        return axis.Location().Translated(gp_Vec(axis.Direction().XYZ()) * along), axis.Direction()
    raise FeatureError("Ein Gelenk braucht ebene Flächen oder Zylinderflächen")


@dataclass
class Joint(Feature):
    """Gelenk: fügt einen Körper (samt seiner Komponente) an einen anderen.

    Beim Anlegen werden zwei Flächen gewählt: die des zu bewegenden Körpers und die des
    Ziels. Der Körper wird so gedreht und verschoben, dass die Flächen mittig aufeinander
    liegen. `offset` ist der Abstand entlang der Zielrichtung, `angle` die Drehung um sie:
    beim Drehgelenk und Schieber sind das die Bewegungen, die das Gelenk frei lässt.
    """

    body: str = ""          # der Körper, der bewegt wird
    target: str = ""        # der Körper, an den er gefügt wird
    joint: str = JOINT_KINDS[0]
    facing: str = JOINT_FACING[0]
    offset: float = 0.0
    angle: float = 0.0
    face_points: list = field(default_factory=list)     # [Bezug am bewegten Körper, Bezug am Ziel]
    kinds: list = field(default_factory=list)           # je Bezug "face" oder "edge" (leer: zwei Flächen)
    # Grenzen der freien Bewegung: Winkel in Grad (Drehgelenk) bzw. Abstand in mm (Schieber); beide 0 = frei
    minimum: float = 0.0
    maximum: float = 0.0
    # Gelenkursprünge statt der Bezüge (Fusion: Gelenk an einem Gelenkursprung): Name oder leer, je Seite
    origin: str = ""            # am bewegten Körper
    target_origin: str = ""     # am Ziel

    KIND = "Gelenk"

    def limits(self) -> tuple[float, float] | None:
        """Die Bewegungsgrenzen (None, wenn keine gesetzt sind oder das Gelenk starr ist)."""
        if self.joint == JOINT_KINDS[0] or self.maximum <= self.minimum:
            return None
        return self.minimum, self.maximum

    def placement(self, state: BuildState) -> tuple[gp_Trsf, gp_Pnt, gp_Dir]:
        """Die Bewegung des Körpers sowie Bezugspunkt und Richtung der Zielfläche."""
        if self.joint not in JOINT_KINDS or self.facing not in JOINT_FACING:
            raise FeatureError("Gelenkart oder Ausrichtung unbekannt")
        for name in (self.body, self.target):
            if name not in state.bodies:
                raise ft.missing_body(name)
        if self.target in state.moving_with(self.body):
            raise FeatureError("Die beiden Flächen müssen zu verschiedenen Körpern bzw. Komponenten gehören")
        origins = [self.origin if self.origin != ft.NO_BASE else "",
                   self.target_origin if self.target_origin != ft.NO_BASE else ""]
        points = (list(self.face_points) + [[], []])[:2]
        if any(not origins[i] and not points[i] for i in range(2)):
            raise FeatureError("Für ein Gelenk müssen zwei Flächen (oder Gelenkursprünge) gewählt sein")
        limits = self.limits()
        moving = self.angle if self.joint == JOINT_KINDS[1] else self.offset
        if limits is not None and not limits[0] - 1e-9 <= moving <= limits[1] + 1e-9:
            raise FeatureError(f"Das Gelenk steht außerhalb seiner Grenzen ({limits[0]:g} … {limits[1]:g})")
        kinds = self.kinds if len(self.kinds) == 2 else ["face", "face"]
        frames = []
        for side, body in enumerate((self.body, self.target)):
            if origins[side]:
                # Gelenkursprung: gespeicherter Punkt mit Richtung, er muss am richtigen Körper sitzen
                origin = state.joint_origins.get(origins[side])
                if origin is None:
                    raise FeatureError(f"Gelenkursprung '{origins[side]}' ist nicht (mehr) vorhanden")
                if origin["body"] not in state.moving_with(body):
                    raise FeatureError(f"Gelenkursprung '{origins[side]}' gehört nicht zu Körper '{body}'")
                frames.append((gp_Pnt(*origin["point"]), gp_Dir(*origin["normal"])))
            else:
                frames.append(_joint_frame(state.bodies[body], points[side], kinds[side]))
        (own_point, own_normal), (goal_point, goal_normal) = frames
        wanted = goal_normal.Reversed() if self.facing == JOINT_FACING[0] else goal_normal
        if "vertex" in kinds:
            # Punkt auf Punkt (oder Punkt auf Fläche): der Körper wird nur verschoben, nicht gedreht
            wanted = own_normal
        # 1. um den eigenen Bezugspunkt in die gewünschte Richtung drehen
        turn = gp_Trsf()
        if own_normal.IsOpposite(wanted, 1e-9):
            # genau entgegengesetzt: halbe Drehung um irgendeine Achse quer dazu
            helper = gp_Dir(1, 0, 0) if abs(own_normal.X()) < 0.9 else gp_Dir(0, 1, 0)
            turn.SetRotation(gp_Ax1(own_point, own_normal.Crossed(helper)), math.pi)
        elif not own_normal.IsEqual(wanted, 1e-9):
            turn.SetRotation(gp_Ax1(own_point, own_normal.Crossed(wanted)), own_normal.Angle(wanted))
        # 2. auf den Zielpunkt schieben, um den Abstand von der Zielfläche abgerückt
        place = goal_point.Translated(gp_Vec(goal_normal.XYZ()) * self.offset)
        shift = gp_Trsf()
        shift.SetTranslation(gp_Vec(own_point, place))
        # 3. um die Zielrichtung drehen
        spin = gp_Trsf()
        spin.SetRotation(gp_Ax1(goal_point, goal_normal), math.radians(self.angle))
        return spin.Multiplied(shift.Multiplied(turn)), goal_point, goal_normal

    def apply(self, state: BuildState) -> None:
        transform, _point, _normal = self.placement(state)
        for name in state.moving_with(self.body):
            state.move_body(name, transform)        # gefärbte Flächen wandern mit


def section_face(shape, axis: tuple, offset: float):
    """Die Schnittfläche eines Körpers mit der Ebene senkrecht zu `axis` im Abstand `offset` vom Ursprung.

    None, wenn die Ebene den Körper nicht trifft.
    """
    normal = gp_Dir(*axis)
    plane = gp_Pln(gp_Pnt(*(c * offset for c in axis)), normal)
    bounds = _bounds(shape)
    reach = math.dist(bounds[:3], bounds[3:]) + abs(offset) + 10.0
    sheet = BRepBuilderAPI_MakeFace(plane, -reach, reach, -reach, reach).Face()
    try:
        cut = ft._boolean(BRepAlgoAPI_Common, sheet, shape)
    except FeatureError:
        return None
    return cut if TopExp_Explorer(cut, TopAbs_FACE).More() else None


def interferences(state: BuildState, names: list[str] | None = None) -> list[tuple[str, str, float]]:
    """Kollisionen: alle Körperpaare, die sich durchdringen, mit dem gemeinsamen Volumen in mm³.

    Körper derselben Komponente dürfen sich berühren oder überlappen (z. B. Schaft und Kopf) und
    werden nicht gemeldet.
    """
    names = [name for name in (names or list(state.bodies)) if name in state.bodies]
    found = []
    # Hüllquader und Volumen einmal je Körper (nicht je Paar); nach x-Anfang sortiert, damit nur Paare geprüft
    # werden, die sich in x überhaupt überlappen (bei 1000 Körpern sonst eine halbe Million Paare)
    bounds = {name: _bounds(state.bodies[name]) for name in names}
    volumes: dict[str, float] = {}
    ordered = sorted(names, key=lambda name: bounds[name][0])
    rank = {name: number for number, name in enumerate(names)}       # ursprüngliche Reihenfolge der Meldungen
    pairs = []
    for position, first in enumerate(ordered):
        for second in ordered[position + 1:]:
            if bounds[second][0] > bounds[first][3]:
                break           # alle weiteren beginnen noch weiter rechts
            a, b = bounds[first], bounds[second]
            if any(a[i] > b[i + 3] or b[i] > a[i + 3] for i in (1, 2)):
                continue        # die umschließenden Quader treffen sich nicht
            pairs.append((first, second) if rank[first] < rank[second] else (second, first))
    def common_volume(pair: tuple[str, str]) -> float:
        """Volumen der Schnittmenge eines Paars (0, wenn der Kern sie nicht rechnen kann)."""
        try:
            common = ft._boolean(BRepAlgoAPI_Common, state.bodies[pair[0]], state.bodies[pair[1]])
        except FeatureError:
            return 0.0
        return _volume(common) if TopExp_Explorer(common, TopAbs_SOLID).More() else 0.0

    known: dict[tuple[str, str], float] = {}            # ohne Boolesche Rechnung schon bekannte Schnittmengen
    todo = []                                            # Paare, die wirklich gerechnet werden müssen
    for first, second in sorted(pairs, key=lambda pair: (rank[pair[0]], rank[pair[1]])):
        if state.components.get(first) and state.components.get(first) == state.components.get(second):
            continue
        a, b = bounds[first], bounds[second]
        # deckungsgleiche Körper (z. B. mehrfach gespiegelt): gleiche Hülle und gleiches Volumen – die
        # Schnittmenge ist der ganze Körper, ohne die teure Boolesche Rechnung
        for name in (first, second):
            if name not in volumes:
                volumes[name] = _volume(state.bodies[name])
        if max(abs(x - y) for x, y in zip(a, b)) < 1e-6 and abs(volumes[first] - volumes[second]) < 1e-6 * max(volumes[first], 1.0):
            known[(first, second)] = volumes[first]
        else:
            todo.append((first, second))
    # Viele Paare (Zufallstest: Abbruch nach 30 s): die voneinander unabhängigen Schnittmengen auf mehrere Prozesse
    # verteilen; gelingt das nicht, wie bisher der Reihe nach
    from . import guard
    measured = guard.run_parallel(common_volume, todo) if len(todo) >= ft.PARALLEL_BOOLEAN_MIN else None
    if measured is None:
        measured = [common_volume(pair) for pair in todo]
    known.update(zip(todo, measured))
    for pair in sorted(known, key=lambda pair: (rank[pair[0]], rank[pair[1]])):
        if known[pair] > 1e-6:
            found.append((pair[0], pair[1], known[pair]))
    return found


SPLIT_KEEP = ("Beide Teile", "Nur die Seite in Pfeilrichtung", "Nur die Gegenseite")
REVOLVE_EXTENTS = ("Eine Seite", "Symmetrisch", "Zwei Seiten")    # wie Fusions Drehen
SCALE_REFERENCES = ("Körpermitte", "Ursprung")
DRAFT_NEUTRAL = ("Unten (Fuß bleibt)", "Oben (Kopf bleibt)")


@dataclass
class SplitBody(Feature):
    """Teilt Körper an einer Ebene: einer Hauptebene oder der beim Aufruf gewählten Körperfläche,
    jeweils um einen Abstand versetzt. Der Teil in Richtung der Ebenennormale behält den Namen."""

    body: str = ALL_BODIES
    plane: str = "XY"
    offset: float = 0.0
    frame: list = field(default_factory=list)   # Lage der gewählten Fläche (siehe sketch.frame_axes)
    keep: str = SPLIT_KEEP[0]
    base: str = ft.NO_BASE      # Konstruktionsebene als Trennebene (statt Hauptebene/Fläche)
    tool: str = ""              # wie Fusion: ein Körper oder Flächenkörper als Trennwerkzeug (statt der Ebene)
    # Runde 82: einzelne (auch gekrümmte) Flächen eines Körpers oder Flächenkörpers als Trennwerkzeug –
    # Körper bzw. Flächenkörper und die Merkpunkte seiner Flächen
    tool_body: str = ""
    tool_points: list = field(default_factory=list)
    extend: bool = True         # wie Fusions „Trennwerkzeug verlängern“: Flächen über ihre Ränder hinaus fortsetzen

    KIND = "Teilen"

    def apply(self, state: BuildState) -> None:
        if self.keep not in SPLIT_KEEP:
            raise FeatureError(f"Unbekannte Auswahl '{self.keep}'")
        if self.tool_points:
            self._split_by_faces(state)
            return
        if self.tool and self.tool != ft.NO_BASE:
            self._split_by_tool(state)
            return
        axes = sketch.placed_axes(state, self.plane, self.offset, self.frame, self.base)
        normal = axes.Direction()
        face = BRepBuilderAPI_MakeFace(gp_Pln(axes.Location(), normal)).Face()
        # Halbraum auf der Seite, in die die Normale zeigt
        beyond = axes.Location().Translated(gp_Vec(normal.XYZ()) * 1.0)
        half = BRepPrimAPI_MakeHalfSpace(face, beyond).Solid()
        cut_any = False
        for name in ft.select_bodies(state, self.body):
            body = state.bodies[name]
            ahead = ft._boolean(BRepAlgoAPI_Common, body, half)
            behind = ft._boolean(BRepAlgoAPI_Cut, body, half)
            if not (TopExp_Explorer(ahead, TopAbs_SOLID).More() and TopExp_Explorer(behind, TopAbs_SOLID).More()):
                continue        # die Ebene läuft an diesem Körper vorbei
            cut_any = True
            # Jedes getrennte Stück wird ein eigener Körper (wie Fusion) – auch wenn eine Seite der Ebene aus
            # mehreren Teilen besteht (z. B. Wände und ein Einbau, wenn der Boden abgetrennt wird)
            if self.keep == SPLIT_KEEP[2]:
                ft.store_pieces(state, name, behind)
            else:
                ft.store_pieces(state, name, ahead)
                if self.keep == SPLIT_KEEP[0]:
                    ft.store_pieces(state, state.add_body(behind), behind)
        if not cut_any:
            raise FeatureError("Die Trennebene schneidet keinen der Körper")

    def _split_by_tool(self, state: BuildState) -> None:
        """Teilen mit einem Werkzeugkörper (dessen Flächen trennen) oder Flächenkörper; alle Teile werden Körper.
        Das Werkzeug selbst bleibt stehen (wie in Fusion)."""
        tool = state.bodies.get(self.tool) if self.tool in state.bodies else state.surfaces.get(self.tool)
        if tool is None:
            raise ft.missing_body(self.tool)
        targets = [name for name in ft.select_bodies(state, self.body) if name != self.tool]
        if self._split_bodies(state, targets, [tool]):
            return
        if self.extend and self.tool in state.surfaces:
            # Ein Flächenkörper, der nicht ganz durch den Körper reicht, trennt erst verlängert (Runde 82; vorher
            # war das ein Fehler – bestehende Entwürfe rechnen also wie bisher)
            reach = _reach(state, targets, tool)
            if self._split_bodies(state, targets, [extended_face(face, reach) for face in ft.faces_of(tool)]):
                return
        raise FeatureError("Das Trennwerkzeug teilt keinen der Körper")

    def _split_by_faces(self, state: BuildState) -> None:
        """Teilen an gewählten Flächen eines Körpers oder Flächenkörpers (eben oder gekrümmt). Verlängert reicht
        die Fläche über ihre Ränder hinaus (Ebene unbegrenzt, Zylinder ringsum und der Länge nach) – so trennt z. B.
        die Mantelfläche eines Bolzens die Platte, durch die er steckt."""
        source = state.bodies.get(self.tool_body)
        if source is None:
            source = state.surfaces.get(self.tool_body)
        if source is None:
            raise FeatureError("Der Körper mit der Trennfläche ist nicht (mehr) vorhanden – die Fläche neu "
                               "anklicken") if self.tool_body else FeatureError("Eine Trennfläche anklicken")
        faces = ft.find_by_points(ft.faces_of(source), ft.face_point, self.tool_points, "Fläche")
        targets = ft.select_bodies(state, self.body)
        if self.extend:
            reach = _reach(state, targets, source)
            tools = [extended_face(face, reach) for face in faces]
        else:
            tools = list(faces)
        if not self._split_bodies(state, targets, tools):
            hint = "" if self.extend else " – „Trennwerkzeug verlängern“ einschalten?"
            raise FeatureError("Die Trennfläche teilt keinen der Körper" + hint)

    def _split_bodies(self, state: BuildState, targets: list[str], tools: list) -> bool:
        """Zerteilt jeden Zielkörper mit den Werkzeugformen; das erste Stück behält den Namen, die übrigen werden
        neue Körper. „Behalten“ wirkt hier nicht (ohne Ebene gibt es keine Pfeilrichtung). True, wenn etwas
        geteilt wurde."""
        split_any = False
        for name in targets:
            pieces = _split_pieces(state.bodies[name], tools)
            if len(pieces) < 2:
                continue            # das Werkzeug trennt diesen Körper nicht
            split_any = True
            state.bodies[name] = pieces[0]
            for piece in pieces[1:]:
                state.add_body(piece)
        return split_any


def _split_pieces(body: TopoDS_Shape, tools: list) -> list:
    """Die Volumenstücke, in die die Werkzeugformen (Körper, Flächen) den Körper zerlegen (leer bei Fehlschlag)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    splitter = BRepAlgoAPI_Splitter()
    arguments, tool_list = List_TopoDS_Shape(), List_TopoDS_Shape()
    arguments.Append(body)
    for tool in tools:
        tool_list.Append(tool)
    splitter.SetArguments(arguments)
    splitter.SetTools(tool_list)
    splitter.Build()
    if not splitter.IsDone():
        return []
    pieces = []
    explorer = TopExp_Explorer(splitter.Shape(), TopAbs_SOLID)
    while explorer.More():
        pieces.append(explorer.Current())
        explorer.Next()
    return pieces


def _reach(state: BuildState, targets: list[str], tool: TopoDS_Shape) -> float:
    """Wie weit eine Trennfläche verlängert werden muss: über alle Zielkörper und das Werkzeug hinweg."""
    reach = 10.0
    for shape in [*(state.bodies[name] for name in targets), tool]:
        bounds = _bounds(shape)
        reach += 2 * math.dist(bounds[:3], bounds[3:])
    return reach


def extended_face(face, reach: float):
    """Die Fläche über ihre Ränder hinaus fortgesetzt (wie Fusions „verlängern“): Ebenen, Zylinder, Kegel … haben
    unbegrenzte Parameter und werden um `reach` erweitert, geschlossene Richtungen (ringsum) ganz genommen;
    Freiformflächen enden an den Grenzen ihrer Fläche."""
    surface = BRep_Tool.Surface_s(TopoDS.Face(face))
    u0, u1, v0, v1 = surface.Bounds()
    fu0, fu1, fv0, fv1 = BRepTools.UVBounds_s(TopoDS.Face(face))
    limit = lambda value, fallback: value if abs(value) < 1e8 else fallback
    return BRepBuilderAPI_MakeFace(surface, limit(u0, fu0 - reach), limit(u1, fu1 + reach),
                                   limit(v0, fv0 - reach), limit(v1, fv1 + reach), 1e-6).Face()


@dataclass
class ScaleBody(Feature):
    """Vergrößert oder verkleinert Körper gleichmäßig um einen Faktor."""

    body: str = ALL_BODIES
    factor: float = 1.5
    reference: str = SCALE_REFERENCES[0]
    # Abweichende Faktoren für Y und Z (0 = wie der Faktor oben, also gleichmäßig)
    factor_y: float = 0.0
    factor_z: float = 0.0

    KIND = "Skalieren"

    def apply(self, state: BuildState) -> None:
        if self.factor <= 0 or self.factor_y < 0 or self.factor_z < 0:
            raise FeatureError("Die Faktoren müssen größer als 0 sein (0 bei Y/Z heißt: wie X)")
        factors = (self.factor, self.factor_y or self.factor, self.factor_z or self.factor)
        if self.reference not in SCALE_REFERENCES:
            raise FeatureError(f"Unbekannter Bezug '{self.reference}'")
        for name in ft.select_bodies(state, self.body):
            body = state.bodies[name]
            centre = gp_Pnt(0, 0, 0)
            if self.reference == SCALE_REFERENCES[0]:
                bounds = _bounds(body)
                centre = gp_Pnt(*((bounds[i] + bounds[i + 3]) / 2 for i in range(3)))
            if factors[0] == factors[1] == factors[2]:
                scaling = gp_Trsf()
                scaling.SetScale(centre, self.factor)
                state.move_body(name, scaling)
                continue
            # Je Achse verschieden: allgemeine Abbildung, die Flächen werden dabei zu Freiformflächen
            stretch = gp_GTrsf()
            for axis, value in enumerate(factors, start=1):
                stretch.SetValue(axis, axis, value)
            origin = (centre.X(), centre.Y(), centre.Z())
            stretch.SetTranslationPart(gp_XYZ(*(origin[i] * (1 - factors[i]) for i in range(3))))
            state.bodies[name] = BRepBuilderAPI_GTransform(body, stretch, True).Shape()


def offset_faces(body: TopoDS_Shape, faces: list, distance: float) -> TopoDS_Shape:
    """Versetzt Flächen eines Körpers um `distance` längs ihrer Normalen; die Nachbarflächen laufen mit (wie Fusions
    „Flächen versetzen“, auch Drücken/Ziehen an gekrümmten Flächen). Positiv = nach außen."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    try:
        # Auf einer Kopie rechnen: der Versatz ändert sonst Toleranzen im übergebenen Körper selbst (der als
        # Zwischenstand der Timeline weiterlebt). Die gewählten Flächen werden in der Kopie wiedergefunden.
        copier = BRepBuilderAPI_Copy(body)
        body = copier.Shape()
        faces = [copier.ModifiedShape(face) for face in faces]
        maker = BRepOffset_MakeOffset()
        maker.Initialize(body, 0.0, 1e-4, BRepOffset_Skin, False, False, GeomAbs_Intersection)
        for face in faces:
            maker.SetOffsetOnFace(TopoDS.Face(face), distance)
        maker.MakeOffsetShape()
        result = maker.Shape() if maker.IsDone() else None
        if result is not None and not TopExp_Explorer(result, TopAbs_SOLID).More():
            # der Versatz liefert nur die Hülle: daraus wieder einen Volumenkörper machen
            shells = TopExp_Explorer(result, TopAbs_SHELL)
            result = _as_solid(shells.Current()) if shells.More() else None
    except Exception as error:
        raise FeatureError("Diese Fläche ließ sich nicht um den Abstand versetzen") from error
    if result is None or result.IsNull() or not TopExp_Explorer(result, TopAbs_SOLID).More():
        raise FeatureError("Diese Fläche ließ sich nicht um den Abstand versetzen")
    if _volume(result) <= 0:
        raise FeatureError("Der Versatz ergibt keinen gültigen Körper (Abstand zu groß?)")
    return result


def remove_faces(body: TopoDS_Shape, faces: list) -> TopoDS_Shape:
    """Entfernt Flächen und schließt die Lücke mit den Nachbarflächen (wie Fusions „Fläche löschen“)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
    from . import guard
    remover = BRepAlgoAPI_Defeaturing()
    remover.SetShape(body)
    for face in faces:
        remover.AddFaceToRemove(face)
    remover.SetRunParallel(not guard.in_child())     # im Probelauf-Kind seriell (sonst Verklemmen)
    remover.Build()
    result = remover.Shape() if remover.IsDone() else None
    if result is None or result.IsNull() or not BRepCheck_Analyzer(result).IsValid() \
            or not TopExp_Explorer(result, TopAbs_SOLID).More():
        raise FeatureError("Diese Flächen lassen sich nicht entfernen – die Nachbarflächen schließen die "
                           "Lücke nicht (z. B. eine Außenfläche eines Quaders)")
    if len(ft.faces_of(result)) == len(ft.faces_of(body)) \
            and abs(_volume(result) - _volume(body)) <= 1e-7 * max(_volume(body), 1.0):
        # der Kern hat die Lücke mit derselben Fläche wieder geschlossen: es wurde nichts entfernt
        raise FeatureError("Diese Flächen lassen sich nicht entfernen – die Nachbarflächen schließen die "
                           "Lücke nicht (z. B. eine Außenfläche eines Quaders)")
    return result


@dataclass
class PressPull(Feature):
    """Drücken/Ziehen wie in Fusion: zieht Körperflächen heraus (+) oder drückt sie in den Körper (−).

    Ebene Flächen werden längs ihrer Normale verschoben; gekrümmte (z. B. Bohrungswände) werden versetzt.
    Dabei kommt Material hinzu oder fällt weg.
    """

    body: str = ALL_BODIES
    distance: float = 5.0
    face_points: list = field(default_factory=list)     # Merkpunkte der beim Aufruf gewählten Flächen

    KIND = "Drücken-Ziehen"

    def apply(self, state: BuildState) -> None:
        if not self.face_points:
            raise FeatureError("Für Drücken/Ziehen muss vor dem Werkzeug eine Fläche angeklickt sein")
        if abs(self.distance) < 1e-9:
            raise FeatureError("Der Abstand darf nicht 0 sein")
        for name in ft.select_bodies(state, self.body):
            body = state.bodies[name]
            faces = ft.find_by_points(ft.faces_of(body), ft.face_point, self.face_points, "Fläche")
            result = press_pull(body, faces, self.distance)
            if not TopExp_Explorer(result, TopAbs_SOLID).More():
                raise FeatureError("Drücken/Ziehen würde den Körper vollständig entfernen")
            state.bodies[name] = ft.unified(result)


def _face_area(face) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return props.Mass()


def _plausible_offset(body, result, faces, distance: float, strict: bool) -> bool:
    """Ist das Ergebnis eines Flächenversatzes glaubhaft? Gültiger Volumenkörper, Volumen ändert sich in die
    richtige Richtung und – bei `strict` – ungefähr um Fläche × Abstand. Der Versatz des Geometriekerns liefert an
    verwinkelten Körpern sonst gern Unsinn (negative Riesenvolumen, Nutzerentwurf 2026-10-26)."""
    if result is None or result.IsNull() or not TopExp_Explorer(result, TopAbs_SOLID).More() \
            or not ft.is_valid(result):
        return False
    change = _volume(result) - _volume(body)
    expected = sum(_face_area(face) for face in faces) * distance
    if change * distance <= 0:
        return False                    # gezogen, aber kleiner geworden (oder umgekehrt)
    if strict:
        # gekrümmte Flächen ändern beim Versatz ihre Größe (ein Loch wird enger): großzügige Spanne
        return 0.4 * abs(expected) <= abs(change) <= 2.5 * abs(expected) + 1e-6
    return True


def _offset_closed_form(body, faces, distance: float):
    """Versatz über die Form, die die Flächen umschließen (Bohrung, Tasche, Zapfen …): Flächen entfernen (Lücke
    schließt sich), die umschlossene Form für sich versetzen – an einem einfachen Körper klappt das zuverlässig –
    und wieder einsetzen. None, wenn die Flächen keine geschlossene Form bilden oder der Versatz scheitert."""
    filled = _without_faces(body, faces)
    if filled is None:
        return None
    points = [list(ft.face_point(face)) for face in faces]
    hollow = ft._boolean(BRepAlgoAPI_Cut, filled, body)      # z. B. das Loch als Volumen
    solid = ft._boolean(BRepAlgoAPI_Cut, body, filled)       # z. B. ein Zapfen
    try:
        if _volume(hollow) > 1e-9:
            # die Flächen sind Innenflächen der Form: aus Sicht der Form zeigen sie in die Gegenrichtung
            inner = ft.find_by_points(ft.faces_of(hollow), ft.face_point, points, "Fläche")
            return ft._boolean(BRepAlgoAPI_Cut, filled, offset_faces(hollow, inner, -distance))
        if _volume(solid) > 1e-9:
            outer = ft.find_by_points(ft.faces_of(solid), ft.face_point, points, "Fläche")
            return ft._boolean(BRepAlgoAPI_Fuse, filled, offset_faces(solid, outer, distance))
    except FeatureError:
        return None
    return None


def _face_slab(face, distance: float):
    """Der Körper zwischen einer (auch gekrümmten) Fläche und ihrer um `distance` versetzten Kopie (Aufdicken einer
    einzelnen Fläche – das gelingt dem Geometriekern auch dort, wo der Versatz am ganzen Körper scheitert)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    # auf einer Kopie arbeiten: der Versatz passt sonst Toleranzen der geteilten Kanten im Körper selbst an – das
    # verdarb in Versuchen nachfolgende Schritte am selben Körper (ungültige Ergebnisse)
    face = TopoDS.Face(BRepBuilderAPI_Copy(face).Shape())
    single = BRepOffset_MakeOffset()
    single.Initialize(face, distance, 1e-6, BRepOffset_Skin, False, False, GeomAbs_Intersection, True)
    single.MakeOffsetShape()
    if not single.IsDone():
        return None
    slab = single.Shape()
    if abs(_volume(slab)) <= 1e-9:
        return None
    if _volume(slab) < 0:
        slab.Reverse()
    return slab


def _offset_by_slabs(body, faces, distance: float):
    """Gekrümmtes Gegenstück zum Prisma: je Fläche die Scheibe zwischen Fläche und Versatz anfügen (nach außen)
    bzw. herausschneiden (nach innen). Die Ränder der Scheibe stehen senkrecht auf der Fläche – genau richtig, wenn
    die Nachbarflächen senkrecht anschließen (Wand einer Tasche oder Bohrung zwischen Boden und Deckel)."""
    result = body
    try:
        for face in faces:
            slab = _outward_slab(body, TopoDS.Face(face), distance)
            if slab is None:
                return None
            result = ft._boolean(BRepAlgoAPI_Fuse if distance > 0 else BRepAlgoAPI_Cut, result, slab)
    except Exception:
        return None
    return result


def _outward_slab(body, face, distance: float):
    """Die Scheibe auf der richtigen Seite der Fläche: nach außen (distance > 0) die, die außerhalb des Körpers
    liegt, nach innen die im Körper. Welche Seite der Kern „vorn“ nennt, hängt von der Lage der Flächenachsen ab
    (eine Bohrungswand kann ihre Normale zur Achse oder von ihr weg haben) – deshalb wird nachgemessen statt
    geraten. Scheiben, deren Volumen nicht etwa Fläche × Abstand ist, sind überschlagen (Abstand größer als der
    Krümmungsradius, z. B. eine Bohrung zuziehen über ihre Achse hinaus): dann gibt es keine Scheibe."""
    expected = _face_area(face) * abs(distance)
    best, best_inside = None, None
    for side in (abs(distance), -abs(distance)):
        slab = _face_slab(face, side)
        if slab is None or not 0.3 * expected <= _volume(slab) <= 3.0 * expected + 1e-6:
            continue
        inside = _volume(ft._boolean(BRepAlgoAPI_Common, body, slab)) / _volume(slab)
        # nach außen: möglichst wenig im Körper; nach innen: möglichst viel
        score = inside if distance > 0 else 1.0 - inside
        if best is None or score < best_inside:
            best, best_inside = slab, score
    if best is None or best_inside > 0.5:
        return None
    return best


def _check_offset_radius(face, distance: float) -> None:
    """Ein Zylinder- oder Kugelstück lässt sich zu seiner Achse bzw. Mitte hin höchstens um seinen Radius versetzen
    (eine Bohrung Ø 10 kann um weniger als 5 mm enger werden). Weiter gibt es keine Fläche mehr – Fusion meldet
    dann einen Fehler; der Kern rechnete hier teils trotzdem etwas Glaubhaftes aus."""
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Sphere
    surface = BRepAdaptor_Surface(face)
    kind = surface.GetType()
    if kind not in (GeomAbs_Cylinder, GeomAbs_Sphere):
        return
    point, normal = face_middle(face)
    if kind == GeomAbs_Cylinder:
        cylinder = surface.Cylinder()
        axis, origin, radius = cylinder.Axis().Direction(), cylinder.Location(), cylinder.Radius()
        offset = gp_Vec(origin, gp_Pnt(*point))
        radial = offset - gp_Vec(axis.XYZ()) * offset.Dot(gp_Vec(axis.XYZ()))
    else:
        sphere = surface.Sphere()
        radius = sphere.Radius()
        radial = gp_Vec(sphere.Location(), gp_Pnt(*point))
    if radial.Magnitude() < 1e-9:
        return
    # neue Krümmung: zeigt die Außennormale von der Achse weg, wächst der Radius beim Herausziehen
    toward = gp_Vec(*normal).Dot(radial.Normalized())
    if radius + distance * toward <= 1e-6:
        raise FeatureError(f"Diese runde Fläche lässt sich höchstens um {radius:.2f} mm zu ihrer Mitte hin "
                           "versetzen – der Abstand ist zu groß")


def _is_blend(body, faces) -> bool:
    """Ist eine der Flächen eine Rundung, d. h. schließt sie über eine Kante glatt (tangential) an eine Nachbarfläche
    an? Nahtkanten einer Fläche mit sich selbst zählen nicht."""
    sharp_edges = ft._sharp_edges(body)
    for face in faces:
        for edge in ft.unique_edges(face):
            if BRep_Tool.IsClosed_s(TopoDS.Edge(edge), TopoDS.Face(face)):
                continue                    # Naht der Fläche mit sich selbst
            if not any(edge.IsSame(other) for other in sharp_edges):
                return True
    return False


def press_pull(body, faces: list, distance: float):
    """Drücken/Ziehen wie in Fusion: die Flächen wandern um `distance` längs ihrer Normalen (+ nach außen), die
    Nachbarflächen laufen mit – auch an Ecken, wo eine Nachbarfläche schräg steht (Nutzervideo 2026-10-26: Ecken
    blieben offen). Es klappt ganz oder gar nicht: nichts Glaubhaftes gefunden → Fehler, Körper unverändert.

    Wege nacheinander, der erste glaubhafte gewinnt:
      1. Versatz des Geometriekerns am ganzen Körper (Nachbarn verlängert, wie Fusion)
      2. ebene Flächen: Prisma über der Fläche anfügen bzw. herausschneiden (Nachbarn senkrecht = gleiches Ergebnis)
      3. umschlossene Formen (Bohrung, Zapfen): die Form für sich versetzen und wieder einsetzen
      4. gekrümmte Flächen: Scheibe zwischen Fläche und Versatz anfügen bzw. herausschneiden
    """
    planar = all(sketch.face_frame(face) is not None for face in faces)
    for face in faces:
        _check_offset_radius(face, distance)
    try:
        result = offset_faces(body, faces, distance)
    except FeatureError:
        result = None
    # am ganzen Körper nur streng geprüft übernehmen; bei ebenen Flächen liefert das Prisma sonst dasselbe sicherer
    if _plausible_offset(body, result, faces, distance, strict=True):
        return result
    if planar:
        result = body
        for face in faces:
            frame = sketch.face_frame(face)
            # Prisma über der Fläche: nach außen wird es angefügt, nach innen herausgeschnitten
            prism = BRepPrimAPI_MakePrism(face, gp_Vec(*frame[3:6]) * distance).Shape()
            result = ft._boolean(BRepAlgoAPI_Fuse if distance > 0 else BRepAlgoAPI_Cut, result, prism)
        if not TopExp_Explorer(result, TopAbs_SOLID).More():
            raise FeatureError("Drücken/Ziehen würde den Körper vollständig entfernen")
        return result
    # nur für Bohrungen, Taschen, Zapfen: eine Rundung (tangential an ihre Nachbarn) umschließt keine Form –
    # dort „schließt“ das Entfernen nur die Ecke, und der Versatz der Ecke ergab viel zu viel Material
    result = None if _is_blend(body, faces) else _offset_closed_form(body, faces, distance)
    # streng prüfen: an einer Eckrundung „schließt“ das Entfernen der Fläche nur die Ecke, und der Versatz dieser
    # Ecke ergab das Achtfache der erwarteten Menge (Nutzerentwurf, Rundung r = 3 mm, +1 mm → +780 statt ≈ 90 mm³)
    if _plausible_offset(body, result, faces, distance, strict=True):
        return result
    result = _offset_by_slabs(body, faces, distance)
    if _plausible_offset(body, result, faces, distance, strict=False):
        return result
    raise FeatureError("Diese Fläche lässt sich nicht um den Abstand versetzen – der Abstand ist zu groß für die "
                       "umgebende Form oder die Nachbarflächen lassen sich nicht verlängern")


@dataclass
class Draft(Feature):
    """Entformungsschräge: neigt Seitenflächen um einen Winkel gegen die Entformungsrichtung.

    Ohne Auswahl werden alle ebenen Flächen geneigt, die parallel zur Richtung stehen.
    Die neutrale Ebene (dort bleibt der Umriss, wie er ist) liegt am Fuß oder am Kopf des Körpers.
    """

    body: str = ALL_BODIES
    angle: float = 3.0
    axis: str = "Z"
    neutral: str = DRAFT_NEUTRAL[0]
    face_points: list = field(default_factory=list)     # Merkpunkte der gewählten Flächen

    require_pick: bool = False  # neu angelegt: erst mit gewählten Flächen wirksam

    KIND = "Schräge"

    def parameters(self) -> list:
        return [item for item in super().parameters() if item.name != "require_pick"]

    def apply(self, state: BuildState) -> None:
        if self.require_pick and not self.face_points:
            raise FeatureError(ft.PICK_FACES_MESSAGE)
        if self.axis not in AXES or self.neutral not in DRAFT_NEUTRAL:
            raise FeatureError("Achse oder neutrale Ebene unbekannt")
        if not 0 < abs(self.angle) < 60:
            raise FeatureError("Der Winkel muss zwischen 0 und 60 Grad liegen")
        pull = gp_Dir(*AXES[self.axis])
        index = "XYZ".index(self.axis)
        names = ft.select_bodies(state, self.body)
        if self.face_points:
            names = names[:1]       # gewählte Flächen gehören zu einem Körper
        for name in names:
            body = state.bodies[name]
            bounds = _bounds(body)
            height = bounds[index] if self.neutral == DRAFT_NEUTRAL[0] else bounds[index + 3]
            origin = [0.0, 0.0, 0.0]
            origin[index] = height
            neutral = gp_Pln(gp_Pnt(*origin), pull)
            if self.face_points:
                faces = ft.find_by_points(ft.faces_of(body), ft.face_point, self.face_points, "Fläche")
            else:
                faces = [face for face in ft.faces_of(body) if _stands_along(face, pull)]
            if not faces:
                raise FeatureError("Es gibt keine Fläche, die sich in dieser Richtung neigen lässt")
            # Die Flächen kippen um ihre Schnittlinie mit der neutralen Ebene: mit dem Fuß als
            # neutraler Ebene wird der Kopf schmaler, mit dem Kopf der Fuß breiter
            angle = math.radians(self.angle)

            def drafted(chosen: list):
                """Körper mit diesen Wänden geneigt; None, wenn der Kern das nicht schafft."""
                try:
                    maker = BRepOffsetAPI_DraftAngle(body)
                    for face in chosen:
                        maker.Add(TopoDS.Face(face), pull, angle, neutral)
                        if not maker.AddDone():
                            return None
                    maker.Build()
                    shape = maker.Shape() if maker.IsDone() else None
                except Exception:
                    return None
                return shape if shape is not None and BRepCheck_Analyzer(shape).IsValid() else None

            if self.face_points:
                result = drafted(faces)
                if result is None:
                    raise FeatureError("Die Schräge ließ sich mit diesem Winkel nicht bilden "
                                       "(nur ebene Flächen und Zylinder; angrenzende Rundungen stören)")
            else:
                # ohne Auswahl („alle Seitenwände“): erst alle auf einmal; geht das nicht (z. B. Wände neben einer
                # Rundung), Wand für Wand dazunehmen und nur behalten, was der Kern noch neigen kann
                result = drafted(faces)
                if result is None:
                    accepted = []
                    for face in faces:
                        if drafted([*accepted, face]) is not None:
                            accepted.append(face)
                    result = drafted(accepted) if accepted else None
                if result is None:
                    raise FeatureError("Keine der Seitenwände lässt sich neigen (z. B. wegen angrenzender Rundungen)")
            state.bodies[name] = result


def _stands_along(face, direction: gp_Dir) -> bool:
    """Ist die Fläche eben und steht sie parallel zur Richtung (also eine „Seitenwand“)?"""
    surface = BRepAdaptor_Surface(TopoDS.Face(face))
    return surface.GetType() == GeomAbs_Plane and abs(surface.Plane().Axis().Direction().Dot(direction)) < 1e-6


@dataclass
class ImportedStep(Feature):
    """Übernimmt die Volumenkörper einer STEP-Datei als eigene, weiter bearbeitbare Körper.

    Gemerkt wird nur der Pfad; die Datei muss beim Öffnen des Entwurfs noch vorhanden sein.
    """

    source: str = ""        # Pfad der STEP-Datei

    KIND = "STEP-Import"

    def apply(self, state: BuildState) -> None:
        try:
            solids = exchange.read_step(self.source)
        except exchange.ExchangeError as error:
            raise FeatureError(str(error)) from error
        # Farben aus der Datei übernehmen; das Programm kennt feste Farben, also jeweils die ähnlichste
        for solid, (body_colour, painted) in zip(solids, exchange.step_colours(self.source)):
            name = state.add_body(solid)
            if body_colour is not None:
                state.colors[name] = nearest_colour(body_colour)
            if painted:
                state.face_colors[name] = [(ft.face_point(face), nearest_colour(colour)) for face, colour in painted]


# Regelgewinde nach ISO: Nenndurchmesser -> Steigung (mm)
ISO_PITCHES = {1: 0.25, 1.2: 0.25, 1.6: 0.35, 2: 0.4, 2.5: 0.45, 3: 0.5, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25,
               10: 1.5, 12: 1.75, 14: 2.0, 16: 2.0, 20: 2.5, 24: 3.0, 30: 3.5, 36: 4.0, 42: 4.5, 48: 5.0,
               56: 5.5, 64: 6.0}
_THREAD_TURNS_LIMIT = 150       # mehr Gänge rechnet der Geometriekern nicht in vertretbarer Zeit


def _thread_label(nominal: float, pitch: float) -> str:
    return f"M{nominal:g} × {pitch:g}".replace(".", ",")


# Wählbare Normgrößen: Regelgewinde und gängige Feingewinde -> Steigung in mm
THREAD_PITCHES = {_thread_label(nominal, pitch): pitch for nominal, pitch in ISO_PITCHES.items()}
THREAD_PITCHES.update({_thread_label(nominal, pitch): pitch for nominal, pitch in
                       ((8, 1.0), (10, 1.0), (10, 1.25), (12, 1.25), (12, 1.5), (16, 1.5), (20, 1.5), (24, 2.0))})
THREAD_SIZES = ("Automatisch (nach Durchmesser)", *sorted(THREAD_PITCHES, key=lambda label: (
    float(label[1:].split(" × ")[0].replace(",", ".")), -THREAD_PITCHES[label])))


TAP_OPTIONS = ("Ohne Gewinde", *THREAD_SIZES)     # Wahl bei der Bohrung: ohne, automatisch oder eine Normgröße


def iso_pitch(diameter: float) -> float:
    """Steigung des Regelgewindes, dessen Nenndurchmesser dem Durchmesser am nächsten liegt."""
    return ISO_PITCHES[min(ISO_PITCHES, key=lambda nominal: abs(nominal - diameter))]


@dataclass
class Thread(Feature):
    """Schneidet ein Gewinde in eine Zylinderfläche: außen auf einem Bolzen oder innen in einer Bohrung.

    Die Gewinderille wird wirklich modelliert (Dreiecksprofil entlang einer Schraubenlinie).
    Steigung 0 wählt das ISO-Regelgewinde zum Durchmesser; Länge 0 bedeutet die ganze Fläche.
    """

    body: str = ALL_BODIES
    pitch: float = 0.0
    length: float = 0.0
    face_points: list = field(default_factory=list)     # Merkpunkt der gewählten Zylinderfläche
    size: str = THREAD_SIZES[0]     # Normgröße; sie legt die Steigung fest, wenn keine eigene eingetragen ist

    KIND = "Gewinde"

    def chosen_pitch(self, diameter: float) -> float:
        """Die Steigung: eigene Angabe, sonst die der gewählten Normgröße, sonst das Regelgewinde zum Durchmesser."""
        if self.pitch > 0:
            return self.pitch
        if self.size in THREAD_PITCHES:
            return THREAD_PITCHES[self.size]
        return iso_pitch(diameter)

    def apply(self, state: BuildState) -> None:
        if self.pitch < 0 or self.length < 0:
            raise FeatureError("Steigung und Länge dürfen nicht negativ sein")
        if self.size != THREAD_SIZES[0] and self.size not in THREAD_PITCHES:
            raise FeatureError(f"Unbekannte Gewindegröße '{self.size}'")
        name = ft.select_bodies(state, self.body)[0]
        body = state.bodies[name]
        face = self._cylinder_face(body)
        surface = BRepAdaptor_Surface(face)
        cylinder = surface.Cylinder()
        radius = cylinder.Radius()
        pitch = self.chosen_pitch(2 * radius)
        inside = face.Orientation() == TopAbs_REVERSED         # Bohrung: das Material liegt außen
        depth = pitch * (0.5413 if inside else 0.6134)          # Gewindetiefe nach ISO
        if not inside and depth >= radius * 0.9:
            raise FeatureError("Die Steigung ist für diesen Durchmesser zu groß")
        # Bereich der Fläche entlang der Zylinderachse; die Rille läuft an freien Enden über die Fläche hinaus
        low, high = sorted((surface.FirstVParameter(), surface.LastVParameter()))
        start, end = low - pitch, high + pitch
        if 0 < self.length < high - low:
            end = low + self.length
        turns = (end - start) / pitch
        if turns > _THREAD_TURNS_LIMIT:
            raise FeatureError(f"Zu viele Gewindegänge ({turns:.0f}) – Steigung vergrößern oder Länge begrenzen")
        targets = List_TopoDS_Shape()
        targets.Append(body)
        cut = BRepAlgoAPI_Cut()
        cut.SetArguments(targets)
        # endet das Gewinde vor dem Ende der Fläche, läuft die Rille dort aus
        fades = 0 < self.length < high - low
        cut.SetTools(_thread_groove(cylinder.Position(), radius, pitch, depth, start, end, inside, fades))
        cut.SetRunParallel(True)        # viele kleine Stücke: parallel deutlich schneller
        cut.Build()
        result = cut.Shape() if cut.IsDone() else None
        # Der Schnitt muss Material abgetragen haben, ohne den Körper zu zerstören
        before = _volume(body)
        if (result is None or result.IsNull() or not BRepCheck_Analyzer(result).IsValid()
                or not 0.3 * before < _volume(result) < before * (1 - 1e-6)):
            raise FeatureError("Das Gewinde ließ sich an dieser Fläche nicht schneiden")
        state.bodies[name] = result

    def _cylinder_face(self, body):
        """Die gewählte Zylinderfläche; ohne Auswahl die größte Zylinderfläche des Körpers."""
        faces = ft.faces_of(body)
        if self.face_points:
            chosen = ft.find_by_points(faces, ft.face_point, self.face_points[:1], "Fläche")
        else:
            round_faces = [f for f in faces if BRepAdaptor_Surface(TopoDS.Face(f)).GetType() == GeomAbs_Cylinder]
            chosen = sorted(round_faces, key=_area, reverse=True)[:1]
        if not chosen or BRepAdaptor_Surface(TopoDS.Face(chosen[0])).GetType() != GeomAbs_Cylinder:
            raise FeatureError("Für das Gewinde muss eine Zylinderfläche gewählt sein")
        return TopoDS.Face(chosen[0])


def _volume(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def _area(face) -> float:
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, properties)
    return properties.Mass()


_THREAD_PIECES_PER_TURN = 2     # die Rille wird in Stücken von einer halben Windung gebaut


def _helix_band(position, radius: float, pitch: float, low: float, high: float, start: float, width: float):
    """Geschlossenes Band der Breite `width` auf der Zylinderfläche, das der Schraubenlinie folgt.

    `low`/`high` sind die Drehwinkel (Bogenmaß) an den Enden; beim Winkel 0 liegt die Bandmitte
    auf der Höhe `start`. In der (Winkel, Höhe)-Abwicklung des Zylinders ist das Band ein Parallelogramm.
    """
    surface = Geom_CylindricalSurface(position, radius)

    def corner(angle: float, side: float) -> gp_Pnt2d:
        return gp_Pnt2d(angle, start + pitch * angle / (2 * math.pi) + side * width / 2)

    corners = [corner(low, -1), corner(high, -1), corner(high, 1), corner(low, 1)]
    maker = BRepBuilderAPI_MakeWire()
    for first, second in zip(corners, corners[1:] + corners[:1]):
        line = Geom2d_Line(first, gp_Dir2d(second.X() - first.X(), second.Y() - first.Y()))
        maker.Add(BRepBuilderAPI_MakeEdge(line, surface, 0.0, first.Distance(second)).Edge())
    wire = maker.Wire()
    BRepLib.BuildCurves3d_s(wire)
    return wire


def _thread_groove(position, radius: float, pitch: float, depth: float, start: float, end: float,
                   inside: bool, runout: bool = False) -> List_TopoDS_Shape:
    """Die Gewinderille als Liste kurzer Volumenstücke entlang der Schraubenlinie.

    Jedes Stück verbindet ein breites Band knapp außerhalb des Materials mit einem schmalen
    Band am Gewindegrund (trapezförmiger Querschnitt, Flankenwinkel rund 60°). In einem Stück
    über viele Windungen gebaut, schneidet der Geometriekern die Rille unzuverlässig aus –
    in Stücken von einer halben Windung klappt es.

    Mit `runout` läuft die Rille auf der letzten Windung aus: sie wird dort in Viertelwindungen gebaut,
    jede flacher und schmaler als die vorige, statt mit voller Tiefe abzubrechen.
    """
    # Die Grundseite steht etwas über die Fläche hinaus, bleibt aber schmaler als eine Steigung
    excess = 0.25 * depth
    wide, narrow = pitch * 0.75 * (1 + excess / depth), pitch * 0.04
    base, tip = (radius - excess, radius + depth) if inside else (radius + excess, radius - depth)
    total = (end - start) / pitch * 2 * math.pi
    step = 2 * math.pi / _THREAD_PIECES_PER_TURN
    pieces = List_TopoDS_Shape()
    angle = 0.0
    while angle < total - 1e-9:
        remaining = total - angle
        piece_wide, piece_tip, piece_step = wide, tip, step
        if runout and remaining <= 2 * math.pi + 1e-9:
            # Auslauf: Anteil der vollen Tiefe, der in der Mitte dieses Stücks noch übrig ist
            piece_step = step / 2
            share = max(0.12, (remaining - piece_step / 2) / (2 * math.pi))
            piece_tip = radius + depth * share if inside else radius - depth * share
            piece_wide = narrow + (wide - narrow) * share
        following = min(angle + piece_step, total)
        try:
            maker = BRepOffsetAPI_ThruSections(True, True)      # Volumen mit geraden Flanken
            maker.AddWire(_helix_band(position, base, pitch, angle, following, start, piece_wide))
            maker.AddWire(_helix_band(position, piece_tip, pitch, angle, following, start, narrow))
            maker.CheckCompatibility(False)
            maker.Build()
            pieces.Append(maker.Shape())
        except Exception as error:
            raise FeatureError("Die Gewinderille ließ sich nicht bilden") from error
        angle = following
    return pieces


def _bounds(shape) -> tuple[float, ...]:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    low, high = box.CornerMin(), box.CornerMax()
    return low.X(), low.Y(), low.Z(), high.X(), high.Y(), high.Z()


def _is_hollowed(result, body) -> bool:
    """Ist `result` ein brauchbar ausgehöhlter `body`: gültig, leichter und nicht größer geworden?"""
    if result is None or not BRepCheck_Analyzer(result).IsValid():
        return False
    if not 0 < _volume(result) < _volume(body) * (1 - 1e-9):
        return False
    inner, outer = _bounds(result), _bounds(body)
    grown = any(inner[i] < outer[i] - 1e-6 for i in range(3)) or any(inner[i] > outer[i] + 1e-6 for i in range(3, 6))
    return not grown


SHELL_DIRECTIONS = ("Innen", "Außen", "Beidseitig")


def _grown(body, faces: list, distance: float):
    """Der Körper um `distance` nach außen versetzt, dazu die Flächen, die aus `faces` geworden sind; None, wenn der
    Kern den Versatz nicht schafft."""
    from OCP.BRepOffset import BRepOffset_Skin
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
    from OCP.GeomAbs import GeomAbs_Intersection
    maker = BRepOffsetAPI_MakeOffsetShape()
    try:
        # scharfe Außenkanten wie in Fusion (nicht abgerundet)
        maker.PerformByJoin(body, distance, 1e-3, BRepOffset_Skin, False, False, GeomAbs_Intersection)
        if not maker.IsDone():
            return None
        grown = maker.Shape()
        if _volume(grown) < _volume(body):
            return None                 # falsch herum versetzt
        mapped = []
        for face in faces:
            # je nach Fall meldet der Kern die versetzte Fläche als geändert oder als neu erzeugt
            for result in [*maker.Modified(face), *maker.Generated(face)]:
                mapped.append(result)
        if not mapped:
            return None
        if grown.ShapeType() == TopAbs_SHELL:
            grown = _as_solid(grown)
        return (grown, mapped) if grown is not None else None
    except Exception:
        return None


def _shell_by_offset(body, removed, thickness: float):
    """Aushöhlen mit dem Versatzwerkzeug des Geometriekerns (None, wenn es kein brauchbares Ergebnis gibt).

    Welches Vorzeichen nach innen zeigt, hängt von der Ausrichtung des Körpers ab; bei einem
    Fehlschlag liefert der Kern teils den unveränderten oder einen nach außen verdickten Körper.
    """
    for offset in (-thickness, thickness):
        maker = BRepOffsetAPI_MakeThickSolid()
        try:
            maker.MakeThickSolidByJoin(body, removed, offset, 1e-3)
            maker.Build()
            candidate = maker.Shape() if maker.IsDone() else None
        except Exception:
            candidate = None
        if _is_hollowed(candidate, body):
            return candidate
    return None


def _as_solid(shape):
    """Macht aus einer geschlossenen Hülle einen Volumenkörper mit nach außen zeigenden Flächen.

    Eine offene oder fehlerhafte Hülle ergibt None: `BRepBuilderAPI_MakeSolid` stürzte bei solchen Hüllen
    (verwickelte Modelle, Zufallstest Startwert 11) mit einem Speicherzugriffsfehler ab, statt einen Fehler zu melden.
    """
    if shape.ShapeType() != TopAbs_SHELL:
        return shape
    from OCP.BRep import BRep_Tool
    if not BRep_Tool.IsClosed_s(shape) or not BRepCheck_Analyzer(shape).IsValid():
        return None
    solid = BRepBuilderAPI_MakeSolid(TopoDS.Shell(shape)).Solid()
    # Negatives Volumen bedeutet: die Flächen zeigen nach innen
    return solid.Reversed() if _volume(solid) < 0 else solid


def _shell_by_cavity(body, faces: list, thickness: float):
    """Ersatzweg: verkleinerten Innenkörper als Hohlraum abziehen und durch ebene Flächen öffnen.

    Der Innenkörper entsteht durch Versatz des ganzen Körpers nach innen. Für jede zu
    entfernende ebene Fläche wird er zusätzlich um die Wandstärke in Richtung dieser Fläche
    verschoben abgezogen; dadurch wird die Wand dort durchbrochen.
    """
    inner = None
    for offset in (-thickness, thickness):
        maker = BRepOffsetAPI_MakeOffsetShape()
        try:
            maker.PerformByJoin(body, offset, 1e-3)
            candidate = _as_solid(maker.Shape()) if maker.IsDone() else None
        except Exception:
            candidate = None
        if candidate is not None and 0 < _volume(candidate) < _volume(body):
            inner = candidate
            break
    if inner is None:
        return None

    result = body
    try:
        result = ft._boolean(BRepAlgoAPI_Cut, result, inner)
        for face in faces:
            surface = BRepAdaptor_Surface(TopoDS.Face(face))
            if surface.GetType() != GeomAbs_Plane:
                return None
            # Nach außen zeigende Normale der Fläche (bei umgekehrter Fläche gedreht)
            normal = surface.Plane().Axis().Direction()
            if face.Orientation() == TopAbs_REVERSED:
                normal = normal.Reversed()
            shift = gp_Trsf()
            shift.SetTranslation(gp_Vec(normal.XYZ()) * (thickness * 1.01))
            opening = BRepBuilderAPI_Transform(inner, shift, True).Shape()
            result = ft._boolean(BRepAlgoAPI_Cut, result, opening)
    except Exception:
        return None
    return result if _is_hollowed(result, body) else None


def _volume(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def _outermost_face(body, direction):
    """Die Fläche, deren Schwerpunkt in der angegebenen Richtung am weitesten außen liegt."""
    best, best_value = None, -math.inf
    explorer = TopExp_Explorer(body, TopAbs_FACE)
    while explorer.More():
        face = explorer.Current()
        properties = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, properties)
        centre = properties.CentreOfMass()
        value = centre.X() * direction[0] + centre.Y() * direction[1] + centre.Z() * direction[2]
        if value > best_value:
            best, best_value = face, value
        explorer.Next()
    if best is None:
        raise FeatureError("Der Körper hat keine Flächen")
    return best


@dataclass
class RemoveBody(Feature):
    """Körper entfernen (Fusion: „Entfernen“ im Browser): der Körper verschwindet ab diesem Schritt aus dem Modell;
    die Schritte davor bleiben, wie sie sind."""

    body: str = ""
    more: list = field(default_factory=list)    # weitere Körper, die im selben Schritt verschwinden (Entf, Rahmen)

    KIND = "Körper entfernen"

    def apply(self, state: BuildState) -> None:
        for name in [self.body, *self.more]:
            if name not in state.bodies:
                raise ft.missing_body(name)
            del state.bodies[name]
            # alles, was am Körper hing, mit entfernen
            for table in (state.components, state.colors, state.face_colors, state.sheets):
                table.pop(name, None)
            state.helpers.discard(name)


@dataclass
class RemoveSketch(Feature):
    """Skizze entfernen (Fusion: „Entfernen“ im Browser): ab diesem Schritt ist die Skizze aus dem Modell
    verschwunden; was vorher aus ihr entstand (Extrusion, Drehen …), bleibt unverändert."""

    sketch: str = ""
    more: list = field(default_factory=list)    # weitere Skizzen im selben Schritt

    KIND = "Skizze entfernen"

    def apply(self, state: BuildState) -> None:
        for name in [self.sketch, *self.more]:
            if name not in state.frames and name not in state.profiles and name not in state.curves:
                raise FeatureError(f"Skizze '{name}' ist nicht (mehr) vorhanden")
            for table in (state.profiles, state.solid_profiles, state.curves, state.frames, state.normals,
                          state.sketch_points):
                table.pop(name, None)
            # auch die angezeigten Linien, Konstruktionslinien und Texte verschwinden aus der Ansicht
            parts = state.sketch_parts.pop(name, [])
            state.sketch_curves = [shape for shape in state.sketch_curves
                                   if not any(shape.IsSame(part) for part in parts)]
            state.construction_curves = [shape for shape in state.construction_curves
                                         if not any(shape.IsSame(part) for part in parts)]


# --- Körper kopieren und einfügen (auch von Entwurf zu Entwurf) ---------------------------------------------------

CLIPBOARD_FORMAT = "wolfcad-koerper"     # Kennung im Inhalt der Zwischenablage (schützt vor fremdem JSON)


def _pack_shape(shape: TopoDS_Shape) -> str:
    """Form als Text für Zwischenablage und Entwurf: Binärformat des Kerns, zlib, base64."""
    import base64
    import zlib
    return base64.b64encode(zlib.compress(ft._shape_bytes(shape), 6)).decode("ascii")


def _unpack_shape(text: str) -> TopoDS_Shape:
    """Gegenstück zu `_pack_shape`."""
    import base64
    import zlib
    return ft._shape_from_bytes(zlib.decompress(base64.b64decode(text)))


def copy_bodies(state: BuildState, names: list[str], source: str = "") -> dict:
    """Kopiert Körper in einen eigenständigen Inhalt für die Zwischenablage (Fusion: Strg+C im Browser).

    Der Inhalt hängt nicht am Entwurf: die Formen stecken gepackt darin, dazu Farben und einzeln gefärbte Flächen
    je Körper. So lässt er sich in jeden anderen Entwurf einfügen, auch nachdem das Programm beendet wurde."""
    names = [name for name in names if name in state.bodies]
    if not names:
        raise FeatureError("Bitte zuerst einen oder mehrere Körper wählen")
    builder, compound = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(compound)
    for name in names:
        builder.Add(compound, state.bodies[name])
    # gemeinsamer Schwerpunkt: Fußpunkt der Verschiebepfeile nach dem Einfügen
    centres = []
    for name in names:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(state.bodies[name], props)
        point = props.CentreOfMass()
        centres.append((point.X(), point.Y(), point.Z()))
    centre = [sum(c[i] for c in centres) / len(centres) for i in range(3)]
    bodies = [{"name": name, "color": state.colors.get(name, ""),
               "face_colors": [[list(point), colour] for point, colour in state.face_colors.get(name, [])]}
              for name in names]
    return {"format": CLIPBOARD_FORMAT, "version": 1, "source": source, "bodies": bodies,
            "centre": centre, "data": _pack_shape(compound)}


@dataclass
class PasteBodies(Feature):
    """Körper einfügen (Fusion: Strg+V): legt kopierte Körper als neue, eigenständige Körper an.

    Die Formen sind im Schritt gespeichert (`data`), der Entwurf braucht also weder die Quelldatei noch die
    Zwischenablage. Eingefügt wird an der Originalstelle; die Pfeile verschieben die Kopie von dort aus."""

    source: str = ""        # Entwurf, aus dem kopiert wurde (nur zur Anzeige)
    data: str = ""          # alle Körper als ein Verbund, gepackt mit `_pack_shape`
    bodies: list = field(default_factory=list)     # je Körper: ursprünglicher Name, Farbe, gefärbte Flächen
    centre: list = field(default_factory=list)     # gemeinsamer Schwerpunkt an der Originalstelle
    move_x: float = 0.0
    move_y: float = 0.0
    move_z: float = 0.0

    KIND = "Körper einfügen"

    def parameters(self) -> list:
        # gepackte Formen und Begleitdaten sind keine Eingabefelder
        return [f for f in super().parameters() if f.name not in ("data", "bodies", "centre")]

    def apply(self, state: BuildState) -> None:
        if not self.data:
            raise FeatureError("Die Zwischenablage enthielt keine Körper")
        compound = _unpack_shape(self.data)
        shift = gp_Trsf()
        shift.SetTranslation(gp_Vec(self.move_x, self.move_y, self.move_z))
        # der Iterator liefert die Körper in der Reihenfolge des Kopierens: so passen die Einträge in `bodies`
        parts = TopoDS_Iterator(compound)
        number = 0
        while parts.More():
            name = state.add_body(BRepBuilderAPI_Transform(parts.Value(), shift, True, True).Shape())
            info = self.bodies[number] if number < len(self.bodies) else {}
            if info.get("color"):
                state.colors[name] = info["color"]
            if info.get("face_colors"):
                # Merkpunkte der gefärbten Flächen wandern mit der Verschiebung
                state.face_colors[name] = [(tuple(gp_Pnt(*point).Transformed(shift).Coord()), colour)
                                           for point, colour in info["face_colors"]]
            number += 1
            parts.Next()


# In das Verzeichnis für das Laden gespeicherter Entwürfe eintragen
for _feature_class in (PasteBodies, RemoveBody, RemoveSketch, Box, Cylinder, Sphere, Revolve, Hole, MoveBody, CombineBodies, MirrorBody, Shell,
                       RectangularPattern, CircularPattern, Sweep, Loft, Thread, ImportedStep,
                       SplitBody, ScaleBody, Draft, Rib, Component, Joint, Appearance, PressPull,
                       ConstructionAxis, PathPattern, DeleteFace, ReplaceFace, MoveFace):
    ft.FEATURE_CLASSES[_feature_class.KIND] = _feature_class
