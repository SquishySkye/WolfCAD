# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Zeichenlogik für freie Skizzen: Linien, Rechtecke, Kreise und Bögen mit Fang und Maßtabelle.

Gezeichnet wird in der 3D-Ansicht (siehe sketch3d.py); `SketchCanvas` hält dafür den Zustand
des Zeichenwerkzeugs und kann die Skizze auch als eigene 2D-Fläche darstellen.

Bedienung einer Zeichenfläche:
  Linksklick   Punkt setzen (je nach Werkzeug Anfang, Ecke, Mittelpunkt, Radius …)
  Rechtsklick  laufendes Element beenden (bei Linien: Linienzug abschließen), auch mit Esc
  Mausrad      zoomen, mittlere Maustaste ziehen = verschieben
  Entf         gewähltes Element löschen (Werkzeug "Auswählen")
Punkte fangen auf vorhandene Endpunkte und Mittelpunkte, sonst auf das Raster.
Genaue Maße lassen sich in der Tabelle rechts eintragen.
"""

from __future__ import annotations

import math

import numpy as np

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QHeaderView,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import constraints as cs
from ..core import features as ft
from ..core import sketch as sk
from .icons import icon

DIMENSION_TOOL = "Bemaßung"
FILLET_TOOL, TRIM_TOOL, CONSTRUCTION_TOOL = "Abrunden", "Stutzen", "Konstruktion"
MIRROR_TOOL, OFFSET_TOOL, PROJECT_TOOL = "Spiegeln", "Versatz", "Projizieren"
PATTERN_TOOL, CIRCULAR_TOOL = "Muster", "Kreismuster"
EXTEND_TOOL, BREAK_TOOL = "Verlängern", "Brechen"
CHAMFER_TOOL, SCALE_TOOL, BLEND_TOOL = "Fase", "Skizze skalieren", "Übergangskurve"
MOVE_TOOL = "Verschieben/Kopieren"
# verändern oder vervielfältigen vorhandene Elemente per Klick
EDIT_TOOLS = (FILLET_TOOL, CHAMFER_TOOL, TRIM_TOOL, EXTEND_TOOL, BREAK_TOOL, CONSTRUCTION_TOOL, MIRROR_TOOL,
              OFFSET_TOOL, PROJECT_TOOL, PATTERN_TOOL, CIRCULAR_TOOL, SCALE_TOOL, BLEND_TOOL, MOVE_TOOL)
TEXT_TOOL = "Text"
# weitere Zeichenwerkzeuge wie in Fusion
CENTER_RECT, THREE_POINT_RECT = "Mittelpunkt-Rechteck", "3-Punkt-Rechteck"
THREE_POINT_CIRCLE, TWO_POINT_CIRCLE = "Kreis 3 Punkte", "Kreis 2 Punkte"
TWO_TANGENT_CIRCLE, THREE_TANGENT_CIRCLE = "Kreis 2 Tangenten", "Kreis 3 Tangenten"
THREE_POINT_ARC, TANGENT_ARC = "3-Punkt-Bogen", "Tangentialbogen"
POINT_TOOL = "Punkt"
# Linientypen der Skizzenpalette (wie Fusion)
LINE_NORMAL, LINE_CONSTRUCTION, LINE_CENTER = "Normal", "Konstruktion", "Mittellinie"
TOOLS = ("Auswählen", POINT_TOOL, "Linie", "Rechteck", CENTER_RECT, THREE_POINT_RECT, "Kreis", THREE_POINT_CIRCLE,
         TWO_POINT_CIRCLE, TWO_TANGENT_CIRCLE, THREE_TANGENT_CIRCLE, "Bogen", THREE_POINT_ARC, TANGENT_ARC, "Polygon", "Langloch", sk.SPLINE, sk.CP_SPLINE, sk.ELLIPSE, sk.CONIC, TEXT_TOOL,
         *EDIT_TOOLS,
         DIMENSION_TOOL, cs.DISTANCE, cs.ANGLE,
         cs.HORIZONTAL, cs.VERTICAL, cs.PARALLEL, cs.PERPENDICULAR, cs.EQUAL, cs.TANGENT, cs.POINT_ON,
         cs.COINCIDENT, cs.MIDPOINT, cs.COLLINEAR, cs.CONCENTRIC, cs.SYMMETRY, cs.CURVATURE, cs.FIX)
# Werkzeuge, die vorhandene Elemente anklicken statt neue Punkte zu setzen
PICK_TOOLS = ("Auswählen", DIMENSION_TOOL) + cs.SINGLES + cs.PAIRS + EDIT_TOOLS
_TOOL_ICONS = {"Auswählen": "select", "Linie": "line", "Rechteck": "rectangle", "Kreis": "circle", "Bogen": "arc",
               DIMENSION_TOOL: "dimension", cs.HORIZONTAL: "horizontal", cs.VERTICAL: "vertical",
               cs.PARALLEL: "parallel", cs.PERPENDICULAR: "perpendicular", cs.EQUAL: "equal",
               cs.DISTANCE: "distance", cs.ANGLE: "angle", cs.TANGENT: "tangent", cs.POINT_ON: "point_on",
               "Polygon": "polygon", "Langloch": "slot", FILLET_TOOL: "sketch_fillet", TRIM_TOOL: "trim",
               CONSTRUCTION_TOOL: "construction", MIRROR_TOOL: "sketch_mirror", OFFSET_TOOL: "offset",
               PROJECT_TOOL: "project", sk.SPLINE: "spline", cs.FIX: "fix", sk.ELLIPSE: "ellipse", sk.CONIC: "conic", sk.CP_SPLINE: "cp_spline",
               PATTERN_TOOL: "sketch_pattern", CIRCULAR_TOOL: "sketch_pattern_circular", TEXT_TOOL: "text",
               CENTER_RECT: "rect_center", THREE_POINT_RECT: "rect_3point", THREE_POINT_CIRCLE: "circle_3point",
               TWO_POINT_CIRCLE: "circle_2point", THREE_POINT_ARC: "arc_3point", TANGENT_ARC: "arc_tangent",
               EXTEND_TOOL: "extend", BREAK_TOOL: "break", POINT_TOOL: "point", CHAMFER_TOOL: "sketch_chamfer",
               SCALE_TOOL: "sketch_scale", BLEND_TOOL: "blend_curve", MOVE_TOOL: "sketch_move", cs.COINCIDENT: "coincident",
               TWO_TANGENT_CIRCLE: "circle_2tangent", THREE_TANGENT_CIRCLE: "circle_3tangent",
               cs.CURVATURE: "curvature", cs.MIDPOINT: "midpoint", cs.COLLINEAR: "collinear", cs.CONCENTRIC: "concentric", cs.SYMMETRY: "symmetry"}
_SNAP_PIXELS = 9        # Abstand, in dem ein vorhandener Punkt oder ein Rasterpunkt gefangen wird
_CENTRE_SNAP_PIXELS = 22    # Mitten von Kreisen, Bögen und Körperkreisen fangen weiter (wie ein Magnet, Fusion)
_ALIGN_PIXELS = 14          # Ausrichtung an gemerkten Punkten fängt großzügiger als ein Punkt (wie Fusion)
_GRID_MIN_PIXELS = 40   # so weit liegen die Rasterlinien auf dem Bildschirm mindestens auseinander
_ANGLE_SNAP_DEGREES = 4.0   # so nah (in Grad) rastet eine Linie auf 0°/90°/180° und auf rechtwinklig/gerade weiter
_GRID_SNAP_PIXELS = 7   # so nah muss die Maus einem Rasterpunkt kommen, damit er fängt (der Rest bleibt frei)
# Rasterweiten in mm, zwischen denen das Raster beim Zoomen wechselt (feiner als 0,1 mm wird es nicht)
GRID_STEPS = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0)
_PICK_PIXELS = 7        # Abstand, in dem ein Klick ein Element auswählt
_COLORS = {
    "background": QColor("#22252b"), "grid": QColor("#2e323a"), "axis": QColor("#4a5160"),
    "entity": QColor("#5aa9ff"), "selected": QColor("#f0c040"), "preview": QColor("#9fd0ff"),
    "snap": QColor("#f0c040"), "text": QColor("#c8ccd4"), "reference": QColor("#8a93a5"),
}


class SketchCanvas(QWidget):
    """Zeichenfläche: zeigt die Elemente und nimmt Mausklicks für das aktive Werkzeug entgegen."""

    changed = Signal()      # die Elementliste oder die Auswahl hat sich geändert
    # Nummer des Maßes, dessen Wert jetzt eingegeben werden soll; True, wenn es gerade erst entstand
    dimension_picked = Signal(int, bool)
    text_requested = Signal(float, float)       # an dieser Stelle soll ein Text entstehen

    def __init__(self, entities: list, parent=None, reference: list | None = None,
                 constraints: list | None = None, construction: list | None = None,
                 texts: list | None = None, centerlines: list | None = None, projected: list | None = None):
        super().__init__(parent)
        self.entities = entities
        # projizierte Elemente (lila, fest verankert, zählen zum Profil) – siehe Sketch.projected
        self.projected = projected if projected is not None else []
        # Mittellinien (zählen zum Profil, werden strichpunktiert gezeigt) – siehe Sketch.centerlines
        self.centerlines = centerlines if centerlines is not None else []
        # Skizzenpalette wie in Fusion: was gezeigt wird und wie gefangen wird
        self.palette = {"profile": True, "points": True, "constraints": True, "construction": True,
                        "projected": True, "grid": True, "snap": True, "3d": False}
        # Linientyp neuer Elemente (Fusion: Konstruktion bzw. Mittellinie schon beim Zeichnen)
        self.line_mode = LINE_NORMAL
        # Texte der Skizze: je Eintrag [Text, x, y, Schrifthöhe, Drehung]
        self.texts = texts if texts is not None else []
        self.text_height = 10.0             # Schrifthöhe für neue Texte in mm
        # Nummern der Konstruktionselemente (Hilfslinien, die kein Profil bilden)
        self.construction = construction if construction is not None else []
        self.polygon_sides = 6              # Eckenzahl des Werkzeugs „Polygon“
        self.polygon_kind = sk.POLYGON_KINDS[0]     # Art wie in Fusion: eingeschrieben, umschrieben, Kante
        self.slot_kind = sk.SLOT_KINDS[0]           # Art des Langlochs (Fusion: fünf Arten)
        self._tangent_lines: list[int] = []         # bei den Tangentenkreisen schon angeklickte Linien
        self.fillet_radius = 2.0            # Radius des Werkzeugs „Abrunden“ in mm
        # Werkzeug „Fase“ wie in Fusion: Art, Abstand(e) und Winkel
        self.chamfer_kind = sk.CHAMFER_KINDS[0]
        self.chamfer_distance, self.chamfer_distance2, self.chamfer_angle = 2.0, 3.0, 45.0
        self.scale_factor = 2.0             # Werkzeug „Skizze skalieren“: Faktor um den angeklickten Punkt
        self._blend_first: tuple | None = None      # Übergangskurve: erstes angeklicktes Kurvenende
        self.blend_g2 = False               # Übergangskurve mit stetiger Krümmung (G2) statt nur tangential (G1)
        self.dimension_rim = False          # Bemaßung zu Kreisen/Bögen bis zum Rand (Fusion: Tangente) statt zur Mitte
        self.move_copy = False              # Verschieben/Kopieren: Kopie anlegen statt verschieben
        self._move_from_point: tuple | None = None  # Verschieben/Kopieren: angeklickter Basispunkt
        self.offset_distance = 2.0          # Abstand des Werkzeugs „Versatz“ in mm
        self.conic_rho = 0.5                # Form neuer Kegelschnitte (0,5 = Parabel)
        self.cp_degree = 3                  # Grad neuer Steuerpunkt-Splines (3 oder 5)
        # Werkzeug „Muster“: Anzahl und Abstand in x und y; „Kreismuster“: Anzahl und Mittelpunkt
        self.pattern = {"nx": 3, "dx": 20.0, "ny": 1, "dy": 20.0, "count": 6, "cx": 0.0, "cy": 0.0}
        self._mirror_axis: int | None = None    # Linie, an der „Spiegeln“ gerade spiegelt
        # Maße und Abhängigkeiten (siehe core/constraints.py); die Elemente folgen ihnen
        self.constraints = constraints if constraints is not None else []
        self.message = ""                   # letzte Meldung zu den Abhängigkeiten (für die Statuszeile)
        self._first_pick: int | None = None     # erstes Element bei Abhängigkeiten zwischen zweien
        self._first_slot = 0                    # dessen angeklickter Punkt (für „Punkt auf Kurve“)
        # Laufendes Ziehen mit der Maus: gegriffene Punkte als (Element, Punkt) oder ein Kreisradius
        self._drag_points: list[tuple[int, int]] = []
        self._drag_radius: int | None = None
        # Mitten von Körperkreisen (Zylinder, Bohrungen, Rundungen) in Skizzenkoordinaten: [(x, y, Radius), …];
        # das Hauptfenster trägt sie beim Öffnen der Skizze ein
        self.body_centers: list[tuple[float, float, float]] = []
        # 3D-Skizze: Höhe (längs der Normale) je gesetztem Punkt des laufenden Splines; fehlende Einträge sind 0
        self.point_heights: list[float] = []
        # gegriffener Krümmungsgriff: (Nummer der Abhängigkeit, Kurvenpunkt, Richtung zur Schmiegekreismitte)
        self._drag_curvature: tuple | None = None
        # Zeichenhilfe: Linienzüge (z. B. der Umriss der Körperfläche), nur zum Anzeigen und Fangen
        self.reference = reference or []
        self.tool = "Linie"
        self.grid = 5.0                     # feste Rasterweite in mm (0 = kein Raster), wenn nicht automatisch
        self.grid_auto = True               # Rasterweite folgt dem Zoom (wie in Fusion)
        self._inferred = ""                 # erkannte Ausrichtung der laufenden Linie (waagrecht, senkrecht, …)
        self._inferred_with = -1            # … und bei „rechtwinklig“/„parallel“ die Linie, auf die sie sich bezieht
        self._hover_line = -1               # Linie, über die die Maus beim Zeichnen zuletzt fuhr (Fang wie in Fusion)
        # Ausrichtungshilfe wie in Fusion: Punkte, über die die Maus gefahren ist („gemerkt“), und die gestrichelten
        # Linien zu ihnen, wenn die Maus waagrecht/senkrecht zu einem davon steht
        self._acquired: list[tuple[float, float]] = []
        self._alignment: list[tuple[tuple[float, float], tuple[float, float]]] = []
        self._foot_on = -1                  # das Linienende sitzt im Lotfußpunkt auf dieser Linie
        # Beim Zeichnen eingetippte Maße des laufenden Rechtecks: [(Breite, Ausdruck)]
        # Maßfelder beim Zeichnen wie Fusion: festgehaltene Werte (Name → (Wert, Ausdruck)) und das aktive Feld
        self._hud_locked: dict[str, tuple[float, str]] = {}
        self._hud_active: str | None = None
        # beim Tippen sofort geltender Wert: Name des Felds und was dort vorher galt (für Esc)
        self._hud_typing: tuple[str, tuple | None] | None = None
        self._raw_cursor: tuple[float, float] | None = None     # Mausstelle vor dem Festhalten der Maße
        self.selected: int | None = None
        # Mehrfachauswahl (Rahmen oder Strg+Klick); `selected` ist das zuletzt gewählte Element daraus
        self.selection: set[int] = set()
        self.additive = False               # der laufende Klick erweitert die Auswahl (Strg/Umschalt)
        self._box: list | None = None       # Auswahlrahmen: [Startpunkt, aktueller Punkt]
        self._move_from: tuple[float, float] | None = None      # Griffstelle beim Verschieben ganzer Elemente
        self._move_base: list = []          # Elemente beim Beginn des Verschiebens
        self._move_index = -1               # das dabei angefasste Element
        self.selected_text: int | None = None   # zuletzt angeklickter Text
        self._drag_text: tuple | None = None    # gegriffener Text: (Nummer, Griffstelle, Lage beim Anfassen)
        self._points: list[tuple[float, float]] = []   # bereits gesetzte Punkte des laufenden Elements
        self._chain_line: int | None = None             # zuletzt fertiges Stück des laufenden Linienzugs (Winkelbezug)
        # Fangweiten sind in Bildpunkten angegeben; auf dichten Bildschirmen (4K ohne Skalierung) wären sie
        # winzig – die Skizze setzt hier das Verhältnis der Pixeldichte zu 96 dpi (siehe _px)
        self.pixel_factor = 1.0
        self._cursor = (0.0, 0.0)           # gefangene Mausposition in mm
        self._snapped_to_point = False
        self._scale = 6.0                   # Bildpunkte je Millimeter
        self._origin = QPointF(0, 0)        # Fensterposition des Skizzenursprungs (in resizeEvent gesetzt)
        self._centered = False              # True, sobald der Benutzer die Ansicht selbst verändert hat
        self._pan_from: QPointF | None = None
        self.setMinimumSize(520, 420)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # --- Umrechnung und Fang ----------------------------------------------

    def to_screen(self, x: float, y: float) -> QPointF:
        return QPointF(self._origin.x() + x * self._scale, self._origin.y() - y * self._scale)

    def to_sketch(self, position: QPointF) -> tuple[float, float]:
        return ((position.x() - self._origin.x()) / self._scale,
                (self._origin.y() - position.y()) / self._scale)

    def snap_points(self) -> list[tuple[float, float]]:
        """Punkte, auf die gefangen wird: Linienenden, Mittelpunkte, Bogenenden, Ursprung."""
        points = [(0.0, 0.0)]
        for entity in self.entities:
            kind, v = entity[0], entity[1:]
            if kind == sk.POINT:
                points.append((v[0], v[1]))
            elif kind == "Linie":
                # Enden und Mitte der Linie
                points += [(v[0], v[1]), (v[2], v[3]), ((v[0] + v[2]) / 2, (v[1] + v[3]) / 2)]
            elif kind == sk.SPLINE:
                points += [(v[i], v[i + 1]) for i in range(0, len(v), 2)]
            elif kind == sk.CONIC:
                points += [(v[0], v[1]), (v[2], v[3])]      # die Enden (der Scheitel liegt nicht auf der Kurve)
            elif kind == sk.CP_SPLINE:
                points += sk.control_points(entity)         # Steuerpunkte (die Enden liegen auf der Kurve)
            elif kind == sk.ELLIPSE:
                if sk.is_ellipse_arc(entity):
                    # Ellipsenbogen: Mittelpunkt und die beiden Enden
                    points += [(v[0], v[1]), sk.ellipse_point(entity, v[5]), sk.ellipse_point(entity, v[6])]
                else:
                    # Mittelpunkt und die vier Scheitel
                    points += [(v[0], v[1]), *sk.ellipse_points(entity, 4)[:4]]
            else:
                points.append((v[0], v[1]))
                if kind == "Bogen":
                    points += [(v[0] + v[2] * math.cos(math.radians(angle)),
                                v[1] + v[2] * math.sin(math.radians(angle))) for angle in v[3:5]]
        # Auch die Ecken der Zeichenhilfe und die Mitten von Körperkreisen sind Fangpunkte
        for polyline in self.reference:
            points += [polyline[0], polyline[-1]]
        points += [(x, y) for x, y, _radius in self.body_centers]
        return points + self._points

    def centre_points(self) -> list[tuple[float, float]]:
        """Mitten von Kreisen, Bögen, Ellipsen der Skizze und von Körperkreisen: sie fangen mit größerer Reichweite."""
        found = [(entity[1], entity[2]) for entity in self.entities if entity[0] in ("Kreis", "Bogen", sk.ELLIPSE)]
        return found + [(x, y) for x, y, _radius in self.body_centers]

    def grid_step(self) -> float:
        """Die gerade gültige Rasterweite in mm (0 = kein Raster).

        Automatisch ist es die feinste Stufe, deren Linien auf dem Bildschirm noch gut
        auseinanderliegen – beim Hineinzoomen wird das Raster feiner, bis 0,1 mm.
        """
        if not self.grid_auto:
            return self.grid
        return next((step for step in GRID_STEPS if step * self._scale >= _GRID_MIN_PIXELS), GRID_STEPS[-1])

    def snap(self, x: float, y: float) -> tuple[tuple[float, float], bool]:
        """Fängt (x, y) auf einen vorhandenen Punkt oder einen nahen Rasterpunkt; meldet, ob etwas fing.

        Wie in Fusion wird nur gefangen, wenn die Maus nahe genug ist – sonst bleibt die
        Stelle frei wählbar.
        """
        if self._inference_off():
            return (x, y), False            # Strg gedrückt: kein Fang, keine Folgerung (wie Fusion)
        reach = self._px(_SNAP_PIXELS)
        self._alignment = []
        self._fixed_snap = False            # Schnitt aus Ausrichtung und Kurve: ein fester Fangpunkt (siehe aim)
        self._curve_hit = None              # Nummer der Kurve, auf der der Fang liegt (für Richtung + Kurve in aim)
        nearest = min(self.snap_points(), key=lambda point: math.dist(point, (x, y)))
        if math.dist(nearest, (x, y)) <= reach:
            self._acquire(nearest)
            return nearest, True
        # Mitten von Kreisen und Rundungen fangen weiter: genau die Mitte trifft man so leicht (wie Fusion)
        centres = self.centre_points()
        if centres:
            centre = min(centres, key=lambda point: math.dist(point, (x, y)))
            if math.dist(centre, (x, y)) <= self._px(_CENTRE_SNAP_PIXELS):
                return centre, True
        # Wie in Fusion fängt auch die Kurve selbst: der Punkt landet genau auf der Linie (bzw. dem Kreis, Bogen …)
        # und wird beim Setzen mit ihr verbunden (siehe _connect_new)
        on_curve = self._on_curve(x, y, reach)
        if on_curve is not None:
            # liegt die Maus zugleich waagrecht/senkrecht zu einem gemerkten Punkt, rastet sie im Schnitt der
            # gestrichelten Ausrichtung mit der Kurve ein (wie Fusion) – und bleibt damit auf der Kurve
            crossing = self._aligned_on_curve(self.entities[on_curve[1]], x, y, self._px(_ALIGN_PIXELS))
            if crossing is not None:
                self._fixed_snap = True
                return crossing, True
            self._curve_hit = on_curve[1]
            return on_curve[0], True
        aligned = self._aligned(x, y, self._px(_ALIGN_PIXELS))
        if aligned is not None:
            return aligned, True
        step = self.grid_step() if self.palette["snap"] else 0.0       # Palette „Fang“: Raster fangen an/aus
        if step > 0:
            node = (round(x / step) * step, round(y / step) * step)
            if math.dist(node, (x, y)) <= self._px(_GRID_SNAP_PIXELS):
                return node, True
        return (x, y), False

    def _acquire(self, point: tuple[float, float]) -> None:
        """Merkt sich einen Punkt, über den die Maus gefahren ist (höchstens die letzten drei, wie Fusion)."""
        point = (float(point[0]), float(point[1]))
        self._acquired = [known for known in self._acquired if math.dist(known, point) > 1e-9][-2:] + [point]

    def _aligned(self, x: float, y: float, reach: float) -> tuple[float, float] | None:
        """Steht die Maus waagrecht oder senkrecht zu einem gemerkten Punkt, rastet sie dort ein; bei zwei gemerkten
        Punkten auch im Kreuzungspunkt. Die gestrichelten Linien dazu stehen danach in `self._alignment`."""
        x_from = min(((abs(point[0] - x), point) for point in self._acquired), default=(math.inf, None))
        y_from = min(((abs(point[1] - y), point) for point in self._acquired), default=(math.inf, None))
        snapped_x = x_from[1][0] if x_from[0] <= reach else None
        snapped_y = y_from[1][1] if y_from[0] <= reach else None
        if snapped_x is None and snapped_y is None:
            return None
        position = (snapped_x if snapped_x is not None else x, snapped_y if snapped_y is not None else y)
        if snapped_x is not None and math.dist(x_from[1], position) > 1e-9:
            self._alignment.append((x_from[1], position))        # senkrecht über/unter dem gemerkten Punkt
        if snapped_y is not None and math.dist(y_from[1], position) > 1e-9:
            self._alignment.append((y_from[1], position))        # waagrecht neben ihm
        return position if self._alignment else None

    def _direction_meets_alignment(self, first, direction, cursor) -> tuple[float, float] | None:
        """Schnittpunkt der eingerasteten Richtung (vom Linienanfang aus) mit einer Ausrichtungslinie zu einem
        gemerkten Punkt (waagrecht oder senkrecht durch ihn); der nächste zur Maus. None, wenn sie sich nicht vor
        dem Linienanfang schneiden. Die gestrichelte Linie zeigt danach auf den Schnittpunkt."""
        found = []
        for start, end in self._alignment:
            if abs(end[0] - start[0]) < 1e-9 and abs(direction[0]) > 1e-9:       # senkrechte Linie x = start.x
                t = (start[0] - first[0]) / direction[0]
            elif abs(end[1] - start[1]) < 1e-9 and abs(direction[1]) > 1e-9:     # waagrechte Linie y = start.y
                t = (start[1] - first[1]) / direction[1]
            else:
                continue
            if t > 1e-9:
                point = (first[0] + direction[0] * t, first[1] + direction[1] * t)
                found.append((math.dist(point, cursor), point, start))
        if not found:
            return None
        _distance, point, start = min(found)
        self._alignment = [(start, point)]
        return point

    def _aligned_on_curve(self, entity: list, x: float, y: float, reach: float) -> tuple[float, float] | None:
        """Schnitt einer Ausrichtung (waagrecht/senkrecht durch einen gemerkten Punkt) mit der Kurve `entity`, nahe
        der Maus; None, wenn keiner in Reichweite liegt. Die gestrichelte Linie zeigt dann darauf."""
        found = []
        for anchor in self._acquired:
            for axis in (0, 1):                 # 0: senkrechte Linie x = anchor.x, 1: waagrechte y = anchor.y
                if abs((x, y)[axis] - anchor[axis]) > reach:
                    continue
                for point in _crossings(entity, axis, anchor[axis]):
                    distance = math.dist(point, (x, y))
                    if distance <= reach and math.dist(point, anchor) > 1e-9:
                        found.append((distance, point, anchor))
        if not found:
            return None
        _distance, point, anchor = min(found)
        self._alignment = [(anchor, point)]
        return point

    def _direction_meets_curve(self, first, angle: float, cursor, reach: float) -> tuple[float, float] | None:
        """Schnitt des Strahls vom Linienanfang in Richtung `angle` (Grad) mit der Kurve, auf der der Fang liegt;
        der nächste zur Maus, sofern in Fangweite. None, wenn es keinen gibt."""
        if self._curve_hit is None or self._curve_hit >= len(self.entities):
            return None
        entity = self.entities[self._curve_hit]
        ux, uy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        found = []
        if entity[0] == "Linie":
            (ax, ay), (bx, by) = entity[1:3], entity[3:5]
            dx, dy = bx - ax, by - ay
            denominator = ux * dy - uy * dx
            if abs(denominator) > 1e-12:
                t = ((ax - first[0]) * dy - (ay - first[1]) * dx) / denominator
                s_along = ((ax - first[0]) * uy - (ay - first[1]) * ux) / denominator
                if t > 1e-9 and -1e-9 <= s_along <= 1 + 1e-9:
                    found.append((first[0] + ux * t, first[1] + uy * t))
        elif entity[0] in ("Kreis", "Bogen"):
            (cx, cy), radius = entity[1:3], entity[3]
            fx, fy = first[0] - cx, first[1] - cy
            b = fx * ux + fy * uy
            c = fx * fx + fy * fy - radius * radius
            disc = b * b - c
            if disc >= 0:
                for t in (-b - math.sqrt(disc), -b + math.sqrt(disc)):
                    if t > 1e-9:
                        point = (first[0] + ux * t, first[1] + uy * t)
                        if entity[0] == "Bogen":
                            seen = math.degrees(math.atan2(point[1] - cy, point[0] - cx)) % 360
                            span = (entity[5] - entity[4]) % 360 or 360
                            if (seen - entity[4]) % 360 > span + 1e-7:
                                continue
                        found.append(point)
        # erlaubt ist die Fangweite plus der seitliche Abstand der Maus zur Richtung (den die Winkelhilfe ja duldet)
        sideways = abs((cursor[0] - first[0]) * uy - (cursor[1] - first[1]) * ux)
        found = [point for point in found if math.dist(point, cursor) <= reach + sideways * 1.5 + 1e-9]
        return min(found, key=lambda point: math.dist(point, cursor)) if found else None

    def alignment_lines(self) -> list:
        """Die gestrichelten Ausrichtungslinien zum aktuellen Mauspunkt (als Linienelemente)."""
        return [sk.line(*start, *end) for start, end in self._alignment]

    def _inference_off(self) -> bool:
        """Strg gehalten bei einem Zeichenwerkzeug: Fusion setzt dann weder Fang noch automatische Abhängigkeiten."""
        return self.tool not in PICK_TOOLS and bool(
            QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)

    def set_linetype(self, mode: str) -> None:
        """Wie Fusions Palette „Linientyp“: gewählte Elemente zu Konstruktion, Mittellinie oder normal machen.

        Ohne Auswahl gilt der Linientyp für die nächsten gezeichneten Elemente (Fusion: Vorwärts-Konstruktion).
        """
        chosen = self.chosen()
        if not chosen:
            self.line_mode = mode
            self.changed.emit()
            return
        for index in chosen:
            if index in self.construction:
                self.construction.remove(index)
            if index in self.centerlines:
                self.centerlines.remove(index)
            if mode == LINE_CONSTRUCTION:
                self.construction.append(index)
            elif mode == LINE_CENTER:
                self.centerlines.append(index)
        self.changed.emit()
        self.update()

    def _on_curve(self, x: float, y: float, reach: float):
        """Der nächste Punkt auf einer vorhandenen Kurve, wenn sie näher als `reach` liegt: ((x, y), Nummer) oder None."""
        best = None
        for index, entity in enumerate(self.entities):
            if entity[0] in (sk.POINT, *sk.SPATIAL):
                continue                        # auf Punkte und räumliche Elemente fängt die Kurve nicht
            distance = _distance_to(entity, x, y)
            if distance <= reach and (best is None or distance < best[0]):
                best = (distance, index)
        if best is None:
            return None
        entity = self.entities[best[1]]
        kind, v = entity[0], entity[1:]
        if kind == "Linie":
            ax, ay, bx, by = v[:4]
            length = (bx - ax) ** 2 + (by - ay) ** 2
            t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length)) if length else 0.0
            return (ax + t * (bx - ax), ay + t * (by - ay)), best[1]
        if kind in ("Kreis", "Bogen"):
            reach_out = math.dist((x, y), (v[0], v[1])) or 1.0
            return (v[0] + (x - v[0]) / reach_out * v[2], v[1] + (y - v[1]) / reach_out * v[2]), best[1]
        # übrige Kurven: nächste Stelle ihres fein nachgezeichneten Zugs
        track = sk.track_of(entity) or []
        if not track:
            return None
        return min(track, key=lambda point: math.dist(point, (x, y))), best[1]

    def _connect_new(self, first_new: int) -> None:
        """Verbindet die Punkte neu gezeichneter Elemente mit vorhandenen Kurven, auf denen sie liegen (wie Fusion):
        auf der Mitte einer Linie → „Mittelpunkt“, sonst auf Linie, Kreis oder Bogen → „Punkt auf Kurve“.

        Punkte, die auf Punkten anderer Elemente liegen, sind ohnehin verbunden (der Löser hält sie zusammen).
        """
        added = False
        for new in range(first_new, len(self.entities)):
            for slot in range(cs.point_count(self.entities[new])):
                point = cs.point_of(self.entities[new], slot)
                others = [index for index in range(len(self.entities)) if index != new]
                if any(math.dist(point, cs.point_of(self.entities[index], other)) < 1e-7
                       for index in others for other in range(cs.point_count(self.entities[index]))):
                    continue
                for index in others:
                    entity = self.entities[index]
                    if index >= first_new and index > new:
                        continue                # zwischen neuen Elementen desselben Klicks nichts verbinden
                    if entity[0] == "Linie" and math.dist(point, ((entity[1] + entity[3]) / 2,
                                                                  (entity[2] + entity[4]) / 2)) < 1e-7:
                        rule = [cs.MIDPOINT, new, index, float(slot)]
                    elif entity[0] in ("Linie", "Kreis", "Bogen") and _distance_to(entity, *point) < 1e-7:
                        rule = [cs.POINT_ON, new, index, float(slot)]
                    else:
                        continue
                    if cs.check(self.entities, rule) is None and rule not in self.constraints:
                        self.constraints.append(rule)
                        added = True
                    break
        if added:
            self.changed.emit()

    def click(self, x: float, y: float) -> None:
        """Verarbeitet einen Klick; neu gezeichnete Elemente werden danach an vorhandene Kurven gebunden."""
        before, locked, tool = len(self.entities), dict(self._hud_locked), self.tool
        chain_before = self._chain_line         # Winkelbezug der gerade entstehenden Linie (voriges Stück)
        self._click(x, y)
        if self.tool not in PICK_TOOLS and len(self.entities) > before:
            # die bleibenden Maße beziehen sich auf den Stand vor dem Klick: danach zeigt der Linienzug schon auf
            # die neue Linie selbst
            chain_after, self._chain_line = self._chain_line, chain_before
            self._hud_dimensions(tool, before, locked)
            self._chain_line = chain_after
            self._hud_locked, self._hud_active = {}, None      # das nächste Element beginnt mit freien Feldern
            if not self._inference_off():
                self._connect_new(before)       # mit Strg: keine automatischen Abhängigkeiten
            self._apply_line_mode(before)

    def _hud_dimensions(self, tool: str, first_new: int, locked: dict) -> None:
        """Festgehaltene Maße bleiben am fertigen Element als Bemaßung stehen (wie Fusion)."""
        def rule(kind: str, index: int, name: str, factor: float = 1.0) -> None:
            if name not in locked or index >= len(self.entities):
                return
            value, expression = locked[name]
            if factor != 1.0 and expression:
                expression = f"{factor:g} * ({expression})"
            entry = [kind, index, -1, float(value) * factor, *([expression] if expression else [])]
            if cs.check(self.entities, entry) is None:
                self.constraints.append(entry)

        if tool == "Linie":
            rule(cs.LENGTH, first_new, "Länge")
            line = self.entities[first_new] if first_new < len(self.entities) else None
            reference = self._angle_reference(tuple(line[1:3]), before=first_new) if line is not None else None
            if reference is not None and "Winkel" in locked:
                # Winkelmaß zwischen den Linien (die Bemaßung misst zwischen den Richtungen Anfang → Ende)
                value, expression = locked["Winkel"]
                other = self.entities[reference[0]]
                flipped = math.dist(other[3:5], line[1:3]) < 1e-9      # Bezug endet am Startpunkt
                measure = 180.0 - float(value) if flipped else float(value)
                if expression and flipped:
                    expression = f"180 - ({expression})"
                entry = [cs.ANGLE, first_new, reference[0], measure, *([expression] if expression else [])]
                if cs.check(self.entities, entry) is None:
                    self.constraints.append(entry)
            else:
                rule(cs.DIRECTION, first_new, "Winkel")
        elif tool in ("Rechteck", CENTER_RECT):
            rule(cs.LENGTH, first_new, "Breite")
            rule(cs.LENGTH, first_new + 1, "Höhe")
        elif tool in ("Kreis", TWO_POINT_CIRCLE):
            rule(cs.DIAMETER, first_new, "Durchmesser")
        elif tool == "Polygon":
            rule(cs.DIAMETER, len(self.entities) - 1, "Radius", 2.0)     # der Konstruktionskreis des Vielecks
        elif tool == "Bogen":
            rule(cs.RADIUS, first_new, "Radius")

    def _apply_line_mode(self, first_new: int) -> None:
        """Neu gezeichnete Elemente bekommen den eingestellten Linientyp (Konstruktion bzw. Mittellinie)."""
        target = {LINE_CONSTRUCTION: self.construction, LINE_CENTER: self.centerlines}.get(self.line_mode)
        if target is not None:
            target.extend(index for index in range(first_new, len(self.entities)) if index not in target)
            self.changed.emit()

    def aim(self, x: float, y: float) -> tuple[tuple[float, float], bool]:
        """Wie `snap`, erkennt aber zusätzlich, wenn die laufende Linie waagrecht oder senkrecht wird.

        Dann rastet sie in diese Richtung ein, und die fertige Linie bekommt die passende
        Abhängigkeit (wie in Fusion).
        """
        position, caught = self.snap(x, y)
        self._inferred, self._inferred_with, self._foot_on = "", -1, -1
        if self.tool == "Linie" and self._points and not self._inference_off():
            first, reach = self._points[-1], self._px(_SNAP_PIXELS)
            # Fährt die Maus über eine Linie, wird sie Bezug für rechtwinklig/parallel (bis eine andere kommt)
            near = [(_distance_to(entity, x, y), index) for index, entity in enumerate(self.entities)
                    if entity[0] == "Linie" and not _touches(entity, first)]
            if near and min(near)[0] <= reach:
                self._hover_line = min(near)[1]
            if self._hover_line >= len(self.entities) or (
                    self._hover_line >= 0 and self.entities[self._hover_line][0] != "Linie"):
                self._hover_line = -1
            # Ein gefangener vorhandener Punkt bleibt, wo er ist: dort zählt nur eine genaue Ausrichtung
            nearest = min(self.snap_points(), key=lambda point: math.dist(point, (x, y)))
            # auch eine (weiter gefangene) Kreismitte ist ein fester Punkt
            on_point = math.dist(nearest, (x, y)) <= reach or getattr(self, "_fixed_snap", False) or (
                caught and any(math.dist(position, centre) < 1e-9 for centre in self.centre_points()))
            # an einem gemerkten Punkt ausgerichtet (gestrichelte Linie): die Richtung (90°/180°, waagrecht …) wird
            # trotzdem gesucht – greift sie, liegt der Punkt im Schnitt beider Hilfslinien (wie Fusion)
            aligned = bool(self._alignment) and not on_point
            raw = position if on_point else (x, y)
            length = math.dist(first, raw)
            if length > 1e-9:
                # Richtungen, in die die Linie einrastet: 0°/90°/180°/270° und – zur vorigen Linie des Zugs –
                # rechtwinklig (90°) oder gerade weiter (180°), wie in Fusion
                wanted = [(0.0, cs.HORIZONTAL, -1), (90.0, cs.VERTICAL, -1), (180.0, cs.HORIZONTAL, -1),
                          (270.0, cs.VERTICAL, -1)]
                previous = self._previous_line(first)
                # Bezugslinien: die vorige Linie des Zugs, alle anderen am Startpunkt anliegenden Linien und die
                # Linie, über die die Maus zuletzt fuhr – zu jeder rastet die neue rechtwinklig oder parallel ein
                references = [previous] if previous is not None else []
                references += [index for index, entity in enumerate(self.entities)
                               if entity[0] == "Linie" and index != previous and _touches(entity, first)]
                if self._hover_line >= 0 and self._hover_line not in references:
                    references.append(self._hover_line)
                for reference in references:
                    line = self.entities[reference]
                    base = math.degrees(math.atan2(line[4] - line[2], line[3] - line[1]))
                    if math.dist(line[1:3], first) < 1e-9:
                        base += 180.0           # die Linie läuft von diesem Punkt weg: Richtung umdrehen
                    wanted += [(base + 90.0, cs.PERPENDICULAR, reference), (base - 90.0, cs.PERPENDICULAR, reference),
                               (base, cs.PARALLEL, reference), (base + 180.0, cs.PARALLEL, reference)]
                angle = math.degrees(math.atan2(raw[1] - first[1], raw[0] - first[0]))
                off = lambda entry: abs((angle - entry[0] + 180.0) % 360.0 - 180.0)
                # bei (fast) gleicher Richtung gewinnt der frühere Eintrag: Achsen, dann die vorige Linie des Zugs
                order = {id(entry): number for number, entry in enumerate(wanted)}
                best = min(wanted, key=lambda entry: (round(off(entry), 6), order[id(entry)]))
                # nah am Anfang zählt die Fangweite in Bildpunkten, weiter draußen ein fester Winkel
                tolerance = 1e-7 if on_point else max(_ANGLE_SNAP_DEGREES, math.degrees(math.atan2(reach, length)))
                crossing = None
                if off(best) <= tolerance and aligned:
                    crossing = self._direction_meets_alignment(
                        first, (math.cos(math.radians(best[0])), math.sin(math.radians(best[0]))), (x, y))
                    if crossing is None:
                        self._alignment = []            # beides zugleich geht nicht: die Richtung gewinnt
                if crossing is not None:
                    position = crossing
                    self._inferred, self._inferred_with = best[1], best[2]
                elif off(best) <= tolerance and self._curve_hit is not None and self._direction_meets_curve(
                        first, best[0], (x, y), reach) is not None:
                    # Richtung (z. B. senkrecht) und Kurve unter der Maus (z. B. die untere Kante) zugleich: der
                    # Punkt liegt genau im Schnitt – auf der Kurve und in der Richtung (wie Fusion)
                    position = self._direction_meets_curve(first, best[0], (x, y), reach)
                    self._inferred, self._inferred_with = best[1], best[2]
                elif off(best) <= tolerance:
                    ux, uy = math.cos(math.radians(best[0])), math.sin(math.radians(best[0]))
                    along = (raw[0] - first[0]) * ux + (raw[1] - first[1]) * uy
                    step = self.grid_step()
                    if step > 0 and not on_point:
                        # längs der Richtung rastet die Länge auf das Raster
                        rounded = round(along / step) * step
                        if rounded > 1e-9 and abs(rounded - along) <= self._px(_GRID_SNAP_PIXELS):
                            along = rounded
                    if not on_point:
                        position = (first[0] + ux * along, first[1] + uy * along)
                        foot = self._foot_point(first, (ux, uy), best, (x, y), reach)
                        if foot is not None:
                            position, self._foot_on = foot, best[2]
                    self._inferred, self._inferred_with = best[1], best[2]
        self._raw_cursor = position
        if self._hud_locked:
            position = self._hud_point(position)    # festgehaltene Maße (Länge, Winkel …) bestimmen die Stelle
        return position, caught or bool(self._inferred)

    def _foot_point(self, first, direction, best, cursor, reach):
        """Rechtwinklig zu einer Linie gezogen, deren Nähe die Maus erreicht hat: das Ende in den Lotfußpunkt
        auf ihr setzen (wie Fusion). None, wenn die Maus nicht nah genug an jener Linie ist."""
        if best[1] != cs.PERPENDICULAR or best[2] < 0 or best[2] != self._hover_line:
            return None
        line = self.entities[best[2]]
        if _distance_to(line, *cursor) > reach:
            return None
        # Schnitt des Strahls first + t·direction mit der Trägergeraden der Linie
        (ax, ay), (bx, by) = line[1:3], line[3:5]
        dx, dy = bx - ax, by - ay
        denominator = direction[0] * dy - direction[1] * dx
        if abs(denominator) < 1e-12:
            return None
        t = ((ax - first[0]) * dy - (ay - first[1]) * dx) / denominator
        return (first[0] + direction[0] * t, first[1] + direction[1] * t) if t > 1e-9 else None

    def _px(self, pixels: float) -> float:
        """Eine Fangweite in Bildpunkten als Länge in der Skizze (mm), mit der Pixeldichte des Bildschirms skaliert –
        so fängt WolfCAD auf einem 4K-Bildschirm auf derselben Strecke wie Fusion (Nutzer 2026-10-22: Fangmarke
        erschien, die Vorschau sprang aber nicht, weil die Maus 9 Bildpunkte kaum trifft)."""
        return pixels * self.pixel_factor / self._scale

    def _angle_reference(self, start: tuple[float, float], before: int | None = None):
        """Bezug für das Winkelfeld einer Linie wie in Fusion: nur beim Fortsetzen eines Linienzugs das vorige Stück.

        Beginnt eine Linie neu – auch an einem Punkt, an dem schon Linien hängen –, misst Fusion gegen die Waagrechte
        (Video des Nutzers 2026-10-22: Linie von der Ecke einer 14-mm-Stufe, Fusion zeigt 65,4° zur Waagrechten,
        WolfCAD zeigte 23,16° zur angehängten Senkrechten). Liefert (Nummer der Linie, Richtung in Grad von `start`
        zu ihrem anderen Ende) oder None. Mit `before` zählen nur Linien mit kleinerer Nummer.
        """
        index = self._chain_line
        limit = len(self.entities) if before is None else before
        if index is None or not 0 <= index < min(limit, len(self.entities)) or self.entities[index][0] != "Linie":
            return None
        entity = self.entities[index]
        if math.dist(entity[3:5], start) < 1e-9:
            other = entity[1:3]
        elif math.dist(entity[1:3], start) < 1e-9:
            other = entity[3:5]
        else:
            return None
        return index, math.degrees(math.atan2(other[1] - start[1], other[0] - start[0]))

    def _touching_line(self, start: tuple[float, float], before: int | None = None):
        """Die zuletzt gezeichnete Linie, die am Startpunkt endet oder beginnt (für das Rechtwinkel-Zeichen).

        Liefert (Nummer der Linie, Richtung in Grad von `start` zu ihrem anderen Ende) oder None.
        """
        limit = len(self.entities) if before is None else before
        for index in range(limit - 1, -1, -1):
            entity = self.entities[index]
            if entity[0] != "Linie":
                continue
            if math.dist(entity[3:5], start) < 1e-9:
                other = entity[1:3]
            elif math.dist(entity[1:3], start) < 1e-9:
                other = entity[3:5]
            else:
                continue
            return index, math.degrees(math.atan2(other[1] - start[1], other[0] - start[0]))
        return None

    def _relative_angle(self, start: tuple[float, float], point: tuple[float, float],
                        reference: tuple | None = None) -> float | None:
        """Winkel (0 … 180°) zwischen der Bezugslinie am Startpunkt und der Richtung start → point; None ohne Bezug.
        `reference`: ein anderer Bezug als der des Winkelfelds (z. B. die anhängende Linie)."""
        reference = reference if reference is not None else self._angle_reference(start)
        if reference is None or math.dist(start, point) < 1e-12:
            return None
        direction = math.degrees(math.atan2(point[1] - start[1], point[0] - start[0]))
        return abs((direction - reference[1] + 180.0) % 360.0 - 180.0)

    @staticmethod
    def _horizontal_base(dx: float) -> float:
        """Ohne Bezugslinie misst Fusion gegen die Waagrechte auf der Seite, zu der die Linie läuft (0° bzw. 180°)."""
        return 0.0 if dx >= 0 else 180.0

    def inference_glyph(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """Kleines Zeichen am Startpunkt der laufenden Linie, wenn sie rechtwinklig zu einer Linie einrastet
        (Fusion zeigt dort das Rechtwinkel-Zeichen): zwei Striche, die mit beiden Linien ein Quadrat bilden."""
        if self.tool != "Linie" or not self._points or self._cursor is None:
            return []
        start, cursor = self._points[-1], self._hud_point(self._cursor)
        length = math.dist(start, cursor)
        if length < 1e-9:
            return []
        if self._inferred == cs.PERPENDICULAR and 0 <= self._inferred_with < len(self.entities):
            reference = self.entities[self._inferred_with]
        else:
            # auch wenn „waagrecht/senkrecht“ gewonnen hat: steht die Linie im rechten Winkel zur Linie, von der sie
            # weggezogen wird, zeigt Fusion das Rechtwinkel-Zeichen
            touching = self._touching_line(start)
            relative = self._relative_angle(start, cursor, touching) if touching is not None else None
            if touching is None or relative is None or abs(relative - 90.0) > 1e-6:
                return []
            reference = self.entities[touching[0]]
        rx, ry = reference[3] - reference[1], reference[4] - reference[2]
        size = 18 / max(self._scale, 1e-9)        # ≈ 18 Bildpunkte, wie Fusions Zeichen
        norm = math.hypot(rx, ry) or 1.0
        rx, ry = rx / norm * size, ry / norm * size
        # die Seite des Zeichens: zur Bezugslinie hin, auf der die Linie liegt (am Startpunkt wie in Fusion)
        if math.dist(reference[1:3], start) < 1e-9:
            pass                                    # Bezug läuft vom Startpunkt weg: Richtung bleibt
        elif math.dist(reference[3:5], start) < 1e-9:
            rx, ry = -rx, -ry                       # Bezug endet am Startpunkt: zurück an ihm entlang
        ux, uy = (cursor[0] - start[0]) / length * size, (cursor[1] - start[1]) / length * size
        a, corner, b = (start[0] + ux, start[1] + uy), (start[0] + ux + rx, start[1] + uy + ry), (start[0] + rx, start[1] + ry)
        return [(a, corner), (corner, b)]

    def _previous_line(self, point: tuple[float, float]) -> int | None:
        """Nummer der zuletzt gezeichneten Linie, die an diesem Punkt endet oder beginnt (sonst None)."""
        for index in range(len(self.entities) - 1, -1, -1):
            entity = self.entities[index]
            if entity[0] == "Linie" and (math.dist(entity[3:5], point) < 1e-9 or math.dist(entity[1:3], point) < 1e-9):
                return index
        return None

    # --- Zeichnen per Klick -----------------------------------------------

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self._points = []
        self._acquired, self._alignment = [], []        # gemerkte Punkte gelten nur für ein Werkzeug
        self.selected = None
        self.selection = set()
        self._first_pick = None
        self._hud_locked, self._hud_active = {}, None
        self._dimension_first = None
        self._dimension_place = None
        self._tangent_lines = []
        self._blend_first = None
        self._mirror_axis = None
        self.message = ""                   # Hinweise des vorigen Werkzeugs gelten nicht mehr
        self.update()
        self.changed.emit()

    def _click(self, x: float, y: float) -> None:
        """Verarbeitet einen Klick an der (bereits gefangenen) Skizzenposition."""
        if self.tool == "Auswählen":
            hit = self._entity_at(x, y)
            if not self.additive:
                self.selection = {hit} if hit is not None else set()
            elif hit is not None:
                self.selection ^= {hit}         # Strg+Klick nimmt dazu oder wieder heraus
            self.selected = hit if hit in self.selection else next(iter(self.selection), None)
            self.changed.emit()
            self.update()
            return
        if self.tool in EDIT_TOOLS:
            self._edit_at(x, y)
            self.update()
            return
        if self.tool == DIMENSION_TOOL and getattr(self, "_dimension_place", None):
            self._place_point_dimension(x, y)
            self.update()
            return
        if self.tool in PICK_TOOLS:
            self._pick_for_constraint(self._entity_or_point_at(x, y), x, y)
            self.update()
            return
        if self.tool == TEXT_TOOL:
            self.text_requested.emit(x, y)      # der Wortlaut wird abgefragt, dann entsteht der Text hier
            return
        if self.tool == sk.SPLINE and self.additive and not self._points:
            self._edit_spline_point(x, y)
            self.update()
            return
        if self.tool == POINT_TOOL:
            self._add(sk.point(x, y))           # ein Klick setzt einen Skizzenpunkt
            self.update()
            return
        if self.tool in (TWO_TANGENT_CIRCLE, THREE_TANGENT_CIRCLE):
            self._tangent_circle_click(x, y)
            self.update()
            return
        if not self._points:
            self._chain_line = None             # neue Linie: noch kein voriges Stück (Winkel gegen die Waagrechte)
        self._points.append((x, y))
        first = self._points[0]
        if self.tool == "Linie" and len(self._points) == 2:
            count = len(self.entities)
            self._add(sk.line(*first, x, y))
            if len(self.entities) > count and self._inferred in (cs.HORIZONTAL, cs.VERTICAL) and (
                    abs(y - first[1]) < 1e-9 if self._inferred == cs.HORIZONTAL else abs(x - first[0]) < 1e-9):
                # Die Linie wurde waagrecht bzw. senkrecht gezogen: das bleibt sie auch
                self.constraints.append([self._inferred, count, -1, 0.0])
                self.changed.emit()
            elif (len(self.entities) > count and self._inferred in (cs.PERPENDICULAR, cs.PARALLEL)
                  and 0 <= self._inferred_with < count):
                # rechtwinklig oder parallel zu einer Bezugslinie gezogen: auch das bleibt als Abhängigkeit
                self.constraints.append([self._inferred, count, self._inferred_with, 0.0])
                reference = self.entities[self._inferred_with]
                at_end = min(math.dist(reference[1:3], (x, y)), math.dist(reference[3:5], (x, y))) < 1e-7
                if self._foot_on == self._inferred_with and not at_end:
                    # Ende im Lotfußpunkt: es bleibt auf jener Linie (Punkt 1 = Ende der neuen Linie)
                    self.constraints.append([cs.POINT_ON, count, self._foot_on, 1.0])
                self.changed.emit()
            self._inferred, self._inferred_with, self._foot_on = "", -1, -1
            self._hover_line = -1
            self._points = [(x, y)]         # Linienzug: der Endpunkt ist der nächste Anfang
            if len(self.entities) > count:
                self._chain_line = count    # das fertige Stück ist der Winkelbezug des nächsten
        elif self.tool == "Rechteck" and len(self._points) == 2:
            x = self._rectangle_x(first, x)     # eine eingetippte Breite gilt auch beim Klicken
            if abs(x - first[0]) > 1e-9 and abs(y - first[1]) > 1e-9:
                # Wie in Fusion bleibt ein Rechteck rechtwinklig: Seiten waagrecht bzw. senkrecht
                count = len(self.entities)
                for side, entity in enumerate(sk.rectangle(*first, x, y)):
                    self.constraints.append([cs.VERTICAL if side % 2 else cs.HORIZONTAL,
                                             len(self.entities), -1, 0.0])
                    self.entities.append(entity)
                self.changed.emit()
            self._points = []
        elif self.tool == "Kreis" and len(self._points) == 2:
            self._add(sk.circle(*first, math.dist(first, (x, y))))
            self._points = []
        elif self.tool == "Polygon" and self.polygon_kind != sk.POLYGON_KINDS[0] and len(self._points) == (
                3 if self.polygon_kind == sk.POLYGON_KINDS[2] else 2):
            self._add_polygon(self._polygon_shape(self._points[:-1], (x, y)),
                              inside=self.polygon_kind == sk.POLYGON_KINDS[1])
            self._points = []
        elif self.tool == "Polygon" and self.polygon_kind == sk.POLYGON_KINDS[0] and len(self._points) == 2:
            if math.dist(first, (x, y)) > 1e-9:
                # Wie in Fusion hält ein Umkreis (Konstruktionslinie) das Vieleck regelmäßig:
                # alle Ecken liegen darauf, alle Seiten sind gleich lang
                count = len(self.entities)
                sides = sk.polygon(*first, x, y, int(self.polygon_sides))
                self.entities.extend(sides)
                self.entities.append(sk.circle(*first, math.dist(first, (x, y))))
                rim = len(self.entities) - 1
                self.construction.append(rim)
                self.constraints += [[cs.POINT_ON, count + n, rim, 0.0] for n in range(len(sides))]
                self.constraints += [[cs.EQUAL, count, count + n, 0.0] for n in range(1, len(sides))]
                self.changed.emit()
            self._points = []
        elif self.tool == "Langloch" and len(self._points) == self._slot_clicks():
            self._points.pop()                  # der letzte Klick legt nur die Breite fest
            for entity in self._slot_to(x, y):
                self._add(entity)
            self._points = []
        elif self.tool == CENTER_RECT and len(self._points) == 2:
            # Mittelpunkt-Rechteck: erste Ecke gespiegelt am Mittelpunkt, Seiten waagrecht/senkrecht
            if abs(x - first[0]) > 1e-9 and abs(y - first[1]) > 1e-9:
                count = len(self.entities)
                for side, entity in enumerate(sk.rectangle(2 * first[0] - x, 2 * first[1] - y, x, y)):
                    self.constraints.append([cs.VERTICAL if side % 2 else cs.HORIZONTAL, len(self.entities), -1, 0.0])
                    self.entities.append(entity)
                self.changed.emit()
            self._points = []
        elif self.tool == THREE_POINT_RECT and len(self._points) == 3:
            corners = self._three_point_corners(x, y)
            if corners is not None:
                # gedrehtes Rechteck: gegenüberliegende Seiten parallel, benachbarte rechtwinklig
                count = len(self.entities)
                self.entities += sk.rectangle_points(corners)
                self.constraints += [[cs.PERPENDICULAR, count, count + 1, 0.0], [cs.PARALLEL, count, count + 2, 0.0],
                                     [cs.PARALLEL, count + 1, count + 3, 0.0]]
                self.changed.emit()
            self._points = []
        elif self.tool == THREE_POINT_CIRCLE and len(self._points) == 3:
            found = sk.circle_through(*self._points)
            if found is not None:
                self._add(sk.circle(*found))
            self._points = []
        elif self.tool == TWO_POINT_CIRCLE and len(self._points) == 2:
            # Durchmesser zwischen den beiden Punkten
            self._add(sk.circle((first[0] + x) / 2, (first[1] + y) / 2, math.dist(first, (x, y)) / 2))
            self._points = []
        elif self.tool == THREE_POINT_ARC and len(self._points) == 3:
            # Start, Ende, dann ein Punkt auf dem Bogen (wie in Fusion)
            made = sk.arc_through(first, (x, y), self._points[1])
            if made is not None:
                self._add(made)
            self._points = []
        elif self.tool == TANGENT_ARC and len(self._points) == 2:
            # Tangentialbogen: beginnt am Ende eines vorhandenen Elements und setzt dessen Richtung fort
            made = self._tangent_arc_to(first, (x, y))
            if made is not None:
                count = len(self.entities)
                source = self._ending_at(first)     # vor dem Hinzufügen: sonst fände es den neuen Bogen selbst
                self._add(made)
                if len(self.entities) > count and source is not None:
                    self.constraints.append([cs.TANGENT, source, count, 0.0])
                    self.changed.emit()
                self._points = [(x, y)]         # wie ein Linienzug: weiter vom Ende aus
            else:
                self._points = []
        elif self.tool == "Bogen" and len(self._points) == 3:
            start = self._points[1]
            self._add(sk.arc(*first, math.dist(first, start), _angle(first, start), _angle(first, (x, y))))
            self._points = []
        elif self.tool == sk.CONIC and len(self._points) == 3:
            # Anfang, Ende, dann der Scheitel: dorthin zeigen beide Enden der Kurve
            curve = sk.conic(*first, *self._points[1], x, y, self.conic_rho)
            if sk.check_entity(curve) is None:
                self._add(curve)
            self._points = []
        elif self.tool == sk.ELLIPSE and len(self._points) == 3:
            oval = self._ellipse_to(x, y)
            if oval is not None:
                self._add(oval)
            self._points = []
        self.update()

    def _ellipse_to(self, x: float, y: float) -> list | None:
        """Die Ellipse aus Mittelpunkt und erstem Scheitel; (x, y) legt die zweite Halbachse fest."""
        centre, tip = self._points[0], self._points[1]
        first = math.dist(centre, tip)
        if first < 1e-9:
            return None
        second = abs((tip[0] - centre[0]) * (y - centre[1]) - (tip[1] - centre[1]) * (x - centre[0])) / first
        return sk.ellipse(*centre, first, second, _angle(centre, tip)) if second > 1e-9 else None

    # --- Maße beim Zeichnen eintippen ---------------------------------------

    def _hud_names(self) -> list[str]:
        """Die Maßfelder, die Fusion beim Zeichnen des laufenden Elements anzeigt (leer: keine)."""
        count, tool = len(self._points), self.tool
        if count == 0:
            return []
        if tool == "Linie":
            return ["Länge", "Winkel"]
        if tool in ("Rechteck", CENTER_RECT) and count == 1:
            return ["Breite", "Höhe"]
        if tool in ("Kreis", TWO_POINT_CIRCLE) and count == 1:
            return ["Durchmesser"]
        if tool == "Polygon" and count == 1 and self.polygon_kind != sk.POLYGON_KINDS[2]:
            return ["Radius"]
        if tool == "Bogen":
            return ["Radius"] if count == 1 else ["Winkel"] if count == 2 else []
        if tool == "Langloch" and self.slot_kind == sk.SLOT_KINDS[0]:
            return ["Länge", "Winkel"] if count == 1 else ["Breite"] if count == 2 else []
        return []

    def typed_prompt(self) -> str | None:
        """Welches Maßfeld gerade aktiv ist (Eingaben gehen dorthin); None: keins."""
        names = self._hud_names()
        if not names:
            return None
        return self._hud_active if self._hud_active in names else names[0]

    def _hud_point(self, cursor: tuple[float, float]) -> tuple[float, float]:
        """Die Mausstelle so verschoben, dass alle festgehaltenen Maße gelten (freie Maße folgen der Maus)."""
        locks = {name: value for name, (value, _expression) in self._hud_locked.items()}
        names, points, tool = self._hud_names(), self._points, self.tool
        if not names or not locks:
            return cursor
        if tool == "Linie" or (tool == "Langloch" and len(points) == 1):
            start = points[-1]
            dx, dy = cursor[0] - start[0], cursor[1] - start[1]
            distance = math.hypot(dx, dy)
            if "Winkel" in locks:
                direction = locks["Winkel"]
                reference = self._angle_reference(start) if tool == "Linie" else None
                if reference is not None:
                    # wie Fusion: der Winkel zählt ab der Linie, von der weggezogen wird – zur Seite der Maus
                    rx, ry = math.cos(math.radians(reference[1])), math.sin(math.radians(reference[1]))
                    side = 1.0 if rx * dy - ry * dx >= 0 else -1.0
                    direction = reference[1] + side * locks["Winkel"]
                elif tool == "Linie":
                    # ohne Bezug: ab der Waagrechten auf der Seite der Maus, zur Maus hin geneigt
                    base = self._horizontal_base(dx)
                    turn = 1.0 if (dy >= 0) == (base == 0.0) else -1.0
                    direction = base + turn * locks["Winkel"]
                ux, uy = math.cos(math.radians(direction)), math.sin(math.radians(direction))
                length = locks.get("Länge", max(dx * ux + dy * uy, 1e-3))    # freie Länge: Maus auf den Strahl
            else:
                ux, uy = (dx / distance, dy / distance) if distance > 1e-9 else (1.0, 0.0)
                length = locks.get("Länge", distance)
            return start[0] + ux * length, start[1] + uy * length
        if tool in ("Rechteck", CENTER_RECT):
            first, (x, y) = points[0], cursor
            share = 1.0 if tool == "Rechteck" else 0.5        # Mittelpunkt-Rechteck: Maße sind ganze Seiten
            if "Breite" in locks:
                x = first[0] + math.copysign(locks["Breite"] * share, x - first[0] or 1.0)
            if "Höhe" in locks:
                y = first[1] + math.copysign(locks["Höhe"] * share, y - first[1] or 1.0)
            return x, y
        if tool == "Bogen" and len(points) == 2 and "Winkel" in locks:
            centre, begin = points
            radius = math.dist(centre, begin)
            angle = math.radians(_angle(centre, begin) + locks["Winkel"])
            return centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)
        if tool == "Langloch" and len(points) == 2 and "Breite" in locks:
            (ax, ay), (bx, by) = points
            length = math.dist((ax, ay), (bx, by)) or 1.0
            nx, ny = -(by - ay) / length, (bx - ax) / length
            side = 1.0 if (cursor[0] - ax) * nx + (cursor[1] - ay) * ny >= 0 else -1.0
            return ax + nx * side * locks["Breite"] / 2, ay + ny * side * locks["Breite"] / 2
        # ein Maß vom ersten Punkt aus: Durchmesser, Radius
        centre = points[0]
        dx, dy = cursor[0] - centre[0], cursor[1] - centre[1]
        distance = math.hypot(dx, dy)
        ux, uy = (dx / distance, dy / distance) if distance > 1e-9 else (1.0, 0.0)
        reach = {"Durchmesser": locks.get("Durchmesser", 2 * distance) / (1 if tool == TWO_POINT_CIRCLE else 2),
                 "Radius": locks.get("Radius", distance)}.get(names[0], distance)
        return centre[0] + ux * reach, centre[1] + uy * reach

    def hud_fields(self) -> list[dict]:
        """Die Maßfelder für die Anzeige in der Ansicht: Name, Wert, Einheit, Stelle (Skizze), fest/aktiv."""
        names = self._hud_names()
        if not names or self._cursor is None:
            return []
        cursor, points, tool = self._hud_point(self._cursor), self._points, self.tool
        gap = 40 / max(self._scale, 1e-9)              # Abstand der Felder von der Geometrie (≈ 40 Bildpunkte)
        if math.dist(cursor, points[-1]) < 2 / max(self._scale, 1e-9):
            return []                                   # Maus noch auf dem letzten Punkt: wie Fusion noch keine Felder
        active = self.typed_prompt()
        fields = []

        def add(name: str, value: float, anchor: tuple[float, float], lines: list | None = None,
                arrows: list | None = None) -> None:
            """Ein Maßfeld; `lines`: Maß- und Hilfslinien (Skizzenpunkte), `arrows`: Pfeilspitzen (Spitze, Richtung
            von dort weg) – so zeichnet die Ansicht Fusions Maßlinien mit."""
            fields.append({"name": name, "value": value, "unit": " °" if name == "Winkel" else " mm",
                           "anchor": anchor, "locked": name in self._hud_locked, "active": name == active,
                           "lines": lines or [], "arrows": arrows or []})

        if tool == "Linie" or (tool == "Langloch" and len(points) == 1):
            start = points[-1]
            dx, dy = cursor[0] - start[0], cursor[1] - start[1]
            length = math.hypot(dx, dy)
            angle = math.degrees(math.atan2(dy, dx))
            # Bezugsrichtung des Winkels: voriges Stück des Linienzugs, sonst die Waagrechte (wie Fusion)
            reference = self._angle_reference(start) if tool == "Linie" else None
            base = reference[1] if reference is not None else (self._horizontal_base(dx) if tool == "Linie" else 0.0)
            turn = (angle - base + 180.0) % 360.0 - 180.0
            # Längenmaß auf der Seite gegenüber dem Winkelbogen (Fusion: parallel zur Linie, mit Hilfslinien)
            ux, uy = (dx / length, dy / length) if length > 1e-9 else (1.0, 0.0)
            side = 1.0 if turn >= 0 else -1.0     # Bogen dreht gegen den Uhrzeiger: Maß rechts davon, sonst links
            nx, ny = -uy * side, ux * side
            offset = 1.4 * gap
            a = (start[0] + nx * offset, start[1] + ny * offset)
            b = (cursor[0] + nx * offset, cursor[1] + ny * offset)
            overshoot = 0.25 * gap
            lines = [(start, (start[0] + nx * (offset + overshoot), start[1] + ny * (offset + overshoot))),
                     (cursor, (cursor[0] + nx * (offset + overshoot), cursor[1] + ny * (offset + overshoot))),
                     (a, b)]
            # Feld außen an der Maßlinie, etwas zum Ende hin (wie Fusion; mittig lag es oft auf anderen Linien)
            add("Länge", length, (a[0] + (b[0] - a[0]) * 0.7 + nx * 2.6 * gap, a[1] + (b[1] - a[1]) * 0.7 + ny * 2.6 * gap),
                lines, [(a, b), (b, a)])
            if tool == "Linie":
                # Winkelbogen um den Startpunkt von der Bezugsrichtung zur Linie; Feld außen in der Bogenmitte
                radius = max(0.75 * length, 1.5 * gap)
                steps = max(4, int(abs(turn) / 6) + 1)
                arc = [(start[0] + radius * math.cos(math.radians(base + turn * k / steps)),
                        start[1] + radius * math.sin(math.radians(base + turn * k / steps))) for k in range(steps + 1)]
                ray = (start, (start[0] + 1.15 * radius * math.cos(math.radians(base)),
                               start[1] + 1.15 * radius * math.sin(math.radians(base))))
                arc_lines = [ray] + list(zip(arc[:-1], arc[1:]))
                half = math.radians(base + turn / 2)
                reach = radius + 1.2 * gap
                value = self._relative_angle(start, cursor) if reference is not None else abs(turn)
                add("Winkel", value if value is not None else abs(turn),
                    (start[0] + reach * math.cos(half), start[1] + reach * math.sin(half)),
                    arc_lines, [(arc[0], arc[1]), (arc[-1], arc[-2])] if len(arc) > 2 else [])
            else:
                half = math.radians(angle / 2)
                add("Winkel", angle, (start[0] + 2.2 * gap * math.cos(half), start[1] + 2.2 * gap * math.sin(half)))
        elif tool in ("Rechteck", CENTER_RECT):
            first = points[0]
            factor = 1.0 if tool == "Rechteck" else 2.0
            width, height = abs(cursor[0] - first[0]) * factor, abs(cursor[1] - first[1]) * factor
            low_y = min(first[1], cursor[1]) if tool == "Rechteck" else first[1] - height / 2
            right = max(first[0], cursor[0]) if tool == "Rechteck" else first[0] + width / 2
            middle_x = (first[0] + cursor[0]) / 2 if tool == "Rechteck" else first[0]
            middle_y = (first[1] + cursor[1]) / 2 if tool == "Rechteck" else first[1]
            add("Breite", width, (middle_x, low_y - gap))
            add("Höhe", height, (right + 1.6 * gap, middle_y))
        elif tool == "Bogen" and len(points) == 2:
            centre, begin = points
            sweep = (_angle(centre, cursor) - _angle(centre, begin)) % 360
            add("Winkel", sweep, (centre[0] + gap, centre[1] + gap))
        elif tool == "Langloch" and len(points) == 2:
            (ax, ay), (bx, by) = points
            length = math.dist((ax, ay), (bx, by)) or 1.0
            across = abs((bx - ax) * (cursor[1] - ay) - (by - ay) * (cursor[0] - ax)) / length
            add("Breite", 2 * across, (cursor[0] + gap, cursor[1] + gap))
        else:
            centre = points[0]
            distance = math.dist(centre, cursor)
            name = names[0]
            value = {"Durchmesser": distance if tool == TWO_POINT_CIRCLE else 2 * distance}.get(name, distance)
            add(name, value, ((centre[0] + cursor[0]) / 2 + gap, (centre[1] + cursor[1]) / 2 + gap))
        return fields

    def _rectangle_x(self, first: tuple[float, float], x: float) -> float:
        """x der zweiten Ecke des laufenden Rechtecks (die festgehaltene Breite gilt schon über `aim`)."""
        return x

    def apply_typed(self, value: float, expression: str = "", more: bool = False, backward: bool = False) -> None:
        """Übernimmt einen eingetippten Wert ins aktive Maßfeld (wie Fusion): Tab hält ihn fest und springt zum
        nächsten Feld (Umschalt+Tab zurück), Eingabe setzt das Element. Festgehaltene Werte werden zu Bemaßungen.
        Beim Rechteck führt Eingabe nach der Breite (wie bisher) erst zur Höhe."""
        prompt = self.typed_prompt()
        self._hud_typing = None                 # der getippte Wert ist jetzt bestätigt
        if prompt is None or (value <= 0 and prompt != "Winkel"):
            return
        self._hud_locked[prompt] = (float(value), expression)
        names = self._hud_names()
        if more or (self.tool in ("Rechteck", CENTER_RECT) and prompt == names[0] and names[1] not in self._hud_locked):
            step = -1 if backward else 1
            self._hud_active = names[(names.index(prompt) + step) % len(names)]
            self._cursor = self._hud_point(self._cursor)
            self.changed.emit()
            self.update()
            return
        self._cursor = self._hud_point(self._cursor)
        self.click(*self._cursor)
        self.changed.emit()
        self.update()

    def hud_typing(self, name: str, value: float | None, expression: str = "") -> None:
        """Ein Wert wird gerade ins Feld `name` getippt: er gilt sofort (die Maus ändert dieses Maß nicht mehr).
        `value` None (leeres oder unbrauchbares Feld): es gilt wieder, was vorher galt."""
        if self._hud_typing is None or self._hud_typing[0] != name:
            self._hud_typing = (name, self._hud_locked.get(name))
        if value is None or (value <= 0 and name != "Winkel"):
            previous = self._hud_typing[1]
            if previous is None:
                self._hud_locked.pop(name, None)
            else:
                self._hud_locked[name] = previous
        else:
            self._hud_locked[name] = (float(value), expression)
        self._cursor = self._hud_point(self._raw_cursor or self._cursor)
        self.changed.emit()
        self.update()

    def hud_typing_cancelled(self) -> None:
        """Esc im Feld: der nur angetippte Wert entfällt, es gilt wieder der vorherige Zustand."""
        if self._hud_typing is not None:
            self.hud_typing(self._hud_typing[0], None)
        self._hud_typing = None

    def hud_unlock(self, name: str) -> None:
        """Gibt ein festgehaltenes Maß wieder frei (es folgt dann wieder der Maus)."""
        self._hud_locked.pop(name, None)
        self.changed.emit()

    def finish(self) -> None:
        """Bricht das laufende Element ab bzw. schließt einen Linienzug oder Spline ab."""
        before = len(self.entities)
        self._finish()
        if len(self.entities) > before:
            self._connect_new(before)       # auch Spline-Enden auf vorhandenen Linien werden verbunden
            self._apply_line_mode(before)

    def _finish(self) -> None:
        # Esc: angefangene Mehrklick-Werkzeuge beginnen neu
        self._dimension_first = self._dimension_place = self._blend_first = self._align_first = None
        self._move_from_point = None
        self._hud_locked, self._hud_active = {}, None
        self._tangent_lines = []
        heights = (list(self.point_heights) + [0.0] * len(self._points))[:len(self._points)]
        self.point_heights = []
        if self.tool == sk.SPLINE and len(self._points) >= 2 and any(abs(z) > 1e-9 for z in heights):
            # 3D-Skizze: mindestens ein Punkt liegt außerhalb der Ebene (Körperecke) – ein Spline durch Raumpunkte
            self._add(sk.spline3d(*((x, y, z) for (x, y), z in zip(self._points, heights))))
        elif self.tool == sk.SPLINE and len(self._points) >= 2:
            self._add(sk.spline(*self._points))     # der Spline läuft durch alle gesetzten Punkte
        if self.tool == sk.CP_SPLINE and len(self._points) >= 2:
            self._add(sk.control_spline(*self._points, degree=self.cp_degree))     # die Punkte bilden das Steuerpolygon
        self._points = []
        self.update()

    def chosen(self) -> list[int]:
        """Alle gewählten Elemente (Mehrfachauswahl und das einzeln gewählte), aufsteigend."""
        numbers = set(self.selection)
        if self.selected is not None:
            numbers.add(self.selected)
        return sorted(n for n in numbers if 0 <= n < len(self.entities))

    def add_text(self, text: str, x: float, y: float, height: float, rotation: float = 0.0) -> bool:
        """Setzt einen Text mit seiner linken unteren Ecke an (x, y); False, wenn er unbrauchbar ist."""
        entry = [text.strip(), float(x), float(y), float(height), float(rotation)]
        problem = sk.check_text(entry)
        if problem is None:
            self.texts.append(entry)
            self.text_height = float(height)
        self.message = problem or ""
        self.changed.emit()
        self.update()
        return problem is None

    def remove_text(self, number: int) -> None:
        if 0 <= number < len(self.texts):
            del self.texts[number]
            self.selected_text = None
            self.changed.emit()

    def change_text(self, number: int, text: str | None = None, height: float | None = None,
                    rotation: float | None = None, font: str | None = None) -> bool:
        """Ändert Wortlaut, Schrifthöhe, Drehung oder Schrift eines Textes; unbrauchbare Werte bleiben außen vor."""
        if not 0 <= number < len(self.texts):
            return False
        entry = list(self.texts[number]) + [""] * (6 - len(self.texts[number]))
        for position, value in ((0, text), (3, height), (4, rotation), (5, font)):
            if value is not None:
                entry[position] = value.strip() if position in (0, 5) else float(value)
        problem = sk.check_text(entry)
        if problem is None:
            self.texts[number][:] = entry if entry[5] else entry[:5]
        self.message = problem or ""
        self.changed.emit()
        self.update()
        return problem is None

    def text_at(self, x: float, y: float) -> int | None:
        """Nummer des Textes, in dessen Feld der Punkt liegt (None, wenn dort keiner steht)."""
        for number, entry in enumerate(self.texts):
            try:
                outline = sk.text_outline(entry[0], entry[3], entry[5] if len(entry) > 5 else "")
            except Exception:
                continue
            points = [point for line in outline for point in line]
            if not points:
                continue
            # den Punkt in die Lage des Textes drehen: dort ist sein Feld ein achsparalleles Rechteck
            cosine, sine = math.cos(math.radians(entry[4])), math.sin(math.radians(entry[4]))
            u = (x - entry[1]) * cosine + (y - entry[2]) * sine
            v = -(x - entry[1]) * sine + (y - entry[2]) * cosine
            if (min(p[0] for p in points) <= u <= max(p[0] for p in points)
                    and min(p[1] for p in points) <= v <= max(p[1] for p in points)):
                return number
        return None

    def copy_data(self) -> dict | None:
        """Die gewählten Elemente samt der Maße und Abhängigkeiten, die nur sie betreffen (None: nichts gewählt)."""
        numbers = self.chosen()
        if not numbers:
            return None
        order = {old: new for new, old in enumerate(numbers)}
        rules = [[kind, order[a], order[b] if b >= 0 else -1, value, *rest]
                 for kind, a, b, value, *rest in self.constraints
                 if kind not in (cs.FIX, cs.SYMMETRY) and a in order and (b < 0 or b in order)]
        return {"entities": [list(self.entities[index]) for index in numbers], "constraints": rules,
                "construction": [order[index] for index in numbers if index in self.construction]}

    def paste_data(self, data: dict, dx: float, dy: float) -> None:
        """Fügt kopierte Elemente um (dx, dy) versetzt ein und wählt sie aus."""
        base = len(self.entities)
        self.entities.extend(_shifted(entity, dx, dy) for entity in data["entities"])
        self.constraints.extend([kind, a + base, b + base if b >= 0 else -1, value, *rest]
                                for kind, a, b, value, *rest in data["constraints"])
        self.construction.extend(index + base for index in data["construction"])
        self.selection = set(range(base, len(self.entities)))
        self.selected = base
        self.message = f"{len(data['entities'])} Element(e) eingefügt – zum Verschieben an einem davon ziehen"
        self.changed.emit()
        self.update()

    def select_all(self) -> None:
        self.selection = set(range(len(self.entities)))
        self.selected = 0 if self.entities else None
        self.changed.emit()
        self.update()

    def select_chain(self, x: float, y: float) -> None:
        """Doppelklick mit „Auswählen“ wählt den ganzen zusammenhängenden Linienzug (wie Fusion)."""
        index = self._entity_at(x, y)
        if index is None:
            return
        self.selection = {number for number, _backwards in sk.chain_of(self.entities, index)}
        self.selected = index
        self.changed.emit()
        self.update()

    def delete_selected(self) -> None:
        if getattr(self, "selected_rule", None) is not None:
            # ein angeklicktes Abhängigkeitssymbol löscht Entf (wie in Fusion)
            number, self.selected_rule = self.selected_rule, None
            self.remove_constraint(number)
            return
        numbers = self.chosen()
        if numbers:
            for index in reversed(numbers):     # von hinten, damit die Nummern davor gültig bleiben
                self._remove_entity(index)
            self.selected = None
            self.selection = set()
            self._first_pick = None
            self.changed.emit()
            self.update()

    def _remove_entity(self, index: int) -> None:
        """Löscht ein Element; Maße, Abhängigkeiten und Konstruktionsmarken zählen neu."""
        del self.entities[index]
        self.constraints[:] = cs.without_entity(self.constraints, index)
        self.construction[:] = [i - (i > index) for i in self.construction if i != index]
        self.centerlines[:] = [i - (i > index) for i in self.centerlines if i != index]
        self.projected[:] = [i - (i > index) for i in self.projected if i != index]

    def _edit_spline_point(self, x: float, y: float) -> None:
        """Strg+Klick mit dem Spline-Werkzeug: auf einem Punkt entfernt ihn, auf der Kurve fügt einen ein."""
        reach = self._px(_SNAP_PIXELS)
        for index, entity in enumerate(self.entities):
            if entity[0] != sk.SPLINE:
                continue
            points = [(entity[i], entity[i + 1]) for i in range(1, len(entity), 2)]
            number = min(range(len(points)), key=lambda n: math.dist(points[n], (x, y)))
            if math.dist(points[number], (x, y)) <= reach:
                shorter = sk.spline_without_point(entity, number)
                if shorter is None:
                    self.message = "Ein Spline braucht mindestens zwei Punkte"
                else:
                    entity[:] = shorter
                    self.message = "Punkt entfernt"
                self.changed.emit()
                return
        index = self._entity_at(x, y)
        if index is not None and self.entities[index][0] == sk.SPLINE:
            self.entities[index][:] = sk.spline_with_point(self.entities[index], x, y)
            self.message = "Punkt eingefügt – mit „Auswählen“ lässt er sich ziehen"
        else:
            self.message = "Strg+Klick: auf einen Spline-Punkt (entfernen) oder auf die Kurve (Punkt einfügen)"
        self.changed.emit()

    def _three_point_corners(self, x: float, y: float) -> list | None:
        """Ecken des 3-Punkt-Rechtecks: die ersten beiden Punkte bilden eine Seite, der dritte bestimmt die Breite."""
        (ax, ay), (bx, by) = self._points[0], self._points[1]
        length = math.dist((ax, ay), (bx, by))
        if length < 1e-9:
            return None
        nx, ny = -(by - ay) / length, (bx - ax) / length
        width = (x - ax) * nx + (y - ay) * ny
        if abs(width) < 1e-9:
            return None
        return [(ax, ay), (bx, by), (bx + nx * width, by + ny * width), (ax + nx * width, ay + ny * width)]

    def _ending_at(self, point) -> int | None:
        """Das zuletzt gezeichnete Linien- oder Bogen-Element, das an diesem Punkt endet oder beginnt."""
        for index in range(len(self.entities) - 1, -1, -1):
            ends = sk._ends(self.entities[index]) if self.entities[index][0] in ("Linie", "Bogen") else None
            if ends and any(math.dist(end, point) < 1e-6 for end in ends):
                return index
        return None

    def _tangent_arc_to(self, start, end) -> list | None:
        """Bogen, der am Startpunkt tangential an das dort endende Element anschließt."""
        source = self._ending_at(start)
        if source is None:
            return None
        entity = self.entities[source]
        a, b = sk._ends(entity)
        if entity[0] == "Linie":
            # Laufrichtung der Linie zum Startpunkt hin
            direction = (b[0] - a[0], b[1] - a[1]) if math.dist(b, start) < 1e-6 else (a[0] - b[0], a[1] - b[1])
        else:
            # Bogen: Tangente am Ende, in Laufrichtung (gegen den Uhrzeigersinn am Ende, mit ihm am Anfang)
            angle = math.radians(entity[5] if math.dist(b, start) < 1e-6 else entity[4])
            sign = 1.0 if math.dist(b, start) < 1e-6 else -1.0
            direction = (-math.sin(angle) * sign, math.cos(angle) * sign)
        return sk.tangent_arc(start, direction, end)

    def _slot_clicks(self) -> int:
        """Klicks des Langlochs je Art (der letzte legt die Breite fest)."""
        return 4 if self.slot_kind in sk.SLOT_KINDS[3:] else 3

    def _slot_to(self, x: float, y: float) -> list:
        """Das Langloch aus den gesetzten Punkten; (x, y) legt die halbe Breite fest (Arten wie Fusion)."""
        kind = self.slot_kind
        if kind in sk.SLOT_KINDS[3:]:
            if len(self._points) < 3:
                return []
            if kind == sk.SLOT_KINDS[3]:
                # 3-Punkt-Bogen (Fusion: erst die beiden Endmitten, dann ein Punkt auf dem Bogen)
                centre_line = sk.arc_through(self._points[0], self._points[2], self._points[1])
                if centre_line is None:
                    return []
                cx, cy, radius, start, end = centre_line[1:6]
            else:
                # Mittelpunkt-Bogen: Mitte, Anfang (Radius), Ende (Winkel)
                (cx, cy), begin, finish = self._points[:3]
                radius = math.dist((cx, cy), begin)
                start, end = _angle((cx, cy), begin), _angle((cx, cy), finish)
            half = abs(math.dist((cx, cy), (x, y)) - radius)
            return sk.arc_slot(cx, cy, radius, start, end, half) if radius > 1e-9 else []
        (ax, ay), (bx, by) = self._points[0], self._points[1]
        length = math.dist((ax, ay), (bx, by))
        if length < 1e-9:
            return []
        radius = abs((bx - ax) * (y - ay) - (by - ay) * (x - ax)) / length     # Abstand zur Achse
        if radius <= 1e-9:
            return []
        if kind == sk.SLOT_KINDS[1]:
            return sk.slot_overall(ax, ay, bx, by, radius)         # Klicks sind die äußeren Enden
        if kind == sk.SLOT_KINDS[2]:
            # Mittelpunkt: erster Klick die Mitte, zweiter eine Endmitte – die andere liegt gespiegelt
            return sk.slot(2 * ax - bx, 2 * ay - by, bx, by, radius)
        return sk.slot(ax, ay, bx, by, radius)

    def _polygon_shape(self, points: list, cursor) -> list:
        """Vieleck nach der eingestellten Art aus den gesetzten Punkten und der Maus."""
        sides = int(self.polygon_sides)
        if self.polygon_kind == sk.POLYGON_KINDS[2]:
            if len(points) < 2:
                return []
            return sk.polygon_edge(*points[0], *points[1], *cursor, sides)
        if math.dist(points[0], cursor) < 1e-9:
            return []
        if self.polygon_kind == sk.POLYGON_KINDS[1]:
            return sk.polygon_circumscribed(*points[0], *cursor, sides)
        return sk.polygon(*points[0], *cursor, sides)

    def _add_polygon(self, sides: list, inside: bool = False) -> None:
        """Legt ein Vieleck an, das ein Konstruktionskreis regelmäßig hält: durch die Ecken – oder beim
        umschriebenen Vieleck (`inside`) innen an allen Seiten anliegend, wie in Fusion."""
        if not sides:
            return
        count = len(self.entities)
        corners = [entity[1:3] for entity in sides]
        centre = (sum(c[0] for c in corners) / len(corners), sum(c[1] for c in corners) / len(corners))
        self.entities.extend(sides)
        if inside:
            middle = ((sides[0][1] + sides[0][3]) / 2, (sides[0][2] + sides[0][4]) / 2)
            self.entities.append(sk.circle(*centre, math.dist(centre, middle)))
        else:
            self.entities.append(sk.circle(*centre, math.dist(centre, corners[0])))
        rim = len(self.entities) - 1
        self.construction.append(rim)
        if inside:
            self.constraints += [[cs.TANGENT, count + n, rim, 0.0] for n in range(len(sides))]
        else:
            self.constraints += [[cs.POINT_ON, count + n, rim, 0.0] for n in range(len(sides))]
        self.constraints += [[cs.EQUAL, count, count + n, 0.0] for n in range(1, len(sides))]
        self.changed.emit()

    def _tangent_circle_click(self, x: float, y: float) -> None:
        """Kreis an 2 bzw. 3 Tangenten (Fusion): Linien anklicken; der letzte Klick wählt Lage (und Radius)."""
        wanted = 2 if self.tool == TWO_TANGENT_CIRCLE else 3
        if len(self._tangent_lines) < wanted:
            index = self._entity_at(x, y)
            if index is None or self.entities[index][0] != "Linie" or index in self._tangent_lines:
                self.message = f"{self.tool}: eine (weitere) Linie anklicken"
                self.changed.emit()
                return
            self._tangent_lines.append(index)
            self.message = (f"{self.tool}: noch {wanted - len(self._tangent_lines)} Linie(n)"
                            if len(self._tangent_lines) < wanted else
                            f"{self.tool}: Stelle des Kreises anklicken")
            self.changed.emit()
            return
        found = self._tangent_circle_at(x, y)
        lines, self._tangent_lines = self._tangent_lines, []
        if found is None:
            self.message = f"{self.tool}: hier passt kein Kreis an diese Linien"
            self.changed.emit()
            return
        self.entities.append(sk.circle(*found))
        new = len(self.entities) - 1
        for index in lines:
            rule = [cs.TANGENT, index, new, 0.0]
            if cs.check(self.entities, rule) is None:
                self.constraints.append(rule)
        self.message = ""
        self.changed.emit()

    def _tangent_circle_at(self, x: float, y: float):
        """Der Tangentenkreis, dessen Mitte der Maus am nächsten liegt (2 Tangenten: Radius = Mausabstand)."""
        lines = [self.entities[index] for index in self._tangent_lines]
        if self.tool == TWO_TANGENT_CIRCLE:
            radius = _distance_to(sk.line(*lines[0][1:5]), x, y)
            # Radius: Abstand der Maus zur ersten Linie (wie Ziehen in Fusion), mindestens ein kleiner Wert
            options = sk.tangent_circles_two(lines[0], lines[1], max(radius, 1e-3))
        else:
            options = sk.tangent_circles_three(lines)
        return min(options, key=lambda c: math.dist(c[:2], (x, y)), default=None)

    # --- Elemente per Klick verändern: Abrunden, Stutzen, Konstruktion -------

    def _edit_at(self, x: float, y: float) -> None:
        """Wendet das aktive Änderungswerkzeug an der Stelle (x, y) an."""
        before = ([list(entity) for entity in self.entities], [list(rule) for rule in self.constraints],
                  list(self.construction))
        if self.tool == CONSTRUCTION_TOOL:
            index = self._entity_at(x, y)
            if index is not None:
                # Umschalten zwischen gewöhnlichem Element und Konstruktionselement
                if index in self.construction:
                    self.construction.remove(index)
                else:
                    self.construction.append(index)
                self.message = ""
                self.changed.emit()
            return
        if self.tool == PROJECT_TOOL:
            # Körperkanten werden in der 3D-Ansicht getroffen; das erledigt die Skizzensitzung
            return
        actions = {FILLET_TOOL: self._fillet_at, TRIM_TOOL: self._trim_at, MIRROR_TOOL: self._mirror_at,
                   CHAMFER_TOOL: self._chamfer_at, SCALE_TOOL: self._scale_at, BLEND_TOOL: self._blend_at,
                   MOVE_TOOL: self._move_at,
                   EXTEND_TOOL: self._extend_at, BREAK_TOOL: self._break_at,
                   OFFSET_TOOL: self._offset_at, PATTERN_TOOL: self._pattern_at, CIRCULAR_TOOL: self._pattern_at}
        problem = actions[self.tool](x, y)
        if problem is None and self.constraints and not self.apply_constraints():
            problem = f"{self.tool} widerspricht den vorhandenen Maßen und Abhängigkeiten"
        if problem:
            # Alles zurück auf den Stand vor dem Klick
            self.entities[:], self.constraints[:], self.construction[:] = before
        if problem or not self.message.startswith((MIRROR_TOOL, BLEND_TOOL, MOVE_TOOL)) and "Muster" not in self.message:
            self.message = problem or ""
        self.changed.emit()

    def _extend_at(self, x: float, y: float) -> str | None:
        """Verlängert die angeklickte Linie am nahen Ende bis zum nächsten Element."""
        index = self._entity_at(x, y)
        if index is None or self.entities[index][0] != "Linie":
            return "Verlängern: eine Linie nahe dem Ende anklicken, das verlängert werden soll"
        longer = sk.extended_line(self.entities, index, x, y)
        if longer is None:
            return "Verlängern: in dieser Richtung liegt kein Element, an das die Linie stoßen könnte"
        self.entities[index][:] = longer
        return None

    def _break_at(self, x: float, y: float) -> str | None:
        """Teilt das angeklickte Element an allen Stellen, an denen andere es schneiden."""
        index = self._entity_at(x, y)
        if index is None or self.entities[index][0] not in ("Linie", "Kreis", "Bogen"):
            return "Brechen: eine Linie, einen Kreis oder Bogen anklicken"
        pieces = sk.broken(self.entities, index)
        if len(pieces) < 2:
            return "Brechen: das Element wird von nichts geschnitten"
        # Maße am alten Element passen nicht mehr zu den Stücken: sie entfallen
        self.constraints[:] = [rule for rule in self.constraints if index not in rule[1:3]]
        self.entities[index][:] = pieces[0]
        for piece in pieces[1:]:
            self.entities.append(piece)
            if index in self.construction:
                self.construction.append(len(self.entities) - 1)
        self.message = f"In {len(pieces)} Stücke geteilt"
        return None

    def add_entities(self, entities: list, projected: bool = True) -> int:
        """Nimmt fertige Elemente auf (z. B. projizierte Körperkanten); gibt zurück, wie viele neu waren.

        Projizierte Elemente werden wie in Fusion fest verankert (FIX) und lila gezeigt; sie zählen zum Profil."""
        added = 0
        for entity in entities:
            known = any(other[0] == entity[0] and all(abs(a - b) < 1e-6 for a, b in zip(other[1:], entity[1:]))
                        for other in self.entities)
            if not known and sk.check_entity(entity) is None:
                self.entities.append(entity)
                if projected:
                    index = len(self.entities) - 1
                    self.projected.append(index)
                    self.constraints.append([cs.FIX, index, -1, 0.0])
                added += 1
        self.message = f"{added} Element(e) projiziert" if added else "Dort gab es nichts Neues zu projizieren"
        self.changed.emit()
        return added

    def _pattern_at(self, x: float, y: float) -> str | None:
        """Vervielfältigt den Linienzug des angeklickten Elements im Raster oder auf einem Kreis."""
        index = self._entity_at(x, y)
        if index is None:
            return f"{self.tool}: ein Element anklicken – sein ganzer Linienzug wird vervielfältigt"
        members = [number for number, _backwards in sk.chain_of(self.entities, index)]
        copies = []
        if self.tool == PATTERN_TOOL:
            nx, ny = int(self.pattern["nx"]), int(self.pattern["ny"])
            if nx * ny < 2:
                return "Muster: die Anzahl muss insgesamt mindestens 2 sein"
            # neue Mustergruppe: an ihr lässt sich der Abstand später für alle Kopien zugleich ändern
            group = 1 + max((cs.pattern_place(rule)[0] for rule in self.constraints if rule[0] == cs.TRANSLATED), default=0)
            for row in range(ny):
                for column in range(nx):
                    if row or column:
                        shift = (column * self.pattern["dx"], row * self.pattern["dy"])
                        copies += [(number, _shifted(self.entities[number], *shift),
                                    [cs.TRANSLATED, number, -1, cs.pattern_code(group, column, row), "",
                                     float(shift[0]), float(shift[1])])
                                   for number in members]
        else:
            count = int(self.pattern["count"])
            if count < 2:
                return "Kreismuster: die Anzahl muss mindestens 2 sein"
            for step in range(1, count):
                turn = 360.0 * step / count
                copies += [(number, sk.rotated(self.entities[number], self.pattern["cx"], self.pattern["cy"], turn),
                            [cs.ROTATED, number, -1, turn, "", float(self.pattern["cx"]), float(self.pattern["cy"])])
                           for number in members]
        for number, copy, link in copies:
            self.entities.append(copy)
            if number in self.construction:
                self.construction.append(len(self.entities) - 1)
            # wie in Fusion bleibt die Kopie mit dem Original verknüpft: ändert es sich, zieht sie mit
            link[2] = len(self.entities) - 1
            if cs.check(self.entities, link) is None:
                self.constraints.append(link)
        self.message = f"{len(copies)} Element(e) als Muster angelegt (mit dem Original verknüpft)"
        return None

    def _mirror_at(self, x: float, y: float) -> str | None:
        """Erster Klick wählt die Spiegelachse (eine Linie), jeder weitere spiegelt ein Element daran."""
        index = self._entity_at(x, y)
        if index is None:
            return "Spiegeln: erst die Spiegelachse (eine Linie) anklicken, dann die Elemente"
        if self._mirror_axis is None or self._mirror_axis >= len(self.entities):
            if self.entities[index][0] != "Linie":
                return "Spiegeln: die Spiegelachse muss eine Linie sein"
            self._mirror_axis = self.selected = index
            self.message = "Spiegeln: jetzt die Elemente anklicken, die gespiegelt werden sollen"
            return None
        if index == self._mirror_axis:
            return None
        self.entities.append(sk.mirrored(self.entities[index], self.entities[self._mirror_axis]))
        if index in self.construction:
            self.construction.append(len(self.entities) - 1)
        link = [cs.SYMMETRY, index, len(self.entities) - 1, float(self._mirror_axis)]
        if cs.check(self.entities, link) is None:
            # Die Kopie bleibt das Spiegelbild: ändert sich das Original, zieht sie mit (auch bei Splines)
            self.constraints.append(link)
        self.message = "Spiegeln: weitere Elemente anklicken oder ein anderes Werkzeug wählen"
        return None

    def _offset_at(self, x: float, y: float) -> str | None:
        """Versetzt den Linienzug des angeklickten Elements zur Seite des Klicks."""
        reach = self._px(4 * _PICK_PIXELS)      # großzügig: man klickt neben das Element, auf die Zielseite
        distances = [(_distance_to(entity, x, y), index) for index, entity in enumerate(self.entities)
                     if entity[0] not in (sk.POINT, *sk.SPATIAL)]    # Punkte und 3D-Elemente lassen sich nicht versetzen
        nearest = min(distances, default=(math.inf, None))
        if nearest[1] is None or nearest[0] > reach:
            return "Versatz: neben ein Element klicken – auf die Seite, auf der die Kopie liegen soll"
        chain = [number for number, _backwards in sk.chain_of(self.entities, nearest[1])]
        result = sk.offset_chain(self.entities, nearest[1], self.offset_distance, (x, y))
        if result is None:
            return "Versatz: mit diesem Abstand würde ein Bogen verschwinden"
        start = len(self.entities)
        kept = [entity for entity in result if sk.check_entity(entity) is None]
        self.entities.extend(kept)
        if len(kept) == len(chain):
            # Versatzkopien bleiben verknüpft: Linien parallel im Versatzabstand, Bögen und Kreise konzentrisch
            for number, copy in zip(chain, range(start, start + len(kept))):
                original = self.entities[number]
                if original[0] == "Linie" and self.entities[copy][0] == "Linie":
                    links = [[cs.PARALLEL, number, copy, 0.0], [cs.DISTANCE, number, copy, float(self.offset_distance)]]
                elif original[0] in ("Kreis", "Bogen", sk.ELLIPSE) and self.entities[copy][0] == original[0]:
                    links = [[cs.CONCENTRIC, number, copy, float(self.entities[copy][3] - original[3])]]
                else:
                    links = []
                self.constraints += [link for link in links if cs.check(self.entities, link) is None]
        return None

    def _fillet_at(self, x: float, y: float) -> str | None:
        """Rundet die Ecke nahe (x, y) ab; gibt bei Misserfolg die Meldung zurück."""
        reach = self._px(2 * _SNAP_PIXELS)
        ends = [(index, sk._ends(entity)[slot]) for index, entity in enumerate(self.entities)
                if entity[0] in ("Linie", "Bogen") for slot in (0, 1)]
        best = None
        for index, point in ends:
            partners = [other for other, place in ends if other != index and math.dist(place, point) < 1e-6]
            distance = math.dist(point, (x, y))
            if len(partners) == 1 and distance <= reach and (best is None or distance < best[0]):
                best = (distance, index, partners[0])
        if best is None:
            return "Abrunden: eine Ecke anklicken, an der sich genau zwei Linien oder Bögen treffen"
        _distance, first, second = best
        result = sk.corner_fillet(self.entities[first], self.entities[second], self.fillet_radius)
        if result is None:
            return "Abrunden: der Radius passt nicht in diese Ecke (oder der Übergang ist schon glatt)"
        self.entities[first][:], self.entities[second][:] = result[0], result[1]
        self.entities.append(result[2])
        arc = len(self.entities) - 1
        # Der Bogen bleibt an den Linien: berührend, mit bemaßtem Radius (wie in Fusion)
        self.constraints += [[cs.TANGENT, first, arc, 0.0], [cs.TANGENT, second, arc, 0.0],
                             [cs.RADIUS, arc, -1, float(self.fillet_radius)]]
        self._drop_collapsed([first, second])
        return None

    def _drop_collapsed(self, indices: list[int]) -> None:
        """Entfernt Linien, die eine Rundung oder Fase auf Länge 0 gekürzt hat (z. B. Quadrat → Kreis). Was an der
        Linie tangential hing (die Bögen davor und danach), bleibt untereinander tangential."""
        for index in sorted(indices, reverse=True):
            entity = self.entities[index]
            if entity[0] != "Linie" or math.dist(entity[1:3], entity[3:5]) > 1e-9:
                continue
            partners = [rule[2] if rule[1] == index else rule[1] for rule in self.constraints
                        if rule[0] == cs.TANGENT and index in (rule[1], rule[2])]
            self.constraints += [[cs.TANGENT, a, b, 0.0] for k, a in enumerate(partners) for b in partners[k + 1:]]
            self._remove_entity(index)

    def _hover_preview(self, x: float, y: float) -> tuple[list, str] | None:
        """Vorschau beim Überfahren wie in Fusion: Stutzen zeigt das Stück, das wegfällt, Verlängern die
        verlängerte Linie, Brechen die Stellen, an denen geteilt wird. None, wenn es nichts zu zeigen gibt."""
        if self.tool not in (TRIM_TOOL, EXTEND_TOOL, BREAK_TOOL):
            return None
        index = self._entity_at(x, y)
        if index is None:
            return None
        entity = self.entities[index]
        if self.tool == EXTEND_TOOL:
            longer = sk.extended_line(self.entities, index, x, y) if entity[0] == "Linie" else None
            return ([longer], "Verlängern: Klick verlängert bis hierhin") if longer else None
        if self.tool == BREAK_TOOL:
            if entity[0] not in ("Linie", "Kreis", "Bogen"):
                return None
            pieces = sk.broken(self.entities, index)
            if len(pieces) < 2:
                return [], "Brechen: das Element wird von nichts geschnitten"
            size = self._px(4 * _SNAP_PIXELS) / 3
            marks = []
            for piece in pieces:
                for px, py in sk._ends(piece) or ():
                    marks += [sk.line(px - size, py - size, px + size, py + size),
                              sk.line(px - size, py + size, px + size, py - size)]
            return marks, f"Brechen: Klick teilt in {len(pieces)} Stücke"
        piece = self._trim_piece(index, x, y)
        return ([piece], "Stutzen: Klick entfernt das gezeigte Stück") if piece is not None else None

    def _trim_piece(self, index: int, x: float, y: float) -> list | None:
        """Das Stück, das „Stutzen“ an dieser Stelle entfernen würde (zwischen den nächsten Schnittstellen)."""
        entity = self.entities[index]
        if entity[0] == "Linie":
            ax, ay, bx, by = entity[1:5]
            length = (bx - ax) ** 2 + (by - ay) ** 2
            clicked = ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length
            cuts = sk.line_cuts(self.entities, index)
            lower = max((t for t in cuts if t < clicked), default=0.0)
            upper = min((t for t in cuts if t > clicked), default=1.0)
            return sk.line(ax + (bx - ax) * lower, ay + (by - ay) * lower, ax + (bx - ax) * upper, ay + (by - ay) * upper)
        if entity[0] in ("Kreis", "Bogen"):
            angle = _angle(tuple(entity[1:3]), (x, y))
            cuts = sorted(sk.round_cuts(self.entities, index))
            if entity[0] == "Bogen":
                cuts = sorted({entity[4] % 360, entity[5] % 360, *cuts})
            if not cuts:
                return list(entity)             # ungeschnittener Kreis: er fiele ganz weg
            # nächste Schnittstelle gegen bzw. mit dem Uhrzeigersinn um die Mausstellung
            after = min(cuts, key=lambda c: (c - angle) % 360)
            before = min(cuts, key=lambda c: (angle - c) % 360)
            if abs((after - before) % 360) < 1e-9:
                return list(entity)
            return sk.arc(entity[1], entity[2], entity[3], before, after)
        return list(entity)                     # übrige Kurven: als Ganzes markiert

    def _corner_at(self, x: float, y: float, kinds: tuple) -> tuple[int, int] | None:
        """Die Ecke nahe (x, y), an der genau zwei Elemente der Arten `kinds` zusammenstoßen."""
        reach = self._px(2 * _SNAP_PIXELS)
        ends = [(index, sk._ends(entity)[slot]) for index, entity in enumerate(self.entities)
                if entity[0] in kinds for slot in (0, 1)]
        best = None
        for index, point in ends:
            partners = [other for other, place in ends if other != index and math.dist(place, point) < 1e-6]
            distance = math.dist(point, (x, y))
            if len(partners) == 1 and distance <= reach and (best is None or distance < best[0]):
                best = (distance, index, partners[0])
        return (best[1], best[2]) if best else None

    def _chamfer_at(self, x: float, y: float) -> str | None:
        """Fase an der Ecke zweier Linien (Arten wie Fusion); die Fase bleibt an beiden Linien."""
        corner = self._corner_at(x, y, ("Linie",))
        if corner is None:
            return "Fase: eine Ecke anklicken, an der sich genau zwei Linien treffen"
        first, second = corner
        result = sk.corner_chamfer(self.entities[first], self.entities[second], self.chamfer_kind,
                                   self.chamfer_distance, self.chamfer_distance2, self.chamfer_angle)
        if result is None:
            return "Fase: Abstand bzw. Winkel passen nicht in diese Ecke"
        self.entities[first][:], self.entities[second][:] = result[0], result[1]
        self.entities.append(result[2])
        self._drop_collapsed([first, second])
        return None

    def _scale_at(self, x: float, y: float) -> str | None:
        """Skizze skalieren (Fusion „Sketch Scale“): die gewählten Elemente – ohne Auswahl alle – um den
        angeklickten Punkt mit dem Faktor vergrößern; Längenmaße wachsen mit, Winkel bleiben."""
        factor = float(self.scale_factor)
        if factor <= 0:
            return "Skizze skalieren: der Faktor muss größer als 0 sein"
        chosen = set(self.chosen()) or set(range(len(self.entities)))
        for index in chosen:
            entity = self.entities[index]
            roles = cs._value_roles(entity)
            for position, role in enumerate(roles, start=1):
                if role in "xy":
                    base = x if role == "x" else y
                    entity[position] = base + (entity[position] - base) * factor
                elif role == "s" and not (entity[0] in (sk.CONIC, sk.CP_SPLINE) and position == len(entity) - 1):
                    entity[position] *= factor          # Radien, Halbachsen (nicht aber Rho oder Grad)
        for rule in self.constraints:
            if rule[0] in cs.DIMENSIONS and rule[0] not in (cs.ANGLE, cs.DIRECTION) and rule[1] in chosen:
                rule[3] *= factor
                if len(rule) > 4 and rule[4]:
                    rule[4] = ""                         # ein Ausdruck passte nicht mehr zum neuen Wert
        return None

    def _blend_at(self, x: float, y: float) -> str | None:
        """Übergangskurve (Fusion „Blend Curve“, G1): erst nahe dem Ende der ersten Kurve klicken, dann nahe dem
        Ende der zweiten – dazwischen entsteht ein Spline, der beide tangential fortsetzt."""
        reach = self._px(2 * _SNAP_PIXELS)
        ends = [(math.dist(end, (x, y)), index, slot == 1) for index, entity in enumerate(self.entities)
                if sk._ends(entity) is not None for slot, end in enumerate(sk._ends(entity))]
        best = min(ends, default=None)
        if best is None or best[0] > reach:
            return "Übergangskurve: nahe an das Ende einer Kurve klicken"
        if self._blend_first is None or self._blend_first[0] == best[1]:
            self._blend_first = best[1:]
            self.message = "Übergangskurve: jetzt nahe an das Ende der zweiten Kurve klicken"
            return None
        (first, first_end), self._blend_first = self._blend_first, None
        make = sk.blend_curve_g2 if self.blend_g2 else sk.blend_curve
        curve = make(self.entities[first], first_end, self.entities[best[1]], best[2])
        if curve is None or sk.check_entity(curve) is not None:
            return "Übergangskurve: zwischen diesen Enden lässt sich keine Kurve legen"
        self.entities.append(curve)
        new = len(self.entities) - 1
        for other in (first, best[1]):
            # G2: „Krümmung“ (schließt die Tangente ein) an Linien und Bögen; sonst tangential
            kind = cs.CURVATURE if self.blend_g2 and self.entities[other][0] in ("Linie", "Bogen") else cs.TANGENT
            rule = [kind, other, new, 0.0]
            if cs.check(self.entities, rule) is None:
                self.constraints.append(rule)
        return None

    def _move_at(self, x: float, y: float) -> str | None:
        """Verschieben/Kopieren (Fusion „Move/Copy“): erst Basispunkt, dann Zielpunkt anklicken – die gewählten
        Elemente wandern um diesen Weg (mit „Kopie“ entsteht eine verschobene Kopie)."""
        chosen = self.chosen()
        if not chosen:
            return "Verschieben/Kopieren: zuerst mit „Auswählen“ Elemente wählen"
        if self._move_from_point is None:
            self._move_from_point = (x, y)
            self.message = "Verschieben/Kopieren: jetzt den Zielpunkt anklicken"
            return None
        (bx, by), self._move_from_point = self._move_from_point, None
        dx, dy = x - bx, y - by
        if self.move_copy:
            start = len(self.entities)
            for index in chosen:
                self.entities.append(_shifted(self.entities[index], dx, dy))
                if index in self.construction:
                    self.construction.append(len(self.entities) - 1)
            self.selection = set(range(start, len(self.entities)))
            self.selected = start
            return None
        for index in chosen:
            self.entities[index][:] = _shifted(self.entities[index], dx, dy)
        # feste Bezüge der verschobenen Elemente gelten an der neuen Stelle nicht mehr
        self.constraints[:] = [rule for rule in self.constraints if not (rule[0] == cs.FIX and rule[1] in chosen)]
        return None

    def _trim_at(self, x: float, y: float) -> str | None:
        """Entfernt das angeklickte Stück einer Linie bis zu den nächsten Schnittpunkten."""
        index = self._entity_at(x, y)
        if index is not None and self.entities[index][0] in (sk.POINT, *sk.SPATIAL):
            self._remove_entity(index)          # einen Punkt (oder ein 3D-Element) stutzen heißt: ihn entfernen
            return None
        if index is None:
            return "Stutzen: das Stück einer Linie, eines Kreises oder Bogens anklicken, das weg soll"
        entity = self.entities[index]
        if entity[0] == sk.CONIC:
            # gestutzt wird ein Kegelschnitt als Spline durch Punkte seiner Kurve (die Form bleibt)
            entity[:] = sk.conic_as_spline(entity, 17)
        elif entity[0] == sk.CP_SPLINE:
            entity[:] = sk.spline(*sk.control_spline_points(entity, 16))     # ebenso der Steuerpunkt-Spline
        if entity[0] == sk.SPLINE:
            kept = sk.trim_spline(self.entities, index, x, y)
            if not kept:
                self._remove_entity(index)
                return None
            entity[:] = kept[0]
            if len(kept) > 1:
                self.entities.append(kept[1])
                if index in self.construction:
                    self.construction.append(len(self.entities) - 1)
            return None
        if entity[0] in ("Kreis", "Bogen"):
            return self._trim_round(index, x, y)
        if entity[0] == sk.ELLIPSE:
            # Ellipse oder Ellipsenbogen: das angeklickte Stück zwischen zwei Schnittstellen fällt weg
            kept = sk.trim_ellipse(entity, sk.ellipse_cuts(self.entities, index), sk.ellipse_angle(entity, x, y))
            if not kept:
                self._remove_entity(index)
                return None
            entity[:] = kept[0]
            if len(kept) > 1:
                self.entities.append(kept[1])
                if index in self.construction:
                    self.construction.append(len(self.entities) - 1)
            return None
        ax, ay, bx, by = entity[1:5]
        length = (bx - ax) ** 2 + (by - ay) ** 2
        clicked = ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length
        cuts = sk.line_cuts(self.entities, index)
        lower = max((t for t in cuts if t < clicked), default=None)
        upper = min((t for t in cuts if t > clicked), default=None)

        def at(t: float) -> tuple[float, float]:
            return ax + (bx - ax) * t, ay + (by - ay) * t

        if lower is None and upper is None:
            self._remove_entity(index)          # nichts schneidet die Linie: sie fällt ganz weg
        elif lower is None:
            entity[1:3] = at(upper)
        elif upper is None:
            entity[3:5] = at(lower)
        else:
            # Die Mitte fällt weg: aus einer Linie werden zwei
            entity[3:5] = at(lower)
            self.entities.append(sk.line(*at(upper), bx, by))
            if index in self.construction:
                self.construction.append(len(self.entities) - 1)
        return None

    # --- Maße und Abhängigkeiten ------------------------------------------

    def _pick_for_constraint(self, index: int | None, x: float = 0.0, y: float = 0.0) -> None:
        """Ein Element wurde mit einem Maß- oder Abhängigkeitswerkzeug an der Stelle (x, y) angeklickt."""
        if index is None:
            self._first_pick = None
            self._dimension_first = None
            self.selected = None
            self.changed.emit()
            return
        self.selected = index
        if self.tool == DIMENSION_TOOL and self.entities[index][0] == "Linie" and self._point_slot(index, x, y) is None \
                and QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Umschalt + Linie: zu ihrer Mitte bemaßen (Fusion) – dort entsteht ein Konstruktionspunkt auf der Mitte
            line = self.entities[index]
            self.entities.append(sk.point((line[1] + line[3]) / 2, (line[2] + line[4]) / 2))
            index = len(self.entities) - 1
            self.construction.append(index)
            self.constraints.append([cs.MIDPOINT, index, self.selected, 0.0])
            x, y = self.entities[index][1:3]
            self.selected = index
        if self.tool == DIMENSION_TOOL and self._dimension_pair(index, x, y):
            return
        if self.tool in (cs.HORIZONTAL, cs.VERTICAL) and self._align_points(index, x, y):
            return
        if self.tool == DIMENSION_TOOL and cs.dimension_for(self.entities, index) is None \
                and self.entities[index][0] != sk.ELLIPSE:
            # z. B. Skizzenpunkt oder Spline: kein eigenes Maß – es wartet auf das zweite Element
            self._dimension_first = (index, self._point_slot(index, x, y), None, False)
            self.message = "Bemaßung: zweites Element oder zweiten Punkt anklicken"
            self.changed.emit()
            return
        if self.tool == DIMENSION_TOOL:
            number = next((n for n, rule in enumerate(self.constraints)
                           if rule[0] in cs.SINGLE_DIMENSIONS and rule[1] == index), None)
            is_new = number is None
            if self.entities[index][0] == sk.ELLIPSE:
                # Eine Ellipse hat zwei Maße: der erste Klick bemaßt die erste Halbachse, der zweite die zweite
                present = {rule[0] for rule in self.constraints if rule[1] == index and rule[0] in cs.SEMI_AXES}
                missing = [kind for kind in cs.SEMI_AXES if kind not in present]
                is_new = bool(missing)
                if missing:
                    self.constraints.append([missing[0], index, -1,
                                             float(self.entities[index][3 + cs.SEMI_AXES.index(missing[0])])])
                    number = len(self.constraints) - 1
            elif is_new:
                # Das Maß entsteht mit dem aktuellen Wert; geändert wird es im Eingabefeld am Maß
                self.constraints.append(cs.dimension_for(self.entities, index))
                number = len(self.constraints) - 1
            self.message = ""
            # Wie in Fusion kann der nächste Klick auf ein zweites Element daraus ein Abstands- bzw. Winkelmaß machen
            self._dimension_first = (index, self._point_slot(index, x, y), number, is_new)
            self.changed.emit()
            self._dimension_created(number, is_new)
        elif self.tool in cs.PAIRS:
            entity = self.entities[index]
            slot = min(range(cs.point_count(entity)), key=lambda n: math.dist(cs.point_of(entity, n), (x, y)))
            if (self.tool == cs.DISTANCE and self._first_pick == index and entity[0] == sk.SPLINE
                    and slot != self._first_slot):
                # zweiter Klick auf einen anderen Punkt desselben Splines: Abstand zwischen seinen Punkten
                self._first_pick = None
                self._pick_point_distance(index, self._first_slot, index, slot)
                return
            if self.tool == cs.SYMMETRY and self._first_pick is not None and self._first_pick != index \
                    and getattr(self, "_second_pick", None) is not None:
                # dritter Klick der Symmetrie: die Spiegelachse
                first, second = self._first_pick, self._second_pick
                self._first_pick = self._second_pick = None
                self.add_constraint([cs.SYMMETRY, first, second, float(index)])
                return
            if self.tool == cs.SYMMETRY and self._first_pick is not None and self._first_pick != index:
                self._second_pick = index
                self.message = "Symmetrie: jetzt die Spiegelachse (eine Linie) anklicken"
                self.changed.emit()
                return
            if self._first_pick is None or self._first_pick == index:
                self._first_pick = index
                self._second_pick = None
                # „Punkt auf Kurve“: der Punkt des Elements, der dem Klick am nächsten liegt
                entity = self.entities[index]
                self._first_slot = min(range(cs.point_count(entity)),
                                       key=lambda slot: math.dist(cs.point_of(entity, slot), (x, y)))
                self.message = f"{self.tool}: jetzt das zweite Element anklicken"
                self.changed.emit()
                return
            first, self._first_pick = self._first_pick, None
            if self.tool == cs.DISTANCE and sk.SPLINE in (self.entities[first][0], self.entities[index][0]):
                # Splines bemaßt man über ihre Punkte: Abstand zwischen den beiden angeklickten Punkten
                second_slot = min(range(cs.point_count(self.entities[index])),
                                  key=lambda slot: math.dist(cs.point_of(self.entities[index], slot), (x, y)))
                self._pick_point_distance(first, self._first_slot, index, second_slot)
            elif self.tool in (cs.DISTANCE, cs.ANGLE):
                self._pick_pair_dimension(first, index)
            elif self.tool in (cs.POINT_ON, cs.MIDPOINT):
                self.add_constraint([self.tool, first, index, float(self._first_slot)])
            elif self.tool == cs.COINCIDENT:
                # der Punkt des zweiten Elements, der dem Klick am nächsten liegt; liegt dort keiner, kommt der
                # erste Punkt auf die Kurve (wie Fusion: Deckungsgleich mit einer Kurve = Punkt auf Kurve)
                other = self.entities[index]
                second_slot = min(range(cs.point_count(other)), key=lambda n: math.dist(cs.point_of(other, n), (x, y)))
                if math.dist(cs.point_of(other, second_slot), (x, y)) <= self._px(2 * _SNAP_PIXELS):
                    self.add_constraint([cs.COINCIDENT, first, index, 0.0, "", self._first_slot, second_slot])
                else:
                    self.add_constraint([cs.POINT_ON, first, index, float(self._first_slot)])
            elif self.tool == cs.CONCENTRIC:
                self.add_constraint([cs.CONCENTRIC, first, index, 0.0, "", cs.FREE_RADIUS])
            else:
                self.add_constraint([self.tool, first, index, 0.0])
        elif self.tool == cs.FIX and [cs.FIX, index, -1, 0.0] in self.constraints:
            # Ein zweiter Klick löst die Fixierung wieder
            self.constraints.remove([cs.FIX, index, -1, 0.0])
            self.message = ""
            self.changed.emit()
        else:
            self.add_constraint([self.tool, index, -1, 0.0])

    def _entity_or_point_at(self, x: float, y: float) -> int | None:
        """Das Element unter dem Klick – oder, wenn keine Kurve getroffen ist, das Element, dessen Punkt (z. B. ein
        Kreismittelpunkt) dort liegt. So lassen sich Mittelpunkte anklicken wie in Fusion."""
        index = self._entity_at(x, y)
        if index is not None:
            return index
        reach = self._px(_SNAP_PIXELS)
        near = [(math.dist(cs.point_of(entity, slot), (x, y)), number) for number, entity in enumerate(self.entities)
                for slot in range(cs.point_count(entity))]
        best = min(near, default=(math.inf, None))
        return best[1] if best[0] <= reach else None

    def _dimension_created(self, number: int, is_new: bool) -> None:
        """Ein Maß ist angelegt. Wie Fusion: würde ein neues Maß die Skizze überbestimmen, wird es ein getriebenes
        Maß (zeigt nur den Wert); sonst öffnet sich sein Eingabefeld."""
        rule = self.constraints[number]
        if is_new and cs.is_redundant(self.entities, self.constraints[:number] + self.constraints[number + 1:], rule):
            self._set_driven(rule, True)        # ein zweiter Klick kann daraus noch ein Paarmaß machen
            self.message = "Das Maß würde die Skizze überbestimmen – es ist als getriebenes Maß (in Klammern) angelegt"
            self.changed.emit()
            self.update()
            return
        self.dimension_picked.emit(number, is_new)

    @staticmethod
    def _set_driven(rule: list, on: bool) -> None:
        """Markiert ein Maß als getrieben (bzw. wieder treibend); der Platz des Ausdrucks trägt die Marke."""
        if on:
            if len(rule) == 4:
                rule.append(cs.DRIVEN)
            else:
                rule[4] = cs.DRIVEN
        elif cs.is_driven(rule):
            rule[4] = ""

    def set_point_curvature(self, index: int, slot: int, radius: float) -> bool:
        """Krümmungsradius am Kurvenpunkt `slot` eines Griff-Splines festlegen (Fusion: Krümmungsgriff);
        ein schon vorhandener am selben Punkt bekommt den neuen Wert."""
        if not 0 <= index < len(self.entities):
            return False
        existing = next((n for n, rule in enumerate(self.constraints)
                         if rule[0] == cs.POINT_CURVATURE and rule[1] == index and rule[5] == slot), None)
        if existing is not None:
            return self.set_dimension(existing, radius)
        rule = [cs.POINT_CURVATURE, index, -1, float(radius), "", int(slot)]
        return self.add_constraint(rule)

    def toggle_driven(self, number: int) -> None:
        """Getrieben ↔ treibend (Fusion: Rechtsklick auf das Maß). Treibend geht nur, wenn es nichts überbestimmt."""
        if not 0 <= number < len(self.constraints) or self.constraints[number][0] not in cs.DIMENSIONS:
            return
        rule = self.constraints[number]
        if not cs.is_driven(rule):
            self._set_driven(rule, True)
            self.message = ""
        else:
            others = self.constraints[:number] + self.constraints[number + 1:]
            if cs.is_redundant(self.entities, others, [*rule[:4], "", *rule[5:]]):
                self.message = "Treibend würde dieses Maß die Skizze überbestimmen – es bleibt getrieben"
            else:
                self._set_driven(rule, False)
                self.message = "" if self.apply_constraints() else "Das Maß passt nicht zu den übrigen"
        self.changed.emit()
        self.update()

    def _place_point_dimension(self, x: float, y: float) -> None:
        """Zweiter Punkt gewählt, jetzt platziert der Klick das Maß (wie Fusion): ober- oder unterhalb der beiden
        Punkte → waagrechter Abstand, links oder rechts davon → senkrechter, sonst der direkte Abstand."""
        first, first_slot, second, second_slot = self._dimension_place
        self._dimension_place = None
        axis = self._placement_axis(first, first_slot, second, second_slot, x, y)
        self._pick_point_distance(first, first_slot, second, second_slot, axis)

    def _placement_axis(self, first, first_slot, second, second_slot, x, y) -> str:
        (ax, ay), (bx, by) = cs.point_of(self.entities[first], first_slot), cs.point_of(self.entities[second], second_slot)
        inside_x = min(ax, bx) <= x <= max(ax, bx)
        inside_y = min(ay, by) <= y <= max(ay, by)
        if inside_x and not inside_y:
            return "x"
        if inside_y and not inside_x:
            return "y"
        return ""

    def _pick_point_distance(self, first: int, first_slot: int, second: int, second_slot: int, axis: str = "") -> None:
        """Punktabstand (z. B. zwischen Spline-Punkten) mit dem aktuellen Wert anlegen; dann öffnet das Maßfeld.

        `axis` "x"/"y": nur der waagrechte bzw. senkrechte Abstand (Fusion: so platziertes Maß)."""
        number = next((n for n, rule in enumerate(self.constraints) if rule[0] == cs.POINT_DISTANCE
                       and {(rule[1], rule[5]), (rule[2], rule[6])} == {(first, first_slot), (second, second_slot)}
                       and (rule[7] if len(rule) > 7 else "") == axis), None)
        is_new = number is None
        if is_new:
            rule = [cs.POINT_DISTANCE, first, second, 0.0, "", first_slot, second_slot] + ([axis] if axis else [])
            value = cs.measured(self.entities, rule)
            rule[3] = value
            problem = cs.check(self.entities, rule)
            if problem is None and value < 1e-6:
                problem = "Die beiden Punkte liegen aufeinander – erst auseinanderziehen"
            if problem is not None:
                self.message = problem
                self.changed.emit()
                return
            self.constraints.append(rule)
            number = len(self.constraints) - 1
        self.message = ""
        self.changed.emit()
        self._dimension_created(number, is_new)

    def _align_points(self, index: int, x: float, y: float) -> bool:
        """Waagrecht/Senkrecht wie Fusion auch zwischen zwei Punkten: Klick auf einen Punkt, dann auf einen
        zweiten. True, wenn der Klick dazu gehörte (auf einen Punkt oder als zweiter Punkt)."""
        slot = self._point_slot(index, x, y)
        pending = getattr(self, "_align_first", None)
        if slot is None:
            self._align_first = None
            return False                    # Klick auf die Linie selbst: wie bisher die Linie ausrichten
        if pending is None or pending == (index, slot):
            self._align_first = (index, slot)
            self.message = f"{self.tool}: jetzt den zweiten Punkt anklicken"
            self.changed.emit()
            return True
        self._align_first = None
        kind = cs.ALIGN_H if self.tool == cs.HORIZONTAL else cs.ALIGN_V
        self.add_constraint([kind, pending[0], index, 0.0, "", pending[1], slot])
        return True

    def _point_slot(self, index: int, x: float, y: float) -> int | None:
        """Nummer des Punkts des Elements, auf den geklickt wurde (Ende, Mitte) – None, wenn keiner nah ist."""
        entity = self.entities[index]
        reach = self._px(_SNAP_PIXELS)
        slots = [(math.dist(cs.point_of(entity, slot), (x, y)), slot) for slot in range(cs.point_count(entity))]
        best = min(slots, default=(math.inf, None))
        return best[1] if best[0] <= reach else None

    def _dimension_pair(self, index: int, x: float, y: float) -> bool:
        """Zweiter Klick der Bemaßung auf ein anderes Element (wie Fusion): zwei Punkte → Punktabstand,
        zwei nicht parallele Linien → Winkel, sonst Abstand. Ein beim ersten Klick neu angelegtes Einzelmaß
        entfällt dann. True, wenn so ein Paarmaß entstand (oder versucht wurde)."""
        previous = getattr(self, "_dimension_first", None)
        if previous is None or previous[0] == index or previous[0] >= len(self.entities):
            return False
        first, first_slot, number, was_new = previous
        self._dimension_first = None
        if was_new and number is not None and 0 <= number < len(self.constraints):
            self.constraints.pop(number)        # das Einzelmaß des ersten Klicks war nur ein Zwischenschritt
        slot = self._point_slot(index, x, y)
        if first_slot is not None and slot is not None:
            # wie Fusion: das Maß folgt der Maus, der nächste Klick platziert es (waagrecht/senkrecht/direkt)
            self._dimension_place = (first, first_slot, index, slot)
            self.message = "Bemaßung: Maß platzieren – darüber/darunter waagrecht, daneben senkrecht"
            self.changed.emit()
            return True
        kind = cs.DISTANCE
        a, b = self.entities[first], self.entities[index]
        if self.dimension_rim and {"Kreis", "Bogen"} & {a[0], b[0]}:
            kind = cs.RIM_DISTANCE          # Fusion: Kreis über die Tangente (bis zum Rand) bemaßen
        elif a[0] == b[0] == "Linie":
            da, db = (a[3] - a[1], a[4] - a[2]), (b[3] - b[1], b[4] - b[2])
            cross = abs(da[0] * db[1] - da[1] * db[0]) / max(math.hypot(*da) * math.hypot(*db), 1e-12)
            kind = cs.DISTANCE if cross < 1e-6 else cs.ANGLE
        self._pick_pair_dimension(first, index, kind)
        return True

    def _pick_pair_dimension(self, first: int, second: int, kind: str | None = None) -> None:
        """Abstand oder Winkel zwischen zwei Elementen: Maß mit dem aktuellen Wert anlegen."""
        kind = kind or self.tool
        number = next((n for n, rule in enumerate(self.constraints)
                       if rule[0] == kind and {rule[1], rule[2]} == {first, second}), None)
        is_new = number is None
        if is_new:
            rule = [kind, first, second, 0.0]
            problem = cs.check(self.entities, [kind, first, second, 1.0])
            if problem is None:
                rule[3] = cs.measure(self.entities, kind, first, second)
                if rule[3] < 1e-6 or cs.check(self.entities, rule) is not None:
                    problem = f"{kind} ist hier gerade 0 – die Elemente erst auseinanderziehen"
            if problem is not None:
                self.message = problem
                self.changed.emit()
                return
            self.constraints.append(rule)
            number = len(self.constraints) - 1
        self.message = ""
        self.changed.emit()
        self._dimension_created(number, is_new)

    # --- Ziehen mit der Maus ----------------------------------------------

    def drag_start(self, x: float, y: float) -> bool:
        """Greift mit dem Werkzeug „Auswählen“, was bei (x, y) liegt: einen Punkt, den Rand eines
        Kreises, ein ganzes Element (samt Mehrfachauswahl) – oder beginnt im Leeren einen Auswahlrahmen.

        Punkte, die an derselben Stelle liegen, werden gemeinsam gegriffen – so bleiben
        Linienzüge beim Ziehen verbunden. True, wenn etwas gegriffen wurde.
        """
        self.drag_end()
        if self.tool == "Linie":
            return self._line_drag_start(x, y)
        if self.tool != "Auswählen":
            return False
        fixed = {rule[1] for rule in self.constraints if rule[0] == cs.FIX}
        reach = self._px(_SNAP_PIXELS)
        text = self.text_at(x, y)
        if text is not None and self._entity_at(x, y) is None:
            # Ein Text wird an seiner Fläche gegriffen und als Ganzes verschoben
            self.selected_text = text
            self._drag_text = (text, (x, y), tuple(self.texts[text][1:3]))
            self.changed.emit()
            return True
        # Ein angewählter Spline ohne Griffe zeigt seine Griffe trotzdem (wie in Fusion): wird eine Griffspitze
        # gegriffen, wird er erst hier zum Griff-Spline – danach zieht der Griff wie gewohnt
        for index in self.chosen():
            entity = self.entities[index]
            if entity[0] == sk.SPLINE and not sk.has_handles(entity) and index not in fixed:
                tips = sk.handle_tips(sk.with_handles(entity))
                backs = sk.back_tips(sk.with_handles(entity))
                if any(math.dist(tip, (x, y)) <= reach for tip in tips + backs):
                    self.set_spline_handles(index, True)
        for index, entity in enumerate(self.entities):
            # Das andere Ende eines Griffs (Gegengriff) ist beim gewöhnlichen Griff-Spline kein eigener Punkt:
            # wird es gegriffen, bekommt der Spline eigene Gegengriffe (je Seite unterschiedlich lang, wie Fusion).
            # Die Punktnummern von Kurvenpunkten und Griffen bleiben dabei gleich, die Gegengriffe kommen dahinter.
            if (entity[0] == sk.SPLINE and sk.has_handles(entity) and not sk.has_uneven_handles(entity)
                    and index not in fixed):
                for number, back in enumerate(sk.back_tips(entity)):
                    if math.dist(back, (x, y)) <= reach:
                        entity[:] = sk.with_back_tip(entity, number, *back)
                        break
        # Krümmungsgriff (Mitte des Schmiegekreises) greifen: Ziehen ändert den Krümmungsradius
        for number, point, normal, centre in self.curvature_grips():
            if math.dist(centre, (x, y)) <= reach and self.constraints[number][1] not in fixed:
                self._drag_curvature = (number, point, normal)
                return True
        located = [(index, slot, cs.point_of(entity, slot)) for index, entity in enumerate(self.entities)
                   for slot in range(cs.point_count(entity))]
        near = [item for item in located if math.dist(item[2], (x, y)) <= reach]
        index = self._entity_at(x, y)
        several = index is not None and index in self.selection and len(self.selection) > 1
        if near and not several:
            grabbed = min(near, key=lambda item: math.dist(item[2], (x, y)))[2]
            self._drag_points = [(number, slot) for number, slot, point in located
                                 if math.dist(point, grabbed) < 1e-6 and number not in fixed]
            return True     # auch wenn alles dort fixiert ist: dann bewegt sich eben nichts
        if index is not None and self.entities[index][0] == "Kreis" and not several:
            if index not in fixed:
                self._drag_radius = index
            return True
        if index is not None:
            # Ein ganzes Element wird verschoben – ist es Teil einer Mehrfachauswahl, alle gewählten.
            # Die Auswahl ändert sich erst, wenn wirklich gezogen wird (sonst ist es ein Klick).
            self._move_index = index
            self._move_from = (x, y)
            self._move_base = [list(entity) for entity in self.entities]
            return True
        self._box = [(x, y), (x, y)]        # im Leeren: Auswahlrahmen aufziehen
        return True

    def set_spline_handles(self, index: int, on: bool) -> None:
        """Schaltet die Tangentengriffe eines Splines an oder aus und rechnet Abhängigkeiten an seinen Punkten um.

        Beim Griff-Spline folgt auf jeden Kurvenpunkt seine Griffspitze: Kurvenpunkt k ist dann Punkt 2k.
        Abhängigkeiten an Griffspitzen entfallen beim Ausschalten.
        """
        entity = self.entities[index]
        if entity[0] != sk.SPLINE or sk.has_handles(entity) == on:
            return
        entity[:] = sk.with_handles(entity) if on else sk.without_handles(entity)
        if not on:
            # Krümmungsradien gibt es nur an Splines mit Griffen
            self.constraints[:] = [rule for rule in self.constraints
                                   if not (rule[0] == cs.POINT_CURVATURE and rule[1] == index)]
        # beim Ausschalten fallen Griffspitzen und (beim ungleichen Spline) die Gegengriffe dahinter weg
        tips_end = 2 * len(sk.fit_points(entity))
        remap = ((lambda slot: 2 * slot) if on
                 else (lambda slot: slot // 2 if slot % 2 == 0 and slot < tips_end else None))
        kept = []
        for rule in self.constraints:
            rule = list(rule)
            if rule[0] in (cs.POINT_ON, cs.MIDPOINT) and rule[1] == index:
                slot = remap(int(rule[3]))
                if slot is None:
                    continue
                rule[3] = float(slot)
            elif rule[0] in (cs.POINT_DISTANCE, cs.COINCIDENT) and index in rule[1:3]:
                slots = [remap(rule[5]) if rule[1] == index else rule[5],
                         remap(rule[6]) if rule[2] == index else rule[6]]
                if None in slots:
                    continue
                rule[5], rule[6] = slots
            kept.append(rule)
        self.constraints[:] = kept
        self.changed.emit()

    def even_spline_handles(self, index: int) -> None:
        """Macht die Griffe eines Griff-Splines wieder je Seite gleich lang; Abhängigkeiten an den wegfallenden
        Gegengriffspitzen (Punkte hinter den Griffspitzen) entfallen."""
        entity = self.entities[index]
        if not sk.has_uneven_handles(entity):
            return
        tips_end = 2 * len(sk.fit_points(entity))
        entity[:] = sk.evened(entity)

        def gone(rule: list) -> bool:
            if rule[0] in (cs.POINT_ON, cs.MIDPOINT) and rule[1] == index:
                return int(rule[3]) >= tips_end
            if rule[0] in (cs.POINT_DISTANCE, cs.COINCIDENT) and index in rule[1:3]:
                return (rule[1] == index and rule[5] >= tips_end) or (rule[2] == index and rule[6] >= tips_end)
            return False
        self.constraints[:] = [rule for rule in self.constraints if not gone(rule)]
        self.changed.emit()

    def curvature_grips(self) -> list[tuple]:
        """Krümmungsgriffe wie in Fusion: für jeden festgelegten Krümmungsradius an einem Griff-Spline
        (Nummer der Abhängigkeit, Kurvenpunkt, Einheitsrichtung zur Kreismitte, Mitte des Schmiegekreises)."""
        found = []
        for number, rule in enumerate(self.constraints):
            if rule[0] != cs.POINT_CURVATURE or cs.check(self.entities, rule) is not None:
                continue
            entity = self.entities[rule[1]]
            point, tip = sk.fit_points(entity)[rule[5]], sk.handle_tips(entity)[rule[5]]
            sides = cs._point_curvatures(entity, np.array(entity[1:], dtype=float), 0, rule[5])
            side = 1.0 if sum(c for c, _l in sides) >= 0 else -1.0     # links herum gebogen: Mitte links
            tx, ty = tip[0] - point[0], tip[1] - point[1]
            norm = math.hypot(tx, ty) or 1.0
            normal = (-ty / norm * side, tx / norm * side)
            found.append((number, point, normal, (point[0] + normal[0] * rule[3], point[1] + normal[1] * rule[3])))
        return found

    def is_dragging(self) -> bool:
        return (bool(self._drag_points) or self._drag_radius is not None or self._box is not None
                or self._drag_curvature is not None
                or self._move_from is not None or self._drag_text is not None
                or getattr(self, "_arc_drag", None) is not None)

    def _line_drag_start(self, x: float, y: float) -> bool:
        """Linienwerkzeug wie Fusion: auf einem Linienende drücken und ziehen ergibt einen Tangentialbogen,
        auf einem Kreis/Bogen drücken und ziehen eine Tangente. True, wenn so ein Ziehen beginnt."""
        reach = self._px(_SNAP_PIXELS)
        self._arc_drag = None
        if self._points:
            # laufender Linienzug: nur am aktuellen Endpunkt
            start = self._points[-1]
            line = self._previous_line(start)
            if line is None or math.dist(start, (x, y)) > reach:
                return False
        else:
            ends = [(index, end) for index, entity in enumerate(self.entities) if entity[0] == "Linie"
                    for end in (tuple(entity[1:3]), tuple(entity[3:5])) if math.dist(end, (x, y)) <= reach]
            if not ends:
                rim = self._on_curve(x, y, reach)
                if rim is not None and self.entities[rim[1]][0] in ("Kreis", "Bogen"):
                    self._arc_drag = {"circle": rim[1], "from": rim[0], "end": None}
                    return True
                return False
            line, start = min(ends, key=lambda item: math.dist(item[1], (x, y)))
        entity = self.entities[line]
        # Laufrichtung am Ende: von der Linie weg
        other = entity[1:3] if math.dist(entity[3:5], start) < 1e-9 else entity[3:5]
        length = math.dist(other, start) or 1.0
        tangent = ((start[0] - other[0]) / length, (start[1] - other[1]) / length)
        self._arc_drag = {"line": line, "start": tuple(start), "tangent": tangent, "end": None}
        return True

    def _drag_shape(self) -> list | None:
        """Was das laufende Ziehen aus dem Linienwerkzeug ergäbe: Tangentialbogen bzw. Tangente (oder None)."""
        drag = getattr(self, "_arc_drag", None)
        if drag is None or drag["end"] is None:
            return None
        end = drag["end"]
        if "circle" in drag:
            circle = self.entities[drag["circle"]]
            centre, radius = circle[1:3], circle[3]
            apart = math.dist(centre, end)
            if apart <= radius + 1e-9:
                return None
            # zwei Berührpunkte von `end` aus; genommen wird der, der näher an der gedrückten Stelle liegt
            base = math.atan2(end[1] - centre[1], end[0] - centre[0])
            spread = math.acos(radius / apart)
            touches = [(centre[0] + radius * math.cos(base + side * spread),
                        centre[1] + radius * math.sin(base + side * spread)) for side in (1, -1)]
            touch = min(touches, key=lambda point: math.dist(point, drag["from"]))
            return sk.line(*touch, *end)
        (px, py), (tx, ty) = drag["start"], drag["tangent"]
        nx, ny = -ty, tx
        dx, dy = end[0] - px, end[1] - py
        across = dx * nx + dy * ny
        if abs(across) < 1e-9:
            return sk.line(px, py, *end)        # genau geradeaus: eine Linie
        signed = (dx * dx + dy * dy) / (2 * across)     # Radius mit Vorzeichen: positiv = Mitte links
        cx, cy = px + nx * signed, py + ny * signed
        start_angle, end_angle = _angle((cx, cy), (px, py)), _angle((cx, cy), end)
        if signed > 0:
            return sk.arc(cx, cy, abs(signed), start_angle, end_angle)     # links herum (gegen den Uhrzeiger)
        return sk.arc(cx, cy, abs(signed), end_angle, start_angle)

    def drag_to(self, x: float, y: float) -> None:
        """Zieht das Gegriffene an die Stelle (x, y); Maße und Abhängigkeiten gelten weiter."""
        if not self.is_dragging():
            return
        if getattr(self, "_arc_drag", None) is not None:
            self._arc_drag["end"] = self.snap(x, y)[0]
            self.update()
            return
        if self._box is not None:
            self._box[1] = (x, y)
            self.update()
            return
        if self._drag_text is not None:
            number, grip, base = self._drag_text
            self.texts[number][1:3] = [base[0] + x - grip[0], base[1] + y - grip[1]]
            self.changed.emit()
            self.update()
            return
        before = [list(entity) for entity in self.entities]
        if self._move_from is not None:
            # Verschieben ab dem Stand beim Anfassen, damit sich kleine Fehler nicht aufsummieren
            if self._move_index not in self.selection:
                self.selection, self.selected = {self._move_index}, self._move_index
            fixed = {rule[1] for rule in self.constraints if rule[0] == cs.FIX}
            dx, dy = x - self._move_from[0], y - self._move_from[1]
            for index in self.selection - fixed:
                if index < len(self._move_base):
                    self.entities[index][:] = _shifted(self._move_base[index], dx, dy)
        elif self._drag_curvature is not None:
            # Krümmungsgriff: Radius = Abstand der Maus vom Kurvenpunkt längs der Richtung zur Kreismitte
            # (die Biegerichtung bleibt; auf der anderen Seite des Punkts wird der Radius nur sehr klein)
            number, point, normal = self._drag_curvature
            rule = self.constraints[number]
            old_radius = rule[3]
            rule[3] = max((x - point[0]) * normal[0] + (y - point[1]) * normal[1], 0.01)
            if len(rule) > 4 and not cs.is_driven(rule):
                rule[4] = ""        # ein Ausdruck gilt nach dem Ziehen nicht mehr
            if not self.apply_constraints():
                rule[3] = old_radius
                for entity, values in zip(self.entities, before):
                    entity[:] = values
                return
            self.changed.emit()
            self.update()
            return
        elif self._drag_radius is not None:
            circle = self.entities[self._drag_radius]
            radius = math.dist(circle[1:3], (x, y))
            # Der Radius rastet nur nahe an einem Vielfachen der Rasterweite ein
            step = self.grid_step()
            near = round(radius / step) * step if step > 0 else radius
            circle[3] = near if near > 0 and abs(near - radius) <= self._px(_GRID_SNAP_PIXELS) else radius
        else:
            target = self._drag_target(x, y)
            for index, slot in self._drag_points:
                self._move_point(self.entities[index], slot, target)
        usable = all(sk.check_entity(entity) is None for entity in self.entities)
        if not usable or (self.constraints and not self.apply_constraints()):
            # So weit lässt sich nicht ziehen: der letzte brauchbare Stand bleibt stehen
            for entity, values in zip(self.entities, before):
                entity[:] = values
            return
        self.changed.emit()
        self.update()

    def drag_end(self) -> None:
        shape = self._drag_shape()
        drag, self._arc_drag = getattr(self, "_arc_drag", None), None
        if shape is not None and sk.check_entity(shape) is None:
            # Tangentialbogen bzw. Tangente aus dem Linienwerkzeug: tangential gebunden, der Zug geht weiter
            self.entities.append(shape)
            new = len(self.entities) - 1
            partner = drag.get("line", drag.get("circle"))
            rule = [cs.TANGENT, partner, new, 0.0]
            if cs.check(self.entities, rule) is None:
                self.constraints.append(rule)
            self._points = [tuple(drag["end"])]
            self._apply_line_mode(new)
            self.changed.emit()
            self.update()
            return
        if self._box is not None and self._box[0] != self._box[1]:
            (ax, ay), (bx, by) = self._box
            # Von links nach rechts gezogen zählt nur, was ganz im Rahmen liegt; von rechts nach
            # links auch alles, was er berührt (wie in Fusion)
            touching = bx < ax
            low_x, high_x, low_y, high_y = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
            picked = set()
            for index, entity in enumerate(self.entities):
                inside = [low_x <= px <= high_x and low_y <= py <= high_y for px, py in _samples(entity)]
                if any(inside) if touching else all(inside):
                    picked.add(index)
            self.selection = (self.selection | picked) if self.additive else picked
            self.selected = next(iter(sorted(self.selection)), None)
            self.changed.emit()
        self._drag_points, self._drag_radius, self._drag_curvature = [], None, None
        self._box, self._move_from, self._move_base, self._drag_text = None, None, [], None
        self.update()

    def box_touches(self) -> bool:
        """Wird der Auswahlrahmen von rechts nach links gezogen (wählt auch Berührtes)?"""
        return self._box is not None and self._box[1][0] < self._box[0][0]

    def box_outline(self) -> list[tuple[float, float]]:
        """Die Ecken des gerade aufgezogenen Auswahlrahmens (leer, wenn keiner offen ist)."""
        if self._box is None or self._box[0] == self._box[1]:
            return []
        (ax, ay), (bx, by) = self._box
        return [(ax, ay), (bx, ay), (bx, by), (ax, by)]

    def _drag_target(self, x: float, y: float) -> tuple[float, float]:
        """Fängt die Zielstelle auf andere Punkte oder das Raster (nicht auf die gezogenen Punkte selbst)."""
        own = [cs.point_of(self.entities[index], slot) for index, slot in self._drag_points]
        others = [point for point in self.snap_points() if all(math.dist(point, o) > 1e-6 for o in own)]
        if others:
            nearest = min(others, key=lambda point: math.dist(point, (x, y)))
            if math.dist(nearest, (x, y)) <= self._px(_SNAP_PIXELS):
                return nearest
        step = self.grid_step()
        if step > 0:
            node = (round(x / step) * step, round(y / step) * step)
            if math.dist(node, (x, y)) <= self._px(_GRID_SNAP_PIXELS):
                return node         # nur nahe an einem Rasterpunkt, sonst frei
        return x, y

    @staticmethod
    def _move_point(entity: list, slot: int, target: tuple[float, float]) -> None:
        """Setzt den Punkt `slot` des Elements auf die Zielstelle."""
        if entity[0] in (sk.CONIC, sk.CP_SPLINE, sk.POINT):
            entity[1 + 2 * slot:3 + 2 * slot] = target      # jeder Punkt steht für sich (Rho/Grad bleiben)
            return
        if sk.has_uneven_handles(entity):
            SketchCanvas._move_uneven_handle(entity, slot, target)
            return
        if entity[0] in ("Linie", sk.SPLINE):
            if sk.has_handles(entity) and slot % 2 == 0:
                # Kurvenpunkt eines Griff-Splines: sein Tangentengriff (der folgende Punkt) wandert mit
                entity[3 + 2 * slot] += target[0] - entity[1 + 2 * slot]
                entity[4 + 2 * slot] += target[1] - entity[2 + 2 * slot]
            entity[1 + 2 * slot:3 + 2 * slot] = target
        elif slot == 0:
            entity[1:3] = target                    # Mittelpunkt: Kreis oder Bogen wandert als Ganzes
        elif sk.is_ellipse_arc(entity):
            # Ende eines Ellipsenbogens: der Laufwinkel folgt der Maus, die Ellipse selbst bleibt
            entity[5 + slot] = sk.ellipse_angle(entity, *target)
        else:
            # Bogenende: Radius und Winkel so, dass das Ende an der Zielstelle liegt
            entity[3] = math.dist(entity[1:3], target)
            entity[3 + slot] = _angle((entity[1], entity[2]), target)

    @staticmethod
    def _move_uneven_handle(entity: list, slot: int, target: tuple[float, float]) -> None:
        """Zieht an einem Griff-Spline mit ungleichen Griffen (siehe `sk.UNEVEN_HANDLE_SPLINE`).

        Wie in Fusion bilden Griff und Gegengriff eine gerade Stange durch den Kurvenpunkt: wird ein Ende gezogen,
        dreht sich das andere mit und behält seine Länge. Ein Kurvenpunkt nimmt beide Griffe mit.
        """
        count = (len(entity) - 1) // 6
        number = slot // 2 if slot < 2 * count else slot - 2 * count       # welcher Kurvenpunkt
        at_point, at_tip, at_back = 1 + 4 * number, 3 + 4 * number, 1 + 4 * count + 2 * number
        px, py = entity[at_point], entity[at_point + 1]
        if slot < 2 * count and slot % 2 == 0:
            # Kurvenpunkt: Griff und Gegengriff verschieben sich mit
            dx, dy = target[0] - px, target[1] - py
            for at in (at_point, at_tip, at_back):
                entity[at] += dx
                entity[at + 1] += dy
            return
        moved, other = (at_tip, at_back) if slot < 2 * count else (at_back, at_tip)
        entity[moved:moved + 2] = target
        # das andere Ende auf die Gegenseite drehen, seine Länge bleibt
        reach = math.dist((px, py), entity[other:other + 2])
        length = math.dist((px, py), target)
        if length > 1e-12:
            entity[other] = px - (target[0] - px) / length * reach
            entity[other + 1] = py - (target[1] - py) / length * reach

    def _trim_round(self, index: int, x: float, y: float) -> str | None:
        """Stutzt einen Kreis oder Bogen: das angeklickte Stück zwischen zwei Schnittstellen fällt weg."""
        entity = self.entities[index]
        kept = sk.trim_round(entity, sk.round_cuts(self.entities, index), _angle((entity[1], entity[2]), (x, y)))
        if not kept:
            self._remove_entity(index)
            return None
        if entity[0] == "Kreis":
            # Aus dem Kreis wird ein Bogen: sein Durchmessermaß wird zum Radiusmaß
            for rule in self.constraints:
                if rule[0] == cs.DIAMETER and rule[1] == index:
                    rule[0], rule[3] = cs.RADIUS, rule[3] / 2
                    del rule[4:]        # ein Ausdruck würde weiter den Durchmesser meinen
        entity[:] = kept[0]
        if len(kept) > 1:
            self.entities.append(kept[1])
            if index in self.construction:
                self.construction.append(len(self.entities) - 1)
        return None

    def add_constraint(self, constraint: list) -> bool:
        """Fügt eine Abhängigkeit hinzu und zieht die Elemente nach; False, wenn sie nicht passt."""
        problem = cs.check(self.entities, constraint)
        if problem is None and constraint not in self.constraints:
            self.constraints.append(constraint)
            if not self.apply_constraints():
                self.constraints.pop()
                problem = f"{constraint[0]} widerspricht den vorhandenen Maßen und Abhängigkeiten"
        self.message = problem or ""
        self.changed.emit()
        self.update()
        return problem is None

    def set_dimension(self, number: int, value: float, expression: str = "") -> bool:
        """Ändert die Maßzahl; passt der Wert nicht zum Rest, bleibt der alte.

        Mit `expression` hängt das Maß an einem Ausdruck mit Benutzerparametern.
        """
        if not 0 <= number < len(self.constraints):
            return False
        self._dimension_first = None        # ein bestätigtes Maß ist fertig: der nächste Klick beginnt neu
        if cs.is_driven(self.constraints[number]):
            self.message = "Ein getriebenes Maß zeigt nur den Wert – erst auf „treibend“ umschalten"
            self.changed.emit()
            return False
        if value <= 0 and self.constraints[number][0] != cs.DIRECTION:
            return False            # nur die Neigung darf 0 oder negativ sein
        previous = list(self.constraints[number])
        if self.constraints[number][0] in (cs.POINT_DISTANCE, cs.POINT_CURVATURE):
            # der Punktabstand behält seine beiden Punktnummern hinter dem Ausdruck
            self.constraints[number][3:5] = [float(value), expression]
        else:
            self.constraints[number][3:] = [float(value), expression] if expression else [float(value)]
        accepted = self.apply_constraints()
        if not accepted:
            self.constraints[number][:] = previous
        self.message = "" if accepted else "Das Maß widerspricht den übrigen Maßen und Abhängigkeiten"
        self.changed.emit()
        self.update()
        return accepted

    def remove_constraint(self, number: int) -> None:
        if 0 <= number < len(self.constraints):
            del self.constraints[number]
            self.message = ""
            self.changed.emit()

    def pattern_of(self, index: int) -> tuple[str, list] | None:
        """Gehört das Element als Kopie zu einem Muster? (Art, Verknüpfungen der ganzen Mustergruppe) oder None."""
        own = next((rule for rule in self.constraints if rule[0] in (cs.TRANSLATED, cs.ROTATED) and rule[2] == index), None)
        if own is None:
            return None
        if own[0] == cs.TRANSLATED:
            group = cs.pattern_place(own)[0]
            return cs.TRANSLATED, [rule for rule in self.constraints
                                   if rule[0] == cs.TRANSLATED and cs.pattern_place(rule)[0] == group and group > 0]
        return cs.ROTATED, [rule for rule in self.constraints if rule[0] == cs.ROTATED and rule[5:7] == own[5:7]]

    def pattern_steps(self, index: int) -> dict[str, float]:
        """Die einstellbaren Größen des Musters, zu dem das Element gehört: Abstände bzw. Winkelschritt."""
        found = self.pattern_of(index)
        if found is None:
            return {}
        kind, rules = found
        if kind == cs.ROTATED:
            return {"step": min((rule[3] for rule in rules if rule[3] > 1e-9), default=0.0)}
        steps = {}
        for rule in rules:
            _group, column, row = cs.pattern_place(rule)
            if column:
                steps.setdefault("dx", rule[5] / column)
            if row:
                steps.setdefault("dy", rule[6] / row)
        return steps

    def pattern_counts(self, index: int) -> dict[str, int]:
        """Anzahlen des Musters, zu dem das Element gehört (mit dem Original): nx/ny bzw. count."""
        found = self.pattern_of(index)
        if found is None:
            return {}
        kind, rules = found
        if kind == cs.ROTATED:
            originals = {rule[1] for rule in rules}
            return {"count": len(rules) // max(len(originals), 1) + 1}
        places = [cs.pattern_place(rule) for rule in rules]
        return {"nx": max(place[1] for place in places) + 1, "ny": max(place[2] for place in places) + 1}

    def set_pattern_count(self, index: int, name: str, value: int) -> bool:
        """Ändert die Anzahl eines Musters: alle Kopien der Gruppe werden mit der neuen Anzahl neu angelegt.

        Beim Kreismuster über den vollen Kreis verteilt sich die neue Anzahl wieder auf 360°.
        """
        found = self.pattern_of(index)
        if found is None or value < 1:
            return False
        kind, rules = found
        originals = list(dict.fromkeys(rule[1] for rule in rules))
        steps, counts = self.pattern_steps(index), self.pattern_counts(index)
        counts[name] = int(value)
        if kind == cs.TRANSLATED and counts["nx"] * counts["ny"] < 2:
            return False
        if kind == cs.ROTATED and counts["count"] < 2:
            return False
        centre, group = tuple(rules[0][5:7]), (cs.pattern_place(rules[0])[0] if kind == cs.TRANSLATED else 0)
        step = steps.get("step", 0.0)
        old_count = len(rules) // max(len(originals), 1) + 1
        if kind == cs.ROTATED and abs(step * old_count - 360.0) < 1e-6:
            step = 360.0 / counts["count"]          # voller Kreis: die Kopien verteilen sich wieder gleichmäßig
        # alte Kopien entfernen (von hinten, damit die Nummern der übrigen stimmen); die Originale behalten
        copies = sorted({rule[2] for rule in rules}, reverse=True)
        for number in copies:
            self._remove_entity(number)
        originals = [number - sum(1 for copy in copies if copy < number) for number in originals]
        added = []
        if kind == cs.TRANSLATED:
            for row in range(counts["ny"]):
                for column in range(counts["nx"]):
                    if row or column:
                        shift = (column * steps.get("dx", 0.0), row * steps.get("dy", 0.0))
                        added += [(number, _shifted(self.entities[number], *shift),
                                   [cs.TRANSLATED, number, -1, cs.pattern_code(group, column, row), "",
                                    float(shift[0]), float(shift[1])]) for number in originals]
        else:
            for k in range(1, counts["count"]):
                added += [(number, sk.rotated(self.entities[number], *centre, k * step),
                           [cs.ROTATED, number, -1, k * step, "", *centre]) for number in originals]
        for number, copy, link in added:
            self.entities.append(copy)
            if number in self.construction:
                self.construction.append(len(self.entities) - 1)
            link[2] = len(self.entities) - 1
            if cs.check(self.entities, link) is None:
                self.constraints.append(link)
        self.selected, self.selection = None, set()
        self.message = f"Muster neu angelegt: {len(added)} Kopie(n)"
        self.changed.emit()
        return True

    def set_pattern_step(self, index: int, name: str, value: float) -> bool:
        """Ändert Abstand oder Winkelschritt für alle Kopien des Musters; die Kopien rücken nach."""
        found = self.pattern_of(index)
        if found is None:
            return False
        kind, rules = found
        before = [list(rule) for rule in self.constraints]
        old = self.pattern_steps(index).get(name, 0.0)
        for rule in rules:
            if kind == cs.ROTATED and old > 1e-9:
                rule[3] = round(rule[3] / old) * value
            elif kind == cs.TRANSLATED:
                _group, column, row = cs.pattern_place(rule)
                if name == "dx":
                    rule[5] = column * value
                else:
                    rule[6] = row * value
        accepted = self.apply_constraints()
        if not accepted:
            self.constraints[:] = before
        self.message = "" if accepted else "Dieser Musterabstand passt nicht zu den übrigen Maßen"
        self.changed.emit()
        return accepted

    def apply_constraints(self) -> bool:
        """Zieht die Elemente auf die Maße und Abhängigkeiten; False, wenn das nicht geht."""
        solved = cs.solve(self.entities, self.constraints)
        if solved is None:
            return False
        for entity, values in zip(self.entities, solved):
            entity[:] = values
        cs.update_driven(self.entities, self.constraints)     # getriebene Maße zeigen den neuen Wert
        return True

    def _add(self, entity: list) -> None:
        # Unbrauchbare Elemente (Länge 0, Radius 0) entstehen bei Doppelklicks und werden verworfen
        if sk.check_entity(entity) is None:
            self.entities.append(entity)
            self.changed.emit()

    def _entity_at(self, x: float, y: float) -> int | None:
        reach = self._px(_PICK_PIXELS)
        best, best_distance = None, reach
        for index, entity in enumerate(self.entities):
            distance = _distance_to(entity, x, y)
            if distance <= best_distance:
                best, best_distance = index, distance
        return best

    # --- Ereignisse ---------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Das Fenster erhält seine endgültige Größe oft erst nach dem Anzeigen. Solange der
        # Benutzer die Ansicht nicht selbst verschoben oder gezoomt hat, wird sie nachgeführt.
        if not self._centered:
            self.fit_view()

    def fit_view(self) -> None:
        """Zeigt alle Elemente mit Rand; eine leere Skizze bekommt den Ursprung in die Mitte."""
        points = ([point for point in self.snap_points() if point != (0.0, 0.0)]
                  if self.entities or self.reference else [])
        points += [point for polyline in self.reference for point in polyline]
        for entity in self.entities:    # Kreise und Bögen reichen über ihren Mittelpunkt hinaus
            if entity[0] != "Linie":
                x, y, radius = entity[1:4]
                points += [(x - radius, y - radius), (x + radius, y + radius)]
        if not points:
            self._scale = 6.0
            self._origin = QPointF(self.width() / 2, self.height() / 2)
        else:
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            width, height = max(xs) - min(xs) or 1.0, max(ys) - min(ys) or 1.0
            self._scale = max(0.2, min(200.0, 0.7 * min(self.width() / width, self.height() / height)))
            centre = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
            self._origin = QPointF(self.width() / 2 - centre[0] * self._scale,
                                   self.height() / 2 + centre[1] * self._scale)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_from = event.position()
        elif event.button() == Qt.MouseButton.RightButton:
            self.finish()
        elif event.button() == Qt.MouseButton.LeftButton:
            (x, y), _on_point = self.snap(*self.to_sketch(event.position()))
            self.click(x, y)

    def mouseMoveEvent(self, event) -> None:
        if self._pan_from is not None:
            self._centered = True
            self._origin += event.position() - self._pan_from
            self._pan_from = event.position()
        self._cursor, self._snapped_to_point = self.snap(*self.to_sketch(event.position()))
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_from = None

    def wheelEvent(self, event) -> None:
        # Um die Mausposition zoomen: der Punkt unter dem Zeiger bleibt stehen
        self._centered = True
        before = self.to_sketch(event.position())
        self._scale = max(0.2, min(200.0, self._scale * math.pow(1.0015, event.angleDelta().y())))
        after = self.to_screen(*before)
        self._origin += event.position() - after
        self.update()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.finish()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        else:
            super().keyPressEvent(event)

    # --- Darstellung --------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), _COLORS["background"])
        self._paint_grid(painter)
        painter.setPen(QPen(_COLORS["reference"], 1.5))
        for polyline in self.reference:
            for start, end in zip(polyline, polyline[1:]):
                painter.drawLine(self.to_screen(*start), self.to_screen(*end))
        for index, entity in enumerate(self.entities):
            color = _COLORS["selected"] if index == self.selected else _COLORS["entity"]
            painter.setPen(QPen(color, 2))
            self._paint_entity(painter, entity)
        self._paint_preview(painter)
        painter.end()

    def _paint_grid(self, painter: QPainter) -> None:
        step = self.grid if self.grid > 0 else 5.0
        while step * self._scale < 8:       # zu enge Rasterlinien zusammenfassen
            step *= 5
        left, top = self.to_sketch(QPointF(0, 0))
        right, bottom = self.to_sketch(QPointF(self.width(), self.height()))
        painter.setPen(QPen(_COLORS["grid"], 1))
        x = math.floor(left / step) * step
        while x <= right:
            painter.drawLine(self.to_screen(x, top), self.to_screen(x, bottom))
            x += step
        y = math.floor(bottom / step) * step
        while y <= top:
            painter.drawLine(self.to_screen(left, y), self.to_screen(right, y))
            y += step
        painter.setPen(QPen(_COLORS["axis"], 1.5))
        painter.drawLine(self.to_screen(left, 0), self.to_screen(right, 0))
        painter.drawLine(self.to_screen(0, top), self.to_screen(0, bottom))

    def _paint_entity(self, painter: QPainter, entity: list) -> None:
        kind, v = entity[0], entity[1:]
        if kind == "Linie":
            painter.drawLine(self.to_screen(v[0], v[1]), self.to_screen(v[2], v[3]))
            return
        centre, radius = self.to_screen(v[0], v[1]), v[2] * self._scale
        if kind == "Kreis":
            painter.drawEllipse(centre, radius, radius)
        else:
            # Qt zählt Winkel in 1/16 Grad gegen den Uhrzeigersinn, wie die Skizze
            span = (v[4] - v[3]) % 360 or 360
            painter.drawArc(int(centre.x() - radius), int(centre.y() - radius),
                            int(2 * radius), int(2 * radius), int(v[3] * 16), int(span * 16))

    def preview(self) -> tuple[list, str]:
        """Das gerade entstehende Element (als Liste von Elementen) und ein Text mit seinen Maßen."""
        cursor = self._cursor
        text = f"x {cursor[0]:.2f}   y {cursor[1]:.2f}"
        shape = self._drag_shape()
        if shape is not None:
            kind = "Tangente" if shape[0] == "Linie" else f"Tangentialbogen R {shape[3]:.2f}"
            return [shape], text + f"   {kind} – loslassen setzt ihn, die Linie geht weiter"
        if self.tool in (TWO_TANGENT_CIRCLE, THREE_TANGENT_CIRCLE):
            wanted = 2 if self.tool == TWO_TANGENT_CIRCLE else 3
            if len(self._tangent_lines) == wanted:
                found = self._tangent_circle_at(*cursor)
                return ([sk.circle(*found)] if found else []), text + "   Klick setzt den Kreis"
            return [], text + f"   {self.tool}: Linie {len(self._tangent_lines) + 1} von {wanted} anklicken"
        if self.tool == TEXT_TOOL:
            return [], text + "   Klick setzt die linke untere Ecke des Textes"
        if self.tool in PICK_TOOLS:
            # Hier wird nichts gezeichnet: der Text sagt, was anzuklicken ist
            if self.tool == "Auswählen":
                return [], ""
            if self.tool == DIMENSION_TOOL and getattr(self, "_dimension_place", None):
                axis = self._placement_axis(*self._dimension_place, *cursor)
                kind = {"x": "waagrechter", "y": "senkrechter"}.get(axis, "direkter")
                return [], f"Bemaßung: Klick platziert einen {kind} Abstand"
            if self.tool == DIMENSION_TOOL:
                return [], "Linie, Kreis oder Bogen anklicken (Umschalt: Linienmitte)"
            hover = self._hover_preview(*cursor)
            if hover is not None:
                return hover
            if self.tool in EDIT_TOOLS:
                return [], {FILLET_TOOL: f"Abrunden (R {self.fillet_radius:g}): Ecke anklicken",
                            TRIM_TOOL: "Stutzen: das Stück anklicken, das weg soll",
                            EXTEND_TOOL: "Verlängern: Linie nahe dem Ende anklicken, das verlängert werden soll",
                            CHAMFER_TOOL: f"Fase ({self.chamfer_kind}): Ecke zweier Linien anklicken",
                            SCALE_TOOL: f"Skizze skalieren (×{self.scale_factor:g}): Bezugspunkt anklicken – "
                                        "wirkt auf die Auswahl, ohne Auswahl auf alles",
                            BLEND_TOOL: "Übergangskurve: Enden zweier Kurven nacheinander anklicken",
                            MOVE_TOOL: "Verschieben/Kopieren: Basispunkt, dann Zielpunkt anklicken",
                            BREAK_TOOL: "Brechen: Element anklicken – es wird an seinen Schnittpunkten geteilt",
                            CONSTRUCTION_TOOL: "Element anklicken: Konstruktionslinie an/aus",
                            MIRROR_TOOL: ("Spiegeln: Spiegelachse (Linie) anklicken" if self._mirror_axis is None
                                          else "Spiegeln: Element anklicken"),
                            OFFSET_TOOL: f"Versatz ({self.offset_distance:g} mm): neben das Element klicken",
                            PROJECT_TOOL: "Projizieren: Kante oder Fläche eines Körpers anklicken",
                            PATTERN_TOOL: f"Muster {int(self.pattern['nx'])} × {int(self.pattern['ny'])}: Element anklicken",
                            CIRCULAR_TOOL: f"Kreismuster {int(self.pattern['count'])}×: Element anklicken"}[self.tool]
            if self.tool == cs.FIX:
                return [], "Fixieren: Element anklicken (nochmal anklicken löst es wieder)"
            if self.tool not in cs.PAIRS:
                return [], f"{self.tool}: Linie anklicken"
            second = self._first_pick is not None
            if self.tool == cs.POINT_ON:
                return [], "Punkt auf Kurve: " + ("Linie, Kreis oder Bogen anklicken" if second
                                                  else "Element nahe an seinem Punkt anklicken")
            return [], f"{self.tool}: {'zweites' if second else 'erstes'} Element anklicken"
        if not self._points:
            if self.tool == sk.SPLINE:
                text += "   Strg+Klick: Punkt in einen Spline einfügen oder entfernen"
            if self.tool == "Linie":
                return [], "Ersten Punkt platzieren"        # wie Fusion vor dem ersten Klick
            return [], text
        first = self._points[0]
        if self.tool == "Linie":
            first = self._points[-1]
            cursor = self._hud_point(cursor)        # festgehaltene Länge/Winkel
            aligned = {cs.HORIZONTAL: "   ⟷ waagrecht", cs.VERTICAL: "   ↕ senkrecht",
                       cs.PERPENDICULAR: f"   ⊥ rechtwinklig zu Linie {self._inferred_with + 1}"
                                         + (" (Lotfußpunkt)" if self._foot_on >= 0 else ""),
                       cs.PARALLEL: ("   ∥ gerade weiter" if 0 <= self._inferred_with < len(self.entities)
                                     and _touches(self.entities[self._inferred_with], first)
                                     else f"   ∥ parallel zu Linie {self._inferred_with + 1}")}.get(self._inferred, "")
            # Wie Fusion nur der kurze Hinweis am Mauszeiger: Länge und Winkel stehen in den Maßfeldern an der Linie
            # (die lange Zeile mit Koordinaten und Bedienhilfe verdeckte die Zeichnung, Video 2026-10-22)
            return [sk.line(*first, *cursor)], "Nächsten Punkt angeben" + aligned
        if self.tool == "Rechteck":
            cursor = (self._rectangle_x(first, cursor[0]), cursor[1])
            size = f"   {abs(cursor[0] - first[0]):.2f} × {abs(cursor[1] - first[1]):.2f}"
            return sk.rectangle(*first, *cursor), text + size
        if self.tool == "Kreis":
            radius = math.dist(first, cursor)
            return ([sk.circle(*first, radius)] if radius > 1e-9 else []), text + f"   Radius {radius:.2f}"
        if self.tool == sk.SPLINE:
            points = [*self._points, cursor] if math.dist(self._points[-1], cursor) > 1e-9 else self._points
            entities = [sk.spline(*points)] if len(points) >= 2 else []
            return entities, text + f"   {len(self._points)} Punkte – Rechtsklick beendet"
        if self.tool == sk.CP_SPLINE:
            points = [*self._points, cursor] if math.dist(self._points[-1], cursor) > 1e-9 else self._points
            curve = sk.control_spline(*points, degree=self.cp_degree) if len(points) >= 2 else None
            # Steuerpolygon gestrichelt mit anzeigen: daran sieht man, wohin die Punkte den Bogen ziehen
            polygon = [sk.line(*a, *b) for a, b in zip(points, points[1:]) if math.dist(a, b) > 1e-9]
            entities = ([curve] if curve and sk.check_entity(curve) is None else []) + polygon
            return entities, text + f"   {len(self._points)} Steuerpunkte, Grad {self.cp_degree} – Rechtsklick beendet"
        if self.tool == "Polygon":
            if self.polygon_kind == sk.POLYGON_KINDS[2] and len(self._points) < 2:
                return [sk.line(*first, *cursor)], text + f"   Kante {math.dist(first, cursor):.2f}"
            entities = self._polygon_shape(self._points, cursor)
            return entities, text + f"   {int(self.polygon_sides)} Ecken ({self.polygon_kind})"
        if self.tool == "Langloch":
            needed = self._slot_clicks()
            if len(self._points) < needed - 1:
                # Punkte der Mittellinie setzen; beim Bogen zeigt die Vorschau schon den Bogen
                if self.slot_kind == sk.SLOT_KINDS[3] and len(self._points) == 2:
                    shape = sk.arc_through(self._points[0], cursor, self._points[1])
                    return ([shape] if shape else []), text + "   Punkt auf dem Bogen"
                return [sk.line(*self._points[-1], *cursor)], text + f"   {self.slot_kind}"
            entities = self._slot_to(*cursor)
            return entities, text + f"   {self.slot_kind} – Breite festlegen"
        if self.tool == sk.CONIC:
            if len(self._points) < 2:
                return [sk.line(*first, *cursor)], text + "   Ende setzen"
            curve = sk.conic(*first, *self._points[1], *cursor, self.conic_rho)
            if sk.check_entity(curve) is not None:
                return [sk.line(*first, *self._points[1])], text + "   Scheitel setzen"
            # Hilfslinien zum Scheitel zeigen, wie die Enden ausgerichtet sind
            return ([curve, sk.line(*first, *cursor), sk.line(*self._points[1], *cursor)],
                    text + f"   Scheitel setzen (Rho {self.conic_rho:g})")
        if self.tool == sk.ELLIPSE:
            if len(self._points) < 2:
                return [sk.line(*first, *cursor)], text + f"   Halbachse {math.dist(first, cursor):.2f}"
            oval = self._ellipse_to(*cursor)
            return ([oval] if oval else []), text + (f"   {oval[3]:.2f} × {oval[4]:.2f}" if oval else "")
        if self.tool == CENTER_RECT:
            return (sk.rectangle(2 * first[0] - cursor[0], 2 * first[1] - cursor[1], *cursor),
                    text + f"   {2 * abs(cursor[0] - first[0]):.2f} × {2 * abs(cursor[1] - first[1]):.2f}")
        if self.tool == THREE_POINT_RECT:
            if len(self._points) < 2:
                return [sk.line(*first, *cursor)], text + f"   Seite {math.dist(first, cursor):.2f}"
            corners = self._three_point_corners(*cursor)
            return (sk.rectangle_points(corners) if corners else []), text
        if self.tool == THREE_POINT_CIRCLE:
            found = sk.circle_through(*self._points[:2], cursor) if len(self._points) == 2 else None
            return ([sk.circle(*found)] if found else [sk.line(*self._points[-1], *cursor)]), text
        if self.tool == TWO_POINT_CIRCLE:
            radius = math.dist(first, cursor) / 2
            return ([sk.circle((first[0] + cursor[0]) / 2, (first[1] + cursor[1]) / 2, radius)] if radius > 1e-9 else []), \
                text + f"   Durchmesser {2 * radius:.2f}"
        if self.tool == THREE_POINT_ARC:
            if len(self._points) < 2:
                return [sk.line(*first, *cursor)], text + "   Endpunkt, dann einen Punkt auf dem Bogen anklicken"
            made = sk.arc_through(first, cursor, self._points[1])
            return ([made] if made else []), text + (f"   Radius {made[3]:.2f}" if made else "")
        if self.tool == TANGENT_ARC:
            made = self._tangent_arc_to(first, cursor)
            hint = f"   Radius {made[3]:.2f}" if made else "   am Ende einer Linie oder eines Bogens beginnen"
            return ([made] if made else []), text + hint
        if self.tool == "Bogen":
            start = self._points[1] if len(self._points) > 1 else cursor
            radius = math.dist(first, start)
            if radius > 1e-9 and len(self._points) > 1:
                entities = [sk.arc(*first, radius, _angle(first, start), _angle(first, cursor))]
            else:
                entities = [sk.line(*first, *cursor)]     # erst der Radius als Hilfslinie
            return entities, text + f"   Radius {radius:.2f}"
        return [], text

    def _paint_preview(self, painter: QPainter) -> None:
        painter.setPen(QPen(_COLORS["snap"] if self._snapped_to_point else _COLORS["text"], 1))
        position = self.to_screen(*self._cursor)
        painter.drawRect(int(position.x()) - 4, int(position.y()) - 4, 8, 8)
        entities, text = self.preview()
        painter.setPen(QPen(_COLORS["preview"], 1, Qt.PenStyle.DashLine))
        for entity in entities:
            if sk.check_entity(entity) is None:
                self._paint_entity(painter, entity)
        painter.setPen(_COLORS["text"])
        painter.drawText(10, self.height() - 10, text)


def _angle(centre: tuple[float, float], point: tuple[float, float]) -> float:
    """Winkel des Punkts um den Mittelpunkt in Grad (0 … 360)."""
    return math.degrees(math.atan2(point[1] - centre[1], point[0] - centre[0])) % 360


def _shifted(entity: list, dx: float, dy: float) -> list:
    """Das Element um (dx, dy) verschoben."""
    moved = list(entity)
    if entity[0] in sk.SPATIAL:
        for i in range(1, len(entity) - 2, 3):      # jeder Raumpunkt in der Ebene verschoben, z bleibt
            moved[i], moved[i + 1] = entity[i] + dx, entity[i + 1] + dy
        return moved
    if entity[0] in ("Linie", sk.SPLINE, sk.CONIC, sk.CP_SPLINE):
        for i in range(1, len(entity) - 1, 2):      # Rho bzw. Grad (der letzte Wert) bleiben stehen
            moved[i], moved[i + 1] = entity[i] + dx, entity[i + 1] + dy
    else:
        moved[1], moved[2] = entity[1] + dx, entity[2] + dy     # Kreis und Bogen: nur der Mittelpunkt
    return moved


def _samples(entity: list) -> list[tuple[float, float]]:
    """Punkte entlang des Elements, an denen geprüft wird, ob es im Auswahlrahmen liegt."""
    kind, v = entity[0], entity[1:]
    if kind == sk.POINT:
        return [(v[0], v[1])]
    if kind == sk.LINE3D:
        # 3D-Linie: ihr Schatten in der Ebene (für Auswahlrahmen und Anklicken)
        return [(v[0] + (v[3] - v[0]) * k / 16, v[1] + (v[4] - v[1]) * k / 16) for k in range(17)]
    if kind == sk.SPLINE3D:
        return sk.shadow(entity)
    if kind == "Linie":
        return [(v[0] + (v[2] - v[0]) * k / 16, v[1] + (v[3] - v[1]) * k / 16) for k in range(17)]
    if kind == sk.SPLINE:
        return sk.spline_points(entity)
    if kind == sk.ELLIPSE:
        return sk.ellipse_points(entity, 32)
    if kind == sk.CONIC:
        return sk.conic_points(entity, 32)
    if kind == sk.CP_SPLINE:
        return sk.control_spline_points(entity)
    start, span = (0.0, 360.0) if kind == "Kreis" else (v[3], (v[4] - v[3]) % 360 or 360.0)
    return [(v[0] + v[2] * math.cos(math.radians(start + span * k / 32)),
             v[1] + v[2] * math.sin(math.radians(start + span * k / 32))) for k in range(33)]


def _touches(entity: list, point) -> bool:
    """Liegt der Punkt auf einem Ende der Linie oder (innen) auf ihr?"""
    return _distance_to(entity, *point) < 1e-7

def _crossings(entity: list, axis: int, value: float) -> list[tuple[float, float]]:
    """Wo die Linie, der Kreis oder Bogen `entity` die achsparallele Gerade trifft (axis 0: x = value, axis 1:
    y = value). Andere Kurven: keine Schnittpunkte."""
    other = 1 - axis
    if entity[0] == "Linie":
        start, end = entity[1:3], entity[3:5]
        span = end[axis] - start[axis]
        if abs(span) < 1e-12:
            return []
        t = (value - start[axis]) / span
        if not -1e-9 <= t <= 1 + 1e-9:
            return []
        point = [0.0, 0.0]
        point[axis] = value
        point[other] = start[other] + t * (end[other] - start[other])
        return [(point[0], point[1])]
    if entity[0] in ("Kreis", "Bogen"):
        centre, radius = entity[1:3], entity[3]
        offset = value - centre[axis]
        if abs(offset) > radius:
            return []
        height = math.sqrt(max(radius * radius - offset * offset, 0.0))
        points = []
        for sign in (1.0, -1.0):
            point = [0.0, 0.0]
            point[axis] = value
            point[other] = centre[other] + sign * height
            if entity[0] == "Bogen":
                angle = math.degrees(math.atan2(point[1] - centre[1], point[0] - centre[0])) % 360
                span = (entity[5] - entity[4]) % 360 or 360
                if (angle - entity[4]) % 360 > span + 1e-7:
                    continue                    # außerhalb des Bogens
            points.append((point[0], point[1]))
        return points
    return []


def _distance_to(entity: list, x: float, y: float) -> float:
    """Abstand des Punkts (x, y) zur Kurve des Elements."""
    kind, v = entity[0], entity[1:]
    if kind == sk.POINT:
        return math.dist((x, y), (v[0], v[1]))
    if kind == sk.LINE3D:
        return _distance_to(sk.line(v[0], v[1], v[3], v[4]), x, y) if math.dist(v[0:2], v[3:5]) > 1e-9 \
            else math.dist((x, y), (v[0], v[1]))
    if kind == sk.SPLINE3D:
        # 3D-Spline: Abstand zu seinem Schatten in der Ebene (Stücke ohne Länge zählen als Punkt)
        track = sk.shadow(entity)
        return min(_distance_to(sk.line(*a, *b), x, y) if math.dist(a, b) > 1e-9 else math.dist((x, y), a)
                   for a, b in zip(track, track[1:]))
    if kind in (sk.SPLINE, sk.ELLIPSE, sk.CONIC, sk.CP_SPLINE):
        # Abstand zum Zug der kurzen Stücke, mit denen Spline und Ellipse gezeichnet werden
        track = sk.track_of(entity)
        return min(_distance_to(sk.line(*a, *b), x, y) for a, b in zip(track, track[1:]))
    if kind == "Linie":
        ax, ay, bx, by = v
        length = (bx - ax) ** 2 + (by - ay) ** 2
        t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length)) if length else 0.0
        return math.dist((x, y), (ax + t * (bx - ax), ay + t * (by - ay)))
    on_circle = abs(math.dist((x, y), (v[0], v[1])) - v[2])
    if kind == "Kreis":
        return on_circle
    # Bogen: nur innerhalb seines Winkelbereichs zählt der Abstand zum Kreis
    angle = _angle((v[0], v[1]), (x, y))
    span = (v[4] - v[3]) % 360 or 360
    return on_circle if (angle - v[3]) % 360 <= span else math.inf


class EntityTable(QTableWidget):
    """Tabelle aller Skizzenelemente mit direkt änderbaren Zahlenwerten."""

    edited = Signal()       # ein Wert wurde gültig geändert

    def __init__(self, entities: list, parent=None):
        super().__init__(0, 6, parent)
        self._entities = entities
        self._filling = False
        self.setHorizontalHeaderLabels(["Art", "Wert 1", "Wert 2", "Wert 3", "Wert 4", "Wert 5"])
        # Alle sechs Spalten sollen ohne Rollen in die Seitenleiste passen
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.itemChanged.connect(self._item_changed)

    def fill(self, selected: int | None = None) -> None:
        """Zeigt alle Elemente mit ihren Zahlenwerten; die Spaltenbedeutung steht im Tooltip."""
        self._filling = True
        # So viele Spalten, wie das längste Element Werte hat (ein Spline hat zwei je Punkt), höchstens 16
        columns = max(6, min(17, max((len(entity) for entity in self._entities), default=6)))
        if columns != self.columnCount():
            self.setColumnCount(columns)
            self.setHorizontalHeaderLabels(["Art", *(f"Wert {n}" for n in range(1, columns))])
        self.setRowCount(len(self._entities))
        for row, entity in enumerate(self._entities):
            kind = QTableWidgetItem(entity[0])
            kind.setFlags(kind.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.setItem(row, 0, kind)
            # Ein Spline hat beliebig viele Punkte; die Tabelle zeigt die ersten Werte
            names = (tuple(f"{'xy'[i % 2]}{i // 2 + 1}" for i in range(len(entity) - 1))
                     if entity[0] in (sk.SPLINE, sk.CP_SPLINE)
                     else tuple(f"{'xyz'[i % 3]}{i // 3 + 1}" for i in range(len(entity) - 1))
                     if entity[0] == sk.SPLINE3D else sk.ENTITY_FIELDS[entity[0]])
            for column in range(1, self.columnCount()):
                has_value = column <= len(names)
                item = QTableWidgetItem(f"{entity[column]:.3f}" if has_value else "")
                if has_value:
                    item.setToolTip(names[column - 1])
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.setItem(row, column, item)
        if selected is not None and selected < len(self._entities):
            self.selectRow(selected)
        self._filling = False

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() == 0:
            return
        entity = self._entities[item.row()]
        try:
            value = float(item.text().replace(",", "."))
        except ValueError:
            value = None
        changed = list(entity)
        if value is not None:
            changed[item.column()] = value
        # Nur brauchbare Werte übernehmen; sonst steht wieder der alte Wert in der Zelle
        accepted = value is not None and sk.check_entity(changed) is None
        if accepted:
            entity[item.column()] = value
        self.fill()
        if accepted:
            self.edited.emit()


def has_profile(entities: list, construction: list | None = None) -> bool:
    """Ergibt die Skizze mindestens einen geschlossenen Bereich? (unabhängig von ihrer Lage)"""
    if not entities:
        return False
    state = ft.BuildState()
    try:
        sk.Sketch(name="Probe", entities=entities, construction=list(construction or [])).apply(state)
    except ft.FeatureError:
        return False
    return "Probe" in state.profiles
