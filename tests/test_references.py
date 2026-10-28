# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Wie in Fusion: Änderungen weiter vorn in der Timeline wirken auf spätere Schritte – Flächen, Kanten und Skizzen
folgen; projizierte Umrisse werden neu projiziert. Dazu: Abrunden ohne Auswahl wartet, Spiegeln an einer Fläche."""
import pytest

from wolfcad.core import features as ft
from wolfcad.core import sketch as sk
from wolfcad.core import solids
from wolfcad.core.timeline import Timeline


def fresh() -> Timeline:
    timeline = Timeline()
    timeline.crash_guard = False
    return timeline


def bounds(timeline, name=None):
    shape = timeline.state.bodies[name] if name else next(iter(timeline.state.bodies.values()))
    return [round(c, 4) for c in solids._bounds(shape)]


def test_face_extrusion_follows_a_taller_box():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=20, width=20, height=10))
    timeline.add(ft.Extrude(name="E", face_points=[[0, 0, 10]], distance=5, operation="Vereinigen"))
    assert bounds(timeline)[5] == 15
    timeline.update(0, height=20)
    assert not timeline.errors and bounds(timeline)[5] == 25
    assert timeline.features[1].face_points[0][2] == pytest.approx(20)       # Merkpunkt nachgeführt
    timeline.update(0, height=30)                                            # und ein zweites Mal
    assert not timeline.errors and bounds(timeline)[5] == 35


def top_sketch(timeline, entities, projected=()):
    face = next(f for f in ft.faces_of(next(iter(timeline.state.bodies.values())))
                if ft.face_point(f)[2] == pytest.approx(10))
    frame = sk.face_frame(face)
    feature = sk.Sketch(name="S", frame=frame, entities=entities, face_ref=list(ft.face_point(face)))
    if projected:
        axes = sk.frame_axes("XY", 0.0, frame)
        outline = sk.project_edges(ft.unique_edges(face), axes)
        feature.entities = outline + entities
        feature.projected = list(range(len(outline)))
        feature.constraints = [[__import__("wolfcad.core.constraints", fromlist=["FIX"]).FIX, i, -1, 0.0]
                               for i in range(len(outline))]
    timeline.add(feature)
    return feature


def test_sketch_on_face_follows_the_face():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=40, width=40, height=10))
    top_sketch(timeline, [sk.circle(0, 0, 5)])
    timeline.add(ft.Extrude(name="E", sketch="S", distance=5, operation="Vereinigen"))
    assert bounds(timeline)[5] == 15
    timeline.update(0, height=25)
    assert not timeline.errors and bounds(timeline)[5] == 30


def test_projected_outline_is_reprojected():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=40, width=40, height=10))
    feature = top_sketch(timeline, [], projected=True)
    timeline.add(ft.Extrude(name="E", sketch="S", distance=5, operation="Neuer Körper"))
    timeline.update(0, length=60)                    # die Fläche wird breiter: der Umriss muss mitwachsen
    assert not timeline.errors
    xs = sorted({round(value, 4) for entity in timeline.features[1].entities for value in (entity[1], entity[3])})
    assert xs[0] - xs[-1] == pytest.approx(-60)
    new = [name for name in timeline.state.bodies if name != "Körper1"][0]
    assert bounds(timeline, new)[3] - bounds(timeline, new)[0] == pytest.approx(60)


def test_fillet_waits_for_a_pick():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=20, width=20, height=10))
    before = solids._volume(next(iter(timeline.state.bodies.values())))
    timeline.add(ft.Fillet(name="R", radius=2, require_pick=True))
    assert timeline.errors[1] == ft.PICK_EDGES_MESSAGE
    assert solids._volume(next(iter(timeline.state.bodies.values()))) == pytest.approx(before)
    # ältere Entwürfe (ohne das Feld) runden ohne Auswahl weiterhin alle Kanten
    timeline.update(1, require_pick=False)
    assert not timeline.errors


def test_mirror_at_a_face():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", center_x=10, length=20, width=10, height=10))
    body = next(iter(timeline.state.bodies))
    face = next(f for f in ft.faces_of(timeline.state.bodies[body]) if ft.face_point(f)[0] == pytest.approx(20))
    timeline.add(solids.MirrorBody(name="M", body=body, frame=sk.face_frame(face)))
    assert not timeline.errors
    lows = sorted(round(solids._bounds(shape)[0], 4) for shape in timeline.state.bodies.values())
    assert lows == [0, 20]


def test_outline_is_reprojected_when_the_face_changes_shape():
    # die Oberseite bekommt durch eine Rundung mehr Kanten: der Umriss wird ganz neu projiziert
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=40, width=40, height=10))
    feature = top_sketch(timeline, [sk.circle(0, 0, 5)], projected=True)
    assert len(feature.projected) == 4
    body = next(iter(timeline.state.bodies))
    timeline.features.insert(1, ft.Fillet(name="R", body=body, radius=4, edge_points=[
        [20, 20, 5], [-20, 20, 5], [20, -20, 5], [-20, -20, 5]]))
    timeline.marker += 1
    timeline.recompute(0)
    assert not timeline.errors
    sketch_feature = timeline.features[2]
    assert len(sketch_feature.projected) == 8          # vier Geraden und vier Bögen
    assert sum(1 for e in sketch_feature.entities if e[0] == sk.circle(0, 0, 1)[0]) == 1    # der Kreis bleibt


def test_old_sketch_without_face_ref_finds_its_face():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=40, width=40, height=10))
    feature = top_sketch(timeline, [], projected=True)
    feature.face_ref = []                               # wie in einer Datei aus Runde 62
    timeline.recompute(0)
    assert len(feature.face_ref) == 3
    timeline.update(0, height=20)
    assert feature.frame[2] == pytest.approx(20)


def test_delete_face_refuses_an_outer_face_and_removes_a_fillet():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=40, width=40, height=10))
    body = next(iter(timeline.state.bodies))
    timeline.add(solids.DeleteFace(name="L", body=body, face_points=[[0, 0, 10]]))
    assert "nicht entfernen" in timeline.errors[1]                 # Oberseite: der Körper bliebe offen
    timeline.remove(1)
    timeline.add(ft.Fillet(name="R", body=body, radius=4, edge_points=[[20, 0, 10]]))
    face = next(f for f in ft.faces_of(timeline.state.bodies[body])
                if abs(ft.face_point(f)[0] - 18.83) < 0.5 and abs(ft.face_point(f)[2] - 8.83) < 0.5)
    timeline.add(solids.DeleteFace(name="L2", body=body, face_points=[list(ft.face_point(face))]))
    assert not timeline.errors
    assert solids._volume(timeline.state.bodies[body]) == pytest.approx(40 * 40 * 10)


def test_remove_sketch_keeps_what_was_built_from_it():
    timeline = fresh()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=[sk.circle(0, 0, 5)]))
    timeline.add(ft.Extrude(name="E", sketch="S", distance=10, operation="Neuer Körper"))
    before = solids._volume(next(iter(timeline.state.bodies.values())))
    timeline.add(solids.RemoveSketch(name="X", sketch="S"))
    assert not timeline.errors
    assert "S" not in timeline.state.frames and "S" not in timeline.state.profiles
    assert solids._volume(next(iter(timeline.state.bodies.values()))) == pytest.approx(before)



def test_removed_sketch_disappears_from_the_view():
    timeline = fresh()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=[sk.circle(0, 0, 5), sk.line(10, 0, 20, 0),
                                                           sk.line(10, 5, 20, 5)], construction=[2]))
    timeline.add(sk.Sketch(name="T", plane="XY", entities=[sk.circle(30, 0, 5)]))
    assert len(timeline.state.sketch_curves) == 2 and len(timeline.state.construction_curves) == 1
    timeline.add(solids.RemoveSketch(name="X", sketch="S"))
    assert len(timeline.state.sketch_curves) == 1 and not timeline.state.construction_curves
    assert "T" in timeline.state.curves                  # die andere Skizze bleibt sichtbar


def test_reprojection_keeps_constraints_on_surviving_edges():
    """Bekommt die Fläche mehr Kanten, bleiben die übrigen projizierten Linien erhalten – samt der Abhängigkeiten,
    die gezeichnete Elemente daran haben (wie in Fusion); nur die neuen Bögen kommen dazu."""
    from wolfcad.core import constraints as cs
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=40, width=40, height=10))
    drawn = sk.line(10, -10, 10, 10)
    feature = top_sketch(timeline, [drawn], projected=True)
    right = next(i for i in feature.projected
                 if abs(feature.entities[i][1] - 20) < 1e-6 and abs(feature.entities[i][3] - 20) < 1e-6)
    line = len(feature.entities) - 1
    feature.constraints.append([cs.COLLINEAR, line, right, 0.0])     # gezeichnete Linie auf der rechten Kante
    body = next(iter(timeline.state.bodies))
    timeline.features.insert(1, ft.Fillet(name="R", body=body, radius=4, edge_points=[
        [20, 20, 5], [-20, 20, 5], [20, -20, 5], [-20, -20, 5]]))
    timeline.marker += 1
    timeline.recompute(0)
    assert not timeline.errors
    sketch_feature = timeline.features[2]
    assert len(sketch_feature.projected) == 8
    rule = next(r for r in sketch_feature.constraints if r[0] == cs.COLLINEAR)
    # die Abhängigkeit hängt weiter an der (jetzt kürzeren) rechten Kante, die gezeichnete Linie liegt darauf
    edge = sketch_feature.entities[rule[2]]
    assert rule[2] in sketch_feature.projected and edge[1] == pytest.approx(20) and edge[3] == pytest.approx(20)
    assert abs(edge[2] - edge[4]) == pytest.approx(32)
    moved = sketch_feature.entities[rule[1]]
    assert moved[1] == pytest.approx(20) and moved[3] == pytest.approx(20)
