"""Runde 95: Fusion-Optionen der Muster – Verteilung, symmetrisch, Exemplare unterdrücken."""
import pytest

from wolfcad.core import solids, sketch as sk
from wolfcad.core.timeline import Timeline


def _cube_with(pattern) -> Timeline:
    timeline = Timeline()
    timeline.add(solids.Box(length=4, width=4, height=4))
    timeline.add(pattern)
    return timeline


def _count(timeline: Timeline) -> int:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    explorer, count = TopExp_Explorer(timeline.state.body, TopAbs_SOLID), 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def test_extent_distribution_spreads_over_total_length():
    timeline = _cube_with(solids.RectangularPattern(count=5, spacing=40, distribution="Ausdehnung",
                                                    operation="Neuer Körper"))
    assert timeline.errors == {}
    bounds = solids._bounds(timeline.state.body)
    assert bounds[3] - bounds[0] == pytest.approx(44)          # 40 mm Ausdehnung + Würfel
    assert _count(timeline) == 5


def test_symmetric_and_skipped_instances():
    timeline = _cube_with(solids.RectangularPattern(count=3, spacing=10, symmetric=True, skip="3, 4",
                                                    operation="Neuer Körper"))
    assert timeline.errors == {}
    bounds = solids._bounds(timeline.state.body)
    assert bounds[0] == pytest.approx(-22) and bounds[3] == pytest.approx(22)     # nach beiden Seiten
    assert _count(timeline) == 5 - 2                                              # zwei unterdrückt


def test_circular_symmetric_and_skip():
    timeline = Timeline()
    timeline.add(solids.Box(center_x=20, length=4, width=4, height=4))
    timeline.add(solids.CircularPattern(count=5, angle=60, symmetric=True, skip="3", operation="Neuer Körper"))
    assert timeline.errors == {} and _count(timeline) == 4
    with pytest.raises(Exception):
        solids.skipped_items("zwei")


def test_path_pattern_skip():
    timeline = Timeline()
    timeline.add(solids.Box(length=2, width=2, height=2))
    timeline.add(sk.Sketch(name="P", entities=[sk.line(0, 0, 50, 0)]))
    timeline.add(solids.PathPattern(path="P", count=5, skip="2-3", operation="Neuer Körper"))
    assert timeline.errors == {} and _count(timeline) == 3


def test_mirror_feature_repeats_hole_on_other_side():
    timeline = Timeline()
    timeline.add(solids.Box(length=40, width=20, height=10))
    timeline.add(sk.Sketch(name="S", offset=10, entities=[sk.circle(10, 0, 3)]))
    from wolfcad.core import features as ft
    timeline.add(ft.Extrude(name="Loch", sketch="S", distance=-10, operation="Ausschneiden"))
    single = solids._volume(timeline.state.body)
    timeline.add(solids.MirrorBody(plane="YZ", object_type="Features", source_feature="Loch"))
    assert timeline.errors == {}
    hole = 40 * 20 * 10 - single
    assert solids._volume(timeline.state.body) == pytest.approx(single - hole, rel=1e-6)
    # das gespiegelte Loch liegt bei x = −10
    from wolfcad.core import features as ft2
    assert any(abs(ft2.face_point(face)[0] + 10) < 3.1 for face in ft2.faces_of(timeline.state.body)
               if abs(ft2.face_point(face)[2] - 5) < 5)


def test_move_point_to_point():
    timeline = Timeline()
    timeline.add(solids.Box(length=10, width=10, height=10))
    timeline.add(solids.MoveBody(move_type="Punkt zu Punkt", from_point=[5, 5, 10], to_point=[30, 0, 0]))
    assert timeline.errors == {}
    low_x, low_y, low_z, high_x, high_y, high_z = solids._bounds(timeline.state.body)
    assert (high_x, high_y, high_z) == pytest.approx((30, 0, 0), abs=1e-5)     # Ecke (5, 5, 10) liegt jetzt auf (30, 0, 0)
