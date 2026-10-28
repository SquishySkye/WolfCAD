# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Prüfwerkzeuge: Entformungs- und Wandstärkenanalyse."""

import numpy as np
import pytest

from wolfcad.core import analysis
from wolfcad.core.solids import Box, Hole, Shell
from wolfcad.core.timeline import Timeline
from wolfcad.gui.mesh import pick_mesh


def body(*features):
    timeline = Timeline()
    for feature in features:
        timeline.add(feature)
    assert not timeline.errors
    return timeline.state.body


def test_draft_classes_of_a_box():
    pick = pick_mesh(body(Box(length=20, width=10, height=5)))
    classes = analysis.draft_classes(pick.triangles, (0, 0, 1), 1.0)
    normals = analysis.triangle_normals(pick.triangles)
    assert set(classes[normals[:, 2] > 0.99]) == {analysis.DRAFT_OK}            # Deckel: nach oben, gut
    assert set(classes[normals[:, 2] < -0.99]) == {analysis.DRAFT_UNDERCUT}     # Boden: gegen die Zugrichtung
    assert set(classes[np.abs(normals[:, 2]) < 0.01]) == {analysis.DRAFT_FLAT}  # Seitenwände ohne Schräge


def test_wall_thickness_of_a_shell():
    hollow = body(Box(length=40, width=30, height=20), Shell(thickness=1.5))
    pick = pick_mesh(hollow)
    thickness = analysis.wall_thickness(hollow, pick.triangles)
    finite = thickness[np.isfinite(thickness)]
    assert finite.min() == pytest.approx(1.5, abs=0.05)
    classes = analysis.thickness_classes(thickness, 2.0)
    assert analysis.THIN in classes
    solid = body(Box(length=40, width=30, height=20))
    assert analysis.wall_thickness(solid, pick_mesh(solid).triangles).min() == pytest.approx(20.0, abs=0.05)


def test_curvature_of_a_filleted_box():
    from wolfcad.core.features import Fillet, faces_of
    rounded = body(Box(length=40, width=20, height=10), Fillet(radius=3.0, edge_points=[[0.0, 10.0, 10.0]]))
    pick = pick_mesh(rounded)
    radii = analysis.curvature_radii(faces_of(rounded), pick.triangles, pick.triangle_face)
    finite = radii[np.isfinite(radii)]
    assert len(finite) > 0 and finite.min() == pytest.approx(3.0, rel=1e-3)
    classes = analysis.curvature_classes(radii, 5.0)
    assert {analysis.FLAT, analysis.TIGHT_CURVE} <= set(classes)


def test_zebra_stripes_and_face_normals():
    """Zebra: gleich geneigte Dreiecke tragen denselben Streifen, eine Kugel zeigt beide Streifenfarben;
    die Normale einer gekrümmten Fläche zeigt nach außen."""
    from wolfcad.core import features as ft
    from wolfcad.core.solids import Cylinder, Sphere

    box = pick_mesh(body(Box(length=20, width=10, height=5)))
    stripes = analysis.zebra_classes(box.triangles, (0, 0, 1), 10.0)
    normals = analysis.triangle_normals(box.triangles)
    assert len(set(stripes[normals[:, 2] > 0.99])) == 1                 # ebene Oberseite: ein Streifen
    ball = pick_mesh(body(Sphere(diameter=20)))
    assert set(analysis.zebra_classes(ball.triangles, (0, 0, 1), 10.0)) == {0, 1}
    with pytest.raises(ValueError):
        analysis.zebra_classes(ball.triangles, (0, 0, 1), 0.0)

    shape = body(Box(length=20, width=10, height=5))
    top = next(face for face in ft.faces_of(shape) if abs(ft.face_point(face)[2] - 5) < 1e-6)
    assert analysis.face_normal(top) == pytest.approx((0, 0, 1))
    roller = body(Cylinder(diameter=10, height=20))
    wall = next(face for face in ft.faces_of(roller) if abs(ft.face_point(face)[2] - 10) < 1e-6)
    normal = analysis.face_normal(wall)
    assert abs(normal[2]) < 1e-9 and np.hypot(normal[0], normal[1]) == pytest.approx(1.0)


def test_zebra_reflection_follows_the_view():
    """Spiegelstreifen: eine Kugel zeigt aus jeder Blickrichtung beide Farben; die ebene Oberseite eine."""
    from wolfcad.core.solids import Sphere

    ball = pick_mesh(body(Sphere(diameter=20)))
    for view in ((0, 0, -1), (1, 0, 0), (0.5, 0.5, -0.7)):
        assert set(analysis.zebra_classes(ball.triangles, (0, 0, 1), 10.0, view)) == {0, 1}
    box = pick_mesh(body(Box(length=20, width=10, height=5)))
    normals = analysis.triangle_normals(box.triangles)
    stripes = analysis.zebra_classes(box.triangles, (0, 0, 1), 10.0, (0.3, 0.2, -0.9))
    assert len(set(stripes[normals[:, 2] > 0.99])) == 1


def test_zebra_mesh_is_finer_and_smooth_within_faces():
    """Feine Zebra-Streifen: auf einem grob zerlegten Zylinder entstehen mehr Streifenwechsel als Facetten-Klassen,
    die ebene Oberseite bleibt einfarbig."""
    from wolfcad.core.solids import Cylinder

    roller = pick_mesh(body(Cylinder(diameter=40, height=10)))
    coarse = analysis.zebra_classes(roller.triangles, (1, 0, 0), 5.0)
    fine_points, fine = analysis.zebra_mesh(roller.triangles, roller.triangle_face, (1, 0, 0), 5.0)
    assert len(fine_points) == len(roller.triangles) * 16
    assert len(fine) == len(roller.triangle_face) * 16
    assert set(fine) == {0, 1}
    # Normalen an den Ecken einer ebenen Fläche bleiben exakt (gemittelt aus gleichen Normalen)
    smooth = analysis.smooth_normals(roller.triangles, roller.triangle_face)
    flat = analysis.triangle_normals(roller.triangles)
    top = flat[:, 2] > 0.999
    assert np.allclose(smooth[top], np.array([0, 0, 1.0]), atol=1e-9)
    assert len(coarse) == len(roller.triangle_face)


def test_zebra_surface_uses_exact_normals():
    """Auf einem Zylindermantel stehen die echten Normalen waagerecht und zeigen von der Achse weg."""
    from wolfcad.core import features as ft
    from wolfcad.core.solids import Cylinder

    shape = body(Cylinder(diameter=40, height=10))
    roller = pick_mesh(shape)
    positions, normals = analysis.zebra_surface(ft.faces_of(shape), roller.triangles, roller.triangle_face, levels=1)
    centres = positions.reshape(-1, 3, 3).mean(axis=1)
    side = np.abs(normals[:, 2]) < 1e-6
    radial = centres[side][:, :2] / np.linalg.norm(centres[side][:, :2], axis=1)[:, None]
    assert side.sum() > 0
    assert np.allclose(np.sum(radial * normals[side][:, :2], axis=1), 1.0, atol=1e-3)
    assert set(analysis.zebra_from_normals(normals, (0, 0, 1), 5.0, (1, 0.2, -0.3))) == {0, 1}
