# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Werkzeuge aus dem Fusion-Abgleich Runde 57: Torus, Spule, Rohr, Punkt, Fläche teilen, Ausrichten, Regelfläche,
Normale umkehren, Netz schneiden/trennen/kombinieren/umkehren, Saum."""
import math

import numpy as np
import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from wolfcad.core import extras as ex
from wolfcad.core import features as ft
from wolfcad.core import meshes as ms
from wolfcad.core import sheetmetal as sm
from wolfcad.core import sketch as sk
from wolfcad.core import solids
from wolfcad.core import surfaces as sf
from wolfcad.core.timeline import Timeline


def volume(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def area(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, properties)
    return properties.Mass()


def fresh() -> Timeline:
    timeline = Timeline()
    timeline.crash_guard = False
    return timeline


def test_torus_coil_and_pipe():
    timeline = fresh()
    timeline.add(ex.Torus(name="T", diameter=40, section=10))
    assert not timeline.errors, timeline.errors
    torus = next(iter(timeline.state.bodies.values()))
    assert volume(torus) == pytest.approx(2 * math.pi ** 2 * 20 * 5 ** 2, rel=1e-6)
    coil = fresh()
    coil.add(ex.Coil(name="S", diameter=30, pitch=8, turns=3, section=3))
    assert not coil.errors, coil.errors
    wire_length = 3 * math.hypot(math.pi * 30, 8)
    assert volume(next(iter(coil.state.bodies.values()))) == pytest.approx(math.pi * 1.5 ** 2 * wire_length, rel=0.01)
    pipe = fresh()
    pipe.add(sk.Sketch(name="P", plane="XY", entities=[sk.line(0, 0, 50, 0), sk.line(50, 0, 50, 40)]))
    pipe.add(ex.Pipe(name="R", path="P", diameter=10, wall=1))
    assert not pipe.errors, pipe.errors
    ring = math.pi * (5 ** 2 - 4 ** 2)
    assert volume(next(iter(pipe.state.bodies.values()))) == pytest.approx(ring * 90, rel=0.02)
    pipe.add(ex.Pipe(name="X", path="P", diameter=10, wall=6))           # Wand dicker als der halbe Durchmesser
    assert "kleiner als der halbe" in pipe.errors.get(len(pipe.features) - 1, "")


def test_point_split_face_and_align():
    timeline = fresh()
    timeline.add(ex.ConstructionPoint(name="Punkt1", x=1, y=2, z=3))
    assert timeline.state.points["Punkt1"] == (1, 2, 3)
    timeline.add(solids.Box(name="Q", length=20, width=20, height=10))
    body = next(iter(timeline.state.bodies))
    faces_before = len(ft.faces_of(timeline.state.bodies[body]))
    # Vorderseite (y = −10) an der Ebene x = 0 (YZ) teilen
    timeline.add(ex.SplitFace(name="F", body=body, face_points=[[0.0, -10.0, 5.0]], plane="YZ", offset=0.0))
    assert not timeline.errors, timeline.errors
    assert len(ft.faces_of(timeline.state.bodies[body])) == faces_before + 1
    assert volume(timeline.state.bodies[body]) == pytest.approx(4000)
    # Ausrichten: zweiter Quader mit seiner Unterseite auf die Oberseite des ersten
    timeline.add(solids.Box(name="Q2", length=10, width=10, height=10, center_x=50))
    second = [name for name in timeline.state.bodies if name != body][0]
    timeline.add(ex.Align(name="A", body=second, source=[50, 0, 0, 0, 0, -1], target=[0, 0, 10, 0, 0, 1]))
    assert not timeline.errors, timeline.errors
    from wolfcad.core.sheetmetal import _bounds
    low = _bounds(timeline.state.bodies[second])
    assert low[2] == pytest.approx(10, abs=1e-6) and low[5] == pytest.approx(20, abs=1e-6)
    assert (low[0] + low[3]) / 2 == pytest.approx(0, abs=1e-6)


def test_ruled_surface_and_reverse_normal():
    timeline = fresh()
    timeline.add(sk.Sketch(name="A", plane="XY", entities=[sk.line(0, 0, 20, 0)]))
    timeline.add(sk.Sketch(name="B", plane="XY", offset=10.0, entities=[sk.line(0, 5, 20, 5)]))
    timeline.add(ex.RuledSurface(name="R", sketch="A", sketch2="B"))
    assert not timeline.errors, timeline.errors
    surface = timeline.state.surfaces["Flächenkörper1"]
    assert area(surface) == pytest.approx(20 * math.hypot(5, 10), rel=1e-6)
    timeline.add(ex.ReverseNormal(name="N", surface="Flächenkörper1"))
    assert timeline.state.surfaces["Flächenkörper1"].Orientation() != surface.Orientation()


def test_mesh_cut_separate_combine_reverse():
    timeline = fresh()
    timeline.add(solids.Box(name="Q", length=20, width=20, height=20))
    timeline.add(solids.Box(name="Q2", length=4, width=4, height=4, center_x=50))
    names = list(timeline.state.bodies)
    for name in names:
        timeline.add(ms.BodyToMesh(name=f"N{name}", body=name))
    timeline.add(ex.MeshCombine(name="K", meshes=["Netz1", "Netz2"]))
    assert not timeline.errors, timeline.errors
    combined = [name for name in timeline.state.meshes][0]
    timeline.add(ex.MeshSeparate(name="T", mesh=combined))
    assert len(timeline.state.meshes) == 2
    big = max(timeline.state.meshes, key=lambda n: len(timeline.state.meshes[n]) and np.ptp(timeline.state.meshes[n][..., 0]))
    # die große Würfelhälfte oberhalb z = 10 abschneiden und schließen → Quader 20 × 20 × 10
    timeline.add(ex.MeshPlaneCut(name="E", mesh=big, plane="XY", offset=10.0))
    assert not timeline.errors, timeline.errors
    solid = ms.to_solid(timeline.state.meshes[big])
    assert volume(solid) == pytest.approx(20 * 20 * 10, rel=1e-6)
    before = np.array(timeline.state.meshes[big])
    timeline.add(ex.MeshReverse(name="U", mesh=big))
    assert np.allclose(timeline.state.meshes[big][:, 1], before[:, 2])


def test_hem_folds_edge_back():
    timeline = fresh()
    timeline.add(sk.Sketch(name="S", plane="XY", entities=sk.rectangle(0, 0, 40, 30)))
    timeline.add(sm.BaseFlange(name="Blech", sketch="S", thickness=1))
    body = next(iter(timeline.state.bodies))
    timeline.add(ex.Hem(name="H", body=body, edge_points=[[20, 0, 1]], length=6, gap=0.5))
    assert not timeline.errors, timeline.errors
    from wolfcad.core.sheetmetal import _bounds
    low = _bounds(timeline.state.bodies[body])
    # umgelegt: der Saum liegt über dem Blech (z bis 1 + 0,5 + 1), ragt nur um Radius + Dicke nach vorn
    assert low[5] == pytest.approx(2.5, abs=1e-6) and low[1] == pytest.approx(-(0.25 + 1), abs=1e-6)
    flat = sm.flat_pattern(timeline.state.sheets[body])
    assert _bounds(flat)[4] - _bounds(flat)[1] == pytest.approx(
        30 + sm.bend_allowance(180, 0.25, 1, sm.DEFAULT_K_FACTOR) + 6, abs=1e-5)
