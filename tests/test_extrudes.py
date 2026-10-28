# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für Profil-Rezepte und den Profilaufbau der Fusion-Extrusionen."""

import struct

import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from wolfcad.f3d import extrudes, sketches

IDENTITY = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0)


def _volume(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return abs(properties.Mass())


def _recipe(*loops: list[int]) -> bytes:
    """Rezept wie in der Datei: 25 Byte Kopf, je Randzug Anzahl und 40-Byte-Einträge."""
    data = bytes(25)
    for loop in loops:
        data += struct.pack("<I", len(loop))
        for tag in loop:
            data += struct.pack("<IQ", 3, tag) + bytes(28)
    return data + b"\xff" * 16


def _rectangle_with_extra_line() -> sketches.FusionSketch:
    corners = [(0, 0, 0), (4, 0, 0), (4, 2, 0), (0, 2, 0)]
    lines = [sketches.SketchLine(corners[i], corners[(i + 1) % 4], tag=10 + i) for i in range(4)]
    # Eine Linie, die über das Rechteck hinausragt und es in zwei Hälften teilt
    lines.append(sketches.SketchLine((2, -1, 0), (2, 3, 0), tag=20))
    return sketches.FusionSketch(1, IDENTITY, lines=lines)


def test_recipe_lists_tags_of_all_loops_and_stops_at_the_end():
    data = _recipe([5, 5, 6], [9])
    assert extrudes._recipe_tags(data, 0) == frozenset({5, 6, 9})


def test_profile_from_whole_curves():
    sketch = _rectangle_with_extra_line()
    extrude = extrudes.FusionExtrude(1, 4, (0, 0, 1), 1, [frozenset({10, 11, 12, 13})])
    # 4 cm x 2 cm, 10 mm hoch -> 40 mm x 20 mm x 10 mm
    assert _volume(extrudes.solid(extrude, sketch, 10)) == pytest.approx(8000)


def test_profile_from_partial_curves_uses_the_region_bounded_by_all_tags():
    sketch = _rectangle_with_extra_line()
    # Das Profil nennt die Teilungslinie mit: gemeint ist eine Rechteckhälfte (2 cm x 2 cm)
    extrude = extrudes.FusionExtrude(1, 4, (0, 0, 1), 1, [frozenset({10, 12, 13, 20})])
    assert _volume(extrudes.solid(extrude, sketch, 10)) == pytest.approx(4000)


def test_missing_curves_give_no_solid():
    sketch = _rectangle_with_extra_line()
    extrude = extrudes.FusionExtrude(1, 4, (0, 0, 1), 1, [frozenset({10, 11})])
    assert extrudes.solid(extrude, sketch, 10) is None


def test_primitives_are_sized_and_placed():
    from wolfcad.f3d import primitives

    # Lage: um 2 cm entlang x verschoben, keine Drehung
    matrix = (1, 0, 0, 2, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    box = primitives.solid("box", matrix, {"x": 40, "y": 20, "z": -10, "offset_x": 5, "offset_y": 0})
    assert _volume(box) == pytest.approx(8000)
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(box, properties)
    centre = properties.CentreOfMass()
    # 20 mm Verschiebung + 5 mm Versatz + halbe Länge; negative Höhe wächst nach unten
    assert (centre.X(), centre.Y(), centre.Z()) == pytest.approx((45, 10, -5))

    cylinder = primitives.solid("cylinder", matrix, {"diameter": 10, "height": -20})
    assert _volume(cylinder) == pytest.approx(3.14159265 * 25 * 20, rel=1e-6)
    assert _volume(primitives.solid("sphere", matrix, {"diameter": 6})) == pytest.approx(113.097, rel=1e-4)
    assert primitives.solid("box", matrix, {"x": 0, "y": 20, "z": 10}) is None


def test_extrude_extents_and_taper():
    import math
    import pytest
    from wolfcad.core.features import EXTENTS, Extrude, RectangleSketch
    from wolfcad.core.solids import _bounds, _volume
    from wolfcad.core.timeline import Timeline

    def build(**options):
        timeline = Timeline()
        timeline.add(RectangleSketch(name="S", width=20, height=10))
        timeline.add(Extrude(sketch="S", **options))
        assert not timeline.errors, timeline.errors
        return timeline.state.body

    symmetric = build(distance=12, extent=EXTENTS[1])
    assert _bounds(symmetric)[2] == pytest.approx(-6, abs=1e-6) and _bounds(symmetric)[5] == pytest.approx(6, abs=1e-6)
    both = build(distance=8, distance2=3, extent=EXTENTS[2])
    assert (_bounds(both)[2], _bounds(both)[5]) == pytest.approx((-3, 8), abs=1e-6) and _volume(both) == pytest.approx(2200)
    # Verjüngung 10°: Pyramidenstumpf, oben je Seite um h·tan(10°) schmaler
    h, shrink = 10.0, 10.0 * math.tan(math.radians(10))
    tapered = build(distance=h, taper=10.0)
    top = (20 - 2 * shrink) * (10 - 2 * shrink)
    middle = (20 - shrink) * (10 - shrink)
    expected = h / 6 * (200 + 4 * middle + top)            # Prismatoidformel (Seiten werden gleich weit schmaler)
    assert _volume(tapered) == pytest.approx(expected, rel=1e-4)
    wider = build(distance=h, taper=-10.0)
    assert _volume(wider) > 2000


def test_symmetric_taper_with_hole_is_one_clean_body():
    import pytest
    from wolfcad.core import sketch as sk
    from wolfcad.core.features import EXTENTS, Extrude
    from wolfcad.core.solids import _volume
    from wolfcad.core.timeline import Timeline
    plate = [sk.line(-20, -10, 20, -10), sk.line(20, -10, 20, 10), sk.line(20, 10, -20, 10), sk.line(-20, 10, -20, -10),
             sk.circle(0, 0, 4)]

    def volume(**options):
        timeline = Timeline()
        timeline.add(sk.Sketch(name="S", entities=plate))
        timeline.add(Extrude(sketch="S", **options))
        assert not timeline.errors
        return _volume(timeline.state.body)

    # symmetrisch 8 mm mit Verjüngung = zweimal die einseitige Extrusion über 4 mm
    assert volume(distance=8, taper=5, extent=EXTENTS[1]) == pytest.approx(2 * volume(distance=4, taper=5), rel=1e-6)


def test_extrude_start_offset_and_up_to_face():
    import pytest
    from wolfcad.core.features import EXTENTS, Extrude, RectangleSketch, face_point, faces_of
    from wolfcad.core.solids import Box, _bounds
    from wolfcad.core.timeline import Timeline
    # Startversatz 5 mm, Länge 10 mm: der Körper liegt zwischen z = 5 und 15
    timeline = Timeline()
    timeline.add(RectangleSketch(name="S", width=20, height=10))
    timeline.add(Extrude(sketch="S", distance=10, start=5))
    bounds = _bounds(timeline.state.body)
    assert (bounds[2], bounds[5]) == pytest.approx((5, 15), abs=1e-6)
    # bis zur Unterseite eines Quaders, der in 30 mm Höhe schwebt (parallel) …
    timeline = Timeline()
    timeline.add(Box(length=60, width=60, height=10, offset=30.0))
    bottom = next(f for f in faces_of(timeline.state.body) if abs(face_point(f)[2] - 30) < 1e-6)
    timeline.add(RectangleSketch(name="S", width=20, height=10))
    timeline.add(Extrude(sketch="S", extent=EXTENTS[3], target_points=[list(face_point(bottom))]))
    assert not timeline.errors
    tops = sorted(_bounds(body)[5] for body in timeline.state.bodies.values())
    assert tops[0] == pytest.approx(30.0, abs=1e-6)
    # … und ohne gewählte Fläche gibt es eine verständliche Meldung
    timeline.update(2, target_points=[])
    assert "Fläche anklicken" in timeline.errors[2]

