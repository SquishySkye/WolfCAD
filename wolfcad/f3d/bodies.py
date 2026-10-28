# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Körperzuordnung und Verschiebungen eines Fusion-Entwurfs.

Zusammenhang der Objekte (Namen laut Schema), an echten Daten ermittelt:
  DcBody         ein Körper des Entwurfs: im Abschnitt der Klasse steht bei Byte 6 seine
                 Kennung (UUID als UTF-16-Text), direkt dahinter ein Byte, das 1 ist,
                 solange der Körper im Modell existiert; außerdem ein Verweis auf das
                 Feature, das ihn erzeugt hat
  DRBody         Verweis eines Features auf einen Körper: nennt dessen Kennung in einem
                 seiner Abschnitte (neben weiteren Kennungen, etwa der Komponente)
  DcMoveFeature  verweist auf seine Zielkörper (DRBody) und auf ein DcMoveMatrix
  DcMoveMatrix   enthält die Bewegung als 4×4-Matrix (zeilenweise, Längen in cm)
  DcCombineFeature  Byte 10 des Klassenabschnitts ist die Operation (0 Vereinigen,
                 1 Ausschneiden, 2 Schnittmenge); die Körperverweise nennen zuerst den
                 Zielkörper, danach die Werkzeugkörper
  DcDeleteBody   verweist auf den Körper, der entfernt wird
  DcPasteBodies  Einfügen einer Körperkopie: verweist auf ein ModelDataRef (ab Byte 6 eine Verweisliste auf
                 DRBody – in den Beispielen immer genau ein kopierter Körper) und auf ein eingebettetes
                 DcBaseFeature, das die Kopie als neuen DcBody erzeugt (es steht nicht selbst im Verlauf)
  DynamicUpdateData  gehört zu einem Objekt und beginnt nach 16 Byte mit Paaren aus Verweis
                 (11 Byte) und Zeitstempel (8 Byte): der Stand, den das genannte Objekt bei
                 der letzten Berechnung hatte. Die gespeicherten Körper der .smb tragen den
                 Zeitstempel des Features, das sie zuletzt verändert hat. In den Beispielen
                 gehört jeder solche Zeitstempel zu genau einem Feature.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from . import primitives, segment, sketches
from .segment import Directory

_WINDOW = 400   # Suchbereich für Verweise und Kennungen ab einem Abschnittsanfang
_BODY_UUID_OFFSET = 6   # Lage der Kennung im Abschnitt der Klasse DcBody
_COMBINE_OPERATION_OFFSET = 10
_UPDATE_HEADER = 16     # Bytes vor dem ersten Paar in DynamicUpdateData
_UPDATE_PAIR = 19       # Verweis (11 Byte) und Zeitstempel (8 Byte)
_MODEL_LIST_OFFSET = 6  # Lage der Verweisliste im Abschnitt von ModelDataRef
# Kennung: Zeichenzahl 36 (4 Byte), dann 36 UTF-16-Zeichen im Muster einer UUID
_UUID = re.compile(rb"\x24\x00\x00\x00((?:[0-9a-fA-F-]\x00){36})")


@dataclass
class FusionMove:
    feature_id: int
    matrix: tuple[float, ...]                       # 16 Werte, Bewegung in cm
    creators: list[int] = field(default_factory=list)   # erzeugende Features der Zielkörper


@dataclass
class FusionCombine:
    feature_id: int
    operation: int                  # 0 Vereinigen, 1 Ausschneiden, 2 Schnittmenge
    target: int                     # erzeugendes Feature des Zielkörpers
    tools: list[int]                # erzeugende Features der Werkzeugkörper


def feature_timestamps(bulk: bytes, directory: Directory, schema: dict[str, dict]) -> dict[int, int]:
    """Zeitstempel -> ID des Features, das diesen Zeitstempel trägt."""
    guid = segment.class_guid(directory, schema, "DynamicUpdateData")
    features = segment._subclass_ids(directory, schema, "DcFeature")
    stamps: dict[int, int] = {}
    for start in (segment.find_sections(bulk, directory, guid) if guid else {}).values():
        position = start + _UPDATE_HEADER
        # Paare aus Verweis und Zeitstempel lesen, solange gültige Verweise folgen
        while (position + _UPDATE_PAIR <= len(bulk) and bulk[position] == 1
               and bulk[position + 9:position + 11] == b"\x00\x00"):
            target, stamp = struct.unpack_from("<QxxQ", bulk, position + 1)
            if target in features and stamp:
                stamps.setdefault(stamp, target)
            position += _UPDATE_PAIR
    return stamps


@dataclass
class FusionPaste:
    feature_id: int
    sources: list[int]              # erzeugende Features der kopierten Körper
    base: int                       # eingebettetes DcBaseFeature: „Erzeuger“ der Kopie


@dataclass
class BodyData:
    creator_of: dict[int, int] = field(default_factory=dict)   # DcBody-ID -> erzeugendes Feature
    moves: dict[int, FusionMove] = field(default_factory=dict)  # Feature-ID -> Verschiebung
    combines: dict[int, FusionCombine] = field(default_factory=dict)
    removals: dict[int, list[int]] = field(default_factory=dict)    # Feature-ID -> Erzeuger der Körper
    # Feature-ID -> erzeugende Features aller Körper, die das Feature nennt (Reihenfolge der Datei)
    targets: dict[int, list[int]] = field(default_factory=dict)
    # Features, von denen mindestens ein Körper im fertigen Modell noch existiert
    living_creators: set[int] = field(default_factory=set)
    pastes: dict[int, FusionPaste] = field(default_factory=dict)    # Feature-ID -> Einfügen


def _uuids(bulk: bytes, start: int) -> list[str]:
    window = bulk[start:start + _WINDOW]
    return [match.group(1).decode("utf-16-le").lower() for match in _UUID.finditer(window)]


def read_bodies(bulk: bytes, directory: Directory, sections: dict[int, list[int]],
                schema: dict[str, dict]) -> BodyData:
    """Liest, welches Feature welchen Körper erzeugt hat, und alle Verschiebungen."""
    def ids(name: str) -> set[int]:
        return set(sketches._ids_of(directory, schema, name))

    def references(object_id: int, targets: set[int]) -> list[int]:
        found: list[int] = []
        for start in sections.get(object_id, ()):
            found.extend(sketches._references(bulk, start, start + _WINDOW, targets))
        return found

    features = segment._subclass_ids(directory, schema, "DcFeature")
    data = BodyData()

    # Körper: Kennung (an fester Stelle im Abschnitt der Klasse) und erzeugendes Feature
    body_guid = segment.class_guid(directory, schema, "DcBody")
    body_sections = segment.find_sections(bulk, directory, body_guid) if body_guid else {}
    body_of_uuid: dict[str, int] = {}
    for body_id, start in body_sections.items():
        creators = references(body_id, features)
        match = _UUID.match(bulk, start + _BODY_UUID_OFFSET)
        if not creators or match is None:
            continue
        data.creator_of[body_id] = creators[0]
        body_of_uuid[match.group(1).decode("utf-16-le").lower()] = body_id
        if bulk[match.end()] == 1:
            data.living_creators.add(creators[0])

    # Körperverweise: über die Kennung zum Körper und weiter zum erzeugenden Feature
    creator_of_reference: dict[int, int] = {}
    for reference_id in ids("DRBody"):
        for start in sections.get(reference_id, ()):
            body_id = next((body_of_uuid[u] for u in _uuids(bulk, start) if u in body_of_uuid), None)
            if body_id is not None:
                creator_of_reference[reference_id] = data.creator_of[body_id]
                break

    matrices = ids("DcMoveMatrix")
    for move_id in ids("DcMoveFeature"):
        matrix_ids = references(move_id, matrices)
        matrix = None
        for start in sections.get(matrix_ids[0], ()) if matrix_ids else ():
            found = primitives._find_matrix(bulk, start)
            if found is not None:
                matrix = found[1]
                break
        targets = references(move_id, set(creator_of_reference))
        creators = list(dict.fromkeys(creator_of_reference[target] for target in targets))
        if matrix is not None and creators:
            data.moves[move_id] = FusionMove(move_id, matrix, creators)

    def targets_of(class_name: str):
        """Je Feature der Klasse: Abschnittsanfang und Erzeuger der genannten Körper (in Reihenfolge)."""
        guid = segment.class_guid(directory, schema, class_name)
        for feature_id, start in (segment.find_sections(bulk, directory, guid) if guid else {}).items():
            found = sketches._references(bulk, start, start + _WINDOW, set(creator_of_reference))
            yield feature_id, start, list(dict.fromkeys(creator_of_reference[r] for r in found))

    # Für jedes Feature: die Körper, auf die es verweist (in allen seinen Abschnitten)
    for feature_id in features:
        found = references(feature_id, set(creator_of_reference))
        if found:
            data.targets[feature_id] = list(dict.fromkeys(creator_of_reference[r] for r in found))

    for feature_id, start, creators in targets_of("DcCombineFeature"):
        operation = bulk[start + _COMBINE_OPERATION_OFFSET]
        if len(creators) >= 2 and operation in (0, 1, 2):
            data.combines[feature_id] = FusionCombine(feature_id, operation, creators[0], creators[1:])
    for feature_id, _start, creators in targets_of("DcDeleteBody"):
        if creators:
            data.removals[feature_id] = creators

    # Einfügen: kopierte Körper über die Verweisliste des ModelDataRef, die Kopie gehört dem DcBaseFeature
    model_refs, bases = ids("ModelDataRef"), ids("DcBaseFeature")
    for paste_id in ids("DcPasteBodies"):
        sources: list[int] = []
        for model_ref in references(paste_id, model_refs)[:1]:
            for start in sections.get(model_ref, ()):
                try:
                    listed = segment.read_reference_list(bulk, start + _MODEL_LIST_OFFSET)
                except (ValueError, struct.error):
                    continue
                sources = [creator_of_reference[r] for r in listed if r in creator_of_reference]
                if sources:
                    break
        base = references(paste_id, bases)[:1]
        if sources and base:
            data.pastes[paste_id] = FusionPaste(paste_id, list(dict.fromkeys(sources)), base[0])
            # der Abgleich mit dem gespeicherten Endstand findet die Kopie über ihren Erzeuger
            data.targets[paste_id] = [base[0]]
    return data
