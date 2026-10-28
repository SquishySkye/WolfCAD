# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für den Timeline-Kern: Neuberechnung, Zurückrollen, Fehlerbehandlung."""

import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from wolfcad.core.features import Chamfer, CircleSketch, Extrude, Fillet, RectangleSketch
from wolfcad.core.timeline import Timeline


def volume(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


@pytest.fixture
def block() -> Timeline:
    """Quader 40 x 20 x 10 mm mit einer Bohrung (Radius 5 mm)."""
    timeline = Timeline()
    timeline.add(RectangleSketch(name="S1", width=40, height=20))
    timeline.add(Extrude(name="E1", sketch="S1", distance=10))
    timeline.add(CircleSketch(name="S2", radius=5))
    timeline.add(Extrude(name="E2", sketch="S2", distance=10, operation="Ausschneiden"))
    return timeline


def test_build_matches_expected_volume(block):
    assert not block.errors
    assert volume(block.state.body) == pytest.approx(40 * 20 * 10 - 3.14159265 * 25 * 10, rel=1e-6)


def test_marker_rolls_model_back_and_forward(block):
    block.set_marker(2)
    assert volume(block.state.body) == pytest.approx(8000)
    block.set_marker(0)
    assert block.state.body is None
    block.set_marker(4)
    assert volume(block.state.body) < 8000


def test_parameter_change_propagates_to_later_features(block):
    block.update(0, width=80)
    assert volume(block.state.body) == pytest.approx(80 * 20 * 10 - 3.14159265 * 25 * 10, rel=1e-6)


def test_new_feature_is_inserted_at_marker(block):
    block.set_marker(2)
    block.add(Chamfer(distance=1))
    assert [f.name for f in block.features] == ["S1", "E1", "Fase1", "S2", "E2"]
    assert block.marker == 3


def test_suppress_skips_feature(block):
    block.set_suppressed(3, True)
    assert volume(block.state.body) == pytest.approx(8000)


def test_failing_feature_is_reported_and_rest_continues(block):
    block.set_marker(2)
    block.add(Fillet(name="zu gross", radius=500))
    block.set_marker(5)
    assert list(block.errors) == [2]
    # Die Bohrung wird trotz des fehlgeschlagenen Schritts noch ausgeführt
    assert volume(block.state.body) < 8000


def test_missing_sketch_reference_is_an_error(block):
    block.remove(2)
    assert "S2" in block.errors[2]


def test_reorder_changes_result(block):
    # Bohrung vor den Quader verschieben: zum Ausschneiden fehlt dann der Körper
    block.move(3, 0)
    assert 0 in block.errors


def test_save_and_load_roundtrip(block, tmp_path):
    block.set_marker(3)
    path = tmp_path / "entwurf.wcad"
    block.save(path)
    loaded = Timeline.load(path)
    assert [f.to_dict() for f in loaded.features] == [f.to_dict() for f in block.features]
    assert loaded.marker == 3
    assert volume(loaded.state.body) == pytest.approx(8000)


def test_fillet_rounds_sharp_edges(block):
    before = volume(block.state.body)
    block.add(Fillet(radius=1))
    assert not block.errors
    assert volume(block.state.body) < before


def test_chamfer_cuts_sharp_edges(block):
    before = volume(block.state.body)
    block.add(Chamfer(distance=0.5))
    assert not block.errors
    assert volume(block.state.body) < before


def test_chamfer_after_full_fillet_reports_missing_sharp_edges(block):
    block.add(Fillet(radius=1))
    block.add(Chamfer(distance=0.2))
    assert "scharfen Kanten" in block.errors[5]


def _two_blocks(gap: float) -> Timeline:
    """Zwei Quader 10 x 10 x 10 mm nebeneinander; `gap` ist der Abstand zwischen ihnen."""
    timeline = Timeline()
    timeline.add(RectangleSketch(name="A", width=10, height=10))
    timeline.add(Extrude(name="EA", sketch="A", distance=10))
    timeline.add(RectangleSketch(name="B", width=10, height=10, center_x=10 + gap))
    return timeline


def test_new_bodies_stay_separate():
    timeline = _two_blocks(gap=5)
    timeline.add(Extrude(name="EB", sketch="B", distance=10, operation="Neuer Körper"))
    assert list(timeline.state.bodies) == ["Körper1", "Körper2"]
    assert volume(timeline.state.body) == pytest.approx(2000)


def test_join_merges_only_touching_bodies():
    touching = _two_blocks(gap=0)
    touching.add(Extrude(name="EB", sketch="B", distance=10, operation="Vereinigen"))
    assert list(touching.state.bodies) == ["Körper1"]
    assert volume(touching.state.body) == pytest.approx(2000)

    apart = _two_blocks(gap=5)
    apart.add(Extrude(name="EB", sketch="B", distance=10, operation="Vereinigen"))
    assert len(apart.state.bodies) == 2


def test_cut_changes_only_the_body_it_hits():
    timeline = _two_blocks(gap=5)
    timeline.add(Extrude(name="EB", sketch="B", distance=10, operation="Neuer Körper"))
    timeline.add(CircleSketch(name="Loch", radius=2))
    timeline.add(Extrude(name="Schnitt", sketch="Loch", distance=10, operation="Ausschneiden"))
    assert not timeline.errors
    assert volume(timeline.state.bodies["Körper1"]) < 1000
    assert volume(timeline.state.bodies["Körper2"]) == pytest.approx(1000)


def test_cut_that_misses_everything_is_an_error():
    timeline = _two_blocks(gap=5)
    timeline.add(CircleSketch(name="Daneben", radius=1, center_x=100))
    timeline.add(Extrude(name="Schnitt", sketch="Daneben", distance=10, operation="Ausschneiden"))
    assert "trifft keinen" in timeline.errors[4]


def test_move_affects_only_bodies_of_the_named_origin():
    from wolfcad.core.features import move_bodies

    timeline = _two_blocks(gap=5)
    timeline.add(Extrude(name="EB", sketch="B", distance=10, operation="Neuer Körper"))
    state = timeline.state
    # Herkunft wie bei übernommenen Fusion-Schritten vermerken
    state.body_origin = {"Körper1": 111, "Körper2": 222}
    identity = (1, 0, 0, 0, 1, 0, 0, 0, 1)
    assert move_bodies(state, [222], identity, (0, 0, 50)) == 1
    assert _centre_z(state.bodies["Körper1"]) == pytest.approx(5)
    assert _centre_z(state.bodies["Körper2"]) == pytest.approx(55)
    assert move_bodies(state, [999], identity, (0, 0, 50)) == 0


def _centre_z(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.CentreOfMass().Z()


def test_combine_uses_tool_bodies_and_removes_them():
    from wolfcad.core.features import combine_bodies

    timeline = _two_blocks(gap=-5)      # die Quader überlappen sich um 5 mm
    timeline.add(Extrude(name="EB", sketch="B", distance=10, operation="Neuer Körper"))
    state = timeline.state
    state.body_origin = {"Körper1": 111, "Körper2": 222}
    combine_bodies(state, 111, [222], "Ausschneiden")
    assert list(state.bodies) == ["Körper1"]
    assert volume(state.bodies["Körper1"]) == pytest.approx(500)

    with pytest.raises(Exception, match="Werkzeugkörper"):
        combine_bodies(state, 111, [222], "Vereinigen")


def test_frozen_bodies_are_left_alone_by_later_cuts():
    timeline = _two_blocks(gap=5)
    timeline.add(Extrude(name="EB", sketch="B", distance=10, operation="Neuer Körper"))
    timeline.state.frozen.add("Körper1")
    from wolfcad.core.features import BuildState, _combine  # noqa: F401
    state = timeline.state
    hole = Timeline()
    hole.add(CircleSketch(name="Loch", radius=2))
    hole.add(Extrude(name="Zylinder", sketch="Loch", distance=10))
    with pytest.raises(Exception, match="trifft keinen"):
        _combine(state, hole.state.body, "Ausschneiden")
    assert volume(state.bodies["Körper1"]) == pytest.approx(1000)


def test_same_bodies_compares_volume_and_position():
    from wolfcad.core.features import _same_bodies

    first, second = _two_blocks(gap=5), _two_blocks(gap=5)
    assert _same_bodies([first.state.body], [second.state.body])
    second.update(1, distance=11)
    assert not _same_bodies([first.state.body], [second.state.body])
    assert not _same_bodies([first.state.body], [])


def test_partial_recompute_gives_the_same_result_as_a_full_one(block):
    block.add(Fillet(radius=1))
    # Parameteränderungen in der Mitte rechnen ab dieser Stelle weiter ...
    block.update(2, radius=3)
    partial = volume(block.state.body)
    # ... und müssen dasselbe ergeben wie eine vollständige Neuberechnung
    block.recompute()
    assert partial == pytest.approx(volume(block.state.body))
    assert volume(block.state_before(1).body) if block.state_before(1).body else True
    assert block.state_before(1).body is None           # vor der ersten Extrusion gibt es keinen Körper
    assert volume(block.state_before(2).body) == pytest.approx(8000)


def test_partial_recompute_keeps_earlier_errors_and_clears_fixed_ones(block):
    block.add(Fillet(name="zu gross", radius=500))
    assert 4 in block.errors
    block.update(4, radius=1)
    assert not block.errors
    block.update(1, distance=0)         # Fehler weiter vorn
    assert 1 in block.errors
    block.update(4, radius=2)           # Änderung weiter hinten lässt den früheren Fehler stehen
    assert 1 in block.errors


def test_marker_uses_remembered_states(monkeypatch):
    """Zurück- und wieder Vorrollen nimmt die gemerkten Zwischenstände (kein Neurechnen) – bei Tausenden Schritten
    einer übernommenen Fusion-Timeline sonst sekundenlang."""
    from wolfcad.core import solids
    timeline = Timeline()
    for number in range(4):
        timeline.add(solids.Box(name=f"Q{number}", center_x=number * 30.0, length=10, width=10, height=10,
                                operation="Neuer Körper"))
    calls = []
    original = solids.Box.apply
    monkeypatch.setattr(solids.Box, "apply", lambda self, state: (calls.append(self.name), original(self, state))[1])
    timeline.set_marker(1)
    assert len(timeline.state.bodies) == 1 and not calls
    timeline.set_marker(4)
    assert len(timeline.state.bodies) == 4 and not calls
    # nach einer Änderung weiter vorn gelten die Stände dahinter nicht mehr: dann wird gerechnet
    timeline.set_marker(2)
    timeline.update(0, length=20)
    timeline.set_marker(4)
    assert calls and len(timeline.state.bodies) == 4
    assert volume(next(iter(timeline.state.bodies.values()))) == pytest.approx(20 * 10 * 10)
