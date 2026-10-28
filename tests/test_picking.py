# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für die Treffersuche bei Mausklicks (ohne Oberfläche)."""

import numpy as np

from wolfcad.core.solids import Box
from wolfcad.core.timeline import Timeline
from wolfcad.gui import mesh, picking

DOWN = np.array([0.0, 0.0, -1.0])


def test_ray_hits_the_nearest_triangle():
    low = [[-1, -1, 0], [1, -1, 0], [0, 1, 0]]
    high = [[-1, -1, 5], [1, -1, 5], [0, 1, 5]]
    triangles = np.array([low, high], dtype=float)
    assert picking.ray_triangles(np.array([0.0, 0.0, 10.0]), DOWN, triangles) == (1, 5.0)
    assert picking.ray_triangles(np.array([9.0, 0.0, 10.0]), DOWN, triangles) is None
    # Dreiecke hinter dem Auge zählen nicht
    assert picking.ray_triangles(np.array([0.0, 0.0, -3.0]), DOWN, triangles) is None


def test_ray_finds_segment_within_the_angle_only():
    segments = np.array([[[-5, 0, 0], [5, 0, 0]], [[-5, 3, 0], [5, 3, 0]]], dtype=float)
    eye = np.array([0.0, 0.05, 10.0])
    assert picking.ray_segments(eye, DOWN, segments, angle=0.01)[0] == 0
    assert picking.ray_segments(eye, DOWN, segments, angle=0.001) is None
    # Jenseits des Segmentendes gibt es keinen Treffer
    assert picking.ray_segments(np.array([7.0, 0.0, 10.0]), DOWN, segments, angle=0.01) is None


def test_pick_mesh_maps_triangles_and_segments_to_faces_and_edges():
    timeline = Timeline()
    timeline.add(Box(length=20, width=10, height=4))
    data = mesh.pick_mesh(timeline.state.body)
    assert len(data.face_points) == 6 and len(data.edge_points) == 12
    assert len(data.triangle_face) == len(data.triangles) // 3
    assert len(data.segment_edge) == len(data.segments) // 2

    # Von oben mitten auf den Quader: die Deckfläche (Schwerpunkt in z = 4) wird getroffen
    hit = picking.ray_triangles(np.array([1.0, 1.0, 50.0]), DOWN, data.triangles.reshape(-1, 3, 3))
    assert data.face_points[data.triangle_face[hit[0]]][2] == 4
    assert hit[1] == 46

    # Knapp neben der vorderen Oberkante: diese Kante wird gefunden
    edge = picking.ray_segments(np.array([0.0, -5.02, 50.0]), DOWN, data.segments.reshape(-1, 2, 3), angle=0.002)
    assert data.edge_points[data.segment_edge[edge[0]]] == (0.0, -5.0, 4.0)
