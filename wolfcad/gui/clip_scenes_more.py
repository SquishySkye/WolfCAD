# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Weitere Werkzeug-Clips (Runde 82): die Werkzeuge aus den Aufklappmenüs der Leiste.

Runde 80 hat die Werkzeuge vorne in der Leiste vorgeführt (clip_scenes.py). Hier kommen die übrigen dazu – die
Modellwerkzeuge über einen kleinen Baukasten (`_scene`: Beispielteil, Klick, Werkzeug starten, einen Wert über
mehrere Bilder ändern, bestätigen), die Skizzenwerkzeuge als Folge von Skizzenständen (`_drawing`).

Ohne Clip bleiben Befehle, die nichts im Modell vorführen (Datei, Rückgängig, Einstellungen, Ansicht …).
Eigene Aufnahmen aus WolfCAD – keine Grafik oder Videos von Autodesk.
"""
from __future__ import annotations

import copy
import dataclasses
import math
from pathlib import Path

from ..core import extras as ex
from ..core import features as ft
from ..core import gears as gr
from ..core import meshes as ms
from ..core import more_tools as mt
from ..core import physics
from ..core import sheetmetal as sm
from ..core import sketch as sk
from ..core import solids
from ..core import surfaces as sf
from .clip_recorder import Recorder
from .clip_scenes import MODEL, SCENES, SKETCH, _add, _face_point, _finish, _live, _set, _sketch, _top, _top_edges

NEW = "Neuer Körper"


# --- Baukasten für Modellwerkzeuge -------------------------------------------------------------------------------

def _scene(make, setup=None, field: str | None = None, start: float = 0.0, end: float = 0.0, steps: int = 20,
           pick=None, look=((0, 0, 6), 120), captions=("1  Auswahl anklicken", "2  Wert einstellen",
                                                       "3  Eingabe – fertig"), cursor=None, after=None):
    """Baut eine Szene: `setup(w)` legt das Beispielteil an und gibt Werte für die übrigen Schritte zurück,
    `pick(w, werte)` ist der angeklickte Modellpunkt, `make(w, werte)` das Werkzeug. Mit `field` wird dieser Wert
    von `start` bis `end` gezogen (Zeiger nach `cursor(t, werte)`), sonst steht das Ergebnis eine Weile.
    `after(rec, w, index)` darf vor dem Bestätigen noch etwas zeigen."""
    def run(rec: Recorder, w) -> None:
        target, distance, *angles = look
        rec.look(target, distance, *angles)
        values = setup(w) if setup else {}
        if pick is not None:
            rec.caption = captions[0]
            rec.move_to(tuple(pick(w, values)))
            rec.click()
        wanted = make(w, values)
        index = _live(w, copy.deepcopy(wanted))
        # Beim Start übernimmt das Fenster die (leere) Auswahl – z. B. die Netze beim Kombinieren; die für die Szene
        # gewollten Werte gelten trotzdem
        started = w.timeline.features[index]
        changes = {item.name: getattr(wanted, item.name) for item in dataclasses.fields(wanted)
                   if getattr(started, item.name) != getattr(wanted, item.name)}
        if changes:
            _set(w, index, **changes)
        rec.caption = captions[1]
        if field:
            rec.shot(hold=3)
            rec.animate(steps, lambda t: _set(w, index, **{field: start + (end - start) * t}),
                        (lambda t: tuple(cursor(t, values))) if cursor else None)
        else:
            rec.shot(hold=14)
        if after is not None:
            after(rec, w, index)
        _finish(rec, captions[2])
    return run


def _block(w, height: float = 12.0, length: float = 40.0, width: float = 26.0) -> dict:
    _add(w, solids.Box(length=length, width=width, height=height, operation=NEW))
    return {"body": next(iter(w.timeline.state.bodies)), "top": _face_point(w)}


def _first(w) -> str:
    return next(iter(w.timeline.state.bodies))


def _faces_where(w, test, body: str | None = None) -> list:
    """Merkpunkte der Flächen, deren Merkpunkt die Bedingung erfüllt."""
    shape = w.timeline.state.bodies[body or _first(w)]
    return [list(ft.face_point(face)) for face in ft.faces_of(shape) if test(ft.face_point(face))]


def _cylinder_side(w, body: str | None = None) -> list:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    shape = w.timeline.state.bodies[body or _first(w)]
    return [list(ft.face_point(face)) for face in ft.faces_of(shape)
            if BRepAdaptor_Surface(face).GetType() == GeomAbs_Cylinder][:1]


def _surface(w) -> str:
    return next(iter(w.timeline.state.surfaces))


def _mesh(w) -> str:
    return next(iter(w.timeline.state.meshes))


def _arc_sheet(w) -> dict:
    """Gewölbter Flächenkörper: ein offener Bogen, 30 mm hoch extrudiert."""
    _sketch(w, [sk.arc(0, -20, 30, 50, 130)], name="Bogen")
    _add(w, sf.SurfaceExtrude(sketch="Bogen", distance=30.0))
    return {"surface": _surface(w)}


def _flat_sheet(w, name: str = "Blatt", z: float = 0.0) -> dict:
    """Ebener Flächenkörper aus einem Rechteck (Flicken)."""
    _add(w, sk.Sketch(name=name, plane="XY", offset=z, entities=sk.rectangle(-20, -14, 20, 14)))
    _add(w, sf.Patch(sketch=name))
    return {"surface": list(w.timeline.state.surfaces)[-1]}


def _sculpt_step(values: dict):
    from ..core import sculpt
    return sculpt.SculptStep(mesh=values["mesh"], data="")


def _sculpt_strokes(rec, w, index: int) -> None:
    """Formen für den Clip: echte Pinselstriche am Netz (wie im Formen-Modus), je Tupfer ein Bild."""
    import numpy as np
    from ..core import sculpt
    feature = w.timeline.features[index]
    mesh = sculpt.SculptMesh.from_triangles(w.timeline.state_before(index).meshes[feature.mesh])
    for kind, start, end, radius, caption in (
            ("Zeichnen", (-8, -15, -2), (8, -15, 4), 4.0, "Zeichnen: Material heraus"),
            ("Aufblasen", (-4, -15, -8), (4, -15, -8), 4.0, "Aufblasen"),
            ("Greifen", (0, -15, 7), (0, -19, 10), 7.0, "Greifen (G): Bereich ziehen"),
            ("Glätten", (-8, -15, 0), (8, -15, 0), 6.0, "Glätten (Umschalt)")):
        rec.caption = "2  " + caption
        hit = mesh.ray_hit((start[0], -100, start[2]), (0, 1, 0))
        if hit is None:
            continue
        stroke = sculpt.Stroke(mesh, sculpt.Brush(kind=kind, radius=radius, strength=0.7), hit[0], (0, 1, 0))
        for step in range(1, 9):
            t = step / 8
            point = np.array(start) + (np.array(end) - np.array(start)) * t
            if kind != "Greifen":
                found = mesh.ray_hit((point[0], -100, point[2]), (0, 1, 0))
                point = found[0] if found is not None else point
            stroke.move_to(point)
            _set(w, index, data=mesh.pack())
            rec.cursor = tuple(point)
            rec.shot(hold=1)
        mesh.refresh_tree()
    rec.shot(hold=6)


def _fine_ball(w) -> dict:
    """Fein zerlegte Netzkugel zum Formen (wie Blenders Kugel mit vielen Flächen)."""
    _add(w, solids.Sphere(diameter=30, operation=NEW))
    _add(w, ms.BodyToMesh(body=_first(w), deflection=0.08, angle=0.08))
    return {"mesh": _mesh(w)}


def _mesh_ball(w) -> dict:
    """Netzkörper aus einer Kugel (wie ein eingelesenes STL)."""
    _add(w, solids.Sphere(diameter=30, operation=NEW))
    _add(w, ms.BodyToMesh(body=_first(w), deflection=0.6))
    return {"mesh": _mesh(w)}


def _sheet(w) -> dict:
    """Blechteil: Grundflansch aus einem Rechteck, 1,5 mm dick."""
    _sketch(w, sk.rectangle(-20, -15, 20, 15), name="Blech")
    _add(w, sm.BaseFlange(sketch="Blech", thickness=1.5))
    return {"body": _first(w)}


def _sheet_edge(w) -> list:
    """Merkpunkt der oberen Längskante vorne am Blech."""
    shape = w.timeline.state.bodies[_first(w)]
    edges = [ft.edge_point(edge) for edge in ft.unique_edges(shape)]
    return [list(min((p for p in edges if abs(p[2] - 1.5) < 1e-6 and abs(p[0]) < 1e-6), key=lambda p: p[1]))]


def _bent(w) -> dict:
    """Blech mit einem hochgebogenen Flansch (für Abwickeln, Zurückbiegen, Abwicklung, Aufreißen)."""
    values = _sheet(w)
    _add(w, sm.EdgeFlange(edge_points=_sheet_edge(w), length=14.0))
    return values


# --- Konstruktion ----------------------------------------------------------------------------------------------

def _along_path(w) -> dict:
    _sketch(w, [sk.spline((-25, -10), (-5, 12), (15, -6), (28, 10))], name="Pfad")
    return {}


def _axis_points(t: float) -> list:
    return [20.0, -13.0, 12.0 + 18.0 * t]


# --- Körperwerkzeuge -------------------------------------------------------------------------------------------

def _sweep_setup(w) -> dict:
    _add(w, sk.Sketch(name="Pfad", plane="XY", entities=[sk.line(0, 0, 0, 20), sk.arc(15, 20, 15, 90, 180)]))
    _add(w, sk.Sketch(name="Profil", plane="XZ", entities=[sk.circle(0, 0, 4)]))
    return {}


def _loft_setup(w) -> dict:
    _add(w, sk.Sketch(name="Unten", plane="XY", entities=sk.rectangle(-15, -15, 15, 15)))
    _add(w, sk.Sketch(name="Oben", plane="XY", offset=25.0, entities=[sk.circle(0, 0, 8)]))
    return {}


def _rib_setup(w) -> dict:
    _add(w, solids.Box(length=40, width=20, height=4, operation=NEW))
    _add(w, solids.Box(center_x=-18, length=4, width=20, height=30, operation="Vereinigen"))
    _add(w, sk.Sketch(name="Rippe", plane="XZ", entities=[sk.line(-16, 26, 18, 4)]))
    return {}


def _emboss_setup(w) -> dict:
    values = _block(w, height=10, length=50, width=30)
    _add(w, sk.Sketch(name="Logo", plane="XY", offset=10.0, entities=[sk.circle(-10, 0, 6), *sk.polygon(10, 0, 17, 0, 5)]))
    return values


def _web_setup(w) -> dict:
    _add(w, solids.Box(length=40, width=30, height=3, operation=NEW))
    _add(w, sk.Sketch(name="Steg", plane="XY", offset=3.0, entities=[sk.line(-18, -12, 18, 12)]))
    return {}


def _path_pattern_setup(w) -> dict:
    _add(w, solids.Cylinder(center_x=-25, center_y=-8, diameter=6, height=6, operation=NEW))
    _add(w, sk.Sketch(name="Pfad", plane="XY", entities=[sk.spline((-25, -8), (-5, 12), (15, -6), (28, 10))]))
    return {"body": _first(w)}


def _replace_setup(w) -> dict:
    values = _block(w)
    _add(w, sk.ConstructionPlane(plane="XY", offset=18.0, tilt=12.0))
    values["plane"] = next(iter(w.timeline.state.planes))
    return values


def _filleted_block(w) -> dict:
    values = _block(w)
    _add(w, ft.Fillet(radius=5.0, edge_points=_top_edges(w, 12.0)[:1]))
    shape = w.timeline.state.bodies[_first(w)]
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    values["round"] = [list(ft.face_point(face)) for face in ft.faces_of(shape)
                       if BRepAdaptor_Surface(face).GetType() == GeomAbs_Cylinder][:1]
    return values


def _thread_setup(w) -> dict:
    _add(w, solids.Cylinder(diameter=10, height=24, operation=NEW))
    return {"side": _cylinder_side(w)}


def _align_setup(w) -> dict:
    _add(w, solids.Box(length=40, width=26, height=8, operation=NEW))
    _add(w, solids.Box(center_x=34, center_y=10, length=12, width=12, height=12, operation=NEW))
    base, block = list(w.timeline.state.bodies)
    bottom = min((f for f in ft.faces_of(w.timeline.state.bodies[block])), key=lambda f: ft.face_point(f)[2])
    top = max((f for f in ft.faces_of(w.timeline.state.bodies[base])), key=lambda f: ft.face_point(f)[2])
    return {"body": block, "source": ex.face_normal_at(bottom, ft.face_point(bottom)),
            "target": ex.face_normal_at(top, ft.face_point(top))}


def _align_after(rec: Recorder, w, index: int) -> None:
    values = rec.window._clip_values
    rec.caption = "3  Zielfläche anklicken"
    rec.move_to(tuple(values["target"][:3]))
    rec.click()
    _set(w, index, target=values["target"])
    rec.shot(hold=10)


def _align_make(w, values: dict):
    w._clip_values = values         # für den zweiten Klick (_align_after)
    return ex.Align(body=values["body"], source=values["source"])


# --- Flächen ---------------------------------------------------------------------------------------------------

def _two_curves(w) -> dict:
    _add(w, sk.Sketch(name="Kurve1", plane="XY", entities=[sk.spline((-20, -10), (0, -4), (20, -10))]))
    _add(w, sk.Sketch(name="Kurve2", plane="XY", offset=20.0, entities=[sk.spline((-20, 10), (0, 16), (20, 10))]))
    return {}


def _sweep_curves(w) -> dict:
    _add(w, sk.Sketch(name="Pfad", plane="XY", entities=[sk.spline((0, 0), (10, 15), (30, 20))]))
    _add(w, sk.Sketch(name="Profil", plane="YZ", entities=[sk.arc(0, 0, 8, 0, 180)]))
    return {}


def _trim_setup(w) -> dict:
    values = _flat_sheet(w)
    _add(w, sk.Sketch(name="Schnitt", plane="XY", entities=[sk.circle(0, 0, 9)]))
    _add(w, sf.SurfaceExtrude(sketch="Schnitt", distance=10.0, direction=sf.DIRECTIONS[-1]))
    values["tool"] = list(w.timeline.state.surfaces)[-1]
    return values


def _boundary_setup(w) -> dict:
    _add(w, sk.Sketch(name="Wand", plane="XY", entities=[sk.circle(0, 0, 14)]))
    _add(w, sf.SurfaceExtrude(sketch="Wand", distance=24.0))
    _add(w, sk.ConstructionPlane(plane="XY", offset=4.0))
    _add(w, sk.ConstructionPlane(plane="XY", offset=18.0, tilt=10.0))
    return {"tools": [_surface(w), *w.timeline.state.planes]}


def _stitch_setup(w) -> dict:
    _add(w, sk.Sketch(name="Links", plane="XY", entities=sk.rectangle(-20, -12, 0, 12)))
    _add(w, sf.Patch(sketch="Links"))
    _add(w, sk.Sketch(name="Rechts", plane="XY", entities=[sk.line(0, -12, 0, 12)]))
    _add(w, sf.SurfaceExtrude(sketch="Rechts", distance=16.0))
    return {"surfaces": list(w.timeline.state.surfaces)}


def _unstitch_setup(w) -> dict:
    _add(w, sk.Sketch(name="Kasten", plane="XY", entities=sk.rectangle(-15, -10, 15, 10)))
    _add(w, sf.SurfaceExtrude(sketch="Kasten", distance=15.0))
    return {"surface": _surface(w)}


# --- Gelenke ---------------------------------------------------------------------------------------------------

def _joint_setup(w) -> dict:
    _add(w, solids.Box(length=40, width=26, height=8, operation=NEW))
    _add(w, solids.Cylinder(center_x=35, center_y=12, diameter=10, height=14, operation=NEW))
    base, pin = list(w.timeline.state.bodies)
    bottom = min(_faces_where(w, lambda p: True, pin), key=lambda p: p[2])
    top = max(_faces_where(w, lambda p: True, base), key=lambda p: p[2])
    return {"base": base, "pin": pin, "points": [bottom, top]}


def _mesh_pair(w, combined: bool = True) -> dict:
    """Zwei Netze (Würfel, Kugel); mit `combined` schon zu einem Netz vereinigt (für Netz trennen)."""
    if not w.timeline.state.bodies:
        _add(w, solids.Box(center_x=-14, length=16, width=16, height=16, operation=NEW))
        _add(w, solids.Sphere(center_x=14, diameter=18, operation=NEW))
    for name in list(w.timeline.state.bodies):
        _add(w, ms.BodyToMesh(body=name, deflection=0.4))
    meshes = list(w.timeline.state.meshes)
    if combined:
        _add(w, ex.MeshCombine(meshes=meshes))
        return {"mesh": list(w.timeline.state.meshes)[-1]}
    return {"meshes": meshes}


def _rip_setup(w) -> dict:
    """Blech mit zwei Flanschen, die an der Ecke vorne rechts geschlossen zusammenstoßen (zum Aufreißen)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Line
    values = _sheet(w)
    shape = w.timeline.state.bodies[values["body"]]
    edges = [ft.edge_point(edge) for edge in ft.unique_edges(shape)]
    right = max((p for p in edges if abs(p[2] - 1.5) < 1e-6 and abs(p[1]) < 1e-6), key=lambda p: p[0])
    _add(w, sm.EdgeFlange(edge_points=[*_sheet_edge(w), list(right)], length=14.0, corner=sm.CORNERS[1]))
    shape = w.timeline.state.bodies[values["body"]]
    corner = [ft.edge_point(edge) for edge in ft.unique_edges(shape)
              if BRepAdaptor_Curve(edge).GetType() == GeomAbs_Line]
    values["edge"] = [list(min((p for p in corner if p[2] > 4 and p[0] > 19 and p[1] < -14),
                               key=lambda p: (abs(p[0] - 21) + abs(p[1] + 15.65))))]
    return values


def _bend_setup(w) -> dict:
    values = _sheet(w)
    _add(w, sk.Sketch(name="Biegelinie", plane="XY", offset=1.5, entities=[sk.line(5, -15, 5, 15)]))
    return values


MODEL_MORE = {
    # Konstruktion
    "Ebene": _scene(lambda w, v: sk.ConstructionPlane(plane="XY", offset=0.0), setup=_block, field="offset",
                    start=0.0, end=22.0, pick=lambda w, v: v["top"], look=((0, 0, 10), 140),
                    captions=("1  Fläche oder Ebene anklicken", "2  Abstand am Pfeil ziehen", "3  Eingabe – Ebene steht"),
                    cursor=lambda t, v: (0, 0, 22 * t)),
    "Achse": _scene(lambda w, v: solids.ConstructionAxis(start=[20.0, -13.0, 0.0], end=_axis_points(0.05)),
                    setup=_block, field=None, pick=lambda w, v: (20, -13, 12), look=((0, 0, 8), 130),
                    captions=("1  Kante oder zwei Punkte anklicken", "2  Achse liegt auf der Kante",
                              "3  Eingabe – fertig"),
                    after=lambda rec, w, i: rec.animate(14, lambda t: _set(w, i, end=_axis_points(t)))),
    "Punkt": _scene(lambda w, v: ex.ConstructionPoint(x=-20.0, y=-13.0, z=12.0), setup=_block, field="x",
                    start=-20.0, end=20.0, pick=lambda w, v: (-20, -13, 12),
                    captions=("1  Ecke oder Fläche anklicken", "2  Lage einstellen", "3  Eingabe – Punkt steht"),
                    cursor=lambda t, v: (-20 + 40 * t, -13, 12)),
    "Ebene entlang Pfad": _scene(lambda w, v: mt.PlaneAlongPath(path="Pfad", position=0.0), setup=_along_path,
                                 field="position", start=0.0, end=1.0, steps=26, pick=lambda w, v: (-25, -10, 0),
                                 captions=("1  Pfad anklicken", "2  Lage auf dem Pfad ziehen", "3  Eingabe – fertig")),
    "Achse senkrecht zur Fläche": _scene(lambda w, v: mt.AxisNormalToFace(body=v["body"], point=v["top"]),
                                         setup=_block, pick=lambda w, v: v["top"],
                                         captions=("1  Fläche anklicken", "2  Achse steht senkrecht darauf",
                                                   "3  Eingabe – fertig")),
    # Erstellen
    "Sweep": _scene(lambda w, v: solids.Sweep(sketch="Profil", path="Pfad", operation=NEW), setup=_sweep_setup,
                    pick=lambda w, v: (0, 0, 4), look=((5, 15, 4), 130),
                    captions=("1  Profil anklicken", "2  Pfad wählen – das Profil läuft ihn ab", "3  Eingabe – fertig")),
    "Erhebung": _scene(lambda w, v: solids.Loft(sketch="Unten", sketch2="Oben", operation=NEW), setup=_loft_setup,
                       pick=lambda w, v: (0, 0, 25), look=((0, 0, 12), 130),
                       captions=("1  Erstes Profil anklicken", "2  Weitere Profile wählen – fließender Übergang",
                                 "3  Eingabe – fertig")),
    "Rippe": _scene(lambda w, v: solids.Rib(sketch="Rippe", thickness=0.5), setup=_rib_setup, field="thickness",
                    start=0.5, end=4.0, pick=lambda w, v: (0, 0, 15), look=((0, 0, 12), 130),
                    captions=("1  Linie zwischen den Wänden anklicken", "2  Dicke einstellen", "3  Eingabe – fertig")),
    "Torus": _scene(lambda w, v: ex.Torus(diameter=8.0, section=6.0), field="diameter", start=8.0, end=40.0,
                    pick=lambda w, v: (0, 0, 0), look=((0, 0, 0), 120),
                    captions=("1  Werkzeug starten", "2  Durchmesser einstellen", "3  Eingabe – fertig"),
                    cursor=lambda t, v: ((8 + 32 * t) / 2 + 3, 0, 0)),
    "Spule": _scene(lambda w, v: ex.Coil(diameter=24.0, pitch=7.0, turns=0.5, section=3.0), field="turns",
                    start=0.5, end=4.0, look=((0, 0, 14), 130),
                    captions=("1  Werkzeug starten", "2  Windungen einstellen", "3  Eingabe – fertig")),
    # Runde 97: Zahnrad – wächst am Pfeil in die Breite, danach bekommt es mehr Zähne
    "Zahnrad": _scene(lambda w, v: gr.Gear(module=2.0, teeth=16, thickness=0.5, bore=6.0), field="thickness",
                      start=0.5, end=10.0, look=((0, 0, 4), 300, -30.0, 40.0),
                      captions=("1  Werkzeug starten", "2  Zahnbreite einstellen", "3  Eingabe – fertig"),
                      after=lambda rec, w, i: [(_set(w, i, teeth=n), rec.shot(hold=3)) for n in (18, 20, 24)]),
    "Rohr": _scene(lambda w, v: ex.Pipe(path="Pfad", diameter=1.0), setup=_sweep_setup, field="diameter",
                   start=1.0, end=9.0, pick=lambda w, v: (0, 10, 0), look=((5, 15, 4), 130),
                   captions=("1  Pfad anklicken", "2  Durchmesser einstellen", "3  Eingabe – fertig")),
    "Prägen": _scene(lambda w, v: mt.Emboss(sketch="Logo", face_points=[v["top"]], depth=0.1), setup=_emboss_setup,
                     field="depth", start=0.1, end=2.5, pick=lambda w, v: (-10, 0, 10), look=((0, 0, 6), 110, -35, 40),
                     captions=("1  Profil und Fläche anklicken", "2  Höhe einstellen", "3  Eingabe – fertig")),
    "Steg": _scene(lambda w, v: mt.Web(sketch="Steg", thickness=2.0, depth=0.5), setup=_web_setup, field="depth",
                   start=0.5, end=10.0, pick=lambda w, v: (0, 0, 3),
                   captions=("1  Linie anklicken", "2  Höhe einstellen", "3  Eingabe – fertig")),
    "Pfadmuster": _scene(lambda w, v: solids.PathPattern(body=v["body"], path="Pfad", count=1.0),
                         setup=_path_pattern_setup, pick=lambda w, v: (-25, -8, 6), look=((0, 0, 4), 130),
                         captions=("1  Körper wählen, Pfad anklicken", "2  Anzahl einstellen", "3  Eingabe – fertig"),
                         after=lambda rec, w, i: [(_set(w, i, count=float(n)), rec.shot(hold=4)) for n in range(2, 8)]),
    # Ändern
    "Fläche ersetzen": _scene(lambda w, v: solids.ReplaceFace(face_points=[v["top"]], target=v["plane"]),
                              setup=_replace_setup, pick=lambda w, v: v["top"], look=((0, 0, 10), 130),
                              captions=("1  Fläche anklicken", "2  Neue Fläche oder Ebene wählen",
                                        "3  Eingabe – Fläche folgt der Ebene")),
    "Fläche löschen": _scene(lambda w, v: solids.DeleteFace(face_points=v["round"]), setup=_filleted_block,
                             pick=lambda w, v: v["round"][0], look=((0, 0, 6), 105),
                             captions=("1  Fläche anklicken (hier die Rundung)", "2  Die Lücke schließt sich",
                                       "3  Eingabe – fertig")),
    "Fläche verschieben": _scene(lambda w, v: solids.MoveFace(face_points=[v["top"]], move_z=0.0), setup=_block,
                                 field="move_z", start=0.0, end=8.0, pick=lambda w, v: v["top"],
                                 captions=("1  Fläche anklicken", "2  Am Pfeil verschieben", "3  Eingabe – fertig"),
                                 cursor=lambda t, v: (v["top"][0], v["top"][1], 12 + 8 * t)),
    "Schräge": _scene(lambda w, v: solids.Draft(angle=0.0), setup=lambda w: _block(w, height=20), field="angle",
                      start=0.0, end=12.0, pick=lambda w, v: (20, 0, 10), look=((0, 0, 10), 120),
                      captions=("1  Seitenflächen anklicken", "2  Winkel einstellen", "3  Eingabe – fertig")),
    "Skalieren": _scene(lambda w, v: solids.ScaleBody(factor=1.0), setup=_block, field="factor", start=1.0,
                        end=1.6, pick=lambda w, v: v["top"], look=((0, 0, 8), 140),
                        captions=("1  Körper wählen", "2  Faktor einstellen", "3  Eingabe – fertig")),
    "Gewinde": _scene(lambda w, v: solids.Thread(face_points=v["side"]), setup=_thread_setup,
                      pick=lambda w, v: v["side"][0], look=((0, 0, 12), 80),
                      captions=("1  Zylinderfläche anklicken", "2  Größe wählen – Gewinde folgt",
                                "3  Eingabe – fertig")),
    "Fläche teilen": _scene(lambda w, v: ex.SplitFace(face_points=[v["top"]], plane="YZ", offset=-15.0),
                            setup=_block, field="offset", start=-15.0, end=8.0, pick=lambda w, v: v["top"],
                            captions=("1  Fläche anklicken", "2  Teilende Ebene verschieben", "3  Eingabe – zwei Flächen"),
                            cursor=lambda t, v: (-15 + 23 * t, 0, 12)),
    "Ausrichten": _scene(_align_make, setup=_align_setup, pick=lambda w, v: v["source"][:3],
                         look=((10, 0, 6), 140), after=_align_after,
                         captions=("1  Fläche am Körper anklicken", "2  Ausrichten gestartet", "4  Eingabe – liegt auf")),
    "Farbe": _scene(lambda w, v: solids.Appearance(), setup=_block,
                    pick=lambda w, v: v["top"],
                    captions=("1  Körper oder Fläche wählen", "2  Farbe aus der Liste wählen", "3  Eingabe – fertig")),
    # Flächen
    "Fläche extrudieren": _scene(lambda w, v: sf.SurfaceExtrude(sketch="Bogen", distance=0.5),
                                 setup=lambda w: _sketch(w, [sk.arc(0, -20, 30, 50, 130)], name="Bogen") and {},
                                 field="distance", start=0.5, end=25.0, pick=lambda w, v: (0, 10, 0),
                                 captions=("1  Kurve anklicken", "2  Am Pfeil ziehen", "3  Eingabe – Flächenkörper"),
                                 cursor=lambda t, v: (0, 10, 0.5 + 24.5 * t)),
    "Fläche drehen": _scene(lambda w, v: sf.SurfaceRevolve(sketch="Kontur", angle=1.0),
                            setup=lambda w: _add(w, sk.Sketch(name="Kontur", plane="XZ", entities=[
                                sk.spline((6, 0), (14, 8), (8, 20))])) and {},
                            field="angle", start=1.0, end=300.0, steps=26, pick=lambda w, v: (14, 0, 8),
                            look=((0, 0, 10), 120),
                            captions=("1  Kurve anklicken", "2  Achse wählen, Winkel ziehen", "3  Eingabe – fertig")),
    "Flicken": _scene(lambda w, v: sf.Patch(sketch="Rand"),
                      setup=lambda w: _sketch(w, [sk.circle(0, 0, 15)], name="Rand") and {},
                      pick=lambda w, v: (15, 0, 0),
                      captions=("1  Geschlossene Kurve anklicken", "2  Sie wird gefüllt", "3  Eingabe – fertig")),
    "Fläche versetzen": _scene(lambda w, v: sf.OffsetSurface(surface=v["surface"], distance=0.2), setup=_arc_sheet,
                               field="distance", start=0.2, end=8.0, pick=lambda w, v: (0, 10, 15),
                               look=((0, 0, 14), 130),
                               captions=("1  Flächenkörper anklicken", "2  Abstand ziehen", "3  Eingabe – fertig")),
    "Fläche sweepen": _scene(lambda w, v: sf.SurfaceSweep(sketch="Profil", path="Pfad"), setup=_sweep_curves,
                             pick=lambda w, v: (0, 0, 8), look=((12, 10, 6), 130),
                             captions=("1  Profilkurve anklicken", "2  Pfad wählen", "3  Eingabe – fertig")),
    "Fläche erheben": _scene(lambda w, v: sf.SurfaceLoft(sketch="Kurve1", sketch2="Kurve2"), setup=_two_curves,
                             pick=lambda w, v: (0, -4, 0), look=((0, 0, 10), 120),
                             captions=("1  Erste Kurve anklicken", "2  Zweite Kurve wählen", "3  Eingabe – fertig")),
    "Begrenzungsfüllung": _scene(lambda w, v: sf.BoundaryFill(tools=v["tools"], cell_points=[[0.0, 0.0, 10.0]]),
                                 setup=_boundary_setup, pick=lambda w, v: (0, -14, 12), look=((0, 0, 12), 130),
                                 captions=("1  Begrenzende Flächen und Ebenen anklicken", "2  Zelle wählen",
                                           "3  Eingabe – Körper aus der Zelle")),
    "Fläche stutzen": _scene(lambda w, v: sf.TrimSurface(surface=v["surface"], tool=v["tool"],
                                                         remove_points=[[0.0, 0.0, 0.0]]),
                             setup=_trim_setup, pick=lambda w, v: (0, 0, 0), look=((0, 0, 0), 120, -35, 45),
                             captions=("1  Fläche und Schnittwerkzeug wählen", "2  Wegfallenden Teil anklicken",
                                       "3  Eingabe – fertig")),
    "Fläche verlängern": _scene(lambda w, v: sf.ExtendSurface(surface=v["surface"], distance=0.2), setup=_arc_sheet,
                                field="distance", start=0.2, end=12.0, pick=lambda w, v: (0, 10, 30),
                                look=((0, 0, 18), 140),
                                captions=("1  Flächenkörper anklicken", "2  Abstand ziehen", "3  Eingabe – fertig")),
    "Trennen": _scene(lambda w, v: sf.Unstitch(surface=v["surface"]), setup=_unstitch_setup,
                      pick=lambda w, v: (15, 0, 8), look=((0, 0, 8), 110),
                      captions=("1  Flächenkörper anklicken", "2  Er zerfällt in einzelne Flächen",
                                "3  Eingabe – fertig")),
    "Regelfläche": _scene(lambda w, v: ex.RuledSurface(sketch="Kurve1", sketch2="Kurve2"), setup=_two_curves,
                          pick=lambda w, v: (0, -4, 0), look=((0, 0, 10), 120),
                          captions=("1  Erste Kurve anklicken", "2  Zweite Kurve: gerade Verbindungen",
                                    "3  Eingabe – fertig")),
    "Normale umkehren": _scene(lambda w, v: ex.ReverseNormal(surface=v["surface"]), setup=_arc_sheet,
                               pick=lambda w, v: (0, 10, 15), look=((0, 0, 14), 130),
                               captions=("1  Flächenkörper anklicken", "2  Innen und außen tauschen",
                                         "3  Eingabe – fertig")),
    "Aufdicken": _scene(lambda w, v: sf.Thicken(surface=v["surface"], thickness=0.2), setup=_arc_sheet,
                        field="thickness", start=0.2, end=5.0, pick=lambda w, v: (0, 10, 15), look=((0, 0, 14), 130),
                        captions=("1  Flächenkörper anklicken", "2  Dicke einstellen", "3  Eingabe – Volumenkörper")),
    "Vernähen": _scene(lambda w, v: sf.Stitch(surfaces=v["surfaces"]), setup=_stitch_setup,
                       pick=lambda w, v: (-10, 0, 0), look=((-5, 0, 6), 110),
                       captions=("1  Flächenkörper anklicken", "2  Gemeinsame Kanten werden verbunden",
                                 "3  Eingabe – ein Flächenkörper")),
    # Netz
    "Körper in Netz umwandeln": _scene(lambda w, v: ms.BodyToMesh(body=_first(w), deflection=0.6),
                                       setup=lambda w: _add(w, solids.Sphere(diameter=30, operation=NEW)) and {},
                                       pick=lambda w, v: (0, 0, 15), look=((0, 0, 0), 110),
                                       captions=("1  Körper wählen", "2  Er wird zum Dreiecksnetz",
                                                 "3  Eingabe – fertig")),
    "Netz in Körper umwandeln": _scene(lambda w, v: ms.MeshToBody(mesh=v["mesh"]), setup=_mesh_ball,
                                       pick=lambda w, v: (0, 0, 15), look=((0, 0, 0), 110),
                                       captions=("1  Netz wählen", "2  Aus Dreiecken wird ein Körper",
                                                 "3  Eingabe – fertig")),
    "Netz verkleinern": _scene(lambda w, v: ms.MeshReduce(mesh=v["mesh"], cell=0.5), setup=_mesh_ball,
                               field="cell", start=0.5, end=6.0, pick=lambda w, v: (0, 0, 15), look=((0, 0, 0), 110),
                               captions=("1  Netz wählen", "2  Zellgröße erhöhen – weniger Dreiecke",
                                         "3  Eingabe – fertig")),
    "Netz reparieren": _scene(lambda w, v: ms.MeshRepair(mesh=v["mesh"]), setup=_mesh_ball,
                              pick=lambda w, v: (0, 0, 15), look=((0, 0, 0), 110),
                              captions=("1  Netz wählen", "2  Löcher und Fehler werden behoben",
                                        "3  Eingabe – fertig")),
    # Runde 94: Formen wie in Blender – Kugel als Netz, Striche mit Zeichnen, Aufblasen, Greifen, Glätten
    "Formen": _scene(lambda w, v: _sculpt_step(v), setup=_fine_ball, pick=lambda w, v: (0, -15, 0),
                     look=((0, 0, 0), 150, -20.0, 15.0),
                     captions=("1  Netz anklicken, „Formen“ starten", "2  Über die Oberfläche streichen",
                               "3  Eingabe – fertig"), after=lambda rec, w, i: _sculpt_strokes(rec, w, i)),
    "Netz glätten": _scene(lambda w, v: ms.MeshSmooth(mesh=v["mesh"], iterations=1, strength=0.5),
                           setup=lambda w: (_add(w, solids.Box(length=30, width=30, height=20, operation=NEW)),
                                            _add(w, ms.BodyToMesh(body=_first(w), deflection=0.3)),
                                            {"mesh": _mesh(w)})[-1],
                           pick=lambda w, v: (0, 0, 20), look=((0, 0, 10), 110),
                           captions=("1  Netz wählen", "2  Durchgänge erhöhen", "3  Eingabe – fertig"),
                           after=lambda rec, w, i: [(_set(w, i, iterations=n), rec.shot(hold=4)) for n in (3, 6, 10, 16)]),
    "Netz schneiden": _scene(lambda w, v: ex.MeshPlaneCut(mesh=v["mesh"], plane="XY", offset=12.0), setup=_mesh_ball,
                             field="offset", start=12.0, end=-4.0, pick=lambda w, v: (0, 0, 15), look=((0, 0, 0), 110),
                             captions=("1  Netz wählen", "2  Schnittebene verschieben", "3  Eingabe – fertig")),
    "Netz trennen": _scene(lambda w, v: ex.MeshSeparate(mesh=v["mesh"]),
                           setup=_mesh_pair,
                           pick=lambda w, v: (-14, 0, 16), look=((0, 0, 6), 120),
                           captions=("1  Netz wählen", "2  Getrennte Teile werden eigene Netze", "3  Eingabe – fertig")),
    "Netze kombinieren": _scene(lambda w, v: ex.MeshCombine(meshes=v["meshes"]),
                                setup=lambda w: _mesh_pair(w, combined=False),
                                pick=lambda w, v: (-14, 0, 16), look=((0, 0, 6), 120),
                                captions=("1  Netze wählen", "2  Sie werden ein Netz", "3  Eingabe – fertig")),
    "Netznormalen umkehren": _scene(lambda w, v: ex.MeshReverse(mesh=v["mesh"]), setup=_mesh_ball,
                                    pick=lambda w, v: (0, 0, 15), look=((0, 0, 0), 110),
                                    captions=("1  Netz wählen", "2  Innen und außen tauschen", "3  Eingabe – fertig")),
    # Blech
    "Flansch": _scene(lambda w, v: sm.BaseFlange(sketch="Blech", thickness=0.3),
                      setup=lambda w: _sketch(w, sk.rectangle(-20, -15, 20, 15), name="Blech") and {},
                      field="thickness", start=0.3, end=3.0, pick=lambda w, v: (0, 0, 0),
                      captions=("1  Profil anklicken", "2  Blechdicke einstellen", "3  Eingabe – Blechteil")),
    "Flansch an Kante": _scene(lambda w, v: sm.EdgeFlange(edge_points=_sheet_edge(w), length=0.5), setup=_sheet,
                               field="length", start=0.5, end=16.0, pick=lambda w, v: _sheet_edge(w)[0],
                               captions=("1  Kante anklicken", "2  Länge am Pfeil ziehen", "3  Eingabe – fertig"),
                               cursor=lambda t, v: (0, -16, 1.5 + 15 * t)),
    "Konturflansch": _scene(lambda w, v: mt.ContourFlange(sketch="Kontur", width=0.5, thickness=1.5),
                            setup=lambda w: _add(w, sk.Sketch(name="Kontur", plane="XZ", entities=[
                                sk.line(-20, 15, -20, 0), sk.line(-20, 0, 20, 0), sk.line(20, 0, 26, 10)])) and {},
                            field="width", start=0.5, end=25.0, pick=lambda w, v: (0, 0, 0), look=((0, 10, 6), 130),
                            captions=("1  Offene Kontur anklicken", "2  Breite ziehen", "3  Eingabe – Blechteil")),
    "Aufreißen": _scene(lambda w, v: mt.Rip(edge_points=v["edge"], gap=0.3), setup=_rip_setup, field="gap",
                        start=0.3, end=3.0, pick=lambda w, v: v["edge"][0], look=((10, -8, 8), 90, -50, 25),
                        captions=("1  Ecke zwischen zwei Wänden anklicken", "2  Spalt einstellen", "3  Eingabe – aufgetrennt")),
    "Saum": _scene(lambda w, v: ex.Hem(edge_points=_sheet_edge(w), length=0.5), setup=_sheet, field="length",
                   start=0.5, end=8.0, pick=lambda w, v: _sheet_edge(w)[0], look=((0, -10, 2), 90),
                   captions=("1  Kante anklicken", "2  Saumlänge einstellen", "3  Eingabe – umgelegt")),
    "Abwicklung": _scene(lambda w, v: sm.FlatPattern(), setup=_bent, pick=lambda w, v: (0, 0, 1.5),
                         look=((35, 0, 4), 175),
                         captions=("1  Blechteil wählen", "2  Abwicklung entsteht daneben", "3  Eingabe – fertig")),
    "In Blech umwandeln": _scene(lambda w, v: sm.ConvertToSheet(body=_first(w)),
                                 setup=lambda w: (_add(w, solids.Box(length=40, width=30, height=2, operation=NEW)),
                                                  {})[-1],
                                 pick=lambda w, v: (0, 0, 2),
                                 captions=("1  Dünnen Körper wählen", "2  Er wird zum Blechteil",
                                           "3  Eingabe – fertig")),
    "Biegen": _scene(lambda w, v: sm.SketchBend(sketch="Biegelinie", angle=1.0), setup=_bend_setup, field="angle",
                     start=1.0, end=90.0, pick=lambda w, v: (5, 0, 1.5), look=((0, 0, 6), 120),
                     captions=("1  Biegelinie anklicken", "2  Winkel einstellen", "3  Eingabe – gebogen")),
    "Abwickeln": _scene(lambda w, v: sm.Unfold(), setup=_bent, pick=lambda w, v: (0, -16, 8),
                        look=((0, 0, 4), 120),
                        captions=("1  Blechteil wählen", "2  Flansche werden flach gelegt", "3  Eingabe – fertig")),
    "Zurückbiegen": _scene(lambda w, v: sm.Refold(), setup=lambda w: (_bent(w), _add(w, sm.Unfold()), {})[-1],
                           pick=lambda w, v: (0, -20, 1.5), look=((0, 0, 4), 120),
                           captions=("1  Abgewickeltes Blech wählen", "2  Wieder gebogen", "3  Eingabe – fertig")),
    # Zusammenbau
    "Komponente": _scene(lambda w, v: solids.Component(body=v["body"], component="Deckel"), setup=_block,
                         pick=lambda w, v: v["top"],
                         captions=("1  Körper wählen", "2  Er wird eine eigene Komponente",
                                   "3  Eingabe – im Browser")),
    "Gelenk": _scene(lambda w, v: solids.Joint(body=v["pin"], target=v["base"], face_points=v["points"],
                                               kinds=["face", "face"]),
                     setup=_joint_setup, pick=lambda w, v: v["points"][0], look=((10, 0, 6), 140),
                     captions=("1  Fläche am bewegten Teil, dann am Ziel", "2  Teil springt an seinen Platz",
                               "3  Eingabe – gefügt")),
    "Gelenkursprung": _scene(lambda w, v: mt.JointOrigin(body=v["body"], point=ex.face_normal_at(
                                 ft.nearest_face(w.timeline.state.bodies[v["body"]], v["top"]), v["top"]), offset=0.0),
                             setup=_block, field="offset", start=0.0, end=10.0, pick=lambda w, v: v["top"],
                             captions=("1  Fläche oder Punkt anklicken", "2  Abstand einstellen",
                                       "3  Eingabe – Ursprung steht")),
    "Starre Gruppe": _scene(lambda w, v: mt.RigidGroup(bodies=list(w.timeline.state.bodies)), setup=_joint_setup,
                            pick=lambda w, v: v["points"][0], look=((10, 0, 6), 140),
                            captions=("1  Körper wählen", "2  Sie bewegen sich nur noch gemeinsam",
                                      "3  Eingabe – fertig")),
}


# --- Skizzenwerkzeuge ------------------------------------------------------------------------------------------

def _drawing(steps: list, final: str = "Fertig", texts=None):
    """Szene aus Skizzenständen: je Eintrag (Beschriftung, Bilder, Funktion t → (Elemente, Zeiger, Maßfelder)).
    `texts(t)` liefert bei Bedarf die Texteinträge der Skizze."""
    def run(rec: Recorder, w) -> None:
        index = _top(rec, w)
        for caption, frames, state in steps:
            rec.caption = caption
            for number in range(frames + 1):
                t = number / frames if frames else 1.0
                entities, cursor, hud, *rest = state(t)
                values = {"entities": entities}
                if rest:
                    values.update(rest[0])
                _set(w, index, **values)
                rec.cursor = cursor
                rec.view.set_hud(hud or [])
                rec.shot()
            if steps and rec.cursor is not None:
                rec.click()
        rec.view.set_hud([])
        rec.cursor = None
        rec.caption = final
        rec.shot(hold=14)
    return run


def _lerp(a, b, t: float):
    """Zahlen bzw. Elemente gleicher Form zwischen a und b mischen (Texte bleiben)."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * t
    if isinstance(a, (list, tuple)):
        return [_lerp(x, y, t) for x, y in zip(a, b)]
    return a


def _morph(before: list, after: list, label: str, at: tuple):
    """Abhängigkeit: die Elemente gleiten in ihre neue Lage; ein Schild nennt die Abhängigkeit."""
    return lambda t: (_lerp(before, after, t), None, [(at, label, t > 0.5, t >= 1.0)])


def _hud(at: tuple, text: str) -> list:
    return [(at, text, True, False)]


def _value(text: str, number: float) -> str:
    return f"{text} {number:.0f} mm"


_SQUARE = sk.rectangle(-18, -12, 18, 12)
_ANGLE_LINES = [sk.line(-20, -10, 20, -10), sk.line(-20, -10, 10, 14)]


def _fillet_frames(kind: str):
    def state(t: float):
        radius = 0.5 + 8.5 * t
        first, second = sk.line(-20, -12, 18, -12), sk.line(18, -12, 18, 14)
        if kind == "Fase":
            cut = 0.5 + 8.5 * t
            entities = [sk.line(-20, -12, 18 - cut, -12), sk.line(18 - cut, -12, 18, -12 + cut),
                        sk.line(18, -12 + cut, 18, 14)]
            return entities, (18 - cut / 2, -12 + cut / 2, 0), _hud((10, -4, 0), _value("", cut))
        done = sk.corner_fillet(first, second, radius)
        entities = list(done) if done else [first, second]
        return entities, (18 - radius * 0.3, -12 + radius * 0.3, 0), _hud((10, -4, 0), f"R {radius:.0f} mm")
    return state


def _arc_points(cx, cy, r, start, end, t):
    angle = math.radians(start + (end - start) * t)
    return cx + r * math.cos(angle), cy + r * math.sin(angle)


SKETCH_MORE = {
    "Auswählen": _drawing([("Elemente anklicken – mit Strg mehrere", 10,
                            lambda t: (_SQUARE, (-18 + 18 * t, -12, 0), None)),
                           ("Ziehen verschiebt die gewählte Linie", 14,
                            lambda t: ([*_SQUARE[:1], sk.line(18 + 6 * t, -12, 18 + 6 * t, 12), *_SQUARE[2:]],
                                       (18 + 6 * t, 0, 0), None))]),
    "Punkt": _drawing([("Punkte anklicken", 4, lambda t: ([sk.point(-15, 5)], (-15, 5, 0), None)),
                       ("Weitere Punkte – z. B. für Bohrungen", 6,
                        lambda t: ([sk.point(-15, 5), sk.point(0, -5), sk.point(15, 5)], (15, 5, 0), None))]),
    "Mittelpunkt-Rechteck": _drawing([("1  Mitte anklicken", 2, lambda t: ([sk.point(0, 0)], (0, 0, 0), None)),
                                      ("2  Ecke anklicken", 16, lambda t: (
                                          sk.rectangle(-(2 + 18 * t), -(2 + 10 * t), 2 + 18 * t, 2 + 10 * t),
                                          (2 + 18 * t, 2 + 10 * t, 0),
                                          _hud((0, 6 + 10 * t, 0), _value("", 4 + 36 * t))))]),
    "3-Punkt-Rechteck": _drawing([("1  Erste Kante zeichnen", 10, lambda t: (
                                      [sk.line(-15, -12, -15 + 30 * t, -12 + 8 * t)], (-15 + 30 * t, -12 + 8 * t, 0),
                                      None)),
                                  ("2  Breite aufziehen – auch schräg", 12, lambda t: (
                                      sk.rectangle_points([(-15, -12), (15, -4), (15 - 6 * t, -4 + 20 * t),
                                                           (-15 - 6 * t, -12 + 20 * t)]),
                                      (15 - 6 * t, -4 + 20 * t, 0), None))]),
    "Kreis 3 Punkte": _drawing([("1  Zwei Punkte anklicken", 4, lambda t: (
                                    [sk.point(-15, 0), sk.point(10, -11)], (10, -11, 0), None)),
                                ("2  Dritter Punkt legt den Kreis fest", 14, lambda t: (
                                    [sk.circle(*_through((-15, 0), (10, -11), (5 + 5 * t, 8 + 4 * t)))],
                                    (5 + 5 * t, 8 + 4 * t, 0), None))]),
    "Kreis 2 Punkte": _drawing([("1  Ersten Punkt anklicken", 2, lambda t: ([sk.point(-15, 0)], (-15, 0, 0), None)),
                                ("2  Gegenüberliegenden Punkt anklicken", 14, lambda t: (
                                    [sk.circle((-15 + (-15 + 30 * t + 2)) / 2, 0, abs(30 * t + 2) / 2)],
                                    (-13 + 30 * t, 0, 0), None))]),
    "Kreis 2 Tangenten": _drawing([("1  Zwei Linien anklicken", 4, lambda t: (_ANGLE_LINES, (0, -10, 0), None)),
                                   ("2  Radius ziehen – Kreis berührt beide", 14, lambda t: (
                                       [*_ANGLE_LINES, sk.circle(*_tangent_two(3 + 5 * t))], (6, 0, 0),
                                       _hud((0, 6, 0), f"R {3 + 5 * t:.0f} mm")))]),
    "Kreis 3 Tangenten": _drawing([("Drei Linien anklicken – Kreis berührt alle", 12, lambda t: (
                                       [sk.line(-20, -12, 20, -12), sk.line(-20, -12, 0, 16), sk.line(20, -12, 0, 16),
                                        *([sk.circle(*_tangent_three())] if t >= 1 else [])],
                                       (_lerp(-10, 10, t), _lerp(-12, 2, t), 0), None))]),
    "3-Punkt-Bogen": _drawing([("1  Anfang und Ende anklicken", 4, lambda t: (
                                   [sk.point(-18, 0), sk.point(18, 0)], (18, 0, 0), None)),
                               ("2  Dritter Punkt: Wölbung", 14, lambda t: (
                                   [sk.arc_through((-18, 0), (0, 2 + 12 * t), (18, 0)) or sk.line(-18, 0, 18, 0)],
                                   (0, 2 + 12 * t, 0), None))]),
    "Tangentialbogen": _drawing([("1  Am Ende einer Linie ansetzen", 4, lambda t: (
                                     [sk.line(-20, -8, 0, -8)], (0, -8, 0), None)),
                                 ("2  Ende ziehen – Bogen schließt glatt an", 14, lambda t: (
                                     [sk.line(-20, -8, 0, -8),
                                      sk.tangent_arc((0, -8), (1, 0), (6 + 6 * t, -2 + 12 * t)) or sk.line(0, -8, 6, -2)],
                                     (6 + 6 * t, -2 + 12 * t, 0), None))]),
    "Langloch": _drawing([("1  Erste Mitte anklicken", 2, lambda t: ([sk.point(-14, 0)], (-14, 0, 0), None)),
                          ("2  Zweite Mitte anklicken", 10, lambda t: (
                              [sk.line(-14, 0, -14 + 28 * t + 0.5, 0)], (-14 + 28 * t, 0, 0), None)),
                          ("3  Breite aufziehen", 12, lambda t: (sk.slot(-14, 0, 14.5, 0, 1 + 6 * t),
                                                                (14, 1 + 6 * t, 0), None))]),
    "Spline": _drawing([("Punkte anklicken – die Kurve läuft glatt hindurch", 18, lambda t: (
                            [sk.spline(*_spline_points(t))], _spline_points(t)[-1] + (0,), None))]),
    "Steuerpunkt-Spline": _drawing([("Steuerpunkte setzen – die Kurve folgt ihnen", 18, lambda t: (
                                        [sk.control_spline(*_control_points(t))],
                                        _control_points(t)[-1] + (0,), None))]),
    "Ellipse": _drawing([("1  Mitte anklicken", 2, lambda t: ([sk.point(0, 0)], (0, 0, 0), None)),
                         ("2  Erste Halbachse", 10, lambda t: ([sk.line(0, 0, 2 + 18 * t, 0)], (2 + 18 * t, 0, 0), None)),
                         ("3  Zweite Halbachse", 12, lambda t: ([sk.ellipse(0, 0, 20, 1 + 9 * t)],
                                                                (0, 1 + 9 * t, 0), None))]),
    "Kegelschnitt": _drawing([("1  Anfang, Ende und Scheitel anklicken", 4, lambda t: (
                                  [sk.line(-18, -8, 0, 12), sk.line(0, 12, 18, -8)], (0, 12, 0), None)),
                              ("2  Rho einstellen: Ellipse … Parabel … Hyperbel", 18, lambda t: (
                                  [sk.conic(-18, -8, 18, -8, 0, 12, 0.2 + 0.7 * t)], (0, 12, 0),
                                  _hud((0, 15, 0), f"Rho {0.2 + 0.7 * t:.2f}".replace(".", ","))))]),
    "Text": _drawing([("1  Stelle anklicken, Text tippen", 10, lambda t: (
                          [], (-20, -4, 0), None, {"texts": [["WolfCAD"[:max(1, round(7 * t))], -20.0, -4.0, 8.0, 0.0]]})),
                      ("2  Höhe einstellen", 10, lambda t: (
                          [], (-20, -4, 0), None, {"texts": [["WolfCAD", -20.0, -4.0, 8.0 + 4.0 * t, 0.0]]}))]),
    "Abstand": _drawing([("1  Zwei Elemente anklicken", 4, lambda t: (
                             [sk.line(-15, -12, -15, 12), sk.line(10, -12, 10, 12)], (10, 0, 0), None)),
                         ("2  Maß eintippen – die Linie folgt", 14, lambda t: (
                             [sk.line(-15, -12, -15, 12), sk.line(10 + 8 * t, -12, 10 + 8 * t, 12)], None,
                             _hud(((-15 + 10 + 8 * t) / 2 - 2, 15, 0), _value("", 25 + 8 * t))))]),
    "Winkel": _drawing([("1  Zwei Linien anklicken", 4, lambda t: (_ANGLE_LINES, (0, 0, 0), None)),
                        ("2  Winkel eintippen", 14, lambda t: (
                            [_ANGLE_LINES[0], sk.line(-20, -10, -20 + 36 * math.cos(math.radians(39 + 21 * t)),
                                                      -10 + 36 * math.sin(math.radians(39 + 21 * t)))], None,
                            _hud((0, -2, 0), f"{39 + 21 * t:.0f}°")))]),
    "Bemaßung": _drawing([("1  Element anklicken", 4, lambda t: (_SQUARE, (0, -12, 0), None)),
                          ("2  Maß platzieren und eintippen", 14, lambda t: (
                              sk.rectangle(-18, -12, 18 + 10 * t, 12), None, _hud((5 * t, -16, 0), _value("", 36 + 10 * t))))]),
    "Abrunden": _drawing([("Ecke anklicken, Radius ziehen", 16, _fillet_frames("Abrunden"))]),
    "Fase": _drawing([("Ecke anklicken, Abstand ziehen", 16, _fillet_frames("Fase"))]),
    "Stutzen": _drawing([("Überstehendes Stück anklicken", 6, lambda t: (
                             [sk.line(-20, 0, 20, 0), sk.line(0, -15, 0, 15)], (10 * t, 7 * t, 0), None)),
                         ("Es fällt bis zur nächsten Kreuzung weg", 2, lambda t: (
                             [sk.line(-20, 0, 20, 0), sk.line(0, -15, 0, 0)], (0, 7, 0), None))]),
    "Verlängern": _drawing([("Linienende anklicken", 4, lambda t: (
                                [sk.line(-20, -6, 2, -6), sk.line(15, -15, 15, 15)], (2, -6, 0), None)),
                            ("Es wächst bis zur nächsten Linie", 10, lambda t: (
                                [sk.line(-20, -6, 2 + 13 * t, -6), sk.line(15, -15, 15, 15)], (2 + 13 * t, -6, 0),
                                None))]),
    "Brechen": _drawing([("Linie an der Kreuzung anklicken", 8, lambda t: (
                             [sk.line(-20, 0, 20, 0), sk.line(0, -15, 0, 15)], (-10 + 10 * t, 0, 0), None)),
                         ("Sie wird dort in zwei Stücke geteilt", 10, lambda t: (
                             [sk.line(-20, 0, -0.5 - 4 * t, 0), sk.line(0.5 + 4 * t, 0, 20, 0),
                              sk.line(0, -15, 0, 15)], (0, 0, 0), None))]),
    "Konstruktion": _drawing([("Element wählen, „Konstruktion“ (X) – Hilfslinie", 10, lambda t: (
                                  [*_SQUARE, sk.line(-18, -12, 18, 12)], (0, 0, 0), None,
                                  {"construction": [4] if t >= 0.5 else []}))]),
    "Spiegeln": _drawing([("1  Elemente und Spiegellinie wählen", 6, lambda t: (
                              [sk.line(0, -16, 0, 16), *sk.polygon(-11, 0, -4, 3, 3)], (0, 10, 0), None,
                              {"construction": [0]})),
                          ("2  Spiegelbild entsteht", 10, lambda t: (
                              [sk.line(0, -16, 0, 16), *sk.polygon(-11, 0, -4, 3, 3),
                               *[_lerp(entity, sk.mirrored(entity, sk.line(0, -16, 0, 16)), t)
                                 for entity in sk.polygon(-11, 0, -4, 3, 3)]], None, None,
                              {"construction": [0]}))]),
    "Versatz": _drawing([("Kontur anklicken, Abstand ziehen", 16, lambda t: (
                             [*_SQUARE, *sk.rectangle(-18 - 5 * t, -12 - 5 * t, 18 + 5 * t, 12 + 5 * t)],
                             (18 + 5 * t, 0, 0), _hud((0, 14 + 5 * t, 0), _value("", 5 * t))))]),
    "Projizieren": _drawing([("Körperkanten anklicken – sie kommen in die Skizze", 10, lambda t: (
                                 sk.rectangle(-15, -10, 15, 10), (15 - 30 * t, -10, 0), None,
                                 {"projected": [0, 1, 2, 3] if t >= 0.5 else []}))]),
    "Schnittkurve": _drawing([("Körper anklicken – sein Schnitt kommt in die Skizze", 10, lambda t: (
                                  [sk.circle(0, 0, 12)] if t >= 0.5 else [], (12 - 6 * t, 4, 0), None,
                                  {"projected": [0] if t >= 0.5 else []}))]),
    "Muster": _drawing([("Elemente wählen, Anzahl und Abstand einstellen", 18, lambda t: (
                            [sk.circle(-18 + 12 * column, -8 + 12 * row, 3)
                             for column in range(1 + round(3 * t)) for row in range(1 + round(t))],
                            (-18 + 36 * t, -8 + 12 * t, 0), None))]),
    "Kreismuster": _drawing([("Elemente und Mitte wählen, Anzahl einstellen", 18, lambda t: (
                                 [sk.circle(0, 0, 3), *[sk.circle(14 * math.cos(2 * math.pi * k / 8),
                                                                  14 * math.sin(2 * math.pi * k / 8), 3)
                                                        for k in range(1 + round(7 * t))]],
                                 (14, 0, 0), None))]),
    "Skizze skalieren": _drawing([("Elemente wählen, Faktor ziehen", 16, lambda t: (
                                      sk.rectangle(-12 * (1 + 0.6 * t), -8 * (1 + 0.6 * t), 12 * (1 + 0.6 * t),
                                                   8 * (1 + 0.6 * t)),
                                      (12 * (1 + 0.6 * t), 8 * (1 + 0.6 * t), 0),
                                      _hud((0, 11 * (1 + 0.6 * t), 0), f"× {1 + 0.6 * t:.1f}".replace(".", ","))))]),
    "Übergangskurve": _drawing([("Zwei Kurvenenden anklicken", 6, lambda t: (
                                    [sk.line(-22, -8, -8, -8), sk.line(8, 10, 22, 10)], (_lerp(-8, 8, t), _lerp(-8, 10, t), 0),
                                    None)),
                                ("Eine glatte Kurve verbindet sie", 2, lambda t: (
                                    [sk.line(-22, -8, -8, -8), sk.line(8, 10, 22, 10),
                                     sk.blend_curve(sk.line(-22, -8, -8, -8), True, sk.line(8, 10, 22, 10), False)
                                     or sk.line(-8, -8, 8, 10)], None, None))]),
    "Verschieben/Kopieren": _drawing([("Elemente wählen, am Griff ziehen oder drehen", 18, lambda t: (
                                          [sk.rotated(entity, 12 * t, 0, 30 * t)
                                           for entity in sk.rectangle(-12 + 12 * t, -8, 12 + 12 * t, 8)],
                                          (12 * t, 0, 0), None))]),
    # Abhängigkeiten: die Elemente gleiten in die verlangte Lage
    "Horizontal": _drawing([("Linie anklicken – sie wird waagrecht", 14,
                             _morph([sk.line(-18, -6, 18, 8)], [sk.line(-18, 1, 18, 1)], "—  waagrecht", (0, 5, 0)))]),
    "Vertikal": _drawing([("Linie anklicken – sie wird senkrecht", 14,
                           _morph([sk.line(-6, -15, 8, 15)], [sk.line(1, -15, 1, 15)], "|  senkrecht", (5, 0, 0)))]),
    "Parallel": _drawing([("Zwei Linien anklicken – sie laufen parallel", 14,
                           _morph([sk.line(-18, -10, 18, -10), sk.line(-18, 4, 18, 14)],
                                  [sk.line(-18, -10, 18, -10), sk.line(-18, 9, 18, 9)], "//  parallel", (0, 13, 0)))]),
    "Rechtwinklig": _drawing([("Zwei Linien anklicken – rechter Winkel", 14,
                               _morph([sk.line(-18, -10, 18, -10), sk.line(-10, -10, 8, 14)],
                                      [sk.line(-18, -10, 18, -10), sk.line(-10, -10, -10, 14)], "⊥  rechtwinklig",
                                      (-4, 4, 0)))]),
    "Gleich": _drawing([("Zwei Elemente anklicken – gleich groß", 14,
                         _morph([sk.circle(-12, 0, 4), sk.circle(12, 0, 9)], [sk.circle(-12, 0, 6.5), sk.circle(12, 0, 6.5)],
                                "=  gleich", (0, 10, 0)))]),
    "Tangential": _drawing([("Linie und Kreis anklicken – sie berühren sich", 14,
                             _morph([sk.circle(0, 0, 8), sk.line(-20, 14, 20, 14)],
                                    [sk.circle(0, 0, 8), sk.line(-20, 8, 20, 8)], "T  tangential", (12, 11, 0)))]),
    "Punkt auf Kurve": _drawing([("Punkt und Kurve anklicken", 14,
                                  _morph([sk.circle(0, 0, 10), sk.point(14, 9)], [sk.circle(0, 0, 10), sk.point(7.07, 7.07)],
                                         "×  auf Kurve", (12, 12, 0)))]),
    "Deckungsgleich": _drawing([("Zwei Punkte anklicken – sie liegen aufeinander", 14,
                                 _morph([sk.line(-18, -8, -2, 4), sk.line(6, 8, 18, -8)],
                                        [sk.line(-18, -8, 2, 6), sk.line(2, 6, 18, -8)], "●  deckungsgleich", (2, 10, 0)))]),
    "Mittelpunkt": _drawing([("Punkt und Linie anklicken – Punkt auf die Mitte", 14,
                              _morph([sk.line(-18, -8, 18, -8), sk.line(8, -8, 8, 12)],
                                     [sk.line(-18, -8, 18, -8), sk.line(0, -8, 0, 12)], "⊿  Mitte", (4, -4, 0)))]),
    "Kollinear": _drawing([("Zwei Linien anklicken – auf einer Geraden", 14,
                            _morph([sk.line(-20, -6, -4, -6), sk.line(4, 6, 20, 6)],
                                   [sk.line(-20, 0, -4, 0), sk.line(4, 0, 20, 0)], "⋯  kollinear", (0, 4, 0)))]),
    "Konzentrisch": _drawing([("Zwei Kreise anklicken – gleiche Mitte", 14,
                               _morph([sk.circle(-6, -3, 6), sk.circle(6, 4, 12)], [sk.circle(0, 0, 6), sk.circle(0, 0, 12)],
                                      "◎  konzentrisch", (0, 15, 0)))]),
    "Symmetrisch": _drawing([("Zwei Elemente und die Achse anklicken", 14, lambda t: (
                                 [sk.line(0, -16, 0, 16), sk.circle(-10, 4, 4), _lerp(sk.circle(12, -6, 6),
                                                                                       sk.circle(10, 4, 4), t)],
                                 None, [((0, 18, 0), "sym  symmetrisch", t > 0.5, t >= 1)], {"construction": [0]}))]),
    "Krümmung": _drawing([("Kurve und Bogen anklicken – Übergang ohne Knick (G2)", 14,
                           _morph([sk.arc(-6, 0, 10, 180, 270), sk.spline((-6, -10), (8, -2), (18, 12))],
                                  [sk.arc(-6, 0, 10, 180, 270), sk.spline((-6, -10), (6, -9), (18, 6))],
                                  "G2  Krümmung", (0, -14, 0)))]),
    "Fixieren": _drawing([("Element anklicken – es bleibt, wo es ist", 8, lambda t: (
                              _SQUARE, (-18, 0, 0), [((-22, 0, 0), "fix", t > 0.5, True)]))]),
    "Löschen": _drawing([("Elemente wählen, Entf drücken", 6, lambda t: (
                             [*_SQUARE, sk.circle(0, 0, 5)], (5, 0, 0), None)),
                         ("Sie sind weg", 2, lambda t: (_SQUARE, (5, 0, 0), None))]),
    "SKIZZE FERTIG STELLEN": _drawing([("Zeichnen", 8, lambda t: (sk.rectangle(-18, -12, -18 + 36 * t, 12),
                                                                 (-18 + 36 * t, 12, 0), None))],
                                      final="Fertig stellen – die Profile sind bereit"),
}


def _through(first, second, third) -> tuple:
    found = sk.circle_through(first, second, third)
    return found if found else (0.0, 0.0, 1.0)


def _tangent_two(radius: float) -> tuple:
    """Der Kreis im Inneren des Winkels (rechts vom Scheitel, zwischen beiden Linien) – nicht eine der Lösungen
    außerhalb."""
    found = sk.tangent_circles_two(_ANGLE_LINES[0], _ANGLE_LINES[1], radius)
    inside = [c for c in found if c[1] > -10 and c[0] > -20]
    return max(inside or found, key=lambda c: c[0]) if found else (0.0, 0.0, radius)


def _tangent_three() -> tuple:
    """Der Inkreis des Dreiecks (kleinste Lösung) – die übrigen Lösungen berühren die Verlängerungen außen."""
    found = sk.tangent_circles_three([sk.line(-20, -12, 20, -12), sk.line(-20, -12, 0, 16), sk.line(20, -12, 0, 16)])
    return min(found, key=lambda c: c[2]) if found else (0.0, 0.0, 1.0)


def _spline_points(t: float) -> list:
    """Spline, der Punkt für Punkt wächst (immer mindestens zwei Punkte)."""
    points = [(-22.0, -8.0), (-10.0, 8.0), (2.0, -6.0), (14.0, 10.0), (22.0, -2.0)]
    count = 2 + int(3 * t)
    head = points[:count]
    if count < len(points):
        nxt, last = points[count], head[-1]
        part = 3 * t - int(3 * t)
        head = [*head[:-1], last] + [(last[0] + (nxt[0] - last[0]) * part, last[1] + (nxt[1] - last[1]) * part)]
    return head


def _control_points(t: float) -> list:
    points = [(-22.0, -8.0), (-12.0, 12.0), (0.0, -12.0), (12.0, 12.0), (22.0, -8.0)]
    count = 4 + int(t >= 0.5)
    return points[:count] if count >= 4 else points[:4]


# --- Prüfen und Ansicht (Werkzeuge ohne eigenen Timeline-Schritt) -------------------------------------------------

def _two_blocks(w) -> list[str]:
    """Platte mit Bohrung und ein Klotz, der in sie hineinragt (Kollision, Messen, Schwerpunkt …)."""
    _add(w, solids.Box(length=50, width=30, height=8, operation=NEW))
    _add(w, solids.Box(center_x=18, length=14, width=14, height=20, offset=4, operation=NEW))
    return list(w.timeline.state.bodies)


def _info_at(rec: Recorder, at: tuple) -> None:
    """Der Infotext oben links liegt außerhalb des aufgenommenen Ausschnitts: im Clip steht er als Maßfelder am
    Modellpunkt `at` (je Zeile ein Feld, untereinander)."""
    lines = [line for line in rec.view._info.text().splitlines() if line.strip()]
    # untereinander in der Höhe versetzt (die y-Achse läuft in der Ansicht nach hinten, dort überlappen die Felder)
    rec.view.set_hud([((at[0], at[1], at[2] - 7.0 * number), line, number == 0, False)
                      for number, line in enumerate(lines[:3])])


def schnitt(rec: Recorder, w) -> None:
    rec.look((0, 0, 4), 120, -35, 30)
    _two_blocks(w)
    _add(w, solids.Hole(position_x=-10.0, diameter=10.0, frame=[], offset=8.0))
    rec.caption = "1  Schnitt einschalten"
    rec.move_to((0, 0, 8)); rec.click()
    w._section_action.setChecked(True)
    w._section_clicked()                # aus → X: die Ebene steht in der Mitte
    rec.shot(hold=6)
    rec.caption = "2  Schnittebene am Pfeil ziehen"
    rec.animate(20, lambda t: (w._section.update(offset=-22.0 + 30.0 * t), w._apply_section(), w._update_handle()),
                lambda t: (-22.0 + 30.0 * t, -15, 10))
    rec.caption = "Nochmal klicken: Achse wechseln (X → Y → Z → aus)"
    rec.cursor = None
    rec.shot(hold=14)
    w._section_action.setChecked(False)
    w._apply_section()


def schwerpunkt(rec: Recorder, w) -> None:
    rec.look((0, 0, 6), 110)
    _two_blocks(w)
    rec.caption = "1  Körper wählen (ohne Auswahl: alle)"
    rec.move_to((0, -15, 8)); rec.click()
    rec.caption = "2  Schwerpunkt: Kreuz und Lage"
    w._show_centre_of_mass()
    _info_at(rec, (-12, -10, 30))
    rec.cursor = None
    rec.shot(hold=22)
    rec.view.set_hud([])


def analyse(rec: Recorder, w) -> None:
    yaw = [-50.0]
    rec.look((0, 0, 4), 100, yaw[0], 35)
    _add(w, solids.Box(length=40, width=30, height=12, operation=NEW))
    _add(w, ft.Fillet(radius=6.0, edge_points=_top_edges(w, 12.0)))
    for kind, value, caption in (("Zebra", 10.0, "Zebra: Streifen zeigen glatte Übergänge"),
                                 ("Krümmung", 5.0, "Krümmung: enge Radien farbig"),
                                 ("Entformung", 1.0, "Entformung: Flächen ohne Schräge markiert")):
        rec.caption = caption
        w._start_analysis(kind, value)
        for _frame in range(14):        # die Ansicht dreht langsam weiter: Streifen und Farben wandern mit
            yaw[0] += 2.0
            rec.look((0, 0, 4), 100, yaw[0], 35)
            rec.shot()
    w._start_analysis(None)
    rec.caption = "„Analyse aus“ schaltet zurück"
    rec.shot(hold=8)


def bewegen(rec: Recorder, w) -> None:
    from PySide6.QtTest import QTest
    rec.look((10, 0, 8), 140)
    values = _joint_setup(w)
    _add(w, solids.Joint(body=values["pin"], target=values["base"], face_points=values["points"],
                         kinds=["face", "face"], joint=solids.JOINT_KINDS[2], minimum=-12.0, maximum=12.0))
    rec.caption = "1  Gelenk anlegen (Schieber oder Drehgelenk)"
    rec.shot(hold=8)
    rec.caption = "2  Bewegen: die Gelenke laufen durch"
    w._motion_action.setChecked(True)
    w._toggle_motion(True)
    for _frame in range(40):
        QTest.qWait(60)
        rec.shot()
    w._motion_action.setChecked(False)
    w._toggle_motion(False)
    rec.caption = "Nochmal klicken hält an"
    rec.shot(hold=8)


def messen(rec: Recorder, w) -> None:
    rec.look((0, 0, 8), 120)
    plate, block = _two_blocks(w)
    top = max(range(len(ft.faces_of(w.timeline.state.bodies[block]))),
              key=lambda i: ft.face_point(ft.faces_of(w.timeline.state.bodies[block])[i])[2])
    base = max(range(len(ft.faces_of(w.timeline.state.bodies[plate]))),
               key=lambda i: ft.face_point(ft.faces_of(w.timeline.state.bodies[plate])[i])[2])
    w._measure.setChecked(True)
    rec.caption = "1  Messen einschalten, erstes Element anklicken"
    rec.move_to((-10, 0, 8)); rec.click()
    w._selection = [("face", plate, base)]
    w._show_selection()
    rec.shot(hold=8)
    rec.caption = "2  Zweites Element: Abstand wird angezeigt"
    rec.move_to((18, 0, 24)); rec.click()
    w._selection = [("face", plate, base), ("face", block, top)]
    w._show_selection()
    _info_at(rec, (-14, -8, 30))
    rec.cursor = None
    rec.shot(hold=20)
    rec.view.set_hud([])
    w._measure.setChecked(False)


def kollision(rec: Recorder, w) -> None:
    rec.look((0, 0, 8), 120)
    _two_blocks(w)
    rec.caption = "1  Kollision anklicken"
    rec.move_to((18, 0, 24)); rec.click()
    rec.caption = "2  Meldung: welche Körper sich durchdringen"
    w.viewport.show_info(w._collision_text().replace("; ", "\n"))
    _info_at(rec, (-10, -10, 32))
    rec.cursor = None
    rec.shot(hold=22)
    rec.view.set_hud([])
    w.viewport.show_info("")


def komponentenfarben(rec: Recorder, w) -> None:
    rec.look((10, 0, 8), 140)
    values = _joint_setup(w)
    _add(w, solids.Component(body=values["base"], component="Grundplatte"))
    _add(w, solids.Component(body=values["pin"], component="Stift"))
    rec.caption = "Komponentenfarben einschalten"
    rec.move_to((0, 0, 8)); rec.click()
    rec.shot(hold=4)
    w._component_colour_action.setChecked(True)
    w._refresh()
    rec.caption = "Jede Komponente erscheint in eigener Farbe"
    rec.cursor = None
    rec.shot(hold=20)
    w._component_colour_action.setChecked(False)


def koerper_kopieren(rec: Recorder, w) -> None:
    """Kopieren schreibt auch in die Zwischenablage-Datei des Nutzers – die Szene arbeitet mit einer eigenen Datei,
    damit dessen kopierte Körper erhalten bleiben."""
    import tempfile
    from . import main_window as mw
    saved = mw._BODY_CLIPBOARD_FILE
    mw._BODY_CLIPBOARD_FILE = Path(tempfile.gettempdir()) / "wolfcad_clip_zwischenablage.json"
    try:
        _copy_paste_scene(rec, w)
    finally:
        mw._BODY_CLIPBOARD_FILE.unlink(missing_ok=True)
        mw._BODY_CLIPBOARD_FILE = saved


def _copy_paste_scene(rec: Recorder, w) -> None:
    rec.look((20, 0, 6), 150)
    _add(w, solids.Box(length=26, width=20, height=10, operation=NEW))
    _add(w, solids.Cylinder(diameter=10, height=18, operation="Vereinigen"))
    body = _first(w)
    rec.caption = "1  Körper wählen, Strg+C (Körper kopieren)"
    rec.move_to((0, 0, 18)); rec.click()
    w._copy_bodies([body])
    rec.shot(hold=6)
    rec.caption = "2  Strg+V (Körper einfügen): Kopie an den Pfeilen verschieben"
    w._paste_bodies()
    index = len(w.timeline.features) - 1
    names = [h.parameter for h in w._handles]
    field = "move_x" if "move_x" in names else None
    if field:
        rec.animate(16, lambda t: _set(w, index, move_x=36.0 * t), lambda t: (36.0 * t, 0, 18))
    else:
        rec.shot(hold=12)
    _finish(rec, "3  Eingabe – Kopie steht")


# --- Physik (Runde 83) ---------------------------------------------------------------------------------------------

def _orbit(rec: Recorder, frames: int, start: float = -35.0, sweep: float = 30.0, target=(0, 0, 5), distance=150,
           pitch: float = 28.0) -> None:
    """Die Kamera dreht langsam um das Ergebnis (so wird ein stehendes Ergebnisbild zum Video)."""
    for number in range(frames):
        rec.look(target, distance, start + sweep * number / max(frames - 1, 1), pitch)
        rec.shot()


def _beam(w) -> tuple[str, int, int, int]:
    """Kragbalken 100 × 10 × 10 mm (Aluminium) mit linker, rechter und oberer Fläche."""
    _add(w, solids.Box(center_x=50, length=100, width=10, height=10, operation=NEW))
    body = _first(w)
    w._set_material(body, "Aluminium (2,70 g/cm³)")
    w._refresh()
    points = w._picks[body].face_points
    left = min(range(len(points)), key=lambda i: points[i][0])
    right = max(range(len(points)), key=lambda i: points[i][0])
    top = max(range(len(points)), key=lambda i: points[i][2])
    return body, left, right, top


def _face_point_of(w, body: str, index: int) -> tuple:
    return tuple(w._picks[body].face_points[index])


def physik_lager(rec: Recorder, w) -> None:
    rec.look((50, 0, 5), 150)
    body, left, _right, _top = _beam(w)
    rec.caption = "1  Fläche anklicken, an der das Teil gehalten wird"
    rec.move_to(_face_point_of(w, body, left)); rec.click()
    rec.caption = "2  Lager – die Fläche wird grün"
    w._physics.add_support((body, [list(_face_point_of(w, body, left))]))
    rec.shot(hold=8)
    _orbit(rec, 14, target=(50, 0, 5))


def _loaded_beam(rec: Recorder, w):
    body, left, right, top = _beam(w)
    w._physics.add_support((body, [list(_face_point_of(w, body, left))]))
    return body, left, right, top


def physik_kraft(rec: Recorder, w) -> None:
    rec.look((50, 0, 5), 150)
    body, _left, right, _top = _loaded_beam(rec, w)
    rec.caption = "1  Fläche anklicken, auf die die Kraft wirkt"
    rec.move_to(_face_point_of(w, body, right)); rec.click()
    rec.caption = "2  Kraft: Wert in N und Richtung – Pfeil zeigt sie"
    w._physics.add_force((body, [list(_face_point_of(w, body, right))]), 100.0, "−Z (nach unten, Gewicht)")
    rec.shot(hold=8)
    _orbit(rec, 14, target=(50, 0, 5))


def physik_druck(rec: Recorder, w) -> None:
    rec.look((50, 0, 5), 150, -35, 40)
    body, _left, _right, top = _loaded_beam(rec, w)
    rec.caption = "1  Fläche anklicken"
    rec.move_to(_face_point_of(w, body, top)); rec.click()
    rec.caption = "2  Druck in MPa – wirkt senkrecht auf die Fläche"
    w._physics.add_pressure((body, [list(_face_point_of(w, body, top))]), 0.5)
    rec.shot(hold=8)
    _orbit(rec, 14, target=(50, 0, 5), pitch=40)


def physik_lasten_entfernen(rec: Recorder, w) -> None:
    rec.look((50, 0, 5), 150)
    body, _left, right, _top = _loaded_beam(rec, w)
    w._physics.add_force((body, [list(_face_point_of(w, body, right))]), 100.0, "−Z (nach unten, Gewicht)")
    rec.caption = "Lager (grün) und Lasten (orange) gesetzt"
    rec.shot(hold=10)
    rec.caption = "Lasten entfernen – alles ist wieder frei"
    w._physics.clear_loads()
    rec.shot(hold=8)
    _orbit(rec, 10, target=(50, 0, 5))


def _stress_result(w):
    body, _left, right, _top = _loaded_beam(None, w)
    w._physics.add_force((body, [list(_face_point_of(w, body, right))]), 100.0, "−Z (nach unten, Gewicht)")
    shape = w.timeline.state.bodies[body]
    setup = w._physics.setup()
    result = physics.static_stress(shape, physics.MATERIALS["Aluminium"], setup["lager"][0]["points"],
                                   [physics.Load(physics.FORCE, setup["lasten"][0]["points"], 100.0, (0, 0, -1))])
    return body, shape, result


def physik_festigkeit(rec: Recorder, w) -> None:
    rec.look((50, 0, 5), 150)
    body, shape, result = _stress_result(w)
    rec.caption = "1  Lager und Lasten setzen, Werkstoff wählen"
    rec.shot(hold=8)
    rec.caption = "2  Festigkeit: blau entspannt, rot hoch belastet"
    w._physics._show_stress(body, shape, result)
    rec.shot(hold=4)
    _orbit(rec, 20, target=(50, 0, 3))
    rec.caption = "Verformung überhöht – Legende nennt Spannung und Sicherheit"
    rec.shot(hold=10)


def _flow_scene(fluid: str):
    def run(rec: Recorder, w) -> None:
        rec.look((0, 0, 8), 140)
        # Körper mit runder Nase: Quader, vorn eine Kugel gleicher Höhe (eine einfache Stromlinienform)
        _add(w, solids.Box(length=40, width=14, height=14, offset=1, operation=NEW))
        _add(w, solids.Sphere(center_x=-20, center_z=8, diameter=14, operation="Vereinigen"))
        body = _first(w)
        shape = w.timeline.state.bodies[body]
        rec.caption = f"1  Körper wählen, {fluid}strömung starten"
        rec.move_to((0, 0, 14)); rec.click()
        result = physics.flow(shape, "+X", 20.0 if fluid == "Luft" else 2.0, fluid, 7)
        rec.caption = "2  Druck: rot Staudruck, blau Sog – Linien folgen der Strömung"
        w._physics._show_flow(body, shape, result)
        rec.cursor = None
        _orbit(rec, 24, start=-60, sweep=50, target=(10, 0, 8), distance=180)
        rec.caption = "Widerstand (cw) und Kraft stehen in der Legende"
        rec.shot(hold=10)
    return run


def physik_schwimmen(rec: Recorder, w) -> None:
    rec.look((0, 0, 10), 150, -35, 22)
    # geschlossener Hohlkörper (Boot mit Deck) aus PLA: schwimmt
    _add(w, solids.Box(length=60, width=30, height=20, operation=NEW))
    body = _first(w)
    _add(w, solids.Shell(body=body, thickness=1.5, face_points=[]))
    w._set_material(body, "PLA (1,24 g/cm³)")
    shape = w.timeline.state.bodies[body]
    rec.caption = "1  Körper wählen, Schwimmen starten"
    rec.move_to((0, 0, 20)); rec.click()
    result = physics.buoyancy(shape, physics.MATERIALS["PLA"].density)
    rec.caption = "2  Wasserspiegel: so tief taucht er ein"
    w._physics._show_buoyancy(body, shape, physics.MATERIALS["PLA"], result)
    rec.cursor = None
    _orbit(rec, 22, start=-50, sweep=40, target=(0, 0, 8), distance=170, pitch=18)
    rec.caption = "Legende: Eintauchtiefe und Zuladung"
    rec.shot(hold=10)


def physik_ergebnis_aus(rec: Recorder, w) -> None:
    rec.look((50, 0, 5), 150)
    body, shape, result = _stress_result(w)
    w._physics._show_stress(body, shape, result)
    rec.caption = "Ergebnis wird gezeigt"
    _orbit(rec, 10, target=(50, 0, 3))
    rec.caption = "Ergebnis aus – der Körper erscheint wieder normal"
    w._physics.clear_result()
    rec.shot(hold=12)


def physik_ansicht(rec: Recorder, w) -> None:
    """Physikansicht aus- und wieder einschalten (Runde 86)."""
    rec.look((50, 0, 5), 150)
    body, shape, result = _stress_result(w)
    w._physics._show_stress(body, shape, result)
    rec.caption = "Ergebnis, Lager und Lasten sind zu sehen"
    _orbit(rec, 8, target=(50, 0, 3))
    rec.caption = "Physikansicht aus – nur der Körper"
    w._physics.set_visible(False)
    _orbit(rec, 8, start=-27, target=(50, 0, 3))
    rec.caption = "Wieder an – nichts ist verloren"
    w._physics.set_visible(True)
    _orbit(rec, 8, start=-19, target=(50, 0, 3))


def physik_live(rec: Recorder, w) -> None:
    """Live neu rechnen: Balken verlängern, die Festigkeit folgt (Rechnung hier im Vordergrund, wie das Ergebnis)."""
    rec.look((60, 0, 5), 170)
    body, shape, result = _stress_result(w)
    w._physics._remember_rerun(None, None)
    w._physics._show_stress(body, shape, result)
    rec.caption = "Festigkeit gerechnet"
    _orbit(rec, 8, target=(60, 0, 3), distance=170)
    rec.caption = "Körper ändern (länger) – die Lasten wandern mit"
    for length in (110.0, 120.0):
        _set(w, 0, length=length, center_x=length / 2)
        rec.shot(hold=3)
    w._physics.follow_changes()
    shape = w.timeline.state.bodies[body]
    setup = w._physics.setup()
    again = physics.static_stress(shape, physics.MATERIALS["Aluminium"], setup["lager"][0]["points"],
                                  [physics.Load(physics.FORCE, setup["lasten"][0]["points"], 100.0, (0, 0, -1))])
    rec.caption = "Live: das Ergebnis wird von selbst neu gerechnet"
    w._physics._show_stress(body, shape, again)
    _orbit(rec, 12, target=(60, 0, 3), distance=170)


PHYSICS_MORE = {"Lager": physik_lager, "Kraft": physik_kraft, "Druck": physik_druck,
                "Lasten entfernen": physik_lasten_entfernen, "Festigkeit": physik_festigkeit,
                "Luftströmung": _flow_scene("Luft"), "Wasserströmung": _flow_scene("Wasser"),
                "Schwimmen": physik_schwimmen, "Ergebnis aus": physik_ergebnis_aus,
                "Physikansicht": physik_ansicht, "Live neu rechnen": physik_live}


INSPECT_MORE = {"Schnitt": schnitt, "Schwerpunkt": schwerpunkt, "Analyse": analyse, "Bewegen": bewegen,
                "Messen": messen, "Kollision": kollision, "Komponentenfarben": komponentenfarben,
                "Körper kopieren": koerper_kopieren, "Körper einfügen": koerper_kopieren}


MORE_SCENES = {**{(MODEL, tool): scene for tool, scene in {**MODEL_MORE, **INSPECT_MORE, **PHYSICS_MORE}.items()},
               **{(SKETCH, tool): scene for tool, scene in SKETCH_MORE.items()}}
SCENES.update(MORE_SCENES)
