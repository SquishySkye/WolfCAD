# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Fusions Flächenverweise: Benennungsrezept und Flächenattribut lesen, benannte Flächen im eigenen Körper
wiederfinden und „Fläche löschen“ bzw. „Flächen versetzen“ nachbauen – mit nachgebildeten Daten."""
import struct
from types import SimpleNamespace

import pytest

from wolfcad.core import features as ft
from wolfcad.core import solids
from wolfcad.f3d import faces

SOURCE = "nachgebildet-flaechen.f3d"


def recipe_bytes(entries) -> bytes:
    """Ein Rezept im Aufbau der Datei: 6 Byte Kopf, 1, 3, Anzahl Einträge; je Eintrag Gruppen."""
    data = b"\0" * 6 + struct.pack("<3i", 1, 3, len(entries))
    for entry in entries:
        data += struct.pack("<i", len(entry))
        for text, operations in entry:
            data += struct.pack("<i", len(text)) + text.encode() + struct.pack("<2i", 0, len(operations))
            data += struct.pack(f"<{len(operations)}i", *operations) + struct.pack("<i", 0)
    return data + b"\0" * 32


def test_recipe_and_attribute_names():
    entries = [(("1", (339,)),), (("14", (313, -345)), ("22", (333, -345)))]
    assert faces.read_recipe(recipe_bytes(entries), 0) == entries
    assert faces.read_recipe(b"\0" * 6 + struct.pack("<3i", 1, 3, 9999), 0) == []     # unplausibel: leer
    # Attributfelder wie in der Geometriedatei (Zahlen vor dem Text: Textlänge)
    fields = ["generic_tag_attrib_def", 3, 3, -1, "generic_tag_attrib_def ", 2, 1, "2", 0, 3, 6854, 6859, 6860, 0,
              1, "2", 0, 2, 6854, 6861, 0]
    assert faces.attribute_name(fields) == (("2", (6854, 6859, 6860)), ("2", (6854, 6861)))
    assert faces.attribute_name(["anderes"]) is None


def box_state(origin: int = 5) -> ft.BuildState:
    state = ft.BuildState()
    solids.Box(length=20, width=20, height=10).apply(state)
    state.body_origin[next(iter(state.bodies))] = origin
    return state


TOP = (("1", (100,)),)


@pytest.fixture
def fake_file():
    """Nachgebildeter Entwurf plus eingelesene Geometrie, deren Oberseite den Namen TOP trägt."""
    reference_state = box_state()
    shape = next(iter(reference_state.bodies.values()))
    top = next(face for face in ft.faces_of(shape) if ft.face_point(face)[2] == pytest.approx(10))
    design = SimpleNamespace(face_features={}, edge_features={}, targets={}, stamp_feature={},
                             bodies=SimpleNamespace(creator_of={100: 5}),
                             leaves_no_body=lambda feature_id: False)
    ft._DESIGN_CACHE[SOURCE] = design
    ft._IMPORT_CACHE[(SOURCE, "blob")] = SimpleNamespace(parts=[], named_faces={TOP: [(top, frozenset())]})
    yield design
    del ft._DESIGN_CACHE[SOURCE]
    del ft._IMPORT_CACHE[(SOURCE, "blob")]
    ft._STORED_CACHE.clear()


def test_offset_named_face(fake_file):
    fake_file.face_features[1] = faces.FusionFaceFeature(1, "offset", [
        faces.FaceSet(bodies=[100], faces=[faces.FaceReference(TOP)])], {"distance": 0.5})
    state = box_state()
    ft.FusionOffsetFaces(source=SOURCE, fusion_id=1, distance=5.0).apply(state)
    # die Oberseite wandert 5 mm nach oben: 20 × 20 × 15
    assert solids._volume(next(iter(state.bodies.values()))) == pytest.approx(20 * 20 * 15, rel=1e-6)


def test_named_face_not_in_model(fake_file):
    fake_file.face_features[2] = faces.FusionFaceFeature(2, "delete", [
        faces.FaceSet(bodies=[100], faces=[faces.FaceReference((("9", (999,)),))])])
    with pytest.raises(ft.FeatureError, match="nicht zu finden"):
        ft.FusionDeleteFace(source=SOURCE, fusion_id=2).apply(box_state())


def test_delete_named_fillet_face(fake_file):
    # Würfel mit gerundeter senkrechter Kante; die Rundungsfläche trägt in der „Datei“ einen Namen
    state = box_state()
    name = next(iter(state.bodies))
    ft.Fillet(body=name, radius=3, edge_points=[[10, 10, 5]]).apply(state)
    rounded = state.bodies[name]
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    round_face = next(f for f in ft.faces_of(rounded) if BRepAdaptor_Surface(f).GetType() == GeomAbs_Cylinder)
    rounding = (("7", (200,)),)
    ft._IMPORT_CACHE[(SOURCE, "blob")].named_faces[rounding] = [(round_face, frozenset())]
    fake_file.face_features[3] = faces.FusionFaceFeature(3, "delete", [
        faces.FaceSet(bodies=[100], faces=[faces.FaceReference(rounding)])])
    ft.FusionDeleteFace(source=SOURCE, fusion_id=3).apply(state)
    assert solids._volume(state.bodies[name]) == pytest.approx(20 * 20 * 10, rel=1e-6)    # wieder scharfkantig
