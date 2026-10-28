# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Flächenverweise eines Fusion-Entwurfs: Fusions Benennung von Flächen lesen und die gemeinte Fläche finden.

Fusion (bzw. sein Geometriekern ASM) gibt jeder Fläche einen Namen und hängt ihn in der Geometriedatei (.smb)
als Attribut `generic_tag_attrib_def` an die Fläche. Ein Name besteht aus einer oder mehreren Gruppen; jede Gruppe
ist ein kurzer Kennungstext (z. B. „22“, vermutlich die Kennnummer der erzeugenden Skizzenkurve) und eine Liste von
Operationsnummern (z. B. 6854, −6978: die Schritte, die die Fläche erzeugt bzw. verändert haben).

Ein Flächenverweis im Entwurf (BrepRef → ASMRecipe) nennt diesen Namen und zur Unterscheidung gleichnamiger
Flächen die Namen der Nachbarflächen. Aufbau des ASMRecipe-Abschnitts (ab Byte 6, alles 4-Byte-Zahlen):
  Kopf: zwei Zahlen (1, 3), Anzahl der Einträge
  Eintrag: Anzahl der Gruppen – der erste Eintrag ist die gemeinte Fläche, die weiteren ihre Nachbarn
  Gruppe: Textlänge, Text (ASCII), Kennzahl, Anzahl der Operationen, Operationen, Endmarke
Dahinter folgen Angaben zur Topologie, die hier nicht gebraucht werden.

Zuordnung (an echten Daten ermittelt, Datei „_eye“): Für 656 + 434 der Flächenverweise von „Flächen versetzen“
bzw. „Fläche löschen“ gibt es in der Geometriedatei genau eine Fläche dieses Namens, für 181 + 221 weitere
entscheiden die Nachbarn; die übrigen sind deckungsgleiche Kopien (eingefügte Körper) – dort entscheidet die Lage
im eigenen nachgebauten Körper.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import segment, sketches
from .segment import Directory

_WINDOW = 600                   # Suchbereich für Verweise ab einem Abschnittsanfang
_RECIPE_OFFSET = 6              # Beginn der Zahlen im Abschnitt von ASMRecipe
_RECIPE_LIMIT = 20000           # so weit wird ein Rezept höchstens gelesen
_LIST_OFFSET = 6                # Verweisliste bzw. Attributtabelle im Abschnitt von ModelDataRef

# Ein Name: Gruppen aus (Kennungstext, Operationsnummern)
Name = tuple[tuple[str, tuple[int, ...]], ...]


@dataclass
class FaceReference:
    """Verweis auf eine Fläche: ihr Name und die Namen ihrer Nachbarn."""
    name: Name
    neighbours: tuple[Name, ...] = ()


@dataclass
class FaceSet:
    bodies: list[int] = field(default_factory=list)            # DcBody-IDs
    faces: list[FaceReference] = field(default_factory=list)
    unreadable: int = 0


@dataclass
class FusionFaceFeature:
    feature_id: int
    kind: str                   # "delete" oder "offset"
    sets: list[FaceSet] = field(default_factory=list)
    values: dict[str, float] = field(default_factory=dict)     # Rolle des Parameters -> Wert (cm)

    @property
    def complete(self) -> bool:
        return bool(self.sets) and all(s.faces and s.bodies and not s.unreadable for s in self.sets)

    def names(self) -> set:
        """Alle genannten Flächennamen (für das gezielte Merken beim Einlesen der Geometrie)."""
        return {reference.name for face_set in self.sets for reference in face_set.faces}


def read_recipe(bulk: bytes, start: int) -> list[Name]:
    """Die Einträge eines Benennungsrezepts (erster: die gemeinte Fläche, dann ihre Nachbarn); leer, wenn das
    Rezept nicht diesem Aufbau folgt."""
    data = bulk[start:start + _RECIPE_LIMIT]
    position = _RECIPE_OFFSET

    def number() -> int:
        nonlocal position
        (value,) = struct.unpack_from("<i", data, position)
        position += 4
        return value

    try:
        number()
        number()
        count = number()
        if not 0 < count < 500:
            return []
        entries: list[Name] = []
        for _entry in range(count):
            groups = number()
            if not 0 < groups < 200:
                return []
            name = []
            for _group in range(groups):
                length = number()
                if not 0 < length < 40:
                    return []
                text = data[position:position + length].decode("ascii")
                position += length
                number()                                    # Kennzahl (bisher immer 0)
                operations = number()
                if not 0 <= operations < 2000:
                    return []
                name.append((text, tuple(number() for _ in range(operations))))
                number()                                    # Endmarke
            entries.append(tuple(name))
        return entries
    except (struct.error, UnicodeDecodeError):
        return []


def attribute_name(fields: list) -> Name | None:
    """Der Name aus den Feldern eines `generic_tag_attrib_def`-Attributs der Geometriedatei.

    Die Felder lauten hinter dem Attributnamen: Anzahl der Gruppen, dann je Gruppe Textlänge, Text, Kennzahl,
    Anzahl der Operationen, Operationen, Endmarke (wie im Rezept)."""
    marker = "generic_tag_attrib_def "
    if marker not in fields:
        return None
    position = fields.index(marker) + 1
    try:
        count = fields[position]
        position += 1
        name = []
        for _group in range(count):
            position += 1                                   # Textlänge
            text = fields[position]
            operations = fields[position + 2]
            position += 3
            name.append((str(text), tuple(int(value) for value in fields[position:position + operations])))
            position += operations + 1
        return tuple(name)
    except (IndexError, TypeError, ValueError):
        return None


def model_ref_list(bulk: bytes, sections: dict[int, list[int]], model_ref: int) -> list[int]:
    """Die Verweisliste eines ModelDataRef. Sie steht ab Byte 6 – oder, wenn Byte 5 eine Attributtabelle ankündigt
    (z. B. `DcFeatureOperationIdFlag`), hinter dieser Tabelle."""
    for start in sections.get(model_ref, ()):
        try:
            position = start + _LIST_OFFSET
            if bulk[start + 5] == 1:
                _attributes, position = sketches._attributes(bulk, start)
            listed = segment.read_reference_list(bulk, position)
            if listed:
                return listed
        except (ValueError, struct.error, IndexError):
            continue
    return []


class _FaceReader:
    """Liest Flächengruppen eines Features: seine ModelDataRef-Verweise → BrepRef → Körper (DcBody) und Name
    (ASMRecipe). Gemeinsam für „Fläche löschen/versetzen“ und Extrusionen von Körperflächen (Runde 82)."""

    def __init__(self, bulk: bytes, directory: Directory, sections: dict[int, list[int]], schema: dict[str, dict]):
        self.bulk, self.directory, self.sections, self.schema = bulk, directory, sections, schema
        self.model_refs, self.brep_refs = self.ids("ModelDataRef"), self.ids("BrepRef")
        self.bodies, self.recipes = self.ids("DcBody"), self.ids("ASMRecipe")

    def ids(self, name: str) -> set[int]:
        return set(sketches._ids_of(self.directory, self.schema, name))

    def references(self, object_id: int, targets: set[int]) -> list[int]:
        found: list[int] = []
        for start in self.sections.get(object_id, ()):
            found.extend(sketches._references(self.bulk, start, start + _WINDOW, targets))
        return list(dict.fromkeys(found))

    def face_sets(self, feature_id: int) -> list[FaceSet]:
        """Je ModelDataRef des Features, der Flächen nennt, eine Gruppe aus Körpern und Flächenverweisen."""
        sets = []
        for model_ref in self.references(feature_id, self.model_refs):
            face_set = FaceSet()
            listed = model_ref_list(self.bulk, self.sections, model_ref)
            for brep in (r for r in listed if r in self.brep_refs):
                for body in self.references(brep, self.bodies)[:1]:
                    if body not in face_set.bodies:
                        face_set.bodies.append(body)
                entries = []
                for recipe in self.references(brep, self.recipes)[:1]:
                    for start in self.sections.get(recipe, ())[:1]:
                        entries = read_recipe(self.bulk, start)
                if entries:
                    face_set.faces.append(FaceReference(entries[0], tuple(entries[1:])))
                else:
                    face_set.unreadable += 1
            if face_set.faces or face_set.unreadable:
                sets.append(face_set)
        return sets


def read_face_features(bulk: bytes, directory: Directory, sections: dict[int, list[int]],
                       schema: dict[str, dict], parameters: dict) -> dict[int, FusionFaceFeature]:
    """„Fläche löschen“ und „Flächen versetzen“: Feature-ID -> Flächen mit Körpern und Maßen.

    `parameters`: Feature-ID -> Parameter (aus segment.feature_parameters)."""
    reader = _FaceReader(bulk, directory, sections, schema)
    found: dict[int, FusionFaceFeature] = {}
    for kind, class_name in (("delete", "DcDeleteFaceFeature"), ("offset", "DcOffsetFacesFeature")):
        for feature_id in reader.ids(class_name):
            feature = FusionFaceFeature(feature_id, kind)
            for parameter in parameters.get(feature_id, ()):
                feature.values.setdefault(parameter.role, parameter.value)
            feature.sets = reader.face_sets(feature_id)
            found[feature_id] = feature
    return found


def read_extrude_faces(bulk: bytes, directory: Directory, sections: dict[int, list[int]],
                       schema: dict[str, dict], extrudes: dict) -> None:
    """Extrusionen ohne Skizzenprofil extrudieren Körperflächen (Fusion: Fläche als Profil wählen). Ihre Flächen
    stehen wie bei „Fläche löschen“ in BrepRef-Verweisen; sie kommen in `face_sets` der Extrusion.

    An der Datei „camera.f3d“ (Runde 82): 128 von 227 Extrusionen sind solche, alle 128 vollständig lesbar."""
    reader = _FaceReader(bulk, directory, sections, schema)
    for feature_id, extrude in extrudes.items():
        if not extrude.profiles:
            extrude.face_sets = reader.face_sets(feature_id)
