# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Skizzieren direkt in der 3D-Ansicht.

`SketchSession` übersetzt Mausbewegungen in der Ansicht auf die Skizzenebene und reicht sie
an die Zeichenlogik (`SketchCanvas`) weiter; Vorschau, Fangmarke und Raster werden in der
Ansicht gezeichnet. `SketchPanel` ist das zugehörige Eigenschaftenfeld mit Werkzeugen und
Maßtabelle. Die Skizze selbst ist dabei schon ein Feature der Timeline und läuft live mit.
"""

from __future__ import annotations

import math

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QLabel, QLineEdit, QListWidget, QPushButton,
    QInputDialog, QSpinBox, QVBoxLayout, QWidget,
)

from ..core import constraints as cs
from ..core import parameters as pm
from ..core import sketch as sk
from .icons import icon
from .viewport import grid_patch
from .sketch_editor import (_TOOL_ICONS, CENTER_RECT, LINE_CENTER, LINE_CONSTRUCTION, LINE_NORMAL, PICK_TOOLS,
                            PROJECT_TOOL, INTERSECT_TOOL, TANGENT_ARC, THREE_POINT_ARC, THREE_POINT_CIRCLE, THREE_POINT_RECT,
                            THREE_TANGENT_CIRCLE, TOOLS, TWO_POINT_CIRCLE, TWO_TANGENT_CIRCLE, EntityTable,
                            SketchCanvas, has_profile)

# Werkzeugfamilien wie in Fusion: im laufenden Werkzeug lässt sich in der Palette die Art wechseln
TOOL_FAMILIES = (("Rechteck", CENTER_RECT, THREE_POINT_RECT),
                 ("Kreis", THREE_POINT_CIRCLE, TWO_POINT_CIRCLE, TWO_TANGENT_CIRCLE, THREE_TANGENT_CIRCLE),
                 ("Bogen", THREE_POINT_ARC, TANGENT_ARC),
                 (sk.SPLINE, sk.CP_SPLINE))

_PROJECTION_STEPS = 16  # Linienstücke, mit denen eine projizierte freie Kurve angenähert wird

_DIMENSION_GAP = 26     # Abstand der Maßlinie vom Element in Bildpunkten
_SYMBOL_GAP = 18        # Abstand der Abhängigkeitszeichen vom Element in Bildpunkten
_SYMBOL_STEP = 30       # Abstand mehrerer Zeichen am selben Element


def number_text(value: float) -> str:
    """Maßzahl mit Komma, wie sie in der Ansicht und in der Liste steht."""
    return f"{value:.2f}".replace(".", ",")

_GRID_LINES = 40        # Rasterlinien je Richtung um den Ursprung, wenn der Ausschnitt nicht bestimmbar ist
_GRID_LIMIT = 400       # mehr Linien je Richtung werden nicht gezeichnet (Raster dann ausgeblendet)
_CIRCLE_SEGMENTS = 72   # Geradenstücke, mit denen ein Vollkreis gezeichnet wird


def outline_points(entity: list) -> list[tuple[float, float]]:
    """Punkte eines Linienzugs, der das Element in Skizzenkoordinaten nachzeichnet."""
    kind, v = entity[0], entity[1:]
    if kind == sk.LINE3D:
        return [(v[0], v[1]), (v[3], v[4])]       # Schatten in der Ebene (die Ansicht zeichnet sie räumlich)
    if kind == sk.SPLINE3D:
        return sk.shadow(entity)
    if kind == sk.POINT:
        # Skizzenpunkt: kleine Raute um die Stelle
        size = 0.6
        return [(v[0] - size, v[1]), (v[0], v[1] + size), (v[0] + size, v[1]), (v[0], v[1] - size), (v[0] - size, v[1])]
    if kind == "Linie":
        return [(v[0], v[1]), (v[2], v[3])]
    if kind == sk.SPLINE:
        return sk.spline_points(entity)
    if kind == sk.ELLIPSE:
        return sk.ellipse_points(entity)
    if kind == sk.CONIC:
        return sk.conic_points(entity)
    if kind == sk.CP_SPLINE:
        return sk.control_spline_points(entity)
    start, span = (0.0, 360.0) if kind == "Kreis" else (v[3], (v[4] - v[3]) % 360 or 360.0)
    steps = max(2, int(_CIRCLE_SEGMENTS * span / 360))
    return [(v[0] + v[2] * math.cos(math.radians(start + span * i / steps)),
             v[1] + v[2] * math.sin(math.radians(start + span * i / steps))) for i in range(steps + 1)]




def screen_pixel_factor(widget) -> float:
    """Faktor für Fangweiten in Bildpunkten (1 … 3): das Größere aus Pixeldichte/96 dpi und Bildschirmhöhe/1440.

    Auf einem 4K-Bildschirm ohne Skalierung (Nutzer: 3840 × 2160, 93 dpi – groß, keine feinen Pixel) sind feste
    9 Bildpunkte zu wenig; Fusion läuft dort mit vergrößerter Oberfläche und fängt entsprechend weiter. Mit 2160/1440
    = 1,5 wird der Punktfang dort etwa 14 Bildpunkte groß."""
    try:
        screen = widget.screen()
        dpi = screen.physicalDotsPerInch() if screen is not None else 96.0
        height = screen.size().height() if screen is not None else 1080
    except Exception:
        dpi, height = 96.0, 1080
    return max(1.0, min(3.0, max(dpi / 96.0, height / 1440.0)))

class SketchSession:
    """Nimmt die Maus der 3D-Ansicht entgegen, solange eine Skizze gezeichnet wird."""

    def __init__(self, viewport, feature: sk.Sketch, entities: list, reference: list | None = None,
                 constraints: list | None = None, axes=None, resolver=None,
                 construction: list | None = None, edge_picker=None, texts: list | None = None,
                 centerlines: list | None = None, vertex_picker=None, projected: list | None = None,
                 section_picker=None):
        self.viewport = viewport
        # Liefert zu einem Fensterpunkt die getroffenen Körperkanten (für „Projizieren“)
        self._edge_picker = edge_picker
        # Schnittkurve: liefert zu Fensterpunkt und Skizzenebene die Schnittkanten des getroffenen Körpers
        self._section_picker = section_picker
        # 3D-Skizze: liefert zu einem Fensterpunkt die getroffene Körperecke im Raum (oder None)
        self._vertex_picker = vertex_picker
        self._z_last = 0.0          # Höhe (längs der Normale) des letzten Punkts im laufenden 3D-Linienzug
        self._resolver = resolver       # rechnet Ausdrücke der Maße mit den Benutzerparametern aus
        # `axes`: die im Modell berechnete Lage (nötig, wenn die Skizze auf einer Konstruktionsebene liegt)
        if axes is None:
            axes = sk.frame_axes(feature.plane, feature.offset, feature.frame)
        self._origin = np.array([axes.Location().X(), axes.Location().Y(), axes.Location().Z()])
        self._x = np.array([axes.XDirection().X(), axes.XDirection().Y(), axes.XDirection().Z()])
        self._y = np.array([axes.YDirection().X(), axes.YDirection().Y(), axes.YDirection().Z()])
        self.normal = np.cross(self._x, self._y)
        # Zustand des Zeichenwerkzeugs; die Fläche selbst wird nie angezeigt
        self.canvas = SketchCanvas(entities, reference=reference, constraints=constraints,
                                   construction=construction, texts=texts, centerlines=centerlines,
                                   projected=projected)
        self.canvas.text_requested.connect(self._ask_text)
        self.canvas.dimension_picked.connect(self.edit_dimension)
        # Nummern der noch beweglichen Elemente; neu bestimmt, sobald sich die Skizze ändert
        self._free: set[int] | None = None
        self.canvas.changed.connect(self._forget_free)
        # Beim Zoomen und Schwenken wandert das Raster mit und wechselt seine Feinheit
        self.viewport.view_changed.connect(self.show)
        self._mouse = (0.0, 0.0)
        # Zu jedem Text in der Ansicht: Nummer der Abhängigkeit und Lage in Skizzenkoordinaten
        self._label_rules: list[int] = []
        self._label_points: list[tuple[float, float]] = []

    # --- Umrechnung ---------------------------------------------------------

    def world(self, x: float, y: float, z: float = 0.0) -> np.ndarray:
        """Punkt der Skizzenebene (bzw. z über ihr) im Raum."""
        return self._origin + self._x * x + self._y * y + self.normal * z

    def local(self, point) -> tuple[float, float, float]:
        """Raumpunkt in Skizzenkoordinaten: (x, y, Abstand längs der Normale)."""
        offset = np.array(point, dtype=float) - self._origin
        return float(offset @ self._x), float(offset @ self._y), float(offset @ self.normal)

    def _on_plane(self, x: float, y: float) -> tuple[float, float] | None:
        """Skizzenkoordinaten unter dem Fensterpunkt (None, wenn der Blick die Ebene nicht trifft)."""
        eye, ray, pixel_angle = self.viewport.pick_ray(x, y)
        denominator = float(ray @ self.normal)
        if abs(denominator) < 1e-9:
            return None
        distance = float((self._origin - eye) @ self.normal) / denominator
        if distance <= 0:
            return None
        hit = eye + ray * distance - self._origin
        # Fangweite der Zeichenlogik ist in Bildpunkten angegeben: Maßstab an dieser Stelle mitgeben
        self.canvas._scale = 1.0 / max(pixel_angle * distance, 1e-9)
        self.canvas.pixel_factor = screen_pixel_factor(self.viewport)
        return float(hit @ self._x), float(hit @ self._y)

    def dashed(self, entities: list, pattern: tuple) -> np.ndarray:
        """Wie `segments`, aber gestrichelt: `pattern` = (Strich, Lücke, …) in Bildpunkten, wiederholt."""
        pixel = self._pixel()
        lengths = [max(part * pixel, 1e-9) for part in pattern]
        points = []
        for entity in entities:
            if sk.check_entity(entity) is not None:
                continue
            outline = outline_points(entity)
            phase, position = 0, 0.0         # Abschnitt im Muster und schon zurückgelegter Weg darin
            for start, end in zip(outline, outline[1:]):
                length = math.dist(start, end)
                done = 0.0
                while done < length - 1e-12:
                    step = min(lengths[phase] - position, length - done)
                    if phase % 2 == 0:      # gerade Abschnitte sind Striche, ungerade Lücken
                        a = done / length
                        b = (done + step) / length
                        points += [self.world(start[0] + (end[0] - start[0]) * a, start[1] + (end[1] - start[1]) * a),
                                   self.world(start[0] + (end[0] - start[0]) * b, start[1] + (end[1] - start[1]) * b)]
                    done += step
                    position += step
                    if position >= lengths[phase] - 1e-12:
                        phase, position = (phase + 1) % len(lengths), 0.0
        return np.array(points, dtype=np.float32).reshape(-1, 3)

    def free_points(self) -> np.ndarray:
        """Offene Endpunkte (gehören nur zu einem Element) als kleine Quadrate – Fusions Punktanzeige."""
        ends = {}
        for entity in self.canvas.entities:
            found = sk._ends(entity) if entity[0] != sk.POINT else None
            for point in (found or ()):
                key = (round(point[0], 6), round(point[1], 6))
                ends[key] = ends.get(key, 0) + 1
        size = 3.0 * self._pixel()
        points = []
        for (x, y), count in ends.items():
            if count != 1:
                continue            # verbundene Punkte zeichnet Fusion nicht
            corners = [(x - size, y - size), (x + size, y - size), (x + size, y + size), (x - size, y + size)]
            for start, end in zip(corners, corners[1:] + corners[:1]):
                points += [self.world(*start), self.world(*end)]
        return np.array(points, dtype=np.float32).reshape(-1, 3)

    def curvature_comb(self) -> np.ndarray:
        """Krümmungskamm der gewählten Freiformkurven: an jeder Stützstelle ein Zahn senkrecht zur Kurve,
        so lang wie die Krümmung dort (größte Zahnlänge ≈ ein Fünftel der Kurvenlänge), dazu die Hüllkurve."""
        points = []
        for index in self.canvas.chosen():
            entity = self.canvas.entities[index]
            track = sk.track_of(entity) if entity[0] in (sk.SPLINE, sk.CP_SPLINE, sk.CONIC, sk.ELLIPSE) else None
            if not track or len(track) < 3:
                continue
            teeth = []
            for before, here, after in zip(track, track[1:], track[2:]):
                # Krümmung aus dem Kreis durch drei Nachbarpunkte (mit Vorzeichen: links = positiv)
                ax, ay = here[0] - before[0], here[1] - before[1]
                bx, by = after[0] - here[0], after[1] - here[1]
                cross = ax * by - ay * bx
                lengths = math.hypot(ax, ay) * math.hypot(bx, by) * math.hypot(after[0] - before[0], after[1] - before[1])
                curvature = 2 * cross / lengths if lengths > 1e-15 else 0.0
                tx, ty = after[0] - before[0], after[1] - before[1]
                norm = math.hypot(tx, ty) or 1.0
                teeth.append((here, (-ty / norm, tx / norm), curvature))
            total = sum(math.dist(a, b) for a, b in zip(track, track[1:]))
            biggest = max((abs(k) for _p, _n, k in teeth), default=0.0)
            scale = 0.2 * total / biggest if biggest > 1e-12 else 0.0
            tips = []
            for (px, py), (nx, ny), curvature in teeth:
                # Zahn auf die Außenseite der Biegung (gegen den Krümmungsmittelpunkt)
                tip = (px - nx * curvature * scale, py - ny * curvature * scale)
                points += [self.world(px, py), self.world(*tip)]
                tips.append(tip)
            for a, b in zip(tips, tips[1:]):
                points += [self.world(*a), self.world(*b)]
        return np.array(points, dtype=np.float32).reshape(-1, 3)

    def osculating_circles(self) -> np.ndarray:
        """Schmiegekreise an Kurvenpunkten mit festgelegtem Krümmungsradius (gestrichelt)."""
        circles = []
        for rule in self.canvas.constraints:
            if rule[0] != cs.POINT_CURVATURE or rule[1] >= len(self.canvas.entities):
                continue
            entity = self.canvas.entities[rule[1]]
            if cs.check(self.canvas.entities, rule) is not None:
                continue
            point = sk.fit_points(entity)[rule[5]]
            tip = sk.handle_tips(entity)[rule[5]]
            found = cs._point_curvatures(entity, np.array(entity[1:], dtype=float), 0, rule[5])
            bend = sum(c for c, _l in found) / max(len(found), 1)
            tx, ty = tip[0] - point[0], tip[1] - point[1]
            norm = math.hypot(tx, ty) or 1.0
            side = 1.0 if bend >= 0 else -1.0
            centre = (point[0] - ty / norm * side * rule[3], point[1] + tx / norm * side * rule[3])
            circles.append(sk.circle(*centre, rule[3]))
        return self.dashed(circles, (6, 5)) if circles else np.zeros((0, 3), dtype=np.float32)

    def look_at(self) -> None:
        """Palette „Draufsicht“: senkrecht auf die Skizzenebene schauen."""
        self.viewport.look_along(self.normal)

    def set_slice(self, on: bool) -> None:
        """Palette „Slice“: alles vor der Skizzenebene ausblenden (Fusion schneidet die Körper dort auf)."""
        self.sliced = on
        if on:
            # sichtbar bleibt, wo n·p ≤ n·o (hinter der Ebene); etwas Spiel, damit Flächen in der Ebene bleiben
            offset = float(self.normal @ self._origin) + 1e-3
            self.viewport.set_clip((-self.normal[0], -self.normal[1], -self.normal[2], offset))
        else:
            self.viewport.set_clip(None)

    def segments(self, entities: list) -> np.ndarray:
        """Liniensegmente der Elemente im Raum (je zwei Punkte ein Segment)."""
        points = []
        for entity in entities:
            if sk.check_entity(entity) is not None:
                continue
            if entity[0] in sk.SPATIAL:
                track = [self.world(*point) for point in sk.track3d(entity)]       # räumlich
                for start, end in zip(track, track[1:]):
                    points += [start, end]
                continue
            outline = [self.world(*point) for point in outline_points(entity)]
            for start, end in zip(outline, outline[1:]):
                points += [start, end]
        return np.array(points, dtype=np.float32).reshape(-1, 3)

    # --- Eingaben aus der Ansicht -------------------------------------------

    def _ask_text(self, x: float, y: float) -> None:
        """Fragt Wortlaut und Schrifthöhe ab und setzt den Text an die angeklickte Stelle."""
        text, accepted = QInputDialog.getText(self.viewport, "Text", "Text:")
        if not accepted or not text.strip():
            return
        height, accepted = QInputDialog.getDouble(self.viewport, "Text", "Schrifthöhe (mm):",
                                                  self.canvas.text_height, 0.5, 1000.0, 1)
        if not accepted:
            return
        # Text am Pfad (Runde 96, wie Fusion): auf eine Kurve geklickt, läuft der Text ab dieser Stelle an ihr entlang
        path = self.canvas._entity_at(x, y)
        if path is not None and self.canvas.entities[path][0] in ("Linie", "Bogen", "Kreis", sk.SPLINE,
                                                                   sk.ELLIPSE, sk.CONIC, sk.CP_SPLINE):
            self.canvas.add_text(text, self._distance_along(path, x, y), 0.0, height, path=path)
        else:
            self.canvas.add_text(text, x, y, height)
        self.redraw()

    def _axes(self):
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
        return gp_Ax2(gp_Pnt(*self._origin), gp_Dir(*self.normal), gp_Dir(*self._x))

    def _distance_along(self, index: int, x: float, y: float) -> float:
        """Abstand vom Anfang des Elements `index` bis zur Stelle, die (x, y) am nächsten liegt (entlang der Kurve)."""
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GCPnts import GCPnts_AbscissaPoint
        from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
        from OCP.BRep import BRep_Tool
        edge = sk._edge(self._axes(), self.canvas.entities[index])
        adaptor = BRepAdaptor_Curve(edge)
        first, last = adaptor.FirstParameter(), adaptor.LastParameter()
        curve = BRep_Tool.Curve_s(edge, first, last)        # (die Anbindung gibt nur die Kurve zurück)
        projector = GeomAPI_ProjectPointOnCurve(sk._point(self._axes(), x, y), curve, first, last)
        if projector.NbPoints() == 0:
            return 0.0
        return float(GCPnts_AbscissaPoint.Length_s(adaptor, first, projector.LowerDistanceParameter()))

    def _path_text_points(self, entry: list) -> list:
        """Umrisse eines Textes am Pfad als Punktpaare (aus den Kanten der Buchstaben)."""
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        try:
            letters = sk.text_on_path(self._axes(), entry, sk._edge(self._axes(), self.canvas.entities[entry[6]]))
        except Exception:
            return []           # unbrauchbar: die Meldung kommt beim Berechnen der Skizze
        points, explorer = [], TopExp_Explorer(letters, TopAbs_EDGE)
        while explorer.More():
            curve = BRepAdaptor_Curve(TopoDS.Edge(explorer.Current()))
            samples = [curve.Value(curve.FirstParameter() + (curve.LastParameter() - curve.FirstParameter()) * k / 6)
                       for k in range(7)]
            for a, b in zip(samples, samples[1:]):
                points += [(a.X(), a.Y(), a.Z()), (b.X(), b.Y(), b.Z())]
            explorer.Next()
        return points

    def text_segments(self) -> np.ndarray:
        """Umrisse aller Texte der Skizze als Liniensegmente im Raum."""
        points = []
        for entry in self.canvas.texts:
            if len(entry) == 7 and 0 <= entry[6] < len(self.canvas.entities):
                points += self._path_text_points(entry)      # Text am Pfad: Umrisse aus den echten Buchstaben
                continue
            text, x, y, height, rotation, *font = entry[:6]
            cosine, sine = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
            try:
                outline = sk.text_outline(text, height, font[0] if font else "")
            except Exception:
                continue        # unbrauchbarer Text: die Fehlermeldung kommt beim Berechnen der Skizze
            for line in outline:
                placed = [self.world(x + u * cosine - v * sine, y + u * sine + v * cosine) for u, v in line]
                for start, end in zip(placed, placed[1:]):
                    points += [start, end]
        return np.array(points, dtype=np.float32).reshape(-1, 3)

    def _forget_free(self) -> None:
        self._free = None

    def typed(self, text: str | None) -> bool:
        """Beim Zeichnen wurde eine Ziffer getippt (oder Tab, dann `text` None): öffnet das Eingabefeld am aktiven
        Maßfeld; False, wenn gerade keins passt."""
        prompt = self.canvas.typed_prompt()
        if prompt is None or self.viewport.number_is_open():
            return False

        def confirmed(value: float, expression: str) -> None:
            before = (len(self.canvas.entities), len(self.canvas._points))
            self.canvas.apply_typed(value, expression, more=self.viewport.confirmed_with_tab(),
                                    backward=self.viewport.confirmed_backward())
            self.redraw()
            self.viewport.show_info("")
            if before == (len(self.canvas.entities), len(self.canvas._points)) and self.canvas.typed_prompt():
                # nichts gesetzt (Tab oder erst die Breite): das nächste Feld ist aktiv und gleich zum Tippen offen
                QTimer.singleShot(0, lambda: self._ask_typed(None))

        self._confirm_typed = confirmed
        self._ask_typed(text)
        return True

    def _ask_typed(self, text: str | None) -> None:
        """Öffnet das Eingabefeld genau am aktiven Maßfeld (wie Fusion); ohne Text steht dort der jetzige Wert."""
        prompt = self.canvas.typed_prompt()
        if prompt is None:
            return
        self.viewport.show_info(f"{prompt} eintippen – Tab/Umschalt+Tab wechselt das Feld und hält den Wert fest, "
                                "Eingabe setzt das Element, Esc verwirft die Eingabe")
        field = next((f for f in self.canvas.hud_fields() if f["name"] == prompt), None)
        anchor = field["anchor"] if field else self.canvas._cursor
        value = field["value"] if field else 1.0
        locked = self.canvas._hud_locked.get(prompt)
        angle = prompt == "Winkel"
        def cancelled() -> None:
            self.canvas.hud_typing_cancelled()
            self.viewport.show_info("")
            self.redraw()

        def edited(typed: str) -> None:
            # wie in Fusion gilt der Wert schon beim Tippen: die Maus ändert dieses Maß dann nicht mehr
            self.canvas.hud_typing(prompt, *self._read_typed(typed))
            self.redraw()

        self.viewport.edit_number(self.world(*anchor), locked[0] if locked else value, self._confirm_typed,
                                  suffix=" °" if angle else " mm", minimum=-360.0 if angle else 0.001,
                                  cancelled=cancelled, edited=edited,
                                  expression=locked[1] if locked else "",
                                  resolver=self._resolver, initial_text=text)

    def _read_typed(self, text: str) -> tuple[float | None, str]:
        """Wert eines gerade getippten Textes: (Zahl, Ausdruck); (None, "") solange er nicht brauchbar ist."""
        text = text.strip().removesuffix("mm").removesuffix("°").strip()
        if not text:
            return None, ""
        if pm.is_number(text):
            return float(text.replace(",", ".")), ""
        try:
            return float((self._resolver or pm.evaluate)(text)), text
        except Exception:
            return None, ""

    def drag_start(self, x: float, y: float) -> bool:
        """Die linke Taste wurde gedrückt: greift mit „Auswählen“ einen Punkt zum Ziehen."""
        if self.viewport.number_is_open() or self.viewport.label_at(x, y) is not None:
            return False
        point = self._on_plane(x, y)
        return point is not None and self.canvas.drag_start(*point)

    def drag_end(self) -> None:
        self.canvas.drag_end()
        # Während des Ziehens rechnet das Modell nicht mit (siehe SketchPanel._canvas_changed); jetzt einmal melden,
        # damit die späteren Schritte (Extrusion, Rundung …) dem neuen Stand folgen
        self.canvas.changed.emit()
        self.redraw()

    def double_click(self, x: float, y: float) -> None:
        """Doppelklick in der Ansicht: mit „Auswählen“ den ganzen Linienzug wählen."""
        point = self._on_plane(x, y)
        if point is not None and self.canvas.tool == "Auswählen":
            self.canvas.select_chain(*point)
            self.redraw()

    def set_additive(self, on: bool) -> None:
        """Der nächste Klick bzw. Rahmen erweitert die Auswahl, statt sie zu ersetzen."""
        self.canvas.additive = on

    def move(self, x: float, y: float) -> None:
        self._mouse = (x, y)
        point = self._on_plane(x, y)
        if point is not None and self.canvas.is_dragging():
            # Erst ab einer echten Bewegung ziehen – ein bloßer Klick wählt nur aus
            if self.viewport.moved_since_press():
                self.canvas.drag_to(*point)
            self.redraw()
            return
        if point is not None:
            self.canvas._cursor, self.canvas._snapped_to_point = self.canvas.aim(*point)
        self.redraw()

    def press(self, x: float, y: float) -> None:
        self._mouse = (x, y)
        self.viewport.close_number()
        # Ein Klick auf eine Maßzahl öffnet ihr Eingabefeld, egal welches Werkzeug aktiv ist
        label = self.viewport.label_at(x, y)
        if label is not None and label < len(self._label_rules):
            rule = self._label_rules[label]
            if self.canvas.constraints[rule][0] in cs.DIMENSIONS and cs.is_driven(self.canvas.constraints[rule]):
                self.canvas.message = "Getriebenes Maß: zeigt nur den Wert – „Maß getrieben/treibend“ schaltet um"
                self.canvas.selected_rule = rule
                self.canvas.changed.emit()
                self.redraw()
                return
            if self.canvas.constraints[rule][0] in cs.DIMENSIONS:
                self.edit_dimension(rule)
                return
            # ein Abhängigkeitssymbol: wählen – Entf löscht es (wie in Fusion). Liegt unter dem Zeiger zugleich
            # ein Element (die Symbole stehen dicht an ihren Linien), gehört der Klick dem Element.
            under = self._on_plane(x, y)
            if self.canvas.tool != "Auswählen" or (under is not None and self.canvas._entity_at(*under) is not None):
                label = None            # beim Zeichnen fangen Symbole keine Klicks ab
        if label is not None and label < len(self._label_rules):
            rule = self._label_rules[label]
            self.canvas.selected_rule = rule
            self.canvas.message = f"{self.canvas.constraints[rule][0]} gewählt – Entf löscht die Abhängigkeit"
            self.canvas.changed.emit()
            self.redraw()
            return
        self.canvas.selected_rule = None
        if self.canvas.tool == PROJECT_TOOL:
            edges = self._edge_picker(x, y) if self._edge_picker is not None else []
            self.canvas.add_entities(self.projected(edges))
            self.redraw()
            return
        if self.canvas.tool == INTERSECT_TOOL:
            # Schnittkurve: wo die Skizzenebene den angeklickten Körper schneidet (liegt schon in der Ebene)
            plane = (tuple(self._origin), tuple(self.normal), tuple(self._x))
            edges = self._section_picker(x, y, plane) if self._section_picker is not None else []
            if not edges:
                self.canvas.message = "Die Skizzenebene schneidet diesen Körper nicht"
            self.canvas.add_entities(self.projected(edges))
            self.redraw()
            return
        point = self._on_plane(x, y)
        if point is None:
            return
        if self.canvas.tool in PICK_TOOLS:
            # Elemente werden dort getroffen, wo geklickt wurde – ohne Fang auf das Raster
            self.canvas.click(*point)
            self.redraw()
            return
        if self._press_3d(x, y, point):
            return
        if self._press_3d_spline(x, y):
            return
        snapped, on_point = self.canvas.aim(*point)
        self.canvas._cursor, self.canvas._snapped_to_point = snapped, on_point
        self.canvas.click(*snapped)
        self._z_last = 0.0                  # gewöhnlicher Punkt: er liegt in der Ebene
        self.redraw()

    def _press_3d(self, x: float, y: float, point: tuple[float, float]) -> bool:
        """3D-Skizze (Palette): das Linienwerkzeug fängt an Körperecken auch außerhalb der Ebene (wie Fusion).
        True, wenn der Klick so verarbeitet wurde; sonst zeichnet die gewöhnliche Linie weiter."""
        if not (self.canvas.palette.get("3d") and self.canvas.tool == "Linie" and self._vertex_picker is not None):
            return False
        vertex = self._vertex_picker(x, y)
        if vertex is None and abs(self._z_last) < 1e-9:
            return False                    # beide Enden in der Ebene: eine gewöhnliche Linie
        end = self.local(vertex) if vertex is not None else (*self.canvas.aim(*point)[0], 0.0)
        if self.canvas._points:
            start = (*self.canvas._points[-1], self._z_last)
            entity = (sk.line3d(*start, *end) if abs(start[2]) > 1e-9 or abs(end[2]) > 1e-9
                      else sk.line(*start[:2], *end[:2]))
            if sk.check_entity(entity) is None:
                self.canvas.entities.append(entity)
                self.canvas.changed.emit()
        self.canvas._points = [end[:2]]     # der Linienzug geht vom neuen Punkt weiter
        self._z_last = end[2]
        self.redraw()
        return True

    def _press_3d_spline(self, x: float, y: float) -> bool:
        """3D-Skizze mit dem Spline-Werkzeug: ein Klick auf eine Körperecke setzt den nächsten Spline-Punkt dort im
        Raum (Höhe in `canvas.point_heights`); gewöhnliche Klicks setzen Punkte in der Ebene (Höhe 0).
        Beim Beenden wird daraus ein 3D-Spline, sobald ein Punkt außerhalb der Ebene liegt. True, wenn verarbeitet."""
        if not (self.canvas.palette.get("3d") and self.canvas.tool == sk.SPLINE and self._vertex_picker is not None):
            return False
        heights = self.canvas.point_heights
        heights[:] = (heights + [0.0] * len(self.canvas._points))[:len(self.canvas._points)]   # gleich lang halten
        vertex = self._vertex_picker(x, y)
        if vertex is None:
            return False                    # Punkt in der Ebene: die gewöhnliche Spline-Eingabe
        end = self.local(vertex)
        self.canvas._points.append((end[0], end[1]))
        heights.append(end[2])
        self.canvas.update()
        self.redraw()
        return True

    def project_face_outline(self, edges: list) -> int:
        """Skizze auf einer Körperfläche: ihr Umriss wird wie in Fusion automatisch projiziert (lila, fest) und
        zählt zum Profil – eine Form darin ergibt so gleich zwei Bereiche (sie selbst und die Fläche drumherum)."""
        added = self.canvas.add_entities(self.projected(edges))
        self.canvas.message = "Umriss der Fläche projiziert" if added else ""
        self._forget_free()
        return added

    def projected(self, edges: list) -> list:
        """Körperkanten senkrecht auf die Skizzenebene projiziert, als Skizzenelemente.

        Gerade Kanten werden Linien, Kreise parallel zur Ebene Kreise oder Bögen; alles
        andere wird durch kurze Linien angenähert. Die Elemente sind danach unabhängig
        vom Körper (sie wandern nicht mit, wenn er sich ändert).
        """
        def flat(point) -> tuple[float, float]:
            offset = np.array([point.X(), point.Y(), point.Z()]) - self._origin
            return float(offset @ self._x), float(offset @ self._y)

        entities = []
        for edge in edges:
            curve = BRepAdaptor_Curve(edge)
            first, last = curve.FirstParameter(), curve.LastParameter()
            start, end = flat(curve.Value(first)), flat(curve.Value(last))
            if curve.GetType() == GeomAbs_Line:
                entities.append(sk.line(*start, *end))      # Länge 0 (Kante senkrecht zur Ebene) fällt später weg
                continue
            if curve.GetType() == GeomAbs_Circle:
                circle = curve.Circle()
                axis = circle.Axis().Direction()
                if abs(abs(float(np.array([axis.X(), axis.Y(), axis.Z()]) @ self.normal)) - 1.0) < 1e-9:
                    centre, radius = flat(circle.Location()), circle.Radius()
                    if abs(abs(last - first) - 2 * math.pi) < 1e-9:
                        entities.append(sk.circle(*centre, radius))
                        continue
                    middle = flat(curve.Value((first + last) / 2))
                    angles = [math.degrees(math.atan2(p[1] - centre[1], p[0] - centre[0])) % 360
                              for p in (start, middle, end)]
                    # Bögen laufen gegen den Uhrzeigersinn: liegt die Mitte nicht auf dem Weg, Enden tauschen
                    forward = (angles[1] - angles[0]) % 360 < (angles[2] - angles[0]) % 360
                    entities.append(sk.arc(*centre, radius, *((angles[0], angles[2]) if forward
                                                              else (angles[2], angles[0]))))
                    continue
            # Sonstige Kurve (auch schräg liegende Kreise): Linienzug aus kurzen Stücken
            points = [flat(curve.Value(first + (last - first) * k / _PROJECTION_STEPS))
                      for k in range(_PROJECTION_STEPS + 1)]
            entities += [sk.line(*a, *b) for a, b in zip(points, points[1:])]
        return entities

    def finish(self) -> None:
        """Beendet das laufende Element (bei Linien: den Linienzug)."""
        self._z_last = 0.0
        self.canvas.finish()
        self.redraw()

    def is_drawing(self) -> bool:
        """Sind schon Punkte eines noch nicht fertigen Elements gesetzt – oder wartet ein Mehrklick-Werkzeug
        (Bemaßung, Tangentenkreis, Übergangskurve, Ausrichten) auf seinen nächsten Klick?"""
        canvas = self.canvas
        return bool(canvas._points or getattr(canvas, "_dimension_first", None) or getattr(canvas, "_dimension_place", None)
                    or getattr(canvas, "_tangent_lines", None) or getattr(canvas, "_blend_first", None)
                    or getattr(canvas, "_align_first", None))

    # --- Maße und Abhängigkeiten ---------------------------------------------

    def _pixel(self) -> float:
        """Länge eines Bildpunkts in mm auf der Skizzenebene (in Bildmitte gemessen)."""
        centre = self._on_plane_raw(self.viewport.width() / 2, self.viewport.height() / 2)
        beside = self._on_plane_raw(self.viewport.width() / 2 + 1, self.viewport.height() / 2)
        if centre is None or beside is None:
            return 1.0 / max(self.canvas._scale, 1e-9)
        return max(math.dist(centre, beside), 1e-9)

    def _on_plane_raw(self, x: float, y: float) -> tuple[float, float] | None:
        """Wie `_on_plane`, aber ohne den Maßstab der Zeichenlogik zu verstellen."""
        scale = self.canvas._scale
        point = self._on_plane(x, y)
        self.canvas._scale = scale
        return point

    def _group_centres(self) -> list[tuple[float, float]]:
        """Je Element die Mitte des Linienzugs, zu dem es gehört (Elemente mit gemeinsamen Endpunkten).

        Daran richtet sich aus, welche Seite eines Elements „außen“ ist.
        """
        entities = self.canvas.entities
        outlines = [outline_points(entity) for entity in entities]
        group = list(range(len(entities)))
        ends = [(outline[0], outline[-1]) for outline in outlines]
        for first in range(len(entities)):
            for second in range(first + 1, len(entities)):
                if any(math.dist(a, b) < 1e-6 for a in ends[first] for b in ends[second]):
                    old, new = group[second], group[first]
                    group = [new if g == old else g for g in group]
        centres = {}
        for g in set(group):
            points = [point for index, outline in enumerate(outlines) if group[index] == g for point in outline]
            centres[g] = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
        return [centres[g] for g in group]

    def _annotations(self) -> tuple[list, list, list, list]:
        """Texte und Maßlinien: (Lagen, Texte, Nummern der Abhängigkeiten, Linienstücke)."""
        pixel = self._pixel()
        # Räumliche Elemente werden für die Beschriftung durch ihren Schatten in der Ebene vertreten: die 3D-Linie
        # als gewöhnliche Linie, der 3D-Spline als Punkt an seinem Anfang (Maß und Zeichen stehen in der Ebene)
        entities = [sk.line(entity[1], entity[2], entity[4], entity[5]) if entity[0] == sk.LINE3D
                    else sk.point(entity[1], entity[2]) if entity[0] == sk.SPLINE3D else entity
                    for entity in self.canvas.entities]
        points, texts, rules, lines = [], [], [], []
        symbols_at: dict[int, int] = {}     # Element -> Zahl der dort schon stehenden Zeichen
        centres = self._group_centres()

        def beside(index: int, gap: float, slot: int = 0) -> tuple[float, float]:
            """Stelle neben dem Element: quer um `gap` versetzt, längs um `slot` Zeichenbreiten."""
            kind, v = entities[index][0], entities[index][1:]
            if kind == "Linie":
                length = math.dist(v[0:2], v[2:4]) or 1.0
                along = ((v[2] - v[0]) / length, (v[3] - v[1]) / length)
                across = (-along[1], along[0])
                middle = ((v[0] + v[2]) / 2, (v[1] + v[3]) / 2)
                # Maße stehen außen, Zeichen innen: "quer" zeigt von der Mitte der Skizze weg
                centre = centres[index]
                if (middle[0] - centre[0]) * across[0] + (middle[1] - centre[1]) * across[1] < 0:
                    across = (-across[0], -across[1])
                return (middle[0] + across[0] * gap + along[0] * slot * _SYMBOL_STEP * pixel,
                        middle[1] + across[1] * gap + along[1] * slot * _SYMBOL_STEP * pixel)
            # Kreis: schräg oben rechts; Bogen: in der Mitte seines Winkelbereichs
            angle = 45.0 if kind == "Kreis" else v[3] + ((v[4] - v[3]) % 360 or 360.0) / 2
            angle = math.radians(angle + slot * 25.0)
            reach = v[2] + gap if gap >= 0 else max(v[2] + gap, v[2] / 2)
            return v[0] + reach * math.cos(angle), v[1] + reach * math.sin(angle)

        def middle_of(index: int) -> tuple[float, float]:
            """Bezugspunkt eines Elements: Mitte der Linie bzw. Mittelpunkt."""
            v = entities[index][1:]
            return ((v[0] + v[2]) / 2, (v[1] + v[3]) / 2) if entities[index][0] == "Linie" else (v[0], v[1])

        def foot(index: int, point: tuple[float, float]) -> tuple[float, float]:
            """Fußpunkt des Lots von `point` auf die Gerade der Linie `index`."""
            v = entities[index][1:]
            dx, dy = v[2] - v[0], v[3] - v[1]
            t = ((point[0] - v[0]) * dx + (point[1] - v[1]) * dy) / ((dx * dx + dy * dy) or 1.0)
            return v[0] + dx * t, v[1] + dy * t

        chosen_now = set(self.canvas.chosen())
        for number, (kind, a, b, value, *expression) in enumerate(self.canvas.constraints):
            if cs.check(entities, [kind, a, b, value, *expression]) is not None:
                continue
            if kind not in cs.DIMENSIONS and not ({a, b} & chosen_now):
                # Fusion: „Deckungsgleich“ nur an gewählten Elementen (sonst zu unübersichtlich); bei
                # ausgeschalteter Palette „Abhängigkeiten zeigen“ gilt das für alle Abhängigkeitssymbole
                if kind == cs.COINCIDENT or not self.canvas.palette["constraints"]:
                    continue
            # Maß hängt an einem Ausdruck (die Marke eines getriebenen Maßes ist keiner)
            marker = "fx: " if expression and expression[0] and expression[0] != cs.DRIVEN else ""
            if kind == cs.POINT_DISTANCE:
                # Maßlinie direkt zwischen den beiden Punkten, die Zahl in der Mitte
                start, end = cs.point_of(entities[a], expression[1]), cs.point_of(entities[b], expression[2])
                points.append(((start[0] + end[0]) / 2, (start[1] + end[1]) / 2))
                texts.append(marker + number_text(value))
                rules.append(number)
                lines += [start, end]
                continue
            if kind == cs.DISTANCE:
                # Maßlinie zwischen den beiden Stellen, deren Abstand gemessen wird
                if entities[a][0] == "Linie":
                    end = middle_of(b)
                    start = foot(a, end)
                elif entities[b][0] == "Linie":
                    start = middle_of(a)
                    end = foot(b, start)
                else:
                    start, end = middle_of(a), middle_of(b)
                points.append(((start[0] + end[0]) / 2, (start[1] + end[1]) / 2))
                texts.append(marker + number_text(value))
                rules.append(number)
                lines += [start, end]
                continue
            if kind == cs.ANGLE:
                # Die Gradzahl steht zwischen den beiden Linien, nahe an ihrem Schnittpunkt
                first, second = middle_of(a), middle_of(b)
                corner = self._crossing(entities[a], entities[b])
                position = ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2)
                if corner is not None:
                    reach = min(math.dist(corner, position), 60 * pixel)
                    length = math.dist(corner, position) or 1.0
                    position = (corner[0] + (position[0] - corner[0]) / length * reach,
                                corner[1] + (position[1] - corner[1]) / length * reach)
                points.append(position)
                texts.append(marker + number_text(value) + "°")
                rules.append(number)
                continue
            if kind in (cs.POINT_ON, cs.MIDPOINT, cs.COINCIDENT, cs.ALIGN_H, cs.ALIGN_V):
                # Das Zeichen steht am Punkt selbst
                spot = cs.point_of(entities[a], int(value) if kind in (cs.POINT_ON, cs.MIDPOINT) else expression[1])
                points.append((spot[0] + 14 * pixel, spot[1] + 14 * pixel))
                texts.append(cs.SYMBOLS[kind])
                rules.append(number)
                continue
            if kind in cs.SEMI_AXES:
                # Maß der Halbachse: steht außen an ihrem Scheitel
                which = cs.SEMI_AXES.index(kind)
                tip = sk.ellipse_points(entities[a], 4)[which]
                centre = entities[a][1:3]
                reach = math.dist(tip, centre) or 1.0
                points.append((tip[0] + (tip[0] - centre[0]) / reach * _DIMENSION_GAP * pixel,
                               tip[1] + (tip[1] - centre[1]) / reach * _DIMENSION_GAP * pixel))
                texts.append(marker + "ab"[which] + " " + number_text(value))
                rules.append(number)
                lines += [tuple(centre), tip]
                continue
            if kind in cs.DIMENSIONS:
                position = beside(a, _DIMENSION_GAP * pixel)
                prefix = {cs.DIAMETER: "⌀ ", cs.RADIUS: "R ", cs.DIRECTION: "∠ ", cs.POINT_CURVATURE: "κ R "}.get(kind, "")
                points.append(position if kind != cs.DIRECTION else beside(a, -2 * _SYMBOL_GAP * pixel))
                texts.append(marker + prefix + number_text(value) + ("°" if kind == cs.DIRECTION else ""))
                rules.append(number)
                if kind == cs.LENGTH:
                    # Maßlinie parallel zur Linie, dazu zwei Hilfslinien von den Enden
                    v = entities[a][1:]
                    shift = (position[0] - (v[0] + v[2]) / 2, position[1] - (v[1] + v[3]) / 2)
                    far = 1.0 + 6.0 / _DIMENSION_GAP
                    for end in ((v[0], v[1]), (v[2], v[3])):
                        lines += [end, (end[0] + shift[0] * far, end[1] + shift[1] * far)]
                    lines += [(v[0] + shift[0], v[1] + shift[1]), (v[2] + shift[0], v[3] + shift[1])]
                continue
            if kind == cs.FIX and entities[a][0] == sk.SPLINE:
                # ein Spline hat keine „Seite“: das Zeichen steht an seinem Anfang
                points.append((entities[a][1] + 14 * pixel, entities[a][2] + 14 * pixel))
                texts.append(cs.SYMBOLS[kind])
                rules.append(number)
                continue
            for index in ((a, b) if kind in cs.PAIRS else (a,)):
                slot = symbols_at.get(index, 0)
                symbols_at[index] = slot + 1
                points.append(beside(index, -_SYMBOL_GAP * pixel, slot))
                texts.append(cs.SYMBOLS[kind])
                rules.append(number)
        # getriebene Maße in Klammern, wie in Fusion
        texts = [f"({text})" if cs.is_driven(self.canvas.constraints[rule]) else text for text, rule in zip(texts, rules)]
        return points, texts, rules, lines

    @staticmethod
    def _crossing(first: list, second: list) -> tuple[float, float] | None:
        """Schnittpunkt der Geraden zweier Linien (None, wenn sie parallel sind)."""
        ax, ay, bx, by = first[1:5]
        cx, cy, dx, dy = second[1:5]
        determinant = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx)
        if abs(determinant) < 1e-9:
            return None
        t = ((cx - ax) * (dy - cy) - (cy - ay) * (dx - cx)) / determinant
        return ax + (bx - ax) * t, ay + (by - ay) * t

    def edit_dimension(self, number: int, is_new: bool = False) -> None:
        """Öffnet das Eingabefeld direkt an der Maßzahl; die Skizze folgt dem eingegebenen Wert.

        Bei einem gerade erst angelegten Maß verwirft Esc das Maß wieder.
        """
        self.redraw()
        if number not in self._label_rules or number >= len(self.canvas.constraints):
            return
        position = self._label_points[self._label_rules.index(number)]
        rule = self.canvas.constraints[number]
        self.viewport.edit_number(
            self.world(*position), rule[3],
            lambda value, expression: self.canvas.set_dimension(number, value, expression),
            suffix=" °" if rule[0] in (cs.ANGLE, cs.DIRECTION) else " mm",
            minimum=-360.0 if rule[0] == cs.DIRECTION else 0.001,
            expression=cs.expression_of(rule), resolver=self._resolver,
            cancelled=(lambda: self.canvas.remove_constraint(number)) if is_new else None)

    # --- Darstellung in der Ansicht -----------------------------------------

    def show(self) -> None:
        """Zeichnet Raster und Zeichenhilfe der Skizzenebene (auch nach jeder Kamerabewegung)."""
        # Maßstab der Zeichenlogik an den Zoom anpassen: davon hängen Fangweite und Rasterfeinheit ab
        self.canvas._scale = 1.0 / self._pixel()
        self.canvas.pixel_factor = screen_pixel_factor(self.viewport)
        step = self.canvas.grid_step() if self.canvas.palette["grid"] else 0.0     # Palette „Skizzenraster“
        fine, major = [], []
        if step > 0:
            # Sichtbaren Ausschnitt der Ebene bestimmen; trifft der Blick sie nicht überall, um den Ursprung
            width, height = self.viewport.width(), self.viewport.height()
            corners = [self._on_plane_raw(x, y) for x in (0, width) for y in (0, height)]
            if all(corner is not None for corner in corners):
                low_x, high_x = min(c[0] for c in corners), max(c[0] for c in corners)
                low_y, high_y = min(c[1] for c in corners), max(c[1] for c in corners)
            else:
                low_x = low_y = -step * _GRID_LINES
                high_x = high_y = step * _GRID_LINES
            # Wie in Fusion ist das Raster ein Feld um das Gezeichnete, das mit ihm wächst: Elemente, die
            # Fläche unter der Skizze, angefangene Punkte und die Maus zählen dazu. Gezeichnet wird davon
            # nur, was im Bild liegt.
            drawn = [point for entity in self.canvas.entities for point in outline_points(entity)]
            drawn += [point for polyline in self.canvas.reference for point in polyline]
            drawn += [tuple(point[:2]) for point in self.canvas._points]
            if self.canvas._cursor is not None:
                drawn.append(tuple(self.canvas._cursor))
            patch = grid_patch(drawn)
            block = 5 * step
            low_x = max(low_x, math.floor(patch[0] / block) * block)
            low_y = max(low_y, math.floor(patch[1] / block) * block)
            high_x = min(high_x, math.ceil(patch[2] / block) * block)
            high_y = min(high_y, math.ceil(patch[3] / block) * block)
            first_x, last_x = math.ceil(low_x / step - 1e-9), math.floor(high_x / step + 1e-9)
            first_y, last_y = math.ceil(low_y / step - 1e-9), math.floor(high_y / step + 1e-9)
            if 0 <= last_x - first_x <= _GRID_LIMIT and 0 <= last_y - first_y <= _GRID_LIMIT:
                # Jede fünfte Linie ist eine Hauptlinie und etwas heller
                for index in range(first_x, last_x + 1):
                    (major if index % 5 == 0 else fine).extend(
                        [self.world(index * step, first_y * step), self.world(index * step, last_y * step)])
                for index in range(first_y, last_y + 1):
                    (major if index % 5 == 0 else fine).extend(
                        [self.world(first_x * step, index * step), self.world(last_x * step, index * step)])
        self.viewport.set_geometry("sketch_grid", np.array(fine, dtype=np.float32).reshape(-1, 3))
        self.viewport.set_geometry("sketch_grid_major", np.array(major, dtype=np.float32).reshape(-1, 3))
        reference = []
        for polyline in (self.canvas.reference if self.canvas.palette["projected"] else []):
            for start, end in zip(polyline, polyline[1:]):
                reference += [self.world(*start), self.world(*end)]
        self.viewport.set_geometry("sketch_reference", np.array(reference, dtype=np.float32).reshape(-1, 3))
        self.redraw()

    def _show_profiles(self) -> None:
        """Füllt die geschlossenen Bereiche der Skizze blau – nur neu berechnet, wenn sich die Elemente ändern."""
        key = repr((self.canvas.entities, sorted(self.canvas.construction), self.canvas.palette["profile"]))
        if key == getattr(self, "_profiles_key", None):
            return
        self._profiles_key = key
        # Palette „Profil zeigen“ aus: auch keine Berechnung (spart wie in Fusion Zeit bei großen Skizzen)
        triangles = (sk.closed_regions(self.canvas.entities, self.canvas.construction)
                     if self.canvas.palette["profile"] else [])
        points = [self.world(u, v) for triangle in triangles for u, v in triangle]
        layer = self.viewport._layers["sketch_profiles"]
        layer.alpha = 0.22
        self.viewport.set_geometry("sketch_profiles", np.array(points, dtype=np.float32).reshape(-1, 3))

    def _show_hud(self) -> None:
        """Maßfelder des gerade gezeichneten Elements in der 3D-Ansicht (wie Fusion)."""
        fields = []
        for field in self.canvas.hud_fields():
            unit = field["unit"]
            locked = self.canvas._hud_locked.get(field["name"])
            shown = locked[1] if locked and locked[1] else number_text(field["value"]) + unit.strip()
            # Maß- und Hilfslinien samt Pfeilspitzen in Raumkoordinaten (die Ansicht zeichnet sie unter den Feldern)
            lines = [(self.world(*a), self.world(*b)) for a, b in field.get("lines", [])]
            arrows = [(self.world(*tip), self.world(*towards)) for tip, towards in field.get("arrows", [])]
            fields.append((self.world(*field["anchor"]), shown, field["active"], field["locked"], lines, arrows))
        self.viewport.set_hud(fields)

    def redraw(self) -> None:
        """Zeichnet Vorschau, Fangmarke und Maßtext des laufenden Elements neu."""
        self._show_profiles()
        self._show_hud()
        entities, text = self.canvas.preview()
        # Die fertigen Elemente liegen genau in der Fläche und wären sonst teils verdeckt
        helpers = set(self.canvas.construction)
        centre_lines = set(self.canvas.centerlines) - helpers
        projected = set(self.canvas.projected) - helpers          # lila wie in Fusion, nicht grün
        locked = {rule[1] for rule in self.canvas.constraints if rule[0] == cs.FIX} - projected
        special = helpers | centre_lines | locked | projected
        if self._free is None:
            self._free = cs.free_entities(self.canvas.entities, self.canvas.constraints)
        # Wie in Fusion: was sich noch bewegen lässt, ist blau – voll Bestimmtes wird hell, Fixiertes grün
        self.viewport.set_geometry("sketch_live", self.segments(
            [entity for index, entity in enumerate(self.canvas.entities)
             if index not in special and index in self._free]))
        fixed = self.segments([entity for index, entity in enumerate(self.canvas.entities)
                               if index not in special and index not in self._free])
        self.viewport.set_geometry("sketch_locked", self.segments(
            [entity for index, entity in enumerate(self.canvas.entities) if index in locked - helpers]))
        # Mittellinien strichpunktiert (Strich, Lücke, Punkt, Lücke)
        self.viewport.set_geometry("sketch_centerline", self.dashed(
            [entity for index, entity in enumerate(self.canvas.entities) if index in centre_lines], (14, 4, 2, 4)))
        self.viewport.set_geometry("sketch_points", self.free_points() if self.canvas.palette["points"]
                                   else np.zeros((0, 3), dtype=np.float32))
        self.viewport.set_geometry("sketch_comb", np.concatenate([
            self.curvature_comb() if self.canvas.palette.get("comb") else np.zeros((0, 3), dtype=np.float32),
            self.osculating_circles()]))
        # Texte liegen fest an ihrer Stelle und erscheinen wie voll bestimmte Elemente
        self.viewport.set_geometry("sketch_fixed", np.concatenate([fixed, self.text_segments()]))
        # Konstruktionselemente in eigener Farbe: sie gehören nicht zum Profil
        # Konstruktion gestrichelt wie in Fusion; Palette „Konstruktion zeigen“ blendet sie aus
        # projizierte Geometrie (Flächenumriss, projizierte Kanten) lila, dazu die alte Zeichenhilfe
        reference = []
        for polyline in (self.canvas.reference if self.canvas.palette["projected"] else []):
            for start, end in zip(polyline, polyline[1:]):
                reference += [self.world(*start), self.world(*end)]
        purple = self.segments([entity for index, entity in enumerate(self.canvas.entities) if index in projected]) \
            if self.canvas.palette["projected"] else np.zeros((0, 3), dtype=np.float32)
        self.viewport.set_geometry("sketch_reference", np.concatenate(
            [np.array(reference, dtype=np.float32).reshape(-1, 3), purple]))
        self.viewport.set_geometry("sketch_construction", self.dashed(
            [entity for index, entity in enumerate(self.canvas.entities) if index in helpers], (8, 5))
            if self.canvas.palette["construction"] else np.zeros((0, 3), dtype=np.float32))
        self.viewport.set_geometry("sketch_preview", self.segments(entities))
        self.viewport.set_geometry("sketch_alignment", self.dashed(self.canvas.alignment_lines(), (6, 4))
                                   if self.canvas.alignment_lines() else np.zeros((0, 3), dtype=np.float32))
        heights = self.canvas.point_heights
        if self.canvas.tool == sk.SPLINE and any(abs(z) > 1e-9 for z in heights) and self.canvas._cursor is not None:
            # 3D-Spline im Entstehen: die Vorschau läuft räumlich durch die gesetzten Punkte bis zur Maus
            placed = [(px, py, z) for (px, py), z in zip(self.canvas._points, heights + [0.0] * len(self.canvas._points))]
            ahead = [*placed, (*self.canvas._cursor, 0.0)]
            if all(math.dist(a, b) > 1e-9 for a, b in zip(ahead, ahead[1:])):
                track = [self.world(*point) for point in sk.track3d(sk.spline3d(*ahead))]
                self.viewport.set_geometry("sketch_preview", np.array(
                    [point for pair in zip(track, track[1:]) for point in pair], dtype=np.float32))
        if abs(self._z_last) > 1e-9 and self.canvas.tool == "Linie" and self.canvas._points \
                and self.canvas._cursor is not None:
            # 3D-Linienzug: die Vorschau läuft räumlich vom erhöhten Punkt zur Maus bzw. zur Körperecke darunter
            start = self.world(*self.canvas._points[-1], self._z_last)
            hovered = self._vertex_picker(*self._mouse) if self._vertex_picker is not None else None
            end = np.array(hovered, dtype=float) if hovered is not None else self.world(*self.canvas._cursor)
            self.viewport.set_geometry("sketch_preview", np.array([start, end], dtype=np.float32))
        # Fangmarke: kleines Quadrat an der gefangenen Position, dazu das gewählte Element
        x, y = self.canvas._cursor
        size = 5.0 / max(self.canvas._scale, 1e-9)
        corners = [(x - size, y - size), (x + size, y - size), (x + size, y + size), (x - size, y + size)]
        marker = []
        # Die Marke erscheint nur, wenn etwas gefangen hat – sonst ist die Stelle frei gewählt
        if self.canvas._snapped_to_point and self.canvas.tool not in PICK_TOOLS:
            for start, end in zip(corners, corners[1:] + corners[:1]):
                marker += [self.world(*start), self.world(*end)]
        # Rechtwinkel-Zeichen am Startpunkt, solange die Linie rechtwinklig einrastet (wie Fusion)
        for start, end in self.canvas.inference_glyph():
            marker += [self.world(*start), self.world(*end)]
        marked = np.array(marker, dtype=np.float32).reshape(-1, 3)
        chosen = self.canvas.chosen()
        if chosen:
            marked = np.concatenate([marked, self.segments([self.canvas.entities[index] for index in chosen])])
        # Tangentengriffe der Griff-Splines: Linie durch den Kurvenpunkt, an der Spitze ein kleines Quadrat zum
        # Greifen. Sichtbar beim Auswählen und an gewählten Splines (wie in Fusion).
        grips = []
        for index, entity in enumerate(self.canvas.entities):
            if entity[0] == sk.CP_SPLINE and (self.canvas.tool == "Auswählen" or index in chosen):
                # Steuerpolygon: zeigt, wohin die Steuerpunkte den Bogen ziehen (Punkte zum Greifen)
                points = sk.control_points(entity)
                for a, b in zip(points, points[1:]):
                    grips += [self.world(*a), self.world(*b)]
                continue
            if entity[0] != sk.SPLINE:
                continue
            # Wie in Fusion zeigt ein angewählter Spline seine Tangentengriffe auch ohne Umschalten;
            # erst wenn einer gezogen wird, wird der Spline zum Griff-Spline
            if sk.has_handles(entity):
                if not (self.canvas.tool == "Auswählen" or index in chosen):
                    continue
                shown = entity
            elif index in chosen and self.canvas.tool == "Auswählen":
                shown = sk.with_handles(entity)
            else:
                continue
            # je Kurvenpunkt eine Griffstange vom Gegengriff zum Griff; beide Enden tragen eine Raute zum Ziehen
            # (das andere Ende darf wie in Fusion eine eigene Länge haben)
            for (gx, gy), (hx, hy) in zip(sk.handle_tips(shown), sk.back_tips(shown)):
                grips += [self.world(hx, hy), self.world(gx, gy)]
                for cx, cy in ((gx, gy), (hx, hy)):
                    for (ax, ay), (bx, by) in zip(((cx - size, cy), (cx, cy + size), (cx + size, cy), (cx, cy - size)),
                                                  ((cx, cy + size), (cx + size, cy), (cx, cy - size), (cx - size, cy))):
                        grips += [self.world(ax, ay), self.world(bx, by)]
        # Krümmungsgriffe: Raute in der Mitte des Schmiegekreises, Stange vom Kurvenpunkt dorthin
        if self.canvas.tool == "Auswählen":
            for _number, (px, py), _normal, (cx, cy) in self.canvas.curvature_grips():
                grips += [self.world(px, py), self.world(cx, cy)]
                for (ax, ay), (bx, by) in zip(((cx - size, cy), (cx, cy + size), (cx + size, cy), (cx, cy - size)),
                                              ((cx, cy + size), (cx + size, cy), (cx, cy - size), (cx - size, cy))):
                    grips += [self.world(ax, ay), self.world(bx, by)]
        if grips:
            marked = np.concatenate([marked, np.array(grips, dtype=np.float32).reshape(-1, 3)])
        # Auswahlrahmen, solange er aufgezogen wird: halb durchsichtig über der Skizze
        frame = self.canvas.box_outline()
        self.viewport.set_rubber_band([self.world(*point) for point in frame] if frame else None,
                                      self.canvas.box_touches())
        self.viewport.set_geometry("sketch_marker", marked)
        self.viewport.show_hint(text, *self._mouse)
        # Maßzahlen, Maßlinien und die Zeichen der Abhängigkeiten
        self._label_points, texts, self._label_rules, lines = self._annotations()
        chosen = self.canvas.selected
        self.viewport.set_labels([
            (self.world(*point), label, (chosen is not None and chosen in self.canvas.constraints[rule][1:3])
             or rule == getattr(self.canvas, "selected_rule", None))
            for point, label, rule in zip(self._label_points, texts, self._label_rules)])
        self.viewport.set_geometry(
            "sketch_dimensions", np.array([self.world(*point) for point in lines], dtype=np.float32).reshape(-1, 3))

    def close(self) -> None:
        """Räumt alles weg, was die Skizze in die Ansicht gezeichnet hat."""
        empty = np.zeros((0, 3), dtype=np.float32)
        self.viewport.view_changed.disconnect(self.show)
        if getattr(self, "sliced", False):
            self.viewport.set_clip(None)        # „Slice“ endet mit der Skizze
        self.viewport.set_hud([])
        for layer in ("sketch_fixed", "sketch_grid", "sketch_grid_major", "sketch_reference", "sketch_live", "sketch_preview", "sketch_marker", "sketch_profiles",
                      "sketch_locked", "sketch_centerline", "sketch_points", "sketch_comb",
                      "sketch_dimensions", "sketch_construction", "sketch_alignment"):
            self.viewport.set_geometry(layer, empty)
        self.viewport.set_labels([])
        self.viewport.set_rubber_band(None)
        self.viewport.close_number()
        self.viewport.show_hint("")


class SketchPanel(QWidget):
    """Eigenschaftenfeld einer Skizze: Zeichenwerkzeuge, Raster und Maßtabelle."""

    changed = Signal()      # Name oder Elemente der Skizze haben sich geändert

    def __init__(self, feature: sk.Sketch, session: SketchSession, parent=None):
        super().__init__(parent)
        self._feature, self._session = feature, session
        self._canvas = session.canvas
        self._last = self._snapshot()

        self._name = QLineEdit(feature.name)
        self._name.editingFinished.connect(self.changed)
        grid = QDoubleSpinBox()
        grid.setRange(0, 1000)
        grid.setDecimals(2)
        grid.setValue(self._canvas.grid)
        grid.setToolTip("Feste Rasterweite; 0 schaltet Raster und Rasterfang aus")
        grid.valueChanged.connect(self._set_grid)
        grid.setEnabled(not self._canvas.grid_auto)
        automatic = QCheckBox("passt sich dem Zoom an")
        automatic.setChecked(self._canvas.grid_auto)
        automatic.setToolTip("Beim Hineinzoomen wird das Raster feiner (bis 0,1 mm), beim Herauszoomen gröber")

        def set_automatic(on: bool) -> None:
            self._canvas.grid_auto = on
            grid.setEnabled(not on)
            self._session.show()

        automatic.toggled.connect(set_automatic)
        sides = QSpinBox()
        sides.setRange(3, 64)
        sides.setValue(self._canvas.polygon_sides)
        sides.valueChanged.connect(lambda value: setattr(self._canvas, "polygon_sides", value))
        radius = QDoubleSpinBox()
        radius.setRange(0.01, 1000)
        radius.setDecimals(2)
        radius.setValue(self._canvas.fillet_radius)
        radius.valueChanged.connect(lambda value: setattr(self._canvas, "fillet_radius", value))
        form = QFormLayout()
        form.addRow("Name", self._name)
        form.addRow("Raster", automatic)
        form.addRow("Feste Rasterweite (mm)", grid)
        form.addRow("Polygon: Ecken", sides)
        # Arten wie in Fusion (dort in der Skizzenpalette unter den Werkzeugoptionen)
        polygon_kind = QComboBox()
        polygon_kind.addItems(list(sk.POLYGON_KINDS))
        polygon_kind.setCurrentText(self._canvas.polygon_kind)
        polygon_kind.currentTextChanged.connect(lambda text: setattr(self._canvas, "polygon_kind", text))
        form.addRow("Polygon: Art", polygon_kind)
        slot_kind = QComboBox()
        slot_kind.addItems(list(sk.SLOT_KINDS))
        slot_kind.setCurrentText(self._canvas.slot_kind)
        slot_kind.currentTextChanged.connect(lambda text: setattr(self._canvas, "slot_kind", text))
        form.addRow("Langloch: Art", slot_kind)
        form.addRow("Abrunden: Radius (mm)", radius)
        # Fase wie in Fusion: Art, Abstände und Winkel
        chamfer_kind = QComboBox()
        chamfer_kind.addItems(list(sk.CHAMFER_KINDS))
        chamfer_kind.setCurrentText(self._canvas.chamfer_kind)
        chamfer_kind.currentTextChanged.connect(lambda text: setattr(self._canvas, "chamfer_kind", text))
        form.addRow("Fase: Art", chamfer_kind)
        for label, name, low, high, suffix in (("Fase: Abstand", "chamfer_distance", 0.01, 1000, " mm"),
                                               ("Fase: zweiter Abstand", "chamfer_distance2", 0.01, 1000, " mm"),
                                               ("Fase: Winkel", "chamfer_angle", 1, 179, " °"),
                                               ("Skizze skalieren: Faktor", "scale_factor", 0.01, 1000, "")):
            box = QDoubleSpinBox()
            box.setRange(low, high)
            box.setDecimals(2)
            box.setSuffix(suffix)
            box.setValue(getattr(self._canvas, name))
            box.valueChanged.connect(lambda value, attribute=name: setattr(self._canvas, attribute, value))
            form.addRow(label, box)
        distance = QDoubleSpinBox()
        distance.setRange(0.01, 1000)
        distance.setDecimals(2)
        distance.setValue(self._canvas.offset_distance)
        distance.valueChanged.connect(lambda value: setattr(self._canvas, "offset_distance", value))
        form.addRow("Versatz: Abstand (mm)", distance)
        rho = QDoubleSpinBox()
        rho.setRange(0.01, 0.99)
        rho.setDecimals(2)
        rho.setSingleStep(0.05)
        rho.setValue(self._canvas.conic_rho)
        rho.setToolTip("Form neuer Kegelschnitte: unter 0,5 Ellipse, 0,5 Parabel, darüber Hyperbel")
        rho.valueChanged.connect(lambda value: setattr(self._canvas, "conic_rho", value))
        form.addRow("Kegelschnitt: Rho", rho)
        degree = QSpinBox()
        degree.setRange(3, 5)
        degree.setSingleStep(2)
        degree.setValue(self._canvas.cp_degree)
        degree.setToolTip("Grad neuer Steuerpunkt-Splines: 3 (straffer) oder 5 (weicher), wie in Fusion")
        degree.valueChanged.connect(lambda value: setattr(self._canvas, "cp_degree", 3 if value < 4 else 5))
        form.addRow("Steuerpunkt-Spline: Grad", degree)
        # Muster: Anzahl und Abstand je Richtung; Kreismuster: Anzahl und Mittelpunkt
        for label, key, whole in (("Muster: Anzahl x", "nx", True), ("Muster: Abstand x (mm)", "dx", False),
                                  ("Muster: Anzahl y", "ny", True), ("Muster: Abstand y (mm)", "dy", False),
                                  ("Kreismuster: Anzahl", "count", True), ("Kreismuster: Mitte x (mm)", "cx", False),
                                  ("Kreismuster: Mitte y (mm)", "cy", False)):
            box = QSpinBox() if whole else QDoubleSpinBox()
            box.setRange(1 if whole else -10000, 200 if whole else 10000)
            box.setValue(self._canvas.pattern[key])
            box.valueChanged.connect(lambda value, name=key: self._canvas.pattern.__setitem__(name, value))
            form.addRow(label, box)

        tools = QGridLayout()
        self._buttons = QButtonGroup(self)
        for position, tool in enumerate(TOOLS):
            button = QPushButton(icon(_TOOL_ICONS[tool]), tool)
            button.setIconSize(QSize(22, 22))
            button.setCheckable(True)
            button.setChecked(tool == self._canvas.tool)
            button.clicked.connect(lambda _checked=False, name=tool: self._set_tool(name))
            self._buttons.addButton(button)
            tools.addWidget(button, position // 2, position % 2)
        delete = QPushButton(icon("delete"), "Löschen")
        delete.setToolTip("Gewähltes Element löschen (Werkzeug „Auswählen“, auch mit Entf)")
        delete.clicked.connect(self._canvas.delete_selected)
        tools.addWidget(delete, len(TOOLS) // 2, len(TOOLS) % 2)

        self.table = EntityTable(self._canvas.entities)
        self.table.edited.connect(self._entities_changed)
        self.table.setMinimumHeight(220)
        # Liste der Maße und Abhängigkeiten; Doppelklick auf ein Maß öffnet sein Eingabefeld im Modell
        self._rules = QListWidget()
        self._rules.setMaximumHeight(150)
        self._rules.itemDoubleClicked.connect(lambda _item: self._edit_rule())
        remove_rule = QPushButton(icon("delete"), "Maß/Abhängigkeit löschen")
        remove_rule.clicked.connect(lambda: self._canvas.remove_constraint(self._rules.currentRow()))
        # Fusion: Rechtsklick auf ein Maß → getrieben/treibend; hier für das in der Liste gewählte Maß
        self._driven_button = QPushButton("Maß getrieben/treibend")
        self._driven_button.setToolTip("Getrieben: das Maß zeigt nur den gemessenen Wert und bestimmt nichts")
        self._driven_button.clicked.connect(lambda: self._canvas.toggle_driven(self._rules.currentRow()))
        self._status = QLabel()
        self._status.setWordWrap(True)
        hint = QLabel("In der Ansicht zeichnen: Linksklick setzt Punkte, Rechtsklick oder Esc beendet "
                      "das Element. Bemaßung: Element anklicken, Wert eintippen, Eingabe. Eine Maßzahl "
                      "anklicken ändert sie. Mit „Auswählen“ lassen sich Punkte und Kreisränder ziehen. "
                      "Umschalt + mittlere Maustaste dreht, die mittlere allein verschiebt die Ansicht; "
                      "die rechte öffnet das Werkzeugmenü (Belegung unter Einstellungen).")
        hint.setWordWrap(True)
        hint.setObjectName("hint")          # Farbe kommt aus dem Design
        # ein umbrochener Text wünscht sich sehr breit zu sein; begrenzt, damit das Skizzenfeld so schmal bleibt
        # wie bei anderen Werkzeugen und die 3D-Ansicht ihren Platz behält
        hint.setMaximumWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._palette_box())
        layout.addLayout(form)
        # Die Werkzeugknöpfe stehen wie in Fusion im Reiter SKIZZE der Werkzeugleiste; das Raster hier bleibt
        # unsichtbar erhalten (es führt das gewählte Werkzeug und dient `select_tool`)
        self._tool_grid = QWidget()
        self._tool_grid.setLayout(tools)
        self._tool_grid.setVisible(False)
        layout.addWidget(self._tool_grid)
        # Gewähltes Element nachträglich anpassen: Länge und Winkel einer Linie, Durchmesser, Radius, Halbachsen …
        layout.addWidget(QLabel("Gewähltes Element anpassen:"))
        self._element = QWidget()
        self._element_form = QFormLayout(self._element)
        self._element_form.setContentsMargins(0, 0, 0, 0)
        self._element_key = None            # was das Feld gerade zeigt: (Nummer, Art, Länge des Elements)
        self._element_boxes: dict[str, QDoubleSpinBox] = {}
        layout.addWidget(self._element)
        layout.addWidget(QLabel("Elemente (Maße direkt änderbar):"))
        layout.addWidget(self.table)
        # Texte der Skizze mit Löschknopf
        self._texts = QListWidget()
        self._texts.setMaximumHeight(70)
        remove_text = QPushButton(icon("delete"), "Text löschen")
        remove_text.clicked.connect(lambda: self._canvas.remove_text(self._texts.currentRow()))
        # Der in der Liste gewählte Text lässt sich hier ändern; verschoben wird er mit der Maus
        self._text_height = QDoubleSpinBox()
        self._text_height.setRange(0.5, 1000)
        self._text_height.setSuffix(" mm")
        self._text_turn = QDoubleSpinBox()
        self._text_turn.setRange(-360, 360)
        self._text_turn.setSuffix(" °")
        self._text_font = QComboBox()
        self._text_font.addItems(list(sk.TEXT_FONTS))
        reword = QPushButton("Wortlaut ändern …")
        self._text_height.valueChanged.connect(
            lambda value: self._text_row_changed(height=value))
        self._text_turn.valueChanged.connect(lambda value: self._text_row_changed(rotation=value))
        self._text_font.currentTextChanged.connect(lambda name: self._text_row_changed(font=name))
        reword.clicked.connect(self._reword_text)
        self._texts.currentRowChanged.connect(self._show_text_row)
        text_form = QFormLayout()
        text_form.addRow("Schrifthöhe", self._text_height)
        text_form.addRow("Drehung", self._text_turn)
        text_form.addRow("Schrift", self._text_font)
        layout.addWidget(QLabel("Texte (mit „Auswählen“ verschiebbar):"))
        layout.addWidget(self._texts)
        layout.addLayout(text_form)
        layout.addWidget(reword)
        layout.addWidget(remove_text)
        layout.addWidget(QLabel("Maße und Abhängigkeiten:"))
        layout.addWidget(self._rules)
        layout.addWidget(remove_rule)
        layout.addWidget(self._driven_button)
        layout.addWidget(self._status)
        layout.addWidget(hint)
        self._canvas.changed.connect(self._canvas_changed)
        self._update_status()

    def _set_tool(self, tool: str) -> None:
        self._canvas.set_tool(tool)
        self._update_kinds()
        self._session.redraw()

    def _update_kinds(self) -> None:
        """Füllt die Auswahl „Werkzeugart“ mit den Varianten der Familie des aktiven Werkzeugs."""
        if not hasattr(self, "_tool_kind"):
            return
        family = next((group for group in TOOL_FAMILIES if self._canvas.tool in group), ())
        self._tool_kind.blockSignals(True)
        self._tool_kind.clear()
        self._tool_kind.addItems(list(family))
        self._tool_kind.setCurrentText(self._canvas.tool)
        self._tool_kind.setEnabled(bool(family))
        self._tool_kind.blockSignals(False)

    def _switch_kind(self, tool: str) -> None:
        if tool and tool != self._canvas.tool:
            self.select_tool(tool)

    def select_tool(self, tool: str) -> None:
        """Wählt ein Werkzeug von außen (Tastenkürzel): der Knopf rastet mit ein."""
        for button in self._buttons.buttons():
            if button.text() == tool:
                button.setChecked(True)
                self._set_tool(tool)
                return

    def _set_grid(self, value: float) -> None:
        self._canvas.grid = value
        self._session.show()

    def _show_element(self) -> None:
        """Zeigt die anpassbaren Größen des gewählten Elements; Änderungen wirken sofort."""
        index = self._canvas.selected
        entity = self._canvas.entities[index] if index is not None and 0 <= index < len(self._canvas.entities) else None
        key = (index, str(entity[0]), len(entity)) if entity is not None else None
        values = sk.element_values(entity) if entity is not None else []
        if key == self._element_key:
            # dasselbe Element: nur die Zahlen nachführen (nicht das Feld, in dem gerade getippt wird)
            for name, _label, value, _unit in values:
                box = self._element_boxes.get(name)
                if box is not None and not box.hasFocus():
                    box.blockSignals(True)
                    box.setValue(value)
                    box.blockSignals(False)
            return
        self._element_key, self._element_boxes = key, {}
        while self._element_form.rowCount():
            self._element_form.removeRow(0)
        if entity is None:
            note = QLabel("Mit „Auswählen“ ein Element anklicken – hier erscheinen dann seine Maße zum Ändern.")
            note.setWordWrap(True)
            note.setObjectName("hint")
            self._element_form.addRow(note)
            return
        self._element_form.addRow(QLabel(f"{entity[0] if not sk.has_handles(entity) else 'Spline mit Griffen'} {index + 1}"))
        for name, label, value, unit in values:
            box = QDoubleSpinBox()
            box.setDecimals(3)
            box.setRange(0.0 if unit == " °" else 0.001, 360.0 if unit == " °" else 1e6)
            if name in ("x", "y"):
                box.setRange(-1e6, 1e6)         # Lage eines Skizzenpunkts: auch negativ
            elif name == "rho":
                box.setRange(0.01, 0.99)        # Kegelschnitt: Rho liegt zwischen 0 und 1
                box.setSingleStep(0.05)
            elif name == "degree":
                box.setRange(3, 5)              # Steuerpunkt-Spline: Grad 3 oder 5 (Schritt 2)
                box.setSingleStep(2)
                box.setDecimals(0)
            box.setSuffix(unit)
            box.setValue(value)
            box.setKeyboardTracking(False)      # erst die fertige Eingabe zählt, nicht jede getippte Ziffer
            box.valueChanged.connect(lambda new, which=name: self._element_edited(which, new))
            self._element_boxes[name] = box
            self._element_form.addRow(label, box)
        # Kopie aus einem Muster: Abstand bzw. Winkelschritt gilt für das ganze Muster (wie in Fusion nachträglich änderbar)
        labels = {"dx": ("Musterabstand X", " mm"), "dy": ("Musterabstand Y", " mm"), "step": ("Musterwinkel je Kopie", " °")}
        for name, value in self._canvas.pattern_steps(index).items():
            box = QDoubleSpinBox()
            box.setDecimals(3)
            box.setRange(-1e6 if name != "step" else 0.001, 1e6 if name != "step" else 360.0)
            box.setSuffix(labels[name][1])
            box.setValue(value)
            box.setKeyboardTracking(False)
            box.valueChanged.connect(lambda new, which=name: self._pattern_edited(which, new))
            self._element_boxes["muster_" + name] = box
            self._element_form.addRow(labels[name][0], box)
        count_labels = {"nx": "Anzahl X", "ny": "Anzahl Y", "count": "Anzahl"}
        for name, value in self._canvas.pattern_counts(index).items():
            box = QSpinBox()
            box.setRange(1, 200)
            box.setValue(value)
            box.setKeyboardTracking(False)
            box.valueChanged.connect(lambda new, which=name: self._pattern_count_edited(which, new))
            self._element_boxes["anzahl_" + name] = box
            self._element_form.addRow(count_labels[name], box)
        if entity[0] == sk.SPLINE:
            # Tangentengriffe wie in Fusion: an jedem Kurvenpunkt ein Griff, dessen Länge den Bogen bauchiger macht
            grips = QCheckBox("Tangentengriffe (Bogen formen)")
            grips.setChecked(sk.has_handles(entity))
            grips.setToolTip("Mit Griffen lässt sich an jedem Punkt die Richtung der Kurve drehen und der Bogen\n"
                             "bauchiger oder flacher ziehen: die Griffspitzen mit „Auswählen“ ziehen")
            grips.toggled.connect(self._toggle_handles)
            self._element_form.addRow(grips)
            if sk.has_uneven_handles(entity):
                # Griffe je Seite unterschiedlich lang (ein Griffende wurde einzeln gezogen): zurück auf gleich lang
                even = QPushButton("Griffe wieder gleich lang")
                even.setObjectName("griffe_gleich")
                even.setToolTip("Jeder Gegengriff bekommt wieder die Länge seines Griffs (der Bogen wird symmetrisch)")
                even.clicked.connect(self._even_handles)
                self._element_form.addRow(even)
            if sk.has_handles(entity):
                # Krümmungsgriff wie in Fusion: an einem Kurvenpunkt die Krümmung (als Radius) festlegen
                spot = QSpinBox()
                spot.setObjectName("kruemmung_punkt")
                spot.setRange(1, len(sk.fit_points(entity)))
                radius = QDoubleSpinBox()
                radius.setObjectName("kruemmung_radius")
                radius.setRange(0.01, 1e6)
                radius.setDecimals(2)
                radius.setSuffix(" mm")
                radius.setValue(10.0)
                fix = QPushButton("Krümmung am Punkt festlegen")
                fix.setToolTip("Hält an diesem Kurvenpunkt die Krümmung (Schmiegekreis-Radius) auf beiden Seiten fest")
                fix.clicked.connect(lambda: self._fix_curvature(spot.value() - 1, radius.value()))
                self._element_form.addRow("Krümmung: Punkt", spot)
                self._element_form.addRow("Krümmung: Radius", radius)
                self._element_form.addRow(fix)

    def _fix_curvature(self, slot: int, radius: float) -> None:
        index = self._canvas.selected
        if index is not None and self._canvas.set_point_curvature(index, slot, radius):
            self._session._forget_free()
        self._session.redraw()

    def _element_edited(self, name: str, value: float) -> None:
        index = self._canvas.selected
        if index is None or not 0 <= index < len(self._canvas.entities):
            return
        entity = self._canvas.entities[index]
        changed = sk.with_value(entity, name, value)
        if sk.check_entity(changed) is not None:
            return
        previous = list(entity)
        entity[:] = changed
        # Beim Nachrechnen bleibt das geänderte Element, wie eingegeben: verknüpfte Kopien und verbundene
        # Elemente richten sich nach ihm, nicht umgekehrt
        pin = [cs.FIX, index, -1, 0.0]
        self._canvas.constraints.append(pin)
        accepted = self._canvas.apply_constraints()
        self._canvas.constraints.remove(pin)
        if not accepted:
            entity[:] = previous
            self._canvas.apply_constraints()
        self._canvas.message = "" if accepted else "Der Wert widerspricht den Maßen und Abhängigkeiten – ggf. das Maß ändern"
        self._session._forget_free()
        self._session.redraw()
        self._store()

    def _pattern_edited(self, name: str, value: float) -> None:
        index = self._canvas.selected
        if index is not None and abs(value) > 1e-9:
            self._canvas.set_pattern_step(index, name, value)
        self._session._forget_free()
        self._session.redraw()
        self._store()

    def _pattern_count_edited(self, name: str, value: int) -> None:
        index = self._canvas.selected
        if index is not None:
            # über den Ereignis-Takt: die Felder werden dabei neu aufgebaut
            QTimer.singleShot(0, lambda: (self._canvas.set_pattern_count(index, name, value),
                                          self._session._forget_free(), self._session.redraw(), self._store()))

    def _toggle_handles(self, on: bool) -> None:
        index = self._canvas.selected
        if index is None or not 0 <= index < len(self._canvas.entities):
            return
        self._canvas.set_spline_handles(index, on)
        self._entities_changed()

    def _even_handles(self) -> None:
        index = self._canvas.selected
        if index is None or not 0 <= index < len(self._canvas.entities):
            return
        self._canvas.even_spline_handles(index)
        self._entities_changed()

    def _update_status(self) -> None:
        self.table.fill(self._canvas.selected)
        self._show_element()
        closed = "ja" if has_profile(self._canvas.entities, self._canvas.construction) else "nein"
        text = f"{len(self._canvas.entities)} Elemente – geschlossenes Profil: {closed}"
        if self._canvas.message:
            text += f"\n{self._canvas.message}"
        self._status.setText(text)
        row = self._canvas.selected_text if self._canvas.selected_text is not None else self._texts.currentRow()
        self._texts.blockSignals(True)
        self._texts.clear()
        for entry in self._canvas.texts:
            self._texts.addItem(f"„{entry[0]}“ – {number_text(entry[3])} mm hoch")
        self._texts.setCurrentRow(min(row, self._texts.count() - 1))
        self._texts.blockSignals(False)
        self._show_text_row(self._texts.currentRow())
        # Liste der Maße und Abhängigkeiten neu füllen, die Markierung bleibt stehen
        row = self._rules.currentRow()
        self._rules.clear()
        for kind, a, b, value, *expression in self._canvas.constraints:
            names = " / ".join(f"{self._canvas.entities[i][0]} {i + 1}" for i in (a, b)
                               if 0 <= i < len(self._canvas.entities))
            unit = "°" if kind in (cs.ANGLE, cs.DIRECTION) else " mm"
            text = f"{kind} – {names}" + (f": {number_text(value)}{unit}" if kind in cs.DIMENSIONS else "")
            self._rules.addItem(text + (f"  = {expression[0]}" if expression and expression[0] else ""))
        self._rules.setCurrentRow(min(row, self._rules.count() - 1))

    def _show_text_row(self, row: int) -> None:
        """Zeigt Schrifthöhe, Drehung und Schrift des gewählten Textes in den Feldern darunter."""
        valid = 0 <= row < len(self._canvas.texts)
        for box in (self._text_height, self._text_turn, self._text_font):
            box.setEnabled(valid)
            box.blockSignals(True)
        if valid:
            entry = self._canvas.texts[row]
            self._text_height.setValue(entry[3])
            self._text_turn.setValue(entry[4])
            self._text_font.setCurrentText(entry[5] if len(entry) > 5 and entry[5] else sk.TEXT_FONT)
        for box in (self._text_height, self._text_turn, self._text_font):
            box.blockSignals(False)

    def _text_row_changed(self, **change) -> None:
        self._canvas.selected_text = self._texts.currentRow()
        self._canvas.change_text(self._texts.currentRow(), **change)

    def _reword_text(self) -> None:
        row = self._texts.currentRow()
        if not 0 <= row < len(self._canvas.texts):
            return
        text, accepted = QInputDialog.getText(self, "Text", "Text:", text=self._canvas.texts[row][0])
        if accepted and text.strip():
            self._canvas.change_text(row, text=text)

    def _palette_box(self) -> QWidget:
        """Skizzenpalette wie in Fusion: Linientyp, Draufsicht, Raster/Fang, Slice und was gezeigt wird."""
        from PySide6.QtWidgets import QGroupBox, QHBoxLayout
        box = QGroupBox("Skizzenpalette")
        layout = QVBoxLayout(box)
        # Linientyp: wirkt auf die gewählten Elemente; ohne Auswahl auf alles, was danach gezeichnet wird
        kinds = QHBoxLayout()
        self._linetype_buttons = {}
        for mode, tip in ((LINE_NORMAL, "Gewöhnliche Geometrie (zählt zum Profil)"),
                          (LINE_CONSTRUCTION, "Konstruktion: gestrichelt, zählt nicht zum Profil (Taste X)"),
                          (LINE_CENTER, "Mittellinie: strichpunktiert, zählt zum Profil, dient als Achse")):
            button = QPushButton(mode)
            button.setCheckable(True)
            button.setChecked(mode == self._canvas.line_mode)
            button.setToolTip(tip + " – wirkt auf die Auswahl, ohne Auswahl auf neu Gezeichnetes")
            button.clicked.connect(lambda _checked=False, chosen=mode: self._set_linetype(chosen))
            kinds.addWidget(button)
            self._linetype_buttons[mode] = button
        layout.addLayout(kinds)
        look = QPushButton("Draufsicht")
        look.setToolTip("Senkrecht auf die Skizzenebene schauen (Fusion: „Ausrichten nach“ / Look At)")
        look.clicked.connect(self._session.look_at)
        layout.addWidget(look)
        options = (("grid", "Skizzenraster"), ("snap", "Fang (am Raster)"), ("profile", "Profil zeigen"),
                   ("points", "Punkte zeigen"), ("constraints", "Abhängigkeiten zeigen"),
                   ("construction", "Konstruktion zeigen"), ("projected", "Projizierte Geometrie zeigen"))
        for key, label in options:
            check = QCheckBox(label)
            check.setChecked(self._canvas.palette[key])
            check.toggled.connect(lambda on, name=key: self._set_palette(name, on))
            layout.addWidget(check)
        # Werkzeugart: zeigt die Varianten des laufenden Werkzeugs (Fusion: Feature-Optionen der Palette)
        self._tool_kind = QComboBox()
        self._tool_kind.setToolTip("Art des laufenden Werkzeugs wechseln (z. B. Rechteck: 2 Punkte, Mittelpunkt, 3 Punkte)")
        self._tool_kind.currentTextChanged.connect(self._switch_kind)
        layout.addWidget(self._tool_kind)
        comb = QCheckBox("Krümmungskamm (gewählte Kurve)")
        comb.toggled.connect(lambda on: self._set_palette("comb", on))
        layout.addWidget(comb)
        rim = QCheckBox("Bemaßung: Kreise bis zum Rand (Tangente)")
        rim.setToolTip("Wie Fusions „Pick Circle/Arc Tangent“: Abstände zu Kreisen und Bögen enden am Rand statt in der Mitte")
        rim.toggled.connect(lambda on: setattr(self._canvas, "dimension_rim", on))
        layout.addWidget(rim)
        g2 = QCheckBox("Übergangskurve mit stetiger Krümmung (G2)")
        g2.toggled.connect(lambda on: setattr(self._canvas, "blend_g2", on))
        layout.addWidget(g2)
        copy = QCheckBox("Verschieben/Kopieren: als Kopie")
        copy.toggled.connect(lambda on: setattr(self._canvas, "move_copy", on))
        layout.addWidget(copy)
        solid = QCheckBox("3D-Skizze (Linie und Spline fangen an Körperecken im Raum)")
        solid.setToolTip("Wie Fusions „3D Sketch“: Linienpunkte fangen an Körperecken auch außerhalb der Skizzenebene")
        solid.toggled.connect(lambda on: self._set_palette("3d", on))
        layout.addWidget(solid)
        cut = QCheckBox("Slice (Körper vor der Ebene ausblenden)")
        cut.toggled.connect(self._session.set_slice)
        layout.addWidget(cut)
        return box

    def _set_linetype(self, mode: str) -> None:
        self._canvas.set_linetype(mode)
        for name, button in self._linetype_buttons.items():
            button.setChecked(name == self._canvas.line_mode)
        self._session.redraw()

    def _set_palette(self, name: str, on: bool) -> None:
        """Schaltet eine Anzeige der Palette (z. B. Profil, Punkte, Kamm) und zeichnet neu."""
        self._canvas.palette[name] = on
        self._session.show()            # Raster und projizierte Geometrie gehören zur Ansicht der Ebene
        self._session.redraw()

    def _snapshot(self) -> tuple:
        """Kopie von Elementen und Abhängigkeiten, um echte Änderungen zu erkennen."""
        return ([list(entity) for entity in self._canvas.entities],
                [list(rule) for rule in self._canvas.constraints], sorted(self._canvas.construction),
                [list(entry) for entry in self._canvas.texts], sorted(self._canvas.centerlines),
                sorted(self._canvas.projected))

    def _edit_rule(self) -> None:
        row = self._rules.currentRow()
        if 0 <= row < len(self._canvas.constraints) and self._canvas.constraints[row][0] in cs.DIMENSIONS:
            self._session.edit_dimension(row)

    def _canvas_changed(self) -> None:
        """Die Zeichenlogik meldet auch Werkzeugwechsel; weitergemeldet werden nur echte Änderungen."""
        self._update_status()
        self._session.redraw()
        if self._canvas.is_dragging():
            # Beim Ziehen eines Punkts kommt bei jedem Mausschritt eine Meldung. Die Skizze zeichnet sich selbst;
            # das ganze Modell danach neu zu rechnen (mit Probelauf riskanter Schritte) kostete je Schritt
            # Zehntelsekunden bis Sekunden und staute sich auf ~10 s (Runde 90). Gemeldet wird beim Loslassen.
            return
        if self._snapshot() != self._last:
            self._store()

    def _entities_changed(self) -> None:
        """Ein Zahlenwert wurde in der Tabelle geändert: Maße und Abhängigkeiten gelten weiter."""
        if not self._canvas.apply_constraints():
            self._canvas.message = "Der Wert widerspricht den Maßen und Abhängigkeiten"
        else:
            self._canvas.message = ""
        self._session._forget_free()        # geänderte Zahlen können die Bestimmtheit ändern
        self._session.redraw()
        self._store()

    def _store(self) -> None:
        self._last = self._snapshot()
        self._update_status()
        self.changed.emit()

    # --- Schnittstelle des Eigenschaftenfelds (wie FeatureForm) ---------------

    def values(self) -> dict:
        return {"name": self._name.text().strip() or self._feature.name,
                "entities": [list(entity) for entity in self._canvas.entities],
                "constraints": [list(rule) for rule in self._canvas.constraints],
                "construction": sorted(self._canvas.construction),
                "centerlines": sorted(self._canvas.centerlines),
                "projected": sorted(self._canvas.projected),
                "texts": [list(entry) for entry in self._canvas.texts]}

    def set_value(self, name: str, value: float) -> None:
        """Skizzen haben keine Ziehpfeile; es gibt nichts einzutragen."""

    def commit(self) -> None:
        """Alle Eingaben sind bereits übernommen."""
