# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für die freie Skizze: Profile aus Linien, Kreisen und Bögen."""

import math

import numpy as np

import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from wolfcad.core import sketch as sk
from wolfcad.core.features import Extrude
from wolfcad.core.solids import Revolve
from wolfcad.core.timeline import Timeline


def volume(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def extruded(entities: list, distance: float = 10) -> Timeline:
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=entities))
    timeline.add(Extrude(name="E", sketch="S", distance=distance))
    return timeline


def test_rectangle_from_four_lines_becomes_a_profile():
    timeline = extruded(sk.rectangle(0, 0, 40, 20))
    assert not timeline.errors
    assert volume(timeline.state.body) == pytest.approx(8000)


def test_circle_inside_rectangle_is_a_hole_by_default():
    entities = [*sk.rectangle(0, 0, 40, 20), sk.circle(20, 10, 5)]
    timeline = extruded(entities)
    assert not timeline.errors
    assert volume(timeline.state.body) == pytest.approx(8000 - math.pi * 25 * 10, rel=1e-6)

    # "Alle Bereiche" füllt auch die Kreisscheibe: zusammen das volle Rechteck
    filled = Timeline()
    filled.add(sk.Sketch(name="S", entities=entities))
    filled.add(Extrude(name="E", sketch="S", distance=10, regions="Alle Bereiche"))
    assert volume(filled.state.body) == pytest.approx(8000)


def test_island_inside_a_hole_is_material_again():
    # Rechteck, darin ein großer Kreis (Loch), darin ein kleiner Kreis (wieder Material)
    timeline = extruded([*sk.rectangle(0, 0, 40, 40), sk.circle(20, 20, 15), sk.circle(20, 20, 5)])
    assert not timeline.errors
    assert volume(timeline.state.body) == pytest.approx((1600 - math.pi * 225 + math.pi * 25) * 10, rel=1e-6)


def test_sketch_on_a_stored_face_frame_and_hole():
    from wolfcad.core.solids import Box, Hole

    # Lage der Deckfläche eines 40 x 20 x 10 Quaders: Ursprung in ihrer Mitte, Normale +Z
    frame = [0, 0, 10, 0, 0, 1, 1, 0, 0]
    timeline = Timeline()
    timeline.add(Box(length=40, width=20, height=10))
    timeline.add(sk.Sketch(name="S", frame=frame, entities=[sk.circle(0, 0, 4)]))
    timeline.add(Extrude(name="Zapfen", sketch="S", distance=6, operation="Vereinigen"))
    assert not timeline.errors
    assert volume(timeline.state.body) == pytest.approx(8000 + math.pi * 16 * 6, rel=1e-6)

    timeline.add(Hole(frame=frame, position_x=12, diameter=4))
    assert not timeline.errors
    assert volume(timeline.state.body) == pytest.approx(8000 + math.pi * 16 * 6 - math.pi * 4 * 10, rel=1e-6)

    blind = Timeline()
    blind.add(Box(length=40, width=20, height=10))
    blind.add(Hole(frame=frame, diameter=4, depth=3))
    assert volume(blind.state.body) == pytest.approx(8000 - math.pi * 4 * 3, rel=1e-6)


def test_crossing_lines_are_split_into_regions():
    # Rechteck mit einer Linie, die an beiden Seiten übersteht: zwei Hälften, zusammen das Rechteck
    timeline = extruded([*sk.rectangle(0, 0, 40, 20), sk.line(20, -5, 20, 25)])
    assert not timeline.errors
    assert volume(timeline.state.body) == pytest.approx(8000)


def test_arc_closes_a_profile():
    # Halbkreis: Durchmesser als Linie, Bogen von 0 bis 180 Grad
    timeline = extruded([sk.line(-10, 0, 10, 0), sk.arc(0, 0, 10, 0, 180)])
    assert not timeline.errors
    assert volume(timeline.state.body) == pytest.approx(math.pi * 100 / 2 * 10, rel=1e-6)


def test_open_curves_give_a_clear_error_on_extrude():
    timeline = extruded([sk.line(0, 0, 10, 0), sk.line(10, 0, 10, 10)])
    assert "kein geschlossenes Profil" in timeline.errors[1]


def test_invalid_entities_are_rejected():
    assert sk.check_entity(sk.line(1, 1, 1, 1)) == "Linie ohne Länge"
    assert "Radius" in sk.check_entity(sk.circle(0, 0, 0))
    assert sk.check_entity(["Ellipse", 1, 2]) is not None
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=[sk.circle(0, 0, -1)]))
    assert "Radius" in timeline.errors[0]


def test_sketch_on_other_plane_revolves_and_survives_saving(tmp_path):
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", plane="XZ", entities=sk.rectangle(8, 0, 12, 10)))
    timeline.add(Revolve(name="R", sketch="S"))
    assert not timeline.errors
    assert volume(timeline.state.body) == pytest.approx(math.pi * (144 - 64) * 10, rel=1e-6)
    path = tmp_path / "skizze.wcad"
    timeline.save(path)
    loaded = Timeline.load(path)
    assert loaded.features[0].entities == timeline.features[0].entities
    assert volume(loaded.state.body) == pytest.approx(volume(timeline.state.body))


def test_chosen_regions_are_extruded_and_tracked_by_their_inner_point():
    entities = [*sk.rectangle(0, 0, 40, 20), sk.circle(20, 10, 5)]
    probe = Timeline()
    probe.add(sk.Sketch(name="S", entities=entities))
    regions = sk.regions_2d(probe.state.profiles["S"], probe.state.frames["S"])
    assert len(regions) == 2 and all(region["triangles"] and region["outline"] for region in regions)
    # Die Kreisscheibe ist der Bereich mit weniger Dreiecksfläche um die Mitte: über den Innenpunkt wählen
    disc = min(regions, key=lambda region: abs(region["point"][0] - 20) + abs(region["point"][1] - 10))

    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=entities))
    timeline.add(Extrude(name="E", sketch="S", distance=10, regions="Gewählte Bereiche",
                         region_points=[list(disc["point"])]))
    assert not timeline.errors
    assert volume(timeline.state.body) == pytest.approx(math.pi * 25 * 10, rel=1e-6)

    # Wird der Kreis weit verschoben, liegt am gemerkten Punkt der Rahmen: die Wahl folgt der Stelle
    timeline.update(0, entities=[*sk.rectangle(0, 0, 40, 20), sk.circle(200, 200, 5)])
    assert not timeline.errors
    # Liegt dort gar kein Bereich mehr, wird das gemeldet
    timeline.update(0, entities=sk.rectangle(100, 100, 140, 120))
    assert "nicht mehr vorhanden" in timeline.errors[1]


def test_sketch_follows_its_construction_plane():
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import Extrude
    from wolfcad.core.timeline import Timeline
    timeline = Timeline()
    timeline.add(sk.ConstructionPlane(name="Ebene1", plane="XY", offset=25))
    timeline.add(sk.Sketch(name="Skizze1", base="Ebene1", entities=sk.rectangle(0, 0, 10, 10)))
    timeline.add(Extrude(sketch="Skizze1", distance=5))
    assert not timeline.errors
    assert timeline.state.frames["Skizze1"].Location().Z() == 25
    assert "Ebene1" in timeline.state.planes and "Ebene1" not in timeline.state.frames
    # Ebene verschieben: Skizze und Körper wandern mit
    timeline.update(0, offset=40)
    assert timeline.state.frames["Skizze1"].Location().Z() == 40
    # Ohne Ebene meldet die Skizze einen Fehler statt irgendwo zu landen
    timeline.features[0].suppressed = True
    timeline.recompute()
    assert "Konstruktionsebene" in timeline.errors[1]
    restored = Timeline.from_document(timeline.to_document())
    assert restored.features[1].base == "Ebene1"


def test_polygon_slot_and_corner_fillet():
    from wolfcad.core import sketch as sk
    from wolfcad.gui.sketch_editor import has_profile
    hexagon = sk.polygon(0, 0, 10, 0, 6)
    assert len(hexagon) == 6 and all(math.dist(e[1:3], e[3:5]) == pytest.approx(10) for e in hexagon)
    assert has_profile(hexagon)
    slot = sk.slot(0, 0, 30, 0, 5)
    assert [e[0] for e in slot] == ["Linie", "Bogen", "Linie", "Bogen"] and has_profile(slot)
    # Rechtwinklige Ecke mit Radius 4: die Linien enden 4 mm vor der Ecke, der Bogen ist ein Viertelkreis
    first, second, arc = sk.corner_fillet(sk.line(0, 0, 20, 0), sk.line(20, 0, 20, 15), 4)
    assert first[3:5] == pytest.approx([16, 0]) and second[1:3] == pytest.approx([20, 4])
    assert arc[1:4] == pytest.approx([16, 4, 4]) and (arc[5] - arc[4]) % 360 == pytest.approx(90)
    assert has_profile([first, second, arc, sk.line(20, 15, 0, 15), sk.line(0, 15, 0, 0)])
    assert sk.corner_fillet(sk.line(0, 0, 20, 0), sk.line(20, 0, 20, 15), 40) is None     # zu groß
    assert sk.corner_fillet(sk.line(0, 0, 20, 0), sk.line(30, 0, 30, 15), 4) is None      # keine gemeinsame Ecke


def test_line_cuts_and_construction_lines():
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import BuildState
    entities = [sk.line(0, 0, 40, 0), sk.line(10, -5, 10, 5), sk.circle(30, 0, 5), sk.arc(20, 0, 2, 180, 360)]
    assert sk.line_cuts(entities, 0) == pytest.approx([0.25, 0.45, 0.55, 0.625, 0.875])
    # Konstruktionslinie: teilt das Rechteck nicht und gehört nicht zum Profil
    rectangle = sk.rectangle(0, 0, 20, 10) + [sk.line(10, 0, 10, 10)]
    split, whole = BuildState(), BuildState()
    sk.Sketch(name="S", entities=rectangle).apply(split)
    sk.Sketch(name="S", entities=rectangle, construction=[4]).apply(whole)
    from wolfcad.core.features import faces_of
    assert len(faces_of(split.profiles["S"])) == 2 and len(faces_of(whole.profiles["S"])) == 1


def test_mirror_and_offset():
    from wolfcad.core import sketch as sk
    from wolfcad.gui.sketch_editor import has_profile
    axis = sk.line(0, -10, 0, 10)                       # Spiegeln an der y-Achse
    assert sk.mirrored(sk.line(5, 0, 20, 8), axis)[1:] == pytest.approx([-5, 0, -20, 8])
    assert sk.mirrored(sk.circle(7, 3, 2), axis)[1:] == pytest.approx([-7, 3, 2])
    arc = sk.mirrored(sk.arc(10, 0, 5, 0, 90), axis)     # Viertelkreis rechts oben -> links oben
    assert arc[1:4] == pytest.approx([-10, 0, 5]) and (arc[4], arc[5]) == pytest.approx((90, 180))
    # Versatz eines Rechtecks nach außen und innen: geschlossen, Ecken neu verschnitten
    rectangle = sk.rectangle(0, 0, 40, 20)
    outer = sk.offset_chain(rectangle, 0, 5, (20, -3))
    xs = [v for e in outer for v in (e[1], e[3])]
    ys = [v for e in outer for v in (e[2], e[4])]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((-5, 45, -5, 25)) and has_profile(outer)
    inner = sk.offset_chain(rectangle, 2, 5, (20, 15))
    xs = [v for e in inner for v in (e[1], e[3])]
    assert (min(xs), max(xs)) == pytest.approx((5, 35)) and has_profile(inner)
    # Langloch (Linien und Bögen, tangential): bleibt beim Versatz geschlossen
    slot = sk.slot(0, 0, 30, 0, 5)
    grown = sk.offset_chain(slot, 0, 2, (15, 9))
    assert sorted(round(e[3], 6) for e in grown if e[0] == "Bogen") == [7, 7] and has_profile(grown)
    assert sk.offset_chain(slot, 0, 6, (15, 0)) is None             # Bögen würden verschwinden
    assert sk.offset_chain([sk.circle(0, 0, 10)], 0, 3, (0, 0))[0][3] == 7
    # Offener Winkel aus zwei Linien: zwei Linien, an der Ecke verschnitten
    bend = sk.offset_chain([sk.line(0, 0, 20, 0), sk.line(20, 0, 20, 15)], 0, 2, (10, 1))
    assert bend[0][1:] == pytest.approx([0, 2, 18, 2]) and bend[1][1:] == pytest.approx([18, 2, 18, 15])


def test_spline_entity():
    from wolfcad.core import constraints as cs
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import BuildState, Extrude
    from wolfcad.core.timeline import Timeline
    curve = sk.spline((0, 0), (10, 8), (20, 0), (30, 8))
    samples = sk.spline_points(curve)
    assert samples[0] == (0, 0) and samples[-1] == (30, 8) and len(samples) == 3 * 12 + 1
    for point in ((10, 8), (20, 0)):                       # die Kurve läuft durch die gesetzten Punkte
        assert min(math.dist(point, s) for s in samples) < 1e-9
    assert sk.spline_points(sk.spline((0, 0), (5, 5))) == [(0, 0), (5, 5)]
    assert sk.check_entity(curve) is None
    assert sk.check_entity(["Spline", 0, 0]) and sk.check_entity(sk.spline((0, 0), (0, 0), (1, 1)))
    # Geschlossenes Profil aus Spline und Linie lässt sich extrudieren
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=[sk.spline((0, 0), (10, 12), (20, 0)), sk.line(20, 0, 0, 0)]))
    timeline.add(Extrude(sketch="S", distance=5))
    assert not timeline.errors and "S" in timeline.state.profiles and timeline.state.bodies
    # Spiegeln, Schnittstellen und Abhängigkeiten
    assert sk.mirrored(curve, sk.line(0, -1, 0, 1))[1:5] == pytest.approx([0, 0, -10, 8])
    assert len(sk.line_cuts([sk.line(-5, 4, 35, 4), curve], 0)) == 3
    assert cs.check([curve, sk.line(0, 0, 5, 0)], [cs.PARALLEL, 0, 1, 0.0]) is not None
    assert cs.dimension_for([curve], 0) is None
    # Das Ende einer bemaßten Linie hängt am Spline-Anfang und nimmt ihn mit
    solved = cs.solve([sk.line(-10, 0, 0, 0), curve], [[cs.LENGTH, 0, -1, 20.0]])
    assert solved[0][3:5] == pytest.approx(solved[1][1:3], abs=1e-6)
    # Versatz: ein neuer Spline auf der angeklickten Seite, überall etwa 2 mm entfernt
    (shifted,) = sk.offset_chain([curve], 0, 2, (5, 9))
    assert shifted[0] == "Spline" and len(shifted) == len(curve)
    gaps = [min(math.dist(p, q) for q in sk.spline_points(curve)) for p in sk.spline_points(shifted)]
    assert max(gaps) < 2.4 and min(gaps) > 1.6 and shifted[2] > 0


def test_trim_circles_and_arcs():
    from wolfcad.core import sketch as sk
    # Kreis r = 10, von einer waagrechten Linie durch die Mitte geschnitten: Schnittstellen bei 0° und 180°
    entities = [sk.circle(0, 0, 10), sk.line(-20, 0, 20, 0)]
    assert sk.round_cuts(entities, 0) == pytest.approx([0, 180])
    upper_removed = sk.trim_round(entities[0], [0, 180], 90)        # oberes Stück angeklickt
    assert upper_removed == [sk.arc(0, 0, 10, 180, 0)]              # es bleibt der untere Halbkreis
    assert sk.trim_round(entities[0], [0], 90) == []                # nur eine Schnittstelle: Kreis fällt weg
    # Bogen 0°…180°, bei 60° und 120° geschnitten: Mitte, Anfang oder Ende entfernen
    arc = sk.arc(0, 0, 10, 0, 180)
    assert sk.trim_round(arc, [60, 120], 90) == [sk.arc(0, 0, 10, 0, 60), sk.arc(0, 0, 10, 120, 180)]
    assert sk.trim_round(arc, [60, 120], 30) == [sk.arc(0, 0, 10, 60, 180)]
    assert sk.trim_round(arc, [60, 120], 150) == [sk.arc(0, 0, 10, 0, 120)]
    assert sk.trim_round(arc, [], 90) == []
    # Zwei sich schneidende Kreise: Schnittpunkte spiegelbildlich, Enden eines Bogens zählen nicht
    cuts = sk.round_cuts([sk.circle(0, 0, 10), sk.circle(10, 0, 10)], 0)
    assert cuts == pytest.approx([60, 300])
    assert sk.round_cuts([sk.arc(0, 0, 10, 0, 180), sk.line(-20, 0, 20, 0), sk.line(0, 0, 0, 20)], 0) == pytest.approx([90])


def test_round_three_sketch_helpers():
    from wolfcad.core import constraints as cs
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import BuildState
    from wolfcad.gui.sketch_editor import has_profile
    # Abrunden zwischen Linie und Bogen: die Rundung berührt beide
    line, arc = sk.line(0, 0, 20, 0), sk.arc(20, 10, 10, 270, 360)        # glatter Übergang bei (20, 0)
    edge, bow = sk.line(0, 0, 20, 0), sk.arc(30, 0, 10, 90, 180)          # Knick: der Bogen steigt bei (20, 0) senkrecht auf
    result = sk.corner_fillet(edge, bow, 3)
    assert result is not None
    cut_line, cut_arc, fillet = result
    centre = fillet[1:3]
    assert centre[1] == pytest.approx(3) and math.dist(centre, (30, 0)) == pytest.approx(13)
    assert cut_line[3:5] == pytest.approx([centre[0], 0]) and cut_arc[5] < 180          # beide enden an der Rundung
    assert sk.corner_fillet(line, arc, 3) is None                         # glatter Übergang: nichts abzurunden
    # Spline: Punkt einfügen und entfernen
    curve = sk.spline((0, 0), (10, 8), (20, 0))
    more = sk.spline_with_point(curve, 15, 5)
    assert more[1:] == [0, 0, 10, 8, 15, 5, 20, 0]
    assert sk.spline_without_point(more, 2) == curve and sk.spline_without_point(sk.spline((0, 0), (1, 1)), 0) is None
    # Spline stutzen: eine senkrechte Linie schneidet ihn; das rechte Stück fällt weg
    wave = sk.spline((0, 0), (10, 8), (20, 0), (30, 8))
    kept = sk.trim_spline([wave, sk.line(15, -5, 15, 15)], 0, 25, 4)
    assert len(kept) == 1 and kept[0][1:3] == [0, 0] and kept[0][-2] == pytest.approx(15)
    both = sk.trim_spline([wave, sk.line(5, -5, 5, 15), sk.line(25, -5, 25, 15)], 0, 15, 4)
    assert len(both) == 2 and both[0][-2] == pytest.approx(5) and both[1][1] == pytest.approx(25)
    # Versatz mit Knick zwischen Linie und Bogen: die Kopie bleibt verbunden
    chain = [sk.line(0, 10, 20, 10), sk.arc(20, 0, 10, 0, 90), sk.line(30, 0, 30, -20)]
    shifted = sk.offset_chain(chain, 0, 2, (10, 14))
    ends = lambda e: sk._ends(e)
    assert math.dist(ends(shifted[0])[1], ends(shifted[1])[1]) < 1e-6      # Linie endet am Bogenende
    assert math.dist(ends(shifted[1])[0], ends(shifted[2])[0]) < 1e-6
    # Symmetrie: das Spiegelbild folgt, wenn das Original bemaßt wird
    entities = [sk.line(0, -20, 0, 20), sk.line(5, 0, 15, 0), sk.line(-5, 0, -15, 0), sk.circle(10, 10, 3), sk.circle(-10, 10, 3)]
    rules = [[cs.SYMMETRY, 1, 2, 0.0], [cs.SYMMETRY, 3, 4, 0.0], [cs.FIX, 0, -1, 0.0]]
    solved = cs.solve(entities, rules + [[cs.LENGTH, 1, -1, 30.0], [cs.DIAMETER, 3, -1, 10.0]])
    assert math.dist(solved[2][1:3], solved[2][3:5]) == pytest.approx(30) and solved[4][3] == pytest.approx(5)
    assert solved[2][1] == pytest.approx(-solved[1][1]) and solved[2][3] == pytest.approx(-solved[1][3])
    assert cs.without_entity(rules, 0) == []                               # ohne Achse keine Symmetrie mehr
    assert cs.without_entity([[cs.SYMMETRY, 2, 3, 1.0]], 0) == [[cs.SYMMETRY, 1, 2, 0.0]]
    # Konstruktionslinien erscheinen im Modellstand als eigene Kurven
    state = BuildState()
    sk.Sketch(name="S", entities=sk.rectangle(0, 0, 20, 10) + [sk.line(10, 0, 10, 10)], construction=[4]).apply(state)
    assert len(state.construction_curves) == 1 and has_profile(sk.rectangle(0, 0, 20, 10))


def test_ellipse_and_rotation():
    from wolfcad.core import constraints as cs
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import Extrude
    from wolfcad.core.timeline import Timeline
    oval = sk.ellipse(10, 5, 20, 8, 30)
    points = sk.ellipse_points(oval)
    assert points[0] == pytest.approx(points[-1]) and points[0] == pytest.approx((10 + 20 * math.cos(math.radians(30)),
                                                                                5 + 20 * math.sin(math.radians(30))))
    assert sk.check_entity(oval) is None and sk.check_entity(sk.ellipse(0, 0, 5, 0)) is not None
    # Als Profil: Fläche π·a·b, auch wenn die zweite Halbachse die größere ist
    for first, second in ((20, 8), (8, 20)):
        timeline = Timeline()
        timeline.add(sk.Sketch(name="S", entities=[sk.ellipse(0, 0, first, second, 15)]))
        timeline.add(Extrude(sketch="S", distance=2))
        assert not timeline.errors
        assert volume(timeline.state.body) == pytest.approx(math.pi * 20 * 8 * 2, rel=1e-6)
    # Schnittstellen mit einer Linie, Spiegeln, Versatz, Abhängigkeiten
    assert len(sk.line_cuts([sk.line(-30, 0, 30, 0), sk.ellipse(0, 0, 20, 8)], 0)) == 2
    assert sk.mirrored(sk.ellipse(10, 5, 20, 8, 30), sk.line(0, -1, 0, 1))[1:] == pytest.approx([-10, 5, 20, 8, 150])
    assert sk.offset_chain([sk.ellipse(0, 0, 20, 8)], 0, 2, (0, 0))[0][3:5] == [18, 6]
    assert sk.offset_chain([sk.ellipse(0, 0, 20, 8)], 0, 2, (30, 0))[0][3:5] == [22, 10]
    assert cs.check([oval, sk.circle(0, 0, 5)], [cs.EQUAL, 0, 1, 0.0]) is not None
    assert cs.solve([oval, sk.line(0, 0, 10, 5)], [[cs.LENGTH, 1, -1, 30.0]]) is not None      # Linienende hängt an der Mitte
    # Drehen um einen Punkt
    assert sk.rotated(sk.line(10, 0, 20, 0), 0, 0, 90)[1:] == pytest.approx([0, 10, 0, 20])
    assert sk.rotated(sk.arc(10, 0, 5, 0, 90), 0, 0, 90)[1:] == pytest.approx([0, 10, 5, 90, 180])
    assert sk.rotated(oval, 10, 5, 45)[1:] == pytest.approx([10, 5, 20, 8, 75])


def test_text_in_sketch_becomes_profile():
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import Extrude, faces_of
    from wolfcad.core.timeline import Timeline
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", texts=[["WOLF", 0.0, 0.0, 10.0, 0.0]]))
    timeline.add(Extrude(sketch="S", distance=2))
    assert not timeline.errors
    # Vier Buchstaben, jeder eine Fläche (das O mit Loch); der Körper hat Volumen = Buchstabenfläche × 2
    letters = faces_of(timeline.state.profiles["S"])
    assert len(letters) == 4
    assert 0 < volume(timeline.state.body) < 40 * 10 * 2
    outline = sk.text_outline("WOLF", 10.0)
    xs = [x for line in outline for x, _y in line]
    assert min(xs) >= -0.5 and 25 < max(xs) < 45                    # etwa vier Buchstabenbreiten
    # Text neben gezeichneten Elementen; gedreht; und unbrauchbare Einträge
    both = Timeline()
    both.add(sk.Sketch(name="S", entities=sk.rectangle(0, -30, 20, -20), texts=[["A1", 0.0, 0.0, 8.0, 90.0]]))
    assert not both.errors and len(faces_of(both.state.profiles["S"])) == 3
    assert sk.check_text(["", 0, 0, 5, 0]) and sk.check_text(["A", 0, 0, 0, 0]) and sk.check_text(["A", 0, 0, 5, 0]) is None
    restored = Timeline.from_document(both.to_document())
    assert restored.features[0].texts == [["A1", 0.0, 0.0, 8.0, 90.0]]


def test_text_font_and_ellipse_dimensions():
    from wolfcad.core import constraints as cs
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import faces_of
    from wolfcad.core.timeline import Timeline
    # Schrift als sechster Eintrag: andere Schrift, andere Umrisse; beides ergibt Flächen
    plain, serif = sk.text_outline("R", 10.0), sk.text_outline("R", 10.0, sk.TEXT_FONTS[1])   # Serifenschrift des Systems
    assert plain != serif
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", texts=[["Ab", 0.0, 0.0, 8.0, 15.0, sk.TEXT_FONTS[1]]]))
    assert not timeline.errors and len(faces_of(timeline.state.profiles["S"])) == 2
    assert sk.check_text(["A", 0, 0, 5, 0, 7]) is not None
    # Halbachsen der Ellipse bemaßen
    oval = [sk.ellipse(5, 5, 20, 8, 30)]
    assert cs.dimension_for(oval, 0) == [cs.SEMI_AXES[0], 0, -1, 20]
    solved = cs.solve(oval, [[cs.SEMI_AXES[0], 0, -1, 25.0], [cs.SEMI_AXES[1], 0, -1, 10.0]])
    assert solved[0][1:] == pytest.approx([5, 5, 25, 10, 30])
    assert cs.check([sk.circle(0, 0, 5)], [cs.SEMI_AXES[0], 0, -1, 5.0]) is not None


def test_ellipse_is_cut_and_trimmed_to_an_arc():
    # Ellipse 20 × 10 um den Ursprung, eine waagrechte Linie durch die Mitte teilt sie in zwei Hälften
    entities = [sk.ellipse(0, 0, 20, 10), sk.line(-30, 0, 30, 0)]
    assert sk.ellipse_cuts(entities, 0) == pytest.approx([0.0, 180.0])
    # Klick unten (Laufwinkel 270°): die untere Hälfte fällt weg, es bleibt der obere Bogen von 0° bis 180°
    assert sk.ellipse_angle(entities[0], 0.0, -10.0) == pytest.approx(270.0)
    kept = sk.trim_ellipse(entities[0], [0.0, 180.0], 270.0)
    assert len(kept) == 1 and sk.is_ellipse_arc(kept[0]) and kept[0][6:] == pytest.approx([0.0, 180.0])
    arc = kept[0]
    assert sk.check_entity(arc) is None and sk.check_entity(sk.ellipse_arc(0, 0, 20, 10, 0, 30, 30)) is not None
    points = sk.ellipse_points(arc)
    assert points[0] == pytest.approx((20.0, 0.0)) and points[-1] == pytest.approx((-20.0, 0.0), abs=1e-9)
    assert all(y >= -1e-9 for _x, y in points)
    assert sk._ends(arc) == (pytest.approx((20.0, 0.0)), pytest.approx((-20.0, 0.0), abs=1e-9))
    # ohne Schnittstellen fällt die ganze Ellipse weg; ein Bogen wird an einer Schnittstelle weiter gekürzt
    assert sk.trim_ellipse(entities[0], [], 90.0) == []
    shorter = sk.trim_ellipse(arc, sk.ellipse_cuts([arc, sk.line(0, -5, 0, 15)], 0), 135.0)
    assert len(shorter) == 1 and shorter[0][6:] == pytest.approx([0.0, 90.0])
    # eine gedrehte Ellipse: die Schnittwinkel zählen in ihrem eigenen Bezugssystem
    turned = [sk.ellipse(5, 5, 20, 10, 90), sk.line(5, -30, 5, 40)]
    assert sk.ellipse_cuts(turned, 0) == pytest.approx([0.0, 180.0])
    # Spiegeln an der x-Achse kehrt den Drehsinn um
    mirrored = sk.mirrored(arc, sk.line(0, 0, 1, 0))
    assert all(y <= 1e-9 for _x, y in sk.ellipse_points(mirrored))


def test_half_ellipse_closes_to_a_profile():
    # oberer Ellipsenbogen und die Linie darunter: eine halbe Ellipse als Profil
    for first, second, turn in ((20.0, 10.0, 0.0), (10.0, 20.0, 0.0), (20.0, 10.0, 35.0)):
        arc = sk.ellipse_arc(0, 0, first, second, turn, 0, 180)
        start, end = sk._ends(arc)
        timeline = Timeline()
        timeline.add(sk.Sketch(name="S", entities=[arc, sk.line(*end, *start)]))
        timeline.add(Extrude(sketch="S", distance=4))
        assert not timeline.errors
        properties = GProp_GProps()
        BRepGProp.VolumeProperties_s(timeline.state.body, properties)
        assert properties.Mass() == pytest.approx(math.pi * first * second / 2 * 4, rel=1e-6)


def test_spline_with_tangent_handles():
    plain = sk.spline((0, 0), (20, 10), (40, 0))
    handled = sk.with_handles(plain)
    assert sk.has_handles(handled) and not sk.has_handles(plain)
    assert handled[0] == sk.SPLINE and plain[0] == sk.SPLINE and handled[0] != "Linie"     # beide zählen als Spline
    assert sk.check_entity(handled) is None and sk.fit_points(handled) == [(0, 0), (20, 10), (40, 0)]
    assert len(sk.handle_tips(handled)) == 3
    # die Kurve läuft weiter durch ihre Punkte und liegt nahe an der alten Form
    track = sk.spline_points(handled)
    for point in sk.fit_points(handled):
        assert min(math.dist(point, sample) for sample in track) < 1e-9
    old = sk.spline_points(plain)
    assert max(min(math.dist(a, b) for b in old) for a in track) < 1.5
    # längere Griffe machen den Bogen bauchiger: zwischen den Punkten liegt die Kurve weiter von der Sehne weg
    wide = sk.scale_handles(handled, 2.5)

    def bulge(entity) -> float:
        """Größter Abstand des ersten Abschnitts von der Sehne zwischen seinen beiden Punkten (0,0)–(20,10)."""
        first = sk.spline_points(entity)[:13]
        return max(abs(10 * x - 20 * y) / math.hypot(10, 20) for x, y in first)

    assert bulge(wide) > bulge(handled) + 0.5
    assert sk.fit_points(wide) == sk.fit_points(handled)
    # Enden, Spiegeln, Punkt einfügen und entfernen kennen die Griffe
    assert sk._ends(handled) == ((0, 0), (40, 0))
    flipped = sk.mirrored(handled, sk.line(0, 0, 1, 0))
    assert sk.has_handles(flipped) and sk.fit_points(flipped) == [(0, 0), (20, -10), (40, 0)]
    more = sk.spline_with_point(handled, 10.0, 6.0)
    assert sk.has_handles(more) and len(sk.fit_points(more)) == 4 and sk.check_entity(more) is None
    fewer = sk.spline_without_point(more, 1)
    assert sk.has_handles(fewer) and sk.fit_points(fewer) == sk.fit_points(handled)
    assert sk.without_handles(handled) == plain
    assert sk.check_entity([sk.HANDLE_SPLINE, 0, 0, 1, 1, 5, 5]) is not None
    # als geschlossenes Profil mit einer Linie lässt er sich extrudieren und übersteht Speichern und Laden
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=[handled, sk.line(40, 0, 0, 0)]))
    timeline.add(Extrude(sketch="S", distance=2))
    assert not timeline.errors
    again = Timeline.from_document(timeline.to_document())
    assert not again.errors and sk.has_handles(again.features[0].entities[0])
    stump = sk.trim_spline([handled, sk.line(20, -5, 20, 20)], 0, 30.0, 6.0)
    assert len(stump) == 1 and not sk.has_handles(stump[0]) and sk._ends(stump[0])[1] == pytest.approx((20.0, 10.0), abs=0.05)


def test_more_drawing_helpers():
    assert sk.circle_through((10, 0), (0, 10), (-10, 0)) == pytest.approx((0.0, 0.0, 10.0))
    assert sk.circle_through((0, 0), (1, 1), (2, 2)) is None
    # 3-Punkt-Bogen: oberer Halbkreis von (10, 0) über (0, 10) nach (-10, 0)
    upper = sk.arc_through((10, 0), (0, 10), (-10, 0))
    assert upper[1:] == pytest.approx([0, 0, 10, 0, 180], abs=1e-9)
    lower = sk.arc_through((10, 0), (0, -10), (-10, 0))
    assert lower[4:] == pytest.approx([180, 0], abs=1e-9) or lower[4:] == pytest.approx([180, 360], abs=1e-9)
    # Tangentialbogen: am Ende einer Linie nach rechts weiter, Viertelkreis nach oben
    quarter = sk.tangent_arc((0, 0), (1, 0), (10, 10))
    assert quarter[1:4] == pytest.approx([0, 10, 10]) and quarter[4] % 360 == pytest.approx(270) and quarter[5] == pytest.approx(0, abs=1e-9)
    # nach unten: Mittelpunkt rechts unten, der Bogen läuft im Uhrzeigersinn (Winkel getauscht)
    down = sk.tangent_arc((0, 0), (1, 0), (10, -10))
    assert down[1:4] == pytest.approx([0, -10, 10]) and down[4] == pytest.approx(0, abs=1e-9) and down[5] == pytest.approx(90)
    assert sk.tangent_arc((0, 0), (1, 0), (5, 0)) is None


def test_extend_and_break():
    wall = sk.line(30, -10, 30, 10)
    short = sk.line(0, 0, 10, 0)
    assert sk.extended_line([short, wall], 0, 9, 0) == pytest.approx(["Linie", 0, 0, 30, 0])
    assert sk.extended_line([short, wall], 0, 1, 0) is None                 # nach links liegt nichts
    assert sk.extended_line([short, sk.circle(-20, 0, 5)], 0, 1, 0) == pytest.approx(["Linie", -15, 0, 10, 0])
    long_line = sk.line(0, 0, 40, 0)
    pieces = sk.broken([long_line, sk.line(10, -5, 10, 5), sk.line(25, -5, 25, 5)], 0)
    assert [p[1:] for p in pieces] == [pytest.approx([0, 0, 10, 0]), pytest.approx([10, 0, 25, 0]), pytest.approx([25, 0, 40, 0])]
    halves = sk.broken([sk.circle(0, 0, 10), sk.line(-20, 0, 20, 0)], 0)
    assert len(halves) == 2 and all(h[0] == "Bogen" for h in halves)
    assert sk.broken([long_line], 0) == [long_line]


def test_sketch_point_is_no_curve_but_carries_holes():
    """Skizzenpunkte bilden kein Profil, lassen sich bemaßen und tragen Bohrungen (wie in Fusion)."""
    from wolfcad.core import constraints as cs
    from wolfcad.core.solids import Hole

    # Punkte stören das Profil nicht: das Rechteck wird wie gewohnt extrudiert
    entities = [*sk.rectangle(1, 1, 41, 21), sk.point(11, 11), sk.point(31, 11)]
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=entities))
    timeline.add(Extrude(name="E", sketch="S", distance=10))
    assert not timeline.errors
    assert volume(timeline.state.body) == pytest.approx(8000)
    assert len(timeline.state.sketch_points["S"]) == 2

    # Zwei Punkte auf der Oberseite: je eine Durchgangsbohrung
    top = sk.Sketch(name="P", plane="XY", offset=10, entities=[sk.point(11, 11), sk.point(31, 11)])
    timeline.add(top)
    timeline.add(Hole(name="B", points_sketch="P", diameter=4))
    assert not timeline.errors, timeline.errors
    assert volume(timeline.state.body) == pytest.approx(8000 - 2 * math.pi * 4 * 10, rel=1e-6)

    # Bemaßung: Abstand zwischen den Punkten wird vom Löser eingehalten
    points = [sk.point(5, 5), sk.point(20, 7)]
    solved = cs.solve(points, [[cs.POINT_DISTANCE, 0, 1, 12.0, "", 0, 0]])
    assert math.dist(solved[0][1:3], solved[1][1:3]) == pytest.approx(12.0, abs=1e-6)
    # eine Größenbemaßung ergibt beim Punkt keinen Sinn
    assert cs.check(points, [cs.RADIUS, 0, 12.0])
    assert sk.element_values(points[0])[0][1] == "X"


def test_through_holes_from_points_below_the_body():
    """Liegen die Skizzenpunkte unter dem Körper, bohrt der Durchgang trotzdem durch ihn hindurch."""
    from wolfcad.core.solids import HOLE_HEADS, Hole

    timeline = extruded(sk.rectangle(1, 1, 41, 21))
    timeline.add(sk.Sketch(name="P", plane="XY", offset=-5, entities=[sk.point(11, 11)]))
    timeline.add(Hole(name="B", points_sketch="P", diameter=4))
    assert not timeline.errors, timeline.errors
    assert volume(timeline.state.body) == pytest.approx(8000 - math.pi * 4 * 10, rel=1e-6)
    # Senkung an der Skizzenebene auf der Oberseite: zusätzlich ein Ring der Senktiefe
    timeline = extruded(sk.rectangle(1, 1, 41, 21))
    timeline.add(sk.Sketch(name="P", plane="XY", offset=10, entities=[sk.point(11, 11)]))
    timeline.add(Hole(name="B", points_sketch="P", diameter=4, head=HOLE_HEADS[1], head_diameter=8, head_depth=3))
    assert not timeline.errors, timeline.errors
    assert volume(timeline.state.body) == pytest.approx(8000 - math.pi * 4 * 10 - math.pi * (16 - 4) * 3, rel=1e-6)


def test_conic_curve_closes_profiles_and_keeps_its_shape():
    """Kegelförmige Kurve: Rho 0,5 ist eine Parabel (Fläche 2/3 von Sehne × Pfeilhöhe), kleineres Rho flacher."""
    from wolfcad.core import constraints as cs

    areas = {}
    for rho in (0.3, 0.5, 0.8):
        curve = sk.conic(1, 1, 21, 1, 11, 11, rho)
        assert sk.check_entity(curve) is None
        timeline = extruded([curve, sk.line(21, 1, 1, 1)], distance=1)
        assert not timeline.errors, timeline.errors
        areas[rho] = volume(timeline.state.body)
    assert areas[0.5] == pytest.approx(2 / 3 * 20 * 5, rel=1e-6)
    assert areas[0.3] < areas[0.5] < areas[0.8]
    # ungültige Formen werden abgelehnt
    assert sk.check_entity(sk.conic(0, 0, 10, 0, 5, 0, 0.5))
    assert sk.check_entity(sk.conic(0, 0, 10, 0, 5, 5, 1.0))
    # Rho lässt sich nachträglich ändern; Spiegeln behält Rho
    curve = sk.conic(1, 1, 21, 1, 11, 11, 0.5)
    assert sk.with_value(curve, "rho", 0.7)[7] == 0.7
    assert sk.mirrored(curve, sk.line(0, 0, 10, 0)) == sk.conic(1, -1, 21, -1, 11, -11, 0.5)
    # die Enden hängen am Linienzug: verschiebt der Löser die Linie, folgt der Kegelschnitt
    entities = [curve, sk.line(21, 1, 1, 1)]
    solved = cs.solve(entities, [[cs.LENGTH, 1, -1, 30.0], [cs.FIX, 0, -1, 0.0]])
    assert solved is None or math.dist(solved[1][1:3], solved[0][3:5]) < 1e-6
    # Bemaßung an seinen Punkten geht, eine Längenbemaßung nicht; tangential an seinem Ende (seit Runde 41) geht
    assert cs.check(entities, [cs.POINT_DISTANCE, 0, 0, 12.0, "", 0, 1]) is None
    assert cs.check(entities, [cs.LENGTH, 0, -1, 10.0])
    assert cs.check(entities, [cs.TANGENT, 0, 1, 0.0]) is None


def test_extrude_a_body_face_like_fusion():
    """Extrusion einer ebenen Körperfläche (ohne Skizze): heraus vereinigt, hinein schneidet sie."""
    from wolfcad.core import features as ft
    from wolfcad.core.solids import Box

    def top_of(timeline):
        return [list(ft.face_point(face)) for face in ft.faces_of(timeline.state.body)
                if abs(ft.face_point(face)[2] - 10) < 1e-6]

    base = Timeline()
    base.add(Box(length=40, width=20, height=10))
    top = top_of(base)
    base.add(ft.Extrude(name="E", sketch="", face_points=top, distance=5, operation=ft.OPERATIONS[1]))
    assert not base.errors, base.errors
    assert volume(base.state.body) == pytest.approx(40 * 20 * 15)
    base.update(1, distance=-4, operation=ft.OPERATIONS[2])
    assert not base.errors, base.errors
    assert volume(base.state.body) == pytest.approx(40 * 20 * 6)
    # eine gekrümmte Fläche lässt sich nicht extrudieren: klare Meldung
    from wolfcad.core.solids import Cylinder
    round_body = Timeline()
    round_body.add(Cylinder(diameter=10, height=20))
    wall = [list(ft.face_point(f)) for f in ft.faces_of(round_body.state.body) if abs(ft.face_point(f)[2] - 10) < 1e-6]
    round_body.add(ft.Extrude(sketch="", face_points=wall, distance=5))
    assert round_body.errors


def test_control_point_spline_shapes_the_arc():
    """Steuerpunkt-Spline (Fusion): durch erste und letzte Stelle, die übrigen Punkte ziehen den Bogen; Grad 3/5."""
    from wolfcad.core import constraints as cs

    curve = sk.control_spline((1, 1), (11, 21), (31, 21), (41, 1))
    assert sk.check_entity(curve) is None
    samples = sk.control_spline_points(curve, 4)
    assert samples[0] == (1.0, 1.0) and samples[-1] == (41.0, 1.0)
    assert samples[2] == pytest.approx((21.0, 16.0))          # kubische Bézierkurve: Mitte = (P0+3P1+3P2+P3)/8
    timeline = extruded([curve, sk.line(41, 1, 1, 1)], distance=1)
    assert not timeline.errors, timeline.errors
    # Fläche unter der kubischen Bézierkurve, numerisch nachgerechnet (Integral von y dx über t)
    import numpy as np
    t = np.linspace(0, 1, 200001)
    poles = np.array([(1, 1), (11, 21), (31, 21), (41, 1)], dtype=float)
    basis = np.stack([(1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3])
    xs, ys = basis.T @ poles[:, 0], basis.T @ poles[:, 1]
    expected = np.trapezoid(ys - 1, xs)
    assert volume(timeline.state.body) == pytest.approx(expected, rel=1e-6)
    # höherer Steuerpunkt: bauchiger; Grad nur 3 oder 5
    taller = sk.control_spline((1, 1), (11, 31), (31, 31), (41, 1))
    assert volume(extruded([taller, sk.line(41, 1, 1, 1)], distance=1).state.body) > volume(timeline.state.body)
    five = sk.control_spline(*[(1 + 8 * k, 1 + (10 if 0 < k < 5 else 0)) for k in range(6)], degree=5)
    assert sk.check_entity(five) is None and not extruded([five, sk.line(41, 1, 1, 1)], distance=1).errors
    assert sk.with_value(curve, "degree", 4.6)[-1] == 5.0
    assert sk.check_entity(sk.control_spline((0, 0), (5, 5), degree=4))
    # Bemaßung über die Steuerpunkte, Spiegeln behält den Grad
    assert cs.point_count(curve) == 4
    assert cs.check([curve], [cs.POINT_DISTANCE, 0, 0, 10.0, "", 0, 1]) is None
    assert sk.mirrored(curve, sk.line(0, 0, 10, 0))[-1] == 3.0


def test_closed_regions_for_the_blue_fill():
    """Die blaue Füllung beim Skizzieren: ein T-Stoß teilt das Rechteck in zwei Bereiche, offene Linien füllen nichts."""
    split = sk.closed_regions([*sk.rectangle(1, 1, 41, 21), sk.line(21, 1, 21, 21)])
    area = sum(abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2 for a, b, c in split)
    assert area == pytest.approx(800, rel=1e-6)
    assert sk.closed_regions([sk.line(1, 1, 41, 1), sk.line(21, 1, 21, 21)]) == []
    # Konstruktionslinien zählen nicht: ein Rechteck aus Konstruktionslinien bleibt ungefüllt
    assert sk.closed_regions(sk.rectangle(1, 1, 41, 21), construction=[0, 1, 2, 3]) == []


def test_polygon_slot_and_tangent_circle_variants_like_fusion():
    """Polygon umschrieben/Kante, Langloch Gesamt und Bogen-Langloch, Kreise an zwei bzw. drei Tangenten."""
    # umschrieben: der Klickpunkt ist Seitenmitte → Inkreisradius 10
    hexagon = sk.polygon_circumscribed(0, 0, 10, 0, 6)
    middles = [((e[1] + e[3]) / 2, (e[2] + e[4]) / 2) for e in hexagon]
    assert all(math.hypot(*m) == pytest.approx(10) for m in middles)
    # Kante: erste Seite ist genau die gezogene Kante, das Sechseck liegt auf der Seite des dritten Punkts
    edge = sk.polygon_edge(1, 1, 11, 1, 5, 8, 6)
    assert edge[0][1:5] == pytest.approx([1, 1, 11, 1]) and all(e[2] >= 1 - 1e-9 for e in edge)
    assert extruded(edge).state.body is not None
    # Langloch „Gesamt“: Gesamtlänge 40, Breite 10 → Fläche = 30·10 + π·25
    timeline = extruded(sk.slot_overall(1, 1, 41, 1, 5), distance=1)
    assert volume(timeline.state.body) == pytest.approx(30 * 10 + math.pi * 25, rel=1e-6)
    # Bogen-Langloch: Viertelkreis R 20, Breite 4 → Fläche = Kreisringviertel + ein Kreis der Breite
    arc_timeline = extruded(sk.arc_slot(1, 1, 20, 0, 90, 2), distance=1)
    assert not arc_timeline.errors, arc_timeline.errors
    ring = math.pi * (22 ** 2 - 18 ** 2) / 4
    assert volume(arc_timeline.state.body) == pytest.approx(ring + math.pi * 4, rel=1e-6)
    # Kreise an zwei Tangenten (zwei sich kreuzende Linien): vier Lösungen, alle berühren beide
    a, b = sk.line(0, 0, 40, 0), sk.line(0, 0, 0, 40)
    circles = sk.tangent_circles_two(a, b, 5)
    assert len(circles) == 4 and (5.0, 5.0, 5) in [tuple(round(c, 9) for c in circle) for circle in circles]
    # Kreis an drei Tangenten: Inkreis des Dreiecks 3-4-5 hat r = 1
    tri = [sk.line(0, 0, 4, 0), sk.line(4, 0, 0, 3), sk.line(0, 3, 0, 0)]
    found = sk.tangent_circles_three(tri)
    assert any(abs(r - 1) < 1e-9 and math.dist((x, y), (1, 1)) < 1e-9 for x, y, r in found)


def test_sketch_chamfer_kinds_and_blend_curve():
    """Skizzenfase (gleicher Abstand, Abstand+Winkel, zwei Abstände) und Übergangskurve (G1) wie in Fusion."""
    a, b = sk.line(10, 0, 0, 0), sk.line(0, 0, 0, 10)          # rechtwinklige Ecke im Ursprung
    one, two, cut = sk.corner_chamfer(a, b, sk.CHAMFER_KINDS[0], 3)
    assert cut[1:5] == pytest.approx([3, 0, 0, 3]) and one[3:5] == pytest.approx([3, 0])
    _o, _t, cut = sk.corner_chamfer(a, b, sk.CHAMFER_KINDS[2], 2, 5)
    assert cut[1:5] == pytest.approx([2, 0, 0, 5])
    _o, _t, cut = sk.corner_chamfer(a, b, sk.CHAMFER_KINDS[1], 4, angle=45)
    assert cut[1:5] == pytest.approx([4, 0, 0, 4])              # 45° an einer rechtwinkligen Ecke = gleich lang
    assert sk.corner_chamfer(a, b, sk.CHAMFER_KINDS[0], 20) is None
    # Übergangskurve: zwei waagerechte Linien versetzt → Spline beginnt/endet waagerecht
    left, right = sk.line(0, 0, 10, 0), sk.line(20, 6, 30, 6)
    blend = sk.blend_curve(left, True, right, False)
    assert sk.has_handles(blend)
    samples = sk.spline_points(blend)
    assert samples[0] == pytest.approx((10, 0)) and samples[-1] == pytest.approx((20, 6))
    # die Griffe legen die Endtangenten fest: beide waagerecht; der Zielgriff zeigt in Kurve B hinein (die Ankunft
    # spiegelt ihn), die Kurve läuft also glatt nach rechts weiter
    assert blend[4] == pytest.approx(0) and blend[3] > 10
    assert blend[8] == pytest.approx(6) and blend[7] > 20


def test_blend_curve_with_continuous_curvature():
    """G2-Übergangskurve zwischen zwei Linien: an beiden Enden tangential und ohne Krümmung (wie die Linien)."""
    from wolfcad.core import constraints as cs

    left, right = sk.line(0, 0, 10, 0), sk.line(25, 8, 35, 8)
    blend = sk.blend_curve_g2(left, True, right, False)
    assert sk.has_handles(blend) and len(sk.fit_points(blend)) == 3
    entities = [left, right, blend]
    for other in (0, 1):
        values = np.array([float(v) for entity in entities for v in entity[1:]])
        starts = np.cumsum([0] + [len(entity) - 1 for entity in entities])
        assert abs(cs._end_curvature(entities, values, starts, other, 2)) < 1e-9
        assert abs(cs._end_tangency(entities, values, starts, other, 2)) < 1e-9


def test_3d_sketch_line_is_a_path_but_not_a_profile():
    """3D-Skizze (Fusion): eine Linie mit Punkt außerhalb der Ebene gehört zu den Kurven (Sweep-Pfad),
    stört aber das flache Profil nicht."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from wolfcad.core import constraints as cs
    from wolfcad.core import features as ft
    from wolfcad.core.solids import Sweep

    timeline = Timeline()
    timeline.add(sk.Sketch(name="Profil", plane="XY", entities=[*sk.rectangle(1, 1, 41, 21), sk.line3d(1, 1, 0, 41, 21, 15)]))
    assert not timeline.errors, timeline.errors
    regions = sk.regions_2d(timeline.state.profiles["Profil"], timeline.state.frames["Profil"])
    assert len(regions) == 1                                      # das Rechteck bleibt ein Profil
    # die 3D-Linie endet 15 mm über der Ebene
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS
    explorer, tops = TopExp_Explorer(timeline.state.curves["Profil"], TopAbs_EDGE), []
    while explorer.More():
        curve = BRepAdaptor_Curve(TopoDS.Edge(explorer.Current()))
        tops.append(max(curve.Value(curve.FirstParameter()).Z(), curve.Value(curve.LastParameter()).Z()))
        explorer.Next()
    assert max(tops) == pytest.approx(15.0)
    # als Sweep-Pfad: ein Kreis wird schräg nach oben gezogen
    timeline.add(sk.Sketch(name="Kreis", plane="XY", entities=[sk.circle(1, 1, 2)]))
    timeline.add(sk.Sketch(name="Pfad", plane="XY", entities=[sk.line3d(1, 1, 0, 1, 1, 30)]))
    timeline.add(Sweep(name="S", sketch="Kreis", path="Pfad"))
    assert not timeline.errors, timeline.errors
    assert volume(timeline.state.body) == pytest.approx(math.pi * 4 * 30, rel=1e-6)
    # nur Fixieren als Abhängigkeit, Drehen um die Normale lässt z unverändert
    assert cs.check([sk.line3d(0, 0, 0, 5, 5, 5)], [cs.HORIZONTAL, 0, -1, 0.0])
    assert sk.rotated(sk.line3d(1, 0, 3, 2, 0, 4), 0, 0, 90)[3] == 3


def test_spline_handles_with_different_length_per_side():
    """Griffe je Seite unterschiedlich lang (Fusion: ein Griffende einzeln ziehen)."""
    handled = sk.with_handles(sk.spline((0, 0), (20, 10), (40, 0)))
    # das Gegengriffende am mittleren Punkt weiter herausziehen: nur der Bogen davor ändert sich
    back = sk.back_tips(handled)[1]
    far = (20 + (back[0] - 20) * 3, 10 + (back[1] - 10) * 3)
    uneven = sk.with_back_tip(handled, 1, *far)
    assert sk.has_handles(uneven) and sk.has_uneven_handles(uneven) and uneven[0] == sk.SPLINE
    assert sk.check_entity(uneven) is None
    # Punkte und Griffe stehen an denselben Stellen (gleiche Punktnummern), der Gegengriff ist dreimal so lang
    assert uneven[1:13] == handled[1:13] and sk.fit_points(uneven) == sk.fit_points(handled)
    assert math.dist(sk.back_tips(uneven)[1], (20, 10)) == pytest.approx(3 * math.dist(back, (20, 10)))
    old, new = sk.spline_points(handled), sk.spline_points(uneven)
    assert max(math.dist(a, b) for a, b in zip(old[:13], new[:13])) > 0.5         # Abschnitt davor bauchiger
    assert max(math.dist(a, b) for a, b in zip(old[12:], new[12:])) < 1e-9        # Abschnitt danach gleich
    # der Gegengriff zeigt immer gegen den Griff, auch wenn sein gespeicherter Punkt schief steht
    skewed = list(uneven)
    skewed[-4:-2] = [20, 10 + math.dist(sk.back_tips(uneven)[1], (20, 10))]
    assert max(math.dist(a, b) for a, b in zip(sk.spline_points(skewed), new)) < 1e-9
    # Punkt einfügen/entfernen, Spiegeln, Stärke und Zurücksetzen kennen die Gegengriffe
    more = sk.spline_with_point(uneven, 10.0, 6.0)
    assert sk.has_uneven_handles(more) and len(sk.fit_points(more)) == 4 and sk.check_entity(more) is None
    fewer = sk.spline_without_point(more, 1)
    assert fewer[1:] == pytest.approx(uneven[1:]) and fewer[0] == sk.UNEVEN_HANDLE_SPLINE
    flipped = sk.mirrored(uneven, sk.line(0, 0, 1, 0))
    assert sk.has_uneven_handles(flipped) and sk.fit_points(flipped) == [(0, 0), (20, -10), (40, 0)]
    stronger = sk.scale_handles(uneven, 2.0)
    assert math.dist(sk.back_tips(stronger)[1], (20, 10)) == pytest.approx(2 * math.dist(far, (20, 10)), rel=1e-6)
    assert sk.evened(uneven) == handled and sk.without_handles(uneven) == sk.without_handles(handled)
    two = sk.with_handles(sk.spline((0, 0), (20, 10)))
    assert sk.check_entity([sk.UNEVEN_HANDLE_SPLINE, *two[1:]]) is not None    # Gegengriffe fehlen
    # als geschlossenes Profil mit einer Linie extrudierbar, übersteht Speichern und Laden
    timeline = Timeline()
    timeline.add(sk.Sketch(name="S", entities=[uneven, sk.line(40, 0, 0, 0)]))
    timeline.add(Extrude(sketch="S", distance=2))
    assert not timeline.errors
    again = Timeline.from_document(timeline.to_document())
    assert not again.errors and sk.has_uneven_handles(again.features[0].entities[0])


def test_3d_spline_through_space_points_and_3d_line_length():
    """3D-Skizze (Fusion): Spline durch Raumpunkte als Sweep-Pfad; die Länge einer 3D-Linie ist bemaßbar."""
    from wolfcad.core import constraints as cs
    from wolfcad.core.solids import Sweep

    curve = sk.spline3d((0, 0, 0), (10, 5, 8), (20, 0, 20))
    assert sk.check_entity(curve) is None and curve[0] in sk.SPATIAL
    assert sk.check_entity([sk.SPLINE3D, 0, 0, 0, 0, 0, 0]) is not None        # zwei gleiche Punkte
    assert sk.check_entity([sk.SPLINE3D, 0, 0, 0, 1, 1]) is not None            # unvollständiger Raumpunkt
    track = sk.track3d(curve)
    # die Kurve läuft durch alle Raumpunkte und hebt zwischen ihnen glatt ab
    for point in sk.points3d(curve):
        assert min(math.dist(point, sample) for sample in track) < 1e-9
    assert len(sk.shadow(curve)) == len(track) and sk._ends(curve) is None
    # Drehen um die Normale und Spiegeln an einer Geraden lassen die Höhen stehen
    assert [p[2] for p in sk.points3d(sk.rotated(curve, 0, 0, 90))] == [0, 8, 20]
    flipped = sk.mirrored(curve, sk.line(0, 0, 1, 0))
    assert sk.points3d(flipped)[1] == pytest.approx((10, -5, 8))
    # Pfad, aber kein Profil: das Rechteck daneben bleibt Profil
    timeline = Timeline()
    timeline.add(sk.Sketch(name="Profil", plane="XY", entities=[*sk.rectangle(30, 0, 40, 10), curve]))
    assert not timeline.errors, timeline.errors
    assert len(sk.regions_2d(timeline.state.profiles["Profil"], timeline.state.frames["Profil"])) == 1
    # als Sweep-Pfad: ein kleiner Kreis wird die Raumkurve entlang gezogen
    timeline.add(sk.Sketch(name="Kreis", plane="YZ", entities=[sk.circle(0, 0, 1)]))
    timeline.add(sk.Sketch(name="Pfad", plane="XY", entities=[sk.spline3d((0, 0, 0), (10, 0, 5), (20, 0, 20))]))
    timeline.add(Sweep(name="S", sketch="Kreis", path="Pfad"))
    assert not timeline.errors, timeline.errors
    assert volume(timeline.state.body) > 0
    again = Timeline.from_document(timeline.to_document())
    assert not again.errors
    # am 3D-Spline geht nur Fixieren; er hat keine Punkte in der Ebene
    assert cs.check([curve], [cs.FIX, 0, -1, 0.0]) is None
    assert cs.check([curve], [cs.LENGTH, 0, -1, 5.0]) is not None and cs.point_count(curve) == 0
    # Länge einer 3D-Linie: räumlich gemessen und vom Löser eingehalten
    slanted = sk.line3d(0, 0, 0, 3, 4, 12)
    rule = cs.dimension_for([slanted], 0)
    assert rule[0] == cs.LENGTH and rule[3] == pytest.approx(13.0)
    assert cs.check([slanted], [cs.LENGTH, 0, -1, 26.0]) is None
    solved = cs.solve([slanted], [[cs.LENGTH, 0, -1, 26.0]])
    assert solved is not None and math.dist(solved[0][1:4], solved[0][4:7]) == pytest.approx(26.0, abs=1e-6)
    assert cs.measured(solved, [cs.LENGTH, 0, -1, 26.0]) == pytest.approx(26.0, abs=1e-6)


def test_rename_sketch_updates_references():
    """Umbenennen einer Skizze: Extrusion und Sweep, die sie nennen, folgen dem neuen Namen."""
    from wolfcad.core.features import Extrude
    timeline = Timeline()
    timeline.add(sk.Sketch(name="Skizze1", plane="XY", entities=[sk.circle(0, 0, 3)]))
    timeline.add(Extrude(name="E", sketch="Skizze1", distance=5))
    timeline.add(sk.Sketch(name="Skizze2", plane="XY", entities=[sk.circle(20, 0, 2)]))
    timeline.rename(0, "Grundkreis")
    assert timeline.features[0].name == "Grundkreis" and timeline.features[1].sketch == "Grundkreis"
    assert not timeline.errors
    with pytest.raises(ValueError):
        timeline.rename(2, "Grundkreis")
    again = Timeline.from_document(timeline.to_document())
    assert not again.errors and again.features[1].sketch == "Grundkreis"


def test_automatic_draft_on_body_with_fillets():
    """Schräge ohne Auswahl an einem Quader mit verrundeten Oberkanten: die Wände, die sich neigen lassen,
    werden geneigt, statt dass die ganze Schräge scheitert."""
    from wolfcad.core import features as ft, solids
    timeline = Timeline()
    timeline.add(solids.Box(name="Q", length=40, width=30, height=20))
    timeline.add(ft.Fillet(name="R", radius=3, body=next(iter(timeline.state.bodies)),
                           edge_points=[[0.0, -15.0, 20.0]]))
    before = volume(timeline.state.body)
    timeline.add(solids.Draft(name="S", angle=3))
    assert not timeline.errors, timeline.errors
    assert volume(timeline.state.body) != pytest.approx(before, rel=1e-6)
