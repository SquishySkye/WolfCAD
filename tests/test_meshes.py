# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Netze (Fusion-Reiter NETZ): STL einfügen, in Volumenkörper umwandeln, Körper zu Netz, verkleinern, STL speichern."""
import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from wolfcad.core import meshes as ms
from wolfcad.core import solids
from wolfcad.core.timeline import Timeline


def volume(shape) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def test_body_to_mesh_stl_roundtrip_and_back_to_body(tmp_path):
    timeline = Timeline()
    timeline.add(solids.Box(name="Q", length=20, width=10, height=5))
    body = next(iter(timeline.state.bodies))
    timeline.add(ms.BodyToMesh(name="N", body=body))
    assert not timeline.errors, timeline.errors
    triangles = timeline.state.meshes["Netz1"]
    assert len(triangles) == 12 and not timeline.state.bodies         # Quader: 6 Flächen × 2 Dreiecke
    path = tmp_path / "quader.stl"
    ms.write_stl(triangles, str(path))
    again = ms.read_stl(str(path))
    assert again.shape == (12, 3, 3)
    # Einfügen (gepackt im Entwurf) mit Maßstab, dann wieder Volumenkörper
    other = Timeline()
    other.add(ms.MeshImport(name="I", source=str(path), data=ms.pack(again), scale=2.0))
    other.add(ms.MeshToBody(name="B", mesh="Netz1"))
    assert not other.errors, other.errors
    solid = next(iter(other.state.bodies.values()))
    assert volume(solid) == pytest.approx(40 * 20 * 10, rel=1e-6)
    from wolfcad.core import features as ft
    assert len(ft.faces_of(solid)) == 6                              # gleiche Ebenen wieder zusammengefasst
    # Speichern und Laden ohne die STL-Datei
    path.unlink()
    loaded = Timeline.from_document(other.to_document())
    assert not loaded.errors and len(loaded.state.bodies) == 1


def test_reduce_and_errors():
    timeline = Timeline()
    timeline.add(solids.Sphere(name="K", diameter=20))
    timeline.add(ms.BodyToMesh(name="N", body=next(iter(timeline.state.bodies)), deflection=0.05))
    fine = len(timeline.state.meshes["Netz1"])
    timeline.add(ms.MeshReduce(name="R", mesh="Netz1", cell=2.0))
    assert not timeline.errors, timeline.errors
    assert 0 < len(timeline.state.meshes["Netz1"]) < fine
    timeline.add(ms.MeshReduce(name="R2", mesh="Netz1", cell=1000.0))
    assert "nichts übrig" in str(timeline.errors)
    open_mesh = Timeline()
    import numpy as np
    open_mesh.add(ms.MeshImport(name="I", data=ms.pack(np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]], dtype=float))))
    open_mesh.add(ms.MeshToBody(name="B", mesh="Netz1"))
    assert "nicht geschlossen" in str(open_mesh.errors)


def test_repair_closes_hole_fixes_orientation_and_smooth():
    import numpy as np
    timeline = Timeline()
    timeline.add(solids.Box(name="Q", length=10, width=10, height=10))
    timeline.add(ms.BodyToMesh(name="N", body=next(iter(timeline.state.bodies))))
    triangles = np.array(timeline.state.meshes["Netz1"])
    broken = np.concatenate([triangles[2:], triangles[3:4], triangles[5:6][:, [0, 2, 1]]])   # Loch, doppelt, falsch herum
    broken[4] = broken[4][[0, 2, 1]]                                                        # ein Dreieck umgedreht
    repaired, report = ms.repair(broken)
    assert report["löcher"] >= 1 and report["doppelt"] >= 1 and report["umgedreht"] >= 1
    solid = ms.to_solid(repaired)
    assert volume(solid) == pytest.approx(1000, rel=1e-6)
    # Glätten: eine Kugel bleibt fast gleich groß (Taubin schrumpft kaum)
    sphere = Timeline()
    sphere.add(solids.Sphere(name="K", diameter=20))
    sphere.add(ms.BodyToMesh(name="N", body=next(iter(sphere.state.bodies)), deflection=0.2))
    sphere.add(ms.MeshSmooth(name="G", mesh="Netz1", iterations=10))
    assert not sphere.errors, sphere.errors
    radii = np.linalg.norm(sphere.state.meshes["Netz1"].reshape(-1, 3), axis=1)
    assert radii.mean() == pytest.approx(10, rel=0.03)


def test_huge_body_to_mesh_stays_fast():
    """Eine riesige Kugel (Ø 10 m) wird nicht in Millionen Dreiecke zerlegt (Feinheit relativ zur Größe)."""
    import time
    timeline = Timeline()
    timeline.crash_guard = False
    timeline.add(solids.Sphere(name="K", diameter=10000))
    start = time.perf_counter()
    timeline.add(ms.BodyToMesh(name="N", body=next(iter(timeline.state.bodies))))
    assert not timeline.errors and time.perf_counter() - start < 10
    assert len(timeline.state.meshes["Netz1"]) < 200000


def test_body_to_mesh_with_crash_guard_does_not_hang():
    """Mit Absturzschutz (Probelauf im abgespaltenen Prozess) hing die parallele Zerlegung des Kerns 45 s."""
    import time
    from wolfcad.core import guard
    if not guard.available():
        pytest.skip("Absturzschutz hier nicht verfügbar")
    timeline = Timeline()
    timeline.crash_guard = True
    timeline.add(solids.Sphere(name="K", diameter=60))
    start = time.perf_counter()
    timeline.add(ms.BodyToMesh(name="N", body=next(iter(timeline.state.bodies))))
    assert not timeline.errors and time.perf_counter() - start < 10
