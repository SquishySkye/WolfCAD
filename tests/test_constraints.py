# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für Maße und Abhängigkeiten der Skizze."""

import math

import pytest

from wolfcad.core import constraints as cs
from wolfcad.core import sketch as sk
from wolfcad.core.timeline import Timeline


def length(entity) -> float:
    return math.dist(entity[1:3], entity[3:5])


def test_length_dimension_keeps_rectangle_closed():
    entities = sk.rectangle(0, 0, 40, 20)
    # Rechteck bleibt Rechteck: Seiten waagrecht/senkrecht, untere Seite auf 60 mm
    rules = [[cs.HORIZONTAL, 0, -1, 0], [cs.VERTICAL, 1, -1, 0], [cs.HORIZONTAL, 2, -1, 0],
             [cs.VERTICAL, 3, -1, 0], [cs.LENGTH, 0, -1, 60.0]]
    solved = cs.solve(entities, rules)
    assert solved is not None
    assert length(solved[0]) == pytest.approx(60) and length(solved[2]) == pytest.approx(60)
    assert length(solved[1]) == pytest.approx(20)
    # Die Ecken hängen weiter zusammen
    for first, second in zip(solved, solved[1:] + solved[:1]):
        assert first[3:5] == pytest.approx(second[1:3], abs=1e-6)
    assert entities[0] == sk.line(0, 0, 40, 0)      # die Eingabe bleibt unverändert


def test_diameter_and_radius():
    solved = cs.solve([sk.circle(5, 5, 8), sk.arc(0, 0, 10, 0, 90)],
                      [[cs.DIAMETER, 0, -1, 30.0], [cs.RADIUS, 1, -1, 4.0]])
    assert solved[0][1:] == pytest.approx([5, 5, 15])      # Mittelpunkt bleibt, Radius = halber Durchmesser
    assert solved[1][3] == pytest.approx(4)


def test_horizontal_vertical_parallel_perpendicular_equal():
    entities = [sk.line(0, 0, 30, 4), sk.line(0, 10, 3, 35), sk.line(50, 0, 80, 9), sk.line(50, 20, 55, 60)]
    solved = cs.solve(entities, [[cs.HORIZONTAL, 0, -1, 0], [cs.VERTICAL, 1, -1, 0],
                                 [cs.PARALLEL, 0, 2, 0], [cs.PERPENDICULAR, 0, 3, 0], [cs.EQUAL, 0, 1, 0]])
    assert solved is not None
    assert solved[0][2] == pytest.approx(solved[0][4])              # waagrecht
    assert solved[1][1] == pytest.approx(solved[1][3])              # senkrecht
    assert solved[2][2] == pytest.approx(solved[2][4], abs=1e-5)    # parallel zur waagrechten Linie
    assert solved[3][1] == pytest.approx(solved[3][3], abs=1e-5)    # rechtwinklig dazu
    assert length(solved[0]) == pytest.approx(length(solved[1]))


def test_contradiction_is_reported():
    entities = [sk.line(0, 0, 30, 0)]
    assert cs.solve(entities, [[cs.LENGTH, 0, -1, 10.0], [cs.LENGTH, 0, -1, 20.0]]) is None
    assert cs.solve(entities, [[cs.DIAMETER, 0, -1, 10.0]]) is None         # Maß passt nicht zur Linie
    assert cs.solve(entities, [[cs.LENGTH, 3, -1, 10.0]]) is None           # Element fehlt


def test_line_stays_attached_to_arc_end():
    # Linie endet am Bogenanfang (10, 0); wird sie länger, bleibt sie am Bogen
    solved = cs.solve([sk.line(-20, 0, 10, 0), sk.arc(0, 0, 10, 0, 90)], [[cs.LENGTH, 0, -1, 50.0]])
    arc = solved[1]
    start = (arc[1] + arc[3] * math.cos(math.radians(arc[4])), arc[2] + arc[3] * math.sin(math.radians(arc[4])))
    assert solved[0][3:5] == pytest.approx(start, abs=1e-5)
    assert length(solved[0]) == pytest.approx(50)


def test_dimension_for_and_deleting_entities():
    entities = [sk.line(0, 0, 3, 4), sk.circle(0, 0, 6), sk.arc(0, 0, 7, 0, 90)]
    assert cs.dimension_for(entities, 0) == [cs.LENGTH, 0, -1, 5.0]
    assert cs.dimension_for(entities, 1) == [cs.DIAMETER, 1, -1, 12.0]
    assert cs.dimension_for(entities, 2) == [cs.RADIUS, 2, -1, 7.0]
    rules = [[cs.LENGTH, 0, -1, 5.0], [cs.DIAMETER, 1, -1, 12.0], [cs.RADIUS, 2, -1, 7.0]]
    assert cs.without_entity(rules, 1) == [[cs.LENGTH, 0, -1, 5.0], [cs.RADIUS, 1, -1, 7.0]]


def test_constraints_survive_saving():
    timeline = Timeline()
    timeline.add(sk.Sketch(name="Skizze1", entities=sk.rectangle(0, 0, 40, 20),
                           constraints=[[cs.LENGTH, 0, -1, 40.0]]))
    restored = Timeline.from_document(timeline.to_document())
    assert restored.features[0].constraints == [[cs.LENGTH, 0, -1, 40.0]]


def test_distance_between_parallel_lines_and_between_circles():
    entities = [sk.line(0, 0, 40, 0), sk.line(0, 10, 40, 10), sk.circle(0, 30, 5), sk.circle(20, 30, 5)]
    assert cs.measure(entities, cs.DISTANCE, 0, 1) == pytest.approx(10)
    assert cs.measure(entities, cs.DISTANCE, 2, 3) == pytest.approx(20)
    solved = cs.solve(entities, [[cs.HORIZONTAL, 0, -1, 0], [cs.PARALLEL, 0, 1, 0],
                                 [cs.DISTANCE, 0, 1, 25.0], [cs.DISTANCE, 2, 3, 50.0]])
    assert cs.measure(solved, cs.DISTANCE, 0, 1) == pytest.approx(25)
    assert math.dist(solved[2][1:3], solved[3][1:3]) == pytest.approx(50)


def test_distance_from_circle_to_line():
    solved = cs.solve([sk.line(0, 0, 40, 0), sk.circle(10, 8, 3)], [[cs.DISTANCE, 0, 1, 20.0]])
    assert cs.measure(solved, cs.DISTANCE, 0, 1) == pytest.approx(20)
    assert solved[1][3] == pytest.approx(3)         # der Radius bleibt


def test_angle_between_lines():
    entities = [sk.line(0, 0, 40, 0), sk.line(0, 0, 30, 30)]
    assert cs.measure(entities, cs.ANGLE, 0, 1) == pytest.approx(45)
    solved = cs.solve(entities, [[cs.ANGLE, 0, 1, 60.0]])
    assert cs.measure(solved, cs.ANGLE, 0, 1) == pytest.approx(60)
    assert solved[0][1:3] == pytest.approx(solved[1][1:3], abs=1e-6)    # gemeinsamer Anfang bleibt
    assert cs.solve(entities, [[cs.ANGLE, 0, 1, 180.0]]) is None


def test_tangent_line_and_circles():
    # Linie berührt den Kreis: Abstand Mittelpunkt–Gerade = Radius
    solved = cs.solve([sk.line(0, 0, 40, 0), sk.circle(20, 12, 8)], [[cs.TANGENT, 0, 1, 0]])
    assert cs.measure(solved, cs.DISTANCE, 0, 1) == pytest.approx(solved[1][3])
    # zwei Kreise nebeneinander berühren sich von außen
    outside = cs.solve([sk.circle(0, 0, 10), sk.circle(30, 0, 8)], [[cs.TANGENT, 0, 1, 0]])
    assert math.dist(outside[0][1:3], outside[1][1:3]) == pytest.approx(outside[0][3] + outside[1][3])
    # kleiner Kreis im großen berührt von innen
    inner = cs.solve([sk.circle(0, 0, 20), sk.circle(5, 0, 6)], [[cs.TANGENT, 0, 1, 0]])
    assert math.dist(inner[0][1:3], inner[1][1:3]) == pytest.approx(inner[0][3] - inner[1][3])
    assert cs.solve([sk.line(0, 0, 1, 0), sk.line(0, 1, 1, 1)], [[cs.TANGENT, 0, 1, 0]]) is None


def test_point_on_curve():
    # Ende der Linie (Punkt 1) auf den Kreis, Anfang (Punkt 0) auf die zweite Linie
    entities = [sk.line(3, 4, 30, 7), sk.circle(40, 0, 10), sk.line(0, -20, 0, 20)]
    solved = cs.solve(entities, [[cs.POINT_ON, 0, 1, 1], [cs.POINT_ON, 0, 2, 0]])
    assert math.dist(solved[0][3:5], solved[1][1:3]) == pytest.approx(solved[1][3])
    # Anfang liegt auf der Geraden durch die zweite Linie
    line = solved[2]
    cross = (line[3] - line[1]) * (solved[0][2] - line[2]) - (line[4] - line[2]) * (solved[0][1] - line[1])
    assert cross == pytest.approx(0, abs=1e-4)
    assert cs.point_of(sk.arc(0, 0, 10, 0, 90), 2) == pytest.approx((0, 10))
    assert cs.solve(entities, [[cs.POINT_ON, 1, 0, 1]]) is None     # ein Kreis hat nur den Mittelpunkt


def test_origin_is_fixed_and_degrees_of_freedom():
    # Rechteck mit einer Ecke im Ursprung: die Ecke bleibt dort, wenn eine Seite bemaßt wird
    entities = sk.rectangle(0, 0, 40, 20)
    shape = [[cs.HORIZONTAL, 0, -1, 0.0], [cs.VERTICAL, 1, -1, 0.0], [cs.HORIZONTAL, 2, -1, 0.0],
             [cs.VERTICAL, 3, -1, 0.0]]
    solved = cs.solve(entities, shape + [[cs.LENGTH, 0, -1, 60.0]])
    assert solved[0][1:] == pytest.approx([0, 0, 60, 0], abs=1e-6)
    # Ohne Maße ist alles beweglich; Breite allein lässt die Höhe frei; beide Maße bestimmen alles
    assert cs.free_entities(entities, shape) == {0, 1, 2, 3}
    assert cs.free_entities(entities, shape + [[cs.LENGTH, 0, -1, 40.0]]) == {1, 2, 3}
    assert cs.free_entities(entities, shape + [[cs.LENGTH, 0, -1, 40.0], [cs.LENGTH, 1, -1, 20.0]]) == set()
    # Dasselbe Rechteck fernab des Ursprungs lässt sich noch verschieben
    away = sk.rectangle(5, 5, 45, 25)
    assert cs.free_entities(away, shape + [[cs.LENGTH, 0, -1, 40.0], [cs.LENGTH, 1, -1, 20.0]]) == {0, 1, 2, 3}
    # Kreis um den Ursprung: erst mit Durchmessermaß bestimmt; ein freier Kreis daneben bleibt beweglich
    circles = [sk.circle(0, 0, 5), sk.circle(30, 0, 5)]
    assert cs.free_entities(circles, []) == {0, 1}
    assert cs.free_entities(circles, [[cs.DIAMETER, 0, -1, 10.0]]) == {1}
    assert cs.free_entities([], []) == set()


def test_fix_keeps_an_element_in_place():
    # Zwei verbundene Linien; die erste ist fixiert, die zweite wird länger: nur ihr freies Ende wandert
    entities = [sk.line(5, 5, 25, 5), sk.line(25, 5, 25, 20)]
    solved = cs.solve(entities, [[cs.FIX, 0, -1, 0.0], [cs.LENGTH, 1, -1, 30.0]])
    assert solved[0] == entities[0] or solved[0][1:] == pytest.approx(entities[0][1:], abs=1e-6)
    assert solved[1][1:3] == pytest.approx([25, 5], abs=1e-6) and length(solved[1]) == pytest.approx(30)
    # Ein fixiertes Element lässt sich nicht bemaßen, wenn das Maß nicht schon stimmt
    assert cs.solve(entities, [[cs.FIX, 0, -1, 0.0], [cs.LENGTH, 0, -1, 50.0]]) is None
    assert cs.solve(entities, [[cs.FIX, 0, -1, 0.0], [cs.LENGTH, 0, -1, 20.0]]) is not None
    # Fixiert zählt als voll bestimmt; auch ein Spline lässt sich fixieren
    assert cs.free_entities(entities, [[cs.FIX, 0, -1, 0.0]]) == {1}
    curve = sk.spline((0, 5), (10, 12), (20, 5))
    assert cs.check([curve], [cs.FIX, 0, -1, 0.0]) is None and cs.free_entities([curve], [[cs.FIX, 0, -1, 0.0]]) == set()


def test_direction_dimension():
    # Linie unter 30° gegen die x-Achse festhalten und danach verlängern: die Neigung bleibt
    entities = [sk.line(0, 0, 20, 5)]
    solved = cs.solve(entities, [[cs.DIRECTION, 0, -1, 30.0], [cs.LENGTH, 0, -1, 40.0]])
    end = solved[0][3:5]
    assert math.degrees(math.atan2(end[1], end[0])) == pytest.approx(30) and length(solved[0]) == pytest.approx(40)
    assert cs.check(entities, [cs.DIRECTION, 0, -1, 0.0]) is None           # 0° ist erlaubt
    assert cs.check([sk.circle(0, 0, 5)], [cs.DIRECTION, 0, -1, 30.0]) is not None
    assert cs.free_entities(entities, [[cs.DIRECTION, 0, -1, 30.0], [cs.LENGTH, 0, -1, 40.0]]) == set()


def test_tangent_prefers_moving_over_resizing():
    # Kreis über einer fixierten Linie: beim Berühren rückt er vor allem heran, statt stark zu wachsen
    entities = [sk.line(0, 0, 40, 0), sk.circle(20, 12, 8)]
    solved = cs.solve(entities, [[cs.FIX, 0, -1, 0.0], [cs.TANGENT, 0, 1, 0.0]])
    assert solved[1][2] == pytest.approx(solved[1][3], abs=1e-6)        # berührt
    assert abs(solved[1][3] - 8) < 0.5 and abs(solved[1][2] - 12) > 3      # Radius fast unverändert, Lage angepasst


def test_point_distance_dimensions_a_spline():
    from wolfcad.core import sketch as sk
    entities = [sk.spline((0, 0), (20, 10), (40, 0)), sk.line(0, -20, 40, -20)]
    # mittlerer Spline-Punkt (Nummer 1) 25 mm vom Anfang der Linie (Punkt 0)
    rule = [cs.POINT_DISTANCE, 0, 1, 25.0, "", 1, 0]
    assert cs.check(entities, rule) is None
    assert cs.check(entities, [cs.POINT_DISTANCE, 0, 1, 25.0]) is not None          # ohne Punktnummern unbrauchbar
    assert cs.check(entities, [cs.POINT_DISTANCE, 0, 1, 25.0, "", 7, 0]) is not None
    solved = cs.solve(entities, [rule, [cs.FIX, 1, -1, 0.0]])
    assert solved is not None
    assert cs.point_distance(solved, 0, 1, 1, 0) == pytest.approx(25.0, abs=1e-6)
    # auch an einem Griff-Spline: Kurvenpunkt 2 zum Kurvenpunkt 0 (Nummern 0, 2, 4 sind Kurvenpunkte)
    handled = [sk.with_handles(entities[0])]
    solved = cs.solve(handled, [[cs.POINT_DISTANCE, 0, 0, 30.0, "", 0, 4], [cs.FIX, 0, -1, 0.0]])
    assert solved is None or cs.point_distance(solved, 0, 0, 0, 4) == pytest.approx(30.0, abs=1e-6)
    free = cs.solve(handled, [[cs.POINT_DISTANCE, 0, 0, 30.0, "", 0, 4]])
    assert free is not None and cs.point_distance(free, 0, 0, 0, 4) == pytest.approx(30.0, abs=1e-6)
    # ein gelöschtes Element nimmt das Maß mit, die Punktnummern bleiben beim Aufrücken erhalten
    kept = cs.without_entity([[cs.POINT_DISTANCE, 1, 2, 5.0, "", 0, 1]], 0)
    assert kept == [[cs.POINT_DISTANCE, 0, 1, 5.0, "", 0, 1]]


def test_elliptical_arc_ends_are_solver_points():
    from wolfcad.core import sketch as sk
    arc = sk.ellipse_arc(0, 0, 20, 10, 0, 0, 180)
    assert cs.point_count(arc) == 3
    assert cs.point_of(arc, 1) == pytest.approx((20.0, 0.0)) and cs.point_of(arc, 2) == pytest.approx((-20.0, 0.0), abs=1e-9)
    # die Linie schließt den Bogen; wird sie per Maß länger, bleiben die Enden verbunden und die Ellipse wächst mit
    entities = [arc, sk.line(-20, 0, 20, 0)]
    solved = cs.solve(entities, [[cs.LENGTH, 1, -1, 50.0], [cs.HORIZONTAL, 1, -1, 0.0]])
    assert solved is not None
    bogen, linie = solved
    ends = {tuple(round(c, 6) for c in cs.point_of(bogen, slot)) for slot in (1, 2)}
    assert ends == {tuple(round(c, 6) for c in linie[1:3]), tuple(round(c, 6) for c in linie[3:5])}
    assert math.dist(linie[1:3], linie[3:5]) == pytest.approx(50.0, abs=1e-6)
    # der Punktabstand greift auch an den Enden des Bogens
    assert cs.check(entities, [cs.POINT_DISTANCE, 0, 0, 10.0, "", 1, 2]) is None


def test_linked_copies_follow_their_original():
    from wolfcad.core import sketch as sk
    # Verschoben: Kreis-Kopie 30 mm rechts; das Original wird per Maß größer und wandert – die Kopie folgt
    entities = [sk.circle(0, 0, 5), sk.circle(30, 0, 5)]
    rules = [[cs.TRANSLATED, 0, 1, 0.0, "", 30.0, 0.0], [cs.DIAMETER, 0, -1, 16.0]]
    solved = cs.solve(entities, rules)
    assert solved[1][3] == pytest.approx(8.0) and solved[1][1] - solved[0][1] == pytest.approx(30.0)
    # Gedreht: Linie um 90° um den Ursprung; wird das Original länger, wird die Kopie es auch
    lines = [sk.line(10, 0, 20, 0), sk.line(0, 10, 0, 20)]
    solved = cs.solve(lines, [[cs.ROTATED, 0, 1, 90.0, "", 0.0, 0.0], [cs.LENGTH, 0, -1, 25.0], [cs.FIX, 0, -1, 0.0]] [:2])
    assert solved is not None and math.dist(solved[1][1:3], solved[1][3:5]) == pytest.approx(25.0, abs=1e-6)
    turned = sk.rotated(solved[0], 0, 0, 90)
    assert solved[1][1:] == pytest.approx(turned[1:], abs=1e-6)
    # Bögen drehen ihre Winkel mit, ein Ellipsenbogen nur seine Drehung
    arcs = [sk.arc(0, 0, 10, 0, 90), sk.arc(0, 0, 10, 90, 180)]
    assert cs.solve(arcs, [[cs.ROTATED, 0, 1, 90.0, "", 0.0, 0.0]]) is not None
    assert cs._value_roles(sk.ellipse_arc(0, 0, 5, 3, 0, 0, 90)) == "xyssass"
    # Konzentrisch: gleiche Mitte, Radius 4 mm größer
    rings = cs.solve([sk.circle(0, 0, 5), sk.circle(1, 1, 9)], [[cs.CONCENTRIC, 0, 1, 4.0], [cs.FIX, 0, -1, 0.0]])
    assert rings[1][1:] == pytest.approx([0.0, 0.0, 9.0], abs=1e-6)
    # falsche Paare werden abgelehnt
    assert cs.check(entities + [sk.line(0, 0, 1, 1)], [cs.TRANSLATED, 0, 2, 0.0, "", 1.0, 0.0]) is not None
    assert cs.check(lines, [cs.CONCENTRIC, 0, 1, 1.0]) is not None


def test_mirrored_spline_stays_linked():
    from wolfcad.core import sketch as sk
    axis = sk.line(0, -50, 0, 50)
    original = sk.spline((5, 0), (15, 10), (25, 0))
    copy = sk.mirrored(original, axis)
    entities = [axis, original, copy]
    rule = [cs.SYMMETRY, 1, 2, 0.0]
    assert cs.check(entities, rule) is None
    moved = [axis, sk.spline((5, 0), (15, 20), (25, 0)), copy]
    solved = cs.solve(moved, [rule, [cs.FIX, 0, -1, 0.0], [cs.FIX, 1, -1, 0.0]])
    assert solved[2][1:] == pytest.approx(sk.mirrored(moved[1], axis)[1:], abs=1e-6)
    handled = [axis, sk.with_handles(original), sk.mirrored(sk.with_handles(original), axis)]
    assert cs.check(handled, rule) is None
    assert cs.pattern_place([cs.TRANSLATED, 0, 1, cs.pattern_code(3, 2, 1), "", 0.0, 0.0]) == (3, 2, 1)


def test_offset_ellipse_stays_concentric():
    from wolfcad.core import sketch as sk
    # nicht im Ursprung: ein Punkt im Ursprung bleibt dort fest
    entities = [sk.ellipse(1, 1, 20, 10, 15), sk.ellipse(1, 1, 23, 13, 15)]
    rule = [cs.CONCENTRIC, 0, 1, 3.0]
    assert cs.check(entities, rule) is None
    moved = [sk.ellipse(5, 2, 30, 12, 40), entities[1]]
    solved = cs.solve(moved, [rule, [cs.FIX, 0, -1, 0.0]])
    assert solved[1][1:] == pytest.approx([5, 2, 33, 15, 40], abs=1e-6)
    assert cs.check([entities[0], sk.circle(0, 0, 5)], rule) is not None


def test_tangent_at_curve_ends_like_fusion():
    """Tangential an Kegelschnitt, Steuerpunkt-Spline und Griff-Spline: am gemeinsamen Ende mit Linie oder Bogen."""
    import math
    from wolfcad.core import sketch as sk

    # Linie endet am Anfang des Kegelschnitts; nach dem Lösen zeigt die Linie in Richtung Scheitel
    entities = [sk.line(-20, 2, 1, 1), sk.conic(1, 1, 21, 1, 11, 11, 0.5)]
    rules = [[cs.TANGENT, 0, 1, 0.0], [cs.FIX, 1, -1, 0.0]]
    assert cs.check(entities, rules[0]) is None
    solved = cs.solve(entities, rules)
    line, curve = solved
    along, towards = (line[3] - line[1], line[4] - line[2]), (curve[5] - curve[1], curve[6] - curve[2])
    assert abs(along[0] * towards[1] - along[1] * towards[0]) / math.hypot(*along) < 1e-6
    # Bogen an den Steuerpunkt-Spline: Tangente steht senkrecht zum Radius
    cp = sk.control_spline((10, 0), (10, 10), (20, 15), (30, 15))
    arc = sk.arc(0, 0, 10, 270, 0)          # endet bei (10, 0)
    rules = [[cs.TANGENT, 0, 1, 0.0], [cs.FIX, 0, -1, 0.0]]
    assert cs.check([arc, cp], rules[0]) is None
    solved = cs.solve([arc, cp], rules)
    assert solved is not None
    first = sk.control_points(solved[1])
    assert abs(first[1][1] - first[0][1]) > 1e-6 and abs(first[1][0] - first[0][0]) < 1e-6     # senkrecht nach oben
    # ohne gemeinsames Ende: Meldung
    assert cs.check([sk.line(-20, 2, -5, 2), sk.conic(1, 1, 21, 1, 11, 11, 0.5)], [cs.TANGENT, 0, 1, 0.0])


def test_coincident_midpoint_collinear_and_free_concentric():
    """Abhängigkeiten wie in Fusions Werkzeugleiste: Deckungsgleich, Mittelpunkt, Kollinear, Konzentrisch (nur Mitte)."""
    import math
    from wolfcad.core import sketch as sk

    entities = [sk.line(2, 2, 12, 2), sk.line(20, 5, 30, 9), sk.circle(5, 8, 3), sk.circle(7, 9, 1)]
    rules = [[cs.FIX, 0, -1, 0.0],
             [cs.COINCIDENT, 1, 0, 0.0, "", 0, 1],      # Anfang von Linie 2 auf das Ende von Linie 1
             [cs.MIDPOINT, 2, 0, 0.0],                  # Kreismitte auf die Mitte von Linie 1
             [cs.CONCENTRIC, 2, 3, 0.0, "", cs.FREE_RADIUS]]
    for rule in rules[1:]:
        assert cs.check(entities, rule) is None, rule
    solved = cs.solve(entities, rules)
    assert solved is not None
    assert math.dist(solved[1][1:3], (12, 2)) < 1e-6
    assert math.dist(solved[2][1:3], (7, 2)) < 1e-6
    assert math.dist(solved[3][1:3], (7, 2)) < 1e-6
    assert solved[3][3] == pytest.approx(1.0) and solved[2][3] == pytest.approx(3.0)     # Radien bleiben frei
    # Kollinear: die zweite Linie legt sich auf die Gerade der ersten
    lines = [sk.line(2, 2, 12, 2), sk.line(20, 5, 30, 9)]
    solved = cs.solve(lines, [[cs.FIX, 0, -1, 0.0], [cs.COLLINEAR, 0, 1, 0.0]])
    assert abs(solved[1][2] - 2) < 1e-6 and abs(solved[1][4] - 2) < 1e-6
    # unpassende Auswahl: Meldungen
    assert cs.check(entities, [cs.MIDPOINT, 0, 2, 0.0])
    assert cs.check(entities, [cs.COLLINEAR, 0, 2, 0.0])
    assert cs.check(entities, [cs.COINCIDENT, 0, 0, 0.0, "", 0, 1])


def test_aligned_points_and_curvature_like_fusion():
    """Waagrecht/Senkrecht zwischen zwei Punkten; Krümmung (G2) zwischen Griff-Spline und Linie bzw. Bogen."""
    import math
    from wolfcad.core import sketch as sk

    entities = [sk.line(2, 2, 12, 2), sk.circle(20, 9, 2)]
    rules = [[cs.FIX, 0, -1, 0.0], [cs.ALIGN_H, 0, 1, 0.0, "", 1, 0]]       # Kreismitte auf Höhe des Linienendes
    assert cs.check(entities, rules[1]) is None
    solved = cs.solve(entities, rules)
    assert abs(solved[1][2] - 2) < 1e-6
    solved = cs.solve(entities, [[cs.FIX, 0, -1, 0.0], [cs.ALIGN_V, 0, 1, 0.0, "", 1, 0]])
    assert abs(solved[1][1] - 12) < 1e-6
    # G2 an einer Linie: am Übergang keine Krümmung → der zweite innere Bézierpunkt liegt auf der Linie
    line = sk.line(-10, 1, 1, 1)
    spline = [sk.HANDLE_SPLINE, 1, 1, 4, 3, 10, 8, 13, 8]
    rules = [[cs.FIX, 0, -1, 0.0], [cs.CURVATURE, 0, 1, 0.0]]
    assert cs.check([line, spline], rules[1]) is None
    solved = cs.solve([line, spline], rules)
    assert solved is not None
    s = solved[1]
    p0, p1, p2 = (s[1], s[2]), (s[3], s[4]), (2 * s[5] - s[7], 2 * s[6] - s[8])
    assert abs(p1[1] - 1) < 1e-6 and abs(p2[1] - 1) < 1e-5          # Richtung und Krümmung wie die Linie
    assert cs.check([line, sk.spline((1, 1), (5, 4), (9, 2))], [cs.CURVATURE, 0, 1, 0.0])   # ohne Griffe: nein


def test_driven_dimensions_and_axis_point_distance():
    """Getriebene Maße (Fusion): treiben nichts, zeigen den gemessenen Wert; überzählige Maße werden erkannt.
    Punktabstand nur waagrecht/senkrecht."""
    from wolfcad.core import sketch as sk

    entities = [sk.line(2, 2, 12, 2)]
    rules = [[cs.HORIZONTAL, 0, -1, 0.0], [cs.LENGTH, 0, -1, 10.0], [cs.FIX, 0, -1, 0.0]]
    # bei fixierter Linie ist jedes weitere Längenmaß überzählig
    assert cs.is_redundant(entities, rules[2:], [cs.LENGTH, 0, -1, 10.0])
    assert not cs.is_redundant(entities, [], [cs.LENGTH, 0, -1, 10.0])
    driven = [cs.LENGTH, 0, -1, 99.0, cs.DRIVEN]
    assert cs.is_driven(driven) and cs.expression_of(driven) == ""
    solved = cs.solve(entities, [rules[0], driven])
    assert math.dist(solved[0][1:3], solved[0][3:5]) == pytest.approx(10.0)     # das getriebene Maß treibt nicht
    cs.update_driven(solved, [driven])
    assert driven[3] == pytest.approx(10.0)                                       # es zeigt den gemessenen Wert
    # Punktabstand waagrecht: nur der x-Abstand wird 20, y bleibt frei
    pair = [sk.point(2, 2), sk.point(12, 9)]
    rule = [cs.POINT_DISTANCE, 0, 1, 20.0, "", 0, 0, "x"]
    assert cs.check(pair, rule) is None
    solved = cs.solve(pair, [[cs.FIX, 0, -1, 0.0], rule])
    assert abs(solved[1][1] - solved[0][1]) == pytest.approx(20.0) and solved[1][2] == pytest.approx(9.0, abs=1e-6)
    assert cs.measured(solved, rule) == pytest.approx(20.0)


def test_rim_distance_like_fusion_tangent_dimension():
    """Abstand bis zum Kreisrand: Linie–Kreis = Mitte–Linie minus Radius, Kreis–Kreis = Mittenabstand minus Radien."""
    from wolfcad.core import sketch as sk

    entities = [sk.line(-10, 0, 10, 0), sk.circle(3, 12, 4), sk.circle(20, 12, 2)]
    assert cs.measure(entities, cs.RIM_DISTANCE, 0, 1) == pytest.approx(8.0)
    assert cs.measure(entities, cs.RIM_DISTANCE, 1, 2) == pytest.approx(11.0)
    rules = [[cs.FIX, 0, -1, 0.0], [cs.RIM_DISTANCE, 0, 1, 5.0]]
    assert cs.check(entities, rules[1]) is None
    solved = cs.solve(entities, rules)
    assert solved[1][2] - solved[1][3] == pytest.approx(5.0)         # Unterkante des Kreises 5 über der Linie
    assert cs.check(entities, [cs.RIM_DISTANCE, 0, 0, 5.0])


def test_point_curvature_on_handle_spline():
    """Krümmungsradius am mittleren Punkt eines Griff-Splines (Fusion: Krümmungsgriff): beide Seiten biegen gleich."""
    from wolfcad.core import sketch as sk
    import numpy as np

    spline = [sk.HANDLE_SPLINE, 1, 1, 6, 5, 20, 12, 26, 12, 40, 1, 44, -3]
    rule = [cs.POINT_CURVATURE, 0, -1, 25.0, "", 1]
    assert cs.check([spline], rule) is None
    solved = cs.solve([spline], [rule])
    assert solved is not None
    values = np.array(solved[0][1:], dtype=float)
    sides = cs._point_curvatures(solved[0], values, 0, 1)
    assert len(sides) == 2 and all(abs(abs(c) - 1 / 25) < 1e-6 for c, _l in sides)
    assert cs.measured(solved, rule) == pytest.approx(25.0, rel=1e-6)
    assert cs.check([sk.spline((0, 0), (5, 5), (10, 0))], [cs.POINT_CURVATURE, 0, -1, 25.0, "", 1])


def test_point_curvature_with_uneven_handles():
    """Krümmungsradius gilt auch, wenn die Griffe je Seite unterschiedlich lang sind; der Löser hält den
    Gegengriff dabei in der Gegenrichtung des Griffs."""
    from wolfcad.core import sketch as sk
    import numpy as np

    even = [sk.HANDLE_SPLINE, 1, 1, 6, 5, 20, 12, 26, 12, 40, 1, 44, -3]
    spline = sk.with_back_tip(even, 1, 8, 12)       # Gegengriff am mittleren Punkt doppelt so lang
    rule = [cs.POINT_CURVATURE, 0, -1, 25.0, "", 1]
    assert cs.check([spline], rule) is None
    solved = cs.solve([spline], [rule])
    assert solved is not None and sk.has_uneven_handles(solved[0])
    sides = cs._point_curvatures(solved[0], np.array(solved[0][1:], dtype=float), 0, 1)
    assert len(sides) == 2 and all(abs(abs(c) - 1 / 25) < 1e-6 for c, _l in sides)
    # Tangential an einer Linie am Ende: Richtung des Endgriffs zählt
    line = sk.line(40, 1, 60, 1)
    tangent = cs.solve([spline, line], [[cs.TANGENT, 0, 1, 0.0]])
    assert tangent is not None
