# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für die Zeichnungsableitung."""

import xml.etree.ElementTree as ET

import pytest

import math

from wolfcad.core import drawing
from wolfcad.core.solids import Box, Cylinder, Hole
from wolfcad.core.timeline import Timeline


def shapes():
    timeline = Timeline()
    timeline.add(Box(length=40, width=20, height=10))
    timeline.add(Hole(diameter=8, offset=10))
    return list(timeline.state.bodies.values())


def test_views_have_model_size_and_hidden_edges():
    front = drawing.project(shapes(), "Vorne")
    assert (front.width, front.height) == pytest.approx((40, 10))
    # Von vorn ist die Bohrung nur als verdeckte Kanten zu sehen
    for wall in (16, 24):
        assert any(all(abs(x - wall) < 1e-6 for x, _y in line) for line in front.hidden)
        assert not any(all(abs(x - wall) < 1e-6 for x, _y in line) for line in front.visible)
    top = drawing.project(shapes(), "Oben")
    assert (top.width, top.height) == pytest.approx((40, 20))
    # Von oben ist der Bohrungsrand sichtbar: Punkte im Abstand 4 von der Mitte (20, 10)
    assert any(all(abs(((x - 20) ** 2 + (y - 10) ** 2) ** 0.5 - 4) < 0.05 for x, y in line) for line in top.visible)
    side = drawing.project(shapes(), "Rechts")
    assert (side.width, side.height) == pytest.approx((20, 10))


def test_scale_fits_the_sheet():
    views = [drawing.project(shapes(), name) for name in drawing.VIEWS]
    assert drawing.choose_scale(views, drawing.SHEETS["A4 quer"]) == (2, 1)
    timeline = Timeline()
    timeline.add(Cylinder(diameter=900, height=400))
    big = [drawing.project(list(timeline.state.bodies.values()), name) for name in drawing.VIEWS]
    assert drawing.choose_scale(big, drawing.SHEETS["A4 quer"]) == (1, 20)


def test_sheet_is_valid_svg_with_title_block():
    text = drawing.sheet_svg(shapes(), "Halter <A&B>", "A3 quer")
    root = ET.fromstring(text)              # gültiges XML, auch mit Sonderzeichen im Titel
    assert root.attrib["viewBox"] == "0 0 420.0 297.0"
    labels = [node.text for node in root.iter("{http://www.w3.org/2000/svg}text")]
    assert "Halter <A&B>" in labels and "40" in labels and "20" in labels and "10" in labels
    assert any(label.startswith("Maßstab") for label in labels)
    plain = drawing.sheet_svg(shapes(), "T", view_names=["Oben"], show_hidden=False, dimensions=False)
    assert "stroke-dasharray" not in plain and "Vorne" not in plain
    with pytest.raises(ValueError):
        drawing.sheet_svg([], "leer")


def test_projection_methods_arrange_views_consistently():
    first, third = list(drawing.METHODS)
    european = drawing.sheet_svg(shapes(), "T", method=first)
    american = drawing.sheet_svg(shapes(), "T", method=third)
    assert ">Links<" in european and ">Rechts<" not in european        # Ansicht von links steht rechts daneben
    assert ">Rechts<" in american and ">Links<" not in american
    # Methode 1: Draufsicht unter der Vorderansicht; Methode 3: darüber
    def height_of(text, label):
        import re
        return float(re.search(r'<text x="[\d.]+" y="([\d.]+)"[^>]*>' + label + "<", text).group(1))
    assert height_of(european, "Oben") > height_of(european, "Vorne")
    assert height_of(american, "Oben") < height_of(american, "Vorne")
    left = drawing.project(shapes(), "Links")
    assert (left.width, left.height) == pytest.approx((20, 10))


def test_section_view_and_diameter_callouts():
    top = drawing.project(shapes(), "Oben")
    assert top.rounds == [pytest.approx((20, 10, 8))]            # die Bohrung, von oben gesehen
    assert drawing.project(shapes(), "Vorne").rounds == []           # von vorn schaut man quer zur Achse
    cut = drawing.section(shapes())
    assert cut.name == drawing.SECTION and (cut.width, cut.height) == pytest.approx((40, 10))
    # Zwei Schnittflächen links und rechts der Bohrung, je 16 × 10
    def area(rim):
        return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(rim, rim[1:] + rim[:1]))) / 2
    assert sorted(round(area(rims[0])) for rims in cut.hatches) == [160, 160]
    # Mit Schnitt geht der Durchgang der Bohrung als sichtbare Kanten durch
    text = drawing.sheet_svg(shapes(), "T", view_names=["Vorne", "Oben", drawing.SECTION])
    assert "Schnitt A–A" in text and 'fill="url(#schraffur)"' in text and "⌀ 8" in text
    assert "Schnitt A–A" not in drawing.sheet_svg(shapes(), "T")      # nur auf Wunsch
    import xml.etree.ElementTree as ET
    ET.fromstring(text)


def test_section_position_cut_line_and_grouped_diameters():
    # Schnitt 15 mm hinter der Mitte: dort gibt es keine Bohrung mehr, also eine durchgehende Schnittfläche
    behind = drawing.section(shapes(), 9.0)
    assert len(behind.hatches) == 1
    assert drawing.section_position(shapes(), 9.0) == pytest.approx(9.0)
    text = drawing.sheet_svg(shapes(), "T", view_names=["Vorne", "Oben", drawing.SECTION])
    assert text.count(">A<") == 2                              # Schnittlinie mit zwei Buchstaben in der Draufsicht
    assert ">A<" not in drawing.sheet_svg(shapes(), "T")
    # Bohrung mit Senkung: beide Durchmesser in einer Beschriftung
    from wolfcad.core.solids import Box, Hole
    timeline = Timeline()
    timeline.add(Box(length=40, width=20, height=10))
    timeline.add(Hole(diameter=6, offset=10, head="Zylindersenkung", head_diameter=11, head_depth=4))
    sunk = drawing.sheet_svg(list(timeline.state.bodies.values()), "T", view_names=["Oben"])
    assert ">⌀ 6 / ⌀ 11<" in sunk


def test_own_dimensions_and_snap_points():
    layout = drawing.arrange(shapes(), view_names=["Vorne", "Oben"])
    points = layout.snap_points()
    top = [(view, point) for view, point, _sheet in points if view == "Oben"]
    assert ("Oben", (0.0, 0.0)) in top and ("Oben", (20.0, 10.0)) in top        # Ecke und Bohrungsmitte
    assert layout.to_sheet("Oben", 0, 0)[0] == pytest.approx(layout.to_sheet("Vorne", 0, 0)[0])
    assert layout.to_sheet("Gibtsnicht", 0, 0) is None
    # Maß von der Ecke zur Bohrungsmitte: waagrecht 20, senkrecht 10
    text = drawing.render(layout, "T", extra=[("Oben", (0.0, 0.0), (20.0, 4.0)), ("Oben", (0.0, 0.0), (3.0, 10.0))],
                          marks=[layout.to_sheet("Oben", 20, 10)])
    import re
    own = re.findall(r'fill="#0f7a3d"[^>]*>([\d,]+)<', text)
    assert sorted(own) == ["10", "20"] and "<circle" in text
    assert drawing.sheet_svg(shapes(), "T", extra=[("Oben", (0.0, 0.0), (20.0, 4.0))]).count('fill="#0f7a3d"') == 1
    import xml.etree.ElementTree as ET
    ET.fromstring(text)


def test_aligned_dimension_and_anchors():
    layout = drawing.arrange(shapes(), view_names=["Oben"])
    extra = [("Oben", (0.0, 0.0), (20.0, 10.0), drawing.ALIGNED), ("Oben", (0.0, 0.0), (20.0, 10.0)),
             ("Fehlt", (0.0, 0.0), (1.0, 1.0))]
    text = drawing.render(layout, "T", extra=extra, lost={1})
    import re
    assert re.findall(r'fill="#0f7a3d"[^>]*>([\d,]+)<', text) == ["22,36"]         # wahrer Abstand √(20²+10²)
    assert re.findall(r'fill="#c62828"[^>]*>([^<]+)<', text) == ["20 ?"]           # verlorenes Maß rot mit Fragezeichen
    anchors = drawing.dimension_anchors(layout, extra)
    assert anchors[2] is None and anchors[0] != anchors[1]
    import xml.etree.ElementTree as ET
    ET.fromstring(text)


def test_dimensions_follow_a_changed_model():
    def sheet(length):
        timeline = Timeline()
        timeline.add(Box(length=length, width=20, height=10))
        return drawing.arrange(list(timeline.state.bodies.values()), view_names=["Oben"])
    old, new = sheet(40), sheet(60)
    size = {"Oben": [old.placed[0].view.width, old.placed[0].view.height]}
    kept, lost = drawing.resnap(new, [["Oben", [0.0, 0.0], [40.0, 0.0], ""], ["Oben", [0.0, 0.0], [40.0, 20.0]]], size)
    assert not lost and kept[0][2] == [60.0, 0.0] and kept[1][2] == [60.0, 20.0]    # die Ecken wandern mit
    # ein Punkt mitten im Nichts findet keinen Fangpunkt: das Maß bleibt stehen und gilt als verloren
    kept, lost = drawing.resnap(new, [["Oben", [0.0, 0.0], [30.0, 10.0]], ["Aus", [0.0, 0.0], [1.0, 1.0]]])
    assert lost == {0} and kept[0][2] == [30.0, 10.0] and kept[1][0] == "Aus"


def test_views_can_be_moved():
    plain = drawing.arrange(shapes(), view_names=["Vorne", "Oben"])
    moved = drawing.arrange(shapes(), view_names=["Vorne", "Oben"], offsets={"Oben": (12.0, -5.0)})
    a, b = plain.to_sheet("Oben", 0, 0), moved.to_sheet("Oben", 0, 0)
    assert (b[0] - a[0], b[1] - a[1]) == pytest.approx((12.0, -5.0))
    assert moved.to_sheet("Vorne", 0, 0) == pytest.approx(plain.to_sheet("Vorne", 0, 0))
    assert moved.view_at(*b) == "Oben" and moved.view_at(1.0, 1.0) is None
    moved.move("Oben", -12.0, 5.0)
    assert moved.to_sheet("Oben", 0, 0) == pytest.approx(a)


def test_cross_section_b_b():
    # Platte 40 × 20 × 10 mit Bohrung ⌀ 6 in der Mitte: quer geschnitten bleibt ein 20 × 10 großes Bild
    cut = drawing.section(shapes(), name=drawing.CROSS_SECTION)
    assert cut.name == drawing.CROSS_SECTION and (cut.width, cut.height) == pytest.approx((20, 10))
    assert len(cut.hatches) == 2                                   # links und rechts der Bohrung je eine Fläche
    beside = drawing.section(shapes(), 10.0, drawing.CROSS_SECTION)
    assert len(beside.hatches) == 1                                # neben der Bohrung eine volle Fläche
    layout = drawing.arrange(shapes(), view_names=["Vorne", "Oben", drawing.SECTION, drawing.CROSS_SECTION],
                             cross_shift=10.0)
    assert layout.cut == pytest.approx(10) and layout.cross == pytest.approx(30)
    text = drawing.render(layout, "T")
    assert text.count(">B<") == 2 and text.count(">A<") == 2 and "Schnitt B–B" in text
    assert drawing.arrange(shapes()).cross is None                 # ohne Wunsch kein Schnitt


def test_dimension_line_can_be_placed_and_views_stay_on_the_sheet():
    layout = drawing.arrange(shapes(), view_names=["Oben"])
    left, bottom = layout.to_sheet("Oben", 0, 0)
    free = drawing.dimension_anchors(layout, [("Oben", (0.0, 0.0), (40.0, 0.0), "", [5.0, 20.0])])[0]
    factor = layout.factor                                              # der Platz steht in Modell-mm
    assert free[1] == pytest.approx(bottom + 20.0 * factor - 1.0)       # waagrechte Maßlinie durch den Platz
    side = drawing.dimension_anchors(layout, [("Oben", (0.0, 0.0), (0.0, 20.0), "", [-15.0, 3.0])])[0]
    assert side[0] == pytest.approx(left - 15.0 * factor - 1.0)
    # schräges Maß: der Platz legt Seite und Abstand fest – einmal oberhalb, einmal unterhalb der Verbindung
    above = drawing.dimension_anchors(layout, [("Oben", (0.0, 0.0), (40.0, 20.0), drawing.ALIGNED, [0.0, -60.0])])[0]
    below = drawing.dimension_anchors(layout, [("Oben", (0.0, 0.0), (40.0, 20.0), drawing.ALIGNED, [60.0, 0.0])])[0]
    assert above[1] < below[1]
    # eine Ansicht lässt sich nicht aus dem Blatt schieben
    moved = layout.move("Oben", -1000.0, 1000.0)
    placed = layout.placed[0]
    assert moved[0] > -1000 and placed.left == pytest.approx(6.0) and placed.bottom == pytest.approx(210.0 - 6.0)


def test_grid_patch_grows_with_the_drawn_area():
    from wolfcad.gui.viewport import grid_patch
    assert grid_patch([]) == (-60.0, -60.0, 60.0, 60.0)                 # leerer Entwurf: Feld um den Ursprung
    low_x, low_y, high_x, high_y = grid_patch([(100.0, 20.0), (400.0, 60.0)])
    # das Feld schließt Ursprung und Gezeichnetes ein und lässt ringsum Rand (35 % der Größe)
    assert low_x == pytest.approx(-140.0) and high_x == pytest.approx(540.0)
    assert low_y == pytest.approx(-140.0) and high_y == pytest.approx(200.0)
    small = grid_patch([(5.0, 5.0)])
    assert small[2] - small[0] == pytest.approx(120.0) and small[0] < 0 < small[2]    # Mindestgröße


def test_fillets_get_a_radius_dimension_not_a_diameter():
    from wolfcad.core.features import Fillet
    timeline = Timeline()
    timeline.add(Box(length=40, width=20, height=10))
    timeline.add(Hole(diameter=6, offset=10))
    # alle senkrechten Kanten verrunden: in der Draufsicht vier Viertelkreise
    edges = [[x, y, 5.0] for x in (-20.0, 20.0) for y in (-10.0, 10.0)]
    timeline.add(Fillet(radius=4.0, edge_points=edges))
    assert not timeline.errors
    view = drawing.project(list(timeline.state.bodies.values()), "Oben")
    assert len(view.arcs) == 4 and {arc[2] for arc in view.arcs} == {4.0}
    assert [round(d, 3) for _x, _y, d in view.rounds] == [6.0]            # die Bohrung bleibt ein Durchmesser
    # der Punkt des Bogens liegt im Abstand des Radius von der Mitte
    cx, cy, radius, px, py = view.arcs[0]
    assert math.hypot(px - cx, py - cy) == pytest.approx(radius, abs=1e-6)
    text = drawing.sheet_svg(list(timeline.state.bodies.values()), "T", view_names=["Oben"])
    assert ">4× R 4<" in text and text.count("⌀") == 1
    assert "R 4" not in drawing.sheet_svg(list(timeline.state.bodies.values()), "T", view_names=["Oben"], dimensions=False)


def test_angle_dimension():
    layout = drawing.arrange(shapes(), view_names=["Oben"])
    # Scheitel an der Ecke, ein Schenkel längs der Unterkante, der andere zur gegenüberliegenden Ecke (40 × 20)
    entry = ["Oben", [0.0, 0.0], [40.0, 0.0], drawing.ANGLE, None, [40.0, 20.0]]
    text = drawing.render(layout, "T", extra=[entry])
    import re
    assert re.findall(r'fill="#0f7a3d"[^>]*>([^<]+)<', text) == ["26,57°"]          # arctan(20/40)
    assert drawing.dimension_anchors(layout, [entry])[0] is not None
    kept, lost = drawing.resnap(layout, [entry])
    assert not lost and kept[0][5] == [40.0, 20.0]
    # ohne dritten Punkt gibt es nichts zu zeichnen
    assert drawing.dimension_anchors(layout, [["Oben", [0.0, 0.0], [40.0, 0.0], drawing.ANGLE]]) == [None]
    import xml.etree.ElementTree as ET
    ET.fromstring(text)


def test_detail_view_magnifies_a_circular_cutout():
    # Platte 40 × 20 mit Bohrung ⌀ 8 in der Mitte: Ausschnitt mit Radius 5 um die Bohrung, dreifach vergrößert
    detail = {"ansicht": "Oben", "mitte": [20.0, 10.0], "radius": 5.0, "faktor": 3.0}
    layout = drawing.arrange(shapes(), view_names=["Vorne", "Oben"], detail=detail)
    view = next(placed.view for placed in layout.placed if placed.view.name == drawing.DETAIL)
    assert (view.width, view.height) == pytest.approx((30.0, 30.0))
    # im Ausschnitt liegen nur die Bohrung (vergrößert auf ⌀ 24) und der Randkreis – keine Plattenkante
    points = [point for line in view.visible[:-1] for point in line]
    assert points and all(math.dist(point, (15.0, 15.0)) == pytest.approx(12.0, abs=0.05) for point in points)
    assert all(math.dist(point, (15.0, 15.0)) == pytest.approx(15.0, abs=1e-6) for point in view.visible[-1])
    text = drawing.render(layout, "T")
    assert "Detail C  (" in text and ">C<" in text and "<circle" in text
    assert all(name != drawing.DETAIL for name, _point, _sheet in layout.snap_points())
    # eine Kante, die den Kreis quert, wird am Rand abgeschnitten
    cut = drawing._clip_to_circle([[(-10.0, 0.0), (10.0, 0.0)], [(0.0, 20.0), (5.0, 20.0)]], 0.0, 0.0, 4.0)
    assert cut == [[pytest.approx((-4.0, 0.0)), pytest.approx((4.0, 0.0))]]
    assert drawing.arrange(shapes(), view_names=["Vorne"], detail=detail).detail is None      # Quelle nicht gezeigt


def test_section_in_any_direction_several_details_and_placed_angle():
    # Platte 40 × 20 × 10 mit Bohrung in der Mitte: um 90° gedreht schneidet A–A quer – das Bild ist 20 breit
    turned = drawing.section(shapes(), angle=90.0)
    assert (turned.width, turned.height) == pytest.approx((20.0, 10.0), abs=1e-6) and len(turned.hatches) == 2
    straight = drawing.section(shapes())
    assert (straight.width, straight.height) == pytest.approx((40.0, 10.0), abs=1e-6)
    slanted = drawing.section(shapes(), angle=30.0)
    assert slanted.width > 20.0 and slanted.hatches
    layout = drawing.arrange(shapes(), view_names=["Oben", drawing.SECTION], section_angle=90.0)
    (x1, y1), (x2, y2) = layout.cut_line
    assert x1 == pytest.approx(x2) == pytest.approx(20.0) and layout.cut is None      # senkrechte Schnittlinie durch die Mitte
    assert drawing.render(layout, "T").count(">A<") == 2
    assert drawing.arrange(shapes(), view_names=["Oben", drawing.SECTION]).cut == pytest.approx(10.0)
    # zwei Detailansichten bekommen die Buchstaben C und D
    details = [{"ansicht": "Oben", "mitte": [20.0, 10.0], "radius": 5.0, "faktor": 2.0},
               {"ansicht": "Oben", "mitte": [2.0, 2.0], "radius": 3.0, "faktor": 4.0}]
    both = drawing.arrange(shapes(), view_names=["Oben"], detail=details)
    assert [placed.view.name for placed in both.placed] == ["Oben", "Detail C", "Detail D"]
    text = drawing.render(both, "T")
    assert ">C<" in text and ">D<" in text and "Detail D  (" in text
    # der Winkelbogen läuft durch die gewählte Stelle: 30 mm (Modell) vom Scheitel entfernt
    entry = ["Oben", [0.0, 0.0], [40.0, 0.0], drawing.ANGLE, [30.0, 0.0], [40.0, 20.0]]
    plain = drawing.arrange(shapes(), view_names=["Oben"])
    anchor = drawing.dimension_anchors(plain, [entry])[0]
    apex = plain.to_sheet("Oben", 0, 0)
    assert math.dist(anchor, (apex[0], apex[1] + 1.0)) == pytest.approx(30.0 * plain.factor + 3.5, abs=1e-6)


def test_radius_dimension_set_by_hand():
    layout = drawing.arrange(shapes(), view_names=["Oben"])
    # Bohrung ⌀ 8 in der Mitte der Platte: Radiusmaß von der Mitte zum Punkt auf dem Kreis, Zahl 12 mm daneben
    entry = ["Oben", [20.0, 10.0], [24.0, 10.0], drawing.RADIUS, [36.0, 10.0]]
    text = drawing.render(layout, "T", extra=[entry])
    import re
    assert re.findall(r'fill="#0f7a3d"[^>]*>([^<]+)<', text) == ["R 4"]
    left, bottom = layout.to_sheet("Oben", 0, 0)
    anchor = drawing.dimension_anchors(layout, [entry])[0]
    assert anchor[0] == pytest.approx(left + 36.0 * layout.factor + 4.0)
    kept, lost = drawing.resnap(layout, [entry])
    assert not lost
