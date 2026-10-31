# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Zahnrad (Runde 97): Stirnrad mit echter Evolventenverzahnung, auch schräg- und pfeilverzahnt, dazu Hohlrad
(Innenverzahnung) und Zahnstange.

Maße nach DIN 867 (Bezugsprofil): Teilkreis d = m·z, Kopfhöhe 1·m, Fußhöhe 1,25·m, Eingriffswinkel meist 20°.
Die Zahnflanken sind Evolventen des Grundkreises (r_b = r·cos α); sie werden als glatte B-Spline-Kurven gebaut,
Kopf und Fuß als Kreisbögen – so entsteht ein sauberer Körper mit wenigen Flächen statt eines Vielecks.

Schrägverzahnung: das Profil dreht sich über die Breite um b·tan β / r. Gebaut als Erhebung durch mehrere
gedrehte Profile (eine Schraubenfläche hat OCCT nicht als einfaches Werkzeug), Pfeilverzahnung aus zwei
gegenläufigen Hälften.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.BOPAlgo import BOPAlgo_GlueEnum
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon,
                                BRepBuilderAPI_MakeWire, BRepBuilderAPI_Transform)
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAPI import GeomAPI_PointsToBSpline
from OCP.OCP.collections import Array1_gp_Pnt as TColgp_Array1OfPnt
from OCP.TopoDS import TopoDS
from OCP.OCP.collections import List_TopoDS_Shape as TopTools_ListOfShape
from OCP.gp import gp_Ax1, gp_Ax2, gp_Pnt, gp_Trsf, gp_Vec

from . import features as ft
from .extras import _frame
from .features import OPERATIONS, BuildState, Feature, FeatureError

# Arten wie im Fusion-Zusatzmodul „Stirnrad“ und darüber hinaus
GEAR_TYPES = ("Stirnrad", "Schrägverzahnt", "Pfeilverzahnt", "Hohlrad", "Zahnstange")
# Felder, die nur bei bestimmten Arten gelten (Eigenschaftenfeld blendet die übrigen aus)
GEAR_FIELDS = {"helix_angle": GEAR_TYPES[1:3], "bore": GEAR_TYPES[:3], "rim": GEAR_TYPES[3:]}

_FLANK_POINTS = 12      # Stützpunkte je Evolventenflanke (B-Spline durch diese Punkte)
_HELIX_SECTIONS = 6     # Profile über die Breite bei Schrägverzahnung (je Hälfte bei Pfeilverzahnung)


def involute(angle: float) -> float:
    """Evolventenfunktion inv(α) = tan α − α."""
    return math.tan(angle) - angle


@dataclass
class GearGeometry:
    """Kenngrößen eines Zahnrads in mm (Radien) bzw. Bogenmaß."""

    pitch: float        # Teilkreisradius r = m·z/2
    base: float         # Grundkreisradius r_b = r·cos α
    tip: float          # Kopfkreisradius
    root: float         # Fußkreisradius
    half_pitch: float   # halbe Zahndicke am Teilkreis als Winkel ψ


def geometry(module: float, teeth: int, pressure_angle: float, backlash: float = 0.0,
             addendum: float = 1.0, dedendum: float = 1.25) -> GearGeometry:
    """Radien und halbe Zahndicke (Winkel) eines Außenrads. `backlash` (Flankenspiel am Teilkreis, mm) macht den Zahn
    dünner; mit `addendum`/`dedendum` (Vielfache des Moduls) lässt sich das Profil für das Hohlrad umdrehen."""
    if module <= 0:
        raise FeatureError("Der Modul muss größer als 0 sein")
    if teeth < 3:
        raise FeatureError("Ein Zahnrad braucht mindestens 3 Zähne")
    if not 5.0 <= pressure_angle <= 35.0:
        raise FeatureError("Eingriffswinkel zwischen 5° und 35° (üblich 20°)")
    pitch = module * teeth / 2
    base = pitch * math.cos(math.radians(pressure_angle))
    thickness = math.pi * module / 2 - backlash
    if thickness <= 0.05 * module:
        raise FeatureError("Das Flankenspiel ist größer als der Zahn")
    return GearGeometry(pitch, base, pitch + addendum * module, pitch - dedendum * module, thickness / (2 * pitch))


def half_angle(gear: GearGeometry, radius: float, pressure_angle: float) -> float:
    """Halber Winkel, den der Zahn auf dem Kreis `radius` einnimmt (von der Zahnmitte bis zur Flanke)."""
    at_radius = math.acos(min(1.0, gear.base / radius))
    return gear.half_pitch + involute(math.radians(pressure_angle)) - involute(at_radius)


def profile_points(gear: GearGeometry, teeth: int, pressure_angle: float) -> list[list[tuple]]:
    """Umriss des Rads in der Ebene als Folge von Abschnitten: ("flank", Punkte), ("arc", Start, Mitte, Ende) bzw.
    ("line", Start, Ende). Zahn k hat seine Mitte bei 2πk/z. Gegen den Uhrzeigersinn, geschlossen."""
    if gear.tip <= gear.base:
        raise FeatureError("Zu wenige Zähne für diesen Eingriffswinkel (Kopfkreis innerhalb des Grundkreises)")
    start = max(gear.base, gear.root)               # dort beginnt die Evolvente
    tip_half = half_angle(gear, gear.tip, pressure_angle)
    if tip_half <= 0.002:
        raise FeatureError("Die Zähne werden spitz – weniger Flankenspiel oder mehr Zähne wählen")
    start_half = half_angle(gear, start, pressure_angle)
    step = 2 * math.pi / teeth
    if start_half >= step / 2:
        raise FeatureError("Die Zähne überschneiden sich am Fuß – mehr Zähne oder anderen Modul wählen")
    polar = lambda radius, angle: (radius * math.cos(angle), radius * math.sin(angle))
    radii = [start + (gear.tip - start) * (i / (_FLANK_POINTS - 1)) ** 1.5 for i in range(_FLANK_POINTS)]
    pieces = []
    for k in range(teeth):
        centre = k * step
        # Fuß: vom Fußkreis radial bis zum Beginn der Evolvente (nur wenn der Fußkreis innerhalb des Grundkreises liegt)
        if gear.root < start - 1e-9:
            pieces.append(("line", polar(gear.root, centre - start_half), polar(start, centre - start_half)))
        # rechte Flanke (im Uhrzeigersinn vor der Zahnmitte) nach außen
        pieces.append(("flank", [polar(r, centre - half_angle(gear, r, pressure_angle)) for r in radii]))
        pieces.append(("arc", polar(gear.tip, centre - tip_half), polar(gear.tip, centre),
                       polar(gear.tip, centre + tip_half)))
        # linke Flanke nach innen
        pieces.append(("flank", [polar(r, centre + half_angle(gear, r, pressure_angle)) for r in reversed(radii)]))
        if gear.root < start - 1e-9:
            pieces.append(("line", polar(start, centre + start_half), polar(gear.root, centre + start_half)))
        # Fußkreisbogen zur nächsten Zahnlücke
        pieces.append(("arc", polar(gear.root, centre + start_half), polar(gear.root, centre + step / 2),
                       polar(gear.root, centre + step - start_half)))
    return pieces


def _wire(pieces: list, frame: gp_Ax2):
    """Baut aus den ebenen Abschnitten einen geschlossenen Kantenzug in der Lage `frame`."""
    origin, x_dir, y_dir = frame.Location(), gp_Vec(frame.XDirection()), gp_Vec(frame.YDirection())

    def point(uv) -> gp_Pnt:
        return origin.Translated(x_dir * uv[0] + y_dir * uv[1])

    wire = BRepBuilderAPI_MakeWire()
    for piece in pieces:
        kind = piece[0]
        if kind == "line":
            edge = BRepBuilderAPI_MakeEdge(point(piece[1]), point(piece[2])).Edge()
        elif kind == "arc":
            edge = BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(point(piece[1]), point(piece[2]),
                                                              point(piece[3])).Value()).Edge()
        else:
            array = TColgp_Array1OfPnt(1, len(piece[1]))
            for index, uv in enumerate(piece[1], 1):
                array.SetValue(index, point(uv))
            edge = BRepBuilderAPI_MakeEdge(GeomAPI_PointsToBSpline(array, 3, 3).Curve()).Edge()
        wire.Add(edge)
    if not wire.IsDone():
        raise FeatureError("Das Zahnprofil ließ sich nicht schließen")
    return wire.Wire()


def _rotated(shape, frame: gp_Ax2, angle: float, lift: float):
    """`shape` um die Radachse gedreht und entlang ihr verschoben."""
    turn = gp_Trsf()
    turn.SetRotation(gp_Ax1(frame.Location(), frame.Direction()), angle)
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(frame.Direction()) * lift)
    return BRepBuilderAPI_Transform(shape, move.Multiplied(turn), True).Shape()


def _twisted(wire, frame: gp_Ax2, start: float, width: float, twist: float, sections: int):
    """Körper durch gedrehte Kopien des Profils: von Höhe `start` über `width`, gesamt um `twist` (Bogenmaß)."""
    loft = BRepOffsetAPI_ThruSections(True, False, 1e-6)
    loft.CheckCompatibility(False)              # alle Profile sind Kopien desselben Kantenzugs
    for i in range(sections + 1):
        share = i / sections
        loft.AddWire(TopoDS.Wire(_rotated(wire, frame, twist * share, start + width * share)))
    loft.Build()
    if not loft.IsDone():
        raise FeatureError("Die Schrägverzahnung ließ sich nicht bilden")
    return loft.Shape()


@dataclass
class Gear(Feature):
    """Zahnrad (Fusion: Zusatzmodul „Stirnrad“): Evolventenverzahnung aus Modul, Zähnezahl und Eingriffswinkel."""

    plane: str = "XY"
    center_x: float = 0.0
    center_y: float = 0.0
    center_z: float = 0.0
    gear_type: str = GEAR_TYPES[0]
    module: float = 2.0             # Modul m in mm (Teilung = π·m)
    teeth: int = 20                 # Zähnezahl z
    pressure_angle: float = 20.0    # Eingriffswinkel α in Grad
    thickness: float = 10.0         # Zahnbreite (Höhe des Rads)
    bore: float = 6.0               # Bohrung in der Mitte (0 = ohne)
    backlash: float = 0.0           # Flankenspiel am Teilkreis in mm
    helix_angle: float = 20.0       # Schrägungswinkel β (Schräg-/Pfeilverzahnung)
    rim: float = 5.0                # Hohlrad: Wandstärke außen um die Zähne; Zahnstange: Höhe unter dem Zahnfuß
    operation: str = OPERATIONS[0]

    KIND = "Zahnrad"

    def tooth_count(self) -> int:
        """Zähnezahl als ganze Zahl (beim Ziehen am Pfeil kommen Zwischenwerte an)."""
        return int(round(self.teeth))

    def pitch_diameter(self) -> float:
        return self.module * self.tooth_count()

    def apply(self, state: BuildState) -> None:
        if self.gear_type not in GEAR_TYPES:
            raise FeatureError(f"Unbekannte Art '{self.gear_type}'")
        if self.thickness <= 0:
            raise FeatureError("Die Zahnbreite muss größer als 0 sein")
        frame = _frame(self.plane, self.center_x, self.center_y, self.center_z)
        if self.gear_type == "Zahnstange":
            solid = self._rack(frame)
        elif self.gear_type == "Hohlrad":
            solid = self._ring(frame)
        else:
            solid = self._spur(frame)
        ft._combine(state, solid, self.operation)

    # --- Außenrad --------------------------------------------------------------------------------------------

    def _body(self, frame: gp_Ax2, wire, pitch: float):
        """Prisma bzw. verdrehter Körper aus dem Profil, je nach Art."""
        if self.gear_type == "Stirnrad":
            face = BRepBuilderAPI_MakeFace(wire).Face()
            return BRepPrimAPI_MakePrism(face, gp_Vec(frame.Direction()) * self.thickness).Shape()
        if not 0.0 < abs(self.helix_angle) < 60.0:
            raise FeatureError("Schrägungswinkel zwischen 0° und 60°")
        slope = math.tan(math.radians(self.helix_angle)) / pitch      # Drehung je mm Breite (Bogenmaß)
        if self.gear_type == "Schrägverzahnt":
            return _twisted(wire, frame, 0.0, self.thickness, slope * self.thickness, _HELIX_SECTIONS)
        half = self.thickness / 2
        lower = _twisted(wire, frame, 0.0, half, slope * half, _HELIX_SECTIONS)
        upper = _twisted(TopoDS.Wire(_rotated(wire, frame, slope * half, half)), frame, 0.0, half,
                         -slope * half, _HELIX_SECTIONS)
        # Die Hälften berühren sich genau in der Mittelfläche: „Kleben“ statt vollem Schnitt der gekrümmten Flächen
        # (ohne das dauerte die Vereinigung 4 s)
        fuse = BRepAlgoAPI_Fuse()
        arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
        arguments.Append(lower)
        tools.Append(upper)
        fuse.SetArguments(arguments)
        fuse.SetTools(tools)
        fuse.SetGlue(BOPAlgo_GlueEnum.BOPAlgo_GlueShift)
        fuse.Build()
        if not fuse.IsDone():
            raise FeatureError("Die Pfeilverzahnung ließ sich nicht bilden")
        return fuse.Shape()

    def _spur(self, frame: gp_Ax2):
        teeth = self.tooth_count()
        gear = geometry(self.module, teeth, self.pressure_angle, self.backlash)
        solid = self._body(frame, _wire(profile_points(gear, teeth, self.pressure_angle), frame), gear.pitch)
        if self.bore > 0:
            if self.bore / 2 >= gear.root - 0.5 * self.module:
                raise FeatureError("Die Bohrung ist größer als der Fußkreis")
            hole = BRepPrimAPI_MakeCylinder(frame, self.bore / 2, self.thickness).Shape()
            solid = BRepAlgoAPI_Cut(solid, hole).Shape()
        return solid

    # --- Hohlrad ---------------------------------------------------------------------------------------------

    def _ring(self, frame: gp_Ax2):
        """Innenverzahnung: Ring, aus dem ein „Gegenrad“ mit vertauschter Kopf- und Fußhöhe herausgeschnitten wird
        (die Zahnlücken des Hohlrads haben die Form der Zähne eines Außenrads gleicher Zähnezahl)."""
        teeth = self.tooth_count()
        # Lücken um das Flankenspiel breiter: der Schneidzahn wird dicker (negatives Spiel)
        cutter = geometry(self.module, teeth, self.pressure_angle, -self.backlash, addendum=1.25, dedendum=1.0)
        if self.rim <= 0:
            raise FeatureError("Das Hohlrad braucht eine Wandstärke größer als 0")
        outer = BRepPrimAPI_MakeCylinder(frame, cutter.tip + self.rim, self.thickness).Shape()
        face = BRepBuilderAPI_MakeFace(_wire(profile_points(cutter, teeth, self.pressure_angle), frame)).Face()
        # etwas länger als der Ring, damit der Schnitt sauber durch beide Deckflächen geht
        below = gp_Trsf()
        below.SetTranslation(gp_Vec(frame.Direction()) * -0.5)
        face = BRepBuilderAPI_Transform(face, below, True).Shape()
        tool = BRepPrimAPI_MakePrism(face, gp_Vec(frame.Direction()) * (self.thickness + 1.0)).Shape()
        return BRepAlgoAPI_Cut(outer, tool).Shape()

    # --- Zahnstange ------------------------------------------------------------------------------------------

    def _rack(self, frame: gp_Ax2):
        """Gerade Verzahnung entlang der x-Richtung der Ebene: Teilungslinie bei v = 0, Zähne nach +v, darunter ein
        Steg der Höhe `rim`. Flanken als Geraden unter dem Eingriffswinkel (Evolvente mit unendlichem Radius)."""
        teeth = self.tooth_count()
        if self.module <= 0 or teeth < 1:
            raise FeatureError("Modul > 0 und mindestens ein Zahn")
        if self.rim <= 0:
            raise FeatureError("Die Zahnstange braucht eine Steghöhe größer als 0")
        pitch = math.pi * self.module
        top, bottom = self.module, -1.25 * self.module
        slope = math.tan(math.radians(self.pressure_angle))
        half = (pitch / 2 - self.backlash) / 2              # halbe Zahndicke auf der Teilungslinie
        if half <= 0 or half - top * slope <= 0.01:
            raise FeatureError("Die Zähne werden spitz – weniger Flankenspiel oder kleineren Eingriffswinkel wählen")
        outline = [(0.0, bottom - self.rim)]
        for k in range(teeth):
            middle = (k + 0.5) * pitch
            outline += [(middle - half - (-bottom) * slope, bottom), (middle - half + top * slope, top),
                        (middle + half - top * slope, top), (middle + half + (-bottom) * slope, bottom)]
        outline += [(teeth * pitch, bottom), (teeth * pitch, bottom - self.rim)]
        origin, x_dir, y_dir = frame.Location(), gp_Vec(frame.XDirection()), gp_Vec(frame.YDirection())
        polygon = BRepBuilderAPI_MakePolygon()
        start = outline[0]
        if outline[1][0] > 1e-9:
            outline.insert(1, (0.0, bottom))                # linkes Ende senkrecht hoch bis zum Fuß
        for u, v in outline:
            polygon.Add(origin.Translated(x_dir * u + y_dir * v))
        polygon.Add(origin.Translated(x_dir * start[0] + y_dir * start[1]))
        face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
        return BRepPrimAPI_MakePrism(face, gp_Vec(frame.Direction()) * self.thickness).Shape()


ft.FEATURE_CLASSES[Gear.KIND] = Gear
