# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Wie Fusion: nach Vereinigen/Ausschneiden liegen Flächen einer Ebene als eine Fläche vor (Nutzervideo 2026-10-22:
ein aufgezogener Körper hatte zerstückelte Flächen, die sich nicht löschen ließen, und die Rundung an der Kante
scheiterte)."""
import math

import pytest

from wolfcad.core import features as ft
from wolfcad.core import solids
from wolfcad.core.timeline import Timeline


def flush_pair() -> Timeline:
    timeline = Timeline()
    timeline.add(solids.Box(name="A", length=100, width=50, height=10))
    timeline.add(solids.Box(name="B", center_x=100, length=100, width=50, height=10, operation="Vereinigen"))
    return timeline


def test_flush_union_is_one_box():
    shape = next(iter(flush_pair().state.bodies.values()))
    assert len(ft.faces_of(shape)) == 6 and len(ft.unique_edges(shape)) == 12


def test_round_the_long_edge_across_the_seam():
    timeline = flush_pair()
    body = next(iter(timeline.state.bodies))
    # obere Längskante über beide Teile (x −50 … 150, y = 25, z = 10)
    timeline.add(ft.Fillet(name="R", body=body, radius=5, edge_points=[[50, 25, 10]]))
    assert not timeline.errors
    removed = (25 - 25 * math.pi / 4) * 200
    assert solids._volume(timeline.state.bodies[body]) == pytest.approx(200 * 50 * 10 - removed, rel=1e-6)


def test_cut_leaves_no_split_faces():
    timeline = Timeline()
    timeline.add(solids.Box(name="A", length=100, width=50, height=20))
    # Nut von oben quer über die ganze Breite: die Böden links und rechts der Nut bleiben je eine Fläche
    timeline.add(solids.Box(name="N", length=10, width=60, height=10, offset=10, operation="Ausschneiden"))
    shape = next(iter(timeline.state.bodies.values()))
    assert len(ft.faces_of(shape)) == 10


def test_old_marker_on_a_merged_edge_still_found():
    """Ältere Entwürfe merkten die Mitte eines Kantenstücks (vor dem Zusammenfassen): sie liegt jetzt auf der
    durchgehenden Kante und findet diese weiterhin."""
    timeline = flush_pair()
    body = next(iter(timeline.state.bodies))
    timeline.add(ft.Fillet(name="R", body=body, radius=5, edge_points=[[0, 25, 10]]))     # alte Mitte von Teil A
    assert not timeline.errors


def test_profile_split_by_a_line_extrudes_to_one_clean_body():
    """Screenshot des Nutzers 2026-10-22: eine Linie teilt das Rechteck in zwei Bereiche; die Extrusion ergab zwei
    Volumenkörper in einem Körper mit Trennkante über die Oberseite. Wie Fusion: ein Quader mit 6 Flächen."""
    from wolfcad.core import sketch as sk
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=[*sk.rectangle(0, 0, 100, 40), sk.line(20, 0, 100, 40)]))
    timeline.add(ft.Extrude(name="E", sketch="S", distance=10))
    assert not timeline.errors and len(timeline.state.bodies) == 1
    shape = next(iter(timeline.state.bodies.values()))
    explorer, solids_found = TopExp_Explorer(shape, TopAbs_SOLID), 0
    while explorer.More():
        solids_found += 1
        explorer.Next()
    assert solids_found == 1 and len(ft.faces_of(shape)) == 6
    assert solids._volume(shape) == pytest.approx(100 * 40 * 10)
