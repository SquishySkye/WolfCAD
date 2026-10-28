# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Zugriff auf den Container einer .f3d- bzw. .f3z-Datei.

Eine .f3d-Datei ist ein (unkomprimiertes) ZIP-Archiv. Pro "Asset" (Konstruktion,
Simulation, Fertigung) gibt es einen Ordner mit:
  - Breps.BlobParts/*.smb   exakte Geometrie im ASM-Binärformat
  - Design1/*.dat           Segmentdaten des parametrischen Entwurfs (Timeline); in neueren
                            Dateien heißt der Ordner FusionDesignSegmentType1
Eine .f3z-Datei ist ein ZIP, das mehrere .f3d-Dateien einer Baugruppe bündelt.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

# Ordner des Entwurfssegments im aktiven Asset; der Name hängt vom Stand der Datei ab
_DESIGN_FOLDER = r"\[Active\]/(?:Design|FusionDesignSegmentType)\d+"

# Klassennamen der Timeline-Features im Segmentstrom, z. B. "DcExtrudeFeatureMetaType"
_FEATURE_TYPE = re.compile(rb"Dc([A-Z][A-Za-z]+?)(?:Feature)?MetaType")


@dataclass(frozen=True)
class BrepBlob:
    """Ein Geometrie-Blob innerhalb des Archivs."""

    path: str
    size: int

    @property
    def label(self) -> str:
        asset = self.path.split("/")[0]
        return f"{asset} – {self.path.rsplit('.', 2)[-2][-8:]} ({self.size / 1e6:.1f} MB)"


@dataclass
class DesignData:
    """Aus den Segmentdaten gelesene Inhalte eines Entwurfs."""

    history: list = field(default_factory=list)     # Konstruktionsschritte in Erstellreihenfolge
    sketches: dict = field(default_factory=dict)    # Feature-ID -> FusionSketch
    extrudes: dict = field(default_factory=dict)    # Feature-ID -> FusionExtrude
    primitives: dict = field(default_factory=dict)  # Feature-ID -> FusionPrimitive
    bodies: object = None                           # BodyData: Verschiebungen, Kombinationen, Entfernen
    stamp_feature: dict = field(default_factory=dict)   # Zeitstempel -> Feature-ID
    edge_features: dict = field(default_factory=dict)   # Feature-ID -> FusionEdgeFeature (Abrunden, Fase)
    names: dict = field(default_factory=dict)           # Feature-ID -> Fusions Name des Schritts (Grundname)
    face_features: dict = field(default_factory=dict)   # Feature-ID -> FusionFaceFeature (Fläche löschen/versetzen)
    _paste_of_base: dict | None = field(default=None, repr=False)
    _last_use: tuple | None = field(default=None, repr=False)

    def step_of_stamp(self) -> dict[int, int]:
        """Zeitstempel eines gespeicherten Körpers -> Schrittnummer (ab 1) im Verlauf."""
        row_of = {entry.object_id: row + 1 for row, entry in enumerate(self.history)}
        return {stamp: row_of[feature] for stamp, feature in self.stamp_feature.items()
                if feature in row_of}

    def leaves_no_body(self, feature_id: int) -> bool:
        """Erzeugt der Schritt nur Hilfskörper, die im fertigen Modell nicht mehr existieren?"""
        return bool(self.bodies and self.bodies.creator_of
                    and feature_id not in self.bodies.living_creators)

    @property
    def targets(self) -> dict:
        return self.bodies.targets if self.bodies else {}

    @property
    def moves(self) -> dict:
        return self.bodies.moves if self.bodies else {}

    @property
    def combines(self) -> dict:
        return self.bodies.combines if self.bodies else {}

    @property
    def removals(self) -> dict:
        return self.bodies.removals if self.bodies else {}

    @property
    def pastes(self) -> dict:
        return self.bodies.pastes if self.bodies else {}

    def can_rebuild(self, feature_id: int) -> bool:
        """Lässt sich der Schritt als eigenes Feature nachbauen?

        Bisher: Grundkörper, Extrusionen mit Skizzenprofil sowie Verschieben, Kombinieren
        und Entfernen von Körpern, die ein übernehmbarer Schritt erzeugt hat; Abrunden und Fase,
        wenn alle Kanten lesbar sind und ihre Körper von übernehmbaren Schritten stammen.
        """
        if feature_id in self.primitives:
            return True
        if feature_id in self.edge_features:
            return self.edge_feature_supported(feature_id)
        if feature_id in self.pastes:
            return all(self.creates_body(source) for source in self.pastes[feature_id].sources)
        if feature_id in self.face_features:
            face = self.face_features[feature_id]
            if not face.complete or not self.bodies or (face.kind == "offset" and "distance" not in face.values):
                return False
            return all(self.creates_body(self.bodies.creator_of.get(body, -1))
                       for face_set in face.sets for body in face_set.bodies)
        if feature_id in self.moves:
            return any(self.creates_body(creator) for creator in self.moves[feature_id].creators)
        if feature_id in self.combines:
            combine = self.combines[feature_id]
            return all(self.creates_body(creator) for creator in (combine.target, *combine.tools))
        if feature_id in self.removals:
            return any(self.creates_body(creator) for creator in self.removals[feature_id])
        return self._extrude_rebuildable(feature_id)

    def _extrude_rebuildable(self, feature_id: int, depth: int = 0) -> bool:
        """Extrusion mit lesbarem Skizzenprofil – oder (Runde 82) mit gelesenen Körperflächen als Profil, deren
        Körper selbst von übernehmbaren Schritten stammen."""
        extrude = self.extrudes.get(feature_id)
        if extrude is None:
            return False
        if extrude.profiles:
            return extrude.sketch_id in self.sketches
        if not extrude.faces_complete or not self.bodies or depth >= 50:
            return False
        return all(self.creates_body(self.bodies.creator_of.get(body, -1), depth + 1)
                   for face_set in extrude.face_sets for body in face_set.bodies)


    def _usage(self) -> tuple[dict, dict]:
        """(Feature-ID -> Zeile im Verlauf, Erzeuger eines Körpers -> letzte Zeile, in der ein Schritt ihn nennt)."""
        if self._last_use is None:
            row = {entry.object_id: number for number, entry in enumerate(self.history)}
            last: dict[int, int] = {}

            def use(creator, feature_id):
                if creator is not None and feature_id in row:
                    last[creator] = max(last.get(creator, -1), row[feature_id])
            for feature_id, creators in self.targets.items():
                for creator in creators:
                    use(creator, feature_id)
            for feature_id, edge in [*self.edge_features.items(), *self.face_features.items()]:
                for edge_set in edge.sets:
                    for body in edge_set.bodies:
                        use(self.bodies.creator_of.get(body) if self.bodies else None, feature_id)
            for feature_id, paste in self.pastes.items():
                for source in paste.sources:
                    use(source, feature_id)
            self._last_use = (row, last)
        return self._last_use

    def used_after(self, creator: int, feature_id: int) -> bool:
        """Nennt ein späterer Schritt als `feature_id` den Körper dieses Erzeugers noch?"""
        row, last = self._usage()
        return feature_id in row and last.get(creator, -1) > row[feature_id]

    def tools_kept(self, combine_id: int) -> bool:
        """Behält das Kombinieren seine Werkzeugkörper (Fusions „Werkzeuge beibehalten“)?

        Ein Schalter dafür ist in den Daten noch nicht gefunden. Abgeleitet wird es daraus, ob ein späterer
        Schritt einen der Werkzeugkörper noch verwendet (in den Beispielen bei 319 von 362 Kombinationen)."""
        combine = self.combines.get(combine_id)
        return combine is not None and any(self.used_after(tool, combine_id) for tool in combine.tools)

    @property
    def paste_of_base(self) -> dict:
        """Eingebettetes DcBaseFeature -> Einfügen, das es erzeugt hat (gemerkt)."""
        if self._paste_of_base is None:
            self._paste_of_base = {paste.base: paste for paste in self.pastes.values()}
        return self._paste_of_base

    def link_reference_targets(self) -> None:
        """Abrunden, Fase, Fläche löschen/versetzen nennen ihre Körper nur in den Kanten- bzw. Flächenverweisen.
        Der Abgleich mit dem gespeicherten Endstand findet die Körper eines Schritts aber über `targets` – fehlen sie
        dort, bliebe beim Ersetzen der eigene Körper zusätzlich stehen (doppelt)."""
        if not self.bodies:
            return
        for feature_id, feature in [*self.edge_features.items(), *self.face_features.items()]:
            creators = [self.bodies.creator_of[body] for group in feature.sets for body in group.bodies
                        if body in self.bodies.creator_of]
            if creators and not self.bodies.targets.get(feature_id):
                self.bodies.targets[feature_id] = list(dict.fromkeys(creators))

    def wanted_face_names(self) -> set:
        """Alle Flächennamen, auf die Schritte des Entwurfs verweisen (werden beim Einlesen der Geometrie gemerkt)."""
        names = set().union(*(face.names() for face in self.face_features.values())) if self.face_features else set()
        # Runde 82: auch die Körperflächen, die Extrusionen als Profil nutzen
        for extrude in self.extrudes.values():
            names |= {reference.name for face_set in extrude.face_sets for reference in face_set.faces}
        return names

    def edge_feature_supported(self, feature_id: int) -> bool:
        """Lässt sich die Abrundung bzw. Fase nachbauen (Kanten lesbar, Maß bekannt, Körper übernehmbar)?"""
        edge = self.edge_features[feature_id]
        if not edge.complete or not self.bodies:
            return False
        for edge_set in edge.sets:
            values = edge_set.values
            if edge.kind == "fillet" and ("Radius" not in values or "StartRadius" in values):
                return False        # variable Radien sind noch nicht übernommen
            if edge.kind == "chamfer" and "Distance" not in values and "Distance 1" not in values:
                return False
            if not all(self.creates_body(self.bodies.creator_of.get(body, -1)) for body in edge_set.bodies):
                return False
        return True

    def creates_body(self, feature_id: int, depth: int = 0) -> bool:
        """Ist der Schritt ein übernehmbarer Grundkörper oder eine übernehmbare Extrusion – oder das eingebettete
        Feature einer übernehmbar eingefügten Kopie?"""
        if feature_id in self.primitives:
            return True
        paste = self.paste_of_base.get(feature_id)
        if paste is not None and depth < 50:        # Kopie einer Kopie …: begrenzt gegen Ringverweise
            return all(self.creates_body(source, depth + 1) for source in paste.sources)
        return self._extrude_rebuildable(feature_id, depth)


class F3dArchive:
    """Lesender Zugriff auf eine geöffnete .f3d-Datei."""

    def __init__(self, source):
        self._zip = zipfile.ZipFile(source)

    def close(self) -> None:
        self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def nested_designs(self) -> list[str]:
        """Enthaltene .f3d-Dateien (nur bei .f3z-Archiven nicht leer)."""
        return [n for n in self._zip.namelist() if n.lower().endswith(".f3d")]

    def open_nested(self, name: str) -> "F3dArchive":
        return F3dArchive(io.BytesIO(self._zip.read(name)))

    def brep_blobs(self) -> list[BrepBlob]:
        """Alle Geometrie-Blobs mit aktuellem Modellstand (.smb, ohne Historie)."""
        return [
            BrepBlob(info.filename, info.file_size)
            for info in self._zip.infolist()
            if "/Breps.BlobParts/" in info.filename and info.filename.endswith(".smb")
        ]

    def read(self, path: str) -> bytes:
        return self._zip.read(path)

    def _design_streams(self) -> tuple[bytes, bytes] | None:
        """Meta- und Bulk-Daten des aktiven Entwurfs (None, wenn nicht vorhanden)."""
        for name in self._zip.namelist():
            if re.search(_DESIGN_FOLDER + r"/MetaStream\.dat$", name):
                try:
                    return (self._zip.read(name),
                            self._zip.read(name.replace("MetaStream.dat", "BulkStream.dat")))
                except KeyError:
                    return None
        return None

    def design(self, schema: dict[str, dict]) -> "DesignData":
        """Liest Verlauf, Skizzen und Extrusionen des Entwurfs in einem Durchgang."""
        from . import bodies, edges, extrudes, faces, primitives, segment, sketches

        streams = self._design_streams()
        if streams is None or not schema:
            return DesignData()
        meta, bulk = streams
        try:
            directory = segment.parse_directory(meta)
            sections = segment.index_sections(bulk, directory)
            history = segment.feature_history(meta, bulk, schema, sections)
            parameters = segment.feature_parameters(bulk, directory, sections, schema)
            data = DesignData(
                history=history,
                names=segment.feature_names(bulk, sections, history),
                sketches=sketches.read_sketches(bulk, directory, sections, schema),
                extrudes=extrudes.read_extrudes(bulk, directory, sections, schema),
                primitives=primitives.read_primitives(bulk, directory, schema, parameters),
                bodies=bodies.read_bodies(bulk, directory, sections, schema),
                stamp_feature=bodies.feature_timestamps(bulk, directory, schema),
                edge_features=edges.read_edge_features(bulk, directory, sections, schema),
                face_features=faces.read_face_features(bulk, directory, sections, schema, parameters),
            )
            faces.read_extrude_faces(bulk, directory, sections, schema, data.extrudes)
            data.link_reference_targets()
            return data
        except ValueError:
            return DesignData()

    def sketches(self, schema: dict[str, dict]) -> dict:
        """Skizzen des Entwurfs: ID des Skizzen-Features -> FusionSketch (siehe sketches.py)."""
        from . import segment, sketches

        streams = self._design_streams()
        if streams is None or not schema:
            return {}
        meta, bulk = streams
        try:
            directory = segment.parse_directory(meta)
            return sketches.read_sketches(bulk, directory, segment.index_sections(bulk, directory), schema)
        except ValueError:
            return {}

    def feature_history(self, schema: dict[str, dict]) -> list:
        """Konstruktionsschritte des Entwurfs in Erstellreihenfolge (siehe segment.py).

        Ausgewertet wird das Segment des aktiven Entwurfs; `schema` stammt aus
        schema.py. Fehlt etwas davon, ist die Liste leer.
        """
        from . import segment

        for name in self._zip.namelist():
            if re.search(_DESIGN_FOLDER + r"/MetaStream\.dat$", name):
                bulk_name = name.replace("MetaStream.dat", "BulkStream.dat")
                try:
                    return segment.feature_history(self._zip.read(name), self._zip.read(bulk_name), schema)
                except (KeyError, ValueError):
                    return []
        return []

    def feature_types(self) -> list[str]:
        """Im Entwurf vorkommende Feature-Typen (alphabetisch, ohne Duplikate).

        Die Segmentdaten sind noch nicht vollständig entschlüsselt; ausgelesen
        werden bisher nur die Typnamen, nicht Reihenfolge oder Parameter.
        """
        found: set[str] = set()
        for name in self._zip.namelist():
            if re.search(_DESIGN_FOLDER + r"/BulkStream\.dat$", name):
                found.update(m.decode() for m in _FEATURE_TYPE.findall(self._zip.read(name)))
        return sorted(found)
