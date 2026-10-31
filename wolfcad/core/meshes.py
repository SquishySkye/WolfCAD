# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Netze wie im Fusion-Reiter NETZ: Dreiecksnetze (z. B. aus STL-Dateien oder 3D-Scans) als eigene Körperart.

Ein Netzkörper ist ein Feld von Dreiecken (n × 3 × 3, mm) in `BuildState.meshes`. Werkzeuge:
- Netz einfügen: STL-Datei laden; die Dreiecke werden komprimiert im Entwurf gespeichert (wie Fusion den
  eingefügten Netzkörper im Entwurf hält), damit der Entwurf auch ohne die Datei aufgeht.
- In Volumenkörper umwandeln (Fusion: In BRep umwandeln, facettiert): jedes Dreieck wird eine ebene Fläche.
- Körper in Netz umwandeln: Volumenkörper zerlegen.
- Netz verkleinern: Ecken auf ein Raster zusammenlegen (weniger Dreiecke, gröbere Form).
"""
from __future__ import annotations

import base64
import zlib
from dataclasses import dataclass

import numpy as np

from . import features as ft
from .features import BuildState, Feature, FeatureError

MAX_TRIANGLES_TO_BODY = 200000      # mehr Dreiecke würden beim Umwandeln zu lange rechnen (≈ 50 000 je 3 s)
UNIFY_LIMIT = 20000                 # bis zu so vielen Dreiecken werden gleiche Ebenen zusammengefasst


def read_stl(path: str) -> np.ndarray:
    """Die Dreiecke einer STL-Datei (binär oder Text) als Feld n × 3 × 3."""
    from OCP.RWStl import RWStl
    triangulation = RWStl.ReadFile_s(path)
    if triangulation is None or triangulation.NbTriangles() == 0:
        raise FeatureError(f"Die STL-Datei '{path}' enthält keine Dreiecke")
    nodes = np.array([[p.X(), p.Y(), p.Z()] for p in
                      (triangulation.Node(i) for i in range(1, triangulation.NbNodes() + 1))])
    indices = np.array([triangulation.Triangle(i).Get() for i in range(1, triangulation.NbTriangles() + 1)]) - 1
    return nodes[indices]


# Dateiarten, aus denen Netze geladen werden können (Runde 97: mehrere Dateien auf einmal, auch OBJ/3MF/STEP)
MESH_FILE_FILTER = "Modelle (*.stl *.obj *.3mf *.step *.stp);;STL (*.stl);;OBJ (*.obj);;3MF (*.3mf);;STEP (*.step *.stp)"


def read_mesh_file(path: str) -> list[tuple[str, np.ndarray]]:
    """Alle Objekte einer Modelldatei als [(Name, Dreiecke n × 3 × 3)]. STL und OBJ liefern ein Objekt, 3MF je
    Objekt der Bauliste eines, STEP je Volumenkörper eines (fein vernetzt)."""
    from pathlib import Path
    file = Path(path)
    suffix = file.suffix.lower()
    if suffix == ".stl":
        return [(file.stem, read_stl(str(file)))]
    if suffix == ".obj":
        return [(file.stem, read_obj(str(file)))]
    if suffix == ".3mf":
        return read_3mf(str(file))
    if suffix in (".step", ".stp"):
        from . import exchange
        try:
            shapes = exchange.read_step(str(file))
        except exchange.ExchangeError as error:
            raise FeatureError(str(error)) from error
        found = [(file.stem if len(shapes) == 1 else f"{file.stem} {number}", tessellate(shape, 0.05))
                 for number, shape in enumerate(shapes, 1)]
        return [(name, triangles) for name, triangles in found if len(triangles)]
    raise FeatureError(f"Unbekannte Dateiart '{file.suffix}' (STL, OBJ, 3MF oder STEP)")


def read_obj(path: str) -> np.ndarray:
    """Dreiecke einer Wavefront-OBJ-Datei; Vielecke werden als Fächer zerlegt, Texturen und Normalen übergangen."""
    vertices, triangles = [], []
    with open(path, encoding="utf-8", errors="replace") as source:
        for line in source:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "v" and len(parts) >= 4:
                vertices.append([float(value) for value in parts[1:4]])
            elif parts[0] == "f" and len(parts) >= 4:
                # „7/1/3“: nur die Ecknummer zählt; negative Nummern zählen vom Ende der bisherigen Ecken
                corners = [int(part.split("/")[0]) for part in parts[1:]]
                corners = [c - 1 if c > 0 else len(vertices) + c for c in corners]
                triangles += [(corners[0], corners[i], corners[i + 1]) for i in range(1, len(corners) - 1)]
    if not triangles:
        raise FeatureError(f"Die OBJ-Datei '{path}' enthält keine Flächen")
    return np.asarray(vertices, float)[np.asarray(triangles)]


def read_3mf(path: str) -> list[tuple[str, np.ndarray]]:
    """Objekte einer 3MF-Datei (Bauliste samt Lage; Komponenten werden mit ihrer Lage eingesetzt)."""
    import zipfile
    import xml.etree.ElementTree as ElementTree
    space = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
    try:
        with zipfile.ZipFile(path) as archive:
            name = next((entry for entry in archive.namelist() if entry.lower().endswith(".model")
                         and entry.lower().startswith("3d/")), None)
            if name is None:
                raise FeatureError(f"'{path}' enthält kein 3D-Modell")
            root = ElementTree.fromstring(archive.read(name))
    except (zipfile.BadZipFile, ElementTree.ParseError, OSError) as error:
        raise FeatureError(f"'{path}' ist keine lesbare 3MF-Datei: {error}") from error

    def matrix(text: str | None) -> np.ndarray:
        """3MF-Lage „m00 m01 m02 m10 … m32“ (Zeilen, Verschiebung zuletzt) als 4 × 4-Matrix für Zeilenvektoren."""
        result = np.eye(4)
        if text:
            values = [float(value) for value in text.split()]
            result[:, :3] = np.asarray(values, float).reshape(4, 3)
        return result

    objects = {item.get("id"): item for item in root.iter(space + "object")}

    def triangles_of(object_id: str, depth: int = 0) -> np.ndarray:
        item = objects.get(object_id)
        if item is None or depth > 8:
            return np.zeros((0, 3, 3))
        mesh = item.find(space + "mesh")
        if mesh is not None:
            points = np.array([[float(v.get(axis)) for axis in "xyz"] for v in mesh.iter(space + "vertex")])
            corners = np.array([[int(t.get(key)) for key in ("v1", "v2", "v3")] for t in mesh.iter(space + "triangle")])
            return points[corners] if len(corners) else np.zeros((0, 3, 3))
        parts = []
        for component in item.iter(space + "component"):
            inner = triangles_of(component.get("objectid"), depth + 1)
            parts.append(_transformed(inner, matrix(component.get("transform"))))
        return np.concatenate(parts) if parts else np.zeros((0, 3, 3))

    build = root.find(space + "build")
    items = list(build.iter(space + "item")) if build is not None else []
    if not items:              # ohne Bauliste: jedes Objekt mit Netz
        items = [ElementTree.Element("item", objectid=key) for key in objects]
    found = []
    for number, item in enumerate(items, 1):
        triangles = _transformed(triangles_of(item.get("objectid")), matrix(item.get("transform")))
        if len(triangles):
            label = objects[item.get("objectid")].get("name") if item.get("objectid") in objects else None
            found.append((label or f"Objekt {number}", triangles))
    if not found:
        raise FeatureError(f"Die 3MF-Datei '{path}' enthält keine Dreiecke")
    return found


def _transformed(triangles: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Dreiecke mit einer 4 × 4-Lage (Zeilenvektoren, Verschiebung in der letzten Zeile)."""
    if not len(triangles):
        return triangles
    points = triangles.reshape(-1, 3) @ matrix[:3, :3] + matrix[3, :3]
    return points.reshape(-1, 3, 3)


def write_stl(triangles: np.ndarray, path: str) -> None:
    """Dreiecke als binäre STL-Datei (mit berechneten Normalen)."""
    triangles = np.asarray(triangles, dtype=np.float32).reshape(-1, 3, 3)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
    records = np.zeros(len(triangles), dtype=[("normal", "<f4", 3), ("corners", "<f4", (3, 3)), ("extra", "<u2")])
    records["normal"], records["corners"] = normals, triangles
    with open(path, "wb") as handle:
        handle.write(b"WolfCAD STL".ljust(80, b" "))
        handle.write(np.uint32(len(triangles)).tobytes())
        handle.write(records.tobytes())


def pack(triangles: np.ndarray) -> str:
    """Dreiecke kompakt als Text (für den Entwurf): float32, zlib, base64."""
    return base64.b64encode(zlib.compress(np.asarray(triangles, dtype=np.float32).tobytes(), 6)).decode("ascii")


def unpack(text: str) -> np.ndarray:
    return np.frombuffer(zlib.decompress(base64.b64decode(text)), dtype=np.float32).astype(float).reshape(-1, 3, 3)


def tessellate(shape, deflection: float = 0.1, angle: float = 0.3) -> np.ndarray:
    """Dreiecke eines Volumenkörpers (außen richtig herum ausgerichtet); `angle`: größte Winkelabweichung (rad)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopLoc import TopLoc_Location
    # nicht parallel: im Probelauf des Absturzschutzes (abgespaltener Prozess) fehlt der Thread-Pool des Kerns –
    # parallel zerlegt wartete das Kind ewig und lief in die 45-s-Grenze (Zufallstest Startwert 21)
    # auf einer Kopie ohne vorhandene Zerlegung: sonst behielt der Kern die gröbere Zerlegung der Anzeige (sie erfüllt
    # die Abweichung in mm, der Winkel zählt dabei nicht) – das Netz hing davon ab, ob der Körper schon angezeigt war
    # (Runde 94: Kugel zum Formen trotz feiner Einstellung grob)
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    shape = BRepBuilderAPI_Copy(shape, True, False).Shape()
    BRepMesh_IncrementalMesh(shape, deflection, False, angle, False)
    found = []
    for face in ft.faces_of(shape):
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, location)
        if mesh is None:
            continue
        transform = location.Transformation()
        nodes = np.array([[p.X(), p.Y(), p.Z()] for p in
                          (mesh.Node(i).Transformed(transform) for i in range(1, mesh.NbNodes() + 1))])
        indices = np.array([mesh.Triangle(i).Get() for i in range(1, mesh.NbTriangles() + 1)]) - 1
        if face.Orientation() == TopAbs_REVERSED:
            indices = indices[:, [0, 2, 1]]
        found.append(nodes[indices])
    if not found:
        raise FeatureError("Der Körper ließ sich nicht in Dreiecke zerlegen")
    return np.concatenate(found)


def to_solid(triangles: np.ndarray):
    """Facettierter Volumenkörper aus einem geschlossenen Netz (jede Facette eine ebene Fläche).

    Gebaut wird direkt aus den gemeinsamen Ecken: jede Ecke ein Punkt, jede Kante genau einmal (von beiden
    Nachbardreiecken in Gegenrichtung benutzt), je Dreieck eine Fläche in der Ebene seiner drei Ecken. Das ist
    viel schneller als einzelne Dreiecke zusammenzunähen; die Ausrichtung kommt aus der Reihenfolge der Ecken
    (einheitlich nach „Netz reparieren“).
    """
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeVertex,
                                    BRepBuilderAPI_MakeWire)
    from OCP.Geom import Geom_Plane
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    from OCP.TopoDS import TopoDS, TopoDS_Shell, TopoDS_Solid
    from OCP.gce import gce_MakePln
    from OCP.gp import gp_Pnt
    points, faces = _indexed(np.asarray(triangles, dtype=float))
    areas = np.linalg.norm(np.cross(points[faces[:, 1]] - points[faces[:, 0]], points[faces[:, 2]] - points[faces[:, 0]]), axis=1)
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
                  & (areas > 1e-12)]
    # dicht ist das Netz, wenn jede Kante genau zweimal vorkommt (einmal je Richtung)
    keys = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _unique, counts = np.unique(keys, axis=0, return_counts=True)
    if not len(faces) or np.any(counts != 2):
        raise FeatureError("Das Netz ist nicht geschlossen (Löcher) – erst reparieren, dann umwandeln")
    vertices = [BRepBuilderAPI_MakeVertex(gp_Pnt(*p)).Vertex() for p in points]
    edges: dict = {}
    builder, shell = BRep_Builder(), TopoDS_Shell()
    builder.MakeShell(shell)
    for a, b, c in faces:
        wire = BRepBuilderAPI_MakeWire()
        for u, v in ((a, b), (b, c), (c, a)):
            key = (min(u, v), max(u, v))
            if key not in edges:
                edges[key] = BRepBuilderAPI_MakeEdge(vertices[key[0]], vertices[key[1]]).Edge()
            wire.Add(edges[key] if u < v else TopoDS.Edge(edges[key].Reversed()))
        plane = Geom_Plane(gce_MakePln(gp_Pnt(*points[a]), gp_Pnt(*points[b]), gp_Pnt(*points[c])).Value())
        builder.Add(shell, BRepBuilderAPI_MakeFace(plane, wire.Wire(), True).Face())
    shell.Closed(True)
    solid = TopoDS_Solid()
    builder.MakeSolid(solid)
    builder.Add(solid, shell)
    from .surfaces import _volume
    if _volume(solid) < 0:
        solid.Reverse()             # nach innen gekehrt: umdrehen
    if len(faces) > UNIFY_LIMIT:
        return solid                # bei großen Netzen (Scans) gibt es kaum gleiche Ebenen zum Zusammenfassen
    # gleiche Ebenen zusammenfassen: ein Quader aus 12 Dreiecken wird wieder ein Quader mit 6 Flächen
    merged = ShapeUpgrade_UnifySameDomain(solid, True, True, False)
    merged.Build()
    return merged.Shape()


def reduce(triangles: np.ndarray, cell: float) -> np.ndarray:
    """Netz verkleinern: Ecken auf ein Raster der Weite `cell` legen und Dreiecke ohne Fläche verwerfen."""
    snapped = np.round(triangles / cell) * cell
    areas = np.linalg.norm(np.cross(snapped[:, 1] - snapped[:, 0], snapped[:, 2] - snapped[:, 0]), axis=1)
    return snapped[areas > 1e-12]


def _indexed(triangles: np.ndarray, tolerance: float = 1e-6) -> tuple[np.ndarray, np.ndarray]:
    """Gemeinsame Ecken: (Eckpunkte k × 3, Dreiecke als Eckennummern n × 3); gleich nahe Ecken werden eins."""
    keys = np.round(triangles.reshape(-1, 3) / tolerance).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    points = np.zeros((len(unique), 3))
    np.add.at(points, inverse.ravel(), triangles.reshape(-1, 3))
    points /= np.bincount(inverse.ravel())[:, None]
    return points, inverse.reshape(-1, 3)


def _signed_volume(points: np.ndarray, faces: np.ndarray) -> float:
    a, b, c = points[faces[:, 0]], points[faces[:, 1]], points[faces[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def repair(triangles: np.ndarray) -> tuple[np.ndarray, dict]:
    """Netz reparieren: doppelte Ecken zusammenfassen, entartete und doppelte Dreiecke verwerfen, alle Dreiecke
    einheitlich ausrichten (bei geschlossenem Netz nach außen) und Löcher mit Dreiecksfächern schließen.
    Gibt das neue Netz und einen Bericht (Zahlen je Schritt) zurück."""
    report = {}
    points, faces = _indexed(np.asarray(triangles, dtype=float))
    # entartet: zwei gleiche Ecken oder keine Fläche
    areas = np.linalg.norm(np.cross(points[faces[:, 1]] - points[faces[:, 0]], points[faces[:, 2]] - points[faces[:, 0]]), axis=1)
    good = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2]) & (areas > 1e-12)
    report["entartet"] = int((~good).sum())
    faces = faces[good]
    # doppelt: dieselben drei Ecken (in beliebiger Reihenfolge)
    _, first = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    report["doppelt"] = len(faces) - len(first)
    faces = faces[np.sort(first)]
    # einheitlich ausrichten: über gemeinsame Kanten von Dreieck zu Dreieck weitergeben (Breitensuche)
    edge_faces: dict = {}
    for number, (a, b, c) in enumerate(faces):
        for u, v in ((a, b), (b, c), (c, a)):
            edge_faces.setdefault((min(u, v), max(u, v)), []).append(number)
    flipped, seen = 0, np.zeros(len(faces), dtype=bool)
    for start in range(len(faces)):
        if seen[start]:
            continue
        seen[start] = True
        queue = [start]
        while queue:
            current = queue.pop()
            a, b, c = faces[current]
            for u, v in ((a, b), (b, c), (c, a)):
                for other in edge_faces[(min(u, v), max(u, v))]:
                    if other == current or seen[other]:
                        continue
                    x, y, z = faces[other]
                    # passt, wenn der Nachbar dieselbe Kante andersherum durchläuft
                    if (u, v) in ((x, y), (y, z), (z, x)):
                        faces[other] = faces[other][[0, 2, 1]]
                        flipped += 1
                    seen[other] = True
                    queue.append(other)
    report["umgedreht"] = flipped
    # Löcher: Randkanten (nur einmal benutzt, gerichtet) zu Schleifen verketten und mit einem Fächer schließen
    directed = {}
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)):
            directed[(u, v)] = True
    boundary = {u: v for (u, v) in directed if (v, u) not in directed}
    patches, holes = [], 0
    points_list = [p for p in points]
    while boundary:
        start, loop = next(iter(boundary)), []
        current = start
        while current in boundary:
            loop.append(current)
            current = boundary.pop(current)
            if current == start:
                break
        if len(loop) < 3 or current != start:
            continue                    # offener Zug (z. B. ein Rand, der nicht schließt): nicht füllen
        holes += 1
        centre = len(points_list)
        points_list.append(np.mean([points_list[i] for i in loop], axis=0))
        for u, v in zip(loop, loop[1:] + loop[:1]):
            patches.append((v, u, centre))      # gegen die Randrichtung: passt zur Ausrichtung der Nachbarn
    report["löcher"] = holes
    points = np.array(points_list)
    if patches:
        faces = np.vstack([faces, np.array(patches)])
    # geschlossen und nach innen gekehrt → alles umdrehen
    if holes or not boundary:
        if _signed_volume(points, faces) < 0:
            faces = faces[:, [0, 2, 1]]
    return points[faces], report


def smooth(triangles: np.ndarray, iterations: int = 5, strength: float = 0.5) -> np.ndarray:
    """Netz glätten (Taubin): abwechselnd zur Nachbarmitte hin und etwas zurück – glättet Kanten und Rauschen,
    ohne die Form wie einfaches Mitteln schrumpfen zu lassen. Randecken offener Netze bleiben stehen."""
    points, faces = _indexed(np.asarray(triangles, dtype=float))
    neighbours = [set() for _ in range(len(points))]
    edge_count: dict = {}
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)):
            neighbours[u].add(v)
            neighbours[v].add(u)
            key = (min(u, v), max(u, v))
            edge_count[key] = edge_count.get(key, 0) + 1
    fixed = {u for (u, v), count in edge_count.items() if count == 1} | {v for (u, v), count in edge_count.items() if count == 1}
    lists = [np.array(sorted(n)) for n in neighbours]
    movable = np.array([i for i in range(len(points)) if i not in fixed and len(lists[i])])
    for _ in range(max(0, int(iterations))):
        for factor in (strength, -(strength + 0.03)):        # Taubin: λ, dann μ = −(λ + 0,03)
            centres = np.array([points[lists[i]].mean(axis=0) for i in movable])
            points[movable] += factor * (centres - points[movable])
    return points[faces]


def _mesh_of(state: BuildState, name: str) -> np.ndarray:
    if name not in state.meshes:
        raise FeatureError(f"Netzkörper '{name}' ist nicht (mehr) vorhanden" if name else "Bitte einen Netzkörper wählen")
    return state.meshes[name]


@dataclass
class MeshImport(Feature):
    """Netz einfügen: Dreiecke einer STL-Datei als Netzkörper (im Entwurf gespeichert, siehe `data`)."""

    source: str = ""        # Pfad der Datei (nur zur Anzeige und falls `data` fehlt)
    data: str = ""          # Dreiecke, gepackt mit `pack`
    scale: float = 1.0      # z. B. 25,4 für Dateien in Zoll

    KIND = "Netz einfügen"

    def parameters(self) -> list:
        # die gepackten Dreiecke sind kein Eingabefeld
        return [f for f in super().parameters() if f.name != "data"]

    def apply(self, state: BuildState) -> None:
        if self.scale <= 0:
            raise FeatureError("Der Maßstab muss größer als 0 sein")
        if not self.data and not self.source:
            raise FeatureError("Bitte eine STL-Datei wählen (Netz einfügen)")
        triangles = unpack(self.data) if self.data else read_stl(self.source)
        state.add_mesh(triangles * self.scale)


@dataclass
class MeshToBody(Feature):
    """In Volumenkörper umwandeln (Fusion: In BRep umwandeln, facettiert): geschlossenes Netz → Volumenkörper."""

    mesh: str = ""

    KIND = "Netz in Körper umwandeln"

    def apply(self, state: BuildState) -> None:
        triangles = _mesh_of(state, self.mesh)
        if len(triangles) > MAX_TRIANGLES_TO_BODY:
            raise FeatureError(f"Das Netz hat {len(triangles)} Dreiecke – erst verkleinern "
                               f"(höchstens {MAX_TRIANGLES_TO_BODY})")
        state.add_body(to_solid(triangles))
        del state.meshes[self.mesh]


@dataclass
class BodyToMesh(Feature):
    """Körper in Netz umwandeln: der Volumenkörper wird ein Netzkörper (Feinheit = größte Abweichung in mm, bei
    großen Körpern höchstens 1/5000 der Diagonale)."""

    body: str = ""
    deflection: float = 0.1
    # größte Winkelabweichung der Zerlegung (rad): 0,3 grob wie bisher; zum Formen fein (Runde 94: eine Kugel
    # blieb sonst trotz feiner Abweichung grob facettiert)
    angle: float = 0.3

    KIND = "Körper in Netz umwandeln"

    def apply(self, state: BuildState) -> None:
        if self.body not in state.bodies:
            raise FeatureError(f"Körper '{self.body}' ist nicht (mehr) vorhanden" if self.body else "Bitte einen Körper wählen")
        if self.deflection <= 0:
            raise FeatureError("Die Feinheit muss größer als 0 sein")
        shape = state.bodies.pop(self.body)
        # Feinheit höchstens 1/5000 der Körperdiagonale: ein riesiger Körper mit 0,1 mm zerfiele in Millionen
        # Dreiecke und die Rechnung hinge (Zufallstest Startwert 21, weit aufgezogene Kugel)
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        box = Bnd_Box()
        BRepBndLib.Add_s(shape, box, False)
        diagonal = 0.0 if box.IsVoid() else box.SquareExtent() ** 0.5
        if not 0.01 <= self.angle <= 1.0:
            raise FeatureError("Die Winkelabweichung liegt zwischen 0,01 und 1 (Bogenmaß)")
        state.add_mesh(tessellate(shape, max(self.deflection, diagonal / 5000.0), self.angle))


@dataclass
class MeshReduce(Feature):
    """Netz verkleinern (Fusion: Reduzieren): Ecken auf ein Raster (Weite in mm) zusammenlegen."""

    mesh: str = ""
    cell: float = 1.0

    KIND = "Netz verkleinern"

    def apply(self, state: BuildState) -> None:
        triangles = _mesh_of(state, self.mesh)
        if self.cell <= 0:
            raise FeatureError("Die Rasterweite muss größer als 0 sein")
        reduced = reduce(triangles, self.cell)
        if not len(reduced):
            raise FeatureError("Bei dieser Rasterweite bliebe vom Netz nichts übrig")
        state.meshes[self.mesh] = reduced


@dataclass
class MeshRepair(Feature):
    """Netz reparieren (Fusion: Reparieren): doppelte/entartete Dreiecke weg, einheitliche Ausrichtung, Löcher zu."""

    mesh: str = ""

    KIND = "Netz reparieren"

    def apply(self, state: BuildState) -> None:
        repaired, _report = repair(_mesh_of(state, self.mesh))
        if not len(repaired):
            raise FeatureError("Vom Netz bleibt nach dem Reparieren nichts übrig")
        state.meshes[self.mesh] = repaired


@dataclass
class MeshSmooth(Feature):
    """Netz glätten (Fusion: Glätten): Durchgänge und Stärke (0 … 1)."""

    mesh: str = ""
    iterations: int = 5
    strength: float = 0.5

    KIND = "Netz glätten"

    def apply(self, state: BuildState) -> None:
        if not 0 < self.strength <= 1 or self.iterations < 1:
            raise FeatureError("Stärke zwischen 0 und 1, mindestens ein Durchgang")
        state.meshes[self.mesh] = smooth(_mesh_of(state, self.mesh), int(self.iterations), self.strength)


def triangle_count(state: BuildState, name: str) -> int:
    return len(state.meshes.get(name, ()))


# In das Verzeichnis für das Laden gespeicherter Entwürfe eintragen
for _feature_class in (MeshImport, MeshToBody, BodyToMesh, MeshReduce, MeshRepair, MeshSmooth):
    ft.FEATURE_CLASSES[_feature_class.KIND] = _feature_class

