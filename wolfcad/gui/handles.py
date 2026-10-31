# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Ziehpfeile für das direkte Bearbeiten in der 3D-Ansicht.

Zu einem Feature wird berechnet, wo sein wichtigstes Maß angreift: ein Fußpunkt,
eine Richtung und der aktuelle Wert. Die Pfeilspitze sitzt bei Fußpunkt + Richtung × Wert;
zieht man sie mit der Maus, ändert sich das Maß und das Modell rechnet sofort neu.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Pnt, gp_Vec
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from ..core import features as ft
from ..core import more_tools as mt
from ..core import sketch, solids
from ..core import extras as ex
from ..core import gears as gr
from ..core import sheetmetal as sm
from ..core import surfaces as sf

Vector = tuple[float, float, float]

LINEAR, ANGLE = "linear", "angle"   # Arten von Griffen: gerader Pfeil (mm) oder Bogen (Grad)


@dataclass
class Handle:
    base: Vector            # Fußpunkt des Pfeils
    direction: Vector       # Einheitsvektor, in den der Wert wächst
    parameter: str          # Name des Feldes, das der Pfeil verändert
    value: float            # aktueller Wert entlang der Richtung (mm)
    factor: float = 1.0     # Feldwert = Wert × Faktor (z. B. Durchmesser = 2 × Radius)
    minimum: float | None = None    # kleinster zulässiger Wert (None = beliebig, auch negativ)
    color: Vector = (1.00, 0.55, 0.10)  # Farbe des Pfeils; Achsen: X rot, Y grün, Z blau
    expression: str = ""    # Ausdruck, an dem das Maß hängt (vom Hauptfenster eingetragen)
    unit: str = ""          # abweichende Einheit im Wertfeld (z. B. " °" für einen Winkel am geraden Pfeil)
    maximum: float | None = None    # größter zulässiger Wert (None = beliebig)
    # Winkelgriff: `direction` ist dann die Drehachse durch `base`, der Wert ein Winkel in Grad.
    # `reference` zeigt (senkrecht zur Achse) dorthin, wo der Winkel 0 liegt; `radius` ist der
    # Abstand des Bogens von der Achse.
    kind: str = LINEAR
    reference: Vector | None = None
    radius: float = 0.0
    # Der Pfeilkopf sitzt um diesen Anteil der Kameraentfernung weiter außen. So bleiben
    # mehrere Griffe am selben Punkt (Verschieben: drei Achsen und die Drehung) auseinander.
    lead: float = 0.0
    # Teil des Verschiebe-Kreuzes wie in Fusion (Runde 82): in der eigenen Achsfarbe gezeichnet (statt im
    # einheitlichen Blau), Pfeile mit kräftigem Schaft ab der Körpermitte, Drehgriffe als Ringstück mit Knauf
    gizmo: bool = False
    # in der eigenen Farbe `color` statt im einheitlichen Blau (Auswahlsätze der Rundung/Fase, Runde 92)
    tinted: bool = False

    def tip(self) -> Vector:
        if self.kind == ANGLE:
            side = _cross(self.direction, self.reference)
            angle = math.radians(self.value)
            return tuple(self.base[i] + self.radius * (math.cos(angle) * self.reference[i]
                                                       + math.sin(angle) * side[i]) for i in range(3))
        return tuple(self.base[i] + self.direction[i] * self.value for i in range(3))


def _cross(a: Vector, b: Vector) -> Vector:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def angle_handle(axis_point: Vector, axis: Vector, towards: Vector, parameter: str, value: float,
                 minimum: float, maximum: float = 360.0, color: Vector = (1.00, 0.55, 0.10),
                 lead: float = 0.0) -> Handle:
    """Winkelgriff um die Achse durch `axis_point`; der Bogen beginnt in Richtung des Punkts `towards`.

    Der Fußpunkt wandert auf der Achse auf die Höhe von `towards`, damit der Bogen dort liegt,
    wo sich das Gedrehte befindet. Liegt `towards` auf der Achse, beginnt der Bogen schräg
    zwischen den Hauptrichtungen, damit er nicht auf einem Achsenpfeil liegt.
    """
    offset = tuple(t - p for t, p in zip(towards, axis_point))
    along = sum(o * a for o, a in zip(offset, axis))
    base = tuple(p + a * along for p, a in zip(axis_point, axis))
    radial = tuple(o - a * along for o, a in zip(offset, axis))
    radius = math.sqrt(sum(c * c for c in radial))
    reference = _unit(radial) if radius > 1e-6 else None
    if reference is None:
        helper = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
        first = _unit(_cross(axis, helper))
        second = _cross(axis, first)
        reference, radius = _unit(tuple(a + b for a, b in zip(first, second))), 0.0
    return Handle(base, axis, parameter, value, minimum=minimum, maximum=maximum, color=color,
                  kind=ANGLE, reference=reference, radius=radius, lead=lead)


def _edge_point(shape) -> Vector:
    """Ein Punkt auf dem Rand eines Profils (Mitte seiner ersten Kante)."""
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    curve = BRepAdaptor_Curve(TopoDS.Edge(explorer.Current()))
    return _xyz(curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2))


def _xyz(point) -> Vector:
    return point.X(), point.Y(), point.Z()


def _unit(vector: Vector) -> Vector | None:
    length = math.sqrt(sum(c * c for c in vector))
    return tuple(c / length for c in vector) if length > 1e-12 else None


def _surface_centre(shape) -> Vector:
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, properties)
    return _xyz(properties.CentreOfMass())


def _surface_centre_of_curves(shape) -> Vector:
    """Schwerpunkt von Kurven (gewichtet nach ihrer Länge)."""
    properties = GProp_GProps()
    BRepGProp.LinearProperties_s(shape, properties)
    return _xyz(properties.CentreOfMass())


def _volume_centre(shape) -> Vector:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return _xyz(properties.CentreOfMass())


_COUNT_STEP = 10.0      # mm Zugweg je Kopie am Anzahl-Griff des Kreismusters
RED, GREEN, BLUE = (0.95, 0.32, 0.32), (0.40, 0.85, 0.42), (0.36, 0.62, 1.00)
# Farben der abgeschlossenen Auswahlsätze einer Rundung/Fase (Pfeil und Kanten gleich gefärbt, Runde 92); der
# gerade bearbeitete Satz behält die gewohnten Farben (blauer Pfeil, gelbe Kanten)
SET_COLORS = ((0.35, 0.85, 0.55), (0.85, 0.50, 0.95), (0.30, 0.80, 0.95), (0.95, 0.55, 0.35))
_AXIS_COLORS = {"X": RED, "Y": GREEN, "Z": BLUE}
_MOVE_LEAD = 0.06       # Abstand der Verschiebe-Griffe vom Körpermittelpunkt (Anteil der Kameraentfernung)


def handles_for(feature: ft.Feature, before: ft.BuildState, after: ft.BuildState) -> list[Handle]:
    """Die Ziehpfeile zum Feature (leer, wenn es dafür noch keine gibt); der erste ist das Hauptmaß.

    `before` ist der Modellstand vor dem Feature, `after` der aktuelle Stand.
    """
    try:
        found = _handles(feature, before, after)
    except Exception:
        return []       # ohne Pfeil bleibt das Eigenschaftenfeld zum Einstellen
    if found is None:
        return []
    return found if isinstance(found, list) else [found]


def _selected_centre(state: ft.BuildState, body: str) -> Vector:
    """Gemeinsamer Schwerpunkt der gewählten Körper (Fußpunkt für Pfeile, die Körper bewegen)."""
    centres = [_volume_centre(state.bodies[name]) for name in ft.select_bodies(state, body)]
    return tuple(sum(centre[i] for centre in centres) / len(centres) for i in range(3))


_DIAGONAL = (0.57735, -0.57735, 0.57735)      # Richtung des Skalier-Pfeils (schräg, abseits der Achsenpfeile)
_SCALE_COLOR = (0.92, 0.92, 0.96)


# Lage der Drehgriffe wie in Fusion: das Ringstück für eine Achse liegt zwischen den beiden anderen Achsen
_RING_BETWEEN = {"X": (0.0, 1.0, 1.0), "Y": (1.0, 0.0, 1.0), "Z": (1.0, 1.0, 0.0)}
_RING_LEAD = 0.025      # Drehgriffe liegen innerhalb der Pfeile (Anteil der Kameraentfernung über dem Grundradius)


def _move_arrows(centre: Vector, feature) -> list[Handle]:
    """Die drei Achsenpfeile des Verschiebe-Kreuzes (Fusion-Art): ihre Spitzen sitzen gemeinsam an der verschobenen
    Mitte, so wandert das ganze Kreuz mit (Felder move_x/move_y/move_z des Features)."""
    shift = (feature.move_x, feature.move_y, feature.move_z)
    at = tuple(centre[i] + shift[i] for i in range(3))
    found = []
    for number, axis in enumerate("XYZ"):
        direction = solids.AXES[axis]
        base = tuple(at[i] - direction[i] * shift[number] for i in range(3))
        found.append(Handle(base, direction, f"move_{axis.lower()}", shift[number], color=_AXIS_COLORS[axis],
                            lead=_MOVE_LEAD, gizmo=True))
    return found


def _move_gizmo(before: ft.BuildState, feature) -> list[Handle]:
    """Verschiebe-Kreuz wie in Fusion: an der (verschobenen) Körpermitte drei Pfeile in den Achsfarben, je Achse ein
    Drehgriff in derselben Farbe und der helle Skalierpfeil.

    Das Kreuz wandert mit dem Körper: jeder Pfeil hat seinen Fußpunkt so, dass seine Spitze (Fußpunkt + Richtung ×
    Wert) die verschobene Mitte ist. Gedreht wird um den Drehpunkt samt Verschiebung – dasselbe wie „erst um den
    Drehpunkt drehen, dann verschieben“ (siehe MoveBody.transform)."""
    centre = _selected_centre(before, feature.body)
    shift = (feature.move_x, feature.move_y, feature.move_z)
    at = tuple(centre[i] + shift[i] for i in range(3))
    found = _move_arrows(centre, feature)
    pivot = centre if feature.pivot == solids.MOVE_PIVOTS[0] else (0.0, 0.0, 0.0)
    pivot = tuple(pivot[i] + shift[i] for i in range(3))
    for axis in "XYZ":
        # ältere Schritte drehen mit `angle` um `axis` – dieser Winkel gehört dann zum Ring dieser Achse
        parameter = "angle" if feature.axis == axis else f"angle_{axis.lower()}"
        between = _unit(_RING_BETWEEN[axis])
        towards = tuple(pivot[i] + between[i] * 1e-3 for i in range(3))
        ring = angle_handle(pivot, solids.AXES[axis], towards, parameter, getattr(feature, parameter),
                            minimum=-360.0, color=_AXIS_COLORS[axis], lead=_RING_LEAD)
        ring.gizmo = True
        found.append(ring)
    # Skalieren (Runde 81): schräger Pfeil; seine Länge ist der Faktor mal einer Bezugslänge des Körpers
    reference = _scale_reference(before, feature.body)
    # Als Teil des Kreuzes gezeichnet: in seiner hellen Farbe, ohne Maßlinie quer durch das Bild
    found.append(Handle(at, _DIAGONAL, "scale", feature.scale * reference, factor=1.0 / reference,
                        minimum=0.02 * reference, color=_SCALE_COLOR, unit=" ×", lead=_MOVE_LEAD * 1.2, gizmo=True))
    return found


def _scale_reference(state: ft.BuildState, body: str) -> float:
    """Bezugslänge für den Skalier-Pfeil: halbe Raumdiagonale der gewählten Körper (mindestens 5 mm)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    for name in ft.select_bodies(state, body):
        BRepBndLib.Add_s(state.bodies[name], box, False)
    if box.IsVoid():
        return 10.0
    return max(5.0, math.sqrt(box.SquareExtent()) / 2)


def _canvas_handles(feature) -> list:
    """Leinwand (Runde 81): Breite (Hauptmaß), Verschieben in der Ebene (x rot, y grün) und Drehen um ihre Mitte."""
    axes = sketch.frame_axes(feature.plane, feature.offset, feature.frame)
    origin, normal = _xyz(axes.Location()), _xyz(axes.Direction())
    x_dir, y_dir = _xyz(axes.XDirection()), _xyz(axes.YDirection())
    at = lambda u, v: tuple(o + x * u + y * v for o, x, y in zip(origin, x_dir, y_dir))
    centre = at(feature.center_x, feature.center_y)
    cos, sin = math.cos(math.radians(feature.angle)), math.sin(math.radians(feature.angle))
    across = tuple(x * cos + y * sin for x, y in zip(x_dir, y_dir))
    return [
        Handle(centre, across, "width", feature.width / 2, factor=2.0, minimum=0.5, color=_SCALE_COLOR),
        Handle(at(0.0, feature.center_y), x_dir, "center_x", feature.center_x, color=RED, lead=_MOVE_LEAD),
        Handle(at(feature.center_x, 0.0), y_dir, "center_y", feature.center_y, color=GREEN, lead=_MOVE_LEAD),
        # der Bogen beginnt in x-Richtung der Ebene (Drehung 0) und läuft wie die Leinwand selbst
        angle_handle(centre, normal, at(feature.center_x + feature.width / 2, feature.center_y), "angle",
                     feature.angle, minimum=-360.0, lead=_MOVE_LEAD * 1.5),
    ]


def _edge_arrow(body, edge, position: float = 0.5):
    """(Fußpunkt, Richtung) eines Pfeils an der Stelle `position` (0 … 1) der Kante, ins Körperinnere und
    senkrecht zur Kante – wie die Pfeile der Rundung in Fusion. None, wenn keine Richtung bestimmbar ist."""
    curve = BRepAdaptor_Curve(edge)
    point, tangent = gp_Pnt(), gp_Vec()
    curve.D1(curve.FirstParameter() + (curve.LastParameter() - curve.FirstParameter()) * position, point, tangent)
    inward = gp_Vec(point, gp_Pnt(*_volume_centre(body)))
    if tangent.Magnitude() > 1e-12:
        tangent.Normalize()
        inward = inward - tangent * inward.Dot(tangent)
    direction = _unit(_xyz(inward))
    return None if direction is None else (_xyz(point), direction)


def _edge_tool_handles(feature, before) -> list | None:
    """Pfeile der Rundung/Fase (Runde 92, wie Fusion): einer für den gerade bearbeiteten Satz, je abgeschlossenem
    Satz einer in dessen Farbe (Parameter „sets.<Nummer>“) und beim variablen Radius je einer am Ende und am
    Zwischenpunkt der Kante."""
    name = ft.select_bodies(before, feature.body)[0]
    body = before.bodies[name]
    edges = ft.unique_edges(body)
    size = "radius" if isinstance(feature, ft.Fillet) else "distance"
    found = []
    if feature.edge_points:
        edge = ft.find_by_points(edges, ft.edge_point, feature.edge_points[:1], "Kante")[0]
    elif not feature.sets:
        edge = ft._sharp_edges(body)[0]
    else:
        edge = None             # nach „+“: der neue Satz bekommt seinen Pfeil erst mit der ersten Kante
    if edge is not None:
        arrow = _edge_arrow(body, edge)
        if arrow is not None:
            found.append(Handle(*arrow, size, getattr(feature, size), minimum=0.05))
        variable = isinstance(feature, ft.Fillet) and feature.radius_type == ft.RADIUS_KINDS[2]
        if variable and feature.fillet_type == ft.FILLET_KINDS[0]:
            # Radius am Ende (Anfang ist der Hauptpfeil, hier in die Kantenmitte gerückt) und am Zwischenpunkt
            for parameter, position in (("radius2", 0.97),
                                        ("mid_radius", min(max(feature.mid_position, 0.05), 0.95))):
                arrow = _edge_arrow(body, edge, position)
                if arrow is not None:
                    found.append(Handle(*arrow, parameter, getattr(feature, parameter), minimum=0.0))
            start = _edge_arrow(body, edge, 0.03)
            if start is not None and found:
                found[0] = Handle(*start, size, getattr(feature, size), minimum=0.05)
    for number, entry in enumerate(feature.sets):
        try:
            first = ft.find_by_points(edges, ft.edge_point, entry["edge_points"][:1], "Kante")[0]
        except (ft.FeatureError, KeyError, IndexError):
            continue
        arrow = _edge_arrow(body, first)
        if arrow is not None:
            found.append(Handle(*arrow, f"sets.{number}", float(entry.get(size, 0.0)), minimum=0.05,
                                color=SET_COLORS[number % len(SET_COLORS)], tinted=True))
    return found or None


_SCALE_LENGTH = 20.0        # Skalieren: so viele mm am Pfeil entsprechen dem Faktor 1


def _sketch_place(state, name: str):
    """(Mitte, Normale) einer Skizze: Mitte ihres Profils bzw. ihrer Kurven, Normale ihrer Ebene."""
    if name not in state.frames:
        return None
    normal = _xyz(state.frames[name].Direction())
    if name in state.profiles:
        return _surface_centre(state.profiles[name]), normal
    if name in getattr(state, "curves", {}):
        return _surface_centre_of_curves(state.curves[name]), normal
    return _xyz(state.frames[name].Location()), normal


def _across(direction: Vector) -> Vector:
    """Eine Richtung senkrecht zu `direction` (bevorzugt waagrecht)."""
    helper = (0.0, 0.0, 1.0) if abs(direction[2]) < 0.9 else (1.0, 0.0, 0.0)
    cross = (direction[1] * helper[2] - direction[2] * helper[1], direction[2] * helper[0] - direction[0] * helper[2],
             direction[0] * helper[1] - direction[1] * helper[0])
    return _unit(cross) or (1.0, 0.0, 0.0)


def _gear_handles(feature) -> list[Handle]:
    """Zahnrad (Runde 97): Pfeil längs der Achse für die Zahnbreite, Pfeil nach außen für die Zähnezahl (sein Wert
    ist der Teilkreisradius m·z/2 – so wächst das Rad beim Ziehen sichtbar mit). Zahnstange: Länge in x-Richtung."""
    frame = ex._frame(feature.plane, feature.center_x, feature.center_y, feature.center_z)
    centre, x_dir, normal = _xyz(frame.Location()), _xyz(frame.XDirection()), _xyz(frame.Direction())
    found = [Handle(centre, normal, "thickness", feature.thickness, minimum=0.0)]
    if feature.module > 1e-9:
        if feature.gear_type == "Zahnstange":
            # Länge der Zahnstange = Zähne × Teilung π·m
            pitch = math.pi * feature.module
            found.append(Handle(centre, x_dir, "teeth", feature.tooth_count() * pitch, factor=1.0 / pitch,
                                minimum=pitch, unit=" Z.", color=_SCALE_COLOR))
        else:
            found.append(Handle(centre, x_dir, "teeth", feature.pitch_diameter() / 2, factor=2.0 / feature.module,
                                minimum=1.5 * feature.module, unit=" Z.", color=_SCALE_COLOR))
    return found


def _more_handles(feature, before):
    """Pfeile der Werkzeuge, die bis Runde 92 keine hatten (Runde 93, wie in Fusion): Torus, Spule, Rohr,
    Prägen, Steg, Skalieren, Fläche extrudieren/versetzen, Aufdicken, Flansch an Kante, Saum, Ebene entlang Pfad.
    None, wenn das Feature nicht dazugehört."""
    if isinstance(feature, gr.Gear):
        return _gear_handles(feature)
    if isinstance(feature, (ex.Torus, ex.Coil)):
        frame = ex._frame(feature.plane, feature.center_x, feature.center_y, feature.center_z)
        centre, x_dir, normal = _xyz(frame.Location()), _xyz(frame.XDirection()), _xyz(frame.Direction())
        ring = tuple(c + x * feature.diameter / 2 for c, x in zip(centre, x_dir))
        found = [Handle(centre, x_dir, "diameter", feature.diameter / 2, factor=2.0, minimum=0.5)]
        if isinstance(feature, ex.Torus):
            # Querschnitt: Pfeil am Ring nach oben (halber Querschnitt)
            found.append(Handle(ring, normal, "section", feature.tube() / 2, factor=2.0, minimum=0.1,
                                color=_SCALE_COLOR))
        elif feature.pitch > 1e-9:
            # Höhe der Spule längs der Achse: Windungen × Steigung
            found.append(Handle(centre, normal, "turns", feature.turns * feature.pitch, factor=1.0 / feature.pitch,
                                minimum=feature.pitch * 0.25, unit=" Wdg.", color=_SCALE_COLOR))
        return found
    if isinstance(feature, ex.Pipe):
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        try:
            curve = BRepAdaptor_CompCurve(TopoDS.Wire(solids._path_wire(before, feature.path)))
        except Exception:
            return []
        point, tangent = gp_Pnt(), gp_Vec()
        curve.D1(curve.FirstParameter(), point, tangent)
        direction = _unit(_xyz(tangent)) or (0.0, 0.0, 1.0)
        return [Handle(_xyz(point), _across(direction), "diameter", feature.diameter / 2, factor=2.0, minimum=0.1)]
    if isinstance(feature, (mt.Emboss, mt.Web, sf.SurfaceExtrude)):
        place = _sketch_place(before, feature.sketch)
        if place is None:
            return []
        centre, normal = place
        if isinstance(feature, mt.Emboss):
            inward = feature.mode == mt.EMBOSS_MODES[1]
            direction = tuple(-c for c in normal) if inward else normal
            return [Handle(centre, direction, "depth", feature.depth, minimum=0.05)]
        if isinstance(feature, mt.Web):
            # Steg: Dicke symmetrisch zur Skizzenebene
            return [Handle(centre, normal, "thickness", feature.thickness / 2, factor=2.0, minimum=0.05)]
        return [Handle(centre, normal, "distance", feature.distance)]
    if isinstance(feature, solids.ScaleBody):
        names = ft.select_bodies(before, feature.body)
        if not names:
            return []
        if feature.reference == solids.SCALE_REFERENCES[0]:
            bounds = solids._bounds(before.bodies[names[0]])
            pivot = tuple((bounds[i] + bounds[i + 3]) / 2 for i in range(3))
        else:
            pivot = (0.0, 0.0, 0.0)
        return [Handle(pivot, _DIAGONAL, "factor", feature.factor * _SCALE_LENGTH, factor=1.0 / _SCALE_LENGTH,
                       minimum=0.05 * _SCALE_LENGTH, unit=" ×", color=_SCALE_COLOR)]
    if isinstance(feature, (sf.OffsetSurface, sf.Thicken)):
        if feature.surface not in before.surfaces:
            return []
        faces = ft.faces_of(before.surfaces[feature.surface])
        if not faces:
            return []
        point, normal = solids.face_middle(faces[0])
        if feature.flip:
            normal = tuple(-c for c in normal)
        size = "distance" if isinstance(feature, sf.OffsetSurface) else "thickness"
        return [Handle(point, normal, size, getattr(feature, size), minimum=0.01)]
    if isinstance(feature, (sm.EdgeFlange, ex.Hem)):
        if not feature.edge_points:
            return []
        name = ft.select_bodies(before, feature.body)[0]
        body = before.bodies[name]
        edge = ft.find_by_points(ft.unique_edges(body), ft.edge_point, feature.edge_points[:1], "Kante")[0]
        frame = ft._edge_frame(body, edge)
        if frame is None:
            return []
        # Richtung des Flanschs: längs der Normale der größeren Nachbarfläche (die Blechseite), wie die Biegung
        middle, _tangent, face_a, face_b, normal_a, normal_b = frame
        area = lambda face: (lambda p: (BRepGProp.SurfaceProperties_s(face, p), p.Mass())[1])(GProp_GProps())
        normal = normal_a if area(face_a) >= area(face_b) else normal_b
        direction = _xyz(normal.Reversed() if feature.flip else normal)
        return [Handle(_xyz(middle), _unit(direction) or (0.0, 0.0, 1.0), "length", feature.length, minimum=0.1)]
    if isinstance(feature, ex.SplitFace) and feature.face_points and feature.plane in ft.PLANES:
        # Fläche teilen: Pfeil längs der Normale der Teilungsebene, Wert = ihr Versatz
        normal = ft.PLANES[feature.plane][0]
        point = feature.face_points[0][:3]
        along = sum(p * n for p, n in zip(point, normal))
        base = tuple(p - n * along for p, n in zip(point, normal))
        return [Handle(base, tuple(float(n) for n in normal), "offset", feature.offset)]
    if isinstance(feature, solids.PathPattern):
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        try:
            curve = BRepAdaptor_CompCurve(TopoDS.Wire(solids._path_wire(before, feature.path)))
        except Exception:
            return []
        point, tangent = gp_Pnt(), gp_Vec()
        curve.D1(curve.FirstParameter(), point, tangent)
        direction = _unit(_xyz(tangent)) or (1.0, 0.0, 0.0)
        # Anzahl wie beim Kreismuster: je 10 mm Zug eine Kopie mehr
        return [Handle(_xyz(point), direction, "count", max(2, round(feature.count)) * _COUNT_STEP,
                       factor=1.0 / _COUNT_STEP, minimum=2 * _COUNT_STEP, unit=" ×")]
    if isinstance(feature, mt.PlaneAlongPath):
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        from OCP.GCPnts import GCPnts_AbscissaPoint
        try:
            curve = BRepAdaptor_CompCurve(TopoDS.Wire(solids._path_wire(before, feature.path)))
        except Exception:
            return []
        length = GCPnts_AbscissaPoint.Length_s(curve)
        if length < 1e-9:
            return []
        point, tangent = gp_Pnt(), gp_Vec()
        curve.D1(curve.FirstParameter(), point, tangent)
        direction = _unit(_xyz(tangent)) or (1.0, 0.0, 0.0)
        return [Handle(_xyz(point), direction, "position", feature.position * length, factor=1.0 / length,
                       minimum=0.0, maximum=length, unit=" (Anteil)")]
    return None


def _handles(feature, before, after):
    if isinstance(feature, ft.Extrude):
        try:
            # Körperflächen gehören zum Stand vor der Extrusion, Skizzenprofile stehen in beiden Ständen
            profile, normal = feature.profile_list(before)[0]
        except ft.FeatureError:
            return None
        centre = _surface_centre(profile)
        centre = tuple(c + n * feature.start for c, n in zip(centre, normal))     # Startversatz
        if feature.extent == ft.EXTENTS[3]:
            found = []                      # „Bis Fläche“: die Länge ergibt sich aus der Zielfläche
        elif feature.extent == ft.EXTENTS[1]:
            # symmetrisch: der Pfeil zeigt die halbe Länge, sein Wert ist die ganze
            found = [Handle(centre, normal, "distance", feature.distance / 2, factor=2.0)]
        else:
            found = [Handle(centre, normal, "distance", feature.distance)]
        if feature.extent == ft.EXTENTS[2]:
            # zweite Seite: eigener Pfeil in Gegenrichtung
            found.append(Handle(centre, tuple(-c for c in normal), "distance2", feature.distance2, minimum=0.01))
        # Verjüngung: Pfeil am Rand des Profils quer zur Normale, jeder mm entspricht einem Grad (wie bei der Schräge)
        across = _unit(tuple(e - c for e, c in zip(_edge_point(profile), centre)))
        if across is not None:
            found.append(Handle(_edge_point(profile), across, "taper", feature.taper, minimum=-60.0, maximum=60.0,
                                unit=" °"))
        return found

    if isinstance(feature, (solids.Box, solids.Cylinder)):
        axes = feature.axes(before)     # Hauptebene, Körperfläche oder Konstruktionsebene, auf der aufgezogen wurde
        base = _xyz(solids._in_plane(axes, feature.center_x, feature.center_y))
        x_direction, y_direction = _xyz(axes.XDirection()), _xyz(axes.YDirection())
        height = Handle(base, _xyz(axes.Direction()), "height", feature.height)
        if isinstance(feature, solids.Cylinder):
            return [height, Handle(base, x_direction, "diameter", feature.diameter / 2,
                                   factor=2.0, minimum=0.05, color=RED)]
        # Der Quader liegt mittig: die Seitenpfeile ziehen von der Mitte aus die halbe Länge/Breite
        return [height,
                Handle(base, x_direction, "length", feature.length / 2, factor=2.0, minimum=0.05, color=RED),
                Handle(base, y_direction, "width", feature.width / 2, factor=2.0, minimum=0.05, color=GREEN)]

    if isinstance(feature, solids.Sphere):
        centre = _xyz(feature.centre(before))       # frei im Raum oder auf einer Konstruktionsebene
        return Handle(centre, (1.0, 0.0, 0.0), "diameter", feature.diameter / 2, factor=2.0, minimum=0.05)

    if isinstance(feature, solids.Hole):
        axes = sketch.placed_axes(before, feature.plane, feature.offset, feature.frame, feature.base)
        base = _xyz(solids._in_plane(axes, feature.position_x, feature.position_y))
        found = [Handle(base, _xyz(axes.Direction().Reversed()), "depth", feature.depth, minimum=0.0)]
        if feature.tapped not in solids.THREAD_PITCHES:
            # bei einer gewählten Gewindegröße legt diese den Durchmesser fest: dann kein Durchmesser-Pfeil
            found.append(Handle(base, _xyz(axes.XDirection()), "diameter", feature.diameter / 2,
                                factor=2.0, minimum=0.05, color=RED))
        return found

    if isinstance(feature, solids.MoveBody):
        return _move_gizmo(before, feature)

    if isinstance(feature, mt.Canvas):
        return _canvas_handles(feature)

    if isinstance(feature, solids.PasteBodies) and len(feature.centre) == 3:
        # Eingefügte Körper: drei Achsenpfeile wie beim Verschieben, Fußpunkt ist die Originalstelle der Kopie
        return _move_arrows(tuple(feature.centre), feature)

    if isinstance(feature, solids.MoveFace) and feature.face_points:
        # Fläche verschieben: drei Achsenpfeile an der Mitte der gewählten Flächen, dazu der Drehbogen
        base = tuple(sum(p[i] for p in feature.face_points) / len(feature.face_points) for i in range(3))
        found = _move_arrows(base, feature)
        # Hauptpfeil (der erste, mit Wertfeld) ist wie in Fusion der längs der Flächennormale: eine Deckfläche
        # zieht man nach oben, nicht seitlich in ihrer Ebene (Runde 93, das gab sonst gleich eine Fehlermeldung)
        try:
            name = ft.select_bodies(before, feature.body)[0]
            face = ft.find_by_points(ft.faces_of(before.bodies[name]), ft.face_point, feature.face_points[:1],
                                     "Fläche")[0]
            frame = sketch.face_frame(face)
        except (ft.FeatureError, IndexError, KeyError):
            frame = None
        if frame is not None:
            normal = frame[3:6]
            linear = [h for h in found if h.kind == LINEAR]
            linear.sort(key=lambda h: -abs(sum(a * b for a, b in zip(h.direction, normal))))
            found = linear + [h for h in found if h.kind != LINEAR]
        if feature.rotate_axis in solids.AXES:
            # der Bogen beginnt seitlich der Mitte, quer zur Drehachse
            axis = solids.AXES[feature.rotate_axis]
            across = (1.0, 0.0, 0.0) if feature.rotate_axis != "X" else (0.0, 1.0, 0.0)
            start = tuple(b + 10.0 * a for b, a in zip(base, across))
            found.append(angle_handle(base, axis, start, "rotate_angle", feature.rotate_angle, minimum=-180.0,
                                      lead=_MOVE_LEAD * 1.5, color=_AXIS_COLORS[feature.rotate_axis]))
        return found

    if isinstance(feature, solids.Revolve):
        if feature.sketch not in after.profiles or feature.sketch not in after.frames:
            return None
        frame = after.frames[feature.sketch]
        axis = frame.XDirection() if feature.axis == solids.REVOLVE_AXES[0] else frame.YDirection()
        profile = ft.profile_of(after, feature.sketch, feature.regions, feature.region_points)
        # Der Bogen beginnt am Profil und folgt der Drehung
        return angle_handle(_xyz(frame.Location()), _xyz(axis), _surface_centre(profile), "angle",
                            feature.angle, minimum=1.0)

    if isinstance(feature, solids.CircularPattern):
        if feature.axis not in solids.AXES:
            return None
        turn = angle_handle((0.0, 0.0, 0.0), solids.AXES[feature.axis], _selected_centre(before, feature.body),
                            "angle", feature.angle, minimum=1.0, color=_AXIS_COLORS[feature.axis])
        # Griff für die Anzahl längs der Drehachse: je 10 mm eine Kopie mehr (mindestens zwei)
        count = Handle((0.0, 0.0, 0.0), solids.AXES[feature.axis], "count", max(2, round(feature.count)) * _COUNT_STEP,
                       factor=1.0 / _COUNT_STEP, minimum=2 * _COUNT_STEP, unit=" ×", color=_AXIS_COLORS[feature.axis])
        return [turn, count]

    if isinstance(feature, solids.RectangularPattern):
        base = _selected_centre(before, feature.body)
        found = [Handle(base, solids.AXES[feature.axis], "spacing", feature.spacing,
                        color=_AXIS_COLORS[feature.axis])]
        if round(feature.second_count) > 1:
            found.append(Handle(base, solids.AXES[feature.second_axis], "second_spacing",
                                feature.second_spacing, color=_AXIS_COLORS[feature.second_axis]))
        if abs(feature.spacing) > 1e-9:
            # Griff für die Anzahl: er steht hinter der letzten Kopie, jeder Abstand weiter ist eine Kopie mehr
            step = abs(feature.spacing)
            sign = 1.0 if feature.spacing > 0 else -1.0
            direction = tuple(sign * c for c in solids.AXES[feature.axis])
            found.append(Handle(base, direction, "count", max(1, round(feature.count)) * step, factor=1.0 / step,
                                minimum=step, unit=" ×", color=_AXIS_COLORS[feature.axis]))
        return found

    if isinstance(feature, solids.Joint):
        _transform, point, normal = feature.placement(before)
        # Abstand entlang der Zielrichtung; beim Drehgelenk zusätzlich der Winkel um sie
        limits = feature.limits()
        slider = feature.joint == solids.JOINT_KINDS[2] and limits is not None
        found = [Handle(_xyz(point), _xyz(normal), "offset", feature.offset,
                        minimum=limits[0] if slider else None, maximum=limits[1] if slider else None)]
        if feature.joint == solids.JOINT_KINDS[1] and feature.body in after.bodies:
            low, high = limits if limits is not None else (-360.0, 360.0)
            found.append(angle_handle(_xyz(point), _xyz(normal), _volume_centre(after.bodies[feature.body]),
                                      "angle", feature.angle, minimum=low, maximum=high, color=BLUE))
        return found

    if isinstance(feature, solids.Rib):
        if feature.sketch not in after.curves or feature.sketch not in after.frames:
            return None
        # Der Pfeil steht mitten auf der Skizze und zieht die halbe Dicke quer zur Skizzenebene
        return Handle(_surface_centre_of_curves(after.curves[feature.sketch]),
                      _xyz(after.frames[feature.sketch].Direction()), "thickness", feature.thickness / 2,
                      factor=2.0, minimum=0.05)

    if isinstance(feature, solids.SplitBody):
        if feature.tool_points or (feature.tool and feature.tool != ft.NO_BASE):
            return None         # Trennflächen oder Trennkörper: keine Ebene, die sich verschieben ließe
        # Der Pfeil zeigt in Richtung der Ebenennormale und verschiebt die Trennebene
        start = sketch.placed_axes(before, feature.plane, 0.0, feature.frame, feature.base)
        return Handle(_xyz(start.Location()), _xyz(start.Direction()), "offset", feature.offset)

    if isinstance(feature, sketch.ConstructionPlane):
        # Der Pfeil steht auf der (gekippten) Ausgangsebene und zieht die Ebene entlang ihrer Normale;
        # der Bogen kippt sie um ihre x- oder y-Achse
        flat = sketch.frame_axes(feature.plane, 0.0, feature.frame)
        tilted = sketch.ConstructionPlane(plane=feature.plane, offset=0.0, frame=feature.frame,
                                          tilt=feature.tilt, tilt_axis=feature.tilt_axis).axes()
        around = flat.XDirection() if feature.tilt_axis == sketch.TILT_AXES[0] else flat.YDirection()
        origin = _xyz(flat.Location())
        towards = tuple(origin[i] + _xyz(flat.Direction())[i] for i in range(3))
        return [Handle(origin, _xyz(tilted.Direction()), "offset", feature.offset),
                angle_handle(origin, _xyz(around), towards, "tilt", feature.tilt, minimum=-180.0, maximum=180.0,
                             color=BLUE)]

    if isinstance(feature, solids.MirrorBody):
        # Die Spiegelebene lässt sich entlang ihrer Normale verschieben
        normal = ft.PLANES[feature.plane][0]
        return Handle((0.0, 0.0, 0.0), tuple(float(c) for c in normal), "offset", feature.offset)

    if isinstance(feature, ft.Fillet) and feature.fillet_type == ft.FILLET_KINDS[2]:
        return None         # volle Rundung: der Radius folgt aus dem Abstand der Seitenflächen
    if isinstance(feature, (ft.Fillet, ft.Chamfer)):
        return _edge_tool_handles(feature, before)

    more = _more_handles(feature, before)
    if more is not None:
        return more

    if isinstance(feature, solids.PressPull):
        # Pfeil auf der gewählten Fläche längs ihrer Normale: heraus ziehen (+) oder hinein drücken (−)
        name = ft.select_bodies(before, feature.body)[0]
        face = ft.find_by_points(ft.faces_of(before.bodies[name]), ft.face_point, feature.face_points[:1], "Fläche")[0]
        frame = sketch.face_frame(face)
        if frame is None:
            # gekrümmte Fläche: Pfeil mitten auf der Fläche längs ihrer Normale dort
            point, normal = solids.face_middle(face)
            return Handle(point, normal, "distance", feature.distance)
        return Handle(tuple(ft.face_point(face)), tuple(float(c) for c in frame[3:6]), "distance", feature.distance)

    if isinstance(feature, solids.Draft):
        # Pfeil in Entformungsrichtung oben auf dem Körper: jeder mm entspricht einem Grad Schräge
        name = ft.select_bodies(before, feature.body)[0]
        bounds = solids._bounds(before.bodies[name])
        index = "XYZ".index(feature.axis)
        base = [(bounds[i] + bounds[i + 3]) / 2 for i in range(3)]
        base[index] = bounds[index + 3]
        return Handle(tuple(base), solids.AXES[feature.axis], "angle", feature.angle, minimum=0.5, maximum=59.5,
                      unit=" °")

    if isinstance(feature, solids.Thread):
        # Pfeil entlang der Achse vom unteren Ende der Fläche: zieht die Gewindelänge
        name = ft.select_bodies(before, feature.body)[0]
        surface = BRepAdaptor_Surface(feature._cylinder_face(before.bodies[name]))
        axis = surface.Cylinder().Axis()
        low, high = sorted((surface.FirstVParameter(), surface.LastVParameter()))
        base = axis.Location().Translated(gp_Vec(axis.Direction().XYZ()) * low)
        return Handle(_xyz(base), _xyz(axis.Direction()), "length", feature.length or (high - low),
                      minimum=0.5, maximum=high - low)

    if isinstance(feature, solids.Shell):
        name = ft.select_bodies(before, feature.body)[0]
        body = before.bodies[name]
        if feature.face_points:
            face = ft.find_by_points(ft.faces_of(body), ft.face_point, feature.face_points[:1], "Fläche")[0]
        else:
            face = solids._outermost_face(body, solids.OPENINGS[feature.opening])
        base = ft.face_point(face)
        direction = _unit(tuple(c - b for c, b in zip(_volume_centre(body), base)))
        if direction is None:
            return None
        return Handle(base, direction, "thickness", feature.thickness, minimum=0.05)

    return None
