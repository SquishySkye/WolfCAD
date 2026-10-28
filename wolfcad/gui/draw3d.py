# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Grundkörper direkt in der Ansicht aufziehen (wie in Fusion): auf dem Bodenraster oder auf einer Körperfläche.

Quader: erste Ecke anklicken, zur gegenüberliegenden Ecke ziehen oder dort klicken. Zylinder und Kugel:
Mittelpunkt anklicken, den Rand aufziehen. Liegt der erste Punkt auf einer ebenen Körperfläche, steht der
neue Körper auf dieser Fläche. Der Körper ist dabei sofort im Modell zu sehen; danach übernimmt das übliche
Live-Bearbeiten mit Ziehpfeilen (Höhe usw.), Eigenschaftenfeld und der Wahl der Operation.

Die Klasse hängt als `sketch_input` an der Ansicht und bekommt von dort Klicks und Mausbewegungen.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QTimer

from ..core import features as ft
from ..core import parameters as pm
from ..core import sketch, solids

_MARKER_SHARE = 0.014       # Größe des Fangkreuzes als Anteil der Kameraentfernung
# Dauert eine Aktualisierung beim Aufziehen länger (Sekunden), rechnet die Vorschau bis zum Loslassen ohne Boolesche
# Operation („Neuer Körper“): sonst stünde das Fenster bei schweren Modellen bei jedem Mausschritt
_SLOW_PREVIEW = 0.4
_PREVIEW_OPERATION = "Neuer Körper"
_MANY_BODIES = 8            # ab so vielen Körpern beginnt das Aufziehen gleich mit der leichten Vorschau
_GROUND = [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0]     # Bodenraster: Ursprung, Normale, x-Richtung


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


class PrimitiveDraw:
    """Zieht einen Quader, Zylinder oder eine Kugel auf dem Bodenraster oder einer Körperfläche auf."""

    uses_ground = True      # das Bodenraster bleibt sichtbar (anders als beim Skizzieren)

    def __init__(self, window, feature):
        self.window, self.viewport, self.feature = window, window.viewport, feature
        # Vorschau ohne Boolesche Operation (siehe _SLOW_PREVIEW); die echte Operation wird beim Loslassen gerechnet
        self._real_operation = getattr(feature, "operation", _PREVIEW_OPERATION)
        # Bei vielen Körpern gleich leicht vorschauen: sonst rechnet schon die erste Mausbewegung die volle Operation
        # mit allen Körpern (Zufallstest: 18 Körper vereinigen, 15 s) – und beim Loslassen noch einmal
        self._light_preview = (self._real_operation != _PREVIEW_OPERATION
                               and len(window.timeline.state.bodies) >= _MANY_BODIES)
        self.frame = list(_GROUND)                          # Ebene, auf der aufgezogen wird (wie sketch.face_frame)
        self.on_face = False                                # steht der Körper auf einer Fläche oder Konstruktionsebene?
        self.where = "auf dem Raster"                       # Beschreibung der Ebene für den Hinweis am Mauszeiger
        self.base = ""                                      # Name der Konstruktionsebene, auf der aufgezogen wird
        self.first: tuple[float, float] | None = None       # erster angeklickter Punkt in der Ebene
        self.hover_face: tuple[str, int] | None = None      # Körperfläche unter der Maus (vor dem ersten Punkt)
        self.index: int | None = None                       # Platz des Körpers in der Timeline, sobald er existiert
        self._fresh = False                                 # der erste Punkt stammt vom gerade gedrückten Klick
        self._last: tuple[float, float] | None = None       # letzte Mausposition in der Ebene (nach dem ersten Punkt)
        # eingetippte Maße (wie in Fusion): Quader "length"/"width", sonst "diameter" – sie gelten statt der Maus
        self._typed: dict[str, float] = {}
        self._active: str | None = None                     # aktives Maßfeld (Tab wechselt, wie in Fusion)
        self._done = False
        if isinstance(feature, solids.Box):
            self._start_text = ("Quader: erste Ecke auf dem Raster, einer Körperfläche oder einer Konstruktionsebene "
                                "anklicken und aufziehen")
            self._next_text = "Quader: gegenüberliegende Ecke anklicken"
        else:
            self._start_text = (f"{feature.KIND}: Mittelpunkt auf dem Raster, einer Körperfläche oder einer "
                                "Konstruktionsebene anklicken und den Rand aufziehen")
            self._next_text = f"{feature.KIND}: Rand anklicken"
        self.viewport.show_info(self._start_text + "\nRechtsklick oder Esc bricht ab.")

    # --- Ebene und Punkte -----------------------------------------------------

    def _frame_under(self, x: float, y: float) -> tuple[list, bool]:
        """Die Ebene unter dem Mauszeiger: eine ebene Körperfläche, sonst eine Konstruktionsebene, sonst das Bodenraster."""
        # nur Flächen zählen (nahe einer Kante bliebe sonst die Kante getroffen und es ginge aufs Raster)
        face = self.window._face_at(x, y)
        hit = ("face", *face) if face is not None else None
        self.hover_face = face
        state = self.window.timeline.state
        self.where, self.base = "auf dem Raster", ""
        eye, direction, _pixel = self.viewport.pick_ray(x, y)
        best = None                             # (Entfernung, Beschreibung, Lage) der vordersten getroffenen Ebene
        if hit is not None and hit[0] == "face" and hit[1] in state.bodies:
            faces = ft.faces_of(state.bodies[hit[1]])
            frame = sketch.face_frame(faces[hit[2]]) if hit[2] < len(faces) else None
            if frame is not None:
                normal = np.array(frame[3:6], dtype=float)
                slope = float(direction @ normal)
                along = float((np.array(frame[0:3], dtype=float) - eye) @ normal) / slope if abs(slope) > 1e-9 else math.inf
                best = (along, "auf der Körperfläche", [float(value) for value in frame])
        if hit is not None and best is None:
            # gekrümmte Fläche: aufgezogen wird auf der Ebene, die sie an der angeklickten Stelle berührt
            tangent = self._tangent_frame(state, hit, eye, direction)
            if tangent is None:
                return list(_GROUND), False     # ein Körper liegt davor: keine Ebene dahinter greifen
            self.where = "auf der gekrümmten Fläche (berührende Ebene)"
            return tangent, True
        # Konstruktionsebenen: getroffen ist, wessen gezeichneter Rahmen unter der Maus liegt. Es zählt, was vorn
        # liegt – eine Ebene vor dem Körper gewinnt gegen dessen Fläche und umgekehrt
        reach = 40.0
        boxes = [solids._bounds(shape) for shape in state.bodies.values()]
        if boxes:
            reach = max(reach, 0.6 * max(max(b[i + 3] - b[i] for i in range(3)) for b in boxes))
        for name, axes in state.planes.items():
            origin = np.array([axes.Location().X(), axes.Location().Y(), axes.Location().Z()])
            normal = np.array([axes.Direction().X(), axes.Direction().Y(), axes.Direction().Z()])
            x_dir = np.array([axes.XDirection().X(), axes.XDirection().Y(), axes.XDirection().Z()])
            slope = float(direction @ normal)
            if abs(slope) < 1e-9:
                continue
            along = float((origin - eye) @ normal) / slope
            offset = eye + along * direction - origin
            if along > 0 and abs(float(offset @ x_dir)) <= reach and abs(float(offset @ np.cross(normal, x_dir))) <= reach:
                if best is None or along < best[0]:
                    best = (along, f"auf der Ebene {name}", [*origin, *normal, *x_dir])
        if best is not None:
            self.where = best[1]
            if best[1].startswith("auf der Ebene "):
                self.base = best[1][len("auf der Ebene "):]
            return [float(value) for value in best[2]], True
        return list(_GROUND), False

    @staticmethod
    def _tangent_frame(state, hit, eye, direction) -> list | None:
        """Lage der Ebene, die eine gekrümmte Körperfläche dort berührt, wo der Blickstrahl sie trifft."""
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.gp import gp_Dir, gp_Lin, gp_Pnt, gp_Vec
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
        from OCP.TopAbs import TopAbs_REVERSED
        from OCP.TopoDS import TopoDS

        if hit[0] != "face" or hit[1] not in state.bodies:
            return None
        faces = ft.faces_of(state.bodies[hit[1]])
        if hit[2] >= len(faces):
            return None
        face = TopoDS.Face(faces[hit[2]])
        finder = IntCurvesFace_ShapeIntersector()
        finder.Load(face, 1e-6)
        finder.Perform(gp_Lin(gp_Pnt(*[float(c) for c in eye]), gp_Dir(*[float(c) for c in direction])), 0.0, 1e9)
        if not finder.IsDone() or finder.NbPnt() < 1:
            return None
        nearest = min(range(1, finder.NbPnt() + 1), key=finder.WParameter)
        point, along_u, along_v = gp_Pnt(), gp_Vec(), gp_Vec()
        BRepAdaptor_Surface(face).D1(finder.UParameter(nearest), finder.VParameter(nearest), point, along_u, along_v)
        normal = along_u.Crossed(along_v)
        if normal.Magnitude() < 1e-12 or along_u.Magnitude() < 1e-12:
            return None
        normal.Normalize()
        along_u.Normalize()
        if face.Orientation() == TopAbs_REVERSED:
            normal.Reverse()
        return [point.X(), point.Y(), point.Z(), normal.X(), normal.Y(), normal.Z(),
                along_u.X(), along_u.Y(), along_u.Z()]

    def _vectors(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Ursprung, Normale und die beiden Richtungen der Ebene."""
        origin, normal, x_dir = (np.array(self.frame[i:i + 3]) for i in (0, 3, 6))
        return origin, normal, x_dir, np.cross(normal, x_dir)

    def _world(self, point: tuple[float, float]) -> np.ndarray:
        origin, _normal, x_dir, y_dir = self._vectors()
        return origin + x_dir * point[0] + y_dir * point[1]

    def _point(self, x: float, y: float) -> tuple[float, float] | None:
        """Rasterpunkt unter dem Mauszeiger in der Ebene, auf die Rasterweite gerundet."""
        origin, normal, x_dir, y_dir = self._vectors()
        eye, direction, _pixel = self.viewport.pick_ray(x, y)
        slope = float(direction @ normal)
        if abs(slope) < 1e-6:
            return None
        along = float((origin - eye) @ normal) / slope
        if along <= 0:
            return None
        offset = eye + along * direction - origin
        step = self.viewport.ground_step()
        # runden und kleine Rechenreste entfernen, damit glatte Maße entstehen
        return (round(round(float(offset @ x_dir) / step) * step, 6),
                round(round(float(offset @ y_dir) / step) * step, 6))

    def _mark(self, *points: tuple[float, float]) -> None:
        """Fangkreuze an den Punkten der Ebene."""
        _origin, _normal, x_dir, y_dir = self._vectors()
        size = self.viewport._distance * _MARKER_SHARE
        lines = []
        for point in points:
            centre = self._world(point)
            lines += [centre - x_dir * size, centre + x_dir * size, centre - y_dir * size, centre + y_dir * size]
        self.viewport.set_geometry("sketch_marker", np.array(lines, dtype=np.float32).reshape(-1, 3))

    def _values(self, point: tuple[float, float]) -> tuple[dict, str] | None:
        """Maße des Körpers zwischen erstem Punkt und Mausposition samt Anzeige; None, solange er keine Größe hat."""
        (ax, ay), (bx, by) = self.first, point
        # auf einer Konstruktionsebene hängt der Körper an ihr (er wandert mit), auf einer Körperfläche merkt er die Lage
        placed = {"base": self.base} if self.base else ({"frame": list(self.frame)} if self.on_face else {})
        if isinstance(self.feature, solids.Box):
            length, width = abs(bx - ax), abs(by - ay)
            if length < 1e-6 or width < 1e-6:
                return None
            return ({"center_x": (ax + bx) / 2, "center_y": (ay + by) / 2, "length": length, "width": width, **placed},
                    f"{_number(length)} × {_number(width)} mm")
        diameter = 2.0 * math.hypot(bx - ax, by - ay)
        if diameter < 1e-6:
            return None
        if isinstance(self.feature, solids.Sphere) and self.base:
            # auf einer Konstruktionsebene hängt die Kugel an ihr: der Mittelpunkt zählt in der Ebene
            return ({"center_x": ax, "center_y": ay, "center_z": 0.0, "diameter": diameter, "base": self.base},
                    f"⌀ {_number(diameter)} mm")
        if isinstance(self.feature, solids.Sphere):
            # sonst kennt die Kugel keine Ebene: ihr Mittelpunkt liegt im Raum auf der angeklickten Stelle
            centre = self._world(self.first)
            return ({"center_x": float(centre[0]), "center_y": float(centre[1]), "center_z": float(centre[2]),
                     "diameter": diameter}, f"⌀ {_number(diameter)} mm")
        return {"center_x": ax, "center_y": ay, "diameter": diameter, **placed}, f"⌀ {_number(diameter)} mm"

    def _update(self, point: tuple[float, float]) -> str:
        """Bringt den Körper auf die Größe bis zur Mausposition (legt ihn beim ersten Mal an)."""
        found = self._values(point)
        if found is None:
            return ""
        values, text = found
        if self.index is None:
            for name, value in values.items():
                setattr(self.feature, name, value)
            if self._light_preview:
                self.feature.operation = _PREVIEW_OPERATION     # die echte Operation folgt beim Loslassen
            self.index = self.window._add_drawn(self.feature)
        else:
            if self._light_preview:
                values = {**values, "operation": _PREVIEW_OPERATION}
            started = time.perf_counter()
            self.window.timeline.update(self.index, **values)
            if (not self._light_preview and self._real_operation != _PREVIEW_OPERATION
                    and time.perf_counter() - started > _SLOW_PREVIEW):
                self._light_preview = True
                self.viewport.show_info(self._next_text + "\nVorschau ohne Verrechnen (schweres Modell) – "
                                        "die Operation folgt beim Loslassen.")
            self.window._refresh()
        return text

    def _restore_operation(self) -> None:
        """Nach der leichten Vorschau: die eigentliche Operation einmal rechnen."""
        if self._light_preview and self.index is not None:
            self._light_preview = False
            self.window.timeline.update(self.index, operation=self._real_operation)
            self.window._refresh()

    # --- Eingaben aus der Ansicht ----------------------------------------------

    def move(self, x: float, y: float) -> None:
        if self._done:
            return
        if self.first is None:
            # vor dem ersten Klick: zeigen, auf welcher Ebene der Körper entstehen würde – die Körperfläche dabei
            # aufhellen wie Fusion
            self.frame, self.on_face = self._frame_under(x, y)
            on_body = self.on_face and self.where.startswith("auf der") and "Ebene" not in self.where
            self.window.show_hover_face(self.hover_face if on_body else None)
        point = self._point(x, y)
        if point is None:
            return
        if not self.on_face:
            # das Rasterfeld reicht immer bis zur Maus, auch wenn sie über seinen Rand hinausfährt
            self.viewport.include_in_ground([point] if self.first is None else [self.first, point])
        if self.first is None:
            self._mark(point)
            self.viewport.show_hint(f"{_number(point[0])} / {_number(point[1])} {self.where}", x, y)
            return
        point = self._constrain(point)
        self._last = point
        text = self._update(point)
        self._mark(self.first, point)
        self.viewport.show_hint(text, x, y)
        self._show_hud(point)

    def drag_start(self, x: float, y: float) -> bool:
        """Maustaste gedrückt: setzt den ersten Punkt (das Ziehen danach formt den Körper)."""
        if self.first is None:
            self.frame, self.on_face = self._frame_under(x, y)
            point = self._point(x, y)
            if point is None:
                return False
            self.first, self._fresh = point, True
            self.window.show_hover_face(None)       # die Ebene steht fest: Aufhellung weg wie in Fusion
            self._mark(point)
            self.viewport.show_info(self._next_text + "\nRechtsklick oder Esc bricht ab.")
        elif not self._done:
            # zweiter Klick: der Körper reicht genau bis zu dieser Stelle, auch ohne Mausbewegung davor
            point = self._point(x, y)
            if point is not None:
                self._update(self._constrain(point))
        return True

    def drag_end(self) -> None:
        """Maustaste losgelassen: nach dem Aufziehen oder beim zweiten Klick steht der Grundriss fest."""
        fresh, self._fresh = self._fresh, False
        dragged = self.viewport.moved_since_press()
        if fresh and dragged and not self._done:
            # aufgezogen: der Körper reicht genau bis zur Stelle des Loslassens
            point = self._point(self.viewport.release_position.x(), self.viewport.release_position.y())
            if point is not None:
                self._update(self._constrain(point))
        if self.index is None or (fresh and not dragged):
            return                      # erst ein einzelner Klick: auf den zweiten Punkt warten
        self._restore_operation()
        self._done = True
        # erst nach diesem Mausereignis umschalten, damit das Loslassen nicht noch etwas auswählt
        QTimer.singleShot(0, lambda: self.window._end_draw(True))

    def press(self, x: float, y: float) -> None:
        """Einzelne Klicks sind bereits über drag_start/drag_end verarbeitet."""

    def finish(self) -> None:
        """Rechtsklick: Aufziehen abbrechen."""
        self._done = True
        QTimer.singleShot(0, lambda: self.window._end_draw(False))

    # --- Maße eintippen (wie in Fusion) -------------------------------------------

    def _constrain(self, point: tuple[float, float]) -> tuple[float, float]:
        """Hält eingetippte Maße fest: die Maus bestimmt dann nur noch Richtung bzw. das andere Maß."""
        if not self._typed or self.first is None:
            return point
        (ax, ay), (bx, by) = self.first, point
        if isinstance(self.feature, solids.Box):
            if "length" in self._typed:
                bx = ax + math.copysign(self._typed["length"], bx - ax if abs(bx - ax) > 1e-9 else 1.0)
            if "width" in self._typed:
                by = ay + math.copysign(self._typed["width"], by - ay if abs(by - ay) > 1e-9 else 1.0)
            return bx, by
        return ax + self._typed["diameter"] / 2, ay

    def _fields(self) -> list[tuple[str, str]]:
        """Die Maße, die sich beim Aufziehen eintippen lassen: (Feld, Beschriftung)."""
        if isinstance(self.feature, solids.Box):
            return [("length", "Länge"), ("width", "Breite")]
        return [("diameter", "Durchmesser")]

    def _active_name(self) -> str:
        """Das aktive Maßfeld: vom Benutzer gewählt (Tab), sonst das erste noch freie."""
        names = [name for name, _label in self._fields()]
        if self._active in names:
            return self._active
        return next((name for name in names if name not in self._typed), names[0])

    def _hud_fields(self, point: tuple[float, float]) -> list[tuple]:
        """Maßfelder wie in Fusion an der Grundfläche: (Name, Wert, Stelle in der Ebene)."""
        (ax, ay), (bx, by) = self.first, point
        gap = self.viewport._distance * 0.035
        if isinstance(self.feature, solids.Box):
            return [("length", abs(bx - ax), ((ax + bx) / 2, min(ay, by) - gap)),
                    ("width", abs(by - ay), (max(ax, bx) + 1.6 * gap, (ay + by) / 2))]
        radius = math.dist((ax, ay), (bx, by))
        return [("diameter", 2 * radius, ((ax + bx) / 2 + gap, (ay + by) / 2 + gap))]

    def _show_hud(self, point: tuple[float, float] | None) -> None:
        """Zeigt die Maßfelder in der Ansicht (aktives blau umrandet, festgehaltene mit Schloss)."""
        if point is None or self.first is None or self._done or math.dist(self.first, point) < 1e-9:
            self.viewport.set_hud([])
            return
        active = self._active_name()
        self.viewport.set_hud([(self._world(anchor), _number(value) + "mm", name == active, name in self._typed)
                               for name, value, anchor in self._hud_fields(point)])

    def typed(self, text: str | None) -> bool:
        """Eine Ziffer (oder Tab: `text` None) beim Aufziehen: öffnet das Eingabefeld am aktiven Maßfeld."""
        if self.first is None or self._done or self.viewport.number_is_open():
            return False
        self._ask(text)
        return True

    def _ask(self, text: str | None = "") -> None:
        names = [name for name, _label in self._fields()]
        labels = dict(self._fields())
        name = self._active_name()
        self.viewport.show_info(f"{labels[name]} eintippen – Tab/Umschalt+Tab wechselt das Feld und hält den Wert "
                                "fest, Eingabe übernimmt")
        point = self._last or (self.first[0] + 10.0, self.first[1] + 10.0)
        field = next((f for f in self._hud_fields(point) if f[0] == name), None)
        current = self._typed.get(name, field[1] if field else 10.0)
        anchor = self._world(field[2] if field else point)

        def confirmed(value: float, _expression: str) -> None:
            self._typed[name] = value
            point = self._constrain(self._last or (self.first[0] + 10.0, self.first[1] + 10.0))
            self._last = point
            self._update(point)
            self._mark(self.first, point)
            step = -1 if self.viewport.confirmed_backward() else 1
            if self.viewport.confirmed_with_tab() and len(names) > 1:
                self._active = names[(names.index(name) + step) % len(names)]
                self._show_hud(point)
                QTimer.singleShot(0, lambda: self._ask(None))   # nächstes Feld gleich zum Tippen offen
                return
            open_fields = [other for other in names if other not in self._typed]
            if open_fields:
                self._active = open_fields[0]                    # Quader: nach der Länge die Breite
                self._show_hud(point)
                QTimer.singleShot(0, lambda: self._ask(None))
                return
            # alle Maße stehen fest: der Grundriss ist fertig, weiter mit Pfeilen und Eigenschaftenfeld
            self._restore_operation()            # nach leichter Vorschau die echte Operation rechnen
            self._done = True
            self.viewport.set_hud([])
            QTimer.singleShot(0, lambda: self.window._end_draw(True))

        before = self._typed.get(name)          # was vor dem Tippen galt (Esc stellt es wieder her)

        def apply_now(value: float | None) -> None:
            if value is None or value <= 0:
                if before is None:
                    self._typed.pop(name, None)
                else:
                    self._typed[name] = before
            else:
                self._typed[name] = value
            if self._last is not None:
                point = self._constrain(self._last)
                self._update(point)
                self._mark(self.first, point)
                self._show_hud(point)

        def edited(typed: str) -> None:
            # wie in Fusion gilt der Wert schon beim Tippen
            text = typed.strip().removesuffix("mm").strip()
            try:
                value = float(text.replace(",", ".")) if pm.is_number(text) else float(pm.evaluate(text))
            except Exception:
                value = None
            apply_now(value)

        def cancelled() -> None:
            apply_now(None)
            self.viewport.show_info(self._next_text)

        self.viewport.edit_number(anchor, max(float(current), 0.001), confirmed, initial_text=text,
                                  cancelled=cancelled, edited=edited)

    def set_additive(self, _on: bool) -> None:
        pass

    def close(self) -> None:
        """Räumt Fangkreuze, Maßfeld und Hinweise wieder weg."""
        self.window.show_hover_face(None)
        if self.viewport.number_is_open():
            self.viewport.close_number()
        self.viewport.set_hud([])
        self.viewport.include_in_ground([])
        self.viewport.set_geometry("sketch_marker", np.zeros((0, 3), dtype=np.float32))
        self.viewport.show_hint("")
        self.viewport.show_info("")
