"""Runde 93: Probelauf liefert sein Ergebnis mit (keine doppelte Rechnung), dünne Extrusion, Verjüngung je Seite."""
import math

import pytest

from wolfcad.core import features as ft, guard, sketch as sk, solids
from wolfcad.core.timeline import Timeline


@pytest.fixture
def crash_guard():
    if not guard.available():
        pytest.skip("Abspalten von Prozessen gibt es hier nicht")
    Timeline.crash_guard = True
    yield
    Timeline.crash_guard = False


def _ribbed() -> Timeline:
    timeline = Timeline()
    timeline.add(solids.Box(length=40, width=30, height=20))
    for number in range(3):
        timeline.add(solids.Box(center_x=-10 + 10 * number, length=3, width=40, height=26, operation="Vereinigen"))
    timeline.add(ft.Fillet(radius=0.8))
    return timeline


def test_trial_result_is_taken_over_and_matches(crash_guard, monkeypatch):
    import wolfcad.core.timeline as tl
    taken = []
    original = tl._unpack_trial
    monkeypatch.setattr(tl, "_unpack_trial", lambda *args: taken.append(original(*args)) or taken[-1])
    guarded = _ribbed()
    assert guarded.errors == {} and any(taken)        # die Rundung kam aus dem Probelauf
    Timeline.crash_guard = False
    plain = _ribbed()
    assert abs(solids._volume(guarded.state.body) - solids._volume(plain.state.body)) < 1e-6
    assert len(ft.faces_of(guarded.state.body)) == len(ft.faces_of(plain.state.body))


def test_followed_edge_points_come_back_from_trial(crash_guard):
    timeline = Timeline()
    timeline.add(solids.Box(length=40, width=30, height=20))
    timeline.add(ft.Fillet(radius=2, edge_points=[[0, -15, 20]], sets=[{"radius": 4, "edge_points": [[0, 15, 20]]}]))
    timeline.update(0, height=25)
    fillet = timeline.features[1]
    assert timeline.errors == {}
    assert fillet.edge_points[0][2] == pytest.approx(25) and fillet.sets[0]["edge_points"][0][2] == pytest.approx(25)


@pytest.mark.parametrize("side, outer, inner", [("Innen", 0, 2), ("Außen", 2, 0), ("Mitte", 1, 1)])
def test_thin_extrude_circle(side, outer, inner):
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=[sk.circle(0, 0, 10)]))
    timeline.add(ft.Extrude(sketch="S", distance=10, extrude_type="Dünn", wall=2, wall_side=side))
    assert timeline.errors == {}
    expected = math.pi * ((10 + outer) ** 2 - (10 - inner) ** 2) * 10
    assert solids._volume(timeline.state.body) == pytest.approx(expected, rel=1e-6)


def test_thin_extrude_rectangle_keeps_sharp_inner_corners():
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=sk.rectangle(0, 0, 40, 20)))
    timeline.add(ft.Extrude(sketch="S", distance=10, extrude_type="Dünn", wall=2))
    assert solids._volume(timeline.state.body) == pytest.approx((800 - 36 * 16) * 10, rel=1e-9)


def test_taper_per_side():
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=sk.rectangle(0, 0, 40, 20)))
    timeline.add(ft.Extrude(sketch="S", distance=10, extent="Zwei Seiten", distance2=10, taper=10, taper2=-10))
    low_x, _y, low_z, high_x, _Y, high_z = solids._bounds(timeline.state.body)
    grow = 10 * math.tan(math.radians(10))
    assert low_z == pytest.approx(-10, abs=1e-4) and high_z == pytest.approx(10, abs=1e-4)
    assert low_x == pytest.approx(-grow, abs=1e-3) and high_x == pytest.approx(40 + grow, abs=1e-3)


def test_every_tool_from_round93_has_a_handle():
    """Runde 93: Torus, Spule, Rohr, Prägen, Steg, Skalieren, Fläche extrudieren/versetzen, Aufdicken, Ebene entlang
    Pfad haben wie in Fusion einen Ziehpfeil; Wert × Faktor ist das Feldmaß."""
    from wolfcad.core import extras as ex, more_tools as mt, surfaces as sf
    from wolfcad.gui import handles

    def handles_of(*steps):
        timeline = Timeline()
        for step in steps:
            timeline.add(step)
        index = len(timeline.features) - 1
        return timeline, handles.handles_for(timeline.features[index], timeline.state_before(index), timeline.state)

    box = lambda: solids.Box(length=40, width=30, height=20)
    path = lambda: sk.Sketch(name="P", plane="XZ", entities=[sk.line(-30, 0, -30, 40)])
    cases = [
        ((ex.Torus(diameter=40, section=10),), {"diameter": 40, "section": 10}),
        ((ex.Coil(diameter=30, pitch=8, turns=5),), {"diameter": 30, "turns": 5}),
        ((path(), ex.Pipe(path="P", diameter=10)), {"diameter": 10}),
        ((box(), sk.Sketch(name="S", offset=20, entities=[sk.circle(0, 0, 5)]), mt.Emboss(sketch="S", depth=2, body="Körper1", face_points=[[10, 10, 20]])),
         {"depth": 2}),
        ((box(), solids.ScaleBody(factor=1.5)), {"factor": 1.5}),
        ((path(), sf.SurfaceExtrude(sketch="P", distance=12)), {"distance": 12}),
        ((path(), sf.SurfaceExtrude(name="F", sketch="P", distance=12), sf.OffsetSurface(surface="Flächenkörper1", distance=3)),
         {"distance": 3}),
        ((path(), mt.PlaneAlongPath(path="P", position=0.25)), {"position": 0.25}),
    ]
    for steps, expected in cases:
        timeline, found = handles_of(*steps)
        by_name = {handle.parameter: handle.value * handle.factor for handle in found}
        assert timeline.errors == {}, (steps[-1].KIND, timeline.errors)
        for name, value in expected.items():
            assert name in by_name, (steps[-1].KIND, list(by_name))
            assert by_name[name] == pytest.approx(value), (steps[-1].KIND, name)


def test_loft_with_four_profiles_closed_and_ruled():
    timeline = Timeline()
    for number, (z, size) in enumerate(((0, 20), (10, 14), (20, 20), (30, 10))):
        timeline.add(sk.Sketch(name=f"P{number}", offset=z, entities=sk.rectangle(-size / 2, -size / 2, size / 2, size / 2)))
    timeline.add(solids.Loft(sketch="P0", sketch2="P1", sketch3="P2", more=["P3"], ruled=True))
    assert timeline.errors == {}
    low_z, high_z = solids._bounds(timeline.state.body)[2], solids._bounds(timeline.state.body)[5]
    assert low_z == pytest.approx(0, abs=1e-6) and high_z == pytest.approx(30, abs=1e-6)
    # gerade Übergänge: Volumen = Summe der Pyramidenstümpfe
    frustum = lambda h, a, b: h / 3 * (a * a + a * b + b * b)
    assert solids._volume(timeline.state.body) == pytest.approx(
        frustum(10, 20, 14) + frustum(10, 14, 20) + frustum(10, 20, 10), rel=1e-6)


def test_trial_error_is_reported_without_second_computation(crash_guard, monkeypatch):
    calls = []
    original = ft.Fillet.apply

    def counting(self, state):
        calls.append(1)             # im Kind gezählt bleibt hier unsichtbar: nur Aufrufe im Elternprozess
        return original(self, state)
    monkeypatch.setattr(ft.Fillet, "apply", counting)
    timeline = Timeline()
    timeline.add(solids.Box(length=10, width=10, height=10))
    timeline.add(ft.Fillet(radius=30))      # zu groß: scheitert
    assert "zu groß" in timeline.errors[1]
    assert calls == []                      # der Fehler kam aus dem Probelauf
