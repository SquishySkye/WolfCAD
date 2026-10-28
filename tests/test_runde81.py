# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Runde 81: Verschieben mit Drehpunkt und Skalieren; Leinwand auf einer Körperfläche."""
import base64

import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from wolfcad.core import features as ft
from wolfcad.core import more_tools as mt
from wolfcad.core import sketch, solids
from wolfcad.core.timeline import Timeline

# kleinstes gültiges PNG (1 × 1 Bildpunkt) – die Leinwand prüft nur, dass ein Bild da ist
_PIXEL = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000000020001e221bc33"
    "0000000049454e44ae426082")).decode()


def _mass(shape):
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    centre = props.CentreOfMass()
    return props.Mass(), (centre.X(), centre.Y(), centre.Z())


def _box_at_30() -> Timeline:
    timeline = Timeline()
    timeline.add(solids.Box(center_x=30, length=10, width=10, height=10, operation="Neuer Körper"))
    return timeline


def test_scale_and_turn_about_the_body_centre():
    timeline = _box_at_30()
    timeline.add(solids.MoveBody(pivot="Körpermitte", scale=2.0, angle=45.0, move_z=5.0))
    volume, centre = _mass(next(iter(timeline.state.bodies.values())))
    assert not timeline.errors
    assert volume == pytest.approx(8000.0)                                   # 2³ × 1000
    assert centre == pytest.approx((30.0, 0.0, 10.0), abs=1e-6)              # bleibt an Ort und Stelle (+5 in z)


def test_old_moves_still_turn_about_the_origin():
    timeline = _box_at_30()
    timeline.add(solids.MoveBody(angle=90.0))            # gespeicherte Schritte ohne Drehpunkt: Ursprung
    _volume, centre = _mass(next(iter(timeline.state.bodies.values())))
    assert centre == pytest.approx((0.0, 30.0, 5.0), abs=1e-6)


def test_scale_must_be_positive():
    timeline = _box_at_30()
    timeline.add(solids.MoveBody(pivot="Körpermitte", scale=0.0))
    assert "größer als 0" in timeline.errors[1]


def test_canvas_lies_on_a_body_face():
    timeline = Timeline()
    timeline.add(solids.Box(length=60, width=40, height=20, operation="Neuer Körper"))
    shape = next(iter(timeline.state.bodies.values()))
    top = max(ft.faces_of(shape), key=lambda face: ft.face_point(face)[2])
    timeline.add(mt.Canvas(image=_PIXEL, aspect=0.5, width=40.0, angle=90.0, frame=sketch.face_frame(top)))
    corners = next(iter(timeline.state.canvases.values()))["corners"]
    assert not timeline.errors
    assert all(corner[2] == pytest.approx(20.0) for corner in corners)       # auf der Oberseite
    xs = [corner[0] for corner in corners]
    assert max(xs) - min(xs) == pytest.approx(20.0)                          # um 90° gedreht: 20 mm hoch wird x
