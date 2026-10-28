# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Maße und Abhängigkeiten einer Skizze.

Eine Abhängigkeit ist eine einfache Liste `[Art, a, b, Wert]`: `a` und `b` sind Nummern
von Skizzenelementen (`b` = -1, wenn nur ein Element betroffen ist), `Wert` ist das Maß
in mm bzw. Grad (0, wenn die Art keines hat). Bei „Punkt auf Kurve“ nennt `Wert` den Punkt
des Elements `a`: Linie 0 = Anfang, 1 = Ende; Kreis 0 = Mittelpunkt; Bogen 0 = Mittelpunkt,
1 = Anfang, 2 = Ende.

Ein Maß darf als fünften Eintrag einen Ausdruck tragen (`[Art, a, b, Wert, "breite / 2"]`):
dann bestimmt der Ausdruck den Wert, sobald das Modell mit den Benutzerparametern rechnet.

Der Löser verschiebt die Zahlenwerte der Elemente so wenig wie möglich, bis alle
Abhängigkeiten erfüllt sind. Punkte, die vor dem Lösen aufeinanderliegen (Linienenden,
Bogenenden, Mittelpunkte), bleiben dabei verbunden – so hält ein Rechteck zusammen,
wenn man eine Seite bemaßt.
"""

from __future__ import annotations

import math

import numpy as np

from . import sketch as sk

LENGTH, DIAMETER, RADIUS = "Länge", "Durchmesser", "Radius"
HORIZONTAL, VERTICAL = "Horizontal", "Vertikal"
PARALLEL, PERPENDICULAR, EQUAL = "Parallel", "Rechtwinklig", "Gleich"
DISTANCE, ANGLE = "Abstand", "Winkel"
DIRECTION = "Neigung"   # Winkel einer Linie gegen die x-Achse der Skizze (Grad, gegen den Uhrzeigersinn)
SEMI_AXES = ("Halbachse 1", "Halbachse 2")      # die beiden Halbachsen einer Ellipse
TANGENT, POINT_ON = "Tangential", "Punkt auf Kurve"
FIX = "Fixieren"        # das Element bleibt, wo es ist
SYMMETRY = "Symmetrisch"    # Element b ist das Spiegelbild von a; `Wert` nennt die Nummer der Spiegelachse
# Abstand zweier einzelner Punkte (z. B. Spline-Punkte): [Art, a, b, Abstand, Ausdruck, Punkt an a, Punkt an b] –
# die Punktnummern zählen wie bei `point_of`; der Ausdruck ist leer, wenn das Maß eine feste Zahl ist
POINT_DISTANCE = "Punktabstand"
# Verknüpfte Kopien (Muster, Versatz): b folgt a, wenn sich a ändert.
#   Verschoben: [Art, a, b, 0, "", dx, dy] – b ist a um (dx, dy) verschoben
#   Gedreht:    [Art, a, b, Winkel, "", cx, cy] – b ist a um den Winkel (Grad) um (cx, cy) gedreht
#   Konzentrisch: [Art, a, b, Δr] – Kreise/Bögen mit gleicher Mitte, Radius von b = Radius von a + Δr
TRANSLATED, ROTATED, CONCENTRIC = "Verschoben", "Gedreht", "Konzentrisch"
# Wie in Fusion als eigene Werkzeuge:
#   Deckungsgleich: [Art, a, b, 0, "", Punkt an a, Punkt an b] – zwei Punkte liegen aufeinander
#   Mittelpunkt:    [Art, a, b, Punkt an a] – der Punkt von a liegt auf der Mitte der Linie b
#   Kollinear:      [Art, a, b, 0] – zwei Linien liegen auf derselben Geraden
# „Konzentrisch“ als Werkzeug: [Art, a, b, 0, "", FREE_RADIUS] – dann gilt nur die gleiche Mitte
# (der fünfte Eintrag bleibt wie überall der Platz für einen Parameter-Ausdruck)
COINCIDENT, MIDPOINT, COLLINEAR = "Deckungsgleich", "Mittelpunkt", "Kollinear"
# Waagrecht/Senkrecht zwischen zwei Punkten (Fusion bietet das neben Linien an):
#   [Art, a, b, 0, "", Punkt an a, Punkt an b] – beide Punkte gleiche y- bzw. x-Lage
ALIGN_H, ALIGN_V = "Waagrecht ausgerichtet", "Senkrecht ausgerichtet"
# Krümmung (G2) wie Fusion: [Art, a, b, 0] – Griff-Spline und Linie/Bogen gehen am gemeinsamen Ende mit gleicher
# Richtung und gleicher Krümmung ineinander über
CURVATURE = "Krümmung"
# Getriebene Maße wie in Fusion: sie zeigen nur den gemessenen Wert und treiben nichts. Markiert über den Platz des
# Ausdrucks (ein getriebenes Maß hat keinen eigenen Ausdruck); der Löser lässt sie aus.
DRIVEN = "(getrieben)"
# Punktabstand nur in einer Richtung (Fusion: Maß waagrecht bzw. senkrecht platziert): achter Eintrag "x" oder "y"
POINT_AXES = ("x", "y")
# Abstand bis zum Kreisrand (Fusion: „Pick Circle/Arc Tangent“): [Art, a, b, Wert] – bei Kreisen und Bögen zählt
# statt der Mitte der nächstgelegene Rand (Mittenabstand minus Radius bzw. Radien)
RIM_DISTANCE = "Abstand Rand"
# Krümmung am Kurvenpunkt eines Griff-Splines (Fusion: Krümmungsgriff): [Art, a, -1, Radius, Ausdruck, Punkt] –
# auf beiden Seiten des Punkts gilt dieselbe Krümmung 1/Radius (die Kurve biegt dort stetig, G2)
POINT_CURVATURE = "Krümmungsradius"


def is_driven(rule: list) -> bool:
    return len(rule) > 4 and rule[4] == DRIVEN
FREE_RADIUS = "frei"
_LINKED = (TRANSLATED, ROTATED)             # Arten mit Lage in den Einträgen 5 und 6
# Bei „Verschoben“ trägt der sonst unbenutzte Wert die Herkunft aus einem Muster: Gruppe·10⁶ + Spalte·10³ + Zeile


def pattern_place(rule: list) -> tuple[int, int, int]:
    """Gruppe, Spalte und Zeile einer Musterkopie (aus dem Wert eines „Verschoben“)."""
    code = int(round(rule[3]))
    return code // 1_000_000, (code // 1000) % 1000, code % 1000


def pattern_code(group: int, column: int, row: int) -> float:
    return float(group * 1_000_000 + column * 1000 + row)
SINGLES = (HORIZONTAL, VERTICAL, FIX)       # Abhängigkeiten an einem einzelnen Element ohne Maßzahl

SINGLE_DIMENSIONS = (LENGTH, DIAMETER, RADIUS, *SEMI_AXES, POINT_CURVATURE)      # Maße an einem einzelnen Element
DIMENSIONS = SINGLE_DIMENSIONS + (DISTANCE, ANGLE, DIRECTION, POINT_DISTANCE, RIM_DISTANCE)   # alle Arten mit Maßzahl
# Arten, die zwei Elemente verbinden
PAIRS = (PARALLEL, PERPENDICULAR, EQUAL, DISTANCE, ANGLE, TANGENT, POINT_ON, SYMMETRY, POINT_DISTANCE,
         TRANSLATED, ROTATED, CONCENTRIC, COINCIDENT, MIDPOINT, COLLINEAR, ALIGN_H, ALIGN_V, CURVATURE, RIM_DISTANCE)
SYMBOLS = {HORIZONTAL: "—", VERTICAL: "|", PARALLEL: "//", PERPENDICULAR: "⊥", EQUAL: "=",
           TANGENT: "T", POINT_ON: "×", FIX: "fix", SYMMETRY: "sym", TRANSLATED: "⇉", ROTATED: "↻",
           CONCENTRIC: "◎", COINCIDENT: "●", MIDPOINT: "⊿", COLLINEAR: "⋯", ALIGN_H: "—·", ALIGN_V: "|·",
           CURVATURE: "G2"}


def _value_roles(entity: list) -> str:
    """Bedeutung jedes Zahlenwerts eines Elements: x/y Koordinate, a Winkel gegen die x-Achse, s sonstige Größe."""
    kind = entity[0]
    if kind in ("Linie", sk.POINT) or kind == sk.SPLINE:
        return "xy" * ((len(entity) - 1) // 2)
    if kind in sk.SPATIAL:
        return "xys" * ((len(entity) - 1) // 3)     # z (Abstand von der Ebene) dreht und spiegelt nicht mit
    if kind == sk.CONIC:
        return "xyxyxys"        # Anfang, Ende, Scheitel und Rho
    if kind == sk.CP_SPLINE:
        return "xy" * ((len(entity) - 2) // 2) + "s"      # Steuerpunkte und der Grad
    if kind == "Kreis":
        return "xys"
    if kind == "Bogen":
        return "xysaa"
    # Ellipse: die Laufwinkel eines Bogens zählen ab der eigenen Achse und drehen deshalb nicht mit
    return "xyssa" + "ss" * (len(entity) == 8)

_COINCIDENT = 1e-6      # so nah beieinander gelten zwei Punkte als verbunden (mm)
_TOLERANCE = 1e-7       # so genau müssen die Abhängigkeiten am Ende erfüllt sein
_ITERATIONS = 60
_RADIUS_EASE = 0.2      # so viel leichter als ein Punkt lässt sich ein Radius verändern (kleiner = träger)
_COLOURING_FROM = 60    # ab so vielen Zahlen werden die Ableitungen mit Färbung gebildet (schneller, gleich genau)
_HEIGHT_EASE = 0.02     # Höhe z räumlicher Punkte: sehr träge – ein Punkt auf der Ebene bzw. an einer Körperecke
                        # bleibt auf seiner Höhe, Maße verschieben ihn vor allem in der Ebene


def dimension_for(entities: list, index: int) -> list | None:
    """Das zum Element passende Maß mit seinem aktuellen Wert (None, wenn es keines gibt)."""
    entity = entities[index]
    if entity[0] == "Linie":
        return [LENGTH, index, -1, math.dist(entity[1:3], entity[3:5])]
    if entity[0] == sk.LINE3D:
        return [LENGTH, index, -1, math.dist(entity[1:4], entity[4:7])]     # räumliche Länge
    if entity[0] == "Kreis":
        return [DIAMETER, index, -1, 2.0 * entity[3]]
    if entity[0] == "Bogen":
        return [RADIUS, index, -1, entity[3]]
    if entity[0] == sk.ELLIPSE:
        return [SEMI_AXES[0], index, -1, entity[3]]     # die zweite Halbachse bemaßt der nächste Klick
    return None         # z. B. Spline: kein eigenes Maß


def point_count(entity: list) -> int:
    """Zahl der benennbaren Punkte eines Elements (siehe Modulbeschreibung)."""
    if entity[0] == sk.SPLINE:
        return (len(entity) - 1) // 2
    if sk.is_ellipse_arc(entity):
        return 3                    # Mittelpunkt und die beiden Enden
    if entity[0] == sk.POINT:
        return 1
    if entity[0] in sk.SPATIAL:
        return 0                    # 3D-Linien und 3D-Splines haben keine Punkte in der Ebene
    if entity[0] == sk.CONIC:
        return 3                    # Anfang, Ende und Scheitel
    if entity[0] == sk.CP_SPLINE:
        return (len(entity) - 2) // 2       # alle Steuerpunkte
    return {"Linie": 2, "Kreis": 1, "Bogen": 3, "Ellipse": 1}[entity[0]]


def point_of(entity: list, slot: int) -> tuple[float, float]:
    """Lage des Punkts `slot` eines Elements."""
    point = _points(entity, np.array(entity[1:], dtype=float), 0)[slot]
    return float(point[0]), float(point[1])


def measured(entities: list, rule: list) -> float | None:
    """Der Wert, den ein Maß an den Elementen gerade misst (für getriebene Maße); None bei anderen Arten."""
    kind, a, b = rule[:3]
    entity = entities[a]
    if kind == LENGTH:
        if entity[0] == sk.LINE3D:
            return math.dist(entity[1:4], entity[4:7])
        return math.dist(entity[1:3], entity[3:5])
    if kind == DIAMETER:
        return 2.0 * entity[3]
    if kind == RADIUS:
        return float(entity[3])
    if kind in SEMI_AXES:
        return float(entity[3 + SEMI_AXES.index(kind)])
    if kind == POINT_CURVATURE:
        found = _point_curvatures(entity, np.array(entity[1:], dtype=float), 0, rule[5])
        mean = abs(sum(c for c, _length in found) / max(len(found), 1))
        return 1.0 / mean if mean > 1e-12 else None
    if kind in (DISTANCE, ANGLE, RIM_DISTANCE):
        return measure(entities, kind, a, b)
    if kind == POINT_DISTANCE:
        if len(rule) > 7 and rule[7] in POINT_AXES:
            axis = POINT_AXES.index(rule[7])
            return abs(point_of(entities[a], rule[5])[axis] - point_of(entities[b], rule[6])[axis])
        return point_distance(entities, a, rule[5], b, rule[6])
    return None


def update_driven(entities: list, constraints: list) -> None:
    """Trägt in getriebene Maße den gemessenen Wert ein (nach jedem Lösen)."""
    for rule in constraints:
        if is_driven(rule):
            value = measured(entities, rule)
            if value is not None:
                rule[3] = float(value)


def is_redundant(entities: list, constraints: list, rule: list) -> bool:
    """Bestimmt das neue Maß nichts, was nicht schon bestimmt ist (es würde die Skizze überbestimmen)?

    Geprüft über den Rang der Ableitungsmatrix: kommt mit dem Maß keine neue unabhängige Bedingung hinzu,
    ist es überzählig – Fusion legt es dann als getriebenes Maß an.
    """
    active = [other for other in constraints if not is_driven(other)]

    def rank(rules: list) -> int:
        values, _starts, residuals = _system(entities, rules)
        error = residuals(values)
        if not len(error):
            return 0
        jacobian = np.empty((len(error), len(values)))
        for column in range(len(values)):
            probe = values.copy()
            probe[column] += 1e-6
            jacobian[:, column] = (residuals(probe) - error) / 1e-6
        sizes = np.linalg.svd(jacobian, compute_uv=False)
        return int(np.sum(sizes > 1e-6 * max(1.0, sizes[0])))

    return rank(active + [rule]) <= rank(active)


def point_distance(entities: list, a: int, first: int, b: int, second: int) -> float:
    """Aktueller Abstand zwischen dem Punkt `first` von Element a und dem Punkt `second` von Element b."""
    return math.dist(point_of(entities[a], first), point_of(entities[b], second))


def measure(entities: list, kind: str, a: int, b: int) -> float:
    """Aktueller Wert eines Maßes zwischen zwei Elementen (Abstand in mm oder Winkel in Grad)."""
    values = np.array([float(v) for entity in entities for v in entity[1:]])
    starts = np.cumsum([0] + [len(entity) - 1 for entity in entities])
    if kind == ANGLE:
        return math.degrees(_angle_between(_direction(values, starts[a]), _direction(values, starts[b])))
    if kind == RIM_DISTANCE:
        return _rim_distance(entities, values, starts, a, b)
    return _distance(entities, values, starts, a, b)


def check(entities: list, constraint: list) -> str | None:
    """Fehlermeldung, wenn die Abhängigkeit nicht zu den Elementen passt, sonst None."""
    if constraint and constraint[0] == POINT_CURVATURE:
        if len(constraint) != 6:
            return f"Unbekannte Abhängigkeit {constraint!r}"
        a, slot = constraint[1], constraint[5]
        if not (isinstance(a, int) and 0 <= a < len(entities) and entities[a][0] == sk.SPLINE
                and sk.has_handles(entities[a])):
            return "Krümmungsradius: gilt an einem Spline mit Griffen"
        if not (isinstance(slot, int) and 0 <= slot < len(sk.fit_points(entities[a]))):
            return "Krümmungsradius: den Kurvenpunkt gibt es nicht"
        return None if constraint[3] > 0 else "Krümmungsradius: das Maß muss größer als 0 sein"
    if constraint and constraint[0] == POINT_DISTANCE and len(constraint) == 8:
        if constraint[7] not in POINT_AXES:
            return f"Unbekannte Abhängigkeit {constraint!r}"
    elif constraint and constraint[0] in (POINT_DISTANCE, COINCIDENT, ALIGN_H, ALIGN_V, *_LINKED):
        if len(constraint) != 7:
            return f"Unbekannte Abhängigkeit {constraint!r}"
    elif constraint and constraint[0] == CONCENTRIC and len(constraint) == 6:
        if constraint[5] != FREE_RADIUS:
            return f"Unbekannte Abhängigkeit {constraint!r}"
    elif len(constraint) not in (4, 5) or constraint[0] not in DIMENSIONS + PAIRS + SINGLES:
        return f"Unbekannte Abhängigkeit {constraint!r}"
    kind, a, b, value = constraint[:4]
    needed = (a, b) if kind in PAIRS else (a,)
    if not all(isinstance(i, int) and 0 <= i < len(entities) for i in needed):
        return f"{kind}: das Element gibt es nicht (mehr)"
    kinds = [entities[i][0] for i in needed]
    if kind == FIX:
        return None         # jedes Element lässt sich fixieren
    if kind == LENGTH and kinds == [sk.LINE3D]:
        # Länge einer 3D-Linie (räumlich gemessen)
        return None if value > 0 else "Länge: das Maß muss größer als 0 sein"
    if any(k in sk.SPATIAL for k in kinds):
        return f"{kind} geht bei 3D-Linien und 3D-Splines nicht (dort nur Fixieren und Länge der 3D-Linie)"
    if kind == RIM_DISTANCE:
        if a == b or not any(k in ("Kreis", "Bogen") for k in kinds) or \
                any(k not in ("Kreis", "Bogen", "Linie", sk.POINT) for k in kinds):
            return "Abstand Rand: ein Kreis oder Bogen und eine Linie, ein Punkt oder ein weiterer Kreis"
        return None if value > 0 else "Abstand Rand: das Maß muss größer als 0 sein"
    if kind in (ALIGN_H, ALIGN_V):
        first, second = constraint[5:7]
        if not (isinstance(first, int) and isinstance(second, int) and 0 <= first < point_count(entities[a])
                and 0 <= second < point_count(entities[b])) or (a == b and first == second):
            return f"{kind}: zwei verschiedene Punkte anklicken"
        return None
    if kind == CURVATURE:
        spline_side = [i for i in (a, b) if entities[i][0] == sk.SPLINE and sk.has_handles(entities[i])]
        other = [i for i in (a, b) if i not in spline_side]
        if a == b or len(spline_side) != 1 or len(other) != 1 or entities[other[0]][0] not in ("Linie", "Bogen"):
            return "Krümmung: einen Spline mit Griffen und eine Linie oder einen Bogen wählen"
        if _shared_end(entities[a], entities[b]) is None:
            return "Krümmung: beide Elemente müssen an einem Ende zusammenstoßen"
        return None
    if kind == COINCIDENT:
        # geht an jedem Element mit Punkten (auch Splines, Skizzenpunkte, Kurvenenden)
        first, second = constraint[5:7]
        if a == b or not (isinstance(first, int) and isinstance(second, int)
                          and 0 <= first < point_count(entities[a]) and 0 <= second < point_count(entities[b])):
            return "Deckungsgleich: zwei Punkte an verschiedenen Elementen anklicken"
        return None
    if kind == MIDPOINT:
        if a == b or entities[b][0] != "Linie" or not (value == int(value) and 0 <= value < point_count(entities[a])):
            return "Mittelpunkt: erst einen Punkt anklicken, dann die Linie, auf deren Mitte er kommt"
        return None
    if kind == COLLINEAR:
        return None if a != b and kinds[0] == kinds[1] == "Linie" else "Kollinear gilt für zwei verschiedene Linien"
    if sk.POINT in kinds:
        # ein Punkt hat keine Größe und keine Richtung: nur Lage-Beziehungen ergeben Sinn
        allowed = kind in (DISTANCE, POINT_DISTANCE, TRANSLATED, ROTATED, SYMMETRY) or (kind == POINT_ON and kinds[1] != sk.POINT)
        if not allowed:
            return f"{kind} geht bei einem Skizzenpunkt nicht"
    if kind == TANGENT and any(_has_end_tangent(entities[i]) for i in (a, b)):
        # Kurve mit Endtangente (Kegelschnitt, Steuerpunkt-Spline, Spline mit Griffen): tangential an ihrem Ende,
        # wo sie an eine Linie, einen Bogen oder eine solche Kurve anschließt (wie in Fusion)
        partner = [i for i in (a, b) if not _has_end_tangent(entities[i])]
        if a == b or any(entities[i][0] not in ("Linie", "Bogen") for i in partner):
            return "Tangential an Kurvenenden: Kurve mit Linie, Bogen oder einer anderen Kurve verbinden"
        if _shared_end(entities[a], entities[b]) is None:
            return "Tangential an Kurvenenden: beide Elemente müssen an einem Ende zusammenstoßen"
        return None
    if sk.CONIC in kinds or sk.CP_SPLINE in kinds:
        # wie beim Spline: nur über seine Punkte bemaßbar
        curve = sk.CONIC if sk.CONIC in kinds else sk.CP_SPLINE
        allowed = kind in (POINT_DISTANCE, TRANSLATED, ROTATED) or (
            kind == POINT_ON and kinds[1] not in (sk.CONIC, sk.CP_SPLINE))
        if not allowed:
            return f"{kind} geht bei einem {curve} nicht (nur seine Punkte lassen sich bemaßen)"
    if kind in _LINKED:
        if a == b or str(entities[a][0]) != str(entities[b][0]) or len(entities[a]) != len(entities[b]):
            return f"{kind}: die Kopie muss ein anderes Element derselben Art sein"
        return None
    if kind == CONCENTRIC:
        ellipses = all(k == sk.ELLIPSE for k in kinds) and len(entities[a]) == len(entities[b])
        if a == b or not (ellipses or all(k in ("Kreis", "Bogen") for k in kinds)):
            return "Konzentrisch gilt für zwei Kreise oder Bögen (oder zwei gleichartige Ellipsen)"
        return None
    if kind == POINT_DISTANCE:
        # geht an jedem Element, das Punkte hat – auch an Splines und Ellipsen
        first, second = constraint[5:7]
        if not (isinstance(first, int) and isinstance(second, int) and 0 <= first < point_count(entities[a])
                and 0 <= second < point_count(entities[b])):
            return "Punktabstand: den Punkt gibt es an diesem Element nicht"
        if a == b and first == second:
            return "Punktabstand: zwei verschiedene Punkte wählen"
        return None if value > 0 else "Punktabstand: das Maß muss größer als 0 sein"
    if kind == SYMMETRY:
        axis = int(value) if value == int(value) else -1
        if not 0 <= axis < len(entities) or entities[axis][0] != "Linie" or axis in (a, b) or a == b:
            return "Symmetrisch: die Spiegelachse muss eine andere Linie sein"
        if str(kinds[0]) != str(kinds[1]) or kinds[0] == sk.ELLIPSE:
            return "Symmetrisch gilt für zwei gleichartige Elemente (Linien, Kreise, Bögen oder Splines)"
        if kinds[0] == sk.SPLINE and len(entities[a]) != len(entities[b]):
            return "Symmetrisch: beide Splines brauchen gleich viele Punkte"
        return None
    if sk.SPLINE in kinds and not (kind == POINT_ON and kinds[1] != sk.SPLINE):
        return f"{kind} geht bei Splines nicht (nur ihre Punkte lassen sich auf eine Kurve legen)"
    if kind in SEMI_AXES:
        if kinds[0] != sk.ELLIPSE:
            return f"{kind} gilt nur für Ellipsen"
        return None if value > 0 else f"{kind}: das Maß muss größer als 0 sein"
    if sk.ELLIPSE in kinds and not (kind == POINT_ON and kinds[1] != sk.ELLIPSE):
        return f"{kind} geht bei Ellipsen nicht (nur ihr Mittelpunkt lässt sich auf eine Kurve legen)"
    if kind == DIRECTION:
        if kinds[0] != "Linie":
            return "Neigung gilt nur für Linien"
        return None if -360 <= value <= 360 else "Neigung: der Winkel muss zwischen -360 und 360 Grad liegen"
    if kind in DIMENSIONS and value <= 0:
        return f"{kind}: das Maß muss größer als 0 sein"
    if kind == ANGLE and value >= 180:
        return "Winkel: das Maß muss kleiner als 180 Grad sein"
    if kind in (LENGTH, HORIZONTAL, VERTICAL, PARALLEL, PERPENDICULAR, ANGLE) and any(k != "Linie" for k in kinds):
        return f"{kind} gilt nur für Linien"
    if kind == TANGENT and kinds[0] == kinds[1] == "Linie":
        return "Tangential braucht mindestens einen Kreis oder Bogen"
    if kind == POINT_ON and not (value == int(value) and 0 <= value < point_count(entities[a])):
        return "Punkt auf Kurve: den Punkt gibt es an diesem Element nicht"
    if kind == DIAMETER and kinds[0] != "Kreis" or kind == RADIUS and kinds[0] != "Bogen":
        return f"{kind} passt nicht zu einem Element der Art {kinds[0]}"
    if kind == EQUAL and (a == b or ("Linie" in kinds) != (kinds[0] == kinds[1] == "Linie")):
        return "Gleich gilt für zwei Linien oder für zwei Kreise/Bögen"
    if kind in PAIRS and a == b:
        return f"{kind} braucht zwei verschiedene Elemente"
    return None


def without_entity(constraints: list, index: int) -> list:
    """Die Abhängigkeiten nach dem Löschen des Elements `index`: betroffene entfallen,
    die Nummern der späteren Elemente rücken auf."""
    kept = []
    for kind, a, b, value, *expression in constraints:
        if index in (a, b):
            continue
        if kind in (SYMMETRY, POINT_ON) and kind == SYMMETRY:
            # hier ist der Wert die Nummer der Spiegelachse: sie zählt mit oder die Abhängigkeit entfällt
            if int(value) == index:
                continue
            value = float(int(value) - (int(value) > index))
        kept.append([kind, a - (a > index), b - (b > index), value, *expression])
    return kept


def expression_of(constraint: list) -> str:
    """Der Ausdruck eines Maßes (leer, wenn es eine feste Zahl ist)."""
    return constraint[4] if len(constraint) > 4 and constraint[4] != DRIVEN else ""


def apply_expressions(entities: list, constraints: list, evaluate) -> list | None:
    """Rechnet die Ausdrücke der Maße aus und zieht die Elemente nach.

    `evaluate(text)` liefert den Wert eines Ausdrucks. Zurück kommen die neuen Elemente,
    None, wenn kein Maß einen Ausdruck trägt (dann bleibt alles, wie es ist). Die Werte in
    `constraints` werden dabei aktualisiert. Passen die Maße nicht mehr zusammen, gibt es
    einen `ValueError`.
    """
    bound = [rule for rule in constraints if expression_of(rule)]
    if not bound:
        return None
    for rule in bound:
        rule[3] = float(evaluate(rule[4]))
    solved = solve(entities, constraints)
    if solved is None:
        raise ValueError("Die Maße der Skizze passen mit diesen Parameterwerten nicht zusammen")
    return solved


# --- Löser ------------------------------------------------------------------

def _has_end_tangent(entity: list) -> bool:
    """Hat das Element eine einstellbare Richtung an seinen Enden (Kegelschnitt, Steuerpunkt-Spline, Griff-Spline)?"""
    return entity[0] in (sk.CONIC, sk.CP_SPLINE) or (entity[0] == sk.SPLINE and sk.has_handles(entity))


def _end_points(entity: list, values: np.ndarray, start: int) -> list[tuple]:
    """Anfang und Ende eines Elements mit seiner Laufrichtung dort: [(Punkt, Richtung), (Punkt, Richtung)].

    Bögen liefern statt der Richtung den Mittelpunkt (die Tangente steht senkrecht auf dem Radius).
    """
    v = values[start:]
    kind = entity[0]
    if kind == "Linie":
        direction = v[2:4] - v[0:2]
        return [(v[0:2], direction), (v[2:4], direction)]
    if kind == sk.CONIC:
        return [(v[0:2], v[4:6] - v[0:2]), (v[2:4], v[2:4] - v[4:6])]
    if kind == sk.CP_SPLINE:
        last = len(entity) - 3          # Anfang des letzten Steuerpunkts im Zahlenvektor
        return [(v[0:2], v[2:4] - v[0:2]), (v[last - 1:last + 1], v[last - 1:last + 1] - v[last - 3:last - 1])]
    if kind == sk.SPLINE:
        points, tips, _backs = _handle_parts(entity, v)     # Griff-Spline: erster und letzter Kurvenpunkt
        return [(points[0], tips[0] - points[0]), (points[-1], tips[-1] - points[-1])]
    if kind == "Bogen":
        ends = _points(entity, values, start)[1:]
        return [(ends[0], None), (ends[1], None)]
    raise ValueError(kind)


def _shared_end(first: list, second: list) -> tuple[int, int] | None:
    """Die Enden (0 = Anfang, 1 = Ende), an denen zwei Elemente zusammenstoßen; None, wenn keine."""
    values_a = np.array(first[1:], dtype=float)
    values_b = np.array(second[1:], dtype=float)
    ends_a, ends_b = _end_points(first, values_a, 0), _end_points(second, values_b, 0)
    pairs = [(float(np.linalg.norm(ends_a[i][0] - ends_b[j][0])), i, j) for i in (0, 1) for j in (0, 1)]
    best = min(pairs)
    return (best[1], best[2]) if best[0] < 1e-6 else None


def _end_tangency(entities: list, values: np.ndarray, starts, a: int, b: int) -> float:
    """Fehler der Tangentialität am gemeinsamen Ende (in mm: Querabweichung je mm Richtung)."""
    ends_a, ends_b = _end_points(entities[a], values, starts[a]), _end_points(entities[b], values, starts[b])
    _gap, i, j = min((float(np.linalg.norm(ends_a[i][0] - ends_b[j][0])), i, j) for i in (0, 1) for j in (0, 1))
    (point_a, dir_a), (point_b, dir_b) = ends_a[i], ends_b[j]
    if dir_a is None:                   # a ist ein Bogen: Richtung von b senkrecht zum Radius von a
        dir_a, dir_b = dir_b, None
        centre = values[starts[a]:starts[a] + 2]
        point_a = point_b
    elif dir_b is None:
        centre = values[starts[b]:starts[b] + 2]
    if dir_b is None:
        radial = point_a - centre
        return float(dir_a @ radial) / max(float(np.linalg.norm(dir_a)), 1e-9)
    cross = dir_a[0] * dir_b[1] - dir_a[1] * dir_b[0]
    return float(cross) / max(float(np.linalg.norm(dir_a)), 1e-9)

def _handle_parts(entity: list, v: np.ndarray) -> tuple[list, list, list]:
    """Kurvenpunkte, Griffspitzen und Gegengriffspitzen eines Griff-Splines aus seinem Zahlenvektor `v`
    (wie `sk.back_tips`, aber auf den Löserwerten: der Gegengriff zeigt immer gegen den Griff)."""
    count = (len(entity) - 1) // (6 if sk.has_uneven_handles(entity) else 4)
    points = [v[4 * i:4 * i + 2] for i in range(count)]
    tips = [v[4 * i + 2:4 * i + 4] for i in range(count)]
    if not sk.has_uneven_handles(entity):
        return points, tips, [2 * p - g for p, g in zip(points, tips)]
    backs = []
    for i, (p, g) in enumerate(zip(points, tips)):
        stored = v[4 * count + 2 * i:4 * count + 2 * i + 2]
        lead = max(float(np.linalg.norm(g - p)), 1e-12)
        backs.append(p - (g - p) / lead * float(np.linalg.norm(stored - p)))
    return points, tips, backs


def _point_curvatures(entity: list, values: np.ndarray, start: int, slot: int) -> list[tuple[float, float]]:
    """Krümmung (mit Vorzeichen: links herum positiv) eines Griff-Splines am Kurvenpunkt `slot`, je Seite, samt
    der Grifflänge als Gewicht: [(κ, Länge), …]. Je Bézierabschnitt P0, P1, P2: κ = 2/3 · ((P1−P0)×(P2−P0)) / |P1−P0|³."""
    v = values[start:start + len(entity) - 1]
    points, tips, backs = _handle_parts(entity, v)
    here, found = points[slot], []

    def signed(p0, p1, p2, backwards: bool) -> tuple[float, float]:
        lead, second = p1 - p0, p2 - p0
        length = max(float(np.linalg.norm(lead)), 1e-9)
        cross = float(lead[0] * second[1] - lead[1] * second[0])
        # rückwärts gelesen (Abschnitt davor) kehrt sich das Vorzeichen um
        return (-1.0 if backwards else 1.0) * 2.0 / 3.0 * cross / length ** 3, length

    if slot + 1 < len(points):
        found.append(signed(here, tips[slot], backs[slot + 1], False))
    if slot > 0:
        found.append(signed(here, backs[slot], tips[slot - 1], True))
    return found


def _end_curvature(entities: list, values: np.ndarray, starts, a: int, b: int) -> float:
    """Krümmungsfehler am gemeinsamen Ende von Griff-Spline und Linie/Bogen (in 1/mm, mit Länge gewichtet).

    Am Ende eines Bézierabschnitts P0, P1, P2, … ist die Krümmung κ = 2/3 · |(P1−P0)×(P2−P0)| / |P1−P0|³.
    Gegen eine Linie soll κ = 0 sein, gegen einen Bogen κ = 1/R.
    """
    spline, other = (a, b) if entities[a][0] == sk.SPLINE else (b, a)
    v = values[starts[spline]:starts[spline] + len(entities[spline]) - 1]
    points, tips, backs = _handle_parts(entities[spline], v)    # Kurvenpunkte, Griff- und Gegengriffspitzen
    ends = _end_points(entities[spline], values, starts[spline])
    other_ends = _end_points(entities[other], values, starts[other])
    _gap, at_end, _j = min((float(np.linalg.norm(ends[i][0] - other_ends[j][0])), i, j) for i in (0, 1) for j in (0, 1))
    if at_end:
        p0, p1, p2 = points[-1], backs[-1], tips[-2]       # letzter Abschnitt, rückwärts gelesen
    else:
        p0, p1, p2 = points[0], tips[0], backs[1]
    lead, second = p1 - p0, p2 - p0
    length = max(float(np.linalg.norm(lead)), 1e-9)
    curvature = 2.0 / 3.0 * abs(float(lead[0] * second[1] - lead[1] * second[0])) / length ** 3
    wanted = 0.0 if entities[other][0] == "Linie" else 1.0 / max(float(values[starts[other] + 2]), 1e-9)
    return (curvature - wanted) * length

def _points(entity: list, values: np.ndarray, start: int) -> list[np.ndarray]:
    """Die Punkte eines Elements (Enden, Mittelpunkt) aus dem Zahlenvektor ab `start`."""
    v = values[start:]
    if entity[0] == "Linie":
        return [v[0:2], v[2:4]]
    if entity[0] == sk.SPLINE:
        return [v[i:i + 2] for i in range(0, len(entity) - 1, 2)]     # alle Punkte, durch die er läuft
    if entity[0] == sk.POINT:
        return [v[0:2]]
    if entity[0] in sk.SPATIAL:
        return []
    if entity[0] == sk.CONIC:
        return [v[0:2], v[2:4], v[4:6]]
    if entity[0] == sk.CP_SPLINE:
        return [v[i:i + 2] for i in range(0, len(entity) - 2, 2)]
    if entity[0] == sk.ELLIPSE:
        if len(entity) == 8:
            # Ellipsenbogen: Mittelpunkt und die beiden Enden (aus Halbachsen, Drehung und Laufwinkeln)
            turn = math.radians(v[4])
            cosine, sine = math.cos(turn), math.sin(turn)
            ends = []
            for at in (v[5], v[6]):
                u, w = v[2] * math.cos(math.radians(at)), v[3] * math.sin(math.radians(at))
                ends.append(v[0:2] + np.array([u * cosine - w * sine, u * sine + w * cosine]))
            return [v[0:2], *ends]
        return [v[0:2]]                                               # nur der Mittelpunkt
    centre = v[0:2]
    if entity[0] == "Kreis":
        return [centre]
    return [centre] + [centre + v[2] * np.array([math.cos(math.radians(v[i])), math.sin(math.radians(v[i]))])
                       for i in (3, 4)]


def _direction(values: np.ndarray, start: int) -> np.ndarray:
    return values[start + 2:start + 4] - values[start:start + 2]


def _size(entity: list, values: np.ndarray, start: int) -> float:
    """Länge einer Linie (auch einer 3D-Linie, dann räumlich) bzw. Radius eines Kreises oder Bogens."""
    if entity[0] == sk.LINE3D:
        return float(np.linalg.norm(values[start + 3:start + 6] - values[start:start + 3]))
    return float(np.linalg.norm(_direction(values, start))) if entity[0] == "Linie" else float(values[start + 2])


def _angle_between(first: np.ndarray, second: np.ndarray) -> float:
    """Winkel zwischen zwei Richtungen im Bogenmaß (0 … π), ohne Vorzeichen."""
    cross = first[0] * second[1] - first[1] * second[0]
    return math.atan2(abs(cross), float(first @ second))


def _to_line(values: np.ndarray, start: int, point: np.ndarray) -> float:
    """Senkrechter Abstand (mit Vorzeichen) des Punkts zur unbegrenzten Geraden der Linie."""
    direction = _direction(values, start)
    offset = point - values[start:start + 2]
    return float(direction[0] * offset[1] - direction[1] * offset[0]) / max(float(np.linalg.norm(direction)), 1e-9)


def _distance(entities: list, values: np.ndarray, starts, a: int, b: int) -> float:
    """Abstand zweier Elemente: Mittelpunkt zu Mittelpunkt, Mittelpunkt zu Gerade oder
    (bei zwei Linien) Mitte der zweiten Linie zur Geraden der ersten."""
    lines = [entities[i][0] == "Linie" for i in (a, b)]
    if not any(lines):
        return float(np.linalg.norm(values[starts[a]:starts[a] + 2] - values[starts[b]:starts[b] + 2]))
    if all(lines):
        middle = (values[starts[b]:starts[b] + 2] + values[starts[b] + 2:starts[b] + 4]) / 2
        return abs(_to_line(values, starts[a], middle))
    line, round_one = (a, b) if lines[0] else (b, a)
    return abs(_to_line(values, starts[line], values[starts[round_one]:starts[round_one] + 2]))


def _rim_distance(entities: list, values: np.ndarray, starts, a: int, b: int) -> float:
    """Abstand bis zum Rand: Mittenabstand (bzw. Mitte–Gerade) minus die Radien der beteiligten Kreise/Bögen."""
    rims = sum(float(values[starts[i] + 2]) for i in (a, b) if entities[i][0] in ("Kreis", "Bogen"))
    return _distance(entities, values, starts, a, b) - rims


def _system(entities: list, constraints: list):
    """Stellt das Gleichungssystem der Skizze auf: (Zahlenvektor, Startstellen, Fehlerfunktion).

    Der Zahlenvektor reiht die Werte aller Elemente aneinander; `starts` nennt je Element
    die Stelle seines ersten Werts. Die Fehlerfunktion ist 0, wenn alle Abhängigkeiten gelten.
    """
    starts, flat = [], []
    for entity in entities:
        starts.append(len(flat))
        flat += [float(value) for value in entity[1:]]
    initial = np.array(flat)

    # Punkte, die jetzt aufeinanderliegen, sollen verbunden bleiben
    located = [(number, slot) for number, entity in enumerate(entities)
               for slot in range(len(_points(entity, initial, starts[number])))]
    position = {key: _points(entities[key[0]], initial, starts[key[0]])[key[1]] for key in located}
    # Paare über ein Raster der Weite _COINCIDENT finden (jeden Punkt mit jedem zu vergleichen dauert bei großen
    # Skizzen zu lange); Reihenfolge wie beim vollständigen Vergleich: erst der früher genannte Punkt
    order = {key: number for number, key in enumerate(located)}
    cells: dict = {}
    for key in located:
        point = position[key]
        cells.setdefault((int(math.floor(point[0] / _COINCIDENT)), int(math.floor(point[1] / _COINCIDENT))), []).append(key)
    joined = set()
    for (cx, cy), members in cells.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for first in members:
                    for second in cells.get((cx + dx, cy + dy), ()):
                        if order[first] < order[second] and first[0] != second[0] \
                                and np.linalg.norm(position[first] - position[second]) < _COINCIDENT:
                            joined.add((first, second))
    joined = sorted(joined, key=lambda pair: (order[pair[0]], order[pair[1]]))
    # Punkte im Ursprung der Skizze bleiben dort: der Ursprung ist der feste Bezug (wie in Fusion)
    anchored = [key for key in located if np.linalg.norm(position[key]) < _COINCIDENT]

    # Berühren sich zwei Kreise, dann von außen (Abstand = r1 + r2) oder von innen (|r1 - r2|):
    # es gilt die Art, der die Skizze jetzt schon näher ist
    inside = {}
    for number, (kind, a, b, *_rest) in enumerate(constraints):
        if kind == TANGENT and "Linie" not in (entities[a][0], entities[b][0]) \
                and not (_has_end_tangent(entities[a]) or _has_end_tangent(entities[b])):
            apart = _distance(entities, initial, starts, a, b)
            radii = initial[starts[a] + 2], initial[starts[b] + 2]
            inside[number] = abs(apart - abs(radii[0] - radii[1])) < abs(apart - (radii[0] + radii[1]))

    # Krümmungsradius am Spline-Punkt: die Biegerichtung (links/rechts) bleibt, wie sie jetzt ist
    curvature_sign = {}
    for number, rule in enumerate(constraints):
        if rule[0] == POINT_CURVATURE:
            found = _point_curvatures(entities[rule[1]], initial, starts[rule[1]], rule[5])
            mean = sum(c for c, _length in found) / max(len(found), 1)
            curvature_sign[number] = 1.0 if mean >= 0 else -1.0
    # Punktabstand nur waagrecht/senkrecht: welche Seite oben bzw. rechts liegt, bleibt wie jetzt
    axis_sign = {}
    for number, rule in enumerate(constraints):
        if rule[0] == POINT_DISTANCE and len(rule) > 7 and rule[7] in POINT_AXES:
            gap = (_points(entities[rule[1]], initial, starts[rule[1]])[rule[5]]
                   - _points(entities[rule[2]], initial, starts[rule[2]])[rule[6]])
            axis_sign[number] = 1.0 if gap[POINT_AXES.index(rule[7])] >= 0 else -1.0

    def residuals(values: np.ndarray) -> np.ndarray:
        result = []
        for key in anchored:
            result += list(_points(entities[key[0]], values, starts[key[0]])[key[1]])
        for first, second in joined:
            result += list(_points(entities[first[0]], values, starts[first[0]])[first[1]]
                           - _points(entities[second[0]], values, starts[second[0]])[second[1]])
        for number, (kind, a, b, target, *_expression) in enumerate(constraints):
            start = starts[a]
            if kind in _LINKED:
                # verknüpfte Kopie: jeder Wert von b ergibt sich aus dem entsprechenden Wert von a
                other, roles = starts[b], _value_roles(entities[a])
                shift_x, shift_y = _expression[1:3]
                turn = math.radians(target) if kind == ROTATED else 0.0
                cosine, sine = math.cos(turn), math.sin(turn)
                for i, role in enumerate(roles):
                    if role == "x":
                        x, y = values[start + i], values[start + i + 1]
                        if kind == ROTATED:
                            dx, dy = x - shift_x, y - shift_y
                            want = (shift_x + dx * cosine - dy * sine, shift_y + dx * sine + dy * cosine)
                        else:
                            want = (x + shift_x, y + shift_y)
                        result += [values[other + i] - want[0], values[other + i + 1] - want[1]]
                    elif role == "a":
                        result.append(math.remainder(values[other + i] - values[start + i] - target * (kind == ROTATED), 360.0))
                    elif role == "s":
                        result.append(values[other + i] - values[start + i])
                continue
            if kind == COINCIDENT:
                first, second = _expression[1:3]
                result += list(_points(entities[a], values, start)[first] - _points(entities[b], values, starts[b])[second])
                continue
            if kind in (ALIGN_H, ALIGN_V):
                first, second = _expression[1:3]
                gap = _points(entities[a], values, start)[first] - _points(entities[b], values, starts[b])[second]
                result.append(float(gap[1] if kind == ALIGN_H else gap[0]))
                continue
            if kind == CURVATURE:
                result.append(_end_tangency(entities, values, starts, a, b))
                result.append(_end_curvature(entities, values, starts, a, b))
                continue
            if kind == POINT_CURVATURE:
                # auf jeder Seite des Punkts: Krümmung (mit Vorzeichen der jetzigen Biegung) gleich 1/Radius
                for curvature, length in _point_curvatures(entities[a], values, start, _expression[1]):
                    result.append((curvature - curvature_sign[number] / target) * length)
                continue
            if kind == MIDPOINT:
                middle = (values[starts[b]:starts[b] + 2] + values[starts[b] + 2:starts[b] + 4]) / 2
                result += list(_points(entities[a], values, start)[int(target)] - middle)
                continue
            if kind == COLLINEAR:
                # beide Enden von b liegen auf der Geraden durch a
                result.append(_to_line(values, start, values[starts[b]:starts[b] + 2]))
                result.append(_to_line(values, start, values[starts[b] + 2:starts[b] + 4]))
                continue
            if kind == CONCENTRIC and _expression[1:2] == [FREE_RADIUS]:
                result += list(values[starts[b]:starts[b] + 2] - values[start:start + 2])    # nur gleiche Mitte
                continue
            if kind == CONCENTRIC:
                result += list(values[starts[b]:starts[b] + 2] - values[start:start + 2])
                result.append(values[starts[b] + 2] - values[start + 2] - target)
                if entities[a][0] == sk.ELLIPSE:
                    # Versatz einer Ellipse: beide Halbachsen um denselben Betrag, Drehung und Bogenwinkel gleich
                    result.append(values[starts[b] + 3] - values[start + 3] - target)
                    result.append(math.remainder(values[starts[b] + 4] - values[start + 4], 360.0))
                    for i in range(5, len(entities[a]) - 1):
                        result.append(math.remainder(values[starts[b] + i] - values[start + i], 360.0))
                continue
            if kind == POINT_DISTANCE:
                first, second = _expression[1:3]
                gap = _points(entities[a], values, start)[first] - _points(entities[b], values, starts[b])[second]
                if number in axis_sign:
                    # nur in einer Richtung: mit dem Vorzeichen, das die Skizze jetzt hat (Seiten tauschen nicht)
                    result.append(axis_sign[number] * float(gap[POINT_AXES.index(_expression[3])]) - target)
                else:
                    result.append(float(np.linalg.norm(gap)) - target)
                continue
            if kind == SYMMETRY:
                # b ist das Spiegelbild von a an der Achse: Punkte gespiegelt gleich, Radien gleich
                axis = starts[int(target)]
                origin, along = values[axis:axis + 2], _direction(values, axis)
                unit = along / max(float(np.linalg.norm(along)), 1e-9)

                def reflect(point: np.ndarray) -> np.ndarray:
                    offset = point - origin
                    return origin + 2 * float(offset @ unit) * unit - offset

                other = starts[b]
                if entities[a][0] == sk.SPLINE:
                    # Spline: jeder Punkt (bei Griff-Splines auch jede Griffspitze) liegt gespiegelt gegenüber
                    for i in range(0, len(entities[a]) - 1, 2):
                        result += list(values[other + i:other + i + 2] - reflect(values[start + i:start + i + 2]))
                elif entities[a][0] == "Linie":
                    result += list(values[other:other + 2] - reflect(values[start:start + 2]))
                    result += list(values[other + 2:other + 4] - reflect(values[start + 2:start + 4]))
                elif entities[a][0] == sk.POINT:
                    result += list(values[other:other + 2] - reflect(values[start:start + 2]))
                else:
                    result += list(values[other:other + 2] - reflect(values[start:start + 2]))
                    result.append(values[other + 2] - values[start + 2])
                continue
            if kind == FIX:
                # alle Zahlen des Elements bleiben auf ihrem jetzigen Wert
                size = len(entities[a]) - 1
                result += list(values[start:start + size] - initial[start:start + size])
                continue
            if kind == DISTANCE:
                result.append(_distance(entities, values, starts, a, b) - target)
            elif kind == RIM_DISTANCE:
                result.append(_rim_distance(entities, values, starts, a, b) - target)
            elif kind == ANGLE:
                first, second = _direction(values, start), _direction(values, starts[b])
                # mit einer Länge malgenommen, damit der Fehler in mm gemessen wird
                result.append((_angle_between(first, second) - math.radians(target))
                              * float(np.linalg.norm(first)))
            elif kind == TANGENT and (_has_end_tangent(entities[a]) or _has_end_tangent(entities[b])):
                result.append(_end_tangency(entities, values, starts, a, b))
            elif kind == TANGENT:
                if number in inside:
                    radii = values[start + 2], values[starts[b] + 2]
                    wanted = abs(radii[0] - radii[1]) if inside[number] else radii[0] + radii[1]
                    result.append(_distance(entities, values, starts, a, b) - wanted)
                else:
                    line, round_one = (a, b) if entities[a][0] == "Linie" else (b, a)
                    result.append(_distance(entities, values, starts, a, b) - values[starts[round_one] + 2])
            elif kind == POINT_ON:
                point = _points(entities[a], values, start)[int(target)]
                if entities[b][0] == "Linie":
                    result.append(_to_line(values, starts[b], point))
                else:
                    centre = values[starts[b]:starts[b] + 2]
                    result.append(float(np.linalg.norm(point - centre)) - values[starts[b] + 2])
            elif kind in SEMI_AXES:
                result.append(values[start + 2 + SEMI_AXES.index(kind)] - target)
            elif kind == DIRECTION:
                # Die Linie zeigt in die Richtung des Winkels: quer dazu darf sie keinen Anteil haben
                first = _direction(values, start)
                aim = np.array([math.cos(math.radians(target)), math.sin(math.radians(target))])
                result.append(float(first[0] * aim[1] - first[1] * aim[0]))
            elif kind in (LENGTH, RADIUS):
                result.append(_size(entities[a], values, start) - target)
            elif kind == DIAMETER:
                result.append(2.0 * values[start + 2] - target)
            elif kind == HORIZONTAL:
                result.append(values[start + 3] - values[start + 1])
            elif kind == VERTICAL:
                result.append(values[start + 2] - values[start])
            elif kind == EQUAL:
                result.append(_size(entities[a], values, start) - _size(entities[b], values, starts[b]))
            else:
                # Parallel: Kreuzprodukt der Richtungen = 0; rechtwinklig: Skalarprodukt = 0.
                # Durch eine Länge geteilt, damit der Fehler wieder in mm gemessen wird.
                first, second = _direction(values, start), _direction(values, starts[b])
                product = (first[0] * second[1] - first[1] * second[0] if kind == PARALLEL
                           else float(first @ second))
                result.append(product / max(float(np.linalg.norm(first)), 1e-9))
        return np.array(result, dtype=float)

    return initial, starts, residuals


def free_entities(entities: list, constraints: list) -> set[int]:
    """Nummern der Elemente, die sich noch bewegen lassen (nicht voll bestimmt sind).

    Ein Element ist voll bestimmt, wenn Maße, Abhängigkeiten, verbundene Punkte und der
    feste Ursprung ihm keine Bewegung mehr lassen. Bei unbrauchbaren Eingaben gelten alle
    Elemente als beweglich.
    """
    everything = set(range(len(entities)))
    constraints = [rule for rule in constraints if not is_driven(rule)]     # getriebene Maße bestimmen nichts
    if any(sk.check_entity(entity) for entity in entities) or any(check(entities, c) for c in constraints):
        return everything
    if not entities:
        return set()
    values, starts, residuals = _system(entities, constraints)
    error = residuals(values)
    if not len(error):
        return everything
    jacobian = np.empty((len(error), len(values)))
    for column in range(len(values)):
        probe = values.copy()
        probe[column] += 1e-6
        jacobian[:, column] = (residuals(probe) - error) / 1e-6
    # Bewegungen, die keinen Fehler erzeugen, spannen den Kern der Ableitungsmatrix auf
    _u, sizes, rows = np.linalg.svd(jacobian)
    rank = int(np.sum(sizes > 1e-6 * max(1.0, sizes[0])))
    freedom = rows[rank:]
    if not len(freedom):
        return set()
    movable = np.max(np.abs(freedom), axis=0) > 1e-6
    return {number for number, entity in enumerate(entities)
            if movable[starts[number]:starts[number] + len(entity) - 1].any()}


def _rule_entities(rule: list) -> list[int]:
    """Die Elemente, die eine Abhängigkeit verbindet (bei „Symmetrisch“ auch die Spiegelachse im Wert)."""
    found = [index for index in rule[1:3] if isinstance(index, int) and index >= 0]
    if rule[0] == SYMMETRY and isinstance(rule[3], (int, float)) and int(rule[3]) >= 0:
        found.append(int(rule[3]))
    return found


def _groups(entities: list, constraints: list) -> list[list[int]]:
    """Unabhängige Teile der Skizze: Elemente, die über Abhängigkeiten oder aufeinanderliegende Punkte
    zusammenhängen. Jeder Teil lässt sich für sich lösen – keine Gleichung reicht über seine Grenze."""
    parent = list(range(len(entities)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(a: int, b: int) -> None:
        parent[root(a)] = root(b)

    for rule in constraints:
        linked = _rule_entities(rule)
        for other in linked[1:]:
            join(linked[0], other)
    # aufeinanderliegende Punkte über ein Raster der Weite _COINCIDENT finden (statt jeden mit jedem zu vergleichen)
    flat = [float(value) for entity in entities for value in entity[1:]]
    values, start, cells = np.array(flat), 0, {}
    for number, entity in enumerate(entities):
        for point in _points(entity, values, start):
            key = (int(math.floor(point[0] / _COINCIDENT)), int(math.floor(point[1] / _COINCIDENT)))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other, other_point in cells.get((key[0] + dx, key[1] + dy), ()):
                        if other != number and np.linalg.norm(point - other_point) < _COINCIDENT:
                            join(number, other)
            cells.setdefault(key, []).append((number, point))
        start += len(entity) - 1
    members: dict[int, list[int]] = {}
    for index in range(len(entities)):
        members.setdefault(root(index), []).append(index)
    return list(members.values())


def solve(entities: list, constraints: list) -> list | None:
    """Erfüllt alle Abhängigkeiten und gibt die neuen Elemente zurück.

    None bedeutet: Die Abhängigkeiten passen nicht zu den Elementen oder widersprechen sich.
    Die übergebenen Listen bleiben unverändert. Getriebene Maße bleiben außen vor (siehe `update_driven`).

    Große Skizzen zerfallen meist in unabhängige Teile (z. B. viele einzelne Rechtecke): jeder Teil wird für sich
    gelöst, Teile ohne Abhängigkeit bleiben unverändert. Das ist bei 400 Linien rund 40-mal schneller.
    """
    constraints = [rule for rule in constraints if not is_driven(rule)]
    if any(sk.check_entity(entity) for entity in entities) or any(check(entities, c) for c in constraints):
        return None
    if not constraints:
        return [list(entity) for entity in entities]
    groups = _groups(entities, constraints)
    if len(groups) == 1:
        return _solve_group(entities, constraints)
    solved = [list(entity) for entity in entities]
    for members in groups:
        renumber = {old: new for new, old in enumerate(members)}
        rules = []
        for rule in constraints:
            linked = _rule_entities(rule)
            if not linked or linked[0] not in renumber:
                continue
            rule = list(rule)
            for position in (1, 2):
                if isinstance(rule[position], int) and rule[position] >= 0:
                    rule[position] = renumber[rule[position]]
            if rule[0] == SYMMETRY and int(rule[3]) >= 0:
                rule[3] = float(renumber[int(rule[3])])
            rules.append(rule)
        if not rules:
            continue                                # keine Abhängigkeit: der Teil bleibt, wie er ist
        result = _solve_group([entities[index] for index in members], rules)
        if result is None:
            return None
        for index, entity in zip(members, result):
            solved[index] = entity
    return solved


def _solve_group(entities: list, constraints: list) -> list | None:
    """Löst einen zusammenhängenden Teil der Skizze (Newton-Verfahren mit kleinstem Schritt, siehe `solve`)."""
    initial, starts, residuals = _system(entities, constraints)
    values = initial.copy()
    error = residuals(values)
    cost = np.ones(len(values))
    for entity, start in zip(entities, starts):
        if entity[0] in ("Kreis", "Bogen"):
            cost[start + 2] = _RADIUS_EASE
        if entity[0] in sk.SPATIAL:
            cost[start + 2:start + len(entity) - 1:3] = _HEIGHT_EASE
    # Aufbau der Ableitungen: kleine Skizzen Spalte für Spalte, große mit Färbung (siehe unten)
    widths = [len(entity) - 1 for entity in entities]
    widest = max(widths, default=0)
    colouring = rows_of = None
    if len(values) > _COLOURING_FROM:
        # welche Gleichungen hängen von welchem Element ab? (einmal je Lösung; ungleiche Probeschritte,
        # damit sich keine Wirkung zufällig aufhebt)
        rows_of = []
        for number, width in enumerate(widths):
            probe = values.copy()
            probe[starts[number]:starts[number] + width] += 1e-3 * (1.0 + 0.37 * np.arange(width))
            rows_of.append(np.nonzero(np.abs(residuals(probe) - error) > 1e-12)[0])
        # gierige Färbung: Elemente derselben Farbe teilen keine Gleichung
        colouring, taken = [], []
        for number in range(len(entities)):
            rows = set(rows_of[number].tolist())
            for colour, busy in zip(colouring, taken):
                if not rows & busy:
                    colour.append(number)
                    busy |= rows
                    break
            else:
                colouring.append([number])
                taken.append(rows)
    for _ in range(_ITERATIONS):
        if np.max(np.abs(error)) < _TOLERANCE:
            break
        # Ableitungen durch kleine Probeschritte
        if colouring is None:
            jacobian = np.empty((len(error), len(values)))
            for column in range(len(values)):
                probe = values.copy()
                probe[column] += 1e-6
                jacobian[:, column] = (residuals(probe) - error) / 1e-6
        else:
            # große Skizze: Elemente ohne gemeinsame Gleichung gleichzeitig anstoßen (eine Auswertung je Farbe und
            # Wertstelle statt je Wert)
            jacobian = np.zeros((len(error), len(values)))
            for colour in colouring:
                for slot in range(widest):
                    probe = values.copy()
                    used = [number for number in colour if slot < widths[number]]
                    if not used:
                        continue
                    for number in used:
                        probe[starts[number] + slot] += 1e-6
                    change = (residuals(probe) - error) / 1e-6
                    for number in used:
                        rows = rows_of[number]
                        jacobian[rows, starts[number] + slot] = change[rows]
        # Kleinster Schritt, der den Fehler ausgleicht: so bewegt sich die Skizze möglichst wenig.
        # Radien sind dabei „teurer“ als Lagen: ein Kreis rückt eher an eine Linie, als dass er wächst.
        step = cost * np.linalg.lstsq(jacobian * cost, -error, rcond=1e-9)[0]
        scale = 1.0
        while scale > 1e-4:
            candidate = values + step * scale
            candidate_error = residuals(candidate)
            if np.linalg.norm(candidate_error) < np.linalg.norm(error):
                break
            scale /= 2.0
        else:
            return None     # kein Fortschritt mehr: die Abhängigkeiten widersprechen sich
        values, error = candidate, candidate_error
    if np.max(np.abs(error)) >= 1e-5:
        return None
    solved = [[entity[0]] + [float(value) for value in values[start:start + len(entity) - 1]]
              for entity, start in zip(entities, starts)]
    return None if any(sk.check_entity(entity) for entity in solved) else solved
