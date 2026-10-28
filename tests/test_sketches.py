# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für den Skizzenleser anhand eines von Hand aufgebauten Mini-Segments."""

import struct

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

from wolfcad.f3d import segment, sketches

GUIDS = {name: f"{index:08d}-0000-0000-0000-000000000000" for index, name in enumerate(
    ("DcSketch", "MSketch", "CoordinateSystem", "Line3D", "CircArc3D", "Root"), start=1)}
SCHEMA = {guid: {"name": name} for name, guid in GUIDS.items() if name != "Root"}


def _text(value: str) -> bytes:
    return struct.pack("<I", len(value)) + value.encode()


def _class(name: str, ids: list[int]) -> bytes:
    parent = "" if name == "Root" else GUIDS["Root"]
    return (_text(GUIDS[name]) + _text(parent) + struct.pack("<I", 0) + _text("Fusion")
            + struct.pack("<I", len(ids)) + b"".join(struct.pack("<Q", i) for i in ids))


def _record(tag: str, object_id: int, content: bytes) -> bytes:
    return _text(tag) + struct.pack("<Q", object_id) + content


def _reference(object_id: int) -> bytes:
    return b"\x01" + struct.pack("<Q", object_id) + b"\x00\x00"


def _attributes() -> bytes:
    return (bytes(5) + b"\x01" + struct.pack("<I", 1) + _text("crv_primary_id")
            + _text("IntrinsicMetaTypeuint64") + struct.pack("<Q", 7))


def _segment() -> tuple[bytes, bytes]:
    # Skizzenebene: x bleibt x, Skizzen-y zeigt nach Welt-z, Ebene liegt bei Welt-y = 2 cm
    matrix = struct.pack("<12d", 1, 0, 0, 0, 0, 0, -1, 2, 0, 1, 0, 0)
    line = _attributes() + struct.pack("<6d", 0, 0, 0, 4, 0, 0) + bytes(48) + _reference(20)
    arc = (_attributes() + struct.pack("<12d", 0, 0, 0, 0, 0, 1, 1, 0, 0, 3, 0, 3.14159)
           + _reference(20))
    bulk = (_record("256", 10, bytes(8))                       # das Skizzen-Feature
            + _reference(10) + bytes(4)                        # Basisabschnitt nennt das Feature
            + _record("257", 20, bytes(4) + _reference(30))    # Modellskizze -> Koordinatensystem
            + _record("258", 30, bytes(7) + matrix)
            + _record("259", 40, line) + _record("260", 50, arc))
    meta = (b"KOPF" + struct.pack("<I", 6)
            + _class("DcSketch", [10]) + _class("MSketch", [20]) + _class("CoordinateSystem", [30])
            + _class("Line3D", [40]) + _class("CircArc3D", [50]) + _class("Root", [])
            + struct.pack("<QQ", 10, 0) + struct.pack("<QI", 50, 0))
    return meta, bulk


def _read():
    meta, bulk = _segment()
    directory = segment.parse_directory(meta)
    return sketches.read_sketches(bulk, directory, segment.index_sections(bulk, directory), SCHEMA)


def test_sketch_is_linked_to_feature_plane_and_curves():
    found = _read()
    assert list(found) == [10]
    sketch = found[10]
    assert sketch.matrix[7] == 2
    assert sketch.lines == [sketches.SketchLine((0, 0, 0), (4, 0, 0), tag=7)]
    assert sketch.arcs[0].radius == 3


def test_shape_is_placed_in_world_coordinates_in_millimetres():
    shape = sketches.to_shape(_read()[10])
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    low, high = box.CornerMin(), box.CornerMax()
    # Linie 4 cm entlang x, Halbkreis Radius 3 cm; alles in der Ebene y = 20 mm
    assert abs(low.Y() - 20) < 1e-3 and abs(high.Y() - 20) < 1e-3
    assert abs(low.X() + 30) < 1e-3 and abs(high.X() - 40) < 1e-3
