# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Runde 85: Optimierungen – Zerlegung von Kopien aus dem gemerkten Formkern, Hüllquader der Zerlegung,
Gültigkeit je Formkern, weniger Kindprozesse beim verteilten Rechnen."""
import numpy as np
import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.TopLoc import TopLoc_Location

from wolfcad.core import features as ft, guard
from wolfcad.gui import mesh


def _part():
    plate = BRepPrimAPI_MakeBox(40.0, 30.0, 5.0).Shape()
    pin = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(20.0, 15.0, -1.0), gp_Dir(0, 0, 1)), 4.0, 7.0).Shape()
    return BRepAlgoAPI_Cut(plate, pin).Shape()


def _copy(shape, angle_degrees: float, shift: tuple):
    """Kopie mit demselben Formkern an anderer Lage (wie ein Kreismuster sie erzeugt)."""
    trsf = gp_Trsf()
    trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), np.radians(angle_degrees))
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(*shift))
    return shape.Moved(TopLoc_Location(move.Multiplied(trsf)))


def test_copy_mesh_comes_from_the_partner_and_matches_a_fresh_one():
    mesh._PARTNER_MESHES.clear()
    original = _part()
    first = mesh.pick_mesh(original)
    copy = _copy(original, 50.0, (100.0, 20.0, 3.0))
    assert copy.IsPartner(original) and not copy.IsEqual(original)
    reused = mesh.pick_mesh(copy)                   # aus dem Formkern bewegt
    mesh._PARTNER_MESHES.clear()
    fresh = mesh.pick_mesh(copy)                    # frisch zerlegt
    assert np.array_equal(reused.triangle_face, fresh.triangle_face)
    assert np.allclose(reused.triangles, fresh.triangles, atol=1e-3)
    assert np.allclose(reused.segments, fresh.segments, atol=1e-3)
    assert np.allclose(reused.face_points, fresh.face_points, atol=1e-6)
    assert np.allclose(reused.edge_points, fresh.edge_points, atol=1e-6)
    # Merkpunkte der Kopie passen zur Auswahl im Kern (features.face_point)
    assert np.allclose(reused.face_points, [ft.face_point(face) for face in ft.faces_of(copy)], atol=1e-6)
    assert first.triangles.shape == reused.triangles.shape


def test_pick_bounds_are_remembered():
    pick = mesh.pick_mesh(BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape())
    bounds = mesh.pick_bounds(pick)
    assert np.allclose(bounds, [[0, 0, 0], [10, 20, 30]], atol=1e-4)
    assert mesh.pick_bounds(pick) is bounds


def test_validity_is_shared_between_copies():
    ft._VALIDITY.clear()
    original = _part()
    assert ft.is_valid(original)
    copy = _copy(original, 20.0, (5.0, 0.0, 0.0))
    entries = sum(len(v) for v in ft._VALIDITY.values())
    assert ft.is_valid(copy) and sum(len(v) for v in ft._VALIDITY.values()) == entries    # keine neue Prüfung


def test_parallel_work_needs_enough_items_per_child():
    assert guard.run_parallel(lambda item: item * 2, [1, 2, 3, 4, 5]) is None      # zu wenige je Kind: selbst rechnen
    if guard.available():
        result = guard.run_parallel(lambda item: item * 2, list(range(12)))
        assert result == [item * 2 for item in range(12)]


def test_cheap_work_is_left_to_the_caller_after_one_measured_run():
    if not guard.available():
        return

    def cheap(item):
        return item + 1

    guard._COST.pop(cheap.__code__, None)
    assert guard.run_parallel(cheap, list(range(30))) == list(range(1, 31))     # erster Aufruf: verteilt, gemessen
    assert cheap.__code__ in guard._COST
    assert guard.run_parallel(cheap, list(range(30))) is None                   # bekannt billig: Aufrufer rechnet
