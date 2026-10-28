# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Extrusion wie in Fusion: getrennte Profile ergeben getrennte Körper; Flächen und Profile gemischt."""
import pytest

from wolfcad.core import features as ft
from wolfcad.core import sketch as sk
from wolfcad.core import solids
from wolfcad.core.timeline import Timeline


def square(cx: float, cy: float, size: float) -> list:
    h = size / 2
    corners = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]
    return [sk.line(*a, *b) for a, b in zip(corners, corners[1:] + corners[:1])]


def fresh() -> Timeline:
    timeline = Timeline()
    timeline.crash_guard = False
    return timeline


def boxes(timeline: Timeline) -> list:
    return sorted([round(c, 3) for c in solids._bounds(body)] for body in timeline.state.bodies.values())


def test_two_separate_profiles_become_two_bodies():
    timeline = fresh()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=square(-20, 0, 10) + square(20, 0, 10)))
    timeline.add(ft.Extrude(name="E", sketch="S", distance=5, operation="Neuer Körper"))
    assert boxes(timeline) == [[-25, -5, 0, -15, 5, 5], [15, -5, 0, 25, 5, 5]]


def test_touching_profiles_stay_one_body():
    timeline = fresh()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=square(-5, 0, 10) + square(5, 0, 10)))
    timeline.add(ft.Extrude(name="E", sketch="S", distance=5, operation="Neuer Körper"))
    assert len(timeline.state.bodies) == 1


def test_join_only_merges_the_touching_piece():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", center_x=-20, length=10, width=10, height=5))
    timeline.add(sk.Sketch(name="S", plane="XY", offset=5, entities=square(-20, 0, 10) + square(20, 0, 10)))
    timeline.add(ft.Extrude(name="E", sketch="S", distance=5, operation="Vereinigen"))
    # das linke Teil wächst auf den Quader (0 … 10), das rechte steht als eigener Körper (5 … 10)
    assert boxes(timeline) == [[-25, -5, 0, -15, 5, 10], [15, -5, 5, 25, 5, 10]]


def test_face_extrusion_ignores_empty_sketch_and_cuts_only_down():
    # die Nachstellung des gemeldeten Fehlers: zwei Profile aufziehen, dann an einer Oberseite 5 mm ausschneiden
    timeline = fresh()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=square(-20, 0, 20) + square(20, 0, 20)))
    timeline.add(ft.Extrude(name="E1", sketch="S", distance=10, operation=ft.AUTO_OPERATION))
    timeline.add(ft.Extrude(name="E2", sketch="", face_points=[[-20, 0, 10]], distance=-5,
                            operation=ft.AUTO_OPERATION))
    assert not timeline.errors
    assert boxes(timeline) == [[-30, -10, 0, -10, 10, 5], [10, -10, 0, 30, 10, 10]]
