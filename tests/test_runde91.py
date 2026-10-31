"""Runde 91: Rundung und Fase mit den Fusion-Funktionen (Auswahlsätze, Regel- und volle Rundung, Radiusarten)."""
import math

import pytest

from wolfcad.core import features as ft, sketch as sk, solids
from wolfcad.core.timeline import Timeline

# Quader 40 × 30 × 20, mittig um den Ursprung: Oberkanten bei z = 20
TOP_FRONT, TOP_BACK, TOP_LEFT = [0, -15, 20], [0, 15, 20], [-20, 0, 20]


def _box(*steps) -> Timeline:
    timeline = Timeline()
    timeline.add(solids.Box(length=40, width=30, height=20))
    for step in steps:
        timeline.add(step)
    return timeline


def _volume(timeline: Timeline) -> float:
    return solids._volume(timeline.state.bodies["Körper1"])


def _removed(radius: float, length: float) -> float:
    """Material, das eine Rundung an einer geraden 90°-Kante wegnimmt."""
    return (1 - math.pi / 4) * radius * radius * length


def test_sets_round_each_edge_with_own_radius():
    one = _box(ft.Fillet(radius=2, edge_points=[TOP_FRONT], sets=[{"radius": 5, "edge_points": [TOP_LEFT]}]))
    assert one.errors == {}
    # Ecke, an der sich beide Rundungen treffen, ändert das Volumen nur wenig
    expected = 24000 - _removed(2, 40) - _removed(5, 30)
    assert abs(_volume(one) - expected) < 15


def test_empty_active_set_after_plus_is_no_error():
    # nach „+“ steht der neue Satz leer bei 0 – der fertige Satz wirkt trotzdem
    timeline = _box(ft.Fillet(radius=0, require_pick=True, sets=[{"radius": 3, "edge_points": [TOP_FRONT]}]))
    assert timeline.errors == {}
    assert abs(_volume(timeline) - (24000 - _removed(3, 40))) < 1


def test_chord_length_gives_radius_over_sqrt2_at_right_angle():
    chord = ft.Fillet(radius=4, radius_type="Sehnenlänge", edge_points=[TOP_FRONT])
    plain = ft.Fillet(radius=4 / math.sqrt(2), edge_points=[TOP_FRONT])
    assert abs(_volume(_box(chord)) - _volume(_box(plain))) < 1e-3


def test_variable_radius_with_middle_point():
    timeline = _box(ft.Fillet(radius=2, radius2=2, radius_type="Variabel", mid_radius=6, mid_position=0.5,
                              edge_points=[TOP_FRONT]))
    assert timeline.errors == {}
    volume = _volume(timeline)
    # mehr als überall 2 mm, weniger als überall 6 mm
    assert 24000 - _removed(6, 40) < volume < 24000 - _removed(2, 40)


def test_rule_fillet_rounds_only_outer_or_inner_edges():
    # L-Profil: eine Innenkante (Kehle), sonst Außenkanten
    def l_profile(**options) -> Timeline:
        timeline = Timeline()
        timeline.add(sk.Sketch(name="S", entities=[sk.line(0, 0, 40, 0), sk.line(40, 0, 40, 10),
                                                   sk.line(40, 10, 10, 10), sk.line(10, 10, 10, 30),
                                                   sk.line(10, 30, 0, 30), sk.line(0, 30, 0, 0)]))
        timeline.add(ft.Extrude(sketch="S", distance=15))
        timeline.add(ft.Fillet(radius=2, fillet_type="Regelverrundung", **options))
        return timeline
    base = 40 * 10 * 15 + 10 * 20 * 15
    inner = l_profile(rule_rounds=False, rule_fillets=True)
    assert inner.errors == {}
    # eine Kehle fügt Material an der Innenkante hinzu (15 mm lang)
    assert abs(solids._volume(inner.state.bodies["Körper1"]) - (base + _removed(2, 15))) < 1
    outer = l_profile(rule_rounds=True, rule_fillets=False)
    assert outer.errors == {} and solids._volume(outer.state.bodies["Körper1"]) < base
    with pytest.raises(AssertionError):
        assert l_profile(rule_rounds=False, rule_fillets=False).errors == {}


def test_rule_fillet_on_chosen_faces():
    timeline = _box(ft.Fillet(radius=2, fillet_type="Regelverrundung", rule_scope="Kanten der gewählten Flächen",
                              face_points=[[0, 0, 20]]))
    assert timeline.errors == {}
    # vier Oberkanten (2 × 40 + 2 × 30), die Ecken sind Kugelstücke
    assert abs(_volume(timeline) - (24000 - _removed(2, 140))) < 15


def test_full_round_over_rib_top():
    rib = Timeline()
    rib.add(solids.Box(length=40, width=6, height=20))
    rib.add(ft.Fillet(fillet_type="Volle Rundung", radius=0, face_points=[[0, 0, 20]]))
    assert rib.errors == {}
    body = rib.state.bodies["Körper1"]
    bounds = solids._bounds(body)
    assert abs(bounds[5] - 20) < 1e-4                      # Höhe bleibt (Hüllquader mit Toleranz)
    # oben ein Halbzylinder mit Radius 3: Volumen = Quader bis 17 + halber Zylinder
    expected = 40 * 6 * 17 + math.pi * 9 / 2 * 40
    assert abs(solids._volume(body) - expected) < 1


def test_chamfer_sets():
    timeline = _box(ft.Chamfer(distance=1, edge_points=[TOP_FRONT], sets=[{"distance": 3, "edge_points": [TOP_BACK]}]))
    assert timeline.errors == {}
    assert abs(_volume(timeline) - (24000 - 0.5 * 40 - 4.5 * 40)) < 1e-6


def test_old_drafts_with_end_radius_stay_variable():
    data = ft.Fillet(radius=1, radius2=3, edge_points=[TOP_FRONT]).to_dict()
    data.pop("radius_type")
    assert ft.feature_from_dict(data).radius_type == "Variabel"


def test_switching_back_to_constant_survives_saving():
    feature = ft.Fillet(radius=1, radius2=3, edge_points=[TOP_FRONT])
    assert feature.radius_type == "Variabel"
    feature.radius_type = "Konstant"
    assert ft.feature_from_dict(feature.to_dict()).radius_type == "Konstant"


def test_moved_edge_is_followed_in_every_set():
    # eine höhere Extrusion davor verschiebt die Oberkanten: beide Sätze finden ihre Kante wieder
    timeline = Timeline()
    timeline.add(solids.Box(name="Q", length=40, width=30, height=20))
    timeline.add(ft.Fillet(radius=2, edge_points=[[0, -15, 20]], sets=[{"radius": 4, "edge_points": [[0, 15, 20]]}]))
    timeline.update(0, height=25)
    assert timeline.errors == {}
    fillet = timeline.features[1]
    assert fillet.edge_points[0][2] == pytest.approx(25) and fillet.sets[0]["edge_points"][0][2] == pytest.approx(25)
