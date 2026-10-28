# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für das Messen von Kanten, Flächen und Abständen."""

from wolfcad.core import features as ft
from wolfcad.core import measure
from wolfcad.core.solids import Box, Cylinder
from wolfcad.core.timeline import Timeline


def shapes(*features):
    timeline = Timeline()
    for feature in features:
        timeline.add(feature)
    return list(timeline.state.bodies.values())


def test_single_edge_and_face():
    (box,) = shapes(Box(length=40, width=20, height=10))
    lengths = sorted(measure.describe("edge", edge)[0] for edge in ft.unique_edges(box))
    assert lengths[0] == "Länge 10,000 mm" and lengths[-1] == "Länge 40,000 mm"
    areas = sorted(measure.describe("face", face)[0] for face in ft.faces_of(box))
    assert areas[0] == "Flächeninhalt 200,000 mm²" and areas[-1] == "Flächeninhalt 800,000 mm²"
    (cylinder,) = shapes(Cylinder(diameter=16, height=14))
    round_face = next(lines for lines in (measure.describe("face", f) for f in ft.faces_of(cylinder)) if len(lines) > 1)
    assert round_face[1] == "Radius 8,000 mm (⌀ 16,000 mm)"
    circle = next(lines for lines in (measure.describe("edge", e) for e in ft.unique_edges(cylinder)) if len(lines) > 1)
    assert circle[1].startswith("Radius 8,000 mm")


def test_distance_and_angle_between_faces():
    (box,) = shapes(Box(length=40, width=20, height=10))
    faces = ft.faces_of(box)
    by_height = sorted(faces, key=lambda face: ft.face_point(face)[2])
    bottom, top = by_height[0], by_height[-1]
    side = next(face for face in faces if abs(ft.face_point(face)[0]) > 19)
    lines, points = measure.between(("face", bottom), ("face", top))
    assert lines[0] == "Abstand 10,000 mm" and lines[1] == "Winkel 180,000°"
    assert abs(points[0][2] - points[1][2]) == 10
    lines, _points = measure.between(("face", top), ("face", side))
    assert lines == ["Abstand 0,000 mm", "Winkel 90,000°"]
    # Kante gegen Fläche: eine senkrechte Kante steht 90° zur Deckfläche
    upright = next(edge for edge in ft.unique_edges(box) if measure.describe("edge", edge)[0] == "Länge 10,000 mm")
    assert measure.between(("edge", upright), ("face", top))[0][-1] == "Winkel 90,000°"
