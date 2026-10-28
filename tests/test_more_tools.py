# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Werkzeuge aus Runde 58: Prägen, Steg, Konturflansch (mit Abwicklung), Aufreißen, Ebene entlang Pfad, Achse senkrecht
zur Fläche, Gelenkursprung, Starre Gruppe, Leinwand, Isokurven, DXF-Blöcke und SVG-Text."""
import math

import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from wolfcad.core import analysis
from wolfcad.core import extras as ex
from wolfcad.core import features as ft
from wolfcad.core import inserts
from wolfcad.core import more_tools as mt
from wolfcad.core import sheetmetal as sm
from wolfcad.core import sketch as sk
from wolfcad.core import solids
from wolfcad.core.timeline import Timeline


def volume(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def fresh() -> Timeline:
    timeline = Timeline()
    timeline.crash_guard = False
    return timeline


def square(cx: float, cy: float, size: float) -> list:
    """Vier Linien eines Quadrats um (cx, cy)."""
    h = size / 2
    corners = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]
    return [sk.line(*a, *b) for a, b in zip(corners, corners[1:] + corners[:1])]


def only_body(timeline: Timeline):
    assert len(timeline.state.bodies) == 1, list(timeline.state.bodies)
    return next(iter(timeline.state.bodies.values()))


@pytest.mark.parametrize("mode, change", [(mt.EMBOSS_MODES[0], 200.0), (mt.EMBOSS_MODES[1], -200.0)])
def test_emboss_on_plane(mode, change):
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=40, width=40, height=10))
    before = volume(only_body(timeline))
    timeline.add(sk.Sketch(name="S", plane="XY", offset=10, entities=square(0, 0, 10)))
    body = next(iter(timeline.state.bodies))
    timeline.add(mt.Emboss(name="P", sketch="S", body=body, face_points=[[0, 0, 10]], depth=2, mode=mode))
    assert not timeline.errors, timeline.errors
    assert volume(only_body(timeline)) - before == pytest.approx(change, rel=1e-6)


def test_emboss_on_cylinder():
    timeline = fresh()
    timeline.add(solids.Cylinder(name="Z", diameter=20, height=40, offset=-20))
    before = volume(only_body(timeline))
    timeline.add(sk.Sketch(name="S", plane="YZ", offset=20, entities=square(0, 0, 4)))
    body = next(iter(timeline.state.bodies))
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    wall = next(face for face in ft.faces_of(timeline.state.bodies[body])
                if BRepAdaptor_Surface(face).GetType() == GeomAbs_Cylinder)
    timeline.add(mt.Emboss(name="P", sketch="S", body=body, face_points=[list(ft.face_point(wall))], depth=2))
    assert not timeline.errors, timeline.errors
    grown = volume(only_body(timeline)) - before
    # projiziertes Quadrat 4 × 4 auf einem Ring 10 … 12 mm: etwas mehr als 4·4·2 (der Ring ist außen breiter)
    assert 30.0 < grown < 40.0


def test_web_joins_body():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=40, width=40, height=10))
    before = volume(only_body(timeline))
    timeline.add(sk.Sketch(name="S", plane="XY", offset=10, entities=[sk.line(-10, 0, 10, 0)]))
    timeline.add(mt.Web(name="W", sketch="S", thickness=2, depth=5))
    assert not timeline.errors, timeline.errors
    assert volume(only_body(timeline)) - before == pytest.approx(20 * 2 * 5, rel=1e-6)


def test_contour_flange_and_flat_pattern():
    timeline = fresh()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=[sk.line(1, 1, 31, 1), sk.line(31, 1, 31, 21)]))
    timeline.add(mt.ContourFlange(name="K", sketch="S", width=10, thickness=1, bend_radius=1, k_factor=0.44))
    assert not timeline.errors, timeline.errors
    name, body = next(iter(timeline.state.bodies.items()))
    # Linksbiegung mit Blech links vom Linienzug: der Linienzug ist die Außenseite (Radius R + T = 2)
    folded = (28 + 18) * 1 * 10 + math.pi / 4 * (2 ** 2 - 1 ** 2) * 10
    assert volume(body) == pytest.approx(folded, rel=1e-6)
    flat = sm.flat_pattern(timeline.state.sheets[name])
    flat_length = 28 + 18 + math.pi / 2 * (1 + 0.44 * 1)
    assert volume(flat) == pytest.approx(flat_length * 1 * 10, rel=1e-6)
    # die Abwicklung liegt eben: eine Seite 1 mm dick
    bounds = solids._bounds(flat)
    sizes = sorted(bounds[i + 3] - bounds[i] for i in range(3))
    assert sizes[0] == pytest.approx(1.0, abs=1e-6) and sizes[2] == pytest.approx(flat_length, abs=1e-6)


def test_contour_flange_rejects_short_segment():
    timeline = fresh()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=[sk.line(1, 1, 2, 1), sk.line(2, 1, 2, 21)]))
    timeline.add(mt.ContourFlange(name="K", sketch="S", bend_radius=3))
    assert "zu kurz" in timeline.errors[1]


def test_rip_opens_corner():
    state = ft.BuildState()
    solids.Box(name="Q", length=40, width=40, height=10).apply(state)
    name = next(iter(state.bodies))
    solids.Shell(name="H", body=name, thickness=1, face_points=[[0, 0, 10]]).apply(state)
    state.sheets[name] = {"thickness": 1.0}
    before = volume(state.bodies[name])
    mt.Rip(name="R", body=name, edge_points=[[20, 20, 5]], gap=0.5).apply(state)
    assert before - volume(state.bodies[name]) == pytest.approx(10 * 1 * 1.5, rel=1e-6)


def test_plane_along_path_and_axis_normal():
    timeline = fresh()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=[sk.line(1, 1, 11, 1)]))
    timeline.add(mt.PlaneAlongPath(name="E", path="S", position=0.5))
    plane = timeline.state.planes["E"]
    assert plane.Location().X() == pytest.approx(6.0) and abs(plane.Direction().X()) == pytest.approx(1.0)
    timeline.add(solids.Box(name="Q", length=40, width=40, height=10))
    body = next(iter(timeline.state.bodies))
    timeline.add(mt.AxisNormalToFace(name="A", body=body, point=[3, 4, 10]))
    point, direction = timeline.state.axes["A"]
    assert point == pytest.approx((3, 4, 10)) and direction == pytest.approx((0, 0, 1))


def test_joint_origin_moves_and_joins():
    timeline = fresh()
    timeline.add(solids.Box(name="Q1", length=10, width=10, height=10))
    timeline.add(solids.Box(name="Q2", center_x=50, length=10, width=10, height=10, operation="Neuer Körper"))
    first, second = list(timeline.state.bodies)
    # Ursprung an der Oberseite des ersten Quaders, außermittig
    timeline.add(mt.JointOrigin(name="U", body=first, point=[2, 3, 10, 0, 0, 1]))
    timeline.add(solids.Joint(name="G", body=second, target=first, face_points=[[50, 0, 0], []],
                              target_origin="U"))
    assert not timeline.errors, timeline.errors
    bounds = solids._bounds(timeline.state.bodies[second])
    # Unterseite des zweiten Quaders sitzt mittig auf dem Ursprung
    assert bounds[2] == pytest.approx(10) and (bounds[0] + bounds[3]) / 2 == pytest.approx(2)
    assert (bounds[1] + bounds[4]) / 2 == pytest.approx(3)


def test_rigid_group_moves_together():
    timeline = fresh()
    for index in range(3):
        timeline.add(solids.Box(name=f"Q{index}", center_x=30 * index, length=10, width=10, height=10,
                                operation="Neuer Körper"))
    a, b, c = list(timeline.state.bodies)
    timeline.add(mt.RigidGroup(name="G", bodies=[a, b]))
    timeline.add(solids.MoveBody(name="V", body=a, move_z=5))
    lows = [solids._bounds(timeline.state.bodies[name])[2] for name in (a, b, c)]
    assert lows == pytest.approx([5, 5, 0], abs=1e-5)
    assert set(timeline.state.moving_with(a)) == {a, b}


def test_canvas_corners():
    timeline = fresh()
    timeline.add(mt.Canvas(name="L", image="AAAA", aspect=0.5, width=100, center_x=10, opacity=0.4))
    corners = timeline.state.canvases["L"]["corners"]
    assert corners[0] == pytest.approx((-40, -25, 0)) and corners[2] == pytest.approx((60, 25, 0))
    timeline.add(mt.Canvas(name="M"))
    assert "kein Bild" in timeline.errors[1]


def test_iso_lines_on_cylinder():
    shape = solids._prim_cylinder(10, 20) if hasattr(solids, "_prim_cylinder") else None
    if shape is None:
        state = ft.BuildState()
        solids.Cylinder(name="Z", diameter=20, height=20).apply(state)
        shape = next(iter(state.bodies.values()))
    segments = analysis.iso_lines(ft.faces_of(shape), count=3)
    assert len(segments) > 0 and len(segments) % 2 == 0
    radii = [math.hypot(x, y) for x, y, z in segments if 0.01 < z < 19.99]
    assert radii and max(radii) == pytest.approx(10, abs=1e-6)


def test_dxf_insert_block(tmp_path):
    path = tmp_path / "block.dxf"
    path.write_text("0\nSECTION\n2\nBLOCKS\n0\nBLOCK\n2\nSTRICH\n10\n0\n20\n0\n0\nLINE\n10\n0\n20\n0\n11\n10\n21\n0\n"
                    "0\nENDBLK\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n"
                    "0\nINSERT\n2\nSTRICH\n10\n5\n20\n5\n41\n2\n42\n2\n50\n90\n0\nENDSEC\n0\nEOF\n")
    (line,) = inserts.read_dxf(str(path))
    assert line[1:] == pytest.approx([5, 5, 5, 25])        # verdoppelt, um 90° gedreht, an (5, 5)


def test_svg_text(tmp_path):
    path = tmp_path / "text.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text x="96" y="0" font-size="96">'
                    'Wolf<tspan>CAD</tspan></text></svg>')
    entities, texts = inserts.read_svg_full(str(path))
    assert not entities and texts == [["Wolf CAD", pytest.approx(25.4), pytest.approx(0.0), pytest.approx(25.4),
                                       pytest.approx(0.0)]]
