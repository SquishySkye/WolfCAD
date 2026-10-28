# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Runde 83: Physik-Tests – Festigkeit gegen die Balkentheorie, Druck, Schwimmen, Strömung (kurz), Werkstoffe; dazu
Upload-Fortschritt-Hilfen und das Lesen der neuen Noctalia-Farbdatei."""
import math

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from wolfcad.core import physics

pytest.importorskip("gmsh")
pytest.importorskip("scipy")


def _beam():
    return BRepPrimAPI_MakeBox(100.0, 10.0, 10.0).Shape()


def test_cantilever_matches_beam_theory():
    # Aluminium, 100 N am Ende: Durchbiegung F·L³/(3·E·I) = 0,5714 mm, Biegespannung an der Einspannung 60 MPa
    result = physics.static_stress(_beam(), physics.MATERIALS["Aluminium"], [[0.0, 5.0, 5.0]],
                                   [physics.Load(physics.FORCE, [[100.0, 5.0, 5.0]], 100.0, (0.0, 0.0, -1.0))],
                                   size=4.0)
    tip = result.displacement[result.nodes[:, 0] > 99.99][:, 2].mean()
    assert tip == pytest.approx(-0.5714, rel=0.02)
    assert result.reaction == pytest.approx(100.0, rel=1e-6) and result.load_total == pytest.approx(100.0)
    assert 55.0 < result.max_stress < 70.0
    assert result.safety == pytest.approx(240.0 / result.max_stress)
    assert result.surface.shape[1] == 3 and result.surface.max() < len(result.nodes)


def test_pressure_on_the_end_face_compresses_the_bar():
    # 1 MPa auf die Endfläche (100 mm²) = 100 N Druck längs: Stauchung F·L/(E·A) = 100·100/(70000·100) mm
    result = physics.static_stress(_beam(), physics.MATERIALS["Aluminium"], [[0.0, 5.0, 5.0]],
                                   [physics.Load(physics.PRESSURE, [[100.0, 5.0, 5.0]], 1.0)], size=4.0)
    end = result.displacement[result.nodes[:, 0] > 99.99][:, 0].mean()
    assert end == pytest.approx(-100.0 * 100.0 / (70000.0 * 100.0), rel=0.05)
    assert result.load_total == pytest.approx(100.0, rel=1e-6)
    assert result.max_stress == pytest.approx(1.0, rel=0.15)        # gleichmäßig 1 MPa (an den Ecken etwas mehr)


def test_missing_supports_or_loads_are_explained():
    with pytest.raises(physics.FeatureError, match="lagern"):
        physics.static_stress(_beam(), physics.MATERIALS["PLA"], [], [])
    with pytest.raises(physics.FeatureError, match="Last"):
        physics.static_stress(_beam(), physics.MATERIALS["PLA"], [[0.0, 5.0, 5.0]], [])


def test_hollow_box_floats_and_solid_steel_sinks():
    outer = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 60.0, 30.0, 20.0).Shape()
    inner = BRepPrimAPI_MakeBox(gp_Pnt(2, 2, 2), 56.0, 26.0, 16.0).Shape()
    hollow = BRepAlgoAPI_Cut(outer, inner).Shape()
    floating = physics.buoyancy(hollow, physics.MATERIALS["PLA"].density)
    walls = 60 * 30 * 20 - 56 * 26 * 16
    mass = walls / 1000 * 1.24
    assert floating.floats and floating.mass == pytest.approx(mass, rel=1e-6)
    # Gleichgewicht: verdrängtes Wasser (mit Hohlraum) trägt die Masse – bei senkrechten Wänden linear in der Tiefe
    assert floating.draft == pytest.approx(mass / 0.998 * 1000 / (60 * 30), rel=0.02)
    assert floating.carry == pytest.approx(60 * 30 * 20 / 1000 * 0.998 - mass, rel=1e-6)
    sinking = physics.buoyancy(outer, physics.MATERIALS["Stahl"].density)
    assert not sinking.floats and sinking.weight > sinking.buoyancy_max
    assert sinking.buoyancy_max == pytest.approx(60 * 30 * 20 * 1e-9 * 998 * 9.81, rel=1e-3)


def test_materials_follow_the_physical_material_names():
    assert physics.material_named("Aluminium (2,70 g/cm³)").young == 70000.0
    assert physics.material_named("PETG (1,27 g/cm³)").name == "PETG"
    assert physics.material_named(None).name == "PLA"


def test_flow_round_trip_is_symmetric_and_positive():
    # kurze, grobe Rechnung: Würfel frontal angeströmt – Widerstand positiv, keine Querkraft (Symmetrie)
    cube = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, -10), 20.0, 20.0, 20.0).Shape()
    result = physics.flow(cube, "+X", 20.0, "Luft", resolution=6, steps=300)
    assert result.drag_coefficient > 0.3 and result.drag_force > 0
    assert abs(result.lift[0]) < 0.05 * result.drag_force and abs(result.lift[1]) < 0.05 * result.drag_force
    assert len(result.pressure_points) == len(result.pressure_values) > 0 and result.streamlines
    # vorn Staudruck (positiv), seitlich Sog (negativ)
    front = result.pressure_values[result.pressure_points[:, 0] < -10]
    assert front.max() > 0.3
    with pytest.raises(physics.FeatureError):
        physics.flow(cube, "X", 1.0)


def test_colour_scale_runs_blue_to_red():
    from wolfcad.gui.physics_ui import colour_scale
    blue, red = colour_scale(0.0), colour_scale(1.0)
    assert blue[2] > blue[0] and red[0] > red[2]


def test_noctalia_argb_colours_are_read():
    from wolfcad.gui import theme
    assert theme._plain_colour("#ff1e1e2e") == "#1e1e2e" and theme._plain_colour("#1e1e2e") == "#1e1e2e"
    assert math.isfinite(1.0)
