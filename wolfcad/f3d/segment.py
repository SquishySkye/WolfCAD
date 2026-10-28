# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Leser für die Segmentdaten eines Fusion-Entwurfs (MetaStream.dat / BulkStream.dat).

MetaStream.dat ist ein Verzeichnis:
  - Kopf (Segmentname, Kennungen)
  - Anzahl der Klassen, dann die Klassentabelle, je Klasse:
        Klassen-GUID (Text), GUID der Basisklasse (Text, bei der Wurzel leer),
        Versionsnummer, Namensraum (Text), Anzahl, Objekt-IDs (je 8 Byte)
    Auf eine Klasse folgen ihre noch nicht genannten Basisklassen (meist ohne Objekte).
  - Positionstabelle: Paare (Objekt-ID, Position im BulkStream), je 8 Byte
BulkStream.dat enthält die Objekte selbst an den genannten Positionen.

Texte sind ASCII mit vorangestellter 4-Byte-Länge.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

_MAX_NAME = 64
# Typnummer eines Abschnitts = Position der Klasse in der Klassentabelle + 256
_TAG_BASE = 256
_TRAILER = 12
# Klassenkennungen sind fast immer 36 Zeichen lang, vereinzelt weichen sie ab
_GUID_LENGTHS = (30, 40)


@dataclass
class ClassEntry:
    namespace: str
    guid: str
    parent_guid: str
    version: int
    ids: list[int] = field(default_factory=list)


@dataclass
class Directory:
    classes: list[ClassEntry]
    offsets: dict[int, int]             # Objekt-ID -> Position im BulkStream
    tail_start: int                     # Position hinter der Klassentabelle (zur Analyse)

    def class_of(self) -> dict[int, ClassEntry]:
        """Objekt-ID -> Klasse."""
        return {object_id: entry for entry in self.classes for object_id in entry.ids}


def _text(data: bytes, position: int) -> tuple[str, int] | None:
    """Liest einen längenpräfixierten ASCII-Text; None, wenn dort keiner steht."""
    if position + 4 > len(data):
        return None
    (length,) = struct.unpack_from("<I", data, position)
    raw = data[position + 4:position + 4 + length]
    if length > _MAX_NAME or len(raw) < length or not all(32 <= b < 127 for b in raw):
        return None
    return raw.decode("ascii"), position + 4 + length


def _class_entry(data: bytes, position: int) -> tuple[ClassEntry, int] | None:
    """Liest einen Eintrag der Klassentabelle; None, wenn an der Stelle keiner beginnt."""
    guid = _text(data, position)
    if guid is None or not _GUID_LENGTHS[0] <= len(guid[0]) <= _GUID_LENGTHS[1]:
        return None
    parent = _text(data, guid[1])
    if parent is None or (parent[0] and not _GUID_LENGTHS[0] <= len(parent[0]) <= _GUID_LENGTHS[1]):
        return None
    (version,) = struct.unpack_from("<I", data, parent[1])
    namespace = _text(data, parent[1] + 4)
    if namespace is None:
        return None
    position = namespace[1]
    (count,) = struct.unpack_from("<I", data, position)
    position += 4
    if position + 8 * count > len(data):
        return None
    ids = list(struct.unpack_from(f"<{count}Q", data, position))
    return ClassEntry(namespace[0], guid[0].upper(), parent[0].upper(), version, ids), position + 8 * count


def _first_entry(data: bytes) -> int:
    """Sucht den Beginn der Klassentabelle hinter dem Kopf."""
    for position in range(0, min(len(data), 4096)):
        found = _class_entry(data, position)
        # Der erste echte Eintrag muss von einem weiteren gefolgt werden
        if found and _class_entry(data, found[1]):
            return position
    raise ValueError("Klassentabelle nicht gefunden")


def parse_directory(meta: bytes) -> Directory:
    position = _first_entry(meta)
    classes: list[ClassEntry] = []
    while True:
        found = _class_entry(meta, position)
        if found is None:
            break
        classes.append(found[0])
        position = found[1]

    # Die Positionstabelle steht am Dateiende, gefolgt von 12 Byte Abschluss
    # (höchste vergebene ID und ein Nullfeld). Sie wird rückwärts gelesen,
    # solange die IDs bekannt sind und absteigen.
    known = {object_id for entry in classes for object_id in entry.ids}
    offsets: dict[int, int] = {}
    cursor = len(meta) - _TRAILER - 16
    previous = None
    while cursor >= position:
        object_id, offset = struct.unpack_from("<QQ", meta, cursor)
        if object_id not in known or (previous is not None and object_id >= previous):
            break
        offsets[object_id] = offset
        previous = object_id
        cursor -= 16
    return Directory(classes, offsets, position)


# Beginn eines Objekts im BulkStream: Typnummer als kurzer Zifferntext, dann die Objekt-ID
_RECORD_START = re.compile(rb"[\x01-\x05]\x00\x00\x00[0-9]{1,5}")


def index_sections(bulk: bytes, directory: Directory) -> dict[int, list[int]]:
    """Sucht alle Abschnitte aller Objekte: Objekt-ID -> Positionen der Abschnittsinhalte.

    Ein Objekt wird je Klassenebene in eigenen Abschnitten gespeichert, die nicht
    zusammenhängen müssen und je nach Stand der Datei in anderer Reihenfolge
    stehen. Jeder Abschnitt beginnt mit der Typnummer (Zifferntext mit Länge) und
    der 8-Byte-ID. Ein Fund zählt nur, wenn die ID bekannt ist und die Typnummer
    zu einer Klasse aus der Vererbungskette des Objekts gehört.
    """
    classes = directory.class_of()
    index_of = {entry.guid: index for index, entry in enumerate(directory.classes)}
    by_guid = {entry.guid: entry for entry in directory.classes}

    # Klasse -> zulässige Typnummern (eigene Klasse und alle Basisklassen)
    allowed: dict[str, set[int]] = {}
    for entry in directory.classes:
        tags, guid = set(), entry.guid
        while guid in by_guid and len(tags) < 64:
            tags.add(index_of[guid] + _TAG_BASE)
            guid = by_guid[guid].parent_guid
        allowed[entry.guid] = tags

    sections: dict[int, list[int]] = {}
    for match in _RECORD_START.finditer(bulk):
        start = match.start()
        length = bulk[start]
        tag = bulk[start + 4:start + 4 + length]
        if not tag.isdigit() or start + 12 + length > len(bulk):
            continue
        (object_id,) = struct.unpack_from("<Q", bulk, start + 4 + length)
        entry = classes.get(object_id)
        if entry is not None and int(tag) in allowed[entry.guid]:
            sections.setdefault(object_id, []).append(start + 12 + length)
    return sections


# Ein Verweis auf ein Objekt: Kennbyte 1, 8-Byte-ID, zwei Füllbytes
_REFERENCE_SIZE = 11
# Kopf von DcCreationOrderFeatures vor der Verweisliste (u. a. Verweis auf das Fragment)
_ORDER_HEADER = 17
_ORDER_CLASS = "DcCreationOrderFeatures"


# Aufbau eines Parameter-Objekts (Klasse "Parameter"), an 9550 Parametern geprüft:
# 31 Byte Kopf, Ausdruck, 9 oder 10 Byte, Rolle, 4 Byte, Einheit, Name, Wert (Double).
# 36 Byte hinter dem Wert folgt der Verweis auf das Feature, dem der Parameter gehört.
_PARAMETER_CLASS = "Parameter"
_FEATURE_BASE = "DcFeature"
_PARAMETER_GAP_UNIT, _PARAMETER_GAP_OWNER = 4, 36
# Länge des Kopfes vor dem Ausdruck: 31 Byte in älteren Dateien, 35 in neueren (Runde 82, „camera.f3d“ vom
# Oktober 2026 – dort las der alte Wert keinen einzigen Parameter, Abstände und Quadermaße fehlten)
_PARAMETER_HEADERS = (31, 35)
_PARAMETER_MAX_GAP = 17     # größte Zahl von Füllbytes vor der Rolle
_MAX_TEXT = 300


@dataclass(frozen=True)
class FeatureParameter:
    role: str           # Bedeutung im Feature, z. B. "AlongDistance" oder "Radius"
    expression: str     # Ausdruck wie eingegeben, z. B. "400.00 mm"
    unit: str
    name: str           # Parametername in Fusion, z. B. "d2"
    value: float        # Zahlenwert in Fusions internen Einheiten (Längen in cm, Winkel in rad)


@dataclass(frozen=True)
class HistoryEntry:
    object_id: int
    kind: str       # Klassenname laut Schema, z. B. "DcExtrudeFeature"
    parameters: tuple[FeatureParameter, ...] = ()


def _wide_text(bulk: bytes, position: int) -> tuple[str, int]:
    """Liest einen UTF-16-Text mit vorangestellter Zeichenzahl (4 Byte)."""
    (count,) = struct.unpack_from("<I", bulk, position)
    if count > _MAX_TEXT:
        raise ValueError("Textlänge unplausibel")
    end = position + 4 + 2 * count
    value = bulk[position + 4:end].decode("utf-16-le")
    if not all(character.isprintable() for character in value):
        raise ValueError("Kein lesbarer Text")
    return value, end


def _subclass_ids(directory: Directory, schema: dict[str, dict], base_name: str) -> set[int]:
    """IDs aller Objekte, deren Klasse von `base_name` abgeleitet ist."""
    by_guid = {entry.guid: entry for entry in directory.classes}
    ids: set[int] = set()
    for entry in directory.classes:
        guid = entry.guid
        while guid in by_guid:
            if schema.get(guid, {}).get("name") == base_name:
                ids.update(entry.ids)
                break
            guid = by_guid[guid].parent_guid
    return ids


def _parameter_at(bulk: bytes, start: int, feature_ids: set[int]) -> tuple[int, FeatureParameter]:
    """Liest einen Parameter ab `start`; gibt (ID des Features, Parameter) zurück oder wirft ValueError."""
    for header in _PARAMETER_HEADERS:
        try:
            return _parameter_with_header(bulk, start + header, feature_ids)
        except (ValueError, struct.error, IndexError, UnicodeDecodeError):
            continue
    raise ValueError("Kein lesbarer Parameter")


def _parameter_with_header(bulk: bytes, at: int, feature_ids: set[int]) -> tuple[int, FeatureParameter]:
    """Liest den Parameter, dessen Ausdruck bei `at` beginnt (siehe _PARAMETER_HEADERS)."""
    expression, position = _wide_text(bulk, at)
    # Vor der Rolle stehen je nach Stand der Datei 9 oder 10 Füllbytes
    gap = next((g for g in range(_PARAMETER_MAX_GAP)
                if 0 < struct.unpack_from("<I", bulk, position + g)[0] <= _MAX_TEXT), None)
    if gap is None:
        raise ValueError("Rolle des Parameters nicht gefunden")
    role, position = _wide_text(bulk, position + gap)
    unit, position = _wide_text(bulk, position + _PARAMETER_GAP_UNIT)
    name, position = _wide_text(bulk, position)
    (value,) = struct.unpack_from("<d", bulk, position)
    owner_at = position + 8 + _PARAMETER_GAP_OWNER
    if bulk[owner_at] != 1:
        raise ValueError("Verweis auf das Feature fehlt")
    (owner,) = struct.unpack_from("<Q", bulk, owner_at + 1)
    if owner not in feature_ids or value != value or abs(value) >= 1e12:
        raise ValueError("Parameter gehört zu keinem Feature")
    return owner, FeatureParameter(role, expression, unit, name, value)


def feature_parameters(bulk: bytes, directory: Directory, sections: dict[int, list[int]],
                       schema: dict[str, dict]) -> dict[int, list[FeatureParameter]]:
    """Alle Parameter des Entwurfs, gruppiert nach der ID ihres Features.

    Parameter mit abweichendem Aufbau werden ausgelassen.
    """
    feature_ids = _subclass_ids(directory, schema, _FEATURE_BASE)
    result: dict[int, list[FeatureParameter]] = {}
    for entry in directory.classes:
        if schema.get(entry.guid, {}).get("name") != _PARAMETER_CLASS:
            continue
        for object_id in entry.ids:
            # Der Abschnitt mit den Parameterdaten ist derjenige, der sich lesen lässt
            for start in sections.get(object_id, ()):
                try:
                    owner, parameter = _parameter_at(bulk, start, feature_ids)
                except (ValueError, struct.error, IndexError, UnicodeDecodeError):
                    continue
                result.setdefault(owner, []).append(parameter)
                break
    return result


def read_reference_list(bulk: bytes, position: int) -> list[int]:
    """Liest eine Verweisliste: Anzahl (4 Byte), dann je Verweis 11 Byte."""
    (count,) = struct.unpack_from("<I", bulk, position)
    if position + 4 + count * _REFERENCE_SIZE > len(bulk):
        raise ValueError("Verweisliste reicht über das Datenende hinaus")
    ids = []
    for index in range(count):
        start = position + 4 + index * _REFERENCE_SIZE
        if bulk[start] != 1:
            raise ValueError("Unerwartetes Kennbyte in der Verweisliste")
        ids.append(struct.unpack_from("<Q", bulk, start + 1)[0])
    return ids


def feature_history(meta: bytes, bulk: bytes, schema: dict[str, dict],
                    sections: dict[int, list[int]] | None = None) -> list[HistoryEntry]:
    """Die Konstruktionsschritte des Entwurfs in Erstellreihenfolge.

    Quelle ist das Objekt DcCreationOrderFeatures, das alle Features als
    Verweisliste führt. `schema` (siehe schema.py) liefert die Klassennamen;
    ohne Schema lässt sich das Objekt nicht finden und die Liste bleibt leer.
    `sections` kann übergeben werden, wenn der Abschnittsindex schon vorliegt.
    """
    directory = parse_directory(meta)
    name_of = {entry.guid: schema.get(entry.guid, {}).get("name", "") for entry in directory.classes}
    order_ids = [object_id for entry in directory.classes
                 if name_of[entry.guid] == _ORDER_CLASS for object_id in entry.ids]
    if not order_ids:
        return []
    if sections is None:
        sections = index_sections(bulk, directory)
    classes = directory.class_of()

    parameters = feature_parameters(bulk, directory, sections, schema)
    history: list[HistoryEntry] = []
    for order_id in sorted(order_ids):
        feature_ids: list[int] = []
        # Die Liste steht in dem Abschnitt, in dem sie sich vollständig lesen lässt
        for start in sections.get(order_id, ()):
            try:
                candidate = read_reference_list(bulk, start + _ORDER_HEADER)
            except (ValueError, struct.error, IndexError):
                continue
            if all(feature_id in classes for feature_id in candidate):
                feature_ids = candidate
                break
        for feature_id in feature_ids:
            entry = classes.get(feature_id)
            history.append(HistoryEntry(feature_id, name_of.get(entry.guid, "") if entry else "",
                                        tuple(parameters.get(feature_id, ()))))
    return history



def find_sections(bulk: bytes, directory: Directory, class_guid: str) -> dict[int, int]:
    """Abschnitte einer Klasse im BulkStream: Objekt-ID -> Position des Abschnittsinhalts.

    Ein Objekt wird je Klassenebene in eigenen Abschnitten gespeichert; jeder
    beginnt mit der Typnummer der Ebene (als Zifferntext) und der Objekt-ID.
    Gesucht werden die Abschnitte der Ebene `class_guid` für alle Objekte dieser
    Klasse und ihrer abgeleiteten Klassen.
    """
    index = next((i for i, entry in enumerate(directory.classes) if entry.guid == class_guid), None)
    if index is None:
        return {}
    by_guid = {entry.guid: entry for entry in directory.classes}
    wanted: set[int] = set()
    for entry in directory.classes:
        guid = entry.guid
        while guid in by_guid:
            if guid == class_guid:
                wanted.update(entry.ids)
                break
            guid = by_guid[guid].parent_guid

    tag = str(index + _TAG_BASE).encode()
    start = struct.pack("<I", len(tag)) + tag
    sections: dict[int, int] = {}
    position = bulk.find(start)
    while position != -1 and position + len(start) + 8 <= len(bulk):
        (object_id,) = struct.unpack_from("<Q", bulk, position + len(start))
        if object_id in wanted and object_id not in sections:
            sections[object_id] = position + len(start) + 8
        position = bulk.find(start, position + 1)
    return sections


def class_guid(directory: Directory, schema: dict[str, dict], name: str) -> str | None:
    """GUID der Klasse mit dem Schemanamen `name`, sofern sie im Entwurf vorkommt."""
    return next((entry.guid for entry in directory.classes
                 if schema.get(entry.guid, {}).get("name") == name), None)


# Fusions Name eines Schritts: im Abschnitt des Features folgt auf eine Kennung aus Nullen (UUID-Text
# „00000000-0000-…“) nach einigen Bytes der Name als UTF-16-Text mit Zeichenzahl davor – der Grundname wie
# „Extrusion“ oder „Skizze“ (die Nummer zählt Fusion erst in der Anzeige hoch) bzw. ein vom Nutzer vergebener Name.
_NULL_UUID = "00000000-0000-0000-0000-000000000000".encode("utf-16-le")
_NAME_TEXT = re.compile(rb"([\x01-\x7f])\x00\x00\x00((?:[^\x00]\x00|[\x00-\xff][\x01-\xff]){1,127})")
_NAME_WINDOW = 400


def _plain_name(text: str) -> bool:
    """Sieht der Text wie ein Name aus (Buchstaben am Anfang, nur lateinische Schrift und übliche Zeichen)?"""
    return (len(text) >= 3 and (text[0].isascii() and text[0].isalpha() or text[0] in "ÄÖÜäöü")
            and all(ord(character) < 0x100 for character in text))


def feature_names(bulk: bytes, sections: dict[int, list[int]], history: list) -> dict[int, str]:
    """Fusions Namen der Schritte: Feature-ID -> Name (nur, wo er sich lesen lässt)."""
    names: dict[int, str] = {}
    for entry in history:
        for start in sections.get(entry.object_id, ()):
            anchor = bulk.find(_NULL_UUID, start, start + _NAME_WINDOW)
            if anchor < 0:
                continue
            for match in _NAME_TEXT.finditer(bulk, anchor + len(_NULL_UUID), anchor + len(_NULL_UUID) + _NAME_WINDOW):
                length = match.group(1)[0]
                raw = bulk[match.start(2):match.start(2) + 2 * length]
                try:
                    text = raw.decode("utf-16-le")
                except UnicodeDecodeError:
                    continue
                # ein Name: lesbar, nicht nur Ziffern, keine Kennung im UUID-Muster
                # (lateinische Schrift: Zeichensalat aus zufällig passenden Bytes fällt so heraus)
                if len(text) == length and text.isprintable() and not text.isdigit() \
                        and not re.fullmatch(r"[0-9a-f-]{36}", text) and _plain_name(text):
                    names[entry.object_id] = text
                    break
            if entry.object_id in names:
                break
    return names
