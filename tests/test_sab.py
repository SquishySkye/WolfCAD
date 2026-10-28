# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Tests für den ASM-Binärleser anhand einer von Hand aufgebauten Minimaldatei."""

import struct

import pytest

from wolfcad.f3d import sab


def _text(tag: int, value: str) -> bytes:
    return bytes([tag, len(value)]) + value.encode()


def _minimal_file() -> bytes:
    header = sab.MAGIC + b"4" + struct.pack("<4i", 23100, 0, 1, 2)
    header += _text(7, "Produkt") + _text(7, "Kern") + _text(7, "Datum")
    header += b"".join(b"\x06" + struct.pack("<d", v) for v in (10.0, 1e-6, 1e-10))
    point = (_text(0x0D, "point") + b"\x0c" + struct.pack("<i", -1) + b"\x04" + struct.pack("<i", -1)
             + b"\x13" + struct.pack("<3d", 1.0, 2.0, 3.0) + b"\x11")
    plane = (_text(0x0E, "plane") + _text(0x0D, "surface") + b"\x0c" + struct.pack("<i", 0)
             + b"\x0b\x0a\x11")
    return header + point + plane + _text(0x0D, "End-of-ASM-data")


def test_header_and_records_are_decoded():
    result = sab.parse(_minimal_file())
    assert result.header.version == 23100
    assert result.header.unit_scale == 10.0
    assert [r.name for r in result.records] == ["point", "plane-surface"]
    assert result.records[0].fields == [sab.Ref(-1), -1, (1.0, 2.0, 3.0)]


def test_references_resolve_to_records():
    result = sab.parse(_minimal_file())
    plane = result.records[1]
    assert result.get(plane.fields[0]) is result.records[0]
    assert result.get(sab.Ref(-1)) is None
    assert plane.fields[1:] == [True, False]


def test_foreign_data_is_rejected():
    with pytest.raises(sab.SabError):
        sab.parse(b"PK\x03\x04 kein ASM")
