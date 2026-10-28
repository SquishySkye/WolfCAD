# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für die Ziehgriffe: gerade Pfeile (mm) und Winkelbögen (Grad)."""

import math

import pytest

from wolfcad.core.features import RectangleSketch
from wolfcad.core.solids import Box, CircularPattern, MoveBody, Revolve
from wolfcad.core.timeline import Timeline
from wolfcad.gui import handles


def handles_of_last(*features) -> list[handles.Handle]:
    """Baut die Features auf und liefert die Griffe des letzten."""
    timeline = Timeline()
    for feature in features:
        timeline.add(feature)
    index = len(timeline.features) - 1
    return handles.handles_for(timeline.features[index], timeline.state_before(index), timeline.state)


def test_angle_handle_follows_the_rotation():
    # Achse Z durch den Ursprung, das Gedrehte liegt bei x = 30 in Höhe z = 5
    handle = handles.angle_handle((0, 0, 0), (0, 0, 1), (30, 0, 5), "angle", 90.0, minimum=1.0)
    assert handle.kind == handles.ANGLE
    assert handle.base == pytest.approx((0, 0, 5))          # Fußpunkt auf der Achse, auf Höhe des Körpers
    assert handle.reference == pytest.approx((1, 0, 0))
    assert handle.radius == pytest.approx(30)
    assert handle.tip() == pytest.approx((0, 30, 5))        # 90° gegen den Uhrzeigersinn um +Z


def test_angle_handle_on_the_axis_gets_a_usable_direction():
    handle = handles.angle_handle((0, 0, 0), (0, 0, 1), (0, 0, 8), "angle", 0.0, minimum=-360.0)
    assert handle.radius == 0
    assert math.sqrt(sum(c * c for c in handle.reference)) == pytest.approx(1)
    assert handle.reference[2] == pytest.approx(0)          # senkrecht zur Achse
    # schräg zwischen X und Y, damit der Bogen nicht auf einem Achsenpfeil beginnt
    assert abs(handle.reference[0]) == pytest.approx(abs(handle.reference[1]))


def test_circular_pattern_has_an_angle_handle():
    found = handles_of_last(Box(length=10, width=10, height=10, center_x=30), CircularPattern(angle=180))
    # der Winkelbogen und der Griff für die Anzahl (je 10 mm eine Kopie; Vorgabe sechs Kopien)
    assert [(h.kind, h.parameter) for h in found] == [(handles.ANGLE, "angle"), (handles.LINEAR, "count")]
    assert found[0].value == 180 and found[1].value * found[1].factor == pytest.approx(round(CircularPattern().count))
    assert found[0].radius == pytest.approx(30)
    assert (found[0].minimum, found[0].maximum) == (1.0, 360.0)


def test_move_has_three_arrows_and_a_rotation_handle():
    found = handles_of_last(Box(length=10, width=10, height=10, center_x=30), MoveBody(angle=30, move_x=5.0, pivot="Körpermitte"))
    # Runde 82: Verschiebe-Kreuz wie Fusion – drei Pfeile, ein Drehgriff je Achse (Z ist das alte `angle`, weil
    # `axis` „Z“ ist), dazu der Skalier-Pfeil aus Runde 81
    assert [h.parameter for h in found] == ["move_x", "move_y", "move_z", "angle_x", "angle_y", "angle", "scale"]
    assert [h.kind for h in found] == [handles.LINEAR] * 3 + [handles.ANGLE] * 3 + [handles.LINEAR]
    assert all(h.gizmo for h in found)
    assert [h.color for h in found[:3]] == [handles.RED, handles.GREEN, handles.BLUE]
    assert found[6].value * found[6].factor == pytest.approx(1.0)     # Faktor 1 = unverändert
    assert found[5].value == 30 and found[3].minimum == -360.0          # Drehen geht in beide Richtungen
    # das Kreuz wandert mit: alle Pfeilspitzen an der verschobenen Mitte (30 + 5, 0, 5)
    for arrow in found[:3]:
        assert arrow.tip() == pytest.approx((35.0, 0.0, 5.0))
    assert all(ring.base == pytest.approx((35.0, 0.0, 5.0)) for ring in found[3:6])


def test_revolve_handle_starts_at_the_profile():
    found = handles_of_last(RectangleSketch(name="Skizze1", center_x=20, width=10, height=20),
                            Revolve(sketch="Skizze1", angle=120))
    assert len(found) == 1 and found[0].kind == handles.ANGLE
    assert found[0].radius == pytest.approx(20)             # Abstand der Profilmitte von der Achse
    assert found[0].value == 120
