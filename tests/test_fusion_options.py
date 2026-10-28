# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Fusion-Optionen: Tangentenkette (Kern), Durch alles, Kopie erstellen, Werkzeuge behalten, Teilen mit Körper,
Schalenrichtung, Drehen symmetrisch/zwei Seiten."""
import math

import pytest

from wolfcad.core import features as ft
from wolfcad.core import sketch as sk
from wolfcad.core import solids
from wolfcad.core.timeline import Timeline


def fresh() -> Timeline:
    timeline = Timeline()
    timeline.crash_guard = False
    return timeline


def square(cx, cy, size):
    h = size / 2
    c = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]
    return [sk.line(*a, *b) for a, b in zip(c, c[1:] + c[:1])]


def only(timeline):
    assert not timeline.errors, timeline.errors
    return next(iter(timeline.state.bodies.values()))


def test_fillet_follows_tangent_chain_like_fusion():
    # eine einzige obere Kante eines abgerundeten Quaders: der Kern rundet den ganzen tangentialen Umlauf
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=20, width=20, height=10))
    body = next(iter(timeline.state.bodies))
    timeline.add(ft.Fillet(name="R1", body=body, edge_points=[[10, 10, 5], [-10, 10, 5], [10, -10, 5],
                                                              [-10, -10, 5]], radius=3))
    before = solids._volume(only(timeline))
    timeline.add(ft.Fillet(name="R2", body=body, edge_points=[[0, 10, 10]], radius=1))
    removed = before - solids._volume(only(timeline))
    single_edge = (1 - math.pi / 4) * 1 * 14       # eine gerade Kante (Länge 14) allein
    assert removed > 3 * single_edge


def test_extrude_through_all_cuts_everything():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=40, width=40, height=10))
    timeline.add(sk.Sketch(name="S", plane="XY", offset=10, entities=square(0, 0, 10)))
    timeline.add(ft.Extrude(name="E", sketch="S", distance=-1, extent=ft.EXTENTS[4], operation="Ausschneiden"))
    assert solids._volume(only(timeline)) == pytest.approx(40 * 40 * 10 - 10 * 10 * 10)


def test_move_copy_keeps_original():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=10, width=10, height=10))
    body = next(iter(timeline.state.bodies))
    timeline.add(solids.MoveBody(name="V", body=body, move_x=30, copy=True))
    lows = sorted(solids._bounds(shape)[0] for shape in timeline.state.bodies.values())
    assert lows == pytest.approx([-5, 25])


def test_combine_keep_tool():
    timeline = fresh()
    timeline.add(solids.Box(name="A", length=20, width=20, height=10))
    timeline.add(solids.Box(name="B", center_x=10, length=20, width=20, height=10, operation="Neuer Körper"))
    a, b = list(timeline.state.bodies)
    timeline.add(solids.CombineBodies(name="K", target=a, tool=b, operation="Ausschneiden", keep_tool=True))
    assert set(timeline.state.bodies) == {a, b}
    assert solids._volume(timeline.state.bodies[a]) == pytest.approx(10 * 20 * 10)


def test_split_by_body():
    timeline = fresh()
    timeline.add(solids.Box(name="A", length=40, width=20, height=10))
    timeline.add(solids.Cylinder(name="Z", diameter=10, height=30, offset=-10, operation="Neuer Körper"))
    a, tool = list(timeline.state.bodies)
    timeline.add(solids.SplitBody(name="T", body=a, tool=tool))
    assert not timeline.errors, timeline.errors
    assert len(timeline.state.bodies) == 3        # Quader in zwei Teile (innen/außen), Werkzeug bleibt


@pytest.mark.parametrize("direction, outer", [("Innen", 40), ("Außen", 44), ("Beidseitig", 42)])
def test_shell_directions(direction, outer):
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=40, width=40, height=20))
    body = next(iter(timeline.state.bodies))
    timeline.add(solids.Shell(name="H", body=body, thickness=2, face_points=[[0, 0, 20]], direction=direction))
    bounds = solids._bounds(only(timeline))
    assert bounds[3] - bounds[0] == pytest.approx(outer, abs=1e-3)


@pytest.mark.parametrize("extent, angle2", [("Symmetrisch", 0.0), ("Zwei Seiten", 30.0)])
def test_revolve_extents(extent, angle2):
    timeline = fresh()
    timeline.add(sk.Sketch(name="S", plane="XZ", entities=square(15, 5, 10)))
    timeline.add(solids.Revolve(name="D", sketch="S", axis="Y-Achse" if False else solids.REVOLVE_AXES[1],
                                angle=90, extent=extent, angle2=angle2))
    full = solids._volume(only(timeline))
    timeline.update(1, extent="Eine Seite")
    one = solids._volume(only(timeline))
    expected = one if extent == "Symmetrisch" else one * (90 + angle2) / 90
    assert full == pytest.approx(expected, rel=1e-6)
