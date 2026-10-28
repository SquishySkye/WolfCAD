# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Runde 76 (Nutzerbilder/-video 2026-10-26): Stege in Skizzenprofilen, keine Nähte nach dem Vereinigen, kaputte
Rundungen werden abgelehnt, Kombinieren mit mehreren Werkzeugkörpern, getrennte Stücke werden eigene Körper,
Drücken/Ziehen an mehreren und an gekrümmten Flächen, ältere Entwürfe rechnen wie bisher."""
import pytest
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Circle

from wolfcad.core import features as ft
from wolfcad.core import sketch as sk
from wolfcad.core import solids
from wolfcad.core.timeline import GEOMETRY_LEVEL, Timeline


def ring_with_bridge() -> list:
    """Rahmen (außen 32 × 22, innen 28 × 18) mit einem Steg vom äußeren zum inneren Rand – wie im Nutzerentwurf."""
    return [*sk.rectangle(-22, -36, 10, -14), *sk.rectangle(-20, -34, 8, -16), sk.line(-22, -25, -20, -25)]


def test_bridge_line_does_not_slit_the_profile():
    # Der Steg teilt den Rahmen nicht: das Profil ist ein Rahmen ohne Schlitz, die Extrusion ein sauberer Körper
    # (vorher: ungültiger Körper mit 13 statt 10 Flächen und sichtbarer Linie)
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=ring_with_bridge()))
    timeline.add(ft.Extrude(name="E", sketch="S", distance=7.5, regions=ft.PROFILE_MODES[2],
                            region_points=[[-21.0, -30.0, 0.0]]))
    assert not timeline.errors
    shape = next(iter(timeline.state.bodies.values()))
    assert ft.is_valid(shape) and len(ft.faces_of(shape)) == 10
    assert solids._volume(shape) == pytest.approx((32 * 22 - 28 * 18) * 7.5)


def test_old_designs_keep_their_geometry():
    # Ältere Dateien (ohne „geometrie“) rechnen wie damals – ihre Schritte beziehen sich auf die damaligen Flächen
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=ring_with_bridge()))
    timeline.add(ft.Extrude(name="E", sketch="S", distance=7.5, regions=ft.PROFILE_MODES[2],
                            region_points=[[-21.0, -30.0, 0.0]]))
    document = timeline.to_document()
    assert document["geometrie"] == GEOMETRY_LEVEL       # neue Entwürfe: aktuelle Stufe (seit Runde 78: 3)
    del document["geometrie"]
    old = Timeline.from_document(document)
    assert old.geometry_level == 1
    assert len(ft.faces_of(next(iter(old.state.bodies.values())))) == 13


def test_combined_bodies_have_no_seams():
    # zwei bündig aneinanderliegende Quader, mit Kombinieren vereinigt: ein Quader mit 6 Flächen
    timeline = Timeline()
    timeline.add(solids.Box(name="A", length=40, width=20, height=10))
    timeline.add(solids.Box(name="B", center_x=40, length=40, width=20, height=10, operation="Neuer Körper"))
    target, tool = list(timeline.state.bodies)
    timeline.add(solids.CombineBodies(name="K", target=target, tools=[tool]))
    assert not timeline.errors
    assert len(ft.faces_of(timeline.state.bodies[target])) == 6


def test_combine_with_several_tools():
    timeline = Timeline()
    for index, x in enumerate((0, 30, 60)):
        timeline.add(solids.Box(name=f"Q{index}", center_x=x, length=20, width=20, height=10,
                                operation="Neuer Körper"))
    first, *others = list(timeline.state.bodies)
    timeline.add(solids.CombineBodies(name="K", target=first, tools=others))
    assert not timeline.errors
    assert list(timeline.state.bodies) == [first]
    assert solids._volume(timeline.state.bodies[first]) == pytest.approx(3 * 20 * 20 * 10)
    # ohne Werkzeug: Hinweis statt stiller Wirkungslosigkeit
    timeline.add(solids.CombineBodies(name="K2", target=first))
    assert "Werkzeugkörper" in timeline.errors[len(timeline.features) - 1]


def test_old_combine_with_single_tool_still_loads():
    feature = ft.feature_from_dict({"kind": "Kombinieren", "name": "K", "target": "Körper1", "tool": "Körper2"})
    assert feature.tool_names() == ["Körper2"]


def groove_cylinder() -> tuple[Timeline, str, list]:
    """Zylinder Ø 60 × 20 mit einer 4 mm breiten, 3 mm tiefen Rille ringsum; dazu die Merkpunkte ihrer beiden
    Innenkanten (Radius 27)."""
    timeline = Timeline()
    timeline.add(solids.Cylinder(name="Z", diameter=60, height=20))
    body = next(iter(timeline.state.bodies))
    # Ring zwischen Ø 54 und Ø 80 in 8 mm Höhe, 4 mm hoch herausgeschnitten
    timeline.add(sk.Sketch(name="S", plane="XY", offset=8.0, entities=[sk.circle(0, 0, 40), sk.circle(0, 0, 27)]))
    timeline.add(ft.Extrude(name="N", sketch="S", distance=4, operation="Ausschneiden",
                            regions=ft.PROFILE_MODES[2], region_points=[[33.0, 0.0, 8.0]]))
    assert not timeline.errors, timeline.errors
    shape = timeline.state.bodies[body]
    inner = [list(ft.edge_point(edge)) for edge in ft.unique_edges(shape)
             if BRepAdaptor_Curve(edge).GetType() == GeomAbs_Circle
             and abs(BRepAdaptor_Curve(edge).Circle().Radius() - 27) < 1e-6]
    assert len(inner) == 2
    return timeline, body, inner


def test_overlapping_fillets_in_a_groove_are_rejected():
    # Nutzerbild 5: zwei Rundungen in einer 4 mm breiten Rille mit je 2,5 mm überlappen sich. Fusion meldet einen
    # Fehler und ändert nichts – vorher entstand ein Dreiecksmurks mit einer dünnen Fläche in den Körper
    timeline, body, inner = groove_cylinder()
    before = solids._volume(timeline.state.bodies[body])
    timeline.add(ft.Fillet(name="R", body=body, radius=2.5, edge_points=inner))
    assert len(timeline.features) - 1 in timeline.errors
    assert solids._volume(timeline.state.bodies[body]) == pytest.approx(before)
    # passt der Radius (2 × 1,5 < 4), klappt es
    timeline.update(len(timeline.features) - 1, radius=1.5)
    assert not timeline.errors and ft.is_valid(timeline.state.bodies[body])


def test_split_off_floor_gives_separate_bodies():
    # Nutzerbild 2: Kasten mit Einbau – Boden abgetrennt: Wände und Einbau sind getrennte Stücke, also Körper
    timeline = Timeline()
    timeline.add(solids.Box(name="K", length=60, width=40, height=20))
    timeline.add(solids.Box(name="I", length=56, width=36, height=18, offset=2, operation="Ausschneiden"))
    timeline.add(solids.Box(name="E", center_x=-10, length=10, width=10, height=8, offset=2, operation="Vereinigen"))
    assert len(timeline.state.bodies) == 1
    timeline.add(solids.SplitBody(name="T", plane="XY", offset=2.0))
    assert not timeline.errors
    # oben: Wände + Einbau (getrennt), unten: der Boden
    assert len(timeline.state.bodies) == 3


def test_press_pull_several_faces_at_once():
    timeline = Timeline()
    timeline.add(solids.Box(name="Q", length=20, width=20, height=10))
    body = next(iter(timeline.state.bodies))
    # Oberseite und rechte Seite gleichzeitig um 2 mm herausziehen
    timeline.add(solids.PressPull(name="D", body=body, distance=2.0, face_points=[[0, 0, 10], [10, 0, 5]]))
    assert not timeline.errors
    assert solids._volume(timeline.state.bodies[body]) == pytest.approx(22 * 20 * 12)


def test_press_pull_hole_wall():
    # Bohrungswand: nach außen (in die Bohrung) gezogen wird das Loch enger, gedrückt weiter
    timeline = Timeline()
    timeline.add(solids.Box(name="Q", length=40, width=40, height=10))
    timeline.add(solids.Cylinder(name="L", diameter=10, height=10, operation="Ausschneiden"))
    body = next(iter(timeline.state.bodies))
    plate = 40 * 40 * 10
    timeline.add(solids.PressPull(name="D", body=body, distance=2.0, face_points=[[5.0, 0.0, 5.0]]))
    assert not timeline.errors, timeline.errors
    assert solids._volume(timeline.state.bodies[body]) == pytest.approx(plate - 3.141592653589793 * 9 * 10, rel=1e-4)
    timeline.update(len(timeline.features) - 1, distance=-2.0)
    assert not timeline.errors, timeline.errors
    assert solids._volume(timeline.state.bodies[body]) == pytest.approx(plate - 3.141592653589793 * 49 * 10, rel=1e-4)
    # zuziehen über die Achse hinaus geht nicht: Fehler, Körper bleibt
    timeline.update(len(timeline.features) - 1, distance=6.0)
    assert len(timeline.features) - 1 in timeline.errors


def test_press_pull_pocket_wall_of_a_complex_body():
    # Taschenwand in einem Körper mit Rundungen: der Versatz des Kerns am ganzen Körper scheitert hier gern –
    # der Weg über die Scheibe zwischen Fläche und Versatz trägt (Nutzerbild 4)
    timeline = Timeline()
    timeline.add(solids.Box(name="Q", length=80, width=80, height=20))
    body = next(iter(timeline.state.bodies))
    timeline.add(solids.Cylinder(name="T", diameter=60, height=12, offset=8, operation="Ausschneiden"))
    timeline.add(ft.Fillet(name="R", body=body, radius=3,
                           edge_points=[[40, 40, 10], [-40, 40, 10], [40, -40, 10], [-40, -40, 10]]))
    assert not timeline.errors, timeline.errors
    before = solids._volume(timeline.state.bodies[body])
    timeline.add(solids.PressPull(name="D", body=body, distance=5.0, face_points=[[30.0, 0.0, 14.0]]))
    assert not timeline.errors, timeline.errors
    grown = 3.141592653589793 * (30 ** 2 - 25 ** 2) * 12
    assert solids._volume(timeline.state.bodies[body]) - before == pytest.approx(grown, rel=1e-3)


def test_press_pull_across_former_seam_fills_the_corner():
    # Nutzervideo: Wand an einen Körper angesetzt, dann die gemeinsame Seite gezogen – die Ecke blieb offen, weil
    # die Seite an der Naht in zwei Flächen zerfiel und nur eine davon gezogen wurde. Jetzt ist es eine Fläche.
    timeline = Timeline()
    timeline.add(solids.Box(name="A", length=40, width=20, height=10))
    timeline.add(solids.Box(name="B", center_x=25, length=10, width=20, height=10, operation="Vereinigen"))
    body = next(iter(timeline.state.bodies))
    # Vorderseite (y = −10) über die ganze Breite (x −20 … 30) um 3 mm herausziehen
    timeline.add(solids.PressPull(name="D", body=body, distance=3.0, face_points=[[0.0, -10.0, 5.0]]))
    assert not timeline.errors, timeline.errors
    shape = timeline.state.bodies[body]
    assert len(ft.faces_of(shape)) == 6
    assert solids._volume(shape) == pytest.approx(50 * 23 * 10)


def test_press_pull_inner_corner_round_stays_plausible():
    # Tasche mit gerundeten Innenecken (r = 3): eine Eckrundung um 1 mm in die Tasche ziehen ändert das Volumen
    # nur um etwa Fläche × Abstand (vorher +780 mm³ über den Weg „umschlossene Form“)
    timeline = Timeline()
    timeline.add(solids.Box(name="Q", length=60, width=40, height=20))
    body = next(iter(timeline.state.bodies))
    timeline.add(solids.Box(name="T", length=40, width=20, height=15, offset=5, operation="Ausschneiden"))
    corners = [[20, 10, 12], [-20, 10, 12], [20, -10, 12], [-20, -10, 12]]
    timeline.add(ft.Fillet(name="R", body=body, radius=3, edge_points=corners))
    assert not timeline.errors, timeline.errors
    shape = timeline.state.bodies[body]
    face = min(ft.faces_of(shape), key=lambda f: sum((a - b) ** 2 for a, b in zip(ft.face_point(f), (19.1, 9.1, 12.5))))
    area = solids._face_area(face)
    before = solids._volume(shape)
    timeline.add(solids.PressPull(name="D", body=body, distance=1.0, face_points=[list(ft.face_point(face))]))
    assert not timeline.errors, timeline.errors
    change = solids._volume(timeline.state.bodies[body]) - before
    assert 0.4 * area <= change <= 2.5 * area
