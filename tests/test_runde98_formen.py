"""Runde 98: Formen – Schleifenschnitt wie Blender (Ebene quer zur Ansatzkante, nur der zusammenhängende Ring)
und die Kantenliste für die Netzansicht."""
import numpy as np
import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Pnt

from wolfcad.core import meshes, sculpt


def _mesh(shape) -> sculpt.SculptMesh:
    return sculpt.SculptMesh.from_triangles(meshes.tessellate(shape))


def _closed(mesh: sculpt.SculptMesh) -> bool:
    keys, _owner = mesh._edge_lookup()
    _unique, counts = np.unique(keys, return_counts=True)
    return bool(np.all(counts == 2))


def _volume(mesh: sculpt.SculptMesh) -> float:
    corners = mesh.vertices[mesh.faces]
    return float(np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum() / 6)


def test_loop_cut_on_a_cube_adds_one_closed_ring():
    mesh = _mesh(BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape())
    volume = _volume(mesh)
    added = mesh.loop_cut([5.0, 0.0, 0.0], [1.0, 0.0, 0.0])
    new = mesh.vertices[-added:]
    assert added >= 4 and np.allclose(new[:, 0], 5.0)
    assert _closed(mesh)
    assert _volume(mesh) == pytest.approx(volume)          # schneiden verändert die Form nicht


def test_loop_positions_are_even_and_slide_together():
    assert np.allclose(sculpt.SculptMesh.loop_positions(3), [0.25, 0.5, 0.75])
    slid = sculpt.SculptMesh.loop_positions(3, 1.0)
    assert np.allclose(np.diff(slid), 0.25) and slid.max() < 1.0
    assert sculpt.SculptMesh.loop_positions(1, -1.0)[0] > 0.0


def test_several_cuts_across_an_edge_and_mask_is_interpolated():
    mesh = _mesh(BRepPrimAPI_MakeCylinder(10.0, 30.0).Shape())
    mesh.mask[:] = np.where(mesh.vertices[:, 2] > 15, 1.0, 0.0)
    upright = [(a, b) for a, b in mesh.wire_edges() if abs(mesh.vertices[a][2] - mesh.vertices[b][2]) > 29]
    a, b = upright[0]
    preview = mesh.loop_cuts_preview(a, b, 2)
    assert set(np.round(preview[:, 2], 6)) == {10.0, 20.0}
    added = mesh.loop_cuts(a, b, 2)
    assert added > 0 and _closed(mesh)
    rings = mesh.vertices[-added:]
    assert set(np.round(rings[:, 2], 6)) == {10.0, 20.0}
    assert len(mesh.mask) == len(mesh.vertices)


def test_only_the_ring_at_the_spot_is_cut():
    """Zwei getrennte Säulen, die dieselbe Ebene schneidet: nur die Säule an der Stelle bekommt den Ring."""
    left = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 5.0, 5.0, 20.0).Shape()
    right = BRepPrimAPI_MakeBox(gp_Pnt(20, 0, 0), 5.0, 5.0, 20.0).Shape()
    mesh = _mesh(BRepAlgoAPI_Fuse(left, right).Shape())
    added = mesh.loop_cut([2.5, 0.0, 10.0], [0.0, 0.0, 1.0])
    assert added > 0 and np.all(mesh.vertices[-added:, 0] < 10.0)
    assert _closed(mesh)


def test_loop_edge_skips_quad_diagonals():
    mesh = _mesh(BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape())
    found = mesh.ray_hit(np.array([5.0, -20.0, 5.0]), np.array([0.0, 1.0, 0.0]))
    a, b = mesh.loop_edge(found[0], found[1])
    span = mesh.vertices[b] - mesh.vertices[a]
    assert np.count_nonzero(np.abs(span) > 1e-9) == 1     # eine Würfelkante, keine Diagonale


def test_nothing_to_cut_outside_the_mesh_and_refine_still_closed():
    mesh = _mesh(BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape())
    assert mesh.loop_cut([50.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == 0
    assert len(mesh.loop_preview([50.0, 0.0, 0.0], [1.0, 0.0, 0.0])) == 0
    mesh.refine(np.ones(len(mesh.vertices), bool), 3.0)       # gemeinsame Zerlegung (_split_faces)
    assert _closed(mesh)


def test_wire_edges_are_unique_and_follow_topology():
    mesh = _mesh(BRepPrimAPI_MakeBox(10.0, 10.0, 10.0).Shape())
    edges = mesh.wire_edges()
    assert len(edges) == 18                                   # 12 Würfelkanten + 6 Diagonalen
    mesh.subdivide()
    assert len(mesh.wire_edges()) == 2 * 18 + 3 * 12      # jede Kante halbiert, je Dreieck drei innere Kanten
