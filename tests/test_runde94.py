"""Runde 94: Formen (Sculpting) wie in Blender – Netz, Pinsel, Symmetrie, Maske, Unterteilen, Timeline-Schritt."""
import numpy as np
import pytest

from wolfcad.core import features as ft, meshes, sculpt, solids
from wolfcad.core.timeline import Timeline


@pytest.fixture(scope="module")
def sphere():
    timeline = Timeline()
    timeline.add(solids.Sphere(diameter=40))
    timeline.add(meshes.BodyToMesh(body="Körper1", deflection=0.3))
    return timeline, next(iter(timeline.state.meshes))


def _mesh(sphere):
    timeline, name = sphere
    return sculpt.SculptMesh.from_triangles(timeline.state.meshes[name])


def test_welded_mesh_is_closed(sphere):
    mesh = _mesh(sphere)
    # geschlossene Kugel: Euler-Charakteristik V − E + F = 2
    edges = np.unique(np.sort(np.concatenate([mesh.faces[:, [0, 1]], mesh.faces[:, [1, 2]],
                                              mesh.faces[:, [2, 0]]]), axis=1), axis=0)
    assert len(mesh.vertices) - len(edges) + len(mesh.faces) == 2


@pytest.mark.parametrize("kind", sculpt.BRUSHES)
def test_every_brush_changes_the_mesh(sphere, kind):
    mesh = _mesh(sphere)
    start = mesh.vertices.copy()
    hit = mesh.ray_hit((0, -100, 0), (0, 1, 0))
    assert hit is not None and hit[0][1] == pytest.approx(-20, abs=0.3)
    stroke = sculpt.Stroke(mesh, sculpt.Brush(kind=kind, radius=6, strength=0.8), hit[0], (0, 1, 0))
    for step in range(1, 10):
        stroke.move_to(hit[0] + np.array([step * 0.6, 0, step * 0.3]))
    if kind == "Maske":
        assert mesh.mask.max() > 0.5 and np.allclose(mesh.vertices, start)
    else:
        assert np.linalg.norm(mesh.vertices - start, axis=1).max() > 1e-3
        far = np.linalg.norm(start - hit[0], axis=1) > 6 + 6 + 1      # außerhalb des Pinselwegs
        assert np.allclose(mesh.vertices[far], start[far])


def test_draw_pushes_out_and_ctrl_pushes_in(sphere):
    for invert, sign in ((False, 1), (True, -1)):
        mesh = _mesh(sphere)
        hit = mesh.ray_hit((0, -100, 0), (0, 1, 0))
        sculpt.Stroke(mesh, sculpt.Brush(kind="Zeichnen", radius=6, strength=1.0, invert=invert), hit[0], (0, 1, 0))
        nearest = np.argmin(np.linalg.norm(mesh.vertices - hit[0], axis=1))
        assert sign * (np.linalg.norm(mesh.vertices[nearest]) - 20) > 0.1


def test_mask_protects_and_symmetry_mirrors(sphere):
    mesh = _mesh(sphere)
    mesh.mask[:] = 1.0
    start = mesh.vertices.copy()
    hit = mesh.ray_hit((0, -100, 0), (0, 1, 0))
    sculpt.Stroke(mesh, sculpt.Brush(kind="Zeichnen", radius=6, strength=1.0), hit[0], (0, 1, 0))
    assert np.allclose(mesh.vertices, start)                  # alles maskiert: nichts bewegt
    mesh.mask[:] = 0.0
    point = np.array([10.0, -17.3, 0.0])
    sculpt.Stroke(mesh, sculpt.Brush(kind="Zeichnen", radius=4, strength=1.0, symmetry=(True, False, False)),
                  point, (0, 1, 0))
    left = np.linalg.norm(mesh.vertices - start, axis=1)[start[:, 0] < -5].max()
    right = np.linalg.norm(mesh.vertices - start, axis=1)[start[:, 0] > 5].max()
    assert left == pytest.approx(right, rel=0.2) and left > 0.1


def test_subdivide_and_pack_roundtrip(sphere):
    mesh = _mesh(sphere)
    faces, vertices = len(mesh.faces), len(mesh.vertices)
    mesh.subdivide()
    assert len(mesh.faces) == 4 * faces
    # neue Ecken = Zahl der Kanten (geschlossenes Netz: 3F/2)
    assert len(mesh.vertices) == vertices + 3 * faces // 2
    back = sculpt.SculptMesh.unpack(mesh.pack())
    assert np.allclose(back.vertices, mesh.vertices, atol=1e-4) and np.array_equal(back.faces, mesh.faces)


def test_sculpt_step_replaces_mesh_and_survives_saving(sphere, tmp_path):
    timeline = Timeline()
    timeline.add(solids.Sphere(diameter=40))
    timeline.add(meshes.BodyToMesh(body="Körper1", deflection=0.3))
    name = next(iter(timeline.state.meshes))
    mesh = sculpt.SculptMesh.from_triangles(timeline.state.meshes[name])
    hit = mesh.ray_hit((0, -100, 0), (0, 1, 0))
    sculpt.Stroke(mesh, sculpt.Brush(kind="Aufblasen", radius=8, strength=1.0), hit[0], (0, 1, 0))
    timeline.add(sculpt.SculptStep(name="Formen1", mesh=name, data=mesh.pack()))
    assert timeline.errors == {}
    assert np.allclose(timeline.state.meshes[name], mesh.triangles(), atol=1e-4)
    path = tmp_path / "geformt.wcad"
    timeline.save(path)
    loaded = Timeline.load(path)
    assert loaded.errors == {} and np.allclose(loaded.state.meshes[name], mesh.triangles(), atol=1e-4)


def test_voxel_remesh_keeps_volume_and_is_closed(sphere):
    mesh = _mesh(sphere)
    result = sculpt.voxel_remesh(mesh, 0.8)
    volume = lambda m: np.einsum("ij,ij->i", m.vertices[m.faces][:, 0],
                                 np.cross(m.vertices[m.faces][:, 1], m.vertices[m.faces][:, 2])).sum() / 6
    assert volume(result) == pytest.approx(volume(mesh), rel=0.02)
    edges = np.unique(np.sort(np.concatenate([result.faces[:, [0, 1]], result.faces[:, [1, 2]],
                                              result.faces[:, [2, 0]]]), axis=1), axis=0)
    assert len(result.vertices) - len(edges) + len(result.faces) == 2          # geschlossen, ohne Löcher
    # gleichmäßig: Kantenlängen nahe der Voxelgröße
    lengths = np.linalg.norm(result.vertices[edges[:, 0]] - result.vertices[edges[:, 1]], axis=1)
    assert 0.4 < np.median(lengths) < 1.2


def test_refine_splits_only_long_edges_in_region_and_stays_closed(sphere):
    mesh = _mesh(sphere)
    region = mesh.vertices[:, 1] < -15                     # nur vorne
    before = len(mesh.faces)
    added = mesh.refine(region, detail=0.5)
    assert added > 0 and len(mesh.faces) == before + 2 * added
    edges = np.unique(np.sort(np.concatenate([mesh.faces[:, [0, 1]], mesh.faces[:, [1, 2]],
                                              mesh.faces[:, [2, 0]]]), axis=1), axis=0)
    assert len(mesh.vertices) - len(edges) + len(mesh.faces) == 2      # weiterhin geschlossen
    corners = mesh.vertices[mesh.faces]
    front = corners[:, :, 1].max(axis=1) < -16
    longest = np.linalg.norm(corners[:, [1, 2, 0]] - corners, axis=2).max(axis=1)
    assert longest[front].max() <= 0.5 + 1e-9                          # vorne fein
    assert longest[corners[:, :, 1].min(axis=1) > 10].max() > 0.5       # hinten unverändert grob
