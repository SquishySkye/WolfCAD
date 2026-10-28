# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für die eigenen Volumen-Werkzeuge (Grundkörper, Drehen, Verschieben, Kombinieren, Spiegeln, Schale)."""

import math

import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from wolfcad.core.features import CircleSketch, Fillet, RectangleSketch
from wolfcad.core.solids import Box, CombineBodies, Cylinder, MirrorBody, MoveBody, Revolve, Shell, Sphere
from wolfcad.core.timeline import Timeline


def measure(shape):
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    centre = properties.CentreOfMass()
    return properties.Mass(), (centre.X(), centre.Y(), centre.Z())


def build(*features) -> Timeline:
    timeline = Timeline()
    for feature in features:
        timeline.add(feature)
    return timeline


def test_primitives_have_expected_volume_and_position():
    box = build(Box(length=40, width=20, height=10, center_x=5))
    volume, centre = measure(box.state.body)
    assert volume == pytest.approx(8000)
    assert centre == pytest.approx((5, 0, 5))

    down = build(Box(length=10, width=10, height=-4))
    assert measure(down.state.body)[1] == pytest.approx((0, 0, -2))

    cylinder = build(Cylinder(plane="XZ", diameter=10, height=20))
    volume, centre = measure(cylinder.state.body)
    assert volume == pytest.approx(math.pi * 25 * 20)
    assert centre == pytest.approx((0, -10, 0))     # Normale der XZ-Ebene zeigt nach vorn (-Y)

    sphere = build(Sphere(diameter=6, center_z=9))
    assert measure(sphere.state.body)[0] == pytest.approx(4 / 3 * math.pi * 27)
    assert not (box.errors or down.errors or cylinder.errors or sphere.errors)


def test_revolve_makes_ring_and_rejects_profile_on_axis():
    # Rechteck 4 x 10 mit Abstand 8 von der Y-Achse der Skizze -> Ring mit Rechteckquerschnitt
    ring = build(RectangleSketch(name="S", width=4, height=10, center_x=10),
                 Revolve(sketch="S", angle=360))
    assert not ring.errors
    assert measure(ring.state.body)[0] == pytest.approx(math.pi * (12 ** 2 - 8 ** 2) * 10, rel=1e-6)

    half = build(RectangleSketch(name="S", width=4, height=10, center_x=10),
                 Revolve(sketch="S", angle=180))
    assert measure(half.state.body)[0] == pytest.approx(math.pi * (12 ** 2 - 8 ** 2) * 10 / 2, rel=1e-6)


def test_move_and_combine_act_on_named_bodies():
    timeline = build(Box(length=10, width=10, height=10), Box(length=10, width=10, height=10))
    timeline.add(MoveBody(body="Körper2", move_x=5))
    assert measure(timeline.state.bodies["Körper1"])[1][0] == pytest.approx(0)
    assert measure(timeline.state.bodies["Körper2"])[1][0] == pytest.approx(5)

    timeline.add(CombineBodies(target="Körper1", tool="Körper2", operation="Ausschneiden"))
    assert not timeline.errors
    assert list(timeline.state.bodies) == ["Körper1"]
    assert measure(timeline.state.body)[0] == pytest.approx(500)

    timeline.add(CombineBodies(target="Körper1", tool="Körper2"))
    assert "Körper2" in timeline.errors[4]


def test_rotation_turns_body_about_axis():
    timeline = build(Box(length=20, width=2, height=2, center_x=10), MoveBody(axis="Z", angle=90))
    assert measure(timeline.state.body)[1] == pytest.approx((0, 10, 1), abs=1e-9)


def test_mirror_creates_copy_or_joins():
    copy = build(Box(length=10, width=10, height=10, center_x=20), MirrorBody(plane="YZ"))
    assert len(copy.state.bodies) == 2
    assert measure(copy.state.bodies["Körper2"])[1][0] == pytest.approx(-20)

    joined = build(Box(length=10, width=10, height=10, center_x=5),
                   MirrorBody(plane="YZ", operation="Vereinigen"))
    assert list(joined.state.bodies) == ["Körper1"]
    assert measure(joined.state.body)[0] == pytest.approx(2000)


def test_shell_hollows_body_with_open_top():
    timeline = build(Box(length=20, width=20, height=10), Shell(thickness=2, opening="Oben (+Z)"))
    assert not timeline.errors
    # Außen 20 x 20 x 10, innen 16 x 16 x 8 (oben offen)
    assert measure(timeline.state.body)[0] == pytest.approx(4000 - 16 * 16 * 8)

    too_thick = build(Box(length=20, width=20, height=10), Shell(thickness=50))
    assert "Wandstärke" in too_thick.errors[1]


def test_fillet_can_be_limited_to_one_body():
    timeline = build(Box(length=10, width=10, height=10), Box(length=10, width=10, height=10, center_x=30),
                     Fillet(radius=1, body="Körper2"))
    assert not timeline.errors
    assert measure(timeline.state.bodies["Körper1"])[0] == pytest.approx(1000)
    assert measure(timeline.state.bodies["Körper2"])[0] < 1000


def test_new_tools_survive_saving_and_loading(tmp_path):
    timeline = build(Cylinder(diameter=10, height=5), Sphere(diameter=4, operation="Ausschneiden"),
                     CircleSketch(name="K", radius=1))
    path = tmp_path / "entwurf.wcad"
    timeline.save(path)
    loaded = Timeline.load(path)
    assert [f.to_dict() for f in loaded.features] == [f.to_dict() for f in timeline.features]
    assert measure(loaded.state.body)[0] == pytest.approx(measure(timeline.state.body)[0])
    restored = Timeline.from_document(timeline.to_document())
    assert len(restored.features) == 3


def test_fillet_only_rounds_selected_edges():
    from wolfcad.core import features as ft

    plain = build(Box(length=20, width=20, height=10))
    edges = ft.unique_edges(plain.state.body)
    assert len(edges) == 12
    # Die vier senkrechten Kanten wählen (ihr Merkpunkt liegt auf halber Höhe)
    vertical = [ft.edge_point(edge) for edge in edges if abs(ft.edge_point(edge)[2] - 5) < 1e-6]
    assert len(vertical) == 4

    chosen = build(Box(length=20, width=20, height=10), Fillet(radius=2, edge_points=vertical))
    everything = build(Box(length=20, width=20, height=10), Fillet(radius=2))
    assert not chosen.errors
    # Vier Kanten der Länge 10: je (4 - pi) * r² / 4 Querschnitt entfällt
    assert measure(chosen.state.body)[0] == pytest.approx(4000 - 4 * (4 - math.pi) * 10, rel=1e-6)
    assert measure(everything.state.body)[0] < measure(chosen.state.body)[0]


def test_selection_that_no_longer_exists_is_reported():
    timeline = build(Box(length=20, width=20, height=10), Fillet(radius=1, edge_points=[(99.0, 0.0, 0.0)]))
    assert "nicht mehr vorhanden" in timeline.errors[1]


def test_selected_edges_survive_a_change_upstream():
    from wolfcad.core import features as ft

    timeline = build(Box(length=20, width=20, height=10))
    top_front = next(ft.edge_point(e) for e in ft.unique_edges(timeline.state.body)
                     if ft.edge_point(e) == pytest.approx((0, -10, 10)))
    timeline.add(Fillet(radius=2, edge_points=[top_front]))
    assert not timeline.errors
    # Die Länge ändert die Lage dieser Kantenmitte nicht: die Auswahl bleibt gültig
    timeline.update(0, length=30)
    assert not timeline.errors
    # Die Breite verschiebt die Kante: wie in Fusion folgt die Rundung ihr (Merkpunkt nachgeführt, seit Runde 63)
    timeline.update(0, width=40)
    assert not timeline.errors
    assert timeline.features[1].edge_points[0] == pytest.approx([0, -20, 10])


def test_shell_removes_selected_face():
    from wolfcad.core import features as ft

    box = build(Box(length=20, width=20, height=10))
    side = next(ft.face_point(f) for f in ft.faces_of(box.state.body)
                if ft.face_point(f) == pytest.approx((10, 0, 5)))
    timeline = build(Box(length=20, width=20, height=10), Shell(thickness=2, face_points=[side]))
    assert not timeline.errors
    # Offen nach +X: innen 18 x 16 x 6
    assert measure(timeline.state.body)[0] == pytest.approx(4000 - 18 * 16 * 6)


def test_shell_works_after_a_fillet():
    from wolfcad.core import features as ft

    box = build(Box(length=60, width=40, height=20))
    edge = next(ft.edge_point(e) for e in ft.unique_edges(box.state.body)
                if ft.edge_point(e) == pytest.approx((0, -20, 20)))
    timeline = build(Box(length=60, width=40, height=20), Fillet(radius=4, edge_points=[edge]))
    solid_volume = measure(timeline.state.body)[0]
    top = max((ft.face_point(f) for f in ft.faces_of(timeline.state.body)), key=lambda point: point[2])
    timeline.add(Shell(thickness=2, face_points=[top]))
    assert not timeline.errors
    assert 0 < measure(timeline.state.body)[0] < solid_volume / 2
    # Die Wand muss innen entstehen: die äußeren Abmessungen bleiben 60 x 40 x 20
    from wolfcad.core.solids import _bounds
    assert _bounds(timeline.state.body) == pytest.approx((-30, -20, 0, 30, 20, 20), abs=1e-6)


def test_automatic_operation_cuts_inside_joins_outside_and_starts_new_bodies():
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import AUTO_OPERATION, Extrude

    top = [0, 0, 10, 0, 0, 1, 1, 0, 0]      # Deckfläche eines 40 x 40 x 10 Quaders
    def with_extrusion(distance):
        return build(Box(length=40, width=40, height=10),
                     sk.Sketch(name="S", frame=top, entities=[sk.circle(0, 0, 5)]),
                     Extrude(name="E", sketch="S", distance=distance, operation=AUTO_OPERATION))

    pocket = with_extrusion(-4)             # in den Körper hinein: ausschneiden
    assert measure(pocket.state.body)[0] == pytest.approx(16000 - math.pi * 25 * 4, rel=1e-6)
    boss = with_extrusion(6)                # aus dem Körper heraus: vereinigen
    assert measure(boss.state.body)[0] == pytest.approx(16000 + math.pi * 25 * 6, rel=1e-6)
    assert list(pocket.state.bodies) == list(boss.state.bodies) == ["Körper1"]

    apart = build(Box(length=10, width=10, height=10),
                  Box(length=10, width=10, height=10, center_x=50, operation=AUTO_OPERATION))
    assert len(apart.state.bodies) == 2     # keine Berührung: neuer Körper


def test_rectangular_pattern_copies_in_a_grid():
    from wolfcad.core.solids import RectangularPattern

    grid = build(Box(length=10, width=10, height=10),
                 RectangularPattern(count=3, spacing=20, second_count=2, second_spacing=30))
    assert not grid.errors
    assert len(grid.state.bodies) == 6
    centres = sorted((round(measure(body)[1][0]), round(measure(body)[1][1])) for body in grid.state.bodies.values())
    assert centres == [(0, 0), (0, 30), (20, 0), (20, 30), (40, 0), (40, 30)]

    joined = build(Box(length=10, width=10, height=10),
                   RectangularPattern(count=3, spacing=10, operation="Vereinigen"))
    assert list(joined.state.bodies) == ["Körper1"]
    assert measure(joined.state.body)[0] == pytest.approx(3000)

    # Anzahl 1 ist seit Runde 88 der neutrale Startwert: kein Fehler, das Modell bleibt unverändert
    single = build(Box(length=10, width=10, height=10), RectangularPattern(count=1))
    assert not single.errors and len(single.state.bodies) == 1


def test_circular_pattern_spreads_copies_around_the_axis():
    from wolfcad.core.solids import CircularPattern

    ring = build(Cylinder(diameter=4, height=5, center_x=20), CircularPattern(count=4))
    assert not ring.errors
    centres = sorted((round(measure(body)[1][0]), round(measure(body)[1][1])) for body in ring.state.bodies.values())
    assert centres == [(-20, 0), (0, -20), (0, 20), (20, 0)]

    # Teilwinkel: erstes und letztes Exemplar liegen an den Enden (0 und 90 Grad)
    quarter = build(Cylinder(diameter=4, height=5, center_x=20), CircularPattern(count=3, angle=90))
    centres = sorted((round(measure(body)[1][0]), round(measure(body)[1][1])) for body in quarter.state.bodies.values())
    assert centres == [(0, 20), (14, 14), (20, 0)]


def test_sweep_follows_path_with_corner_and_arc():
    from wolfcad.core import sketch as sk
    from wolfcad.core.solids import Sweep
    # Kreisprofil (r = 3) in XY, Pfad in XZ: 40 mm hoch, dann 30 mm zur Seite
    corner = build(CircleSketch(name="Profil", radius=3),
                   sk.Sketch(name="Pfad", plane="XZ", entities=[sk.line(0, 0, 0, 40), sk.line(0, 40, 30, 40)]),
                   Sweep(sketch="Profil", path="Pfad"))
    assert not corner.errors
    assert measure(corner.state.body)[0] == pytest.approx(math.pi * 9 * 70, rel=1e-3)
    bent = build(CircleSketch(name="Profil", radius=3),
                 sk.Sketch(name="Pfad", plane="XZ", entities=[sk.line(0, 0, 0, 30), sk.arc(10, 30, 10, 90, 180)]),
                 Sweep(sketch="Profil", path="Pfad"))
    assert measure(bent.state.body)[0] == pytest.approx(math.pi * 9 * (30 + 5 * math.pi), rel=2e-3)


def test_sweep_reports_broken_path():
    from wolfcad.core import sketch as sk
    from wolfcad.core.solids import Sweep
    broken = build(CircleSketch(name="Profil", radius=3),
                   sk.Sketch(name="Pfad", plane="XZ", entities=[sk.line(0, 0, 0, 40), sk.line(20, 0, 20, 40)]),
                   Sweep(sketch="Profil", path="Pfad"))
    assert "zusammenhängender" in next(iter(broken.errors.values()))
    same = build(CircleSketch(name="Profil", radius=3), Sweep(sketch="Profil", path="Profil"))
    assert same.errors


def test_loft_between_two_and_three_sketches():
    from wolfcad.core.solids import Loft
    # Pyramidenstumpf: Quadrat 20 unten, Quadrat 10 in 30 mm Höhe -> V = h/3 (A1 + A2 + sqrt(A1 A2))
    frustum = build(RectangleSketch(name="Unten", width=20, height=20),
                    RectangleSketch(name="Oben", width=10, height=10, offset=30),
                    Loft(sketch="Unten", sketch2="Oben"))
    assert not frustum.errors
    assert measure(frustum.state.body)[0] == pytest.approx(30 / 3 * (400 + 100 + 200), rel=1e-3)
    three = build(CircleSketch(name="A", radius=10), CircleSketch(name="B", radius=4, offset=20),
                  CircleSketch(name="C", radius=10, offset=40), Loft(sketch="A", sketch2="B", sketch3="C"))
    assert not three.errors
    volume, centre = measure(three.state.body)
    assert 0 < volume < math.pi * 100 * 40 and centre[2] == pytest.approx(20, abs=0.5)
    assert build(CircleSketch(name="A", radius=10), Loft(sketch="A", sketch2="A")).errors


def test_every_solid_feature_can_be_loaded_again():
    import dataclasses
    from wolfcad.core import features, solids
    own = [cls for cls in vars(solids).values()
           if dataclasses.is_dataclass(cls) and issubclass(cls, features.Feature) and cls.__module__ == solids.__name__]
    assert solids.Sweep in own and solids.Loft in own
    for cls in own:
        assert features.FEATURE_CLASSES.get(cls.KIND) is cls, f"{cls.__name__} ist nicht für das Laden angemeldet"


def test_thread_on_bolt_and_in_hole():
    from wolfcad.core.features import face_point, faces_of
    from wolfcad.core.solids import Hole, Thread, iso_pitch
    assert iso_pitch(10) == 1.5 and iso_pitch(6.2) == 1.0
    # Außengewinde M10 auf einem Bolzen: Material wird abgetragen, der Körper bleibt gültig
    bolt = build(Cylinder(diameter=10, height=12), Thread())
    assert not bolt.errors
    plain = math.pi * 25 * 12
    assert plain * 0.85 < measure(bolt.state.body)[0] < plain * 0.99
    # Innengewinde in einer Bohrung: gewählt wird die Zylinderfläche der Bohrung
    block = build(Box(length=30, width=30, height=10), Hole(diameter=8, depth=0, offset=10))
    before = measure(block.state.body)[0]
    wall = next(face for face in faces_of(block.state.body)
                if len(measure_lines(face)) > 1)
    threaded = build(Box(length=30, width=30, height=10), Hole(diameter=8, depth=0, offset=10),
                     Thread(face_points=[list(face_point(wall))]))
    assert not threaded.errors
    assert before == pytest.approx(9000 - math.pi * 16 * 10, rel=1e-3)      # die Bohrung geht ganz durch
    assert before * 0.97 < measure(threaded.state.body)[0] < before      # die Rille liegt außerhalb der Bohrung
    # Auf einer ebenen Fläche gibt es kein Gewinde
    flat = build(Box(length=30, width=30, height=10), Thread())
    assert "Zylinderfläche" in next(iter(flat.errors.values()))


def measure_lines(face):
    from wolfcad.core import measure as measuring
    return measuring.describe("face", face)


def test_split_scale_and_draft():
    from wolfcad.core.solids import DRAFT_NEUTRAL, SPLIT_KEEP, Draft, ScaleBody, SplitBody
    # Teilen: Quader 40 x 20 x 10 an der YZ-Ebene bei x = 5
    both = build(Box(length=40, width=20, height=10), SplitBody(plane="YZ", offset=5))
    volumes = sorted(measure(shape)[0] for shape in both.state.bodies.values())
    assert volumes == pytest.approx([3000, 5000]) and list(both.state.bodies) == ["Körper1", "Körper2"]
    assert measure(both.state.bodies["Körper1"])[1][0] == pytest.approx(12.5)     # Seite in Pfeilrichtung behält den Namen
    one = build(Box(length=40, width=20, height=10), SplitBody(plane="YZ", offset=5, keep=SPLIT_KEEP[2]))
    assert len(one.state.bodies) == 1 and measure(one.state.body)[0] == pytest.approx(5000)
    assert "schneidet keinen" in next(iter(build(Box(length=40, width=20, height=10),
                                                 SplitBody(plane="YZ", offset=50)).errors.values()))
    # Skalieren um die Körpermitte: Volumen mal Faktor hoch drei, Mitte bleibt
    scaled = build(Box(length=40, width=20, height=10, center_x=30), ScaleBody(factor=0.5))
    volume, centre = measure(scaled.state.body)
    assert volume == pytest.approx(8000 / 8) and centre == pytest.approx((30, 0, 5))
    # Schräge 5° an allen Seitenwänden: Pyramidenstumpf, der Fuß bleibt 40 x 20
    drafted = build(Box(length=40, width=20, height=10), Draft(angle=5))
    assert not drafted.errors
    shrink = 2 * 10 * math.tan(math.radians(5))
    # Volumen eines Körpers mit ebenen Seiten: h/6 · (Fuß + Kopf + 4 · Mittelschnitt)
    top, middle = (40 - shrink) * (20 - shrink), (40 - shrink / 2) * (20 - shrink / 2)
    assert measure(drafted.state.body)[0] == pytest.approx(10 / 6 * (800 + top + 4 * middle), rel=1e-6)
    upside = build(Box(length=40, width=20, height=10), Draft(angle=5, neutral=DRAFT_NEUTRAL[1]))
    bottom, middle = (40 + shrink) * (20 + shrink), (40 + shrink / 2) * (20 + shrink / 2)
    assert measure(upside.state.body)[0] == pytest.approx(10 / 6 * (800 + bottom + 4 * middle), rel=1e-6)
    # Winkel 0 ist seit Runde 88 der neutrale Startwert: kein Fehler, Quader unverändert
    flat = build(Box(length=40, width=20, height=10), Draft(angle=0))
    assert not flat.errors and measure(flat.state.body)[0] == pytest.approx(8000)


def test_rib_fills_between_line_and_body():
    from wolfcad.core import sketch as sk
    from wolfcad.core.solids import Rib
    # Winkel aus Bodenplatte und Wand; die Skizze in der XZ-Ebene trägt eine schräge Linie
    angle = [Box(length=40, width=20, height=4, center_x=20),
             Box(length=4, width=20, height=30, center_x=2, operation="Vereinigen")]
    line = sk.Sketch(name="Linie", plane="XZ", entities=[sk.line(4, 24, 30, 4)])
    ribbed = build(*angle, line, Rib(sketch="Linie", thickness=3))
    assert not ribbed.errors and len(ribbed.state.bodies) == 1
    # Dreieck mit den Katheten 26 und 20, 3 mm dick
    assert measure(ribbed.state.body)[0] == pytest.approx(5280 + 0.5 * 26 * 20 * 3, rel=1e-6)
    thicker = build(*angle, line, Rib(sketch="Linie", thickness=6))
    assert measure(thicker.state.body)[0] == pytest.approx(5280 + 0.5 * 26 * 20 * 6, rel=1e-6)
    # Eine Linie fernab des Körpers ergibt keine Rippe
    away = build(*angle, sk.Sketch(name="Linie", plane="XZ", offset=50, entities=[sk.line(4, 24, 30, 4)]),
                 Rib(sketch="Linie"))
    assert "trifft den Körper nicht" in next(iter(away.errors.values()))
    assert build(*angle, sk.Sketch(name="Linie", plane="XZ", entities=[sk.circle(20, 20, 5)]),
                 Rib(sketch="Linie")).errors


def assembly():
    """Platte (liegt auf der XY-Ebene) und ein schräg im Raum stehender Bolzen."""
    return [Box(length=40, width=30, height=10),
            Cylinder(center_x=80, center_y=20, diameter=10, height=25, plane="XZ", operation="Neuer Körper")]


def face_at(timeline, body, test):
    from wolfcad.core.features import face_point, faces_of
    return list(next(face_point(face) for face in faces_of(timeline.state.bodies[body]) if test(face_point(face))))


def test_joint_places_parts_face_to_face():
    from wolfcad.core.solids import JOINT_FACING, JOINT_KINDS, Joint
    base = build(*assembly())
    top = face_at(base, "Körper1", lambda p: abs(p[2] - 10) < 1e-6)            # Deckfläche der Platte
    # Bolzen in der XZ-Ebene: seine Achse liegt entlang Y; die Stirnfläche bei y = 0 wird gefügt
    end = face_at(base, "Körper2", lambda p: abs(p[1]) < 1e-6)
    joined = build(*assembly(), Joint(body="Körper2", target="Körper1", face_points=[end, top]))
    assert not joined.errors
    volume, centre = measure(joined.state.bodies["Körper2"])
    assert volume == pytest.approx(math.pi * 25 * 25)
    assert centre == pytest.approx((0, 0, 22.5), abs=1e-6)     # steht mittig und senkrecht auf der Platte
    assert measure(joined.state.bodies["Körper1"])[1] == pytest.approx((0, 0, 5))      # die Platte bleibt
    # Schieber: Abstand von der Fläche; gleich gerichtet: der Bolzen steckt nach unten in der Platte
    lifted = build(*assembly(), Joint(body="Körper2", target="Körper1", face_points=[end, top],
                                      joint=JOINT_KINDS[2], offset=7))
    assert measure(lifted.state.bodies["Körper2"])[1] == pytest.approx((0, 0, 29.5), abs=1e-6)
    sunk = build(*assembly(), Joint(body="Körper2", target="Körper1", face_points=[end, top],
                                    facing=JOINT_FACING[1]))
    assert measure(sunk.state.bodies["Körper2"])[1] == pytest.approx((0, 0, -2.5), abs=1e-6)
    # Fehlende Auswahl und Gelenk mit sich selbst werden gemeldet
    assert build(*assembly(), Joint(body="Körper2", target="Körper1")).errors
    assert build(*assembly(), Joint(body="Körper1", target="Körper1", face_points=[top, top])).errors


def test_components_move_together_and_can_be_addressed():
    from wolfcad.core.solids import JOINT_KINDS, Component, Joint
    parts = [*assembly(), Box(length=6, width=6, height=6, center_x=80, offset=30, operation="Neuer Körper"),
             Component(name="Schraube", body="Körper2"), Component(name="K2", body="Körper3", component="Schraube")]
    grouped = build(*parts)
    assert grouped.state.components == {"Körper2": "Schraube", "Körper3": "Schraube"}
    before = [measure(grouped.state.bodies[name])[1] for name in ("Körper2", "Körper3")]
    gap = math.dist(*before)
    top = face_at(grouped, "Körper1", lambda p: abs(p[2] - 10) < 1e-6)
    end = face_at(grouped, "Körper2", lambda p: abs(p[1]) < 1e-6)
    # Drehgelenk mit 90°: beide Körper der Komponente wandern gemeinsam, ihr Abstand bleibt
    joined = build(*parts, Joint(body="Körper2", target="Körper1", face_points=[end, top],
                                 joint=JOINT_KINDS[1], angle=90))
    assert not joined.errors
    after = [measure(joined.state.bodies[name])[1] for name in ("Körper2", "Körper3")]
    assert math.dist(*after) == pytest.approx(gap) and after[0] == pytest.approx((0, 0, 22.5), abs=1e-6)
    # Ein Werkzeug mit Körperwahl kann die ganze Komponente ansprechen
    moved = build(*parts, MoveBody(body="Schraube", move_z=5))
    assert [measure(moved.state.bodies[n])[1][2] - b[2] for n, b in zip(("Körper2", "Körper3"), before)] == pytest.approx([5, 5])
    assert measure(moved.state.bodies["Körper1"])[1] == pytest.approx((0, 0, 5))
    assert build(*assembly(), Component(name="Körper1", body="Körper2")).errors     # Name schon vergeben
    restored = Timeline.from_document(joined.to_document())
    assert restored.state.components == joined.state.components


def test_sweep_with_hole_makes_a_tube():
    from wolfcad.core import sketch as sk
    from wolfcad.core.solids import Sweep
    ring = sk.Sketch(name="Ring", entities=[sk.circle(0, 0, 5), sk.circle(0, 0, 3)])
    path = sk.Sketch(name="Pfad", plane="XZ", entities=[sk.line(0, 0, 0, 40)])     # 40 mm nach +Z
    tube = build(ring, path, Sweep(sketch="Ring", path="Pfad"))
    assert not tube.errors
    assert measure(tube.state.body)[0] == pytest.approx(math.pi * (25 - 9) * 40, rel=1e-3)


def test_hole_and_split_on_construction_plane():
    from wolfcad.core import sketch as sk
    from wolfcad.core.solids import Hole, SplitBody
    plane = sk.ConstructionPlane(name="Ebene1", plane="XY", offset=10)
    # Bohrung von der Ebene über dem Quader nach unten: geht ganz durch
    drilled = build(Box(length=40, width=20, height=10), plane, Hole(diameter=8, base="Ebene1"))
    assert not drilled.errors
    assert measure(drilled.state.body)[0] == pytest.approx(8000 - math.pi * 16 * 10, rel=1e-6)
    # Teilen an der Ebene, 4 mm darunter; die Ebene verschieben nimmt den Schnitt mit
    split = build(Box(length=40, width=20, height=10), plane, SplitBody(base="Ebene1", offset=-4))
    assert sorted(measure(b)[0] for b in split.state.bodies.values()) == pytest.approx([3200, 4800])
    split.update(1, offset=9)
    assert sorted(measure(b)[0] for b in split.state.bodies.values()) == pytest.approx([4000, 4000])
    missing = build(Box(length=40, width=20, height=10), Hole(diameter=8, base="Ebene9"))
    assert "Konstruktionsebene" in next(iter(missing.errors.values()))


def test_hole_with_counterbore_and_countersink():
    from wolfcad.core.solids import HOLE_HEADS, Hole
    block = Box(length=40, width=30, height=20)
    plain = math.pi * 3 ** 2 * 20                      # Durchgangsbohrung ⌀ 6
    bore = build(block, Hole(diameter=6, offset=20, head=HOLE_HEADS[1], head_diameter=11, head_depth=6))
    assert not bore.errors
    # Zylindersenkung: zusätzlich ein Ring ⌀ 11 / ⌀ 6, 6 mm tief
    assert measure(bore.state.body)[0] == pytest.approx(24000 - plain - math.pi * (5.5 ** 2 - 9) * 6, rel=1e-6)
    sink = build(block, Hole(diameter=6, offset=20, head=HOLE_HEADS[2], head_diameter=12))
    # Kegelsenkung 90°: Kegelstumpf von r = 6 auf r = 3 über 3 mm, abzüglich des Bohrungskerns
    cone = math.pi * 3 / 3 * (36 + 9 + 18) - math.pi * 9 * 3
    assert measure(sink.state.body)[0] == pytest.approx(24000 - plain - cone, rel=1e-6)
    assert build(block, Hole(diameter=6, offset=20, head=HOLE_HEADS[1], head_diameter=5)).errors


def test_appearance_colours_bodies():
    from wolfcad.core.solids import Appearance, Component
    parts = [Box(length=10, width=10, height=10), Box(length=10, width=10, height=10, center_x=30, operation="Neuer Körper")]
    coloured = build(*parts, Appearance(body="Körper2", color="Rot"))
    assert coloured.state.colors == {"Körper2": "Rot"}
    everything = build(*parts, Component(name="Teil", body="Körper1"), Appearance(body="Teil", color="Messing"),
                       Appearance(color="Blau"), Appearance(body="Körper2", color="Grün"))
    assert everything.state.colors == {"Körper1": "Blau", "Körper2": "Grün"}      # der spätere Schritt gewinnt
    assert build(*parts, Appearance(color="Lila mit Punkten")).errors
    assert Timeline.from_document(coloured.to_document()).state.colors == {"Körper2": "Rot"}


def test_round_one_of_removed_limits():
    from wolfcad.core.solids import HOLE_HEADS, THREAD_SIZES, Hole, ScaleBody, Thread
    block = Box(length=40, width=30, height=20)
    # Kegelsenkung mit 120°: flacher als 90°, also weniger tief
    plain = math.pi * 9 * 20
    wide = build(block, Hole(diameter=6, offset=20, head=HOLE_HEADS[2], head_diameter=12, head_angle=120))
    deep = 3 / math.tan(math.radians(60))
    cone = math.pi * deep / 3 * (36 + 9 + 18) - math.pi * 9 * deep
    assert measure(wide.state.body)[0] == pytest.approx(24000 - plain - cone, rel=1e-6)
    assert build(block, Hole(diameter=6, offset=20, head=HOLE_HEADS[2], head_diameter=12, head_angle=5)).errors
    # Skalieren je Achse: nur Z verdoppeln, die Mitte bleibt
    stretched = build(block, ScaleBody(factor=1.0, factor_z=2.0))
    volume, centre = measure(stretched.state.body)
    assert volume == pytest.approx(48000, rel=1e-6) and centre == pytest.approx((0, 0, 10), abs=1e-6)
    # Gewindegröße: Feingewinde M10 × 1 statt des Regelgewindes 1,5
    assert "M10 × 1" in THREAD_SIZES and THREAD_SIZES[0].startswith("Automatisch")
    assert Thread(size="M10 × 1").chosen_pitch(10) == 1.0 and Thread().chosen_pitch(10) == 1.5
    assert Thread(size="M10 × 1", pitch=0.75).chosen_pitch(10) == 0.75
    fine = build(Cylinder(diameter=10, height=10), Thread(size="M10 × 1"))
    assert not fine.errors and build(Cylinder(diameter=10, height=10), Thread(size="M99")).errors


def test_view_state_is_saved():
    timeline = build(Box(length=10, width=10, height=10))
    timeline.view_state = {"hidden": ["Körper1"]}
    assert Timeline.from_document(timeline.to_document()).view_state == {"hidden": ["Körper1"]}
    document = timeline.to_document()
    del document["ansicht"]
    assert Timeline.from_document(document).view_state == {}


def test_section_face():
    from wolfcad.core.solids import Hole, section_face
    from wolfcad.core.solids import _area
    block = build(Box(length=40, width=30, height=20), Hole(diameter=10, offset=20))
    # Schnitt mitten durch die Bohrung: Rechteck 30 × 20 abzüglich des Bohrungsstreifens 10 × 20
    assert _area(section_face(block.state.body, (1, 0, 0), 0.0)) == pytest.approx(30 * 20 - 10 * 20)
    assert _area(section_face(block.state.body, (1, 0, 0), 15.0)) == pytest.approx(600)
    assert section_face(block.state.body, (1, 0, 0), 99.0) is None


def test_round_two_of_removed_limits():
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import LEGACY_XZ, _plane_axes, face_point, faces_of, unique_edges, edge_point
    from wolfcad.core.solids import Component, Hole, Joint, Rib, interferences
    # XZ-Ebene: y der Skizze zeigt nach oben (+Z); alte Entwürfe behalten ihre Lage
    front, old = _plane_axes("XZ", 0), _plane_axes(LEGACY_XZ, 0)
    assert (front.YDirection().Z(), old.YDirection().Z()) == (1, -1)
    document = build(Cylinder(plane="XZ", diameter=10, height=20)).to_document()
    document["version"] = 1
    legacy = Timeline.from_document(document)
    assert legacy.features[0].plane == LEGACY_XZ and measure(legacy.state.body)[1] == pytest.approx((0, 10, 0))
    assert Timeline.from_document(legacy.to_document()).features[0].plane == LEGACY_XZ
    # Durchgangsbohrung von der Hauptebene: geht durch den Körper, auch wenn er über der Ebene steht
    drilled = build(Box(length=40, width=30, height=20), Hole(diameter=6))
    assert measure(drilled.state.body)[0] == pytest.approx(24000 - math.pi * 9 * 20, rel=1e-6)
    # Rippe an einem Bogen statt einer Linie
    angle = [Box(length=40, width=20, height=4, center_x=20),
             Box(length=4, width=20, height=30, center_x=2, operation="Vereinigen")]
    curved = build(*angle, sk.Sketch(name="Bogen", plane="XZ", entities=[sk.arc(30, 30, 26, 180, 270)]),
                   Rib(sketch="Bogen", thickness=3))
    assert not curved.errors
    # Viertelkreis r = 26 um (30, 30): die Rippe füllt das Quadrat 26 × 26 abzüglich des Kreisviertels
    assert measure(curved.state.body)[0] == pytest.approx(5280 + (26 * 26 - math.pi * 26 * 26 / 4) * 3, rel=1e-4)
    # Komponente mit mehreren Körpern in einem Schritt
    parts = [*assembly(), Box(length=6, width=6, height=6, center_x=80, offset=30, operation="Neuer Körper")]
    one = build(*parts, Component(name="Schraube", body="Körper2", bodies=["Körper3"]))
    assert one.state.components == {"Körper2": "Schraube", "Körper3": "Schraube"}
    # Gelenk mit einer Kreiskante als Bezug: Bolzen mittig auf den Rand einer Bohrung setzen
    plate = [Box(length=40, width=30, height=10), Hole(position_x=10, diameter=8, offset=10),
             Cylinder(center_x=80, diameter=8, height=25, operation="Neuer Körper")]
    base = build(*plate)
    rim = next(list(edge_point(e)) for e in unique_edges(base.state.bodies["Körper1"])
               if abs(edge_point(e)[2] - 10) < 1e-6 and abs(math.dist(edge_point(e)[:2], (10, 0)) - 4) < 1e-6)
    foot = next(list(face_point(f)) for f in faces_of(base.state.bodies["Körper2"]) if abs(face_point(f)[2]) < 1e-6)
    seated = build(*plate, Joint(body="Körper2", target="Körper1", face_points=[foot, rim], kinds=["face", "edge"]))
    assert not seated.errors
    centre = measure(seated.state.bodies["Körper2"])[1]
    assert centre[:2] == pytest.approx((10, 0), abs=1e-6) and abs(centre[2] - 10) == pytest.approx(12.5)
    # Kollisionen: der Bolzen steckt je nach Richtung in der Platte oder steht darüber
    hits = interferences(seated.state)
    assert all(pair[:2] == ("Körper1", "Körper2") for pair in hits)
    overlapping = build(Box(length=20, width=20, height=20), Box(length=20, width=20, height=20, center_x=15,
                                                                 operation="Neuer Körper"))
    assert [(a, b, round(v)) for a, b, v in interferences(overlapping.state)] == [("Körper1", "Körper2", 2000)]
    apart = build(Box(length=20, width=20, height=20), Box(length=20, width=20, height=20, center_x=50,
                                                           operation="Neuer Körper"))
    assert interferences(apart.state) == []


def test_tilted_plane_and_plane_through_edges():
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import edge_point, unique_edges
    tilted = sk.ConstructionPlane(plane="XY", offset=0, tilt=90).axes()
    assert (tilted.Direction().X(), tilted.Direction().Y(), tilted.Direction().Z()) == pytest.approx((0, -1, 0))
    lifted = sk.ConstructionPlane(plane="XY", offset=5, tilt=90, tilt_axis=sk.TILT_AXES[1]).axes()
    assert (lifted.Location().X(), lifted.Location().Y(), lifted.Location().Z()) == pytest.approx((5, 0, 0))
    # Ebene durch zwei gegenüberliegende Kanten eines Quaders: die Diagonalebene
    box = build(Box(length=40, width=30, height=20)).state.body
    edges = unique_edges(box)
    lower = next(e for e in edges if edge_point(e) == pytest.approx((0, -15, 0)))
    upper = next(e for e in edges if edge_point(e) == pytest.approx((0, 15, 20)))
    frame = sk.edges_frame(lower, upper)
    normal = frame[3:6]
    assert abs(normal[0]) < 1e-9 and abs(normal[1] * 30 + normal[2] * 20) < 1e-9      # senkrecht zur Diagonale
    assert sk.edges_frame(lower, lower) is None


def test_joint_limits():
    from wolfcad.core.solids import JOINT_KINDS, Joint
    base = build(*assembly())
    top = face_at(base, "Körper1", lambda p: abs(p[2] - 10) < 1e-6)
    end = face_at(base, "Körper2", lambda p: abs(p[1]) < 1e-6)
    hinge = Joint(body="Körper2", target="Körper1", face_points=[end, top], joint=JOINT_KINDS[1],
                  angle=30, minimum=-45, maximum=90)
    assert hinge.limits() == (-45, 90) and not build(*assembly(), hinge).errors
    outside = Joint(body="Körper2", target="Körper1", face_points=[end, top], joint=JOINT_KINDS[1],
                    angle=120, minimum=-45, maximum=90)
    assert "außerhalb seiner Grenzen" in next(iter(build(*assembly(), outside).errors.values()))
    assert Joint(joint=JOINT_KINDS[0], minimum=0, maximum=10).limits() is None      # starr: keine Bewegung
    assert Joint(joint=JOINT_KINDS[2]).limits() is None                             # keine Grenzen gesetzt


def test_sub_assemblies():
    from wolfcad.core.solids import Component
    parts = [*assembly(), Box(length=6, width=6, height=6, center_x=80, offset=30, operation="Neuer Körper"),
             Component(name="Schraube", body="Körper2"), Component(name="Mutter", body="Körper3", parent="Schraube")]
    nested = build(*parts)
    assert nested.state.component_parents == {"Mutter": "Schraube"}
    assert nested.state.component_bodies("Schraube") == ["Körper2", "Körper3"]     # samt Unterbaugruppe
    assert nested.state.component_bodies("Mutter") == ["Körper3"]
    assert nested.state.moving_with("Körper3") == ["Körper3"] and nested.state.moving_with("Körper2") == ["Körper2", "Körper3"]
    # Die übergeordnete Komponente verschieben nimmt die Unterbaugruppe mit
    before = measure(nested.state.bodies["Körper3"])[1][2]
    moved = build(*parts, MoveBody(body="Schraube", move_z=7))
    assert measure(moved.state.bodies["Körper3"])[1][2] == pytest.approx(before + 7)
    loop = build(*parts, Component(name="Schraube2", body="Körper2", component="Schraube", parent="Mutter"))
    assert "eigene Unterbaugruppe" in next(iter(loop.errors.values()))


def test_face_colours_and_joint_on_vertices():
    from wolfcad.core.features import face_point, faces_of
    from wolfcad.core.solids import APPEARANCE_SCOPES, Appearance, Joint
    FACES = APPEARANCE_SCOPES[1]
    block = build(Box(length=20, width=20, height=10))
    top = list(next(face_point(f) for f in faces_of(block.state.body) if abs(face_point(f)[2] - 10) < 1e-6))
    side = list(next(face_point(f) for f in faces_of(block.state.body) if abs(face_point(f)[0] - 10) < 1e-6))
    painted = build(Box(length=20, width=20, height=10), Appearance(body="Körper1", color="Rot", scope=FACES, face_points=[top]),
                    Appearance(body="Körper1", color="Grün", scope=FACES, face_points=[side]))
    assert painted.state.colors == {} and sorted(c for _p, c in painted.state.face_colors["Körper1"]) == ["Grün", "Rot"]
    # dieselbe Fläche neu gefärbt ersetzt die alte Farbe; die Körperfarbe überdeckt alles
    again = build(Box(length=20, width=20, height=10), Appearance(body="Körper1", color="Rot", scope=FACES, face_points=[top]),
                  Appearance(body="Körper1", color="Blau", scope=FACES, face_points=[top]))
    assert [c for _p, c in again.state.face_colors["Körper1"]] == ["Blau"]
    whole = build(Box(length=20, width=20, height=10), Appearance(body="Körper1", color="Rot", scope=FACES, face_points=[top]),
                  Appearance(color="Gelb"))
    assert whole.state.colors == {"Körper1": "Gelb"} and "Körper1" not in whole.state.face_colors
    assert build(Box(length=20, width=20, height=10), Appearance(body="Körper1", scope=FACES, face_points=[[99, 99, 99]])).errors
    # Ohne ausdrückliche Wahl gilt die Farbe dem ganzen Körper, auch wenn eine Fläche angeklickt war
    default = build(Box(length=20, width=20, height=10), Appearance(body="Körper1", color="Rot", face_points=[top]))
    assert default.state.colors == {"Körper1": "Rot"} and not default.state.face_colors
    assert build(Box(length=20, width=20, height=10), Appearance(body="Körper1", scope=FACES)).errors
    # Gelenk Ecke auf Ecke: nur verschieben, die Ausrichtung bleibt
    pair = [Box(length=20, width=20, height=10), Box(length=6, width=6, height=6, center_x=50, operation="Neuer Körper")]
    joined = build(*pair, Joint(body="Körper2", target="Körper1", face_points=[[47, -3, 0], [10, 10, 10]],
                                kinds=["vertex", "vertex"]))
    assert not joined.errors
    assert measure(joined.state.bodies["Körper2"])[1] == pytest.approx((13, 13, 13), abs=1e-6)


def test_face_colour_follows_a_moved_body():
    from wolfcad.core.features import face_point, faces_of
    from wolfcad.core.solids import APPEARANCE_SCOPES, Appearance
    block = build(Box(length=20, width=20, height=10))
    top = list(next(face_point(f) for f in faces_of(block.state.body) if abs(face_point(f)[2] - 10) < 1e-6))
    moved = build(Box(length=20, width=20, height=10),
                  Appearance(body="Körper1", color="Rot", scope=APPEARANCE_SCOPES[1], face_points=[top]),
                  MoveBody(move_x=30, move_z=5, axis="Z", angle=90))
    (point, colour), = moved.state.face_colors["Körper1"]
    faces = faces_of(moved.state.body)
    assert colour == "Rot" and min(math.dist(point, face_point(f)) for f in faces) < 1e-6
    assert point == pytest.approx((30, 0, 15), abs=1e-6)


def test_box_and_cylinder_on_a_body_face():
    # Lage einer Körperfläche: Ursprung auf der Deckfläche (z = 10), Normale nach oben, x-Richtung entlang y
    frame = [5.0, 5.0, 10.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    stacked = build(Box(length=40, width=40, height=10),
                    Box(length=10, width=6, height=4, frame=frame, operation="Vereinigen"),
                    Cylinder(diameter=4, height=-3, center_x=10, frame=frame, operation="Ausschneiden"))
    from wolfcad.core.solids import _volume
    assert not stacked.errors and len(stacked.state.bodies) == 1
    assert _volume(stacked.state.body) == pytest.approx(40 * 40 * 10 + 10 * 6 * 4 - math.pi * 4 * 3, rel=1e-6)
    from wolfcad.core.solids import _bounds
    bounds = _bounds(stacked.state.body)
    assert bounds[2] == pytest.approx(0.0, abs=1e-6) and bounds[5] == pytest.approx(14.0, abs=1e-6)


def test_box_follows_its_construction_plane():
    from wolfcad.core.sketch import ConstructionPlane
    from wolfcad.core.solids import _bounds
    timeline = build(ConstructionPlane(name="Ebene1", plane="XY", offset=40.0),
                     Box(length=20, width=20, height=10, base="Ebene1"),
                     Cylinder(diameter=6, height=5, center_x=30, base="Ebene1"))
    assert not timeline.errors
    low = min(_bounds(body)[2] for body in timeline.state.bodies.values())
    assert low == pytest.approx(40.0, abs=1e-6)
    timeline.update(0, offset=70.0)                    # die Ebene wandert, die Körper wandern mit
    assert min(_bounds(body)[2] for body in timeline.state.bodies.values()) == pytest.approx(70.0, abs=1e-6)
    broken = build(Box(base="Fehlt"))
    assert "nicht (mehr) vorhanden" in broken.errors[0]


def test_press_pull_moves_a_planar_face():
    from wolfcad.core.features import face_point, faces_of
    from wolfcad.core.solids import PressPull, _bounds, _volume
    block = build(Box(length=40, width=20, height=10))
    top = list(next(face_point(f) for f in faces_of(block.state.body) if abs(face_point(f)[2] - 10) < 1e-6))
    pulled = build(Box(length=40, width=20, height=10), PressPull(distance=5.0, face_points=[top]))
    assert not pulled.errors and _volume(pulled.state.body) == pytest.approx(40 * 20 * 15)
    assert _bounds(pulled.state.body)[5] == pytest.approx(15.0, abs=1e-6)
    assert len(faces_of(pulled.state.body)) == 6                     # keine Naht: die Seitenflächen sind verschmolzen
    pushed = build(Box(length=40, width=20, height=10), PressPull(distance=-4.0, face_points=[top]))
    assert _volume(pushed.state.body) == pytest.approx(40 * 20 * 6)
    assert "Fläche angeklickt" in build(Box(), PressPull()).errors[1]
    assert "vollständig" in build(Box(length=40, width=20, height=10), PressPull(distance=-10.0, face_points=[top])).errors[1]


def test_extrude_takes_profiles_of_several_sketches():
    from wolfcad.core.features import Extrude
    both = build(RectangleSketch(name="A", width=20, height=10),
                 CircleSketch(name="B", radius=5, center_x=40),
                 Extrude(sketch="A", distance=8, extra_profiles=[["B", []]]))
    assert not both.errors and len(both.state.bodies) == 2
    total = sum(measure(body)[0] for body in both.state.bodies.values())
    assert total == pytest.approx((20 * 10 + math.pi * 25) * 8, rel=1e-6)
    missing = build(RectangleSketch(name="A"), Extrude(sketch="A", extra_profiles=[["Fehlt", []]]))
    assert "Fehlt" in missing.errors[1]


def test_sweep_moves_the_profile_to_the_path_start():
    from wolfcad.core import sketch as sk
    from wolfcad.core.solids import SWEEP_PLACEMENTS, Sweep, _bounds
    # Kreisprofil liegt abseits (bei x = 50) und flach in XY; der Pfad läuft in XZ von (0, 0) 40 mm nach oben
    parts = (CircleSketch(name="Profil", radius=3, center_x=50),
             sk.Sketch(name="Pfad", plane="XZ", entities=[sk.line(10, 0, 10, 40)]))
    placed = build(*parts, Sweep(sketch="Profil", path="Pfad", placement=SWEEP_PLACEMENTS[1]))
    assert not placed.errors
    assert measure(placed.state.body)[0] == pytest.approx(math.pi * 9 * 40, rel=1e-3)
    bounds = _bounds(placed.state.body)
    assert (bounds[0], bounds[3]) == pytest.approx((7.0, 13.0), abs=1e-3)          # mittig um den Pfad bei x = 10
    # ein quer liegender Pfad: das Profil wird gedreht
    across = build(CircleSketch(name="Profil", radius=3), sk.Sketch(name="Pfad", entities=[sk.line(0, 0, 30, 0)]),
                   Sweep(sketch="Profil", path="Pfad", placement=SWEEP_PLACEMENTS[1]))
    assert not across.errors and measure(across.state.body)[0] == pytest.approx(math.pi * 9 * 30, rel=1e-3)


def test_press_pull_offsets_a_curved_face():
    from wolfcad.core.features import face_point, faces_of
    from wolfcad.core.sketch import face_frame
    from wolfcad.core.solids import Hole, PressPull, face_middle
    plate = build(Box(length=40, width=20, height=10), Hole(diameter=6, offset=10))
    wall = next(face for face in faces_of(plate.state.body) if face_frame(face) is None)
    point = list(face_point(wall))
    # die Normale der Bohrungswand zeigt aus dem Material heraus, also zur Bohrungsachse
    middle, normal = face_middle(wall)
    assert math.hypot(middle[0], middle[1]) == pytest.approx(3.0, abs=1e-6)
    assert normal[0] * middle[0] + normal[1] * middle[1] < 0
    wider = build(Box(length=40, width=20, height=10), Hole(diameter=6, offset=10),
                  PressPull(distance=-1.0, face_points=[point]))
    assert not wider.errors
    assert measure(wider.state.body)[0] == pytest.approx(40 * 20 * 10 - math.pi * 16 * 10, rel=1e-4)     # ⌀ 6 → ⌀ 8
    narrower = build(Box(length=40, width=20, height=10), Hole(diameter=6, offset=10),
                     PressPull(distance=1.0, face_points=[point]))
    assert measure(narrower.state.body)[0] == pytest.approx(40 * 20 * 10 - math.pi * 4 * 10, rel=1e-4)   # ⌀ 6 → ⌀ 4


def test_thread_runs_out_when_it_ends_inside_the_face():
    from OCP.BRepCheck import BRepCheck_Analyzer
    from wolfcad.core.solids import Thread
    shaft = build(Cylinder(diameter=10, height=30))
    whole = build(Cylinder(diameter=10, height=30), Thread())
    part = build(Cylinder(diameter=10, height=30), Thread(length=12.0))
    assert not part.errors and BRepCheck_Analyzer(part.state.body).IsValid()
    plain, full, partial = (measure(t.state.body)[0] for t in (shaft, whole, part))
    # das begrenzte Gewinde nimmt weniger weg als das durchgehende, aber deutlich etwas
    assert full < partial < plain and plain - partial > 0.2 * (plain - full)


def test_sphere_follows_its_construction_plane():
    from wolfcad.core.sketch import ConstructionPlane
    from wolfcad.core.solids import _bounds
    timeline = build(ConstructionPlane(name="Ebene1", plane="XY", offset=40.0),
                     Sphere(diameter=10, center_x=5, base="Ebene1"))
    assert not timeline.errors
    bounds = _bounds(timeline.state.body)
    assert ((bounds[2] + bounds[5]) / 2, (bounds[0] + bounds[3]) / 2) == pytest.approx((40.0, 5.0), abs=1e-5)
    timeline.update(0, offset=15.0)
    bounds = _bounds(timeline.state.body)
    assert (bounds[2] + bounds[5]) / 2 == pytest.approx(15.0, abs=1e-5)


def test_construction_axis_drives_revolve_and_circular_pattern():
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import faces_of
    from wolfcad.core.solids import CircularPattern, ConstructionAxis, Hole, _bounds, axis_of_face
    # Achse senkrecht durch (30, 0): ein Quader um sie herum viermal verteilt
    pattern = build(Box(length=10, width=10, height=10, center_x=45), ConstructionAxis(name="Achse1", start=[30, 0, 0], end=[30, 0, 1]),
                    CircularPattern(count=4, angle=360, axis="Achse1"))
    assert not pattern.errors and len(pattern.state.bodies) == 4
    xs = sorted(round((_bounds(body)[0] + _bounds(body)[3]) / 2, 4) for body in pattern.state.bodies.values())
    assert xs == pytest.approx([15.0, 30.0, 30.0, 45.0], abs=1e-4)
    # Drehen einer Skizze um die Achse
    turned = build(sk.Sketch(name="P", entities=[sk.line(40, 0, 50, 0), sk.line(50, 0, 50, 10), sk.line(50, 10, 40, 10),
                                                  sk.line(40, 10, 40, 0)], plane="XZ"),
                   ConstructionAxis(name="Achse1", start=[30, 0, 0], end=[30, 0, 1]),
                   Revolve(sketch="P", axis="Achse1"))
    assert not turned.errors
    assert measure(turned.state.body)[0] == pytest.approx(math.pi * (20 ** 2 - 10 ** 2) * 10, rel=1e-4)
    # Achse einer Bohrung
    plate = build(Box(length=40, width=20, height=10), Hole(diameter=6, offset=10))
    wall = next(face for face in faces_of(plate.state.body) if axis_of_face(face) is not None)
    start, end = axis_of_face(wall)
    assert (start[0], start[1]) == pytest.approx((0.0, 0.0), abs=1e-9) and abs(end[2] - start[2]) == pytest.approx(10.0)
    assert "zusammen" in build(ConstructionAxis(start=[1, 1, 1], end=[1, 1, 1])).errors[0]


def test_midplane_and_tangent_plane():
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import face_point, faces_of
    block = build(Box(length=40, width=20, height=10))
    faces = faces_of(block.state.body)
    left = next(f for f in faces if abs(face_point(f)[0] + 20) < 1e-6)
    right = next(f for f in faces if abs(face_point(f)[0] - 20) < 1e-6)
    frame = sk.midplane_frame(sk.face_frame(left), sk.face_frame(right))
    assert frame[0] == pytest.approx(0.0, abs=1e-9) and abs(frame[3]) == pytest.approx(1.0)
    shaft = build(Cylinder(diameter=20, height=30))
    mantle = next(f for f in faces_of(shaft.state.body) if sk.face_frame(f) is None)
    tangent = sk.tangent_frame(mantle)
    assert math.hypot(tangent[0], tangent[1]) == pytest.approx(10.0, abs=1e-6) and tangent[5] == pytest.approx(0.0, abs=1e-9)


def test_chamfer_kinds_and_variable_fillet():
    from wolfcad.core.features import CHAMFER_KINDS, Chamfer, Fillet, face_point, faces_of
    from wolfcad.core.solids import _volume
    top_edge = [[0.0, 10.0, 10.0]]          # obere Längskante hinten des Quaders 40 × 20 × 10
    plain = 40 * 20 * 10
    even = build(Box(length=40, width=20, height=10), Chamfer(distance=2, edge_points=top_edge))
    assert _volume(even.state.body) == pytest.approx(plain - 0.5 * 2 * 2 * 40, rel=1e-6)
    uneven = build(Box(length=40, width=20, height=10),
                   Chamfer(distance=2, distance2=4, chamfer_type=CHAMFER_KINDS[1], edge_points=top_edge))
    assert not uneven.errors and _volume(uneven.state.body) == pytest.approx(plain - 0.5 * 2 * 4 * 40, rel=1e-6)
    angled = build(Box(length=40, width=20, height=10),
                   Chamfer(distance=3, angle=45, chamfer_type=CHAMFER_KINDS[2], edge_points=top_edge))
    assert not angled.errors and _volume(angled.state.body) == pytest.approx(plain - 0.5 * 3 * 3 * 40, rel=1e-4)
    # variabler Radius: zwischen dem Volumen mit 1 mm und dem mit 4 mm überall
    lean = build(Box(length=40, width=20, height=10), Fillet(radius=1, edge_points=top_edge))
    full = build(Box(length=40, width=20, height=10), Fillet(radius=4, edge_points=top_edge))
    varied = build(Box(length=40, width=20, height=10), Fillet(radius=1, radius2=4, edge_points=top_edge))
    assert not varied.errors
    assert _volume(full.state.body) < _volume(varied.state.body) < _volume(lean.state.body)
    # „Richtung umkehren“: gleiches Volumen, aber die Abstände liegen auf den jeweils anderen Flächen
    flipped = build(Box(length=40, width=20, height=10),
                    Chamfer(distance=2, distance2=4, chamfer_type=CHAMFER_KINDS[1], flip=True,
                            edge_points=top_edge))
    assert not flipped.errors and _volume(flipped.state.body) == pytest.approx(_volume(uneven.state.body), rel=1e-6)
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    def deckflaeche(body):
        """Fläche der Oberseite (höchste ebene Fläche): 40 × (20 − Fasenbreite oben)."""
        def flaeche(face):
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            return props.Mass()
        oben = max(faces_of(body), key=lambda f: (round(face_point(f)[2], 6), flaeche(f)))
        return flaeche(oben)
    # ohne und mit Umkehren landen die 2 bzw. 4 mm auf verschiedenen Flächen
    assert {round(deckflaeche(uneven.state.body)), round(deckflaeche(flipped.state.body))} == {40 * 18, 40 * 16}
    # die Art der Fase und die Richtung überstehen Speichern und Laden
    from wolfcad.core.timeline import Timeline
    again = Timeline.from_document(uneven.to_document())
    assert again.features[1].chamfer_type == CHAMFER_KINDS[1] and not again.errors
    assert Timeline.from_document(flipped.to_document()).features[1].flip is True


def test_tapped_hole():
    from wolfcad.core.solids import Hole, TAP_OPTIONS, _volume
    plain = build(Box(length=30, width=30, height=12), Hole(diameter=8, offset=12))
    tapped = build(Box(length=30, width=30, height=12), Hole(diameter=8, offset=12, tapped=TAP_OPTIONS[1]))
    assert not tapped.errors
    # das Gewinde trägt Material aus der Wand ab
    assert _volume(tapped.state.body) < _volume(plain.state.body) - 5
    # eine Normgröße bestimmt den Durchmesser: M10 bohrt ⌀ 10, auch wenn 8 eingetragen ist
    m10 = build(Box(length=30, width=30, height=12), Hole(diameter=8, offset=12, tapped="M10 × 1,5"))
    assert not m10.errors and _volume(m10.state.body) < _volume(tapped.state.body)
    assert "Gewindeart" in build(Box(), Hole(tapped="M99")).errors[1]


def test_path_pattern_and_delete_face():
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import Fillet, face_point, faces_of
    from wolfcad.core.solids import PATH_ORIENTATIONS, DeleteFace, Hole, PathPattern, _bounds, _volume
    # Pfad: gerade Linie 60 mm nach rechts, 4 Kopien → Abstand 20 mm
    straight = build(Box(length=4, width=4, height=4), sk.Sketch(name="P", entities=[sk.line(0, 0, 60, 0)]),
                     PathPattern(path="P", count=4))
    assert not straight.errors and len(straight.state.bodies) == 4
    xs = sorted(round((_bounds(b)[0] + _bounds(b)[3]) / 2, 4) for b in straight.state.bodies.values())
    assert xs == pytest.approx([0, 20, 40, 60], abs=1e-4)
    # Pfad: Viertelkreis, ausgerichtet – die letzte Kopie ist um 90° gedreht (lange Seite zeigt nach y)
    turned = build(Box(length=10, width=2, height=2, center_x=20), sk.Sketch(name="P", entities=[sk.arc(0, 0, 20, 270, 0)]),
                   PathPattern(path="P", count=2, orientation=PATH_ORIENTATIONS[1]))
    assert not turned.errors
    sizes = sorted((round(b[3] - b[0], 3), round(b[4] - b[1], 3)) for b in map(_bounds, turned.state.bodies.values()))
    assert (2.0, 10.0) in [(round(a, 1), round(c, 1)) for a, c in sizes]
    # Fläche löschen: Bohrung entfernen bringt das volle Volumen zurück
    plate = build(Box(length=40, width=20, height=10), Hole(diameter=6, offset=10))
    wall = next(f for f in faces_of(plate.state.body) if abs(face_point(f)[0]) < 1e-6 and abs(face_point(f)[1]) < 1e-6
                and 0 < face_point(f)[2] < 10)
    filled = build(Box(length=40, width=20, height=10), Hole(diameter=6, offset=10), DeleteFace(face_points=[list(face_point(wall))]))
    assert not filled.errors and _volume(filled.state.body) == pytest.approx(8000, rel=1e-6)
    assert "Flächen anklicken" in build(Box(), DeleteFace()).errors[1]


def test_replace_face_with_a_plane():
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import face_point, faces_of
    from wolfcad.core.solids import ReplaceFace, _bounds, _volume
    base = build(Box(length=40, width=20, height=10))
    top = [list(next(face_point(f) for f in faces_of(base.state.body) if abs(face_point(f)[2] - 10) < 1e-6))]
    # Oberseite durch eine Ebene in 25 mm Höhe ersetzen: der Quader wächst
    higher = build(Box(length=40, width=20, height=10), sk.ConstructionPlane(name="E", plane="XY", offset=25.0),
                   ReplaceFace(face_points=top, target="E"))
    assert not higher.errors and _bounds(higher.state.body)[5] == pytest.approx(25.0, abs=1e-6)
    assert _volume(higher.state.body) == pytest.approx(40 * 20 * 25, rel=1e-6)
    # … durch eine schräge Ebene (10° um die y-Achse gekippt, in 6 mm Höhe): oben schräg abgeschnitten
    slanted = build(Box(length=40, width=20, height=10),
                    sk.ConstructionPlane(name="E", plane="XY", offset=6.0, tilt=10.0, tilt_axis=sk.TILT_AXES[1]),
                    ReplaceFace(face_points=top, target="E"))
    # die gekippte Ebene liegt 6 mm längs ihrer Normale vom Ursprung: in der Mitte also 6 / cos 10° hoch
    assert not slanted.errors and _volume(slanted.state.body) == pytest.approx(40 * 20 * 6 / math.cos(math.radians(10)), rel=1e-4)
    assert "Konstruktionsebene" in build(Box(), ReplaceFace(face_points=top)).errors[1]


def test_pattern_of_features_repeats_holes_and_bosses():
    """Muster von Features (wie in Fusion): nur die Wirkung des Features wird wiederholt, nicht der ganze Körper."""
    from wolfcad.core.solids import (PATTERN_OBJECTS, CircularPattern, Cylinder, Hole, PathPattern,
                                     RectangularPattern)

    block = Box(length=60, width=20, height=10)
    hole = Hole(name="B", diameter=4, offset=10, position_x=-20)
    pattern = RectangularPattern(object_type=PATTERN_OBJECTS[1], source_feature="B", axis="X", count=3, spacing=15)
    drilled = build(block, hole, pattern)
    assert not drilled.errors, drilled.errors
    assert len(drilled.state.bodies) == 1             # kein neuer Körper: nur weitere Bohrungen
    volume, _ = measure(drilled.state.body)
    assert volume == pytest.approx(12000 - 3 * math.pi * 4 * 10, rel=1e-6)

    # angefügtes Material: ein Zapfen, im Kreis wiederholt
    boss = Cylinder(name="Z", diameter=4, height=5, center_x=20, offset=10, operation="Vereinigen")
    ring = CircularPattern(object_type=PATTERN_OBJECTS[1], source_feature="Z", axis="Z", count=2)
    bossed = build(Box(length=60, width=60, height=10), boss, ring)
    assert not bossed.errors, bossed.errors
    volume, _ = measure(bossed.state.body)
    assert volume == pytest.approx(36000 + 2 * math.pi * 4 * 5, rel=1e-6)

    # ohne gültiges Quell-Feature: klare Meldung
    missing = build(block, RectangularPattern(object_type=PATTERN_OBJECTS[1], source_feature="Nix"))
    assert missing.errors
    # Körper-Muster funktionieren wie bisher
    copies = build(block, RectangularPattern(axis="Z", count=2, spacing=20))
    assert len(copies.state.bodies) == 2
    assert PathPattern().object_type == PATTERN_OBJECTS[0]


def test_move_face_moves_a_hole_and_lifts_a_top_face():
    """Fläche verschieben: die Wand einer Bohrung versetzt die ganze Bohrung; eine Oberseite wandert längs der Normale."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt
    from wolfcad.core import features as ft
    from wolfcad.core.solids import Hole, MoveFace

    def inside(shape, x, y, z):
        return BRepClass3d_SolidClassifier(shape, gp_Pnt(x, y, z), 1e-6).State() == TopAbs_IN

    block = Box(length=60, width=20, height=10)
    hole = Hole(diameter=6, offset=10, position_x=-15)
    drilled = build(block, hole)
    # Merkpunkt der Bohrungswand: der Schwerpunkt der Zylinderfläche liegt auf der Bohrungsachse
    wall = [list(ft.face_point(face)) for face in ft.faces_of(drilled.state.body)
            if math.hypot(ft.face_point(face)[0] + 15, ft.face_point(face)[1]) < 1e-6]
    assert len(wall) == 1
    moved = build(block, hole, MoveFace(face_points=wall, move_x=25.0, move_y=0.0, move_z=0.0))
    assert not moved.errors, moved.errors
    body = moved.state.body
    assert inside(body, -15, 0, 5)                          # alte Stelle wieder gefüllt
    assert not inside(body, 10, 0, 5)                       # Bohrung jetzt bei x = 10
    volume, _ = measure(body)
    assert volume == pytest.approx(12000 - math.pi * 9 * 10, rel=1e-6)

    # Oberseite um 4 mm anheben (ein seitlicher Anteil ändert an einer ebenen Außenfläche nichts)
    plain = build(block)
    top = [list(ft.face_point(face)) for face in ft.faces_of(plain.state.body) if abs(ft.face_point(face)[2] - 10) < 1e-6]
    lifted = build(block, MoveFace(face_points=top, move_x=3.0, move_y=0.0, move_z=4.0))
    assert not lifted.errors, lifted.errors
    volume, _ = measure(lifted.state.body)
    assert volume == pytest.approx(60 * 20 * 14)
    # nur seitlich: klare Meldung statt stiller Wirkungslosigkeit
    sideways = build(block, MoveFace(face_points=top, move_x=3.0, move_y=0.0, move_z=0.0))
    assert sideways.errors


def test_sweep_with_guide_rail_scales_or_only_aligns():
    """Sweep mit Führungsschiene (Fusion: „Pfad + Führungsschiene“): Skalieren ergibt hier einen Kegelstumpf."""
    from wolfcad.core import sketch as sk
    from wolfcad.core.solids import GUIDE_MODES, Sweep

    def swept(mode, guide="Schiene"):
        return build(sk.Sketch(name="Profil", plane="XY", entities=[sk.circle(0, 0, 5)]),
                     sk.Sketch(name="Pfad", plane="XZ", entities=[sk.line(0, 0, 0, 50)]),
                     sk.Sketch(name="Schiene", plane="XZ", entities=[sk.line(5, 0, 10, 50)]),
                     Sweep(name="S", sketch="Profil", path="Pfad", guide=guide, guide_mode=mode))

    scaled = swept(GUIDE_MODES[0])
    assert not scaled.errors, scaled.errors
    assert measure(scaled.state.body)[0] == pytest.approx(math.pi * 50 / 3 * (25 + 50 + 100), rel=1e-4)
    aligned = swept(GUIDE_MODES[1])
    assert measure(aligned.state.body)[0] == pytest.approx(math.pi * 25 * 50, rel=1e-4)
    assert swept(GUIDE_MODES[0], guide="Pfad").errors          # Schiene muss eine eigene Skizze sein


def test_replace_face_with_a_curved_face_of_another_body():
    """Fläche ersetzen durch eine Zylinderfläche: die Oberseite wird gewölbt – nach oben wachsend oder abschneidend."""
    import numpy as np
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from wolfcad.core import features as ft
    from wolfcad.core.solids import TARGET_FACE, ReplaceFace

    for centre in (-5.0, -15.0):            # Scheitel bei z = 15 (wächst) bzw. z = 5 (schneidet ab)
        timeline = build(Box(length=40, width=20, height=10),
                         Cylinder(plane="YZ", offset=-30, center_y=centre, diameter=40, height=60))
        block, roller = list(timeline.state.bodies)
        top = [list(ft.face_point(f)) for f in ft.faces_of(timeline.state.bodies[block])
               if abs(ft.face_point(f)[2] - 10) < 1e-6]
        wall = [list(ft.face_point(f)) for f in ft.faces_of(timeline.state.bodies[roller])
                if BRepAdaptor_Surface(f).GetType() == GeomAbs_Cylinder]
        timeline.add(ReplaceFace(body=block, face_points=top, target=TARGET_FACE, target_body=roller, target_points=wall))
        assert not timeline.errors, timeline.errors
        y = np.linspace(-10, 10, 200001)
        expected = 40 * np.trapezoid(centre + np.sqrt(400 - y * y), y)
        volume, _ = measure(timeline.state.bodies[block])
        assert volume == pytest.approx(expected, rel=1e-6)
    # ohne Ersatzfläche: klare Meldung
    missing = build(Box(length=40, width=20, height=10), ReplaceFace(face_points=[[0, 0, 10]], target=TARGET_FACE))
    assert missing.errors


def test_feature_pattern_adjusts_to_the_geometry_at_each_instance():
    """„Angepasst“ (Fusion): die Durchgangsbohrung wird an der Musterstelle neu berechnet und geht auch durch den
    dickeren Teil; „Identisch“ kopiert nur ihre Wirkung (10 mm Tiefe)."""
    from wolfcad.core.solids import PATTERN_COMPUTE, PATTERN_OBJECTS, Hole, RectangularPattern

    def drilled(compute):
        return build(Box(length=60, width=20, height=10),
                     Box(length=20, width=20, height=20, center_x=20, operation="Vereinigen"),
                     Hole(name="B", diameter=4, offset=30, position_x=-20),
                     RectangularPattern(object_type=PATTERN_OBJECTS[1], source_feature="B", compute=compute,
                                        axis="X", count=2, spacing=40))
    solid = 60 * 20 * 10 + 20 * 20 * 10
    adjusted = drilled(PATTERN_COMPUTE[0])
    assert not adjusted.errors, adjusted.errors
    assert measure(adjusted.state.body)[0] == pytest.approx(solid - math.pi * 4 * (10 + 20), rel=1e-6)
    same = drilled(PATTERN_COMPUTE[1])
    assert not same.errors, same.errors
    assert measure(same.state.body)[0] == pytest.approx(solid - math.pi * 4 * (10 + 10), rel=1e-6)


def test_move_face_rotates_a_top_face_into_a_slope():
    """Fläche verschieben mit Drehung: die Oberseite kippt um die Y-Achse durch ihre Mitte (Volumen bleibt bei
    symmetrischem Kippen gleich, die hohe Kante steigt um 20 · tan 10°); dazu verschoben wird sie zusätzlich angehoben."""
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box
    from wolfcad.core import features as ft
    from wolfcad.core.solids import MoveFace

    plain = build(Box(length=40, width=20, height=10))
    top = [list(ft.face_point(f)) for f in ft.faces_of(plain.state.body) if abs(ft.face_point(f)[2] - 10) < 1e-6]
    tilted = build(Box(length=40, width=20, height=10),
                   MoveFace(face_points=top, move_x=0.0, move_y=0.0, move_z=0.0, rotate_axis="Y", rotate_angle=10.0))
    assert not tilted.errors, tilted.errors
    assert measure(tilted.state.body)[0] == pytest.approx(8000, rel=1e-6)
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(tilted.state.body, box, False, False)
    assert box.CornerMax().Z() == pytest.approx(10 + 20 * math.tan(math.radians(10)), abs=1e-6)
    lifted = build(Box(length=40, width=20, height=10),
                   MoveFace(face_points=top, move_x=0.0, move_y=0.0, move_z=3.0, rotate_axis="Y", rotate_angle=10.0))
    assert measure(lifted.state.body)[0] == pytest.approx(8000 + 40 * 20 * 3, rel=1e-6)
    # zu weit gedreht: Meldung statt kaputtem Körper
    assert build(Box(length=40, width=20, height=10),
                 MoveFace(face_points=top, move_z=0.0, rotate_axis="Y", rotate_angle=120.0)).errors
