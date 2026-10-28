# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Runde 79: Zwischenspeicher für Flächen-/Kantenmerkpunkte und abgetastete Kanten (Leistung) – gleiche Ergebnisse
wie vorher, richtige Zuordnung der Strecken zu ihren Kanten."""
import math

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from wolfcad.core import features as ft
from wolfcad.gui import mesh


def _plate_with_hole():
    plate = BRepPrimAPI_MakeBox(40.0, 30.0, 5.0).Shape()
    pin = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(20.0, 15.0, -1.0), gp_Dir(0, 0, 1)), 4.0, 7.0).Shape()
    return BRepAlgoAPI_Cut(plate, pin).Shape()


def test_face_and_edge_points_are_cached_and_unchanged():
    shape = _plate_with_hole()
    faces, edges = ft.faces_of(shape), ft.unique_edges(shape)
    first = [ft.face_point(face) for face in faces]
    again = [ft.face_point(face) for face in ft.faces_of(shape)]      # neue Python-Objekte, dieselben Flächen
    assert first == again
    assert any(key == hash(faces[0]) for key in ft._FACE_POINTS)
    # die obere Fläche (z = 5) hat ihren Schwerpunkt in der Plattenmitte
    assert any(math.isclose(z, 5.0) and math.isclose(x, 20.0, abs_tol=1e-6) for x, _y, z in first)
    assert [ft.edge_point(edge) for edge in edges] == [ft.edge_point(edge) for edge in ft.unique_edges(shape)]


def test_box_segments_belong_to_their_edges():
    box = BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()
    pick = mesh.pick_mesh(box)
    # zwölf gerade Kanten: je eine Strecke, jede gehört genau ihrer Kante
    assert len(pick.segments) == 24 and list(pick.segment_edge) == list(range(12))
    for number, (start, end) in enumerate(pick.segments.reshape(-1, 2, 3)):
        middle = (start + end) / 2
        assert np.allclose(middle, pick.edge_points[number], atol=1e-5)


def test_curved_edges_and_sketch_lines_match_after_caching():
    shape = _plate_with_hole()
    first = mesh.pick_mesh(shape)
    second = mesh.pick_mesh(shape)                      # zweites Mal aus dem Speicher
    assert np.array_equal(first.segments, second.segments)
    assert np.array_equal(first.segment_edge, second.segment_edge)
    assert len(first.segments) // 2 == len(first.segment_edge)
    lines = mesh.lines(shape)
    assert len(lines) % 2 == 0 and len(lines) >= len(first.segments) // 2
    assert np.array_equal(lines, mesh.lines(shape))
