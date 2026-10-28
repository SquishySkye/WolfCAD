# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für den Segmentleser anhand eines von Hand aufgebauten Mini-Segments."""

import struct

from wolfcad.f3d import segment

ORDER_GUID = "11111111-1111-1111-1111-111111111111"
SKETCH_GUID = "22222222-2222-2222-2222-222222222222"
ROOT_GUID = "33333333-3333-3333-3333-333333333333"
SCHEMA = {
    ORDER_GUID: {"name": "DcCreationOrderFeatures"},
    SKETCH_GUID: {"name": "DcSketch"},
}


def _text(value: str) -> bytes:
    return struct.pack("<I", len(value)) + value.encode()


def _class(guid: str, parent: str, namespace: str, ids: list[int]) -> bytes:
    return (_text(guid) + _text(parent) + struct.pack("<I", 1) + _text(namespace)
            + struct.pack("<I", len(ids)) + b"".join(struct.pack("<Q", i) for i in ids))


def _record(tag: str, object_id: int, content: bytes) -> bytes:
    return _text(tag) + struct.pack("<Q", object_id) + content


def _segment() -> tuple[bytes, bytes]:
    references = b"".join(b"\x01" + struct.pack("<Q", i) + b"\x00\x00" for i in (7, 5))
    # Typnummer = Position der Klasse in der Klassentabelle + 256
    order = _record("256", 3, bytes(17) + struct.pack("<I", 2) + references)
    # Objekt 5 liegt eingebettet hinter Objekt 7 und steht nicht in der Positionstabelle
    sketches = _record("257", 7, b"\xaa" * 6) + _record("257", 5, b"\xbb" * 6)
    bulk = order + sketches
    meta = (b"KOPF" + struct.pack("<I", 3)
            + _class(ORDER_GUID, ROOT_GUID, "Fusion", [3])
            + _class(SKETCH_GUID, ROOT_GUID, "Fusion", [5, 7])
            + _class(ROOT_GUID, "", "", [])
            + struct.pack("<QQ", 3, 0) + struct.pack("<QQ", 7, len(order))
            + struct.pack("<QI", 7, 0))
    return meta, bulk


def test_directory_lists_classes_and_positions():
    meta, _ = _segment()
    directory = segment.parse_directory(meta)
    assert [entry.guid for entry in directory.classes] == [ORDER_GUID, SKETCH_GUID, ROOT_GUID]
    assert directory.classes[1].ids == [5, 7]
    # Länge des ersten Objekts: 7 Byte Typnummer, 8 Byte ID, 17 Byte Kopf, 4 Byte Anzahl, 2 Verweise
    assert directory.offsets == {3: 0, 7: 7 + 8 + 17 + 4 + 2 * 11}


def test_embedded_objects_are_indexed():
    meta, bulk = _segment()
    sections = segment.index_sections(bulk, segment.parse_directory(meta))
    assert set(sections) == {3, 5, 7}
    assert all(len(positions) == 1 for positions in sections.values())


def test_sections_with_foreign_type_numbers_are_ignored():
    meta, bulk = _segment()
    # Typnummer 258 gehört zur Wurzelklasse und damit zur Kette von Objekt 7, 299 zu keiner Klasse
    bulk += _record("258", 7, b"") + _record("299", 7, b"")
    sections = segment.index_sections(bulk, segment.parse_directory(meta))
    assert len(sections[7]) == 2


def test_history_follows_the_stored_order():
    meta, bulk = _segment()
    history = segment.feature_history(meta, bulk, SCHEMA)
    assert [(entry.object_id, entry.kind) for entry in history] == [(7, "DcSketch"), (5, "DcSketch")]


def test_history_is_empty_without_schema():
    meta, bulk = _segment()
    assert segment.feature_history(meta, bulk, {}) == []


def _wide(value: str) -> bytes:
    return struct.pack("<I", len(value)) + value.encode("utf-16-le")


def test_parameters_are_attached_to_their_feature():
    parameter_guid = "44444444-4444-4444-4444-444444444444"
    feature_base = "55555555-5555-5555-5555-555555555555"
    schema = {**SCHEMA, parameter_guid: {"name": "Parameter"}, feature_base: {"name": "DcFeature"}}
    reference = b"\x01" + struct.pack("<Q", 7) + b"\x00\x00"
    parameter = _record("259", 9, bytes(31) + _wide("40.00 mm") + bytes(9) + _wide("AlongDistance")
                        + bytes(4) + _wide("mm") + _wide("d6") + struct.pack("<d", 4.0)
                        + bytes(36) + reference)
    order = _record("256", 3, bytes(17) + struct.pack("<I", 1) + reference)
    bulk = order + _record("257", 7, b"\xaa" * 6) + parameter
    meta = (b"KOPF" + struct.pack("<I", 4)
            + _class(ORDER_GUID, ROOT_GUID, "Fusion", [3])
            + _class(SKETCH_GUID, feature_base, "Fusion", [7])
            + _class(feature_base, ROOT_GUID, "Fusion", [])
            + _class(parameter_guid, ROOT_GUID, "CommonData", [9])
            + _class(ROOT_GUID, "", "", [])
            + struct.pack("<QQ", 3, 0) + struct.pack("<QI", 9, 0))
    (entry,) = segment.feature_history(meta, bulk, schema)
    (parameter,) = entry.parameters
    assert (parameter.role, parameter.expression, parameter.unit, parameter.name, parameter.value) == (
        "AlongDistance", "40.00 mm", "mm", "d6", 4.0)


def test_timestamps_are_mapped_to_features():
    from wolfcad.f3d import bodies

    update_guid = "66666666-6666-6666-6666-666666666666"
    feature_base = "55555555-5555-5555-5555-555555555555"
    schema = {**SCHEMA, update_guid: {"name": "DynamicUpdateData"}, feature_base: {"name": "DcFeature"}}

    def pair(object_id: int, stamp: int) -> bytes:
        return b"\x01" + struct.pack("<Q", object_id) + b"\x00\x00" + struct.pack("<Q", stamp)

    # Klassentabelle: Skizze (0, ist ein Feature), Feature-Basis (1), Aktualisierungsdaten (2), Wurzel (3)
    bulk = (_record("256", 7, b"\xaa" * 6)
            + _record("258", 8, bytes(16) + pair(7, 1111) + pair(99, 2222) + pair(7, 3333) + bytes(8)))
    meta = (b"KOPF" + struct.pack("<I", 4)
            + _class(SKETCH_GUID, feature_base, "Fusion", [7])
            + _class(feature_base, ROOT_GUID, "Fusion", [])
            + _class(update_guid, ROOT_GUID, "Fusion", [8])
            + _class(ROOT_GUID, "", "", [])
            + struct.pack("<QI", 8, 0))
    stamps = bodies.feature_timestamps(bulk, segment.parse_directory(meta), schema)
    # Objekt 99 ist kein Feature und beendet die Liste nicht; beide Stempel von Feature 7 zählen
    assert stamps == {1111: 7, 3333: 7}
