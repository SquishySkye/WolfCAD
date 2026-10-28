# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Szenen der Werkzeug-Clips (Runde 80): je Werkzeug eine kurze Vorführung in einem echten WolfCAD-Fenster.

Jede Szene bekommt einen `Recorder` (clip_recorder.py) und ein frisches Hauptfenster, legt ein Beispielteil an,
startet das Werkzeug wie ein Nutzer (Live-Bearbeitung mit Pfeil) und ändert den Wert über mehrere Bilder. Die
Schrittzeile folgt den „So geht's“-Schritten der Erklärkarte.

Aufruf: python -m wolfcad.gui.clip_scenes <ausgabeordner> [Werkzeug …]   (unsichtbar, z. B. unter Xvfb)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from ..core import features as ft
from ..core import sketch as sk
from ..core import solids
from .clip_recorder import CLIP_DIR, Recorder, clip_name

MODEL, SKETCH = "modell", "skizze"


# --- Hilfen ------------------------------------------------------------------------------------------------------

def _add(w, feature):
    """Schritt ohne Live-Bearbeitung anlegen (Beispielteil der Szene)."""
    w.timeline.add(feature)
    w._refresh()
    return len(w.timeline.features) - 1


def _live(w, feature) -> int:
    """Werkzeug wie aus der Leiste starten (Live-Bearbeitung mit Pfeil); gibt den Index des Schritts zurück."""
    w._selection = []
    w._create(feature)
    return len(w.timeline.features) - 1


def _set(w, index: int, **values) -> None:
    w.timeline.update(index, **values)
    # auch ins Eigenschaftenfeld: beim Bestätigen übernimmt WolfCAD dessen Werte
    form = (w._live or {}).get("form") if (w._live or {}).get("index") == index else None
    for name, value in values.items():
        if form is not None and isinstance(value, float) and hasattr(form, "set_value"):
            form.set_value(name, value)
    w._refresh()


def _finish(rec: Recorder, caption: str, hold: int = 18) -> None:
    rec.window._finish_live(True)
    rec.window._refresh()
    rec.caption = caption
    rec.cursor = None
    rec.shot(hold=hold)


def _face_point(w, body: str | None = None, axis: int = 2, highest: bool = True) -> list:
    """Merkpunkt der Fläche mit dem größten (bzw. kleinsten) Schwerpunkt längs einer Achse."""
    shape = w.timeline.state.bodies[body or next(iter(w.timeline.state.bodies))]
    points = [ft.face_point(face) for face in ft.faces_of(shape)]
    chosen = (max if highest else min)(points, key=lambda point: point[axis])
    return list(chosen)


def _top_edges(w, z: float) -> list:
    """Merkpunkte der Kanten, die ganz auf der Höhe z liegen (z. B. die oberen Kanten eines Quaders)."""
    shape = next(iter(w.timeline.state.bodies.values()))
    return [list(ft.edge_point(edge)) for edge in ft.unique_edges(shape) if abs(ft.edge_point(edge)[2] - z) < 1e-6]


def _sketch(w, entities: list, name: str = "Skizze1", plane: str = "XY") -> int:
    return _add(w, sk.Sketch(name=name, plane=plane, entities=entities))


# --- Volumenkörper -----------------------------------------------------------------------------------------------

def extrusion(rec: Recorder, w) -> None:
    rec.look((0, 0, 8), 120)
    _sketch(w, sk.rectangle(-20, -12, 20, 12))
    rec.caption = "1  Profil der Skizze anklicken"
    rec.move_to((0, 0, 0)); rec.click()
    index = _live(w, ft.Extrude(sketch="Skizze1", distance=0.5, operation="Neuer Körper"))
    rec.caption = "2  Am blauen Pfeil hochziehen"
    rec.shot(hold=4)
    rec.animate(26, lambda t: _set(w, index, distance=0.5 + 19.5 * t), lambda t: (0, 0, 0.5 + 19.5 * t))
    _finish(rec, "3  Eingabe – fertig")


def drehen(rec: Recorder, w) -> None:
    rec.look((0, 0, 8), 140, yaw=-30, pitch=24)
    _sketch(w, sk.rectangle(10, 0, 18, 22), plane="XZ")
    rec.caption = "1  Profil anklicken"
    rec.move_to((14, 0, 11)); rec.click()
    index = _live(w, solids.Revolve(sketch="Skizze1", axis=solids.REVOLVE_AXES[1], angle=1.0,
                                    operation="Neuer Körper"))
    rec.caption = "2  Achse wählen, Winkel ziehen"
    rec.animate(32, lambda t: _set(w, index, angle=1.0 + 359.0 * t),
                lambda t: (18 * math.cos(math.radians(1 + 359 * t)), -18 * math.sin(math.radians(1 + 359 * t)), 22))
    _finish(rec, "3  Eingabe – fertig")


def quader(rec: Recorder, w) -> None:
    rec.look((0, 0, 6), 130)
    rec.caption = "1  Erste Ecke anklicken"
    rec.move_to((-20, -12, 0)); rec.click()
    index = _live(w, solids.Box(center_x=-19.5, center_y=-11.5, length=1, width=1, height=1, operation="Neuer Körper"))
    rec.caption = "2  Zur zweiten Ecke ziehen"
    rec.animate(18, lambda t: _set(w, index, length=1 + 39 * t, width=1 + 23 * t,
                                   center_x=-20 + (1 + 39 * t) / 2, center_y=-12 + (1 + 23 * t) / 2),
                lambda t: (-20 + 40 * t, -12 + 24 * t, 0))
    rec.caption = "3  Höhe am Pfeil ziehen"
    rec.animate(16, lambda t: _set(w, index, height=1 + 14 * t), lambda t: (0, 0, 1 + 14 * t))
    _finish(rec, "Eingabe – fertig")


def zylinder(rec: Recorder, w) -> None:
    rec.look((0, 0, 8), 120)
    rec.caption = "1  Mittelpunkt anklicken"
    rec.move_to((0, 0, 0)); rec.click()
    index = _live(w, solids.Cylinder(diameter=1, height=1, operation="Neuer Körper"))
    rec.caption = "2  Rand aufziehen"
    rec.animate(16, lambda t: _set(w, index, diameter=1 + 29 * t), lambda t: ((1 + 29 * t) / 2, 0, 0))
    rec.caption = "3  Höhe am Pfeil ziehen"
    rec.animate(16, lambda t: _set(w, index, height=1 + 19 * t), lambda t: (0, 0, 1 + 19 * t))
    _finish(rec, "Eingabe – fertig")


def kugel(rec: Recorder, w) -> None:
    rec.look((0, 0, 0), 110)
    rec.caption = "1  Mittelpunkt anklicken"
    rec.move_to((0, 0, 0)); rec.click()
    index = _live(w, solids.Sphere(diameter=1, operation="Neuer Körper"))
    rec.caption = "2  Rand aufziehen"
    rec.animate(22, lambda t: _set(w, index, diameter=1 + 29 * t), lambda t: ((1 + 29 * t) / 2, 0, 0))
    _finish(rec, "Eingabe – fertig")


def _block(w, height: float = 12.0) -> None:
    _add(w, solids.Box(length=40, width=26, height=height, operation="Neuer Körper"))


def abrunden(rec: Recorder, w) -> None:
    rec.look((0, 0, 6), 105)
    _block(w)
    edges = _top_edges(w, 12.0)
    rec.caption = "1  Kanten anklicken"
    rec.move_to(tuple(edges[0])); rec.click()
    index = _live(w, ft.Fillet(radius=0.2, edge_points=edges, require_pick=True))
    rec.shot(hold=4)
    rec.caption = "2  Radius am Pfeil ziehen"
    rec.animate(24, lambda t: _set(w, index, radius=0.2 + 4.8 * t), lambda t: (0, 13, 12 - 4 * t))
    _finish(rec, "3  Eingabe – fertig")


def fase(rec: Recorder, w) -> None:
    rec.look((0, 0, 6), 105)
    _block(w)
    edges = _top_edges(w, 12.0)
    rec.caption = "1  Kanten anklicken"
    rec.move_to(tuple(edges[0])); rec.click()
    index = _live(w, ft.Chamfer(distance=0.2, edge_points=edges, require_pick=True))
    rec.shot(hold=4)
    rec.caption = "2  Abstand am Pfeil ziehen"
    rec.animate(24, lambda t: _set(w, index, distance=0.2 + 3.8 * t), lambda t: (0, 13, 12 - 3 * t))
    _finish(rec, "3  Eingabe – fertig")


def bohrung(rec: Recorder, w) -> None:
    rec.look((0, 0, 4), 100, pitch=38)
    _block(w)
    top = _face_point(w)
    face = next(f for f in ft.faces_of(next(iter(w.timeline.state.bodies.values())))
                if abs(ft.face_point(f)[2] - 12.0) < 1e-6)
    frame = sk.face_frame(face)
    rec.caption = "1  Fläche anklicken"
    rec.move_to(tuple(top)); rec.click()
    index = _live(w, solids.Hole(frame=frame, diameter=1.0, depth=0.0))
    rec.caption = "2  Durchmesser einstellen"
    rec.animate(20, lambda t: _set(w, index, diameter=1 + 9 * t), lambda t: (top[0] + (1 + 9 * t) / 2, top[1], 12))
    _finish(rec, "3  Eingabe – fertig")


def schale(rec: Recorder, w) -> None:
    rec.look((0, 0, 6), 105, pitch=40)
    _block(w, 16)
    top = _face_point(w)
    rec.caption = "1  Fläche anklicken, die offen sein soll"
    rec.move_to(tuple(top)); rec.click()
    index = _live(w, solids.Shell(thickness=0.4, face_points=[top], require_pick=True))
    rec.caption = "2  Wandstärke einstellen"
    rec.animate(22, lambda t: _set(w, index, thickness=0.4 + 3.1 * t), lambda t: (20 - 3.5 * t, 0, 16))
    _finish(rec, "3  Eingabe – fertig")


def druecken_ziehen(rec: Recorder, w) -> None:
    rec.look((0, 0, 8), 115)
    _block(w)
    top = _face_point(w)
    rec.caption = "1  Fläche anklicken"
    rec.move_to(tuple(top)); rec.click()
    index = _live(w, solids.PressPull(distance=0.0, face_points=[top]))
    rec.caption = "2  Am Pfeil ziehen oder drücken"
    rec.animate(18, lambda t: _set(w, index, distance=10 * t), lambda t: (top[0], top[1], 12 + 10 * t))
    rec.animate(14, lambda t: _set(w, index, distance=10 - 15 * t), lambda t: (top[0], top[1], 22 - 15 * t))
    _finish(rec, "3  Eingabe – fertig")


def kombinieren(rec: Recorder, w) -> None:
    rec.look((0, 0, 8), 120)
    _block(w)
    _add(w, solids.Cylinder(center_x=10, diameter=14, height=22, operation="Neuer Körper"))
    target, tool = list(w.timeline.state.bodies)
    index = _live(w, solids.CombineBodies(operation="Ausschneiden"))
    rec.caption = "1  Zielkörper anklicken"
    rec.move_to((-12, -6, 12)); rec.click()
    w._combine_choose(index, w.timeline.features[index], target); rec.shot(hold=6)
    rec.caption = "2  Werkzeugkörper anklicken"
    rec.move_to((10, 0, 22)); rec.click()
    w._combine_choose(index, w.timeline.features[index], tool); rec.shot(hold=14)
    _finish(rec, "3  Eingabe – ausgeschnitten")


def spiegeln(rec: Recorder, w) -> None:
    rec.look((0, 0, 6), 140)
    _add(w, solids.Box(center_x=16, length=18, width=24, height=10, operation="Neuer Körper"))
    _add(w, solids.Cylinder(center_x=20, diameter=8, height=20, operation="Vereinigen"))
    rec.caption = "1  Körper wählen, Werkzeug starten"
    rec.move_to((16, 0, 10)); rec.click()
    index = _live(w, solids.MirrorBody(plane="YZ", operation="Neuer Körper"))
    rec.caption = "2  Spiegelebene wählen"
    rec.shot(hold=16)
    _finish(rec, "3  Eingabe – gespiegelt", hold=14)
    del index


def muster(rec: Recorder, w) -> None:
    rec.look((27, 0, 4), 115)
    _add(w, solids.Cylinder(diameter=10, height=8, operation="Neuer Körper"))
    rec.caption = "1  Körper wählen, Werkzeug starten"
    rec.move_to((0, 0, 8)); rec.click()
    index = _live(w, solids.RectangularPattern(axis="X", count=1, spacing=18))
    rec.caption = "2  Anzahl und Abstand einstellen"
    for count in (2, 3, 4):
        _set(w, index, count=float(count)); rec.cursor = (18 * (count - 1), 0, 8); rec.shot(hold=7)
    _finish(rec, "3  Eingabe – fertig")


def kreismuster(rec: Recorder, w) -> None:
    rec.look((0, 0, 4), 140, pitch=45)
    _add(w, solids.Cylinder(center_x=22, diameter=9, height=8, operation="Neuer Körper"))
    rec.caption = "1  Körper wählen, Werkzeug starten"
    rec.move_to((22, 0, 8)); rec.click()
    index = _live(w, solids.CircularPattern(axis="Z", count=1, angle=360))
    rec.caption = "2  Achse wählen, Anzahl einstellen"
    for count in range(2, 9):
        _set(w, index, count=float(count)); rec.shot(hold=4)
    _finish(rec, "3  Eingabe – fertig")


def verschieben(rec: Recorder, w) -> None:
    rec.look((12, 0, 6), 150)
    _block(w)
    rec.caption = "1  Körper wählen, Werkzeug starten"
    rec.move_to((0, 0, 12)); rec.click()
    index = _live(w, solids.MoveBody(move_x=0.0, pivot=solids.MOVE_PIVOTS[0]))
    rec.caption = "2  Am Pfeil verschieben"
    rec.animate(16, lambda t: _set(w, index, move_x=22 * t), lambda t: (22 * t + 26, 0, 6))
    # Runde 82: wie Fusion um jede Achse – erst um Z (blaues Ringstück), dann um X (rotes)
    rec.caption = "3  Ringstücke drehen um jede Achse"
    rec.animate(14, lambda t: _set(w, index, angle=40 * t), lambda t: (22 + 9 * math.cos(math.radians(45 + 40 * t)),
                                                                       9 * math.sin(math.radians(45 + 40 * t)), 6))
    rec.animate(14, lambda t: _set(w, index, angle_x=35 * t), lambda t: (22, 9 * math.cos(math.radians(45 + 35 * t)),
                                                                         6 + 9 * math.sin(math.radians(45 + 35 * t))))
    rec.caption = "4  Am hellen Pfeil skalieren"
    rec.animate(14, lambda t: _set(w, index, scale=1.0 - 0.35 * t), lambda t: (34 - 4 * t, -10 + 3 * t, 16 - 4 * t))
    _finish(rec, "Eingabe – fertig")


def leinwand(rec: Recorder, w) -> None:
    """Leinwand auf die Oberseite eines Körpers legen, drehen und skalieren (eigenes, gezeichnetes Testbild)."""
    import tempfile
    from PySide6.QtGui import QColor, QImage, QPainter
    rec.look((0, 0, 8), 120, pitch=38)
    _add(w, solids.Box(length=60, width=40, height=12, operation="Neuer Körper"))
    picture = QImage(400, 200, QImage.Format.Format_ARGB32)
    picture.fill(QColor("#eef1f5"))
    painter = QPainter(picture)
    for x in range(0, 400, 50):
        for y in range(0, 200, 50):
            if (x + y) // 50 % 2:
                painter.fillRect(x, y, 50, 50, QColor("#3a78d4"))
    painter.setBrush(QColor("#e0602e"))
    painter.drawEllipse(150, 50, 100, 100)
    painter.end()
    path = Path(tempfile.gettempdir()) / "wolfcad_clip_vorlage.png"
    picture.save(str(path))
    body = next(iter(w.timeline.state.bodies))
    faces = ft.faces_of(w.timeline.state.bodies[body])
    top = max(range(len(faces)), key=lambda number: ft.face_point(faces[number])[2])
    rec.caption = "1  Fläche wählen, Bild öffnen"
    rec.move_to((0, 0, 12)); rec.click()
    w._selection = [("face", body, top)]
    w.insert_canvas_file(str(path))
    w._selection = []
    w._show_selection()
    index = len(w.timeline.features) - 1
    rec.shot(hold=8)
    rec.caption = "2  Am Bogen drehen"
    rec.animate(14, lambda t: _set(w, index, angle=25.0 * t))
    rec.caption = "3  Am hellen Pfeil skalieren"
    width = w.timeline.features[index].width
    rec.animate(14, lambda t: _set(w, index, width=width * (1 - 0.35 * t)))
    rec.caption = "4  An den Pfeilen verschieben"
    rec.animate(12, lambda t: _set(w, index, center_x=-10.0 * t))
    _finish(rec, "Eingabe – darauf nachzeichnen")
    path.unlink(missing_ok=True)


def teilen(rec: Recorder, w) -> None:
    rec.look((0, 0, 6), 115)
    _block(w)
    rec.caption = "1  Werkzeug starten, Ebene wählen"
    rec.move_to((0, 0, 12)); rec.click()
    index = _live(w, solids.SplitBody(plane="YZ", offset=-12.0))
    rec.caption = "2  Ebene an die Schnittstelle ziehen"
    rec.animate(18, lambda t: _set(w, index, offset=-12 + 18 * t), lambda t: (-12 + 18 * t, 0, 14))
    _finish(rec, "3  Eingabe – zwei Körper", hold=6)
    bodies = list(w.timeline.state.bodies)
    moved = _live(w, solids.MoveBody(body=bodies[-1], move_x=0.0))
    rec.animate(12, lambda t: _set(w, moved, move_x=10 * t))
    _finish(rec, "Die Stücke sind eigene Körper", hold=14)


def skizze(rec: Recorder, w) -> None:
    rec.look((0, 0, 0), 120, pitch=40)
    rec.caption = "1  Werkzeug starten, Ebene wählen"
    rec.move_to((0, 0, 0)); rec.click()
    index = _sketch(w, [])
    rec.caption = "2  Zeichnen"
    corners = [(-20, -12), (20, -12), (20, 12), (-20, 12), (-20, -12)]
    _draw_polyline(rec, w, index, corners)
    rec.caption = "3  Skizze fertig stellen – Profil ist bereit"
    rec.cursor = None
    rec.shot(hold=16)


# --- Skizzenwerkzeuge (von oben auf die Skizze) ------------------------------------------------------------------

def _draw_polyline(rec: Recorder, w, index: int, corners: list, steps: int = 7, hud: bool = True) -> None:
    """Linienzug wie mit dem Linienwerkzeug: jede Strecke wächst bis zum nächsten Klick."""
    done: list = []
    for (ax, ay), (bx, by) in zip(corners, corners[1:]):
        for number in range(1, steps + 1):
            t = number / steps
            x, y = ax + (bx - ax) * t, ay + (by - ay) * t
            _set(w, index, entities=[*done, sk.line(ax, ay, x, y)])
            rec.cursor = (x, y, 0)
            if hud:
                length = math.hypot(x - ax, y - ay)
                rec.view.set_hud([(((ax + x) / 2, (ay + y) / 2 + 3, 0), f"{length:.0f} mm", True, False)])
            rec.shot()
        done.append(sk.line(ax, ay, bx, by))
        rec.click()
    rec.view.set_hud([])


def _top(rec: Recorder, w) -> int:
    # genau 90° zeichnet das Raster streifig; das Ziel liegt etwas tiefer, damit unten Platz für die Schrittzeile ist
    rec.look((0, -5, 0), 125, yaw=0.0, pitch=89.0)
    return _sketch(w, [])


def linie(rec: Recorder, w) -> None:
    index = _top(rec, w)
    rec.caption = "Punkte nacheinander anklicken"
    rec.move_to((-25, -10, 0)); rec.click()
    _draw_polyline(rec, w, index, [(-25, -10), (5, -10), (20, 12)])
    rec.caption = "Esc oder Doppelklick beendet den Zug"
    rec.shot(hold=14)


def rechteck(rec: Recorder, w) -> None:
    index = _top(rec, w)
    rec.caption = "1  Erste Ecke anklicken"
    rec.move_to((-22, -12, 0)); rec.click()
    rec.caption = "2  Zweite Ecke anklicken"
    for number in range(1, 17):
        t = number / 16
        x, y = -22 + 44 * t, -12 + 26 * t
        _set(w, index, entities=sk.rectangle(-22, -12, x, y))
        rec.cursor = (x, y, 0)
        rec.view.set_hud([((-22 + (x + 22) / 2, y + 4, 0), f"{x + 22:.0f} mm", True, False),
                          ((x + 5, (y - 12) / 2, 0), f"{y + 12:.0f} mm", False, False)])
        rec.shot()
    rec.click()
    rec.view.set_hud([])
    rec.caption = "Fertig – Breite und Höhe lassen sich auch eintippen"
    rec.cursor = None
    rec.shot(hold=14)


def kreis(rec: Recorder, w) -> None:
    index = _top(rec, w)
    rec.caption = "1  Mittelpunkt anklicken"
    rec.move_to((0, 0, 0)); rec.click()
    rec.caption = "2  Rand anklicken (oder Durchmesser tippen)"
    for number in range(1, 17):
        radius = 1 + 17 * number / 16
        _set(w, index, entities=[sk.circle(0, 0, radius)])
        rec.cursor = (radius * 0.707, radius * 0.707, 0)
        rec.view.set_hud([((0, radius + 4, 0), f"⌀ {2 * radius:.0f} mm", True, False)])
        rec.shot()
    rec.click()
    rec.view.set_hud([])
    rec.cursor = None
    rec.caption = "Fertig"
    rec.shot(hold=12)


def bogen(rec: Recorder, w) -> None:
    index = _top(rec, w)
    rec.caption = "1  Mittelpunkt anklicken"
    rec.move_to((0, 0, 0)); rec.click()
    rec.caption = "2  Anfang anklicken"
    rec.move_to((18, 0, 0)); rec.click()
    rec.caption = "3  Ende anklicken"
    for number in range(1, 19):
        angle = 160 * number / 18
        _set(w, index, entities=[sk.arc(0, 0, 18, 0, angle)])
        rec.cursor = (18 * math.cos(math.radians(angle)), 18 * math.sin(math.radians(angle)), 0)
        rec.shot()
    rec.click()
    rec.cursor = None
    rec.caption = "Fertig"
    rec.shot(hold=12)


def polygon(rec: Recorder, w) -> None:
    index = _top(rec, w)
    rec.caption = "1  Mitte anklicken"
    rec.move_to((0, 0, 0)); rec.click()
    rec.caption = "2  Ecke anklicken – Eckenzahl einstellbar"
    for number in range(1, 17):
        angle = math.radians(20 + 50 * number / 16)
        radius = 6 + 12 * number / 16
        x, y = radius * math.cos(angle), radius * math.sin(angle)
        _set(w, index, entities=sk.polygon(0, 0, x, y, 6))
        rec.cursor = (x, y, 0)
        rec.shot()
    rec.click()
    rec.cursor = None
    rec.caption = "Fertig"
    rec.shot(hold=12)


# Werkzeugname -> Szene, je Art (Modell bzw. Reiter SKIZZE)
SCENES = {
    (MODEL, "Skizze"): skizze, (MODEL, "Extrusion"): extrusion, (MODEL, "Drehen"): drehen,
    (MODEL, "Quader"): quader, (MODEL, "Zylinder"): zylinder, (MODEL, "Kugel"): kugel,
    (MODEL, "Abrunden"): abrunden, (MODEL, "Fase"): fase, (MODEL, "Bohrung"): bohrung, (MODEL, "Schale"): schale,
    (MODEL, "Drücken/Ziehen"): druecken_ziehen, (MODEL, "Kombinieren"): kombinieren, (MODEL, "Spiegeln"): spiegeln,
    (MODEL, "Muster"): muster, (MODEL, "Kreismuster"): kreismuster, (MODEL, "Verschieben"): verschieben,
    (MODEL, "Teilen"): teilen, (MODEL, "Leinwand"): leinwand,
    (SKETCH, "Linie"): linie, (SKETCH, "Rechteck"): rechteck, (SKETCH, "Kreis"): kreis, (SKETCH, "Bogen"): bogen,
    (SKETCH, "Polygon"): polygon,
}

# Runde 82: die Werkzeuge aus den Aufklappmenüs. Das Modul nutzt die Hilfen oben und trägt seine Szenen selbst in
# SCENES ein – daher erst hier geladen (auch wenn es zuerst importiert wird, ist danach alles eingetragen)
from . import clip_scenes_more  # noqa: E402,F401


def record(out: Path, only: list[str] | None = None) -> list[str]:
    """Nimmt die Szenen auf (alle oder nur die genannten Werkzeuge); gibt Meldungen je Clip zurück."""
    from PySide6.QtTest import QTest
    from . import main_window as mw
    report = []
    for (kind, tool), scene in SCENES.items():
        if only and tool not in only:
            continue
        w = mw.MainWindow()
        w.resize(1500, 950)
        w.show()
        QTest.qWaitForWindowExposed(w)
        QTest.qWait(150)
        rec = Recorder(w)
        try:
            scene(rec, w)
            path = out / clip_name(kind, tool)
            rec.save(path)
            report.append(f"{kind}/{tool}: {len(rec.frames)} Bilder, {path.stat().st_size // 1024} KB"
                          + (f", Fehler im Modell: {w.timeline.errors}" if w.timeline.errors else ""))
        except Exception as error:      # eine fehlerhafte Szene hält die übrigen nicht auf
            import traceback
            report.append(f"{kind}/{tool}: FEHLER {error!r}\n{traceback.format_exc()}")
        w._finish_live(False)
        w.close()
        w.deleteLater()
        QTest.qWait(50)
        print(report[-1], flush=True)
    return report


if __name__ == "__main__":
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication
    surface = QSurfaceFormat()
    surface.setVersion(3, 3)
    surface.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    surface.setDepthBufferSize(24)
    surface.setSamples(4)
    QSurfaceFormat.setDefaultFormat(surface)
    app = QApplication(sys.argv)
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else CLIP_DIR
    record(target, sys.argv[2:] or None)
