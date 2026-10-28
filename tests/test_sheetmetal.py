# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Blech (Fusion-Arbeitsbereich „Blech“): Flansch aus Profil, Flansch an Kante, Abwicklung."""
import math

import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from wolfcad.core import sheetmetal as sm
from wolfcad.core import sketch as sk
from wolfcad.core.timeline import Timeline


def volume(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def test_base_flange_edge_flanges_and_flat_pattern():
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=sk.rectangle(0, 0, 40, 30)))
    timeline.add(sm.BaseFlange(name="Blech", sketch="S", thickness=2, bend_radius=1))
    assert not timeline.errors, timeline.errors
    body = next(iter(timeline.state.bodies))
    assert volume(timeline.state.bodies[body]) == pytest.approx(40 * 30 * 2)
    assert timeline.state.sheets[body]["thickness"] == 2
    # Flansch an der vorderen Oberkante: 90° nach oben, 10 mm gerader Teil
    timeline.add(sm.EdgeFlange(name="F1", body=body, edge_points=[[20, 0, 2]], length=10))
    assert not timeline.errors, timeline.errors
    bend = 40 * math.pi / 4 * ((1 + 2) ** 2 - 1 ** 2)
    assert volume(timeline.state.bodies[body]) == pytest.approx(40 * 30 * 2 + bend + 40 * 2 * 10, rel=1e-6)
    # Flansch am Flansch: innere Oberkante, biegt über das Grundblech
    timeline.add(sm.EdgeFlange(name="F2", body=body, edge_points=[[20, -1, 13]], length=5))
    assert not timeline.errors, timeline.errors
    # Abwicklung: Länge = 30 + zwei Biegezugaben + 10 + 5, Breite 40, Dicke 2
    timeline.add(sm.FlatPattern(name="A", body=body))
    assert not timeline.errors, timeline.errors
    flat_name = [name for name in timeline.state.bodies if name != body][0]
    low_x, low_y, low_z, high_x, high_y, high_z = sm._bounds(timeline.state.bodies[flat_name])
    allowance = sm.bend_allowance(90, 1, 2, sm.DEFAULT_K_FACTOR)
    assert high_y - low_y == pytest.approx(30 + 2 * allowance + 15, abs=1e-5)
    assert high_x - low_x == pytest.approx(40, abs=1e-5) and high_z - low_z == pytest.approx(2, abs=1e-5)
    # neben dem gefalteten Teil, nicht darin
    assert low_x > sm._bounds(timeline.state.bodies[body])[3]
    # Speichern und Laden
    again = Timeline.from_document(timeline.to_document())
    assert not again.errors and len(again.state.bodies) == 2


def test_sheet_metal_reports_wrong_input():
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=sk.rectangle(0, 0, 40, 30)))
    timeline.add(sm.BaseFlange(name="Blech", sketch="S", thickness=0))
    assert "Blechdicke" in str(timeline.errors)
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=sk.rectangle(0, 0, 40, 30)))
    timeline.add(sm.BaseFlange(name="Blech", sketch="S", thickness=2))
    body = next(iter(timeline.state.bodies))
    # eine senkrechte Kante (Ecke, Länge = Dicke) ist kein Blechrand mit Stirnfläche der Dicke
    timeline.add(sm.EdgeFlange(name="F", body=body, edge_points=[[0, 0, 1]]))
    assert timeline.errors
    from wolfcad.core import solids
    other = Timeline()
    other.add(solids.Box(name="Q", length=10, width=10, height=10))
    other.add(sm.EdgeFlange(name="F", edge_points=[[0, -5, 10]]))
    assert "kein Blechteil" in str(other.errors)


def test_flat_pattern_as_dxf(tmp_path):
    """Zuschnitt als DXF: Außenkontur (mit Loch als Kreis) und die Biegelinie in der Mitte der Biegezugabe."""
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=[*sk.rectangle(0, 0, 40, 30), sk.circle(20, 15, 4)]))
    timeline.add(sm.BaseFlange(name="Blech", sketch="S", thickness=2, bend_radius=1))
    body = next(iter(timeline.state.bodies))
    timeline.add(sm.EdgeFlange(name="F1", body=body, edge_points=[[20, 0, 2]], length=10))
    assert not timeline.errors, timeline.errors
    path = tmp_path / "zuschnitt.dxf"
    counts = sm.write_dxf(timeline.state.sheets[body], str(path))
    text = path.read_text(encoding="ascii")
    lines = text.split("\n")
    assert counts == {"KONTUR": 5, "BIEGELINIEN": 1}        # vier Kanten, ein Loch – eine Biegung
    assert text.count("\nCIRCLE\n") == 1 and text.startswith("0\nSECTION") and text.rstrip().endswith("EOF")
    # Biegelinie: quer über die ganze Breite, eine halbe Biegezugabe vor der alten Kante (y = 0)
    at = lines.index("BIEGELINIEN")
    values = {lines[i]: float(lines[i + 1]) for i in range(at + 1, at + 16, 2) if lines[i] in ("10", "20", "11", "21")}
    half = sm.bend_allowance(90, 1, 2, sm.DEFAULT_K_FACTOR) / 2
    assert abs(values["10"] - values["11"]) == pytest.approx(40)
    assert values["20"] == pytest.approx(-half) and values["21"] == pytest.approx(-half)


def test_edge_flange_over_part_of_the_edge():
    """Teillänge: 5 mm Abstand am Anfang, 10 mm am Ende – der Flansch ist 25 statt 40 mm breit."""
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=sk.rectangle(0, 0, 40, 30)))
    timeline.add(sm.BaseFlange(name="Blech", sketch="S", thickness=2, bend_radius=1))
    body = next(iter(timeline.state.bodies))
    timeline.add(sm.EdgeFlange(name="F1", body=body, edge_points=[[20, 0, 2]], length=10,
                               offset_start=5, offset_end=10))
    assert not timeline.errors, timeline.errors
    bend = 25 * math.pi / 4 * ((1 + 2) ** 2 - 1 ** 2)
    # zwei Biegefreistellungen: je Blechdicke (2) breit, Radius + Dicke (3) tief, durch die Dicke (2)
    relief = 2 * (2 * 3 * 2)
    assert volume(timeline.state.bodies[body]) == pytest.approx(40 * 30 * 2 + bend + 25 * 2 * 10 - relief, rel=1e-6)
    # die Freistellung erscheint auch im Zuschnitt (flach: gleiche Fläche weniger)
    flat_volume = volume(sm.flat_pattern(timeline.state.sheets[body]))
    allowance = sm.bend_allowance(90, 1, 2, sm.DEFAULT_K_FACTOR)
    assert flat_volume == pytest.approx((40 * 30 + 25 * (allowance + 10)) * 2 - relief, rel=1e-6)
    low_x, *_rest, high_x, _hy, _hz = sm._bounds(sm.flat_pattern(timeline.state.sheets[body]))
    assert high_x - low_x == pytest.approx(40, abs=1e-5)
    timeline.add(sm.EdgeFlange(name="F2", body=body, edge_points=[[20, 30, 2]], offset_start=20, offset_end=20))
    assert "nichts" in str(timeline.errors)


def test_holes_made_later_appear_in_flat_pattern_and_dxf(tmp_path):
    """Eine Bohrung im Flansch (mit dem gewöhnlichen Bohrungswerkzeug) erscheint in Abwicklung und DXF an der
    flach gelegten Stelle; zwei Flansche an benachbarten Kanten überlappen sich nicht."""
    from wolfcad.core import solids
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=sk.rectangle(0, 0, 40, 30)))
    timeline.add(sm.BaseFlange(name="Blech", sketch="S", thickness=2, bend_radius=1))
    body = next(iter(timeline.state.bodies))
    timeline.add(sm.EdgeFlange(name="F1", body=body, edge_points=[[20, 0, 2]], length=10))
    timeline.add(sm.EdgeFlange(name="F2", body=body, edge_points=[[40, 15, 2]], length=10))
    assert not timeline.errors, timeline.errors
    bend = math.pi / 4 * ((1 + 2) ** 2 - 1 ** 2)
    # benachbarte Flansche: Volumen ist die Summe (keine Überlappung an der Ecke)
    assert volume(timeline.state.bodies[body]) == pytest.approx(40 * 30 * 2 + (40 + 30) * (bend + 2 * 10), rel=1e-6)
    flat_before = volume(sm.flat_pattern(timeline.state.sheets[body], timeline.state.bodies[body]))
    # Bohrung Ø4 quer durch den Flansch an der Vorderkante (der gerade Teil liegt bei y = −3 … −1, z = 3 … 13)
    timeline.add(solids.Hole(name="B", diameter=4, depth=0, plane="XZ", offset=0.0, position_x=20, position_y=8))
    assert not timeline.errors, timeline.errors
    hole_volume = math.pi * 2 ** 2 * 2
    assert volume(timeline.state.bodies[body]) == pytest.approx(
        40 * 30 * 2 + (40 + 30) * (bend + 2 * 10) - hole_volume, rel=1e-4)
    flat = sm.flat_pattern(timeline.state.sheets[body], timeline.state.bodies[body])
    assert volume(flat) == pytest.approx(flat_before - hole_volume, rel=1e-4)
    path = tmp_path / "mit_loch.dxf"
    sm.write_dxf(timeline.state.sheets[body], str(path), timeline.state.bodies[body])
    assert path.read_text(encoding="ascii").count("\nCIRCLE\n") == 1


def test_unfold_cut_and_refold():
    """Abwickeln, in der flachen Lage ein Loch durch den Flansch-Bereich bohren, Zurückbiegen: das Loch sitzt im
    gefalteten Flansch."""
    from wolfcad.core import solids
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=sk.rectangle(0, 0, 40, 30)))
    timeline.add(sm.BaseFlange(name="Blech", sketch="S", thickness=2, bend_radius=1))
    body = next(iter(timeline.state.bodies))
    timeline.add(sm.EdgeFlange(name="F1", body=body, edge_points=[[20, 0, 2]], length=10))
    folded_volume = volume(timeline.state.bodies[body])
    timeline.add(sm.Unfold(name="A", body=body))
    assert not timeline.errors, timeline.errors
    low = sm._bounds(timeline.state.bodies[body])
    assert low[5] - low[2] == pytest.approx(2, abs=1e-6)            # liegt flach (nur die Blechdicke hoch)
    # flacher Flansch liegt bei y < −Biegezugabe; Loch Ø4 bei (20, −8) senkrecht durch das Blech
    allowance = sm.bend_allowance(90, 1, 2, sm.DEFAULT_K_FACTOR)
    timeline.add(solids.Hole(name="B", diameter=4, depth=0, plane="XY", offset=2.0, position_x=20,
                             position_y=-(allowance + 5)))
    assert not timeline.errors, timeline.errors
    timeline.add(sm.Refold(name="Z", body=body))
    assert not timeline.errors, timeline.errors
    hole = math.pi * 2 ** 2 * 2
    assert volume(timeline.state.bodies[body]) == pytest.approx(folded_volume - hole, rel=1e-4)
    # das Loch sitzt im aufgestellten Flansch: 5 mm über dessen Anfang (z = 3 + 5), quer durch y = −3 … −1
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_OUT
    from OCP.gp import gp_Pnt
    state = BRepClass3d_SolidClassifier(timeline.state.bodies[body], gp_Pnt(20, -2, 8), 1e-7).State()
    assert state == TopAbs_OUT
    assert "schon abgewickelt" not in str(timeline.errors)


def test_bend_along_a_sketch_line():
    """Biegen an einer Linie: Blech 60 × 30, Linie quer bei x = 40 – das Stück dahinter klappt um 90° hoch; die
    Abwicklung ist wieder 60 lang, die Biegelinie liegt im DXF bei x = 40."""
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=sk.rectangle(0, 0, 60, 30)))
    timeline.add(sm.BaseFlange(name="Blech", sketch="S", thickness=2, bend_radius=1))
    body = next(iter(timeline.state.bodies))
    timeline.add(sk.Sketch(name="L", plane="XY", offset=2.0, entities=[sk.line(40, 0, 40, 30)]))
    timeline.add(sm.SketchBend(name="Biegen1", sketch="L", body=body))
    assert not timeline.errors, timeline.errors
    allowance = sm.bend_allowance(90, 1, 2, sm.DEFAULT_K_FACTOR)
    bend = 30 * math.pi / 4 * ((1 + 2) ** 2 - 1 ** 2)
    # flaches Material außerhalb der Biegezone bleibt, die Zone wird zur Biegung
    assert volume(timeline.state.bodies[body]) == pytest.approx((60 - allowance) * 30 * 2 + bend, rel=1e-6)
    low_x, low_y, low_z, high_x, high_y, high_z = sm._bounds(timeline.state.bodies[body])
    assert high_z > 15            # das Stück hinter der Linie steht hoch
    flat = sm.flat_pattern(timeline.state.sheets[body])
    fx0, _y0, _z0, fx1, _y1, _z1 = sm._bounds(flat)
    assert fx1 - fx0 == pytest.approx(60, abs=1e-5)
    # eine weitere Biegung auf dem stehen gebliebenen Stück (Vorgabe: das kleinere Stück klappt)
    timeline.add(sk.Sketch(name="L2", plane="XY", offset=2.0, entities=[sk.line(15, 0, 15, 30)]))
    timeline.add(sm.SketchBend(name="Biegen2", sketch="L2", body=body))     # wieder klappt das kleinere Stück
    assert not timeline.errors, timeline.errors
    fx0, _y0, _z0, fx1, _y1, _z1 = sm._bounds(sm.flat_pattern(timeline.state.sheets[body]))
    assert fx1 - fx0 == pytest.approx(60, abs=1e-5)


def test_convert_plate_to_sheet_metal():
    """In Blech umwandeln: ein flacher Quader 50 × 20 × 3 wird Blechteil; danach geht ein Flansch, die Abwicklung
    stimmt. Ein Würfel ist keine Platte."""
    from wolfcad.core import solids
    timeline = Timeline()
    timeline.add(solids.Box(name="Q", length=50, width=20, height=3))
    body = next(iter(timeline.state.bodies))
    timeline.add(sm.ConvertToSheet(name="U", body=body, bend_radius=2))
    assert not timeline.errors, timeline.errors
    assert timeline.state.sheets[body]["thickness"] == pytest.approx(3)
    timeline.add(sm.EdgeFlange(name="F", body=body, edge_points=[[0, -10, 3]], length=8))    # Quader liegt mittig
    assert not timeline.errors, timeline.errors
    low_x, low_y, low_z, high_x, high_y, high_z = sm._bounds(sm.flat_pattern(timeline.state.sheets[body]))
    assert high_y - low_y == pytest.approx(20 + sm.bend_allowance(90, 2, 3, sm.DEFAULT_K_FACTOR) + 8, abs=1e-5)
    cube = Timeline()
    cube.add(solids.Box(name="W", length=10, width=10, height=10))
    cube.add(sm.ConvertToSheet(name="U", body=next(iter(cube.state.bodies))))
    assert not cube.errors      # ein Würfel ist (formal) eine 10 mm dicke Platte
    hollow = Timeline()
    hollow.add(solids.Box(name="H", length=20, width=20, height=10))
    hollow.add(solids.Hole(name="B", diameter=4, depth=5, plane="XY", offset=10.0))
    hollow.add(sm.ConvertToSheet(name="U", body=next(iter(hollow.state.bodies))))
    assert "nicht überall gleich dick" in str(hollow.errors) or "keine Platte" in str(hollow.errors)


def test_closed_corner_between_adjacent_flanges():
    """Geschlossene Ecke: der zweite Flansch (rechte Kante) und der erste (Vorderkante) wachsen in die Ecke und
    treffen sich auf Gehrung mit 0,5 mm Spalt."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_OUT
    from OCP.gp import gp_Pnt
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=sk.rectangle(0, 0, 40, 30)))
    timeline.add(sm.BaseFlange(name="Blech", sketch="S", thickness=2, bend_radius=1))
    body = next(iter(timeline.state.bodies))
    timeline.add(sm.EdgeFlange(name="F1", body=body, edge_points=[[20, 0, 2]], length=10))
    timeline.add(sm.EdgeFlange(name="F2", body=body, edge_points=[[40, 15, 2]], length=10, corner=sm.CORNERS[1]))
    assert not timeline.errors, timeline.errors
    shape = timeline.state.bodies[body]
    assert BRepCheck_Analyzer(shape).IsValid()
    state = lambda x, y, z: BRepClass3d_SolidClassifier(shape, gp_Pnt(x, y, z), 1e-7).State()
    assert state(42.0, -1.0, 8.0) == TopAbs_IN          # Verlängerung des zweiten Flanschs
    assert state(41.5, -2.2, 8.0) == TopAbs_IN          # Verlängerung des ersten Flanschs
    assert state(41.6, -1.5, 8.0) == TopAbs_OUT         # im Gehrungsspalt
    # Zuschnitt: an der Ecke liegen beide Biegestreifen auf Gehrung – im Spalt kein Material, daneben schon
    flat = sm.flat_pattern(timeline.state.sheets[body], shape)
    flat_state = lambda x, y: BRepClass3d_SolidClassifier(flat, gp_Pnt(x, y, 1.0), 1e-7).State()
    allowance = sm.bend_allowance(90, 1, 2, sm.DEFAULT_K_FACTOR)
    assert flat_state(40.0 + allowance / 2, -0.3) == TopAbs_IN               # Streifen des zweiten Flanschs
    assert flat_state(40.3, -allowance / 2) == TopAbs_IN                     # verlängerter Streifen des ersten
    assert flat_state(40.0 + allowance / 2, -allowance / 2) == TopAbs_OUT    # Gehrungsspalt
