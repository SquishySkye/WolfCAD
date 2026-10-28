# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Leser für das ASM-Binärformat ("ASM BinaryFile4", Dateiendung .smb/.smbh).

Fusion speichert die exakte Geometrie (B-Rep) jedes Entwurfs in diesem Format.
Es ist ein Abkömmling des ACIS-SAB-Formats: Nach einem kurzen Kopf folgt eine
Liste von Datensätzen (Entities). Jeder Wert im Datenstrom beginnt mit einem
Tag-Byte, das den Typ des folgenden Werts festlegt. Ein Datensatz endet mit
dem Terminator-Tag.

Der Aufbau wurde ausschließlich durch Analyse eigener .f3d-Dateien ermittelt.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"ASM BinaryFile"

# Tag-Bytes des Datenstroms
TAG_CHAR = 0x02
TAG_SHORT = 0x03
TAG_LONG = 0x04
TAG_FLOAT = 0x05
TAG_DOUBLE = 0x06
TAG_STR_U8 = 0x07       # Zeichenkette, Länge als 1 Byte
TAG_STR_U16 = 0x08      # Zeichenkette, Länge als 2 Bytes
TAG_STR_U32 = 0x09      # Zeichenkette, Länge als 4 Bytes
TAG_FALSE = 0x0A
TAG_TRUE = 0x0B
TAG_REF = 0x0C          # Verweis auf einen anderen Datensatz (Index, -1 = leer)
TAG_IDENT = 0x0D        # Typname des Datensatzes (letzter Namensteil)
TAG_SUBIDENT = 0x0E     # vorangestellter Namensteil, z. B. "plane" in "plane-surface"
TAG_SUB_OPEN = 0x0F     # Beginn eines eingebetteten Unterdatensatzes
TAG_SUB_CLOSE = 0x10    # Ende eines eingebetteten Unterdatensatzes
TAG_TERMINATOR = 0x11   # Ende des Datensatzes
TAG_STR_U32_B = 0x12
TAG_POSITION = 0x13     # Punkt im Raum (3 Doubles)
TAG_VECTOR = 0x14       # Richtungsvektor (3 Doubles)
TAG_ENUM = 0x15
TAG_VECTOR_2D = 0x16    # 2 Doubles
TAG_INT64 = 0x17

# Datensatznamen, die das Ende der eigentlichen Geometriedaten markieren
_END_MARKERS = ("End-of-ASM-data", "Begin-of-ASM-History-Data", "End-of-ACIS-data")


class SabError(ValueError):
    """Die Datei entspricht nicht dem erwarteten ASM-Binärformat."""


@dataclass(frozen=True)
class Ref:
    """Verweis auf einen Datensatz über dessen Index; -1 bedeutet 'kein Ziel'."""

    index: int

    def __bool__(self) -> bool:
        return self.index >= 0


@dataclass(frozen=True)
class Sub:
    """Markierung für Beginn (open=True) oder Ende eines Unterdatensatzes."""

    open: bool


@dataclass
class Record:
    """Ein Datensatz: Position in der Datei, Typname und die gelesenen Werte."""

    index: int
    name: str
    fields: list = field(default_factory=list)


@dataclass
class Header:
    version: int        # z. B. 23100 für ASM 231.0.0
    flags: int          # Bit 0 gesetzt = Datei enthält Änderungshistorie (.smbh)
    product: str
    kernel: str
    date: str
    unit_scale: float   # Millimeter pro Modelleinheit (10.0 = Zentimeter)
    res_abs: float
    res_nor: float


@dataclass
class SabFile:
    header: Header
    records: list[Record]

    def get(self, ref: Ref) -> Record | None:
        """Löst einen Verweis auf; leere oder ungültige Verweise ergeben None."""
        if isinstance(ref, Ref) and 0 <= ref.index < len(self.records):
            return self.records[ref.index]
        return None

    def of_type(self, name: str) -> list[Record]:
        return [r for r in self.records if r.name == name]


class _Reader:
    """Liest getaggte Werte nacheinander aus einem Bytepuffer."""

    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos

    def _unpack(self, fmt: str, size: int):
        values = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return values

    def _string(self, length: int) -> str:
        raw = self.data[self.pos:self.pos + length]
        self.pos += length
        return bytes(raw).decode("utf-8", errors="replace")

    def value(self):
        """Liest ein Tag samt Wert und gibt (tag, wert) zurück."""
        tag = self.data[self.pos]
        self.pos += 1
        if tag == TAG_REF:
            return tag, Ref(self._unpack("<i", 4)[0])
        if tag == TAG_DOUBLE:
            return tag, self._unpack("<d", 8)[0]
        if tag in (TAG_LONG, TAG_ENUM):
            return tag, self._unpack("<i", 4)[0]
        if tag in (TAG_POSITION, TAG_VECTOR):
            return tag, self._unpack("<3d", 24)
        if tag in (TAG_STR_U8, TAG_IDENT, TAG_SUBIDENT):
            return tag, self._string(self._unpack("<B", 1)[0])
        if tag == TAG_TRUE:
            return tag, True
        if tag == TAG_FALSE:
            return tag, False
        if tag == TAG_TERMINATOR:
            return tag, None
        if tag == TAG_SUB_OPEN:
            return tag, Sub(True)
        if tag == TAG_SUB_CLOSE:
            return tag, Sub(False)
        if tag == TAG_VECTOR_2D:
            return tag, self._unpack("<2d", 16)
        if tag == TAG_INT64:
            return tag, self._unpack("<q", 8)[0]
        if tag == TAG_STR_U16:
            return tag, self._string(self._unpack("<H", 2)[0])
        if tag in (TAG_STR_U32, TAG_STR_U32_B):
            return tag, self._string(self._unpack("<I", 4)[0])
        if tag == TAG_CHAR:
            return tag, self._unpack("<b", 1)[0]
        if tag == TAG_SHORT:
            return tag, self._unpack("<h", 2)[0]
        if tag == TAG_FLOAT:
            return tag, self._unpack("<f", 4)[0]
        raise SabError(f"Unbekanntes Tag 0x{tag:02x} an Position {self.pos - 1}")


def parse(data: bytes) -> SabFile:
    """Zerlegt eine .smb/.smbh-Datei in Kopf und Datensätze.

    Gelesen wird nur der aktuelle Modellstand; ein eventuell angehängter
    Historienblock (.smbh) wird nicht ausgewertet.
    """
    if not data.startswith(MAGIC):
        raise SabError("Keine ASM-Binärdatei (Kennung fehlt)")

    # Kopf: Kennung + 1 Zeichen Formatversion, danach vier ungetaggte Ganzzahlen
    pos = len(MAGIC) + 1
    version, _record_count, _entity_count, flags = struct.unpack_from("<4i", data, pos)
    reader = _Reader(data, pos + 16)

    # Danach folgen sechs getaggte Kopfwerte (3 Texte, 3 Zahlen)
    head = [reader.value()[1] for _ in range(6)]
    header = Header(version, flags, *head)

    records: list[Record] = []
    end = len(data)
    while reader.pos < end:
        # Namensteile sammeln, bis das abschließende IDENT-Tag kommt
        parts: list[str] = []
        while True:
            tag, value = reader.value()
            if tag == TAG_SUBIDENT:
                parts.append(value)
            elif tag == TAG_IDENT:
                parts.append(value)
                break
            else:
                raise SabError(
                    f"Datensatzname erwartet, Tag 0x{tag:02x} gefunden "
                    f"(Position {reader.pos})"
                )
        name = "-".join(parts)
        if name.startswith(_END_MARKERS):
            break

        record = Record(len(records), name)
        while True:
            tag, value = reader.value()
            if tag == TAG_TERMINATOR:
                break
            record.fields.append(value)
        records.append(record)

    return SabFile(header, records)
