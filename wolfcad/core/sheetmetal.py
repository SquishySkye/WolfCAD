# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Blech wie im Arbeitsbereich „Blech“ von Fusion: Flansch aus einem Skizzenprofil, Flansch an einer Kante
(mit Biegung) und die Abwicklung (flaches Zuschnittteil).

Ein Blechkörper merkt sich in `BuildState.sheets` seine Blechregel (Dicke, Biegeradius, K-Faktor) und, aus
welchen ebenen Platten und Biegungen er besteht. Die Abwicklung entsteht nicht durch nachträgliches Aufbiegen
eines beliebigen Körpers, sondern aus diesem Aufbau:

- jede ebene Platte (Grundflansch, gerader Teil eines Kantenflanschs) ist ein Stück des gefalteten Körpers mit
  einer Lage-Umrechnung „gefaltet → flach“;
- jede Biegung wird flach zu einem Streifen der Breite der Biegezugabe BA = Winkel · (R + K · T)
  (R innerer Biegeradius, T Dicke, K Lage der neutralen Faser).

Der gerade Teil eines Kantenflanschs wird für die Abwicklung um die Biegeachse zurückgedreht und um die
Biegezugabe nach außen geschoben; ein Flansch an einem Flansch übernimmt zusätzlich die Umrechnung seiner
Platte. Spätere Änderungen mit anderen Werkzeugen (Bohrung, Ausschnitt …) kennt die Abwicklung nicht.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex, BRepBuilderAPI_Transform
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.Bnd import Bnd_Box
from OCP.GeomAbs import GeomAbs_Line
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.collections import IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as _EdgeFaceMap
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from . import features as ft
from .analysis import face_normal
from .features import ALL_BODIES, BuildState, Feature, FeatureError

# Fusion-Vorgabe für Stahlblech: K-Faktor 0,44 (die neutrale Faser liegt bei 44 % der Dicke von innen)
DEFAULT_K_FACTOR = 0.44
# Ecke zwischen Flanschen an benachbarten Kanten (Fusion: Ecktyp): offen lassen oder beide Flansche bis in die Ecke
# verlängern und auf Gehrung mit kleinem Spalt schneiden
CORNERS = ("Offen", "Geschlossen")
_FLAT_GAP = 20.0        # Abstand der Abwicklung neben dem gefalteten Teil (mm)


def bend_allowance(angle: float, radius: float, thickness: float, k_factor: float) -> float:
    """Biegezugabe: Länge der neutralen Faser in einer Biegung (Winkel in Grad, innerer Radius R, Dicke T)."""
    return math.radians(angle) * (radius + k_factor * thickness)


def _fused(shapes: list):
    """Vereinigt die Formen und fasst gleiche Ebenen zusammen (saubere Flächen ohne Nahtkanten)."""
    result = shapes[0]
    for shape in shapes[1:]:
        result = ft._boolean(BRepAlgoAPI_Fuse, result, shape)
    merged = ShapeUpgrade_UnifySameDomain(result, True, True, False)
    merged.Build()
    return merged.Shape()


def _moved(shape, transform: gp_Trsf):
    return BRepBuilderAPI_Transform(shape, transform, True).Shape()


def _new_sheet(thickness: float, radius: float, k_factor: float, plate, frame) -> dict:
    """Blechbeschreibung eines Körpers: Regel, Ebene des Grundflanschs (Ursprung, Normale, x-Richtung) und Aufbau
    (Platten und Biegestreifen mit Lage-Umrechnung, Biegelinien für den Zuschnitt)."""
    return {"thickness": thickness, "radius": radius, "k_factor": k_factor, "frame": frame,
            "plates": [(plate, gp_Trsf())], "strips": [], "bends": []}


@dataclass
class BaseFlange(Feature):
    """Flansch aus einem Skizzenprofil (Fusion: Flansch auf ein Profil): das Profil wird um die Blechdicke
    aufgezogen und der neue Körper als Blechteil mit seiner Blechregel geführt."""

    sketch: str = ""
    thickness: float = 1.0          # Blechdicke T
    bend_radius: float = 1.0        # innerer Biegeradius R für die Flansche dieses Teils
    k_factor: float = DEFAULT_K_FACTOR
    flip: bool = False              # Dicke zur anderen Seite der Skizzenebene

    KIND = "Flansch"

    def apply(self, state: BuildState) -> None:
        if self.thickness <= 0:
            raise FeatureError("Die Blechdicke muss größer als 0 sein")
        if self.bend_radius < 0:
            raise FeatureError("Der Biegeradius darf nicht negativ sein")
        if not 0.0 <= self.k_factor <= 1.0:
            raise FeatureError("Der K-Faktor liegt zwischen 0 und 1")
        profile = state.solid_profiles.get(self.sketch) or state.profiles.get(self.sketch)
        if profile is None:
            raise FeatureError(f"Die Skizze '{self.sketch}' hat kein geschlossenes Profil")
        normal = gp_Vec(*state.normals[self.sketch])
        plate = BRepPrimAPI_MakePrism(profile, normal * (-self.thickness if self.flip else self.thickness)).Shape()
        name = state.add_body(plate)
        axes = state.frames[self.sketch]
        frame = tuple((v.X(), v.Y(), v.Z()) for v in (axes.Location(), axes.Direction(), axes.XDirection()))
        state.sheets[name] = {**_new_sheet(self.thickness, self.bend_radius, self.k_factor, plate, frame),
                              "folded": plate}


def _edge_faces(body, edge) -> list:
    """Die (zwei) Flächen des Körpers an einer Kante."""
    found = _EdgeFaceMap()
    TopExp.MapShapesAndAncestors_s(body, TopAbs_EDGE, TopAbs_FACE, found)
    faces = []
    for face in found.FindFromKey(edge):
        if not any(face.IsSame(other) for other in faces):
            faces.append(TopoDS.Face(face))
    return faces


def _strip_face(start: gp_Pnt, direction: gp_Vec, length: float, before: float, after: float, depth: gp_Vec):
    """Rechteckige Stirnfläche eines Teil-Flanschs: längs der Kante von `before` bis `length − after`, quer über
    die Blechdicke (`depth` zeigt von der Kante durch das Blech)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    a = start.Translated(direction * before)
    b = start.Translated(direction * (length - after))
    polygon = BRepBuilderAPI_MakePolygon(a, b, b.Translated(depth), a.Translated(depth), True)
    return BRepBuilderAPI_MakeFace(polygon.Wire(), True).Face()


def _inside(body, point: gp_Pnt) -> bool:
    """Liegt der Punkt im Inneren des Körpers?"""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    classifier = BRepClass3d_SolidClassifier(body, point, 1e-7)
    return classifier.State() == TopAbs_IN


def _distance(shape, point: gp_Pnt) -> float:
    measure = BRepExtrema_DistShapeShape(shape, BRepBuilderAPI_MakeVertex(point).Vertex())
    return measure.Value() if measure.IsDone() else math.inf


@dataclass
class EdgeFlange(Feature):
    """Flansch an einer geraden Kante eines Blechteils (Fusion: Flansch an Kante): an der Kante entsteht eine
    Biegung mit dem Biegeradius der Blechregel, daran ein gerader Teil der angegebenen Länge.

    Die Biegung geht zur Seite der Fläche, an der die Kante gewählt wurde (Umkehren: zur anderen Seite).
    """

    body: str = ALL_BODIES
    edge_points: list = field(default_factory=list)     # Merkpunkte der gewählten Kanten
    length: float = 10.0            # gerader Teil hinter der Biegung
    angle: float = 90.0             # Biegewinkel in Grad
    bend_radius: float = 0.0        # 0 = Biegeradius der Blechregel des Teils
    flip: bool = False              # zur anderen Seite biegen
    # Teillänge wie Fusions Ausdehnung „Versatz“: so weit bleibt der Flansch vom Anfang bzw. Ende der Kante weg
    offset_start: float = 0.0
    offset_end: float = 0.0
    # Biegefreistellung wie Fusion: bei Teillänge an jedem Biegungsende ein Schlitz im Blech (Breite = Dicke,
    # Tiefe = Radius + Dicke), damit das Blech dort beim Biegen nicht einreißt
    relief: bool = True
    corner: str = CORNERS[0]        # Ecke zu einem Flansch an der Nachbarkante: offen oder geschlossen
    corner_gap: float = 0.5         # Spalt an der geschlossenen Ecke (mm)

    KIND = "Flansch an Kante"

    def apply(self, state: BuildState) -> None:
        if not self.edge_points:
            raise FeatureError("Bitte eine gerade Kante eines Blechteils wählen")
        if self.length < 0:
            raise FeatureError("Die Flanschlänge darf nicht negativ sein")
        if not 0.0 < self.angle <= 180.0:
            raise FeatureError("Der Biegewinkel liegt zwischen 0° und 180°")
        if self.offset_start < 0 or self.offset_end < 0:
            raise FeatureError("Die Abstände zu den Kantenenden dürfen nicht negativ sein")
        names = [name for name in ft.select_bodies(state, self.body) if name in state.sheets]
        if not names:
            raise FeatureError("Der gewählte Körper ist kein Blechteil (erst einen Flansch aus einer Skizze anlegen)")
        for point in self.edge_points:
            for name in names:
                edges = ft.unique_edges(state.bodies[name])
                if min(math.dist(ft.edge_point(edge), point) for edge in edges) <= ft.SELECTION_TOLERANCE:
                    self._flange(state, name, point)
                    break
            else:
                raise FeatureError("Eine gewählte Kante ist im Blechteil nicht mehr vorhanden")

    def _flange(self, state: BuildState, name: str, point: list) -> None:
        body, sheet = state.bodies[name], state.sheets[name]
        thickness, k_factor = sheet["thickness"], sheet["k_factor"]
        radius = self.bend_radius if self.bend_radius > 0 else sheet["radius"]
        edge, = ft.find_by_points(ft.unique_edges(body), ft.edge_point, [point], "Kante")
        curve = BRepAdaptor_Curve(edge)
        if curve.GetType() != GeomAbs_Line:
            raise FeatureError("Ein Flansch braucht eine gerade Kante")
        start, end = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        direction = gp_Vec(start, end)
        direction.Normalize()
        faces = _edge_faces(body, edge)
        if len(faces) != 2:
            raise FeatureError("Die Kante gehört nicht zu genau zwei Flächen")
        # Welche der beiden Flächen ist die Blechfläche (oben/unten), welche die Stirnfläche? Von der Kante eine
        # Blechdicke gegen die Normale der Blechfläche gegangen, ist man knapp jenseits der Gegenseite – also
        # außerhalb des Körpers; bei der Stirnfläche dagegen noch im Blech. (Nach dem Verschmelzen gleicher Ebenen
        # kann die Stirnfläche größer sein als die Blechdicke – ihre Breite taugt deshalb nicht als Merkmal.)
        middle = curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2)
        normals = [gp_Vec(*face_normal(face)) for face in faces]
        if abs(normals[0].Dot(normals[1])) > 1e-6:
            raise FeatureError("Die Flächen an dieser Kante stehen nicht rechtwinklig (keine Blechkante)")
        # Prüfpunkte ein Stück (0,05 mm) von der jeweils anderen Fläche nach innen gerückt – genau auf der
        # Oberfläche wäre ein Punkt weder innen noch außen
        nudge = 0.05
        beyond = [not _inside(body, middle.Translated(normals[1 - i] * -nudge + normals[i] * -(thickness + 0.01)))
                  for i in (0, 1)]
        if beyond[0] == beyond[1]:
            raise FeatureError("Die Kante liegt nicht am Rand des Blechs (keine Stirnfläche der Blechdicke)")
        up, outward = (normals[0], normals[1]) if beyond[0] else (normals[1], normals[0])
        if not _inside(body, middle.Translated(outward * -nudge + up * -(thickness - 0.01))):
            raise FeatureError("Das Blech ist an dieser Kante nicht so dick wie in der Blechregel")
        bend_up = up if not self.flip else up.Reversed()
        # Gebogen wird das Rechteck der Stirnfläche über die Kante (bei Teillänge zwischen den beiden Abständen)
        length = start.Distance(end)
        if self.offset_start + self.offset_end >= length - 1e-6:
            raise FeatureError("Die Abstände sind zusammen so lang wie die Kante – vom Flansch bliebe nichts")
        start = start.Translated(direction * self.offset_start)
        end = end.Translated(direction * -self.offset_end)
        edge_start, through = gp_Pnt(start.XYZ()), up * -thickness     # quer durch die Blechdicke
        # Geschlossene Ecke: liegt an einem Ende (ohne Abstand) schon ein Flansch der Nachbarkante, werden beide
        # um Radius + Dicke in die Ecke verlängert und dort auf Gehrung geschnitten
        corners = []
        if self.corner == CORNERS[1]:
            if self.corner_gap < 0:
                raise FeatureError("Der Spalt an der Ecke darf nicht negativ sein")
            for at, along, room in ((start, -1.0, self.offset_start), (end, 1.0, self.offset_end)):
                if room > 1e-9:
                    continue
                for other in sheet.get("flanges", []):
                    touches = min(at.Distance(gp_Pnt(*other["start"])), at.Distance(gp_Pnt(*other["end"]))) < 1e-6
                    if touches and abs(gp_Vec(*other["outward"]).Dot(outward)) < 1e-6:
                        corners.append((gp_Pnt(at.XYZ()), along, other))
                        break
        reach = radius + thickness
        before = -reach if any(along < 0 for _at, along, _other in corners) else 0.0
        after = -reach if any(along > 0 for _at, along, _other in corners) else 0.0
        side = _strip_face(edge_start, direction, start.Distance(end), before, after, through)
        # Biegeachse parallel zur Kante: innerer Radius R auf der Seite, zu der gebogen wird
        inner = gp_Vec(start.XYZ()) if not self.flip else gp_Vec(start.XYZ()) - up * thickness
        axis_point = gp_Pnt((inner + bend_up * radius).XYZ())
        turn = outward.Crossed(bend_up)         # so dreht die Stirnfläche von „außen“ zu „oben“
        axis = gp_Ax1(axis_point, gp_Dir(turn))
        angle = math.radians(self.angle)
        revolve = BRepPrimAPI_MakeRevol(side, axis, angle)
        revolve.Build()
        if not revolve.IsDone():
            raise FeatureError("Die Biegung ließ sich nicht bilden")
        bend = revolve.Shape()
        lead = outward * math.cos(angle) + bend_up * math.sin(angle)     # Richtung des geraden Teils
        plate = BRepPrimAPI_MakePrism(revolve.LastShape(), lead * self.length).Shape() if self.length > 1e-9 else None
        corner_pieces = []          # Verlängerungen der Nachbarflansche: (Biegung, gerader Teil, dessen Umrechnung)
        corner_strips = []          # … und ihre Biegestreifen für den Zuschnitt: (Streifen, Umrechnung)
        mitres = []                 # Halbräume, die dieser Flansch an den Ecken behält
        for at, _along, other in corners:
            mitre = outward - gp_Vec(*other["outward"])
            mitre.Normalize()
            # dieser Flansch behält die Seite zu sich hin, der Nachbar die andere – dazwischen der Spalt
            mine = _halfspace(at.Translated(mitre * (self.corner_gap / 2)), mitre)
            theirs = _halfspace(at.Translated(mitre * -(self.corner_gap / 2)), mitre.Reversed())
            mitres.append(mine)
            bend = _common(bend, mine)
            if plate is not None:
                plate = _common(plate, mine)
            # Nachbarflansch um dasselbe Stück in die Ecke verlängern (gleiche Achse, gleicher Winkel)
            outward_dir = gp_Vec(*other["direction"]) * (1.0 if at.Distance(gp_Pnt(*other["end"])) < 1e-6 else -1.0)
            extension = _strip_face(at, outward_dir, reach, 0.0, 0.0, gp_Vec(*other["depth"]))
            grow = BRepPrimAPI_MakeRevol(extension, gp_Ax1(gp_Pnt(*other["axis_point"]), gp_Dir(*other["axis_dir"])),
                                         other["angle"])
            grow.Build()
            if not grow.IsDone():
                raise FeatureError("Die Ecke ließ sich nicht schließen")
            their_plate = None
            if other["length"] > 1e-9:
                their_plate = _common(BRepPrimAPI_MakePrism(grow.LastShape(), gp_Vec(*other["lead"]) * other["length"]).Shape(),
                                      theirs)
            corner_pieces.append((_common(grow.Shape(), theirs), their_plate, other["plate_trsf"]))
            # Zuschnitt: der Biegestreifen der Verlängerung liegt flach in der Platte, an der der Nachbarflansch
            # hängt – dort gilt dieselbe Gehrung (beide Flansche hängen an derselben Platte)
            if other.get("allowance", 0.0) > 1e-9:
                their_strip = BRepPrimAPI_MakePrism(extension, gp_Vec(*other["outward"]) * other["allowance"]).Shape()
                corner_strips.append((_common(their_strip, theirs), other["parent_trsf"]))
        reliefs = []
        if self.relief:
            for at, along, room in ((start, -1.0, self.offset_start), (end, 1.0, self.offset_end)):
                if room <= 1e-9:
                    continue                    # Flansch reicht bis ans Kantenende: dort keine Freistellung
                width, depth = min(thickness, room), radius + thickness
                a, b = at, at.Translated(direction * (along * width))
                c, d = b.Translated(outward * -depth), a.Translated(outward * -depth)
                from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
                outline = BRepBuilderAPI_MakePolygon(a, b, c, d, True).Wire()
                # von der Blechfläche, an der die Kante liegt, durch die ganze Dicke
                reliefs.append(BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(outline, True).Face(),
                                                     up * -thickness).Shape())
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        for cut in reliefs:
            body = ft._boolean(BRepAlgoAPI_Cut, body, cut)
        pieces = [body, bend] + ([plate] if plate is not None else [])
        for their_bend, their_plate, _place in corner_pieces:
            pieces += [their_bend] + ([their_plate] if their_plate is not None else [])
        state.bodies[name] = _fused(pieces)

        # Abwicklung: auf welcher Platte liegt die Kante? Deren Umrechnung gilt auch für Biegung und Flansch
        middle = gp_Pnt(*point)
        base_plate, parent = min(sheet["plates"], key=lambda item: _distance(item[0], middle))
        allowance = bend_allowance(self.angle, radius, thickness, k_factor)
        strip = BRepPrimAPI_MakePrism(side, outward * allowance).Shape() if allowance > 1e-9 else None
        for mine in mitres:                 # der eigene Streifen endet an der Ecke ebenfalls auf Gehrung
            if strip is not None:
                strip = _common(strip, mine)
        back = gp_Trsf()
        back.SetRotation(axis, -angle)          # den geraden Teil um die Biegeachse zurückdrehen …
        shift = gp_Trsf()
        shift.SetTranslation(outward * allowance)   # … und um die Biegezugabe nach außen schieben
        local = shift.Multiplied(back)
        plates = list(sheet["plates"])
        if reliefs:
            # die Freistellung gehört in die Platte, damit sie auch in Abwicklung und DXF erscheint
            number = next(i for i, item in enumerate(plates) if item[0] is base_plate)
            trimmed = base_plate
            for cut in reliefs:
                trimmed = ft._boolean(BRepAlgoAPI_Cut, trimmed, cut)
            plates[number] = (trimmed, parent)
        if plate is not None:
            plates.append((plate, parent.Multiplied(local)))
        for _their_bend, their_plate, place in corner_pieces:
            if their_plate is not None:
                plates.append((their_plate, place))     # Verlängerung des Nachbarflanschs auch im Zuschnitt
        strips = list(sheet["strips"]) + ([(strip, parent)] if strip is not None else []) + corner_strips
        # Lage dieses Flanschs für spätere Ecken (ein Flansch an der Nachbarkante kann ihn verlängern)
        as_tuple = lambda v: (v.X(), v.Y(), v.Z())
        flanges = list(sheet.get("flanges", [])) + [{
            "start": as_tuple(start), "end": as_tuple(end), "direction": as_tuple(direction),
            "outward": as_tuple(outward), "depth": as_tuple(through), "axis_point": as_tuple(axis_point),
            "axis_dir": as_tuple(turn), "angle": angle, "lead": as_tuple(lead), "length": self.length,
            "plate_trsf": parent.Multiplied(local), "parent_trsf": parent, "allowance": allowance}]
        # Biegelinie für den Zuschnitt: Mitte des Biegestreifens, in der Lage der Platte davor
        middle_shift = outward * (allowance / 2)
        line = tuple((p.X(), p.Y(), p.Z()) for p in (start.Translated(middle_shift), end.Translated(middle_shift)))
        bends = list(sheet.get("bends", [])) + [(line, parent, self.angle)]
        # „folded“: der Körper, wie ihn die Blech-Werkzeuge gebaut haben – was andere Werkzeuge später daran
        # wegnehmen (Bohrung, Ausschnitt), findet die Abwicklung als Unterschied dazu
        state.sheets[name] = {**sheet, "plates": plates, "strips": strips, "bends": bends, "flanges": flanges,
                              "folded": state.bodies[name]}


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def flat_pattern(sheet: dict, current=None):
    """Die Abwicklung eines Blechteils aus seinem Aufbau (alle Platten und Biegestreifen flach gelegt).

    Mit `current` (dem Körper, wie er jetzt ist) fehlt in der Abwicklung auch, was andere Werkzeuge später
    weggenommen haben (Bohrungen, Ausschnitte): der Unterschied zum gefalteten Blech wird je ebener Platte mit
    deren Lage-Umrechnung flach gelegt und abgezogen. In einer Biegung Weggenommenes bleibt unberücksichtigt
    (es läge dort gekrümmt).
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
    pieces = [_moved(shape, transform) for shape, transform in sheet["plates"] + sheet["strips"]]
    flat = _fused(pieces)
    folded = sheet.get("folded")
    if current is None or folded is None or current.IsSame(folded):
        return flat
    removed = ft._boolean(BRepAlgoAPI_Cut, folded, current)
    if _volume(removed) < 1e-9:
        return flat
    for plate, transform in sheet["plates"]:
        piece = ft._boolean(BRepAlgoAPI_Common, removed, plate)
        if _volume(piece) > 1e-9:
            flat = ft._boolean(BRepAlgoAPI_Cut, flat, _moved(piece, transform))
    merged = ShapeUpgrade_UnifySameDomain(flat, True, True, False)
    merged.Build()
    return merged.Shape()


def _dxf_entities(shape, origin, x_axis, y_axis, normal, layer: str) -> list[str]:
    """Die Kanten der größten Fläche der flachen Form, die quer zur Normale liegt, als DXF-Einträge in den
    Koordinaten der Ebene (Linien, Kreise, Bögen; andere Kurven als kurze Linienstücke)."""
    import numpy as np
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.GeomAbs import GeomAbs_Circle

    def area(face) -> float:
        properties = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, properties)
        return properties.Mass()

    flat_faces = [face for face in ft.faces_of(shape)
                  if abs(abs(float(np.dot(face_normal(face), normal))) - 1.0) < 1e-6]
    if not flat_faces:
        raise FeatureError("Die Abwicklung hat keine ebene Seite in der Blechebene")
    face = max(flat_faces, key=area)
    local = lambda p: (float(np.dot(np.array([p.X(), p.Y(), p.Z()]) - origin, x_axis)),
                       float(np.dot(np.array([p.X(), p.Y(), p.Z()]) - origin, y_axis)))
    entries = []

    def line(a, b) -> None:
        entries.append(f"0\nLINE\n8\n{layer}\n10\n{a[0]:.6f}\n20\n{a[1]:.6f}\n30\n0.0\n"
                       f"11\n{b[0]:.6f}\n21\n{b[1]:.6f}\n31\n0.0\n")

    for edge in ft.unique_edges(face):
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        if curve.GetType() == GeomAbs_Line:
            line(local(curve.Value(first)), local(curve.Value(last)))
        elif curve.GetType() == GeomAbs_Circle:
            circle = curve.Circle()
            centre, radius = local(circle.Location()), circle.Radius()
            if abs((last - first) - 2 * math.pi) < 1e-9:
                entries.append(f"0\nCIRCLE\n8\n{layer}\n10\n{centre[0]:.6f}\n20\n{centre[1]:.6f}\n30\n0.0\n"
                               f"40\n{radius:.6f}\n")
                continue
            # Bogen: DXF läuft gegen den Uhrzeigersinn vom Start- zum Endwinkel (in der Ebene gesehen)
            angle = lambda p: math.degrees(math.atan2(p[1] - centre[1], p[0] - centre[0])) % 360
            a, b = angle(local(curve.Value(first))), angle(local(curve.Value(last)))
            middle = angle(local(curve.Value((first + last) / 2)))
            counter = (middle - a) % 360 < (b - a) % 360
            start, finish = (a, b) if counter else (b, a)
            entries.append(f"0\nARC\n8\n{layer}\n10\n{centre[0]:.6f}\n20\n{centre[1]:.6f}\n30\n0.0\n"
                           f"40\n{radius:.6f}\n50\n{start:.6f}\n51\n{finish:.6f}\n")
        else:
            steps = 24
            points = [local(curve.Value(first + (last - first) * k / steps)) for k in range(steps + 1)]
            for a, b in zip(points, points[1:]):
                line(a, b)
    return entries


def write_dxf(sheet: dict, path: str, current=None) -> dict:
    """Zuschnitt als DXF (wie Fusions „DXF exportieren“ an der Abwicklung): Außenkontur und Löcher auf dem Layer
    „KONTUR“, Biegelinien auf „BIEGELINIEN“ – in Millimetern, in den Koordinaten der Grundflansch-Ebene.
    Gibt die Zahl der Einträge je Layer zurück."""
    import numpy as np
    origin, normal, x_axis = (np.array(v, dtype=float) for v in sheet["frame"])
    y_axis = np.cross(normal, x_axis)
    flat = flat_pattern(sheet, current)
    outline = _dxf_entities(flat, origin, x_axis, y_axis, normal, "KONTUR")
    bends = []
    for (a, b), transform, _angle in sheet.get("bends", []):
        ends = []
        for point in (a, b):
            moved = gp_Pnt(*point).Transformed(transform)
            offset = np.array([moved.X(), moved.Y(), moved.Z()]) - origin
            ends.append((float(offset @ x_axis), float(offset @ y_axis)))
        bends.append(f"0\nLINE\n8\nBIEGELINIEN\n62\n1\n10\n{ends[0][0]:.6f}\n20\n{ends[0][1]:.6f}\n30\n0.0\n"
                     f"11\n{ends[1][0]:.6f}\n21\n{ends[1][1]:.6f}\n31\n0.0\n")
    # einfaches DXF (R12): nur der Abschnitt ENTITIES – das lesen alle Laserschneid- und CAD-Programme
    text = "0\nSECTION\n2\nENTITIES\n" + "".join(outline + bends) + "0\nENDSEC\n0\nEOF\n"
    with open(path, "w", encoding="ascii") as handle:
        handle.write(text)
    return {"KONTUR": len(outline), "BIEGELINIEN": len(bends)}


def _bounds(shape) -> tuple[float, float, float, float, float, float]:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    low, high = box.CornerMin(), box.CornerMax()
    return low.X(), low.Y(), low.Z(), high.X(), high.Y(), high.Z()


@dataclass
class FlatPattern(Feature):
    """Abwicklung (Fusion: Abwicklung erstellen): das Blechteil flach gelegt, als eigener Körper neben dem
    gefalteten Teil (in der Ebene des Grundflanschs)."""

    body: str = ALL_BODIES

    KIND = "Abwicklung"

    def apply(self, state: BuildState) -> None:
        names = [name for name in ft.select_bodies(state, self.body) if name in state.sheets]
        if not names:
            raise FeatureError("Kein Blechteil gewählt (erst einen Flansch aus einer Skizze anlegen)")
        for name in names:
            flat = flat_pattern(state.sheets[name], state.bodies[name])
            # neben das gefaltete Teil legen (in x-Richtung), damit beide sichtbar bleiben
            folded, laid = _bounds(state.bodies[name]), _bounds(flat)
            move = gp_Trsf()
            move.SetTranslation(gp_Vec(folded[3] + _FLAT_GAP - laid[0], 0.0, 0.0))
            state.add_body(_moved(flat, move))


@dataclass
class Unfold(Feature):
    """Abwickeln (Fusion: Abwickeln): das Blechteil liegt ab hier flach an Ort und Stelle – in der Ebene des
    Grundflanschs. Danach lassen sich mit den gewöhnlichen Werkzeugen Löcher und Ausschnitte über die flache Fläche
    setzen; „Zurückbiegen“ faltet das Teil wieder samt diesen Ausschnitten."""

    body: str = ALL_BODIES

    KIND = "Abwickeln"

    def apply(self, state: BuildState) -> None:
        names = [name for name in ft.select_bodies(state, self.body) if name in state.sheets]
        if not names:
            raise FeatureError("Kein Blechteil gewählt (erst einen Flansch aus einer Skizze anlegen)")
        for name in names:
            sheet = state.sheets[name]
            if sheet.get("unfolded") is not None:
                raise FeatureError("Das Blechteil ist schon abgewickelt (erst „Zurückbiegen“)")
            folded = state.bodies[name]
            flat = flat_pattern(sheet, folded)
            state.bodies[name] = flat
            # gemerkt für „Zurückbiegen“: der gefaltete Körper und die flache Form vor neuen Ausschnitten
            state.sheets[name] = {**sheet, "unfolded": {"folded": folded, "flat": flat}}


@dataclass
class Refold(Feature):
    """Zurückbiegen (Fusion: Zurückbiegen): faltet ein abgewickeltes Blechteil wieder. Was in der flachen Lage
    weggenommen wurde, wird je ebener Platte in die gefaltete Lage umgerechnet und dort ebenfalls entfernt (in
    einer Biegezone Weggenommenes bleibt unberücksichtigt, ebenso in der flachen Lage hinzugefügtes Material)."""

    body: str = ALL_BODIES

    KIND = "Zurückbiegen"

    def apply(self, state: BuildState) -> None:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
        names = [name for name in ft.select_bodies(state, self.body)
                 if name in state.sheets and state.sheets[name].get("unfolded") is not None]
        if not names:
            raise FeatureError("Kein abgewickeltes Blechteil (erst „Abwickeln“)")
        for name in names:
            sheet = state.sheets[name]
            folded, flat = sheet["unfolded"]["folded"], sheet["unfolded"]["flat"]
            current = state.bodies[name]
            removed = ft._boolean(BRepAlgoAPI_Cut, flat, current)
            if _volume(removed) > 1e-9:
                for plate, transform in sheet["plates"]:
                    region = _moved(plate, transform)
                    piece = ft._boolean(BRepAlgoAPI_Common, removed, region)
                    if _volume(piece) > 1e-9:
                        folded = ft._boolean(BRepAlgoAPI_Cut, folded, _moved(piece, transform.Inverted()))
                merged = ShapeUpgrade_UnifySameDomain(folded, True, True, False)
                merged.Build()
                folded = merged.Shape()
            state.bodies[name] = folded
            state.sheets[name] = {**sheet, "unfolded": None}


def _halfspace(point: gp_Pnt, normal: gp_Vec):
    """Der Halbraum auf der Seite von `normal` der Ebene durch `point` (für Boolesche Schnitte)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
    from OCP.gp import gp_Pln
    face = BRepBuilderAPI_MakeFace(gp_Pln(point, gp_Dir(normal)), -1e5, 1e5, -1e5, 1e5).Face()
    return BRepPrimAPI_MakeHalfSpace(face, point.Translated(normal)).Solid()


def _common(shape, tool):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    return ft._boolean(BRepAlgoAPI_Common, shape, tool)


def _centre(shape) -> gp_Pnt:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.CentreOfMass()


@dataclass
class SketchBend(Feature):
    """Biegen an einer Linie (Fusion: Biegen): eine Skizzenlinie auf einer ebenen Blechfläche ist die Biegelinie;
    die Seite dahinter klappt um den Biegewinkel hoch. Die Biegezone (Breite = Biegezugabe) liegt mittig auf der
    Linie. Was an der klappenden Seite hängt (Flansche), klappt mit; die Abwicklung bleibt richtig."""

    sketch: str = ""
    body: str = ALL_BODIES
    angle: float = 90.0
    bend_radius: float = 0.0        # 0 = Biegeradius der Blechregel
    flip: bool = False              # zur anderen Seite biegen (nach unten statt nach oben)
    flip_side: bool = False         # das größere statt des kleineren Stücks klappt

    KIND = "Biegen"

    def apply(self, state: BuildState) -> None:
        if not 0.0 < self.angle <= 180.0:
            raise FeatureError("Der Biegewinkel liegt zwischen 0° und 180°")
        curves = state.curves.get(self.sketch)
        if curves is None:
            raise FeatureError(f"Die Skizze '{self.sketch}' gibt es nicht")
        lines = [edge for edge in ft.unique_edges(curves) if BRepAdaptor_Curve(edge).GetType() == GeomAbs_Line]
        if not lines:
            raise FeatureError("Die Skizze braucht eine Linie als Biegelinie")
        curve = BRepAdaptor_Curve(lines[0])
        first, last = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        middle = gp_Pnt((first.X() + last.X()) / 2, (first.Y() + last.Y()) / 2, (first.Z() + last.Z()) / 2)
        along = gp_Vec(first, last)
        along.Normalize()
        # Blechteil und ebene Platte, auf der die Linie liegt
        candidates = [name for name in ft.select_bodies(state, self.body) if name in state.sheets
                      and _distance(state.bodies[name], middle) < 1e-6]
        if not candidates:
            raise FeatureError("Die Biegelinie liegt auf keinem Blechteil")
        name = candidates[0]
        sheet, body = state.sheets[name], state.bodies[name]
        if sheet.get("unfolded") is not None:
            raise FeatureError("Das Blechteil ist abgewickelt (erst „Zurückbiegen“)")
        thickness, k_factor = sheet["thickness"], sheet["k_factor"]
        radius = self.bend_radius if self.bend_radius > 0 else sheet["radius"]
        number = min(range(len(sheet["plates"])), key=lambda i: _distance(sheet["plates"][i][0], middle))
        plate, transform = sheet["plates"][number]
        if _distance(plate, middle) > 1e-6:
            raise FeatureError("Die Biegelinie liegt nicht auf einer ebenen Platte (z. B. in einer Biegung)")
        # Blechfläche unter der Linie: ihre Normale steht quer zur Linie, eine halbe Dicke darunter ist Blech
        up = None
        for face in ft.faces_of(plate):
            if _distance(face, middle) > 1e-6:
                continue
            normal = gp_Vec(*face_normal(face))
            if abs(normal.Dot(along)) < 1e-6 and _inside(plate, middle.Translated(normal * -(thickness / 2))):
                up = normal
                break
        if up is None:
            raise FeatureError("Die Biegelinie liegt nicht auf der Ober- oder Unterseite des Blechs")
        across = up.Crossed(along)              # in der Blechebene quer zur Linie: zur klappenden Seite
        across.Normalize()
        # Vorgabe: das kleinere Stück klappt, das größere bleibt stehen (wie meist gewollt); „andere Seite“ dreht um
        smaller_first = _volume(_common(body, _halfspace(middle, across))) <= \
            _volume(_common(body, _halfspace(middle, across.Reversed())))
        if smaller_first == self.flip_side:
            across.Reverse()
        bend_up = up if not self.flip else up.Reversed()
        allowance = bend_allowance(self.angle, radius, thickness, k_factor)
        start_plane = middle.Translated(across * -(allowance / 2))      # Anfang der Biegezone
        end_plane = middle.Translated(across * (allowance / 2))         # Ende der Biegezone
        keep_side = _halfspace(start_plane, across.Reversed())
        move_side = _halfspace(end_plane, across)
        # Biegezone zwischen den beiden Ebenen: nacheinander mit beiden Halbräumen schneiden (die Schnittmenge
        # zweier unendlicher Halbräume selbst liefert der Kern nicht verlässlich)
        zone_sides = (_halfspace(start_plane, across), _halfspace(end_plane, across.Reversed()))
        in_zone = lambda shape: _common(_common(shape, zone_sides[0]), zone_sides[1])
        # andere Flansche dürfen die Biegezone nicht kreuzen (sie müssten sich mitverbiegen)
        for index, (shape, _transform) in enumerate(sheet["plates"]):
            if index != number and _volume(in_zone(shape)) > 1e-6:
                raise FeatureError("Die Biegelinie kreuzt einen Flansch – so lässt sich nicht biegen")
        # Gefaltet: Biegeachse an der Zonengrenze, innerer Radius auf der Seite, zu der gebogen wird
        inner = gp_Vec(start_plane.XYZ()) if not self.flip else gp_Vec(start_plane.XYZ()) - up * thickness
        axis = gp_Ax1(gp_Pnt((inner + bend_up * radius).XYZ()), gp_Dir(across.Crossed(bend_up)))
        turn = math.radians(self.angle)
        back = gp_Trsf()
        back.SetRotation(axis, -turn)
        shift = gp_Trsf()
        shift.SetTranslation(across * allowance)
        local = shift.Multiplied(back)          # gefaltet → flach für alles hinter der Biegung
        fold = local.Inverted()                 # flach → gefaltet
        kept = _common(body, keep_side)
        moving = _common(body, move_side)
        # Querschnitte an der Zonengrenze um die Achse drehen: das ist die Biegung
        sections = [face for face in ft.faces_of(kept) if abs(gp_Vec(*face_normal(face)).Dot(across) - 1.0) < 1e-6
                    and _distance(face, start_plane.Translated(up * -(thickness / 2))) < 1e-6]
        if not sections:
            raise FeatureError("An der Biegelinie wurde kein Blechquerschnitt gefunden")
        bends = []
        for face in sections:
            revolve = BRepPrimAPI_MakeRevol(face, axis, turn)
            revolve.Build()
            if not revolve.IsDone():
                raise FeatureError("Die Biegung ließ sich nicht bilden")
            bends.append(revolve.Shape())
        pieces = [kept, *bends]
        if _volume(moving) > 1e-9:
            pieces.append(_moved(moving, fold))
        state.bodies[name] = _fused(pieces)

        # Aufbau für die Abwicklung: die Platte teilen, alles auf der klappenden Seite mitnehmen
        def on_moving_side(shape) -> bool:
            return gp_Vec(middle, _centre(shape)).Dot(across) > 0

        plates = []
        for index, (shape, place) in enumerate(sheet["plates"]):
            if index == number:
                plates.append((_common(shape, keep_side), place))
                rest = _common(shape, move_side)
                if _volume(rest) > 1e-9:
                    plates.append((_moved(rest, fold), place.Multiplied(local)))
            elif on_moving_side(shape):
                plates.append((_moved(shape, fold), place.Multiplied(local)))
            else:
                plates.append((shape, place))
        strips = [(_moved(shape, fold), place.Multiplied(local)) if on_moving_side(shape) else (shape, place)
                  for shape, place in sheet["strips"]]
        strips.append((in_zone(plate), transform))
        lines_flat = []
        for (a, b), place, angle in sheet.get("bends", []):
            centre = gp_Pnt((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
            if gp_Vec(middle, centre).Dot(across) > 0:
                moved = [gp_Pnt(*point).Transformed(fold) for point in (a, b)]
                lines_flat.append((tuple((p.X(), p.Y(), p.Z()) for p in moved), place.Multiplied(local), angle))
            else:
                lines_flat.append(((a, b), place, angle))
        lines_flat.append((tuple((p.X(), p.Y(), p.Z()) for p in (first, last)), transform, self.angle))
        state.sheets[name] = {**sheet, "plates": plates, "strips": strips, "bends": lines_flat,
                              "folded": state.bodies[name]}


@dataclass
class ConvertToSheet(Feature):
    """In Blech umwandeln (Fusion: In Blech umwandeln): ein plattenförmiger Körper gleicher Dicke (z. B. ein
    extrudiertes Profil oder ein flacher Quader) wird zum Blechteil. Die Dicke ist der Abstand seiner beiden
    größten gegenüberliegenden ebenen Flächen; danach gelten Flansch, Biegen, Abwicklung und DXF auch für ihn."""

    body: str = ""
    bend_radius: float = 1.0
    k_factor: float = DEFAULT_K_FACTOR

    KIND = "In Blech umwandeln"

    def apply(self, state: BuildState) -> None:
        import numpy as np
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        if self.body not in state.bodies:
            raise ft.missing_body(self.body)
        if self.body in state.sheets:
            raise FeatureError("Der Körper ist schon ein Blechteil")
        if self.bend_radius < 0 or not 0.0 <= self.k_factor <= 1.0:
            raise FeatureError("Biegeradius ≥ 0 und K-Faktor zwischen 0 und 1")
        body = state.bodies[self.body]

        def area(face) -> float:
            properties = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, properties)
            return properties.Mass()

        planes = []
        for face in ft.faces_of(body):
            frame = None
            try:
                from .sketch import face_frame
                frame = face_frame(face)
            except Exception:
                frame = None
            if frame is not None:
                planes.append((area(face), np.array(frame[0:3]), np.array(frame[3:6]), np.array(frame[6:9])))
        planes.sort(key=lambda entry: -entry[0])
        # die größte Fläche und ihr Gegenstück (entgegengesetzte Normale, gleich groß)
        for size, origin, normal, x_axis in planes:
            partner = next(((o, n) for a, o, n, _x in planes if np.dot(n, normal) < -1 + 1e-6
                            and abs(a - size) < 1e-6 * max(size, 1.0)), None)
            if partner is not None:
                break
        else:
            raise FeatureError("Keine zwei gleich großen gegenüberliegenden ebenen Flächen – der Körper ist keine Platte")
        thickness = float(abs(np.dot(partner[0] - origin, normal)))
        if thickness < 1e-6 or abs(_volume(body) - size * thickness) > 1e-6 * max(_volume(body), 1.0):
            raise FeatureError("Der Körper ist nicht überall gleich dick (keine ebene Platte)")
        # Ebene des Blechs: die untere der beiden Flächen, Normale zur oberen hin
        bottom = origin - normal * thickness
        frame = (tuple(bottom), tuple(normal), tuple(x_axis))
        state.sheets[self.body] = {**_new_sheet(thickness, self.bend_radius, self.k_factor, body, frame),
                                   "folded": body}


# In das Verzeichnis für das Laden gespeicherter Entwürfe eintragen
for _feature_class in (BaseFlange, EdgeFlange, FlatPattern, Unfold, Refold, SketchBend, ConvertToSheet):
    ft.FEATURE_CLASSES[_feature_class.KIND] = _feature_class
