# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Übernommene Abrundungen, Fasen und Einfügen aus Fusion-Dateien: Kanten über Fusions zwischengespeicherte
Geometrie wiederfinden (auch zerlegte Kanten), anwenden, Körperkopien – mit einem nachgebildeten Entwurf."""
import math
from types import SimpleNamespace

import pytest

from wolfcad.core import features as ft
from wolfcad.core import solids
from wolfcad.f3d import bodies, edges

SOURCE = "nachgebildet.f3d"


def box_state(origin: int = 5) -> ft.BuildState:
    """Ein Würfel 20 × 20 × 10 mm (Mitte x/y im Ursprung) mit Herkunft `origin`."""
    state = ft.BuildState()
    solids.Box(length=20, width=20, height=10).apply(state)
    name = next(iter(state.bodies))
    state.body_origin[name] = origin
    return state


# senkrechte Kante des Würfels bei x = y = 10 mm, in Fusions Einheit cm: Start (1, 1, 0), Vektor (0, 0, 1)
VERTICAL = edges.CachedEdge("line", (1.0, 1.0, 0.0, 0.0, 0.0, 1.0))


@pytest.fixture
def fake_design():
    design = SimpleNamespace(
        edge_features={}, pastes={}, targets={}, combines={},
        bodies=SimpleNamespace(creator_of={100: 5}),
        leaves_no_body=lambda feature_id: False)
    ft._DESIGN_CACHE[SOURCE] = design
    yield design
    del ft._DESIGN_CACHE[SOURCE]


def test_cached_line_and_arc_points():
    assert VERTICAL.points() == [(1.0, 1.0, 0.0), (1.0, 1.0, 0.5), (1.0, 1.0, 1.0)]
    # Viertelkreis um den Ursprung in der xy-Ebene, Radius 2
    arc = edges.CachedEdge("arc", (0, 0, 0, 0, 0, 1, 1, 0, 0, 2.0, 0.0, math.pi / 2))
    first, middle, last = arc.points()
    assert first == pytest.approx((2, 0, 0)) and last == pytest.approx((0, 2, 0), abs=1e-12)
    assert middle == pytest.approx((math.sqrt(2), math.sqrt(2), 0))


def test_edges_found_by_cached_geometry():
    shape = next(iter(box_state().bodies.values()))
    assert len(ft.edges_at_cached(shape, [VERTICAL])) == 1
    # eine Kante, die es nicht gibt (1 mm daneben), wird nicht gefunden
    assert ft.edges_at_cached(shape, [edges.CachedEdge("line", (1.1, 1.0, 0.0, 0.0, 0.0, 1.0))]) == []
    # eine gespeicherte Kante über die ganze obere Längskante plus Überstand: findet die (kürzere) Körperkante
    longer = edges.CachedEdge("line", (-1.5, 1.0, 1.0, 3.0, 0.0, 0.0))
    assert len(ft.edges_at_cached(shape, [longer])) == 1


def test_fusion_fillet_rounds_the_cached_edge(fake_design):
    fake_design.edge_features[1] = edges.FusionEdgeFeature(1, "fillet", [
        edges.EdgeSet(bodies=[100], edges=[VERTICAL], values={"Radius": 0.2})])
    state = box_state()
    before = solids._volume(next(iter(state.bodies.values())))
    ft.FusionFillet(source=SOURCE, fusion_id=1, radius=2.0).apply(state)
    after = solids._volume(next(iter(state.bodies.values())))
    # eine Rundung mit 2 mm nimmt (4 − π) mm² × 10 mm weg
    assert before - after == pytest.approx((4 - math.pi) * 10, rel=1e-6)


def test_fusion_chamfer_and_missing_edges(fake_design):
    fake_design.edge_features[2] = edges.FusionEdgeFeature(2, "chamfer", [
        edges.EdgeSet(bodies=[100], edges=[VERTICAL], values={"Distance": 0.3})])
    state = box_state()
    before = solids._volume(next(iter(state.bodies.values())))
    ft.FusionChamfer(source=SOURCE, fusion_id=2, distance=3.0).apply(state)
    assert before - solids._volume(next(iter(state.bodies.values()))) == pytest.approx(4.5 * 10, rel=1e-6)
    # Kante nicht im Modell: klare Meldung statt eines Absturzes
    fake_design.edge_features[3] = edges.FusionEdgeFeature(3, "fillet", [
        edges.EdgeSet(bodies=[100], edges=[edges.CachedEdge("line", (5, 5, 0, 0, 0, 1))], values={"Radius": 0.1})])
    with pytest.raises(ft.FeatureError, match="nicht zu finden"):
        ft.FusionFillet(source=SOURCE, fusion_id=3, radius=1.0).apply(box_state())


def test_fusion_paste_copies_the_body(fake_design):
    fake_design.pastes[4] = bodies.FusionPaste(4, [5], 77)
    state = box_state()
    ft.FusionPaste(source=SOURCE, fusion_id=4).apply(state)
    assert len(state.bodies) == 2 and sorted(state.body_origin.values()) == [5, 77]
    volumes = [solids._volume(shape) for shape in state.bodies.values()]
    assert volumes[0] == pytest.approx(volumes[1])


def test_combine_can_keep_its_tools():
    state = box_state(origin=5)
    tool = ft.BuildState()
    solids.Box(center_x=10, length=20, width=20, height=10).apply(tool)
    name = state.add_body(next(iter(tool.bodies.values())), 6)
    ft.combine_bodies(state, 5, [6], "Vereinigen", keep_tools=True)
    assert name in state.bodies and len(state.bodies) == 2
    ft.combine_bodies(state, 5, [6], "Vereinigen")
    assert name not in state.bodies and len(state.bodies) == 1


def test_count_matching_bodies():
    boxes = []
    for x in (0, 30, 60):
        state = ft.BuildState()
        solids.Box(center_x=x, length=10, width=10, height=10).apply(state)
        boxes.append(next(iter(state.bodies.values())))
    # zwei der drei Bezugskörper sind da (einer doppelt – zählt nur einmal), einer fehlt
    assert ft.count_matching_bodies(boxes, [boxes[0], boxes[1], boxes[1]]) == 2
    assert ft.count_matching_bodies(boxes[:1] * 2, boxes[:1]) == 1
