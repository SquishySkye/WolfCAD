# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Verrundung und Fase bis an die Grenze wie in Fusion (Quader 20 × 20 mit Radius 10 → Zylinder), Skizzenecken bis
zur halben Seitenlänge (Quadrat → Kreis)."""
import math

import pytest

from wolfcad.core import features as ft
from wolfcad.core import sketch as sk
from wolfcad.core import solids
from wolfcad.core.timeline import Timeline

VERTICAL = [[10, 10, 5], [-10, 10, 5], [10, -10, 5], [-10, -10, 5]]


def build(feature) -> Timeline:
    timeline = Timeline()
    timeline.crash_guard = False
    timeline.add(solids.Box(name="Q", length=20, width=20, height=10))
    feature.body = next(iter(timeline.state.bodies))
    timeline.add(feature)
    return timeline


def test_fillet_to_cylinder():
    timeline = build(ft.Fillet(name="R", edge_points=VERTICAL, radius=10))
    assert not timeline.errors
    body = next(iter(timeline.state.bodies.values()))
    assert solids._volume(body) == pytest.approx(math.pi * 100 * 10, rel=1e-6)
    # wie in Fusion ein einziger Mantel: oben, unten und eine Zylinderfläche (nicht vier Teilstücke)
    assert len(ft.faces_of(body)) == 3


def test_full_round_slot_ends_are_one_face_each():
    """Langloch: beide Enden voll gerundet → je ein Halbzylinder (oben, unten, zwei Seiten, zwei Enden)."""
    timeline = Timeline()
    timeline.crash_guard = False
    timeline.add(solids.Box(name="Q", length=40, width=20, height=5))
    body = next(iter(timeline.state.bodies))
    timeline.add(ft.Fillet(name="R", body=body, radius=10,
                           edge_points=[[20, 10, 2.5], [20, -10, 2.5], [-20, 10, 2.5], [-20, -10, 2.5]]))
    assert not timeline.errors
    shape = timeline.state.bodies[body]
    assert len(ft.faces_of(shape)) == 6
    assert solids._volume(shape) == pytest.approx((20 * 20 + math.pi * 100) * 5, rel=1e-5)


def test_fillet_too_big_still_fails():
    timeline = build(ft.Fillet(name="R", edge_points=VERTICAL, radius=10.5))
    assert "zu groß" in timeline.errors[1]


def test_chamfer_to_diamond():
    timeline = build(ft.Chamfer(name="F", edge_points=VERTICAL, distance=10))
    assert not timeline.errors
    body = next(iter(timeline.state.bodies.values()))
    assert solids._volume(body) == pytest.approx(200 * 10, rel=1e-6)     # Raute mit Diagonalen 20 und 20


def test_sketch_corner_fillet_up_to_half_side():
    a, b, c = sk.line(0, 0, 20, 0), sk.line(20, 0, 20, 20), sk.line(20, 20, 0, 20)
    first = sk.corner_fillet(a, b, 10)
    second = sk.corner_fillet(first[1], c, 10)
    assert second is not None and math.dist(second[0][1:3], second[0][3:5]) < 1e-9     # die Seite ist verschwunden
    assert sk.corner_fillet(first[1], c, 10.5) is None


def l_profile(timeline, thickness=3.0):
    """L-Profil (Schenkel 40 und 30 mm, Wandstärke `thickness`), 20 mm hoch extrudiert."""
    t = thickness
    points = [(0, 0), (40, 0), (40, t), (t, t), (t, 30), (0, 30)]
    timeline.add(sk.Sketch(name="L", plane="XY",
                           entities=[sk.line(*a, *b) for a, b in zip(points, points[1:] + points[:1])]))
    timeline.add(ft.Extrude(name="E", sketch="L", distance=20, operation="Neuer Körper"))
    return next(iter(timeline.state.bodies))


def cylinders(shape):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    return sorted((round(c.Radius(), 6), round(c.Location().X(), 6), round(c.Location().Y(), 6))
                  for f in ft.faces_of(shape) for s in [BRepAdaptor_Surface(f)] if s.GetType() == GeomAbs_Cylinder
                  for c in [s.Cylinder()])


@pytest.mark.parametrize("aware, outer", [(True, 8.0), (False, 5.0)])
def test_inner_and_outer_corner_give_an_even_wall(aware, outer):
    timeline = Timeline()
    timeline.crash_guard = False
    body = l_profile(timeline)
    timeline.add(ft.Fillet(name="R", body=body, radius=5, wall_aware=aware,
                           edge_points=[[3, 3, 10], [0, 0, 10]]))       # innere und äußere Ecke
    assert not timeline.errors
    found = cylinders(timeline.state.bodies[body])
    assert [radius for radius, _x, _y in found] == [5.0, outer]
    if aware:
        # konzentrisch: gleicher Mittelpunkt – die Wand ist auch in der Biegung 3 mm stark
        assert found[0][1:] == found[1][1:]


def test_only_outer_edge_keeps_the_given_radius():
    timeline = Timeline()
    timeline.crash_guard = False
    body = l_profile(timeline)
    timeline.add(ft.Fillet(name="R", body=body, radius=5, wall_aware=True, edge_points=[[0, 0, 10]]))
    assert [radius for radius, _x, _y in cylinders(timeline.state.bodies[body])] == [5.0]


@pytest.mark.parametrize("box, edges, face", [
    (dict(length=20, width=20, height=10), VERTICAL, [0, 0, 10]),
    (dict(length=40, width=20, height=5), [[20, 10, 2.5], [20, -10, 2.5], [-20, 10, 2.5], [-20, -10, 2.5]], [0, 0, 5]),
    (dict(length=40, width=20, height=20), [[0, 10, 20], [0, -10, 20], [0, 10, 0], [0, -10, 0]], [20, 0, 10]),
])
def test_full_round_result_can_be_shelled(box, edges, face):
    """Die zusammengefasste volle Rundung ist sauber genug für weitere Schritte (die Schale nach innen stürzte an
    nur teilweise erneuerten Rändern ab)."""
    from OCP.ShapeAnalysis import ShapeAnalysis_ShapeTolerance
    timeline = Timeline()
    timeline.crash_guard = False
    timeline.add(solids.Box(name="Q", **box))
    body = next(iter(timeline.state.bodies))
    timeline.add(ft.Fillet(name="R", body=body, radius=10, edge_points=edges))
    assert ShapeAnalysis_ShapeTolerance().Tolerance(timeline.state.bodies[body], 1) < 1e-6
    before = solids._volume(timeline.state.bodies[body])
    timeline.add(solids.Shell(name="S", body=body, thickness=1.0, face_points=[face], direction="Innen"))
    assert not timeline.errors
    assert 0 < solids._volume(timeline.state.bodies[body]) < before
