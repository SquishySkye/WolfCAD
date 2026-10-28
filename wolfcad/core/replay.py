# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Übersetzt Schritte eines Fusion-Verlaufs in eigene, bearbeitbare Features.

Die Oberfläche und Prüfwerkzeuge nutzen dieselbe Zuordnung. Maße stehen in der
Datei in Zentimetern und werden hier in Millimeter umgerechnet.
"""

from __future__ import annotations

from . import features as ft

# Operation eines Fusion-Features (Zahl in der Datei) -> Bezeichnung der eigenen Operation
FUSION_OPERATIONS = {1: "Vereinigen", 2: "Ausschneiden", 3: "Schnittmenge", 4: "Neuer Körper"}
# Operation beim Kombinieren (dort beginnt die Zählung bei 0)
COMBINE_OPERATIONS = ("Vereinigen", "Ausschneiden", "Schnittmenge")


def feature_for_step(design, source: str, row: int) -> ft.Feature:
    """Erzeugt das Feature zum Schritt `row` des Verlaufs (muss übernehmbar sein).

    `row` zählt ab 0; der Name des Features enthält die Schrittnummer ab 1.
    """
    entry = design.history[row]
    feature_id, number = entry.object_id, row + 1
    common = dict(source=source, fusion_id=feature_id)

    if not design.can_rebuild(feature_id):
        # Nicht nachbaubar: der gespeicherte Endstand des Schritts wird eingesetzt
        return ft.FusionStored(name=f"Endstand{number}", fusion_kind=entry.kind, **common)
    if feature_id in design.primitives:
        return _primitive(design.primitives[feature_id], number, common)
    if feature_id in design.combines:
        operation = COMBINE_OPERATIONS[design.combines[feature_id].operation]
        return ft.FusionCombine(name=f"Kombinieren{number}", operation=operation, **common)
    if feature_id in design.removals:
        return ft.FusionRemove(name=f"Entfernen{number}", **common)
    if feature_id in design.face_features:
        face_feature = design.face_features[feature_id]
        if face_feature.kind == "delete":
            return ft.FusionDeleteFace(name=f"Flächelöschen{number}", **common)
        return ft.FusionOffsetFaces(name=f"Flächenversetzen{number}",
                                    distance=face_feature.values.get("distance", 0.1) * 10, **common)
    if feature_id in design.pastes:
        return ft.FusionPaste(name=f"Einfügen{number}", **common)
    if feature_id in design.edge_features:
        # Abrunden/Fase: Maße der (ersten) Kantengruppe, Längen in cm → mm
        values = design.edge_features[feature_id].sets[0].values
        if design.edge_features[feature_id].kind == "fillet":
            return ft.FusionFillet(name=f"Abrunden{number}", radius=values["Radius"] * 10, **common)
        first = values.get("Distance", values.get("Distance 1", 0.1))
        second = values.get("Distance 2", 0.0)
        return ft.FusionChamfer(name=f"Fase{number}", distance=first * 10, distance2=second * 10, **common)
    if feature_id in design.moves:
        matrix = design.moves[feature_id].matrix
        return ft.FusionMove(name=f"Verschieben{number}", offset_x=matrix[3] * 10,
                             offset_y=matrix[7] * 10, offset_z=matrix[11] * 10, **common)

    extrude = design.extrudes[feature_id]
    # Abstand aus dem Parameter der Datei, sonst ein Vorgabewert
    distance = next((parameter.value * 10 for parameter in entry.parameters
                     if parameter.role == "AlongDistance"), 10.0)
    return ft.FusionExtrude(name=f"Extrusion{number}", distance=distance,
                            operation=FUSION_OPERATIONS[extrude.operation], **common)


def _primitive(primitive, number: int, common: dict) -> ft.Feature:
    def value(role: str) -> float:
        return primitive.values.get(role, 0.0) * 10

    operation = FUSION_OPERATIONS[primitive.operation]
    if primitive.kind == "box":
        return ft.FusionBox(name=f"Quader{number}", length_x=value("DistanceX"),
                            length_y=value("DistanceY"), height=value("DistanceZ"),
                            offset_x=value("OffsetDistanceX"), offset_y=value("OffsetDistanceY"),
                            operation=operation, **common)
    if primitive.kind == "cylinder":
        return ft.FusionCylinder(name=f"Zylinder{number}", diameter=value("Diameter"),
                                 height=value("Height"), operation=operation, **common)
    return ft.FusionSphere(name=f"Kugel{number}", diameter=value("Diameter"),
                           operation=operation, **common)


def rebuildable_rows(design) -> list[int]:
    """Zeilen des Verlaufs, die sich als bearbeitbares Feature nachbauen lassen."""
    return [row for row, entry in enumerate(design.history) if design.can_rebuild(entry.object_id)]


def playable_rows(design, source: str) -> list[int]:
    """Zeilen, die sich abspielen lassen: nachbaubar oder mit eingelesenem gespeichertem Endstand."""
    stored = ft.stored_bodies(source)
    return [row for row, entry in enumerate(design.history)
            if design.can_rebuild(entry.object_id) or entry.object_id in stored]


def timeline_features(design, source: str) -> list[ft.Feature]:
    """Fusions ganze Timeline als eigene Features, in Fusions Reihenfolge und mit Fusions Namen.

    Je Schritt: nachbaubar → bearbeitbares Feature; Skizze → ihre Kurven; mit gespeichertem Endstand (der
    Geometrie-Blob muss dafür eingelesen sein) → Endstand; sonst ein Platzhalter ohne Wirkung, damit die
    Reihenfolge vollständig bleibt. Namen: Fusions Grundname mit fortlaufender Nummer je Grundname
    („Extrusion1“, „Extrusion2“ …) wie in Fusions Anzeige; fehlt er, der häufigste Name derselben Schrittart.
    """
    import collections

    stored = ft.stored_bodies(source)
    # Ersatz für Schritte ohne lesbaren Namen: häufigster Name derselben Art, sonst der Klassenname ohne „Dc“
    usual: dict[str, str] = {}
    by_kind: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for entry in design.history:
        if entry.object_id in design.names:
            by_kind[entry.kind][design.names[entry.object_id]] += 1
    for kind, counts in by_kind.items():
        usual[kind] = counts.most_common(1)[0][0]

    numbers: collections.Counter = collections.Counter()
    used: set[str] = set()
    features = []
    for row, entry in enumerate(design.history):
        feature_id = entry.object_id
        common = dict(source=source, fusion_id=feature_id)
        if design.can_rebuild(feature_id):
            feature = feature_for_step(design, source, row)
        elif entry.kind == "DcSketch" and feature_id in design.sketches:
            feature = ft.FusionSketchStep(**common)
        elif feature_id in stored:
            feature = ft.FusionStored(fusion_kind=entry.kind, **common)
        else:
            feature = ft.FusionPlaceholder(fusion_kind=entry.kind, **common)
        base = design.names.get(feature_id) or usual.get(entry.kind) or entry.kind.removeprefix("Dc")
        numbers[base] += 1
        name = f"{base}{numbers[base]}"
        while name in used:                 # vom Nutzer vergebene Namen können mit gezählten zusammenfallen
            numbers[base] += 1
            name = f"{base}{numbers[base]}"
        used.add(name)
        feature.name = name
        features.append(feature)
    return features
