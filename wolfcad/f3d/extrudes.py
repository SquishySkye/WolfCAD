# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Liest die Extrusionen eines Fusion-Entwurfs und baut ihre Profile nach.

Zusammenhang der Objekte (Namen laut Schema), an echten Daten ermittelt:
  DcExtrudeFeature    Abschnitt der Klasse: bei Byte 13 die Operation (4-Byte-Zahl),
                      bei Byte 31 die Profilnormale in Weltkoordinaten (3 Doubles)
  SketchRegionRef     je gewähltem Profil eines; sein Abschnitt verweist auf das Rezept
                      und auf die Extrusion, sein Basisabschnitt auf die Skizze
  SketchRegionRecipe  nennt die Kennnummern der Skizzenkurven, die das Profil beranden:
                      17 Byte Kopf (mit Verweis auf den SketchRegionRef), zwei 4-Byte-Zahlen,
                      dann je Randzug die Anzahl und so viele Einträge zu je 40 Byte
                      (Zahl 3, Kennnummer als 8 Byte, 28 Byte Angaben zum Teilstück)

Ein Profil kann aus Teilstücken von Kurven bestehen. Deshalb werden die genannten
Kurven miteinander verschnitten und aus dem Kurvennetz die Fläche gewählt, deren
Rand alle genannten Kurven benutzt (ersatzweise die größte).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import segment, sketches
from .segment import Directory

# Operation in der Reihenfolge von Fusions Dialog
OPERATIONS = {1: "Vereinigen", 2: "Ausschneiden", 3: "Schnittmenge", 4: "Neuer Körper"}
_OPERATION_OFFSET, _NORMAL_OFFSET = 13, 31
_REGION_WINDOW = 600        # Suchfenster für Verweise im Abschnitt eines SketchRegionRef
_RECIPE_HEADER, _RECIPE_ENTRY = 25, 40   # Kopf samt zwei Zahlen; Länge eines Eintrags
_RECIPE_LIMIT = 5_000       # größte plausible Zahl von Einträgen je Randzug
_TAG_ENTRY = b"\x03\x00\x00\x00"


@dataclass
class FusionExtrude:
    feature_id: int
    operation: int                      # Schlüssel von OPERATIONS
    normal: tuple[float, float, float]  # Richtung der Extrusion in Weltkoordinaten
    sketch_id: int | None = None        # ID des DcSketch-Features der Profile
    profiles: list[frozenset[int]] = field(default_factory=list)   # Kurven-Kennnummern je Profil
    # Runde 82: ohne Skizzenprofil die extrudierten Körperflächen (faces.FaceSet), siehe faces.read_extrude_faces
    face_sets: list = field(default_factory=list)

    @property
    def faces_complete(self) -> bool:
        """Sind die Körperflächen als Profil vollständig gelesen?"""
        return bool(self.face_sets) and all(s.faces and s.bodies and not s.unreadable for s in self.face_sets)


def _recipe_tags(bulk: bytes, start: int) -> frozenset[int]:
    """Alle Kurven-Kennnummern, die das Rezept ab `start` nennt."""
    tags = set()
    position = start + _RECIPE_HEADER
    while position + 8 <= len(bulk):
        (count,) = struct.unpack_from("<I", bulk, position)
        # Ein weiterer Randzug beginnt nur, wenn direkt ein gültiger Eintrag folgt
        if not 0 < count <= _RECIPE_LIMIT or bulk[position + 4:position + 8] != _TAG_ENTRY:
            break
        position += 4
        for _ in range(count):
            if bulk[position:position + 4] != _TAG_ENTRY:
                return frozenset(tags)
            tags.add(struct.unpack_from("<Q", bulk, position + 4)[0])
            position += _RECIPE_ENTRY
    return frozenset(tags)


def read_extrudes(bulk: bytes, directory: Directory, all_sections: dict[int, list[int]],
                  schema: dict[str, dict]) -> dict[int, FusionExtrude]:
    """Alle Extrusionen des Entwurfs: Feature-ID -> Extrusion samt Profilangaben."""
    def sections(name: str) -> dict[int, int]:
        guid = segment.class_guid(directory, schema, name)
        return segment.find_sections(bulk, directory, guid) if guid else {}

    extrudes: dict[int, FusionExtrude] = {}
    for feature_id, position in sections("DcExtrudeFeature").items():
        (operation,) = struct.unpack_from("<I", bulk, position + _OPERATION_OFFSET)
        normal = struct.unpack_from("<3d", bulk, position + _NORMAL_OFFSET)
        length = sum(value * value for value in normal) ** 0.5
        if operation in OPERATIONS and 0.99 < length < 1.01:
            extrudes[feature_id] = FusionExtrude(feature_id, operation, normal)

    recipe_at = sections("SketchRegionRecipe")
    sketch_ids = set(sketches._ids_of(directory, schema, "DcSketch"))
    for region_id, position in sections("SketchRegionRef").items():
        window = position + _REGION_WINDOW
        recipe_id = next(sketches._references(bulk, position, window, set(recipe_at)), None)
        owner = next(sketches._references(bulk, position, window, set(extrudes)), None)
        if recipe_id is None or owner is None:
            continue
        # Die Skizze wird in einem der übrigen Abschnitte des Profilverweises genannt
        sketch_id = next((found for start in all_sections.get(region_id, ())
                          if (found := sketches._first_reference(bulk, start, sketch_ids)) is not None),
                         None)
        tags = _recipe_tags(bulk, recipe_at[recipe_id])
        if tags and sketch_id is not None:
            extrudes[owner].sketch_id = sketch_id
            extrudes[owner].profiles.append(tags)
    return extrudes


def profile_face(sketch: sketches.FusionSketch, tags: frozenset[int]):
    """Fläche des Profils in Skizzenkoordinaten (cm) oder None, wenn sie sich nicht bilden lässt."""
    from OCP.BOPAlgo import BOPAlgo_Tools
    from OCP.BRep import BRep_Builder
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Compound

    known = {curve.tag for curve in (*sketch.lines, *sketch.arcs, *sketch.splines)}
    used = set(tags) & known
    if not used:
        return None
    builder = BRep_Builder()
    sources = [(tag, sketches.curve_edges(sketch, {tag})) for tag in used]
    network = TopoDS_Compound()
    builder.MakeCompound(network)
    for _, edges in sources:
        for edge in edges:
            builder.Add(network, edge)

    # Kurven verschneiden, zu geschlossenen Zügen verbinden und Flächen daraus bilden
    wires, faces = TopoDS_Compound(), TopoDS_Compound()
    builder.MakeCompound(wires)
    builder.MakeCompound(faces)
    BOPAlgo_Tools.EdgesToWires_s(network, wires, False, 1e-8)
    if not BOPAlgo_Tools.WiresToFaces_s(wires, faces, 1e-8):
        return None

    def explore(shape, kind):
        explorer = TopExp_Explorer(shape, kind)
        while explorer.More():
            yield explorer.Current()
            explorer.Next()

    def tags_on_boundary(face) -> set[int]:
        """Welche Ausgangskurven liegen auf dem Rand der Fläche? (Probe am Kantenmittelpunkt)"""
        found = set()
        for item in explore(face, TopAbs_EDGE):
            curve = BRepAdaptor_Curve(TopoDS.Edge(item))
            middle = curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2)
            probe = BRepBuilderAPI_MakeVertex(middle).Vertex()
            for tag, edges in sources:
                if any(BRepExtrema_DistShapeShape(probe, edge).Value() < 1e-6 for edge in edges):
                    found.add(tag)
                    break
        return found

    def area(face) -> float:
        properties = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, properties)
        return properties.Mass()

    candidates = [TopoDS.Face(item) for item in explore(faces, TopAbs_FACE)]
    if not candidates:
        return None
    complete = [face for face in candidates if tags_on_boundary(face) == used]
    return max(complete or candidates, key=area)


def solid(extrude: FusionExtrude, sketch: sketches.FusionSketch, distance_mm: float):
    """Volumen der Extrusion in Weltkoordinaten (mm) oder None ohne brauchbares Profil."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec
    from OCP.TopoDS import TopoDS_Compound

    faces = [face for face in (profile_face(sketch, tags) for tags in extrude.profiles) if face is not None]
    if not faces or abs(distance_mm) < 1e-9:
        return None
    builder, profile = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(profile)
    for face in faces:
        builder.Add(profile, face)
    placed = BRepBuilderAPI_Transform(profile, sketches.placement(sketch), True).Shape()
    return BRepPrimAPI_MakePrism(placed, gp_Vec(*extrude.normal) * distance_mm).Shape()
