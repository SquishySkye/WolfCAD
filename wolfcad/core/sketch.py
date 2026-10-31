# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Freie Skizze: Linien, Kreise, Kreisbögen und Splines auf einer Ebene.

Die Elemente werden als einfache Zahlenlisten gespeichert (Millimeter, Winkel in
Grad, Koordinaten in der Skizzenebene). Aus den Kurven werden alle geschlossenen
Bereiche als Profil gebildet; Kurven dürfen sich dabei kreuzen oder berühren.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import numpy as np
from OCP.BOPAlgo import BOPAlgo_Tools
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.collections import HArray1_gp_Pnt
from OCP.GeomAbs import GeomAbs_Line, GeomAbs_Plane
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.Font import Font_FontAspect
from OCP.gp import gp_Ax1, gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Elips, gp_Pnt, gp_Trsf, gp_Vec
from OCP.NCollection import NCollection_String
from OCP.StdPrs import StdPrs_BRepFont, StdPrs_BRepTextBuilder
from OCP.Geom import Geom_BezierCurve
from OCP.collections import Array1_double as TColStd_Array1OfReal, Array1_gp_Pnt as TColgp_Array1OfPnt
from OCP.TCollection import TCollection_AsciiString
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_IN, TopAbs_REVERSED, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound

from . import features as ft
from .features import PLANES, BuildState, Feature, FeatureError

# Art eines Elements -> Namen seiner Zahlenwerte (in dieser Reihenfolge gespeichert)
ENTITY_FIELDS = {
    "Linie": ("x1", "y1", "x2", "y2"),
    "Kreis": ("x", "y", "Radius"),
    "Bogen": ("x", "y", "Radius", "Startwinkel", "Endwinkel"),
    "Ellipse": ("x", "y", "Halbachse 1", "Halbachse 2", "Drehung"),
    "Punkt": ("x", "y"),
    "3D-Linie": ("x1", "y1", "z1", "x2", "y2", "z2"),
    "Kegelschnitt": ("x1", "y1", "x2", "y2", "Scheitel x", "Scheitel y", "Rho"),
}
POINT = "Punkt"     # Skizzenpunkt wie in Fusion: keine Kurve, nur eine Stelle (z. B. für Bohrungen)
# 3D-Skizze wie in Fusion: Linie mit Punkten auch außerhalb der Skizzenebene (z = Abstand längs der Normale).
# Sie bildet kein flaches Profil, gehört aber zu den Kurven der Skizze (z. B. als Sweep-Pfad).
LINE3D = "3D-Linie"


def line3d(x1: float, y1: float, z1: float, x2: float, y2: float, z2: float) -> list:
    return [LINE3D, float(x1), float(y1), float(z1), float(x2), float(y2), float(z2)]


# 3D-Skizze: Spline durch Raumpunkte ["3D-Spline", x1, y1, z1, x2, y2, z2, …] – glatt wie der gewöhnliche Spline,
# aber jeder Punkt mit eigenem Abstand z von der Ebene. Wie die 3D-Linie nur Kurve (Pfad), kein Profil.
SPLINE3D = "3D-Spline"
SPATIAL = (LINE3D, SPLINE3D)    # Elemente, die aus der Skizzenebene herausragen


def spline3d(*points: tuple[float, float, float]) -> list:
    """Freie Kurve, die glatt durch die angegebenen Raumpunkte (x, y, z) läuft."""
    return [SPLINE3D, *(float(value) for point in points for value in point)]


def points3d(entity: list) -> list[tuple[float, float, float]]:
    """Die Raumpunkte einer 3D-Linie bzw. eines 3D-Splines (x, y in der Ebene, z längs der Normale)."""
    return [tuple(entity[i:i + 3]) for i in range(1, len(entity) - 2, 3)]


def track3d(entity: list, steps: int = 12) -> list[tuple[float, float, float]]:
    """Punkte entlang eines räumlichen Elements (3D-Linie: ihre Enden; 3D-Spline: `steps` Stücke je Abschnitt)."""
    if entity[0] == LINE3D:
        return points3d(entity)
    samples = _natural_spline(np.array(entity[1:], dtype=float).reshape(-1, 3), steps)
    return [tuple(float(value) for value in sample) for sample in samples]


def shadow(entity: list) -> list[tuple[float, float]]:
    """Schatten eines räumlichen Elements in der Skizzenebene (zum Anklicken und für den Auswahlrahmen)."""
    return [(x, y) for x, y, _z in track3d(entity)]


def point(x: float, y: float) -> list:
    return [POINT, float(x), float(y)]


# Kegelförmige Kurve wie in Fusion: ["Kegelschnitt", x1, y1, x2, y2, sx, sy, rho] – sie läuft vom Anfang zum Ende,
# beide Enden zeigen auf den Scheitelpunkt (sx, sy). Rho (zwischen 0 und 1) bestimmt die Form:
# unter 0,5 eine Ellipse, genau 0,5 eine Parabel, darüber eine Hyperbel – je größer, desto näher am Scheitel.
CONIC = "Kegelschnitt"
_CONIC_STEPS = 48       # Stücke, mit denen ein Kegelschnitt nachgezeichnet und geschnitten wird


def conic(x1: float, y1: float, x2: float, y2: float, sx: float, sy: float, rho: float = 0.5) -> list:
    return [CONIC, float(x1), float(y1), float(x2), float(y2), float(sx), float(sy), float(rho)]


def conic_weight(rho: float) -> float:
    """Gewicht des Scheitelpunkts in der rationalen Bézierkurve (Rho 0,5 ergibt 1, also eine Parabel)."""
    return rho / (1.0 - rho)


def conic_points(entity: list, steps: int = _CONIC_STEPS) -> list[tuple[float, float]]:
    """Punkte entlang eines Kegelschnitts (rationale quadratische Bézierkurve)."""
    (x1, y1, x2, y2, sx, sy), weight = entity[1:7], conic_weight(entity[7])
    result = []
    for step in range(steps + 1):
        t = step / steps
        a, b, c = (1 - t) ** 2, 2 * weight * t * (1 - t), t * t
        total = a + b + c
        result.append(((a * x1 + b * sx + c * x2) / total, (a * y1 + b * sy + c * y2) / total))
    return result


# Steuerpunkt-Spline wie in Fusion („Spline mit Steuerpunkten“): ["Steuerpunkt-Spline", x1, y1, …, xn, yn, Grad].
# Die Kurve läuft durch den ersten und letzten Punkt; die übrigen Punkte ziehen sie nur zu sich hin (Steuerpolygon) –
# so lässt sich der Bogen fein einstellen. Grad 3 oder 5 wie in Fusion (höherer Grad = weicher).
CP_SPLINE = "Steuerpunkt-Spline"
CP_DEGREES = (3, 5)
_CP_STEPS = 16          # Stücke je Steuerpunkt beim Nachzeichnen


def control_spline(*points: tuple[float, float], degree: int = 3) -> list:
    return [CP_SPLINE, *(float(value) for point in points for value in point), float(degree)]


def control_points(entity: list) -> list[tuple[float, float]]:
    """Die Steuerpunkte eines Steuerpunkt-Splines (ohne den Grad am Ende)."""
    return [(entity[i], entity[i + 1]) for i in range(1, len(entity) - 1, 2)]


def _cp_knots(count: int, degree: int) -> list[float]:
    """Geklemmter, gleichmäßiger Knotenvektor (Kurve beginnt und endet im ersten bzw. letzten Steuerpunkt)."""
    inner = count - degree - 1
    return [0.0] * (degree + 1) + [k / (inner + 1) for k in range(1, inner + 1)] + [1.0] * (degree + 1)


def cp_degree(entity: list) -> int:
    """Der wirksame Grad: höchstens Zahl der Steuerpunkte − 1."""
    return max(1, min(int(round(entity[-1])), len(control_points(entity)) - 1))


def control_spline_points(entity: list, steps: int | None = None) -> list[tuple[float, float]]:
    """Punkte entlang des Steuerpunkt-Splines (de-Boor-Auswertung der B-Spline-Kurve)."""
    poles = np.array(control_points(entity), dtype=float)
    degree = cp_degree(entity)
    knots = _cp_knots(len(poles), degree)
    count = steps or _CP_STEPS * (len(poles) - 1)
    result = []
    for step in range(count + 1):
        t = min(step / count, 1.0 - 1e-12)
        # Abschnitt k mit knots[k] <= t < knots[k+1]
        k = max(i for i in range(degree, len(poles)) if knots[i] <= t)
        local = [poles[j].copy() for j in range(k - degree, k + 1)]
        for r in range(1, degree + 1):
            for j in range(degree, r - 1, -1):
                i = j + k - degree
                alpha = (t - knots[i]) / (knots[i + degree + 1 - r] - knots[i])
                local[j] = (1 - alpha) * local[j - 1] + alpha * local[j]
        result.append((float(local[degree][0]), float(local[degree][1])))
    result[-1] = tuple(float(c) for c in poles[-1])
    return result


def conic_as_spline(entity: list, count: int = 9) -> list:
    """Ein Spline durch Punkte des Kegelschnitts (zum Stutzen und Versetzen, wo nur Splines gehen)."""
    samples = conic_points(entity, count - 1)
    return spline(*samples)
ELLIPSE = "Ellipse"
_ELLIPSE_STEPS = 72     # Stücke, mit denen eine Ellipse nachgezeichnet und geschnitten wird


class _SplineKind(str):
    """Die Art „Spline“; im Vergleich gilt sie auch für Splines mit Tangentengriffen („Griff-Spline“).

    So behandelt jede Stelle, die auf `SPLINE` prüft, beide Formen gleich; wo der Unterschied zählt,
    fragt man `has_handles`.
    """

    def __eq__(self, other) -> bool:
        return str(other) in ("Spline", "Griff-Spline", "Griff-Spline ungleich") if isinstance(other, str) else False

    def __ne__(self, other) -> bool:
        return not self.__eq__(other)

    __hash__ = str.__hash__


# freie Kurve durch beliebig viele Punkte: ["Spline", x1, y1, x2, y2, …]
SPLINE = _SplineKind("Spline")
# Spline mit Tangentengriffen wie in Fusion: ["Griff-Spline", P1x, P1y, G1x, G1y, P2x, P2y, G2x, G2y, …] – auf jeden
# Kurvenpunkt P folgt die Spitze G seines Griffs. Die Kurve verlässt P in Richtung G; je länger der Griff, desto
# bauchiger der Bogen. Auf der anderen Seite von P wirkt der Griff gespiegelt (glatter Durchgang).
HANDLE_SPLINE = "Griff-Spline"
# Griff-Spline mit je Seite unterschiedlich langen Griffen (Fusion: jedes Griffende einzeln ziehen):
# ["Griff-Spline ungleich", P1, G1, P2, G2, …, H1, H2, …] – wie oben, dahinter für jeden Kurvenpunkt die Spitze H
# seines Gegengriffs. Die Richtung von H folgt immer der Gegenrichtung von G (glatter Durchgang); zählt nur der
# Abstand |H − P|. Die H stehen am Ende, damit die Punktnummern von P und G gleich bleiben wie beim Griff-Spline
# (Abhängigkeiten an den Punkten überstehen den Wechsel).
UNEVEN_HANDLE_SPLINE = "Griff-Spline ungleich"
_SPLINE_STEPS = 12      # Stützstellen je Abschnitt zwischen zwei Punkten


def spline(*points: tuple[float, float]) -> list:
    """Freie Kurve, die glatt durch die angegebenen Punkte läuft."""
    return ["Spline", *(float(value) for point in points for value in point)]


def has_handles(entity: list) -> bool:
    """Ist das Element ein Spline mit Tangentengriffen (gleich oder ungleich lang)?"""
    return str(entity[0]) in (HANDLE_SPLINE, UNEVEN_HANDLE_SPLINE)


def has_uneven_handles(entity: list) -> bool:
    """Hat der Griff-Spline eigene Gegengriffe (je Seite unterschiedlich lang)?"""
    return str.__eq__(str(entity[0]), UNEVEN_HANDLE_SPLINE)


def _handle_end(entity: list) -> int:
    """Index hinter dem letzten Paar Kurvenpunkt/Griffspitze (danach folgen ggf. die Gegengriffe)."""
    if has_uneven_handles(entity):
        return 1 + 4 * ((len(entity) - 1) // 6)
    return len(entity)


def fit_points(entity: list) -> list[tuple[float, float]]:
    """Die Punkte, durch die ein Spline läuft (bei Tangentengriffen ohne die Griffspitzen)."""
    stride = 4 if has_handles(entity) else 2
    return [(entity[i], entity[i + 1]) for i in range(1, _handle_end(entity) - 1, stride)]


def handle_tips(entity: list) -> list[tuple[float, float]]:
    """Die Spitzen der Tangentengriffe eines Griff-Splines, je Kurvenpunkt eine."""
    return [(entity[i], entity[i + 1]) for i in range(3, _handle_end(entity) - 1, 4)] if has_handles(entity) else []


def back_tips(entity: list) -> list[tuple[float, float]]:
    """Die Spitzen der Gegengriffe, je Kurvenpunkt eine: beim gewöhnlichen Griff-Spline das Spiegelbild der
    Griffspitze, beim ungleichen in Gegenrichtung des Griffs mit eigener Länge."""
    points, tips = fit_points(entity), handle_tips(entity)
    if not has_uneven_handles(entity):
        return [(2 * p[0] - g[0], 2 * p[1] - g[1]) for p, g in zip(points, tips)]
    end = _handle_end(entity)
    stored = [(entity[i], entity[i + 1]) for i in range(end, len(entity) - 1, 2)]
    result = []
    for p, g, h in zip(points, tips, stored):
        lead = math.dist(p, g) or 1e-12
        reach = math.dist(p, h)
        result.append((p[0] - (g[0] - p[0]) / lead * reach, p[1] - (g[1] - p[1]) / lead * reach))
    return result


def with_back_tip(entity: list, number: int, x: float, y: float) -> list:
    """Der Griff-Spline, dessen Gegengriff am Punkt `number` bis (x, y) reicht (wie Fusion: das andere Griffende
    ziehen). Die Richtung bleibt die des Griffs; die Länge ist der Abstand längs dieser Richtung (mindestens
    ein Hundertstel der Grifflänge, damit der Bogen nicht abknickt). Ein gewöhnlicher Griff-Spline wird dabei
    zum ungleichen."""
    points, tips = fit_points(entity), handle_tips(entity)
    backs = back_tips(entity)
    p, g = points[number], tips[number]
    lead = math.dist(p, g) or 1e-12
    reach = max(-((x - p[0]) * (g[0] - p[0]) + (y - p[1]) * (g[1] - p[1])) / lead, lead / 100)
    backs[number] = (p[0] - (g[0] - p[0]) / lead * reach, p[1] - (g[1] - p[1]) / lead * reach)
    return [UNEVEN_HANDLE_SPLINE, *entity[1:_handle_end(entity)], *(value for back in backs for value in back)]


def evened(entity: list) -> list:
    """Derselbe Griff-Spline mit wieder gleich langen Griffen je Seite (die Gegengriffe entfallen)."""
    return [HANDLE_SPLINE, *entity[1:_handle_end(entity)]] if has_handles(entity) else list(entity)


def with_handles(entity: list) -> list:
    """Derselbe Spline mit Tangentengriffen: Richtung wie bisher, Länge ein Drittel des Wegs zum Nachbarpunkt."""
    if has_handles(entity):
        return list(entity)
    points = fit_points(entity)
    samples = spline_points(entity)
    steps = (len(samples) - 1) // max(len(points) - 1, 1)
    result = [HANDLE_SPLINE]
    for number, point in enumerate(points):
        # Richtung der Kurve am Punkt aus ihren Nachbarstützstellen
        at = number * steps
        before, after = samples[max(at - 1, 0)], samples[min(at + 1, len(samples) - 1)]
        length = math.dist(before, after) or 1.0
        neighbour = points[number + 1] if number + 1 < len(points) else points[number - 1]
        reach = math.dist(point, neighbour) / 3
        result += [point[0], point[1], point[0] + (after[0] - before[0]) / length * reach,
                   point[1] + (after[1] - before[1]) / length * reach]
    return result


def without_handles(entity: list) -> list:
    """Derselbe Spline ohne Tangentengriffe (er läuft dann wieder frei durch seine Punkte)."""
    return spline(*fit_points(entity))


def scale_handles(entity: list, factor: float) -> list:
    """Alle Tangentengriffe um den Faktor verlängert oder verkürzt (macht die Bögen bauchiger oder flacher)."""
    result = list(entity)
    end = _handle_end(entity)
    for i in range(1, end - 1, 4):
        result[i + 2] = entity[i] + (entity[i + 2] - entity[i]) * factor
        result[i + 3] = entity[i + 1] + (entity[i + 3] - entity[i + 1]) * factor
    # Gegengriffe des ungleichen Splines im selben Maß
    for p, i in zip(fit_points(entity), range(end, len(entity) - 1, 2)):
        result[i] = p[0] + (entity[i] - p[0]) * factor
        result[i + 1] = p[1] + (entity[i + 1] - p[1]) * factor
    return result


def spline_points(entity: list, steps: int = _SPLINE_STEPS) -> list[tuple[float, float]]:
    """Punkte entlang eines Splines (je Abschnitt `steps` Stücke), in Skizzenkoordinaten.

    Gerechnet wird ein natürlicher kubischer Spline; der Kurvenparameter folgt der Wurzel
    der Punktabstände, damit eng und weit gesetzte Punkte keine Schleifen erzeugen.
    """
    if has_handles(entity):
        # Griff-Spline: zwischen zwei Punkten ein kubischer Bogen, dessen Stützpunkte die Griffe vorgeben –
        # er verlässt P in Richtung seines Griffs und kommt beim nächsten Punkt aus der Gegenrichtung von dessen Griff an
        # (über die Spitze des Gegengriffs – beim gewöhnlichen Griff-Spline das Spiegelbild des Griffs)
        points, tips = fit_points(entity), handle_tips(entity)
        result = []
        for (start, lead), (end, arrive) in zip(zip(points, tips), list(zip(points, back_tips(entity)))[1:]):
            for step in range(steps):
                t = step / steps
                a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
                result.append((a * start[0] + b * lead[0] + c * arrive[0] + d * end[0],
                               a * start[1] + b * lead[1] + c * arrive[1] + d * end[1]))
        result.append((float(points[-1][0]), float(points[-1][1])))
        return result
    fit = np.array(entity[1:], dtype=float).reshape(-1, 2)
    return [(float(x), float(y)) for x, y in _natural_spline(fit, steps)]


def _natural_spline(fit: np.ndarray, steps: int) -> np.ndarray:
    """Stützstellen eines natürlichen kubischen Splines durch die Zeilen von `fit` (Punkte in 2D oder 3D).

    Der Kurvenparameter folgt der Wurzel der Punktabstände, damit eng und weit gesetzte Punkte keine
    Schleifen erzeugen; an den Enden ist die zweite Ableitung 0.
    """
    count = len(fit)
    if count < 3:
        return fit.copy()
    gaps = np.maximum(np.sqrt(np.linalg.norm(np.diff(fit, axis=0), axis=1)), 1e-9)
    # Zweite Ableitungen an den Punkten: an den Enden 0 (natürlicher Spline)
    system, right = np.zeros((count, count)), np.zeros((count, fit.shape[1]))
    system[0, 0] = system[-1, -1] = 1.0
    for i in range(1, count - 1):
        system[i, i - 1:i + 2] = gaps[i - 1], 2 * (gaps[i - 1] + gaps[i]), gaps[i]
        right[i] = 6 * ((fit[i + 1] - fit[i]) / gaps[i] - (fit[i] - fit[i - 1]) / gaps[i - 1])
    bend = np.linalg.solve(system, right)
    result = []
    for i in range(count - 1):
        h = gaps[i]
        for step in range(steps):
            a, b = 1 - step / steps, step / steps
            result.append(a * fit[i] + b * fit[i + 1]
                          + ((a ** 3 - a) * bend[i] + (b ** 3 - b) * bend[i + 1]) * h * h / 6)
    result.append(fit[-1])
    return np.array(result)


def ellipse(x: float, y: float, first: float, second: float, rotation: float = 0.0) -> list:
    """Ellipse um (x, y) mit zwei Halbachsen; die erste liegt um `rotation` Grad gegen die x-Achse gedreht."""
    return [ELLIPSE, x, y, first, second, rotation]


def ellipse_arc(x: float, y: float, first: float, second: float, rotation: float, start: float, end: float) -> list:
    """Ellipsenbogen: ein Stück einer Ellipse vom Start- zum Endwinkel, gegen den Uhrzeigersinn.

    Die Winkel (Grad) zählen im Bezugssystem der Ellipse ab ihrer ersten Halbachse; gemeint ist der
    Laufwinkel t der Darstellung (a·cos t, b·sin t), nicht der Winkel des Fahrstrahls.
    """
    return [ELLIPSE, x, y, first, second, rotation, start, end]


def is_ellipse_arc(entity: list) -> bool:
    return entity[0] == ELLIPSE and len(entity) == 8


def ellipse_point(entity: list, angle: float) -> tuple[float, float]:
    """Der Punkt der Ellipse beim Laufwinkel `angle` (Grad)."""
    x, y, first, second, rotation = entity[1:6]
    cosine, sine = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
    u, v = first * math.cos(math.radians(angle)), second * math.sin(math.radians(angle))
    return x + u * cosine - v * sine, y + u * sine + v * cosine


def ellipse_angle(entity: list, x: float, y: float) -> float:
    """Laufwinkel (Grad, 0 … 360) der Ellipse in Richtung des Punkts (x, y)."""
    cx, cy, first, second, rotation = entity[1:6]
    cosine, sine = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
    u, v = (x - cx) * cosine + (y - cy) * sine, -(x - cx) * sine + (y - cy) * cosine
    return math.degrees(math.atan2(v / second, u / first)) % 360


def ellipse_points(entity: list, steps: int = _ELLIPSE_STEPS) -> list[tuple[float, float]]:
    """Punkte rund um die Ellipse (der letzte gleich dem ersten) bzw. entlang eines Ellipsenbogens."""
    start, span = (entity[6], (entity[7] - entity[6]) % 360 or 360.0) if is_ellipse_arc(entity) else (0.0, 360.0)
    steps = max(2, round(steps * span / 360))
    return [ellipse_point(entity, start + span * step / steps) for step in range(steps + 1)]


def _track(entity: list) -> list[tuple[float, float]]:
    """Linienzug, der ein beliebiges Element nachzeichnet (zum Schneiden mit einer Ellipse)."""
    known = track_of(entity)
    if known is not None:
        return known
    if entity[0] == POINT:
        return [(entity[1], entity[2])]         # ein Punkt schneidet nichts
    if entity[0] in SPATIAL:
        return []                               # räumliche Elemente liegen nicht in der Ebene: sie schneiden nichts
    if entity[0] == "Linie":
        return [(entity[1], entity[2]), (entity[3], entity[4])]
    start, span = (0.0, 360.0) if entity[0] == "Kreis" else (entity[4], (entity[5] - entity[4]) % 360 or 360.0)
    steps = max(8, round(180 * span / 360))
    return [(entity[1] + entity[3] * math.cos(math.radians(start + span * k / steps)),
             entity[2] + entity[3] * math.sin(math.radians(start + span * k / steps))) for k in range(steps + 1)]


def ellipse_cuts(entities: list, index: int) -> list[float]:
    """Laufwinkel (Grad, 0 … 360), an denen andere Elemente die Ellipse oder den Ellipsenbogen `index` schneiden.

    Gerechnet wird im Bezugssystem der Ellipse, so gestaucht, dass sie zum Einheitskreis wird: dort ist der
    Schnitt mit jedem Stück eines anderen Elements eine einfache Gleichung.
    """
    entity = entities[index]
    cx, cy, first, second, rotation = entity[1:6]
    cosine, sine = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))

    def local(point) -> tuple[float, float]:
        dx, dy = point[0] - cx, point[1] - cy
        return (dx * cosine + dy * sine) / first, (-dx * sine + dy * cosine) / second

    angles = []
    for number, other in enumerate(entities):
        if number == index:
            continue
        track = [local(point) for point in _track(other)]
        for (ax, ay), (bx, by) in zip(track, track[1:]):
            dx, dy = bx - ax, by - ay
            a, b = dx * dx + dy * dy, 2 * (dx * ax + dy * ay)
            root = b * b - 4 * a * (ax * ax + ay * ay - 1.0)
            if a < 1e-18 or root < 0:
                continue
            for t in ((-b - math.sqrt(root)) / (2 * a), (-b + math.sqrt(root)) / (2 * a)):
                if -1e-9 <= t <= 1 + 1e-9:
                    angles.append(math.degrees(math.atan2(ay + t * dy, ax + t * dx)) % 360)
    found = []
    for angle in sorted(angles):
        if is_ellipse_arc(entity):
            # nur Stellen im Inneren des Bogens zählen, nicht seine Enden
            span = (entity[7] - entity[6]) % 360 or 360
            if not 1e-6 < (angle - entity[6]) % 360 < span - 1e-6:
                continue
        if not found or angle - found[-1] > 1e-6:
            found.append(angle)
    return found


def trim_ellipse(entity: list, cuts: list[float], angle: float) -> list[list]:
    """Was von einer Ellipse oder einem Ellipsenbogen bleibt, wenn das Stück beim Laufwinkel `angle` wegfällt.

    `cuts` sind die Schnittstellen (siehe `ellipse_cuts`). Zurück kommen kein, ein oder zwei Ellipsenbögen.
    """
    base = entity[1:6]
    if not is_ellipse_arc(entity):
        if len(cuts) < 2:
            return []                   # nichts teilt die Ellipse: sie fällt ganz weg
        after = min(cuts, key=lambda cut: (cut - angle) % 360)      # nächste Schnittstelle gegen den Uhrzeigersinn
        before = min(cuts, key=lambda cut: (angle - cut) % 360)     # nächste im Uhrzeigersinn
        return [ellipse_arc(*base, after, before)]
    start, span = entity[6], (entity[7] - entity[6]) % 360 or 360
    clicked = (angle - start) % 360
    places = [(cut - start) % 360 for cut in cuts]
    lower = max((place for place in places if place < clicked), default=None)
    upper = min((place for place in places if place > clicked), default=None)
    kept = []
    if lower is not None:
        kept.append(ellipse_arc(*base, start, (start + lower) % 360))
    if upper is not None:
        kept.append(ellipse_arc(*base, (start + upper) % 360, entity[7]))
    return kept


def track_of(entity: list) -> list[tuple[float, float]] | None:
    """Linienzug, der ein Element ohne eigene Schnittformel nachzeichnet (Spline, Ellipse); sonst None."""
    if entity[0] == SPLINE:
        return spline_points(entity)
    if entity[0] == ELLIPSE:
        return ellipse_points(entity)
    if entity[0] == CONIC:
        return conic_points(entity)
    if entity[0] == CP_SPLINE:
        return control_spline_points(entity)
    return None


def rotated(entity: list, cx: float, cy: float, angle: float) -> list:
    """Das Element um den Punkt (cx, cy) gedreht (Winkel in Grad, gegen den Uhrzeigersinn)."""
    cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))

    def turn(x: float, y: float) -> tuple[float, float]:
        return cx + (x - cx) * cosine - (y - cy) * sine, cy + (x - cx) * sine + (y - cy) * cosine

    moved = list(entity)
    if entity[0] in SPATIAL:
        for i in range(1, len(entity) - 2, 3):              # Drehen um die Normale: z bleibt
            moved[i], moved[i + 1] = turn(entity[i], entity[i + 1])
        return moved
    if entity[0] == CP_SPLINE:
        for i in range(1, len(entity) - 1, 2):      # alle Steuerpunkte drehen mit, der Grad bleibt
            moved[i], moved[i + 1] = turn(entity[i], entity[i + 1])
        return moved
    if entity[0] == CONIC:
        for i in (1, 3, 5):                 # Anfang, Ende und Scheitel drehen mit, Rho bleibt
            moved[i], moved[i + 1] = turn(entity[i], entity[i + 1])
        return moved
    if entity[0] in ("Linie", SPLINE):
        for i in range(1, len(entity), 2):
            moved[i], moved[i + 1] = turn(entity[i], entity[i + 1])
        return moved
    moved[1], moved[2] = turn(entity[1], entity[2])
    if entity[0] == "Bogen":
        moved[4], moved[5] = (entity[4] + angle) % 360, (entity[5] + angle) % 360
    elif entity[0] == ELLIPSE:
        moved[5] = (entity[5] + angle) % 360
    return moved


def _face_under_frame(faces: list, frame: list):
    """Die ebene Fläche, in deren Ebene der Ursprung der Skizzenlage `frame` liegt (gleiche Normale) und die ihn
    enthält; None, wenn es keine gibt."""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON
    from OCP.gp import gp_Pnt
    origin, normal = frame[:3], frame[3:6]
    coplanar = []
    for face in faces:
        other = face_frame(face)
        if other is None or abs(sum(a * b for a, b in zip(normal, other[3:6]))) < 0.9999:
            continue
        if abs(sum((a - b) * n for a, b, n in zip(origin, other[:3], normal))) > 1e-6:
            continue                            # andere Ebene
        state = BRepClass_FaceClassifier(TopoDS.Face(face), gp_Pnt(*origin), 1e-6).State()
        if state in (TopAbs_IN, TopAbs_ON):
            return face
        coplanar.append((math.dist(origin, other[:3]), face))
    # der Ursprung liegt z. B. in einem Loch der Fläche: die nächste Fläche derselben Ebene
    return min(coplanar, key=lambda item: item[0])[1] if coplanar else None


def project_edges(edges: list, axes) -> list:
    """Körperkanten senkrecht auf die Skizzenebene `axes` (gp_Ax2) projiziert, als Skizzenelemente: gerade Kanten
    werden Linien, Kreise parallel zur Ebene Kreise oder Bögen, alles andere kurze Linienstücke."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    origin = (axes.Location().X(), axes.Location().Y(), axes.Location().Z())
    x_axis = (axes.XDirection().X(), axes.XDirection().Y(), axes.XDirection().Z())
    y_axis = (axes.YDirection().X(), axes.YDirection().Y(), axes.YDirection().Z())
    normal = (axes.Direction().X(), axes.Direction().Y(), axes.Direction().Z())

    def flat(point) -> tuple[float, float]:
        offset = (point.X() - origin[0], point.Y() - origin[1], point.Z() - origin[2])
        return (sum(a * b for a, b in zip(offset, x_axis)), sum(a * b for a, b in zip(offset, y_axis)))

    entities = []
    for edge in edges:
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        start, end = flat(curve.Value(first)), flat(curve.Value(last))
        if curve.GetType() == GeomAbs_Line:
            if math.dist(start, end) > 1e-9:
                entities.append(line(*start, *end))
            continue
        if curve.GetType() == GeomAbs_Circle:
            circle_geometry = curve.Circle()
            axis = circle_geometry.Axis().Direction()
            if abs(abs(axis.X() * normal[0] + axis.Y() * normal[1] + axis.Z() * normal[2]) - 1.0) < 1e-9:
                centre, radius = flat(circle_geometry.Location()), circle_geometry.Radius()
                if abs(abs(last - first) - 2 * math.pi) < 1e-9:
                    entities.append(circle(*centre, radius))
                    continue
                middle = flat(curve.Value((first + last) / 2))
                angles = [math.degrees(math.atan2(p[1] - centre[1], p[0] - centre[0])) % 360
                          for p in (start, middle, end)]
                forward = (angles[1] - angles[0]) % 360 < (angles[2] - angles[0]) % 360
                entities.append(arc(*centre, radius, *((angles[0], angles[2]) if forward else (angles[2], angles[0]))))
                continue
        points = [flat(curve.Value(first + (last - first) * k / 24)) for k in range(25)]
        entities += [line(*a, *b) for a, b in zip(points, points[1:]) if math.dist(a, b) > 1e-9]
    return entities


def line(x1: float, y1: float, x2: float, y2: float) -> list:
    return ["Linie", x1, y1, x2, y2]


def circle(x: float, y: float, radius: float) -> list:
    return ["Kreis", x, y, radius]


def arc(x: float, y: float, radius: float, start: float, end: float) -> list:
    """Kreisbogen um (x, y) vom Start- zum Endwinkel, gegen den Uhrzeigersinn."""
    return ["Bogen", x, y, radius, start, end]


def polygon(cx: float, cy: float, px: float, py: float, sides: int) -> list[list]:
    """Regelmäßiges Vieleck um (cx, cy), dessen erste Ecke bei (px, py) liegt."""
    radius, first = math.dist((cx, cy), (px, py)), math.atan2(py - cy, px - cx)
    corners = [(cx + radius * math.cos(first + 2 * math.pi * k / sides),
                cy + radius * math.sin(first + 2 * math.pi * k / sides)) for k in range(sides)]
    return [line(*start, *end) for start, end in zip(corners, corners[1:] + corners[:1])]


POLYGON_KINDS = ("Eingeschrieben", "Umschrieben", "Kante")     # wie Fusions Polygon-Arten
SLOT_KINDS = ("Mitte zu Mitte", "Gesamt", "Mittelpunkt", "3-Punkt-Bogen", "Mittelpunkt-Bogen")   # Fusions Langlöcher


def polygon_circumscribed(cx: float, cy: float, px: float, py: float, sides: int) -> list[list]:
    """Vieleck um einen Kreis (Fusion „umschrieben“): (px, py) ist die Mitte einer Seite."""
    apothem, middle = math.dist((cx, cy), (px, py)), math.atan2(py - cy, px - cx)
    radius = apothem / math.cos(math.pi / sides)
    first = middle - math.pi / sides
    corner = (cx + radius * math.cos(first), cy + radius * math.sin(first))
    return polygon(cx, cy, *corner, sides)


def polygon_edge(ax: float, ay: float, bx: float, by: float, side_x: float, side_y: float, sides: int) -> list[list]:
    """Vieleck über einer Kante von (ax, ay) nach (bx, by), auf der Seite des Punkts (side_x, side_y)."""
    length = math.dist((ax, ay), (bx, by))
    if length < 1e-9:
        return []
    ux, uy = (bx - ax) / length, (by - ay) / length
    left = (side_x - ax) * -uy + (side_y - ay) * ux >= 0     # liegt der Punkt links der Kante?
    nx, ny = (-uy, ux) if left else (uy, -ux)
    apothem = length / (2 * math.tan(math.pi / sides))
    cx, cy = (ax + bx) / 2 + nx * apothem, (ay + by) / 2 + ny * apothem
    shape = polygon(cx, cy, ax, ay, sides)
    # die erste Seite soll genau die Kante a→b sein: Drehsinn so wählen, dass die zweite Ecke b ist
    if math.dist(shape[0][3:5], (bx, by)) > 1e-6:
        shape = [line(*entity[3:5], *entity[1:3]) for entity in reversed(shape)]
    return shape


def slot_overall(ax: float, ay: float, bx: float, by: float, radius: float) -> list[list]:
    """Langloch „Gesamt“: (a) und (b) sind die äußeren Enden; die Mittelpunkte liegen um die halbe Breite innen."""
    length = math.dist((ax, ay), (bx, by))
    if length <= 2 * radius + 1e-9:
        return []
    ux, uy = (bx - ax) / length, (by - ay) / length
    return slot(ax + ux * radius, ay + uy * radius, bx - ux * radius, by - uy * radius, radius)


def arc_slot(cx: float, cy: float, radius: float, start: float, end: float, half_width: float) -> list[list]:
    """Bogen-Langloch: Mittellinie ist der Bogen um (cx, cy) von `start` nach `end` (Grad, gegen den Uhrzeiger)."""
    if not 0 < half_width < radius:
        return []
    begin = (cx + radius * math.cos(math.radians(start)), cy + radius * math.sin(math.radians(start)))
    finish = (cx + radius * math.cos(math.radians(end)), cy + radius * math.sin(math.radians(end)))
    return [arc(cx, cy, radius + half_width, start, end),                    # außen
            arc(*finish, half_width, end, end + 180.0),                     # Endkappe
            arc(cx, cy, radius - half_width, start, end),                    # innen
            arc(*begin, half_width, start + 180.0, start + 360.0)]           # Anfangskappe


def tangent_circles_two(first: list, second: list, radius: float) -> list[tuple[float, float, float]]:
    """Kreise mit Radius `radius`, die beide Linien(geraden) berühren: bis zu vier (x, y, r)."""
    found = []
    for side_a in (1, -1):
        for side_b in (1, -1):
            centre = _offset_crossing(first, side_a * radius, second, side_b * radius)
            if centre is not None:
                found.append((*centre, radius))
    return found


def tangent_circles_three(lines: list[list]) -> list[tuple[float, float, float]]:
    """Kreise, die drei Linien(geraden) berühren (Inkreis und Ankreise): je (x, y, r)."""
    found = []
    first, second, third = lines
    for side_a in (1, -1):
        for side_b in (1, -1):
            # Mitte liegt gleich weit (r) von allen dreien: Schnitt der Winkelhalbierenden – über Gleichungen
            for side_c in (1, -1):
                solution = _equidistant(first, side_a, second, side_b, third, side_c)
                if solution is not None and solution[2] > 1e-9 and all(
                        abs(math.dist(solution[:2], found_one[:2])) > 1e-6 for found_one in found):
                    found.append(solution)
    return found


def _line_normal_form(entity: list) -> tuple[float, float, float]:
    """Gerade als n·p = d mit Einheitsnormale n."""
    ax, ay, bx, by = entity[1:5]
    length = math.dist((ax, ay), (bx, by)) or 1.0
    nx, ny = -(by - ay) / length, (bx - ax) / length
    return nx, ny, nx * ax + ny * ay


def _offset_crossing(first: list, offset_a: float, second: list, offset_b: float):
    """Schnittpunkt der um offset_a bzw. offset_b (längs der Normale) verschobenen Geraden; None bei parallel."""
    n1x, n1y, d1 = _line_normal_form(first)
    n2x, n2y, d2 = _line_normal_form(second)
    determinant = n1x * n2y - n1y * n2x
    if abs(determinant) < 1e-12:
        return None
    d1, d2 = d1 + offset_a, d2 + offset_b
    return (d1 * n2y - n1y * d2) / determinant, (n1x * d2 - d1 * n2x) / determinant


def _equidistant(first, side_a, second, side_b, third, side_c):
    """Mitte (x, y) und Radius r mit n_i·p − d_i = s_i·r für alle drei Geraden (lineares 3×3-System)."""
    rows = []
    for entity, side in ((first, side_a), (second, side_b), (third, side_c)):
        nx, ny, d = _line_normal_form(entity)
        rows.append((nx, ny, -side, d))
    matrix = np.array([row[:3] for row in rows], dtype=float)
    right = np.array([row[3] for row in rows], dtype=float)
    if abs(np.linalg.det(matrix)) < 1e-12:
        return None
    x, y, r = np.linalg.solve(matrix, right)
    return (float(x), float(y), float(r)) if r > 0 else None


def slot(x1: float, y1: float, x2: float, y2: float, radius: float) -> list[list]:
    """Langloch: zwei Halbkreise um (x1, y1) und (x2, y2), verbunden durch zwei Linien."""
    length = math.dist((x1, y1), (x2, y2))
    along = math.degrees(math.atan2(y2 - y1, x2 - x1))
    nx, ny = -(y2 - y1) / length * radius, (x2 - x1) / length * radius     # quer zur Achse
    return [line(x1 + nx, y1 + ny, x2 + nx, y2 + ny), arc(x2, y2, radius, along - 90, along + 90),
            line(x2 - nx, y2 - ny, x1 - nx, y1 - ny), arc(x1, y1, radius, along + 90, along + 270)]


def _carrier_hits(first: list, second: list) -> list[tuple[float, float]]:
    """Schnittpunkte der Trägerkurven zweier Elemente (Linie: ganze Gerade; Kreis/Bogen: ganzer Kreis)."""
    if first[0] != "Linie" and second[0] == "Linie":
        first, second = second, first
    if first[0] == "Linie" and second[0] == "Linie":
        ax, ay, bx, by = first[1:5]
        cx, cy, dx, dy = second[1:5]
        determinant = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx)
        if abs(determinant) < 1e-12:
            return []
        t = ((cx - ax) * (dy - cy) - (cy - ay) * (dx - cx)) / determinant
        return [(ax + (bx - ax) * t, ay + (by - ay) * t)]
    if first[0] == "Linie":
        ax, ay, bx, by = first[1:5]
        mx, my, radius = second[1:4]
        dx, dy = bx - ax, by - ay
        a, b = dx * dx + dy * dy, 2 * (dx * (ax - mx) + dy * (ay - my))
        root = b * b - 4 * a * ((ax - mx) ** 2 + (ay - my) ** 2 - radius * radius)
        if a < 1e-18 or root < 0:
            return []
        return [(ax + t * dx, ay + t * dy) for t in ((-b - math.sqrt(root)) / (2 * a), (-b + math.sqrt(root)) / (2 * a))]
    (mx, my, radius), (ox, oy, other_radius) = first[1:4], second[1:4]
    apart = math.dist((mx, my), (ox, oy))
    if apart < 1e-12 or apart > radius + other_radius + 1e-9 or apart < abs(radius - other_radius) - 1e-9:
        return []
    along = (radius * radius - other_radius * other_radius + apart * apart) / (2 * apart)
    across = math.sqrt(max(radius * radius - along * along, 0.0))
    ux, uy = (ox - mx) / apart, (oy - my) / apart
    return [(mx + ux * along - uy * across * side, my + uy * along + ux * across * side) for side in (1, -1)]


def _set_end(entity: list, at_end: bool, point: tuple[float, float]) -> None:
    """Legt Anfang oder Ende eines Elements auf den Punkt (bei Bögen: den passenden Winkel)."""
    if entity[0] == CONIC:
        entity[3 if at_end else 1:5 if at_end else 3] = point
        return
    if entity[0] == CP_SPLINE:
        position = len(entity) - 3 if at_end else 1     # letzter bzw. erster Steuerpunkt
        entity[position:position + 2] = point
        return
    if entity[0] in ("Linie", SPLINE):
        # beim Griff-Spline steht hinter dem letzten Kurvenpunkt noch sein Griff, der mitwandert
        position = (_handle_end(entity) - 4 if has_handles(entity) else len(entity) - 2) if at_end else 1
        if has_handles(entity):
            entity[position + 2] += point[0] - entity[position]
            entity[position + 3] += point[1] - entity[position + 1]
            if has_uneven_handles(entity):
                # der Gegengriff dieses Punkts wandert ebenso mit
                back = len(entity) - 2 if at_end else _handle_end(entity)
                entity[back] += point[0] - entity[position]
                entity[back + 1] += point[1] - entity[position + 1]
        entity[position:position + 2] = point
    else:
        entity[5 if at_end else 4] = math.degrees(math.atan2(point[1] - entity[2], point[0] - entity[1])) % 360


def _touch_point(entity: list, centre: tuple[float, float]) -> tuple[float, float]:
    """Der Punkt des Elements (bzw. seiner Trägerkurve), der dem Punkt `centre` am nächsten liegt."""
    if entity[0] == "Linie":
        ax, ay, bx, by = entity[1:5]
        t = ((centre[0] - ax) * (bx - ax) + (centre[1] - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)
        return ax + (bx - ax) * t, ay + (by - ay) * t
    reach = math.dist(centre, entity[1:3]) or 1.0
    return (entity[1] + (centre[0] - entity[1]) / reach * entity[3],
            entity[2] + (centre[1] - entity[2]) / reach * entity[3])


def _on_entity(entity: list, point: tuple[float, float]) -> bool:
    """Liegt der Punkt (der auf der Trägerkurve liegt) innerhalb des Elements, nicht an seinen Enden?"""
    if entity[0] == "Linie":
        ax, ay, bx, by = entity[1:5]
        t = ((point[0] - ax) * (bx - ax) + (point[1] - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)
        return 1e-9 < t < 1 - 1e-9
    angle = math.degrees(math.atan2(point[1] - entity[2], point[0] - entity[1]))
    span = (entity[5] - entity[4]) % 360 or 360
    return 1e-7 < (angle - entity[4]) % 360 < span - 1e-7


CHAMFER_KINDS = ("Gleicher Abstand", "Abstand und Winkel", "Zwei Abstände")     # wie Fusions Skizzenfase


def corner_chamfer(first: list, second: list, kind: str, distance: float, distance2: float = 0.0,
                   angle: float = 45.0) -> tuple[list, list, list] | None:
    """Fase an der gemeinsamen Ecke zweier Linien: beide werden gekürzt, dazwischen kommt eine Linie.

    Arten wie in Fusion: gleicher Abstand (d), Abstand und Winkel (d auf der ersten Linie, Winkel der Fase
    gegen sie) oder zwei Abstände (d auf der ersten, d2 auf der zweiten). None, wenn es nicht passt.
    """
    if first[0] != "Linie" or second[0] != "Linie" or distance <= 0:
        return None
    ends = [_ends(first), _ends(second)]
    shared = next(((i, j) for i in (0, 1) for j in (0, 1) if math.dist(ends[0][i], ends[1][j]) < 1e-6), None)
    if shared is None:
        return None
    corner = ends[0][shared[0]]
    far = [ends[0][1 - shared[0]], ends[1][1 - shared[1]]]
    lengths = [math.dist(corner, point) for point in far]
    units = [((point[0] - corner[0]) / length, (point[1] - corner[1]) / length) for point, length in zip(far, lengths)]
    if kind == CHAMFER_KINDS[1]:
        # Sinussatz im Dreieck Ecke – Fasenanfang – Fasenende: Eckwinkel φ, Winkel der Fase θ an der ersten Linie
        between = math.acos(max(-1.0, min(1.0, units[0][0] * units[1][0] + units[0][1] * units[1][1])))
        theta = math.radians(angle)
        if not 0 < theta < math.pi - between:
            return None
        distance2 = distance * math.sin(theta) / math.sin(math.pi - between - theta)
    elif kind == CHAMFER_KINDS[0]:
        distance2 = distance
    # bis genau an das ferne Ende erlaubt (die Linie schrumpft dann auf 0 und fällt im Editor weg)
    if distance2 <= 0 or distance > lengths[0] + 1e-9 or distance2 > lengths[1] + 1e-9:
        return None
    cut = [(corner[0] + units[0][0] * distance, corner[1] + units[0][1] * distance),
           (corner[0] + units[1][0] * distance2, corner[1] + units[1][1] * distance2)]
    trimmed = [list(first), list(second)]
    for entity, which, point in zip(trimmed, shared, cut):
        _set_end(entity, which == 1, point)
    return trimmed[0], trimmed[1], line(*cut[0], *cut[1])


def end_direction(entity: list, at_end: bool) -> tuple[float, float] | None:
    """Einheitsrichtung, in der die Kurve an ihrem Anfang bzw. Ende nach außen verlässt (für Übergangskurven)."""
    if entity[0] == "Bogen":
        angle = math.radians(entity[5] if at_end else entity[4])
        # gegen den Uhrzeiger: am Ende weiter gegen den Uhrzeiger, am Anfang mit dem Uhrzeiger hinaus
        return (-math.sin(angle), math.cos(angle)) if at_end else (math.sin(angle), -math.cos(angle))
    track = track_of(entity) or ([tuple(entity[1:3]), tuple(entity[3:5])] if entity[0] == "Linie" else None)
    if not track or len(track) < 2:
        return None
    tip, before = (track[-1], track[-2]) if at_end else (track[0], track[1])
    length = math.dist(tip, before)
    return ((tip[0] - before[0]) / length, (tip[1] - before[1]) / length) if length > 1e-12 else None


def blend_curve(first: list, first_end: bool, second: list, second_end: bool) -> list | None:
    """Übergangskurve wie Fusion (G1): ein Spline mit Tangentengriffen vom Ende der ersten Kurve zum Ende der
    zweiten, der beide tangential fortsetzt. Die Griffe sind je ein Drittel des Abstands lang."""
    ends = [_ends(first), _ends(second)]
    if ends[0] is None or ends[1] is None:
        return None
    start, finish = ends[0][1 if first_end else 0], ends[1][1 if second_end else 0]
    leave, enter = end_direction(first, first_end), end_direction(second, second_end)
    reach = math.dist(start, finish) / 3
    if leave is None or enter is None or reach < 1e-9:
        return None
    lead = (start[0] + leave[0] * reach, start[1] + leave[1] * reach)
    # am Ziel kommt die Kurve aus der Gegenrichtung von dessen Austritt an: der Griff zeigt in die Kurve hinein
    tail = (finish[0] - enter[0] * reach, finish[1] - enter[1] * reach)
    return [HANDLE_SPLINE, *start, *lead, *finish, *tail]


def blend_curve_g2(first: list, first_end: bool, second: list, second_end: bool) -> list | None:
    """Übergangskurve mit stetiger Krümmung (G2) zwischen den Enden zweier Linien: ein Spline mit Griffen und
    einem Zwischenpunkt, dessen innere Bézierpunkte auf den Tangenten der Linien liegen (dort Krümmung 0).

    Für Bögen liefert sie dieselbe Form; die Krümmung gleicht dann die Abhängigkeit „Krümmung“ an.
    """
    ends = [_ends(first), _ends(second)]
    if ends[0] is None or ends[1] is None:
        return None
    start, finish = ends[0][1 if first_end else 0], ends[1][1 if second_end else 0]
    leave, enter = end_direction(first, first_end), end_direction(second, second_end)
    reach = math.dist(start, finish) / 3
    if leave is None or enter is None or reach < 1e-9:
        return None
    # Q_A auf der Tangente am Anfang, Q_B auf der Tangente am Ziel; der Zwischenpunkt liegt mittig zwischen beiden
    qa = (start[0] + leave[0] * reach, start[1] + leave[1] * reach)
    qb = (finish[0] + enter[0] * reach, finish[1] + enter[1] * reach)
    middle = ((qa[0] + qb[0]) / 2, (qa[1] + qb[1]) / 2)
    lead = (start[0] + leave[0] * reach / 2, start[1] + leave[1] * reach / 2)
    tail = (finish[0] - enter[0] * reach / 2, finish[1] - enter[1] * reach / 2)
    # Griff des Zwischenpunkts zeigt auf Q_B (so liegt sein gespiegeltes Gegenstück auf Q_A)
    return [HANDLE_SPLINE, *start, *lead, *middle, *qb, *finish, *tail]


def corner_fillet(first: list, second: list, radius: float) -> tuple[list, list, list] | None:
    """Rundet die Ecke ab, an der sich zwei Elemente treffen (Linien oder Bögen, auch gemischt).

    Zurück kommen die beiden gekürzten Elemente und der Bogen dazwischen; None, wenn sie keinen
    gemeinsamen Endpunkt haben, dort schon tangential ineinander übergehen oder der Radius nicht passt.
    """
    if first[0] not in ("Linie", "Bogen") or second[0] not in ("Linie", "Bogen") or radius <= 0:
        return None
    ends = [_ends(first), _ends(second)]
    shared = next(((i, j) for i in (0, 1) for j in (0, 1) if math.dist(ends[0][i], ends[1][j]) < 1e-6), None)
    if shared is None:
        return None
    corner = ends[0][shared[0]]

    def carriers(entity: list) -> list[list]:
        """Kurven, auf denen der Mittelpunkt der Rundung liegen kann: um den Radius versetzt."""
        if entity[0] == "Linie":
            ax, ay, bx, by = entity[1:5]
            length = math.dist((ax, ay), (bx, by))
            nx, ny = -(by - ay) / length * radius, (bx - ax) / length * radius
            return [line(ax + nx * side, ay + ny * side, bx + nx * side, by + ny * side) for side in (1, -1)]
        return [circle(entity[1], entity[2], entity[3] + grow) for grow in (radius, -radius) if entity[3] + grow > 1e-9]

    best = None
    for one in carriers(first):
        for two in carriers(second):
            for centre in _carrier_hits(one, two):
                touch = _touch_point(first, centre), _touch_point(second, centre)
                # Die Rundung muss beide Elemente in ihrem Inneren berühren – oder genau an ihrem fernen Ende
                # (wie in Fusion: Quadrat mit Radius = halbe Seite wird zum Kreis, die Linie schrumpft auf 0)
                far = [ends[0][1 - shared[0]], ends[1][1 - shared[1]]]
                if not all(_on_entity(entity, point) or math.dist(point, end) < 1e-6
                           for entity, point, end in zip((first, second), touch, far)):
                    continue
                distance = math.dist(centre, corner)
                if best is None or distance < best[0]:
                    best = (distance, centre, touch)
    if best is None:
        return None
    _distance, centre, touch = best
    angles = [math.degrees(math.atan2(t[1] - centre[1], t[0] - centre[0])) % 360 for t in touch]
    if abs((angles[1] - angles[0]) % 360 - 180) < 1e-6 or math.dist(*touch) < 1e-9:
        return None             # die Elemente gehen schon glatt ineinander über
    # Der Bogen läuft gegen den Uhrzeigersinn über den kürzeren Weg
    start, end = (angles[0], angles[1]) if (angles[1] - angles[0]) % 360 < 180 else (angles[1], angles[0])
    trimmed = [list(first), list(second)]
    for entity, which, point in zip(trimmed, shared, touch):
        _set_end(entity, which == 1, point)
    return trimmed[0], trimmed[1], arc(*centre, radius, start, end)


def line_cuts(entities: list, index: int) -> list[float]:
    """Stellen (0 … 1 entlang der Linie `index`), an denen andere Elemente sie schneiden."""
    ax, ay, bx, by = entities[index][1:5]
    dx, dy = bx - ax, by - ay
    cuts = []
    for number, other in enumerate(entities):
        if number == index or other[0] in (POINT, *SPATIAL):
            continue                            # Punkte schneiden keine Linie
        if other[0] in ("Linie", SPLINE, ELLIPSE, CONIC, CP_SPLINE):
            # Spline und Ellipse zählen als Zug kurzer Linienstücke
            track = track_of(other) or [other[1:3], other[3:5]]
            for (cx, cy), (ex, ey) in zip(track, track[1:]):
                determinant = dx * (ey - cy) - dy * (ex - cx)
                if abs(determinant) < 1e-12:
                    continue
                t = ((cx - ax) * (ey - cy) - (cy - ay) * (ex - cx)) / determinant
                s = ((cx - ax) * dy - (cy - ay) * dx) / determinant
                if -1e-9 <= s <= 1 + 1e-9:
                    cuts.append(t)
            continue
        # Kreis oder Bogen: |A + t·d - M|² = r²
        mx, my, radius = other[1:4]
        a, b = dx * dx + dy * dy, 2 * (dx * (ax - mx) + dy * (ay - my))
        root = b * b - 4 * a * ((ax - mx) ** 2 + (ay - my) ** 2 - radius * radius)
        if root < 0:
            continue
        for t in ((-b - math.sqrt(root)) / (2 * a), (-b + math.sqrt(root)) / (2 * a)):
            if other[0] == "Bogen":
                angle = math.degrees(math.atan2(ay + t * dy - my, ax + t * dx - mx))
                if (angle - other[4]) % 360 > ((other[5] - other[4]) % 360 or 360) + 1e-6:
                    continue
            cuts.append(t)
    found = []
    for t in sorted(t for t in cuts if 1e-9 < t < 1 - 1e-9):
        # Dieselbe Stelle kann doppelt vorkommen (z. B. am Übergang zweier Stücke eines Splines)
        if not found or t - found[-1] > 1e-7:
            found.append(t)
    return found


def extended_line(entities: list, index: int, x: float, y: float) -> list | None:
    """Die Linie `index`, am Ende nahe (x, y) bis zum nächsten anderen Element verlängert (None: nichts im Weg)."""
    entity = entities[index]
    ax, ay, bx, by = entity[1:5]
    at_end = math.dist((x, y), (bx, by)) <= math.dist((x, y), (ax, ay))
    # Gerade über die ganze Länge des Modells nach beiden Seiten: wo sie andere Elemente trifft
    reach = 1e5
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    probe = line(ax - ux * reach, ay - uy * reach, bx + ux * reach, by + uy * reach)
    cuts = line_cuts([*entities[:index], probe, *entities[index + 1:]], index)
    total = length + 2 * reach
    along = [t * total - reach for t in cuts]            # Lage längs der Linie, 0 = Anfang, length = Ende
    if at_end:
        beyond = [a for a in along if a > length + 1e-7]
        if not beyond:
            return None
        end = min(beyond)
        return line(ax, ay, ax + ux * end, ay + uy * end)
    before = [a for a in along if a < -1e-7]
    if not before:
        return None
    start = max(before)
    return line(ax + ux * start, ay + uy * start, bx, by)


def broken(entities: list, index: int) -> list[list]:
    """Das Element `index` an allen Stellen geteilt, an denen andere Elemente es schneiden (Linien und Bögen)."""
    entity = entities[index]
    if entity[0] == "Linie":
        ax, ay, bx, by = entity[1:5]
        points = [0.0, *line_cuts(entities, index), 1.0]
        return [line(ax + (bx - ax) * s, ay + (by - ay) * s, ax + (bx - ax) * t, ay + (by - ay) * t)
                for s, t in zip(points, points[1:])]
    if entity[0] in ("Kreis", "Bogen"):
        cuts = round_cuts(entities, index)
        if entity[0] == "Kreis":
            if len(cuts) < 2:
                return [entity]
            return [arc(*entity[1:4], a, b) for a, b in zip(cuts, cuts[1:] + cuts[:1])]
        start, span = entity[4], (entity[5] - entity[4]) % 360 or 360
        inner = sorted((cut - start) % 360 for cut in cuts)
        stops = [start, *[(start + place) % 360 for place in inner], entity[5]]
        return [arc(*entity[1:4], a, b) for a, b in zip(stops, stops[1:])]
    return [entity]


def mirrored(entity: list, axis: list) -> list:
    """Spiegelbild eines Elements an der Geraden durch die Linie `axis`."""
    if entity[0] == SPLINE:
        points = [mirrored(line(entity[i], entity[i + 1], 0.0, 0.0), axis)[1:3]
                  for i in range(1, len(entity), 2)]
        # ein Griff-Spline bleibt einer: seine Griffe werden mitgespiegelt
        return [str(entity[0]), *spline(*points)[1:]]
    ax, ay, bx, by = axis[1:5]
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy

    def reflect(x: float, y: float) -> tuple[float, float]:
        t = ((x - ax) * dx + (y - ay) * dy) / length
        return 2 * (ax + t * dx) - x, 2 * (ay + t * dy) - y

    if entity[0] == "Linie":
        return line(*reflect(entity[1], entity[2]), *reflect(entity[3], entity[4]))
    if entity[0] == CONIC:
        return conic(*reflect(entity[1], entity[2]), *reflect(entity[3], entity[4]),
                     *reflect(entity[5], entity[6]), entity[7])
    if entity[0] == CP_SPLINE:
        return control_spline(*[reflect(*p) for p in control_points(entity)], degree=int(entity[-1]))
    if entity[0] in SPATIAL:
        # Spiegeln an einer Geraden der Ebene: z bleibt
        return [entity[0], *(value for x, y, z in points3d(entity) for value in (*reflect(x, y), z))]
    centre = reflect(entity[1], entity[2])
    if entity[0] == POINT:
        return point(*centre)
    if entity[0] == "Kreis":
        return circle(*centre, entity[3])
    if entity[0] == ELLIPSE:
        flipped = ellipse(*centre, entity[3], entity[4], (2 * math.degrees(math.atan2(dy, dx)) - entity[5]) % 360)
        if is_ellipse_arc(entity):
            # Spiegeln kehrt den Drehsinn um: Anfang und Ende des Bogens tauschen die Rollen
            flipped += [(-entity[7]) % 360, (-entity[6]) % 360]
        return flipped
    # Spiegeln kehrt den Drehsinn um: Anfang und Ende des Bogens tauschen die Rollen
    turn = 2 * math.degrees(math.atan2(dy, dx))
    return arc(*centre, entity[3], (turn - entity[5]) % 360, (turn - entity[4]) % 360)


def _ends(entity: list) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Anfangs- und Endpunkt eines Elements (None beim Vollkreis, beim Punkt und bei 3D-Linien)."""
    if entity[0] in (POINT, *SPATIAL):
        return None
    if entity[0] in ("Linie", CONIC):
        return (entity[1], entity[2]), (entity[3], entity[4])
    if entity[0] == CP_SPLINE:
        points = control_points(entity)
        return points[0], points[-1]
    if entity[0] == SPLINE:
        points = fit_points(entity)
        return points[0], points[-1]
    if is_ellipse_arc(entity):
        return ellipse_point(entity, entity[6]), ellipse_point(entity, entity[7])
    if entity[0] in ("Kreis", ELLIPSE):
        return None
    return tuple((entity[1] + entity[3] * math.cos(math.radians(angle)),
                  entity[2] + entity[3] * math.sin(math.radians(angle))) for angle in entity[4:6])


def chain_of(entities: list, index: int) -> list[tuple[int, bool]]:
    """Der zusammenhängende Linienzug, zu dem das Element gehört: (Nummer, läuft rückwärts?).

    Die Reihenfolge folgt dem Zug von einem Ende zum anderen; das angeklickte Element
    läuft dabei vorwärts (bei Bögen: gegen den Uhrzeigersinn).
    """
    if _ends(entities[index]) is None:
        return [(index, False)]
    used, chain = {index}, [(index, False)]

    def extend(point, forward: bool) -> None:
        while True:
            found = None
            for number, entity in enumerate(entities):
                ends = _ends(entity)
                if number in used or ends is None:
                    continue
                for side in (0, 1):
                    if math.dist(ends[side], point) < 1e-6:
                        found = (number, side)
            if found is None:
                return
            number, side = found
            used.add(number)
            ends = _ends(entities[number])
            # Vorwärts gesucht hängt das neue Element mit `side` am Zug; es läuft rückwärts, wenn das sein Ende ist
            if forward:
                chain.append((number, side == 1))
            else:
                chain.insert(0, (number, side == 0))
            point = ends[1 - side]

    start, end = _ends(entities[index])
    extend(end, True)
    if len(chain) == 1 or math.dist(_ends(entities[chain[-1][0]])[0 if chain[-1][1] else 1], start) > 1e-6:
        extend(start, False)        # kein geschlossener Zug: auch nach hinten verlängern
    return chain


def offset_chain(entities: list, index: int, distance: float, point: tuple[float, float]) -> list[list] | None:
    """Versatz des Linienzugs, zu dem das Element `index` gehört, um `distance` zur Seite von `point`.

    Ecken zwischen zwei Linien werden neu verschnitten; tangentiale Übergänge bleiben von
    selbst verbunden. None, wenn ein Bogen dabei verschwinden würde.
    """
    # Kegelschnitte werden (wie in Fusion) als Splines versetzt
    entities = [conic_as_spline(other) if other[0] == CONIC
                else spline(*control_spline_points(other, 8 * len(control_points(other)))[::2]) if other[0] == CP_SPLINE
                else other for other in entities]
    entity = entities[index]
    if entity[0] == ELLIPSE:
        # beide Halbachsen um den Abstand ändern (eine gute Näherung der Parallelkurve)
        local = rotated(line(*point, *point), entity[1], entity[2], -entity[5])[1:3]
        inside = ((local[0] - entity[1]) / entity[3]) ** 2 + ((local[1] - entity[2]) / entity[4]) ** 2 < 1
        first, second = (entity[3] - distance, entity[4] - distance) if inside else (entity[3] + distance, entity[4] + distance)
        # ein Ellipsenbogen behält seine Winkel
        return [[*ellipse(entity[1], entity[2], first, second, entity[5]), *entity[6:]]] if min(first, second) > 1e-9 else None
    if entity[0] == "Kreis":
        inside = math.dist(point, entity[1:3]) < entity[3]
        radius = entity[3] - distance if inside else entity[3] + distance
        return [circle(entity[1], entity[2], radius)] if radius > 1e-9 else None
    # Seite: links oder rechts der Laufrichtung des angeklickten Elements
    if entity[0] == "Linie":
        cross = (entity[3] - entity[1]) * (point[1] - entity[2]) - (entity[4] - entity[2]) * (point[0] - entity[1])
        left = cross > 0
    elif entity[0] == SPLINE:
        # am nächstgelegenen Kurvenstück entscheidet sich, auf welcher Seite der Punkt liegt
        samples = spline_points(entity)
        at = min(range(len(samples) - 1), key=lambda i: math.dist(samples[i], point))
        (ax, ay), (bx, by) = samples[at], samples[at + 1]
        left = (bx - ax) * (point[1] - ay) - (by - ay) * (point[0] - ax) > 0
    else:
        left = math.dist(point, entity[1:3]) < entity[3]    # gegen den Uhrzeigersinn liegt die Mitte links
    shift = distance if left else -distance
    chain = chain_of(entities, index)
    result, reversed_arc = [], []
    for number, backwards in chain:
        current = entities[number]
        if current[0] == SPLINE:
            # Jeden Punkt des Splines quer zur Kurve versetzen und einen neuen Spline hindurchlegen
            samples = spline_points(current)
            steps = (len(samples) - 1) // max(len(fit_points(current)) - 1, 1)
            side = -shift if backwards else shift
            moved = []
            # nur die Kurvenpunkte (Griffspitzen liegen nicht auf der Kurve)
            for point_number in range(len(fit_points(current))):
                at = min(point_number * steps, len(samples) - 1)
                before, after = samples[max(at - 1, 0)], samples[min(at + 1, len(samples) - 1)]
                length = math.dist(before, after) or 1.0
                moved.append((samples[at][0] - (after[1] - before[1]) / length * side,
                              samples[at][1] + (after[0] - before[0]) / length * side))
            result.append(spline(*(reversed(moved) if backwards else moved)))
            reversed_arc.append(False)
        elif current[0] == "Linie":
            start, end = (current[3:5], current[1:3]) if backwards else (current[1:3], current[3:5])
            length = math.dist(start, end)
            nx, ny = -(end[1] - start[1]) / length * shift, (end[0] - start[0]) / length * shift
            result.append(line(start[0] + nx, start[1] + ny, end[0] + nx, end[1] + ny))
            reversed_arc.append(False)      # Linien und Splines laufen im Ergebnis immer in Zugrichtung
        else:
            radius = current[3] + shift if backwards else current[3] - shift
            if radius <= 1e-9:
                return None
            result.append(arc(current[1], current[2], radius, current[4], current[5]))
            reversed_arc.append(backwards)  # ein Bogen bleibt gegen den Uhrzeigersinn gespeichert
    # Scharfe Ecken neu verschneiden: wo zwei Nachbarn nach dem Versatz nicht mehr zusammentreffen,
    # rücken ihre Enden auf den Schnittpunkt ihrer Trägerkurven
    first_ends, last_ends = _ends(entities[chain[0][0]]), _ends(entities[chain[-1][0]])
    closed = len(result) > 1 and math.dist(last_ends[0 if chain[-1][1] else 1], first_ends[1 if chain[0][1] else 0]) < 1e-6
    for position in range(len(result)):
        following = (position + 1) % len(result)
        if position == len(result) - 1 and not closed:
            continue
        first, second = result[position], result[following]
        if SPLINE in (first[0], second[0]):
            continue
        leaving = _ends(first)[0 if reversed_arc[position] else 1]
        arriving = _ends(second)[1 if reversed_arc[following] else 0]
        if math.dist(leaving, arriving) < 1e-9:
            continue
        hits = _carrier_hits(first, second)
        if not hits:
            continue
        middle = ((leaving[0] + arriving[0]) / 2, (leaving[1] + arriving[1]) / 2)
        corner = min(hits, key=lambda hit: math.dist(hit, middle))
        _set_end(first, not reversed_arc[position], corner)
        _set_end(second, reversed_arc[following], corner)
    return result


def spline_with_point(entity: list, x: float, y: float) -> list:
    """Der Spline mit einem zusätzlichen Punkt bei (x, y), eingefügt in den nächstgelegenen Abschnitt."""
    samples = spline_points(entity)
    points = fit_points(entity)
    count = len(points)
    steps = (len(samples) - 1) // max(count - 1, 1)
    nearest = min(range(len(samples)), key=lambda i: math.dist(samples[i], (x, y)))
    section = min(nearest // steps, count - 2) if nearest % steps or nearest == 0 else nearest // steps - 1
    if has_handles(entity):
        # der neue Punkt bekommt einen Griff längs der Kurve an dieser Stelle
        before, after = samples[max(nearest - 1, 0)], samples[min(nearest + 1, len(samples) - 1)]
        length = math.dist(before, after) or 1.0
        reach = math.dist(points[section], points[section + 1]) / 6
        tip = (x + (after[0] - before[0]) / length * reach, y + (after[1] - before[1]) / length * reach)
        cut = 1 + 4 * (section + 1)
        if has_uneven_handles(entity):
            # beim ungleichen Spline bekommt der neue Punkt einen gleich langen Gegengriff
            end = _handle_end(entity)
            back_cut = end + 2 * (section + 1)
            back = (2 * x - tip[0], 2 * y - tip[1])
            return [*entity[:cut], float(x), float(y), tip[0], tip[1], *entity[cut:back_cut], *back,
                    *entity[back_cut:]]
        return [*entity[:cut], float(x), float(y), tip[0], tip[1], *entity[cut:]]
    return spline(*points[:section + 1], (x, y), *points[section + 1:])


def spline_without_point(entity: list, number: int) -> list | None:
    """Der Spline ohne seinen Punkt `number` (None, wenn dann weniger als zwei Punkte blieben)."""
    points = fit_points(entity)
    if len(points) <= 2 or not 0 <= number < len(points):
        return None
    if has_uneven_handles(entity):
        end = _handle_end(entity)       # Punkt samt Griff und Gegengriff
        return [*entity[:1 + 4 * number], *entity[5 + 4 * number:end + 2 * number], *entity[end + 2 * number + 2:]]
    if has_handles(entity):
        return [*entity[:1 + 4 * number], *entity[5 + 4 * number:]]     # Punkt samt seinem Griff
    return spline(*points[:number], *points[number + 1:])


def trim_spline(entities: list, index: int, x: float, y: float) -> list[list]:
    """Was von einem Spline bleibt, wenn das Stück bei (x, y) bis zu den nächsten Schnittstellen entfernt wird.

    Die Reste laufen durch die bisherigen Punkte ihrer Seite und enden an der Schnittstelle;
    ihre Form weicht dadurch nahe dem Schnitt leicht von der alten Kurve ab.
    """
    entity = entities[index]
    if has_handles(entity):
        # Ein Griff-Spline wird zum Stutzen in einen gewöhnlichen Spline durch eng gesetzte Punkte seiner
        # Kurve umgewandelt: die Form bleibt, die Griffe entfallen an den Resten
        entity = spline(*spline_points(entity, 4))
    samples = spline_points(entity)
    count = (len(entity) - 1) // 2
    steps = (len(samples) - 1) // max(count - 1, 1)
    # Schnittstellen entlang des Linienzugs: (Lage als Stücknummer + Anteil, Punkt)
    cuts = []
    others = []
    for number, other in enumerate(entities):
        if number == index or other[0] in (POINT, *SPATIAL):
            continue
        # andere Splines und Ellipsen zählen als Zug kurzer Linien
        track = track_of(other)
        others += [line(*a, *b) for a, b in zip(track, track[1:])] if track else [other]
    for piece, (start, end) in enumerate(zip(samples, samples[1:])):
        part = line(*start, *end)
        if check_entity(part) is not None:
            continue
        for other in others:
            for hit in _carrier_hits(part, other):
                t = ((hit[0] - start[0]) * (end[0] - start[0]) + (hit[1] - start[1]) * (end[1] - start[1])) / (
                    math.dist(start, end) ** 2)
                # der Schnittpunkt muss auf dem Stück liegen (Anfang eingeschlossen) und auf dem anderen Element
                if not -1e-9 <= t < 1 - 1e-9:
                    continue
                if other[0] == "Linie":
                    reach = ((hit[0] - other[1]) * (other[3] - other[1]) + (hit[1] - other[2]) * (other[4] - other[2])) / (
                        math.dist(other[1:3], other[3:5]) ** 2)
                    if not -1e-9 <= reach <= 1 + 1e-9:
                        continue
                elif other[0] == "Bogen" and not (_on_entity(other, hit) or any(
                        math.dist(hit, end_point) < 1e-6 for end_point in _ends(other))):
                    continue
                if not any(abs(piece + t - known[0]) < 1e-6 for known in cuts):
                    cuts.append((piece + t, hit))
    clicked = min(range(len(samples)), key=lambda i: math.dist(samples[i], (x, y)))
    # Angeklickt wurde zwischen zwei Stützstellen: maßgeblich ist die Lage entlang des Zugs
    lower = max((cut for cut in cuts if cut[0] <= clicked - 1e-6), default=None, key=lambda cut: cut[0])
    upper = min((cut for cut in cuts if cut[0] >= clicked + 1e-6), default=None, key=lambda cut: cut[0])
    points = [(entity[i], entity[i + 1]) for i in range(1, len(entity), 2)]
    kept = []
    if lower is not None:
        before = [point for number, point in enumerate(points) if number * steps < lower[0] - 1e-6]
        if before and math.dist(before[-1], lower[1]) > 1e-6:
            kept.append(spline(*before, lower[1]))
    if upper is not None:
        after = [point for number, point in enumerate(points) if number * steps > upper[0] + 1e-6]
        if after and math.dist(after[0], upper[1]) > 1e-6:
            kept.append(spline(upper[1], *after))
    return kept


def round_cuts(entities: list, index: int) -> list[float]:
    """Winkel (Grad, 0 … 360), an denen andere Elemente den Kreis oder Bogen `index` schneiden."""
    mx, my, radius = entities[index][1:4]
    points = []
    for number, other in enumerate(entities):
        if number == index or other[0] in (POINT, *SPATIAL):
            continue
        if other[0] in ("Linie", SPLINE, ELLIPSE, CONIC, CP_SPLINE):
            track = track_of(other) or [other[1:3], other[3:5]]
            for (ax, ay), (bx, by) in zip(track, track[1:]):
                dx, dy = bx - ax, by - ay
                a, b = dx * dx + dy * dy, 2 * (dx * (ax - mx) + dy * (ay - my))
                root = b * b - 4 * a * ((ax - mx) ** 2 + (ay - my) ** 2 - radius * radius)
                if a < 1e-18 or root < 0:
                    continue
                for t in ((-b - math.sqrt(root)) / (2 * a), (-b + math.sqrt(root)) / (2 * a)):
                    if -1e-9 <= t <= 1 + 1e-9:
                        points.append((ax + t * dx, ay + t * dy))
            continue
        # Kreis gegen Kreis: die beiden Schnittpunkte liegen spiegelbildlich zur Verbindungslinie der Mitten
        ox, oy, other_radius = other[1:4]
        apart = math.dist((mx, my), (ox, oy))
        if apart < 1e-12 or apart > radius + other_radius or apart < abs(radius - other_radius):
            continue
        along = (radius * radius - other_radius * other_radius + apart * apart) / (2 * apart)
        across = math.sqrt(max(radius * radius - along * along, 0.0))
        ux, uy = (ox - mx) / apart, (oy - my) / apart
        for side in (1, -1):
            point = (mx + ux * along - uy * across * side, my + uy * along + ux * across * side)
            if other[0] == "Bogen":
                angle = math.degrees(math.atan2(point[1] - oy, point[0] - ox))
                if (angle - other[4]) % 360 > ((other[5] - other[4]) % 360 or 360) + 1e-6:
                    continue
            points.append(point)
    angles = sorted(math.degrees(math.atan2(y - my, x - mx)) % 360 for x, y in points)
    found = []
    for angle in angles:
        if entities[index][0] == "Bogen":
            # nur Stellen im Inneren des Bogens zählen, nicht seine Enden
            start, span = entities[index][4], (entities[index][5] - entities[index][4]) % 360 or 360
            if not 1e-6 < (angle - start) % 360 < span - 1e-6:
                continue
        if not found or angle - found[-1] > 1e-6:
            found.append(angle)
    return found


def trim_round(entity: list, cuts: list[float], angle: float) -> list[list]:
    """Was von einem Kreis oder Bogen bleibt, wenn das Stück beim Winkel `angle` entfernt wird.

    `cuts` sind die Schnittstellen (siehe `round_cuts`). Zurück kommen kein, ein oder zwei Bögen.
    """
    x, y, radius = entity[1:4]
    if entity[0] == "Kreis":
        if len(cuts) < 2:
            return []                   # nichts teilt den Kreis: er fällt ganz weg
        after = min(cuts, key=lambda cut: (cut - angle) % 360)      # nächste Schnittstelle gegen den Uhrzeigersinn
        before = min(cuts, key=lambda cut: (angle - cut) % 360)     # nächste im Uhrzeigersinn
        return [arc(x, y, radius, after, before)]
    start, span = entity[4], (entity[5] - entity[4]) % 360 or 360
    clicked = (angle - start) % 360
    places = [(cut - start) % 360 for cut in cuts]
    lower = max((place for place in places if place < clicked), default=None)
    upper = min((place for place in places if place > clicked), default=None)
    kept = []
    if lower is not None:
        kept.append(arc(x, y, radius, start, (start + lower) % 360))
    if upper is not None:
        kept.append(arc(x, y, radius, (start + upper) % 360, entity[5]))
    return kept


def element_values(entity: list) -> list[tuple[str, str, float, str]]:
    """Die Größen eines Elements, die sich nachträglich anpassen lassen: (Kennung, Beschriftung, Wert, Einheit)."""
    kind = entity[0]
    if kind == "Linie":
        return [("length", "Länge", math.dist(entity[1:3], entity[3:5]), " mm"),
                ("angle", "Winkel", math.degrees(math.atan2(entity[4] - entity[2], entity[3] - entity[1])) % 360, " °")]
    if kind == POINT:
        return [("x", "X", entity[1], " mm"), ("y", "Y", entity[2], " mm")]
    if kind == CONIC:
        return [("rho", "Rho (Form)", entity[7], "")]
    if kind == CP_SPLINE:
        return [("degree", "Grad (3 oder 5)", float(entity[-1]), "")]
    if kind == "Kreis":
        return [("diameter", "Durchmesser", 2 * entity[3], " mm")]
    if kind == "Bogen":
        return [("radius", "Radius", entity[3], " mm"), ("start", "Startwinkel", entity[4] % 360, " °"),
                ("end", "Endwinkel", entity[5] % 360, " °")]
    if kind == ELLIPSE:
        found = [("first", "Halbachse 1", entity[3], " mm"), ("second", "Halbachse 2", entity[4], " mm"),
                 ("rotation", "Drehung", entity[5] % 360, " °")]
        if is_ellipse_arc(entity):
            found += [("start", "Startwinkel", entity[6] % 360, " °"), ("end", "Endwinkel", entity[7] % 360, " °")]
        return found
    if kind == SPLINE and has_handles(entity):
        lengths = [math.dist(point, tip) for point, tip in zip(fit_points(entity), handle_tips(entity))]
        return [("strength", "Griffstärke (Bauchigkeit)", sum(lengths) / len(lengths), " mm")]
    return []


def with_value(entity: list, name: str, value: float) -> list:
    """Das Element mit einer geänderten Größe (siehe `element_values`); Anfangspunkt bzw. Mitte bleiben stehen."""
    result = list(entity)
    kind = entity[0]
    if kind == "Linie":
        length = math.dist(entity[1:3], entity[3:5])
        angle = math.atan2(entity[4] - entity[2], entity[3] - entity[1])
        length, angle = (value, angle) if name == "length" else (length, math.radians(value))
        result[3:5] = [entity[1] + length * math.cos(angle), entity[2] + length * math.sin(angle)]
    elif kind == POINT:
        result[1 if name == "x" else 2] = value
    elif kind == CONIC:
        result[7] = value
    elif kind == CP_SPLINE:
        result[-1] = float(min(CP_DEGREES, key=lambda d: abs(d - value)))     # nur Grad 3 oder 5
    elif kind == "Kreis":
        result[3] = value / 2
    elif kind == "Bogen":
        result[{"radius": 3, "start": 4, "end": 5}[name]] = value
    elif kind == ELLIPSE:
        result[{"first": 3, "second": 4, "rotation": 5, "start": 6, "end": 7}[name]] = value
    elif kind == SPLINE and has_handles(entity) and name == "strength":
        current = element_values(entity)[0][2]
        if current > 1e-9 and value > 0:
            result = scale_handles(entity, value / current)
    return result


def circle_through(first, second, third) -> tuple[float, float, float] | None:
    """Mittelpunkt und Radius des Kreises durch drei Punkte; None, wenn sie auf einer Geraden liegen."""
    (ax, ay), (bx, by), (cx, cy) = first, second, third
    determinant = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(determinant) < 1e-12:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    x = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / determinant
    y = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / determinant
    return x, y, math.dist((x, y), first)


def arc_through(start, through, end) -> list | None:
    """Bogen vom Start- zum Endpunkt, der durch den mittleren Punkt läuft (3-Punkt-Bogen)."""
    found = circle_through(start, through, end)
    if found is None:
        return None
    x, y, radius = found
    angle = lambda point: math.degrees(math.atan2(point[1] - y, point[0] - x)) % 360
    a, b, c = angle(start), angle(through), angle(end)
    # Bögen laufen gegen den Uhrzeigersinn: liegt der mittlere Punkt nicht dazwischen, andersherum zeichnen
    if (b - a) % 360 < (c - a) % 360:
        return arc(x, y, radius, a, c)
    return arc(x, y, radius, c, a)


def tangent_arc(start, direction, end) -> list | None:
    """Bogen, der im Startpunkt in Richtung `direction` (tangential) losläuft und im Endpunkt endet."""
    dx, dy = direction
    length = math.hypot(dx, dy)
    chord = (end[0] - start[0], end[1] - start[1])
    if length < 1e-12 or math.hypot(*chord) < 1e-12:
        return None
    dx, dy = dx / length, dy / length
    normal = (-dy, dx)                         # links der Laufrichtung
    # Mittelpunkt auf der Senkrechten im Startpunkt, gleich weit von Start und Ende
    side = chord[0] * normal[0] + chord[1] * normal[1]
    if abs(side) < 1e-12:
        return None                             # das Ende liegt genau in Laufrichtung: kein Bogen, eine Gerade
    radius = (chord[0] ** 2 + chord[1] ** 2) / (2 * side)
    x, y = start[0] + normal[0] * radius, start[1] + normal[1] * radius
    a = math.degrees(math.atan2(start[1] - y, start[0] - x)) % 360
    b = math.degrees(math.atan2(end[1] - y, end[0] - x)) % 360
    # linksherum (Mitte links) läuft der Bogen gegen den Uhrzeigersinn vom Start zum Ende, sonst umgekehrt
    return arc(x, y, abs(radius), a, b) if radius > 0 else arc(x, y, abs(radius), b, a)


def rectangle_points(corners: list) -> list[list]:
    """Vier Linien durch vier Ecken in Reihenfolge (auch ein gedrehtes Rechteck)."""
    return [line(*corners[i], *corners[(i + 1) % 4]) for i in range(4)]


def rectangle(x1: float, y1: float, x2: float, y2: float) -> list[list]:
    """Vier Linien eines achsparallelen Rechtecks zwischen zwei gegenüberliegenden Ecken."""
    return [line(x1, y1, x2, y1), line(x2, y1, x2, y2), line(x2, y2, x1, y2), line(x1, y2, x1, y1)]


def check_entity(entity: list) -> str | None:
    """Gibt eine Fehlermeldung zurück, wenn das Element unbrauchbar ist, sonst None."""
    kind = entity[0] if entity else None
    if kind == CP_SPLINE:
        if len(entity) < 6 or len(entity) % 2:
            return "Steuerpunkt-Spline: es braucht mindestens zwei Steuerpunkte"
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in entity[1:]):
            return "Steuerpunkt-Spline: ungültiger Zahlenwert"
        if int(round(entity[-1])) not in CP_DEGREES:
            return "Steuerpunkt-Spline: der Grad muss 3 oder 5 sein"
        points = control_points(entity)
        if any(math.dist(a, b) < 1e-9 for a, b in zip(points, points[1:])):
            return "Steuerpunkt-Spline: zwei aufeinanderfolgende Punkte liegen an derselben Stelle"
        return None
    if kind == SPLINE:
        if len(entity) < 5 or len(entity) % 2 == 0:
            return "Spline: es braucht mindestens zwei Punkte"
        if has_uneven_handles(entity) and ((len(entity) - 1) % 6 or len(entity) < 13):
            return "Griff-Spline: zu jedem Punkt gehören Griff und Gegengriff, und es braucht mindestens zwei Punkte"
        if has_handles(entity) and not has_uneven_handles(entity) and ((len(entity) - 1) % 4 or len(entity) < 9):
            return "Griff-Spline: zu jedem Punkt gehört ein Griff, und es braucht mindestens zwei Punkte"
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in entity[1:]):
            return "Spline: ungültiger Zahlenwert"
        points = fit_points(entity)
        if any(math.dist(a, b) < 1e-9 for a, b in zip(points, points[1:])):
            return "Spline: zwei aufeinanderfolgende Punkte liegen an derselben Stelle"
        return None
    if kind == SPLINE3D:
        if len(entity) < 7 or (len(entity) - 1) % 3:
            return "3D-Spline: es braucht mindestens zwei Raumpunkte (je x, y, z)"
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in entity[1:]):
            return "3D-Spline: ungültiger Zahlenwert"
        points = points3d(entity)
        if any(math.dist(a, b) < 1e-9 for a, b in zip(points, points[1:])):
            return "3D-Spline: zwei aufeinanderfolgende Punkte liegen an derselben Stelle"
        return None
    arc_of_ellipse = kind == ELLIPSE and len(entity) == 8     # Ellipsenbogen: zusätzlich Start- und Endwinkel
    if kind not in ENTITY_FIELDS or (len(entity) != len(ENTITY_FIELDS[kind]) + 1 and not arc_of_ellipse):
        return f"Unbekanntes Skizzenelement {entity!r}"
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in entity[1:]):
        return f"{kind}: ungültiger Zahlenwert"
    if kind == "Linie" and math.dist(entity[1:3], entity[3:5]) < 1e-9:
        return "Linie ohne Länge"
    if kind == LINE3D and math.dist(entity[1:4], entity[4:7]) < 1e-9:
        return "3D-Linie ohne Länge"
    if kind == CONIC:
        if not 0.0 < entity[7] < 1.0:
            return "Kegelschnitt: Rho muss zwischen 0 und 1 liegen"
        (x1, y1, x2, y2, sx, sy) = entity[1:7]
        if abs((x2 - x1) * (sy - y1) - (y2 - y1) * (sx - x1)) < 1e-9:
            return "Kegelschnitt: Anfang, Ende und Scheitel dürfen nicht auf einer Geraden liegen"
    if kind == ELLIPSE:
        if arc_of_ellipse and abs(entity[7] - entity[6]) < 1e-9:
            return "Ellipsenbogen ohne Länge"
        return None if entity[3] > 0 and entity[4] > 0 else "Ellipse: beide Halbachsen müssen größer als 0 sein"
    if kind in ("Kreis", "Bogen") and entity[3] <= 0:
        return f"{kind}: der Radius muss größer als 0 sein"
    if kind == "Bogen" and abs(entity[5] - entity[4]) < 1e-9:
        return "Bogen ohne Öffnungswinkel"
    return None


@dataclass
class Sketch(Feature):
    """Skizze aus frei gezeichneten Linien, Kreisen und Bögen."""

    plane: str = "XY"
    offset: float = 0.0
    entities: list = field(default_factory=list)
    # Liegt die Skizze auf einer Körperfläche: Ursprung, Normale und x-Richtung (9 Zahlen)
    frame: list = field(default_factory=list)
    # Maße und Abhängigkeiten (siehe constraints.py); die Elemente sind bereits passend gelöst
    constraints: list = field(default_factory=list)
    # Name einer Konstruktionsebene, auf der die Skizze liegt (leer: Hauptebene oder Körperfläche).
    # Die Skizze wandert mit, wenn die Ebene verschoben wird.
    base: str = ""
    # Nummern der Konstruktionselemente: Hilfslinien zum Ausrichten und Bemaßen, die kein
    # Profil bilden und in keinen Körper eingehen
    construction: list = field(default_factory=list)
    # Mittellinien wie in Fusion: Nummern der Elemente, die strichpunktiert gezeigt werden, aber (anders als
    # Konstruktionslinien) zum Profil zählen
    centerlines: list = field(default_factory=list)
    # Texte: je Eintrag [Text, x, y, Schrifthöhe in mm, Drehung in Grad]; ihre Buchstaben werden Profilflächen
    texts: list = field(default_factory=list)
    # Projizierte Geometrie wie in Fusion (lila): Nummern der Elemente, die aus Körperkanten projiziert wurden – z. B.
    # der Umriss der Fläche, auf der die Skizze liegt. Sie sind fest verankert und zählen zum Profil
    projected: list = field(default_factory=list)
    # Merkpunkt der Körperfläche, auf der die Skizze liegt (leer: Hauptebene/Konstruktionsebene). Wie in Fusion
    # wandert die Skizze mit der Fläche, und ihr projizierter Umriss wird neu projiziert
    face_ref: list = field(default_factory=list)

    KIND = "Skizze"

    def axes(self, state: BuildState) -> gp_Ax2:
        """Lage der Skizzenebene im aktuellen Modellstand."""
        self._follow_face(state)
        return placed_axes(state, self.plane, self.offset, self.frame, self.base)

    def _face(self, state: BuildState):
        """Die Körperfläche, auf der die Skizze liegt (nachgeführt, siehe features._follow); None, wenn es keine
        gibt oder sie nicht mehr zu finden ist – dann bleibt die Skizze, wo sie war."""
        from .features import faces_of, find_by_points, face_point
        if len(self.frame) != 9:
            return None
        every = [face for body in state.bodies.values() for face in faces_of(body)]
        if len(self.face_ref) < 3:
            # ältere Skizze (vor Runde 63) mit projiziertem Umriss: die Fläche ist die, in deren Ebene der
            # Skizzenursprung liegt – einmal gefunden, wird sie gemerkt
            if not self.projected:
                return None
            found = _face_under_frame(every, self.frame)
            if found is None:
                return None
            self.face_ref = list(face_point(found))
            return found
        try:
            return find_by_points(every, face_point, [self.face_ref], "Fläche")[0]
        except FeatureError:
            return None

    def _follow_face(self, state: BuildState) -> None:
        """Liegt die Skizze auf einer Fläche, die ein früherer Schritt verschoben hat, rückt sie mit (längs ihrer
        Normale, Lage in der Ebene bleibt)."""
        face = self._face(state)
        if face is None:
            return
        frame = face_frame(face)
        if frame is None:
            return
        origin, normal = self.frame[:3], self.frame[3:6]
        if abs(sum(a * b for a, b in zip(normal, frame[3:6]))) < 0.9999:
            return                              # die Fläche hat sich gedreht: nicht still umlegen
        distance = sum((b - a) * n for a, b, n in zip(origin, frame[:3], normal))
        if abs(distance) > 1e-9:
            self.frame[:3] = [a + n * distance for a, n in zip(origin, normal)]

    def _replace_projection(self, fresh: list) -> None:
        """Projizierten Umriss ersetzen, wenn die Fläche eine andere Gestalt bekommen hat (z. B. mehr Kanten durch
        eine Rundung). Wie in Fusion bleiben Elemente erhalten, die es weiterhin gibt: eine alte projizierte Linie
        bzw. ein Bogen, zu dem eine neue Kante passt, behält ihren Platz in der Liste – und damit alle Maße und
        Abhängigkeiten, die gezeichnete Elemente daran haben; sie bekommt nur die neue Lage. Was es nicht mehr gibt,
        entfällt samt seinen Abhängigkeiten; neue Kanten kommen fest verankert dazu."""
        from . import constraints as cs
        if not fresh:
            return
        old = [index for index in self.projected if index < len(self.entities)]
        pairs = _match_outline([self.entities[index] for index in old], fresh)   # Stelle in old → Stelle in fresh
        entities = [list(entity) for entity in self.entities]
        for position, number in pairs.items():
            entities[old[position]] = _oriented_like(entities[old[position]], fresh[number])
        kept = [old[position] for position in sorted(pairs)]
        constraints = [list(rule) for rule in self.constraints]
        construction, centerlines = list(self.construction), list(self.centerlines)
        for index in sorted((old[position] for position in range(len(old)) if position not in pairs), reverse=True):
            del entities[index]
            constraints = cs.without_entity(constraints, index)
            construction = [i - (i > index) for i in construction if i != index]
            centerlines = [i - (i > index) for i in centerlines if i != index]
            kept = [i - (i > index) for i in kept]
        start = len(entities)
        entities += [list(fresh[number]) for number in range(len(fresh)) if number not in pairs.values()]
        self.projected = kept + list(range(start, len(entities)))
        constraints += [[cs.FIX, index, -1, 0.0] for index in range(start, len(entities))]
        self.construction, self.centerlines = construction, centerlines
        # gezeichnete Elemente rücken über ihre Abhängigkeiten an den neuen Umriss nach
        solved = cs.solve(entities, constraints) if constraints else entities
        self.entities = solved if solved is not None else entities
        self.constraints = constraints

    def _update_projection(self, state: BuildState, axes) -> None:
        """Assoziative Projektion wie in Fusion: der projizierte Umriss der Fläche folgt ihrer heutigen Form;
        gezeichnete Elemente, die daran hängen, rücken über die Abhängigkeiten nach."""
        from . import constraints as cs
        from .features import unique_edges
        face = self._face(state)
        if face is None or not self.projected:
            return
        fresh = project_edges(unique_edges(face), axes)
        old = [self.entities[index] for index in self.projected if index < len(self.entities)]
        if len(fresh) != len(old) or any(a[0] != b[0] for a, b in zip(sorted(old), sorted(fresh))):
            self._replace_projection(fresh)     # andere Gestalt (z. B. mehr Kanten): ganz neu projizieren
            return
        changed, used = False, set()
        for index in self.projected:
            entity = self.entities[index]
            candidates = [(sum((x - y) ** 2 for x, y in zip(entity[1:], other[1:])), number)
                          for number, other in enumerate(fresh) if number not in used and other[0] == entity[0]]
            if not candidates:
                return
            _distance, number = min(candidates)
            used.add(number)
            if any(abs(x - y) > 1e-9 for x, y in zip(entity[1:], fresh[number][1:])):
                changed = True
        if not changed:
            return
        updated = [list(entity) for entity in self.entities]
        used = set()
        for index in self.projected:
            entity = updated[index]
            _distance, number = min((sum((x - y) ** 2 for x, y in zip(entity[1:], other[1:])), number)
                                    for number, other in enumerate(fresh) if number not in used and other[0] == entity[0])
            used.add(number)
            updated[index] = list(fresh[number])
        solved = cs.solve(updated, self.constraints) if self.constraints else updated
        self.entities = solved if solved is not None else updated

    def resolve_parameters(self, evaluate) -> None:
        """Maße mit Ausdruck folgen den Benutzerparametern; die Elemente werden nachgezogen."""
        from . import constraints as cs     # hier eingebunden, weil constraints dieses Modul benutzt
        solved = cs.apply_expressions(self.entities, self.constraints, evaluate)
        if solved is not None:
            self.entities = solved

    def apply(self, state: BuildState) -> None:
        axes = self.axes(state)
        self._update_projection(state, axes)
        self._apply_curves(state, axes)
        if not self.texts:
            return
        # Texte: ihre Buchstaben sind fertige Flächen und kommen zum Profil der Skizze dazu
        builder, faces = BRep_Builder(), TopoDS_Compound()
        builder.MakeCompound(faces)
        for name in (self.name,):
            if name in state.profiles:
                for face in ft.faces_of(state.profiles[name]):
                    builder.Add(faces, face)
        for entry in self.texts:
            if len(entry) == 7 and entry[6] >= 0:
                # Text am Pfad: Buchstabe für Buchstabe entlang eines Elements der Skizze
                if entry[6] >= len(self.entities):
                    raise FeatureError("Der Pfad des Textes ist nicht mehr in der Skizze")
                letters = text_on_path(axes, entry, _edge(axes, self.entities[entry[6]]))
            else:
                letters = text_shape(axes, *entry[:6])  # Text, Lage, Höhe, Drehung und ggf. die Schrift
            state.sketch_curves.append(letters)
            state.sketch_parts.setdefault(self.name, []).append(letters)
            for face in ft.faces_of(letters):
                builder.Add(faces, face)
        state.profiles[self.name] = faces
        state.solid_profiles[self.name] = faces

    def _apply_curves(self, state: BuildState, axes: gp_Ax2) -> None:
        """Legt die Ebene fest und bildet aus den gezeichneten Elementen Kurven und Profil."""
        normal = axes.Direction()
        # Auch eine noch leere Skizze legt ihre Ebene fest (sie wird gerade erst gezeichnet)
        state.normals[self.name] = (normal.X(), normal.Y(), normal.Z())
        state.frames[self.name] = axes
        if not self.entities:
            return
        builder, curves = BRep_Builder(), TopoDS_Compound()
        builder.MakeCompound(curves)
        flat = TopoDS_Compound()                    # nur die Kurven in der Ebene: daraus entsteht das Profil
        builder.MakeCompound(flat)
        solid_lines = 0
        helpers = TopoDS_Compound()
        builder.MakeCompound(helpers)
        # Fast deckungsgleiche Kurven zusammenlegen (siehe snapped_entities). Ab Geometriestufe 3 immer; in älteren
        # Entwürfen nur, wenn es wirklich einen Beinahe-Treffer gibt – der ergab bisher ungültige Körper, alle
        # anderen Skizzen rechnen dort unverändert
        snapped, moved = snapped_entities(self.entities)
        if ft.geometry_level() < 3 and not moved:
            snapped = list(self.entities)
        for index, entity in enumerate(self.entities):
            problem = check_entity(entity)
            if problem:
                raise FeatureError(problem)
            if snapped[index] is None:
                solid_lines += 1                    # doppelt: die gleiche Kurve ist schon da
                continue
            entity = snapped[index]
            if entity[0] == POINT:
                # Skizzenpunkte bilden keine Kurve: sie werden als Stellen gemerkt (z. B. für Bohrungen)
                where = _point(axes, entity[1], entity[2])
                state.sketch_points.setdefault(self.name, []).append((where.X(), where.Y(), where.Z()))
                # im Modell als kleines Kreuz sichtbar (wie die Konstruktionslinien, ohne Profil)
                for dx, dy in ((0.8, 0.0), (0.0, 0.8)):
                    builder.Add(helpers, BRepBuilderAPI_MakeEdge(
                        _point(axes, entity[1] - dx, entity[2] - dy), _point(axes, entity[1] + dx, entity[2] + dy)).Edge())
                continue
            if entity[0] in SPATIAL and index not in self.construction:
                # 3D-Linien und 3D-Splines zählen zu den Kurven (Pfad), aber nicht zum flachen Profil
                builder.Add(curves, _edge(axes, entity))
                solid_lines += 1
                continue
            if index not in self.construction:      # Konstruktionselemente bleiben außen vor
                builder.Add(curves, _edge(axes, entity))
                builder.Add(flat, _edge(axes, entity))
                solid_lines += 1
            else:
                builder.Add(helpers, _edge(axes, entity))
        if solid_lines < len(self.entities):
            state.construction_curves.append(helpers)      # nur zur Anzeige im Modell (auch Skizzenpunkte)
            state.sketch_parts.setdefault(self.name, []).append(helpers)
        if not any(entity[0] != POINT and index not in self.construction for index, entity in enumerate(self.entities)):
            return                              # nur Punkte (oder nur Konstruktion): keine Kurven, kein Profil

        state.sketch_curves.append(curves)
        state.sketch_parts.setdefault(self.name, []).append(curves)
        state.curves[self.name] = curves
        normal = axes.Direction()
        state.normals[self.name] = (normal.X(), normal.Y(), normal.Z())
        state.frames[self.name] = axes
        profile = _regions(flat) if any(entity[0] not in SPATIAL for entity in self.entities) else None
        if profile is not None:
            state.profiles[self.name] = profile
            state.solid_profiles[self.name] = _without_inner_regions(profile)


# Schrift für Skizzentexte und Schriften zur Auswahl (sechster Eintrag eines Textes); fehlt eine, ersetzt der Kern
# sie durch eine vorhandene. Unter Windows gibt es die Linux-Schriften meist nicht – dort die mitgelieferten.
if os.name == "nt":
    TEXT_FONT = "Arial"
    TEXT_FONTS = ("Arial", "Times New Roman", "Consolas", "Segoe UI", "Georgia", "Verdana", "Calibri")
else:
    TEXT_FONT = "DejaVu Sans"
    TEXT_FONTS = ("DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono", "Liberation Sans", "Liberation Serif",
                  "Noto Sans", "Noto Serif")


def check_text(entry: list) -> str | None:
    """Fehlermeldung, wenn der Texteintrag unbrauchbar ist, sonst None."""
    # 5 Werte (Text, x, y, Höhe, Drehung), dazu wahlweise die Schrift und (Runde 96) die Nummer des Elements, an dem
    # der Text entlangläuft (Text am Pfad; x ist dann der Abstand vom Anfang des Elements, y der Abstand zum Pfad)
    if (len(entry) not in (5, 6, 7) or not isinstance(entry[0], str) or not entry[0].strip()
            or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in entry[1:5])
            or (len(entry) >= 6 and not isinstance(entry[5], str))
            or (len(entry) == 7 and not isinstance(entry[6], int))):
        return f"Unbrauchbarer Texteintrag {entry!r}"
    return None if entry[3] > 0 else "Text: die Schrifthöhe muss größer als 0 sein"


class _quiet_stderr:
    """Hält die Fehlerausgabe kurz an: die Schriftverwaltung des Systems schreibt beim ersten
    Zugriff sonst Warnungen über ihre eigenen Einstellungsdateien auf die Konsole."""

    def __enter__(self):
        try:
            self._saved = os.dup(2)
            self._null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(self._null, 2)
        except OSError:
            self._saved = None

    def __exit__(self, *_error):
        if self._saved is not None:
            os.dup2(self._saved, 2)
            os.close(self._saved)
            os.close(self._null)


def text_shape(axes: gp_Ax2, text: str, x: float, y: float, height: float, rotation: float = 0.0,
               font_name: str = ""):
    """Die Buchstaben eines Textes als Flächen in der Skizzenebene.

    (x, y) ist die linke untere Ecke der Grundlinie, `height` die Schriftgröße in mm;
    ohne `font_name` gilt die Standardschrift.
    """
    problem = check_text([text, x, y, height, rotation])
    if problem:
        raise FeatureError(problem)
    along = gp_Vec(axes.XDirection().XYZ()) * math.cos(math.radians(rotation)) + gp_Vec(
        axes.YDirection().XYZ()) * math.sin(math.radians(rotation))
    place = gp_Ax3(_point(axes, x, y), axes.Direction(), gp_Dir(along))
    try:
        with _quiet_stderr():
            font = StdPrs_BRepFont.FindAndCreate_s(TCollection_AsciiString(font_name or TEXT_FONT),
                                                   Font_FontAspect.Font_FontAspect_Regular, float(height))
        shape = StdPrs_BRepTextBuilder().Perform(font, NCollection_String(text), place)
    except Exception as error:
        raise FeatureError(f"Der Text '{text}' ließ sich nicht in Flächen umwandeln") from error
    if shape is None or shape.IsNull() or not TopExp_Explorer(shape, TopAbs_FACE).More():
        raise FeatureError(f"Der Text '{text}' ergibt keine Flächen")
    return shape


def text_on_path(axes: gp_Ax2, entry: list, edge):
    """Text am Pfad (Fusion: Text an Pfad, Runde 96): jeder Buchstabe sitzt auf der Kurve, an der Stelle seines
    Abstands vom Anfang (entry[1] + bisherige Breite) und um ihre Richtung dort gedreht; entry[2] hebt die Grundlinie
    quer zur Kurve an."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint
    text, offset, lift, height = entry[0], float(entry[1]), float(entry[2]), float(entry[3])
    font_name = entry[5] if len(entry) > 5 else ""
    curve = BRepAdaptor_Curve(edge)
    length = GCPnts_AbscissaPoint.Length_s(curve)
    normal = gp_Vec(axes.Direction().XYZ())
    # lesbar wie in Fusion: die Oberkante der Buchstaben zeigt von der Krümmungsmitte weg (Text außen auf einem
    # Bogen, von links nach rechts) – sonst läuft der Text andersherum über die Kurve
    point, first_d, second_d = gp_Pnt(), gp_Vec(), gp_Vec()
    curve.D2((curve.FirstParameter() + curve.LastParameter()) / 2, point, first_d, second_d)
    up_middle = normal.Crossed(first_d)
    backwards = second_d.Magnitude() > 1e-12 and up_middle.Dot(second_d) > 0
    if not backwards and second_d.Magnitude() <= 1e-12:
        # gerade: von links nach rechts in der Skizze (x-Richtung der Ebene), bei senkrechter Linie von unten
        along = first_d.Dot(gp_Vec(axes.XDirection().XYZ()))
        backwards = along < -1e-9 or (abs(along) <= 1e-9 and first_d.Dot(gp_Vec(axes.YDirection().XYZ())) < 0)
    builder, letters = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(letters)
    position = offset
    with _quiet_stderr():
        font = StdPrs_BRepFont.FindAndCreate_s(TCollection_AsciiString(font_name or TEXT_FONT),
                                               Font_FontAspect.Font_FontAspect_Regular, height)
    for character in text:
        width = _letter_width(character, height, font_name)
        if character.strip() and 0.0 <= position + width / 2 <= length:
            distance = position + width / 2
            parameter = GCPnts_AbscissaPoint(curve, length - distance if backwards else distance,
                                             curve.FirstParameter()).Parameter()
            middle, tangent = gp_Pnt(), gp_Vec()
            curve.D1(parameter, middle, tangent)
            tangent.Normalize()
            if backwards:
                tangent.Reverse()
            up = normal.Crossed(tangent)            # quer zur Kurve in der Skizzenebene
            start = middle.Translated(tangent * (-width / 2) + up * lift)
            place = gp_Ax3(start, axes.Direction(), gp_Dir(tangent.XYZ()))
            shape = StdPrs_BRepTextBuilder().Perform(font, NCollection_String(character), place)
            if shape is not None and not shape.IsNull():
                builder.Add(letters, shape)
        position += width + height * 0.08
    if not TopExp_Explorer(letters, TopAbs_FACE).More():
        raise FeatureError(f"Der Text '{text}' passt nicht auf den Pfad (zu lang oder Abstand zu groß)")
    return letters


def _letter_width(character: str, height: float, font_name: str) -> float:
    """Breite eines Buchstabens (aus seinem Umriss; Leerzeichen ein Drittel der Schrifthöhe)."""
    if not character.strip():
        return height * 0.33
    lines = text_outline(character, height, font_name)
    xs = [x for line in lines for x, _y in line]
    return (max(xs) - min(xs)) if xs else height * 0.5


_text_outlines: dict[tuple, list] = {}


def text_outline(text: str, height: float, font_name: str = "") -> list[list[tuple[float, float]]]:
    """Umrisse eines Textes als Linienzüge, bezogen auf seinen Ankerpunkt (für die Anzeige beim Zeichnen)."""
    key = (text, round(float(height), 6), font_name)
    if key not in _text_outlines:
        flat = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0))
        lines = []
        explorer = TopExp_Explorer(text_shape(flat, text, 0.0, 0.0, height, 0.0, font_name), TopAbs_EDGE)
        while explorer.More():
            curve = BRepAdaptor_Curve(TopoDS.Edge(explorer.Current()))
            explorer.Next()
            first, last = curve.FirstParameter(), curve.LastParameter()
            steps = 1 if curve.GetType() == GeomAbs_Line else 8
            lines.append([(curve.Value(first + (last - first) * k / steps).X(),
                           curve.Value(first + (last - first) * k / steps).Y()) for k in range(steps + 1)])
        if len(_text_outlines) > 200:
            _text_outlines.clear()
        _text_outlines[key] = lines
    return _text_outlines[key]


def points_frame(first: tuple, second: tuple, third: tuple) -> list | None:
    """Lage der Ebene durch drei Punkte als 9 Zahlen (Ursprung, Normale, x-Richtung); None, wenn sie
    auf einer Geraden liegen."""
    along = gp_Vec(gp_Pnt(*first), gp_Pnt(*second))
    normal = along.Crossed(gp_Vec(gp_Pnt(*first), gp_Pnt(*third))) if along.Magnitude() > 1e-9 else gp_Vec(0, 0, 0)
    if normal.Magnitude() < 1e-9:
        return None
    normal.Normalize()
    along.Normalize()
    return [*first, normal.X(), normal.Y(), normal.Z(), along.X(), along.Y(), along.Z()]


TILT_AXES = ("x-Achse der Ebene", "y-Achse der Ebene")


def midplane_frame(first: list, second: list) -> list:
    """Mittelebene zwischen zwei ebenen Flächen (je 9 Zahlen wie `face_frame`).

    Parallele Flächen: mittig dazwischen. Schräg zueinander: die winkelhalbierende Ebene durch ihre Schnittgerade.
    """
    a_point, a_normal, a_x = (gp_Vec(*first[i:i + 3]) for i in (0, 3, 6))
    b_point, b_normal = gp_Vec(*second[0:3]), gp_Vec(*second[3:6])
    if b_normal.Dot(a_normal) < 0:
        b_normal = b_normal.Reversed()          # beide Normalen in dieselbe Richtung drehen
    normal = a_normal + b_normal
    if normal.Magnitude() < 1e-9:
        raise FeatureError("Die beiden Flächen stehen genau gegeneinander – keine Mittelebene bestimmbar")
    normal.Normalize()
    middle = (a_point + b_point) * 0.5
    if a_normal.Crossed(b_normal).Magnitude() > 1e-9:
        # schräg: ein Punkt, der von beiden Ebenen gleich weit entfernt ist (auf der Winkelhalbierenden)
        distance_a = (middle - a_point).Dot(a_normal)
        distance_b = (middle - b_point).Dot(b_normal)
        middle = middle - normal * ((distance_a + distance_b) / 2 / max(normal.Dot(a_normal), 1e-9))
    x_dir = a_x - normal * a_x.Dot(normal)
    if x_dir.Magnitude() < 1e-9:
        x_dir = normal.Crossed(gp_Vec(0, 0, 1)) if abs(normal.Z()) < 0.9 else normal.Crossed(gp_Vec(1, 0, 0))
    x_dir.Normalize()
    return [middle.X(), middle.Y(), middle.Z(), normal.X(), normal.Y(), normal.Z(), x_dir.X(), x_dir.Y(), x_dir.Z()]


def tangent_frame(face) -> list | None:
    """Ebene, die eine gekrümmte Fläche in ihrer Mitte berührt (9 Zahlen); None bei ebenen Flächen."""
    from .solids import face_middle
    if face_frame(face) is not None:
        return None
    point, normal = face_middle(face)
    normal_vec = gp_Vec(*normal)
    x_dir = normal_vec.Crossed(gp_Vec(0, 0, 1)) if abs(normal[2]) < 0.9 else normal_vec.Crossed(gp_Vec(1, 0, 0))
    x_dir.Normalize()
    return [*point, *normal, x_dir.X(), x_dir.Y(), x_dir.Z()]


def edges_frame(first, second) -> list | None:
    """Lage der Ebene durch zwei gerade Körperkanten als 9 Zahlen (Ursprung, Normale, x-Richtung).

    None, wenn eine Kante nicht gerade ist oder beide auf einer Geraden liegen.
    """
    curves = [BRepAdaptor_Curve(edge) for edge in (first, second)]
    if any(curve.GetType() != GeomAbs_Line for curve in curves):
        return None
    middle = [curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2) for curve in curves]
    along = gp_Vec(curves[0].Line().Direction().XYZ())
    across = gp_Vec(middle[0], middle[1])
    if across.Magnitude() < 1e-9:
        across = gp_Vec(curves[1].Line().Direction().XYZ())
    normal = along.Crossed(across)
    if normal.Magnitude() < 1e-9:
        return None
    normal.Normalize()
    return [middle[0].X(), middle[0].Y(), middle[0].Z(), normal.X(), normal.Y(), normal.Z(),
            along.X(), along.Y(), along.Z()]


@dataclass
class ConstructionPlane(Feature):
    """Konstruktionsebene: eine Hauptebene oder Körperfläche, um einen Abstand versetzt.

    Auf ihr lassen sich Skizzen anlegen; sie selbst erzeugt keine Geometrie.
    """

    plane: str = "XY"
    offset: float = 10.0
    frame: list = field(default_factory=list)   # Lage der beim Anlegen gewählten Körperfläche
    # Ebene im Winkel: um die eigene x- oder y-Achse der Ausgangsebene gekippt (vor dem Versatz)
    tilt: float = 0.0
    tilt_axis: str = TILT_AXES[0]

    KIND = "Ebene"

    def axes(self) -> gp_Ax2:
        """Lage der Ebene: Ausgangsebene, gekippt, dann entlang der neuen Normale versetzt."""
        if self.tilt_axis not in TILT_AXES:
            raise FeatureError(f"Unbekannte Kippachse '{self.tilt_axis}'")
        start = frame_axes(self.plane, 0.0, self.frame)
        if self.tilt:
            around = start.XDirection() if self.tilt_axis == TILT_AXES[0] else start.YDirection()
            turn = gp_Trsf()
            turn.SetRotation(gp_Ax1(start.Location(), around), math.radians(self.tilt))
            start = start.Transformed(turn)
        normal = start.Direction()
        return gp_Ax2(start.Location().Translated(gp_Vec(normal.XYZ()) * self.offset), normal, start.XDirection())

    def apply(self, state: BuildState) -> None:
        state.planes[self.name] = self.axes()


def placed_axes(state: BuildState, plane: str, offset: float, frame: list, base: str = "") -> gp_Ax2:
    """Lage einer Ebene im Modell: auf einer Konstruktionsebene (`base`), sonst wie `frame_axes`.

    `offset` verschiebt in beiden Fällen entlang der Normale.
    """
    if base in ("", ft.NO_BASE):
        return frame_axes(plane, offset, frame)
    if base not in state.planes:
        raise FeatureError(f"Konstruktionsebene '{base}' ist nicht (mehr) vorhanden")
    found = state.planes[base]
    normal = found.Direction()
    return gp_Ax2(found.Location().Translated(gp_Vec(normal.XYZ()) * offset), normal, found.XDirection())


def frame_axes(plane: str, offset: float, frame: list) -> gp_Ax2:
    """Lage einer Ebene: eine Hauptebene oder, wenn `frame` gesetzt ist, die gemerkte Fläche.

    `frame` enthält Ursprung, Normale und x-Richtung; `offset` verschiebt entlang der Normale.
    """
    if not frame:
        return ft._plane_axes(plane, offset)
    if len(frame) != 9:
        raise FeatureError("Die gemerkte Lage der Fläche ist unvollständig")
    normal = gp_Dir(*frame[3:6])
    origin = gp_Pnt(*frame[0:3]).Translated(gp_Vec(normal.XYZ()) * offset)
    return gp_Ax2(origin, normal, gp_Dir(*frame[6:9]))


def _point3(axes: gp_Ax2, x: float, y: float, z: float) -> gp_Pnt:
    """Punkt mit den Skizzenkoordinaten (x, y) und dem Abstand z längs der Skizzennormale."""
    return _point(axes, x, y).Translated(gp_Vec(axes.Direction().XYZ()) * z)


def _point(axes: gp_Ax2, x: float, y: float) -> gp_Pnt:
    """Punkt mit den Skizzenkoordinaten (x, y) im Raum."""
    vector = (gp_Vec(axes.Location().XYZ()) + gp_Vec(axes.XDirection().XYZ()) * x
              + gp_Vec(axes.YDirection().XYZ()) * y)
    return gp_Pnt(vector.XYZ())


# Unterhalb dieses Abstands (mm) gelten Skizzenpunkte und Kreisträger als gleich (Geometriestufe 3). Projizierte
# Körperkanten weichen oft um einige Tausendstel Mikrometer von dem ab, was darauf gezeichnet wird; ohne Fangen
# entstehen zwischen fast gleichen Kreisen haarfeine Bereiche und daraus ungültige Körper (Runde 78, Nutzerentwurf).
SNAP_TOLERANCE = 1e-3


def snapped_entities(entities: list) -> tuple[list, bool]:
    """Die Elemente mit gefangenen Lagen: Kreise und Bögen auf (fast) gleichem Träger bekommen genau dessen
    Mittelpunkt und Radius, Linienenden springen auf nahe Punkte früherer Elemente; ein Kreis bzw. eine Linie, die
    danach genau einem früheren Element gleicht, wird None (entfällt). Die Liste behält Länge und Reihenfolge, damit
    Nummern (Konstruktion, Abhängigkeiten) gültig bleiben.

    Zweiter Wert: ob etwas wirklich verschoben wurde (ein Beinahe-Treffer, nicht nur eine exakte Doppelung)."""
    rounds: list[tuple[float, float, float]] = []      # bekannte Kreisträger (x, y, r)
    points: list[tuple[float, float]] = []             # bekannte Punkte (Linienenden, Bogenenden, Mittelpunkte)
    seen: set[tuple] = set()
    moved = False

    def snap_point(x: float, y: float) -> tuple[float, float]:
        nonlocal moved
        for px, py in points:
            if math.hypot(x - px, y - py) < SNAP_TOLERANCE:
                moved = moved or (px, py) != (x, y)
                return px, py
        points.append((x, y))
        return x, y

    def snap_round(x: float, y: float, r: float) -> tuple[float, float, float]:
        nonlocal moved
        for cx, cy, cr in rounds:
            if math.hypot(x - cx, y - cy) < SNAP_TOLERANCE and abs(r - cr) < SNAP_TOLERANCE:
                moved = moved or (cx, cy, cr) != (x, y, r)
                return cx, cy, cr
        x, y = snap_point(x, y)         # neuer Träger: wenigstens der Mittelpunkt wird gefangen
        rounds.append((x, y, r))
        return x, y, r

    result: list = []
    for entity in entities:
        kind = entity[0]
        if kind == "Kreis":
            x, y, r = snap_round(*entity[1:4])
            key = ("Kreis", x, y, r)
            result.append(None if key in seen else ["Kreis", x, y, r, *entity[4:]])
            seen.add(key)
        elif kind == "Bogen":
            x, y, r = snap_round(*entity[1:4])
            for angle in entity[4:6]:      # Bogenenden als Fangpunkte für spätere Linien
                snap_point(x + r * math.cos(math.radians(angle)), y + r * math.sin(math.radians(angle)))
            result.append(["Bogen", x, y, r, *entity[4:]])
        elif kind == "Linie":
            start, end = snap_point(*entity[1:3]), snap_point(*entity[3:5])
            key = ("Linie", *sorted((start, end)))
            result.append(None if key in seen or start == end else ["Linie", *start, *end, *entity[5:]])
            seen.add(key)
        else:
            result.append(entity)
    return result, moved


def _edge(axes: gp_Ax2, entity: list):
    kind, values = entity[0], entity[1:]
    if kind == LINE3D:
        return BRepBuilderAPI_MakeEdge(_point3(axes, *values[0:3]), _point3(axes, *values[3:6])).Edge()
    if kind == SPLINE3D:
        # glatte Raumkurve durch die Stützstellen – dieselbe Form, die die Ansicht zeigt
        samples = track3d(entity)
        if len(samples) == 2:
            return BRepBuilderAPI_MakeEdge(_point3(axes, *samples[0]), _point3(axes, *samples[1])).Edge()
        points = HArray1_gp_Pnt(1, len(samples))
        for number, sample in enumerate(samples, start=1):
            points.SetValue(number, _point3(axes, *sample))
        curve = GeomAPI_Interpolate(points, False, 1e-7)
        curve.Perform()
        if not curve.IsDone():
            raise FeatureError("Der 3D-Spline ließ sich nicht bilden")
        return BRepBuilderAPI_MakeEdge(curve.Curve()).Edge()
    if kind == "Linie":
        return BRepBuilderAPI_MakeEdge(_point(axes, values[0], values[1]),
                                       _point(axes, values[2], values[3])).Edge()
    if kind == ELLIPSE:
        # Der Kern verlangt die größere Halbachse zuerst: sonst die Achsen tauschen und um 90° weiterdrehen
        major, minor, turn = (values[2], values[3], values[4]) if values[2] >= values[3] else (
            values[3], values[2], values[4] + 90.0)
        along = gp_Vec(axes.XDirection().XYZ()) * math.cos(math.radians(turn)) + gp_Vec(
            axes.YDirection().XYZ()) * math.sin(math.radians(turn))
        frame = gp_Ax2(_point(axes, values[0], values[1]), axes.Direction(), gp_Dir(along))
        if len(values) == 7:
            # Ellipsenbogen: die Laufwinkel zählen ab der ersten Halbachse; wurden die Achsen getauscht,
            # liegt die Bezugsrichtung des Kerns um 90° weiter
            shift = 0.0 if values[2] >= values[3] else 90.0
            start, end = math.radians(values[5] - shift), math.radians(values[6] - shift)
            if end < start:     # immer gegen den Uhrzeigersinn vom Start- zum Endwinkel
                end += 2 * math.pi
            return BRepBuilderAPI_MakeEdge(gp_Elips(frame, major, minor), start, end).Edge()
        return BRepBuilderAPI_MakeEdge(gp_Elips(frame, major, minor)).Edge()
    if kind == CP_SPLINE:
        # exakt als B-Spline-Kurve mit den Steuerpunkten als Polen (geklemmt: durch erste und letzte)
        from OCP.collections import Array1_int
        from OCP.Geom import Geom_BSplineCurve
        points = control_points(entity)
        degree = cp_degree(entity)
        if degree == 1:
            return BRepBuilderAPI_MakeEdge(_point(axes, *points[0]), _point(axes, *points[-1])).Edge()
        knots = _cp_knots(len(points), degree)
        distinct = sorted(set(knots))
        poles = TColgp_Array1OfPnt(1, len(points))
        for number, (x, y) in enumerate(points, start=1):
            poles.SetValue(number, _point(axes, x, y))
        values, multiplicities = TColStd_Array1OfReal(1, len(distinct)), Array1_int(1, len(distinct))
        for number, knot in enumerate(distinct, start=1):
            values.SetValue(number, knot)
            multiplicities.SetValue(number, knots.count(knot))
        return BRepBuilderAPI_MakeEdge(Geom_BSplineCurve(poles, values, multiplicities, degree)).Edge()
    if kind == CONIC:
        # exakt als rationale Bézierkurve: Anfang, Scheitel (mit Gewicht aus Rho), Ende
        poles, weights = TColgp_Array1OfPnt(1, 3), TColStd_Array1OfReal(1, 3)
        for number, (x, y) in enumerate((values[0:2], values[4:6], values[2:4]), start=1):
            poles.SetValue(number, _point(axes, x, y))
        for number, weight in enumerate((1.0, conic_weight(values[6]), 1.0), start=1):
            weights.SetValue(number, weight)
        return BRepBuilderAPI_MakeEdge(Geom_BezierCurve(poles, weights)).Edge()
    if kind == SPLINE:
        # Eine glatte Kurve durch die berechneten Stützstellen – dieselbe Form, die die Ansicht zeigt
        samples = spline_points(entity)
        if len(samples) == 2:
            return BRepBuilderAPI_MakeEdge(_point(axes, *samples[0]), _point(axes, *samples[1])).Edge()
        points = HArray1_gp_Pnt(1, len(samples))
        for number, sample in enumerate(samples, start=1):
            points.SetValue(number, _point(axes, *sample))
        curve = GeomAPI_Interpolate(points, False, 1e-7)
        curve.Perform()
        if not curve.IsDone():
            raise FeatureError("Der Spline ließ sich nicht bilden")
        return BRepBuilderAPI_MakeEdge(curve.Curve()).Edge()
    # Kreis und Bogen liegen in der Skizzenebene um ihren Mittelpunkt
    frame = gp_Ax2(_point(axes, values[0], values[1]), axes.Direction(), axes.XDirection())
    round_curve = gp_Circ(frame, values[2])
    if kind == "Kreis":
        return BRepBuilderAPI_MakeEdge(round_curve).Edge()
    start, end = math.radians(values[3]), math.radians(values[4])
    if end < start:     # immer gegen den Uhrzeigersinn vom Start- zum Endwinkel
        end += 2 * math.pi
    return BRepBuilderAPI_MakeEdge(round_curve, start, end).Edge()


def _inner_point(face) -> gp_Pnt | None:
    """Ein Punkt sicher im Inneren der Fläche (ihr Schwerpunkt kann in einem Loch liegen)."""
    # nicht parallel: kann im Probelauf (abgespaltener Prozess, ohne Thread-Pool des Kerns) laufen, dort hinge es
    BRepMesh_IncrementalMesh(face, 0.5, False, 0.5, False)
    location = TopLoc_Location()
    mesh = BRep_Tool.Triangulation_s(face, location)
    if mesh is None or mesh.NbTriangles() == 0:
        return None
    transform = location.Transformation()
    corners = [mesh.Node(index).Transformed(transform) for index in mesh.Triangle(1).Get()]
    return gp_Pnt(*(sum(getattr(corner, axis)() for corner in corners) / 3 for axis in ("X", "Y", "Z")))


def _without_inner_regions(profile):
    """Lässt Bereiche weg, die im Loch eines anderen Bereichs liegen (Kreis im Rechteck = Loch).

    Für jeden Bereich wird gezählt, in wie vielen anderen Umrissen er liegt; bei gerader
    Anzahl ist er Material, bei ungerader ein Loch.
    """
    faces = []
    explorer = TopExp_Explorer(profile, TopAbs_FACE)
    while explorer.More():
        faces.append(TopoDS.Face(explorer.Current()))
        explorer.Next()
    # Umriss jedes Bereichs als gefüllte Fläche (ohne dessen Löcher)
    outlines = [BRepBuilderAPI_MakeFace(BRepTools.OuterWire_s(face)).Face() for face in faces]
    builder, kept = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(kept)
    for index, face in enumerate(faces):
        point = _inner_point(face)
        depth = 0 if point is None else sum(
            1 for other, outline in enumerate(outlines)
            if other != index and BRepClass_FaceClassifier(outline, point, 1e-7).State() == TopAbs_IN)
        if depth % 2 == 0:
            builder.Add(kept, face)
    return kept


def _regions(curves):
    """Alle von den Kurven eingeschlossenen Bereiche als Flächenverbund (None, wenn es keine gibt)."""
    builder = BRep_Builder()
    wires, faces = TopoDS_Compound(), TopoDS_Compound()
    builder.MakeCompound(wires)
    builder.MakeCompound(faces)
    try:
        # Kurven an ihren Schnittpunkten teilen, zu Zügen verbinden, Flächen bilden
        BOPAlgo_Tools.EdgesToWires_s(curves, wires, False, 1e-8)
        if not BOPAlgo_Tools.WiresToFaces_s(wires, faces, 1e-8):
            return None
    except Exception:
        return None
    if not TopExp_Explorer(faces, TopAbs_FACE).More():
        return None
    if ft.geometry_level() < 2:
        return faces                    # ältere Entwürfe: Stege bleiben, wie sie damals gerechnet wurden
    cleaned = TopoDS_Compound()
    builder.MakeCompound(cleaned)
    for face in ft.faces_of(faces):
        builder.Add(cleaned, _without_dangling_edges(face))
    return cleaned


def _without_dangling_edges(face):
    """Die Bereichsfläche ohne offene Drahtstücke im Inneren.

    Eine Skizzenlinie, die nur zwei Ränder verbindet (z. B. ein Steg vom äußeren zum inneren Rahmen), teilt den
    Bereich nicht. WiresToFaces legt sie trotzdem als offenen Draht in die Fläche; die Extrusion machte daraus eine
    Schlitzfläche im Körper – der Körper war ungültig, und auf dem Deckel blieb eine Linie sichtbar (Nutzerentwurf
    2026-10-26). Wie in Fusion zählen nur geschlossene Ränder; die Fläche wird ohne die offenen Drähte neu gebaut.
    """
    wires = []
    explorer = TopExp_Explorer(face, TopAbs_WIRE)
    while explorer.More():
        wires.append(TopoDS.Wire(explorer.Current()))
        explorer.Next()
    closed = [wire for wire in wires if BRep_Tool.IsClosed_s(wire)]
    if len(closed) == len(wires) or not closed:
        return face
    outer = BRepTools.OuterWire_s(face)
    if not BRep_Tool.IsClosed_s(outer):
        return face
    maker = BRepBuilderAPI_MakeFace(BRep_Tool.Surface_s(face), outer, True)
    for wire in closed:
        if not wire.IsSame(outer):
            maker.Add(wire)
    if not maker.IsDone():
        return face
    rebuilt = maker.Face()
    rebuilt.Orientation(face.Orientation())
    return rebuilt


ft.FEATURE_CLASSES[Sketch.KIND] = Sketch
ft.FEATURE_CLASSES[ConstructionPlane.KIND] = ConstructionPlane


def face_frame(face) -> list | None:
    """Lage einer ebenen Körperfläche als 9 Zahlen (Ursprung, Normale, x-Richtung); None, wenn sie nicht eben ist.

    Der Ursprung ist der Flächenschwerpunkt, die Normale zeigt aus dem Körper heraus.
    """
    surface = BRepAdaptor_Surface(face)
    if surface.GetType() != GeomAbs_Plane:
        return None
    plane = surface.Plane()
    normal = plane.Axis().Direction()
    if face.Orientation() == TopAbs_REVERSED:
        normal = normal.Reversed()
    x_direction = plane.XAxis().Direction()
    origin = ft.face_point(face)
    return [*origin, normal.X(), normal.Y(), normal.Z(),
            x_direction.X(), x_direction.Y(), x_direction.Z()]


def face_outline(face, frame: list) -> list[list[tuple[float, float]]]:
    """Kanten der Fläche als Linienzüge in den Skizzenkoordinaten ihrer Lage (Zeichenhilfe)."""
    axes = frame_axes("", 0.0, frame)
    origin, x_dir, y_dir = axes.Location(), axes.XDirection(), axes.YDirection()
    outline = []
    for edge in ft.unique_edges(face):
        try:
            sampler = GCPnts_TangentialDeflection(BRepAdaptor_Curve(edge), 0.2, 0.05)
        except Exception:
            continue    # entartete Kante ohne Kurve
        polyline = []
        for index in range(1, sampler.NbPoints() + 1):
            offset = gp_Vec(origin, sampler.Value(index))
            polyline.append((offset.Dot(gp_Vec(x_dir.XYZ())), offset.Dot(gp_Vec(y_dir.XYZ()))))
        outline.append(polyline)
    return outline


def regions_2d(profile, axes: gp_Ax2) -> list[dict]:
    """Bereiche eines Profils für die Auswahl: je Bereich ein Innenpunkt im Raum sowie seine
    Dreiecke und Randstücke in Skizzenkoordinaten."""
    origin, x_dir, y_dir = axes.Location(), gp_Vec(axes.XDirection().XYZ()), gp_Vec(axes.YDirection().XYZ())

    def flat(point: gp_Pnt) -> tuple[float, float]:
        offset = gp_Vec(origin, point)
        return offset.Dot(x_dir), offset.Dot(y_dir)

    regions = []
    for face in ft.faces_of(profile):
        inner = _inner_point(face)      # zerlegt die Fläche dabei auch in Dreiecke
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, location)
        if inner is None or mesh is None:
            continue
        transform = location.Transformation()
        nodes = [flat(mesh.Node(index).Transformed(transform)) for index in range(1, mesh.NbNodes() + 1)]
        triangles = [tuple(nodes[corner - 1] for corner in mesh.Triangle(index).Get())
                     for index in range(1, mesh.NbTriangles() + 1)]
        outline = []
        for polyline in face_outline(face, [origin.X(), origin.Y(), origin.Z(),
                                            *(getattr(axes.Direction(), a)() for a in ("X", "Y", "Z")),
                                            *(getattr(axes.XDirection(), a)() for a in ("X", "Y", "Z"))]):
            outline += list(zip(polyline, polyline[1:]))
        regions.append({"point": (inner.X(), inner.Y(), inner.Z()), "triangles": triangles, "outline": outline})
    return regions


def closed_regions(entities: list, construction: list | None = None) -> list[list]:
    """Die geschlossenen Bereiche einer Skizze als Dreiecke in Skizzenkoordinaten: [[(u, v), (u, v), (u, v)], …].

    Dient der blauen Füllung beim Skizzieren (wie Fusion zeigt, welche Flächen ein Profil ergeben). Elemente,
    die (noch) unbrauchbar sind, ergeben einfach keine Füllung.
    """
    drawn = Sketch(name="_Füllung", entities=[list(entity) for entity in entities],
                   construction=list(construction or []))
    state = BuildState()
    try:
        drawn.apply(state)
    except Exception:
        return []
    if drawn.name not in state.profiles:
        return []
    try:
        regions = regions_2d(state.profiles[drawn.name], state.frames[drawn.name])
    except Exception:
        return []
    return [triangle for region in regions for triangle in region["triangles"]]


def _match_outline(old: list, fresh: list) -> dict[int, int]:
    """Ordnet alten projizierten Elementen die passende neue Kante zu (Stelle in `old` → Stelle in `fresh`).

    Passend heißt: gleiche Art und praktisch dieselbe Lage – Linien parallel (unter 2°) und höchstens ein Viertel
    der Umrissgröße voneinander entfernt, Kreise/Bögen mit nahem Mittelpunkt und ähnlichem Radius. Die besten
    Paare werden zuerst vergeben; jede Kante passt zu höchstens einem alten Element."""
    # Umrissgröße aus den Endpunkten der Linien bzw. dem umschließenden Quadrat der Kreise und Bögen
    points = [point for entity in fresh
              for point in ([entity[1:3], entity[3:5]] if entity[0] == "Linie" else
                            [(entity[1] - entity[3], entity[2] - entity[3]), (entity[1] + entity[3], entity[2] + entity[3])]
                            if entity[0] in ("Kreis", "Bogen") else [entity[1:3]])]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    size = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6) if points else 1.0
    limit = size * 0.25

    def cost(a: list, b: list) -> float | None:
        if a[0] != b[0]:
            return None
        if a[0] == "Linie":
            ax, ay, bx, by = a[1:5]
            cx, cy, dx, dy = b[1:5]
            u, v = (bx - ax, by - ay), (dx - cx, dy - cy)
            lu, lv = math.hypot(*u), math.hypot(*v)
            if lu < 1e-12 or lv < 1e-12 or abs(u[0] * v[1] - u[1] * v[0]) / (lu * lv) > math.sin(math.radians(2.0)):
                return None
            mid = ((cx + dx) / 2.0, (cy + dy) / 2.0)
            across = abs((mid[0] - ax) * u[1] - (mid[1] - ay) * u[0]) / lu      # Abstand quer zur alten Linie
            along = math.hypot(mid[0] - (ax + bx) / 2.0, mid[1] - (ay + by) / 2.0)
            return None if across > limit else across * 10.0 + along
        if a[0] in ("Kreis", "Bogen"):
            centre = math.hypot(a[1] - b[1], a[2] - b[2])
            radius = abs(a[3] - b[3])
            if centre + radius > limit:
                return None
            if a[0] == "Bogen":                                  # Bögen: auch die Lage auf dem Kreis zählt
                middle = lambda arc: math.radians((arc[4] + ((arc[5] - arc[4]) % 360.0) / 2.0) % 360.0)
                turn = abs((middle(a) - middle(b) + math.pi) % (2.0 * math.pi) - math.pi)
                if turn > math.pi / 2.0:
                    return None
                return centre + radius + turn * a[3]
            return centre + radius
        return 0.0 if a == b else None                           # sonstige Elemente nur bei gleicher Lage

    candidates = sorted((value, position, number) for position, a in enumerate(old) for number, b in enumerate(fresh)
                        if (value := cost(a, b)) is not None)
    pairs: dict[int, int] = {}
    for _value, position, number in candidates:
        if position not in pairs and number not in pairs.values():
            pairs[position] = number
    return pairs


def _oriented_like(old: list, new: list) -> list:
    """Die neue Kante so herum wie das alte Element: bei Linien bleiben Anfang und Ende auf derselben Seite, damit
    Abhängigkeiten an „Anfang“ bzw. „Ende“ den richtigen Punkt behalten."""
    if old[0] == "Linie" and new[0] == "Linie":
        ax, ay, bx, by = old[1:5]
        cx, cy, dx, dy = new[1:5]
        if math.hypot(ax - cx, ay - cy) + math.hypot(bx - dx, by - dy) > math.hypot(ax - dx, ay - dy) + math.hypot(bx - cx, by - cy):
            return [new[0], dx, dy, cx, cy, *new[5:]]
    return list(new)
