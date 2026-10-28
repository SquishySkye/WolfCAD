# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Zerlegt OpenCascade-Formen in Dreiecke und Linien für die 3D-Anzeige."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape

_EMPTY = np.zeros((0, 3), dtype=np.float32)


def _explore(shape: TopoDS_Shape, kind):
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        yield explorer.Current()
        explorer.Next()


def triangles(shape: TopoDS_Shape | None, deflection: float = 0.1) -> np.ndarray:
    """Eckpunkte aller Flächendreiecke als Array (n*3, 3), je drei Zeilen ein Dreieck."""
    if shape is None:
        return _EMPTY
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.3, True)
    points: list[tuple[float, float, float]] = []
    for item in _explore(shape, TopAbs_FACE):
        face = TopoDS.Face(item)
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, location)
        if mesh is None:
            continue
        transform = location.Transformation()
        nodes = [mesh.Node(i).Transformed(transform) for i in range(1, mesh.NbNodes() + 1)]
        coords = [(p.X(), p.Y(), p.Z()) for p in nodes]
        # Bei umgekehrten Flächen die Umlaufrichtung drehen, damit die Normale nach außen zeigt
        flip = face.Orientation() == TopAbs_REVERSED
        for i in range(1, mesh.NbTriangles() + 1):
            a, b, c = mesh.Triangle(i).Get()
            if flip:
                b, c = c, b
            points += (coords[a - 1], coords[b - 1], coords[c - 1])
    return np.array(points, dtype=np.float32).reshape(-1, 3)


def lines(shape: TopoDS_Shape | None, deflection: float = 0.05) -> np.ndarray:
    """Kanten als Liniensegmente: Array (n*2, 3), je zwei Zeilen ein Segment."""
    if shape is None:
        return _EMPTY
    # gemerkte Abtastung je Kante (siehe _edge_line): bei jeder Aktualisierung kommen alle Skizzen des Modells
    # vorbei, geändert hat sich meist keine
    pieces = [_edge_line(TopoDS.Edge(item), deflection)[1] for item in _explore(shape, TopAbs_EDGE)]
    return np.concatenate(pieces) if pieces else _EMPTY


@dataclass
class PickMesh:
    """Zerlegung eines Körpers, bei der jedes Dreieck seine Fläche und jedes Liniensegment
    seine Kante kennt (für Anzeige, Treffersuche und Hervorhebung)."""

    triangles: np.ndarray       # (n*3, 3)
    triangle_face: np.ndarray   # (n,) Index in face_points
    face_points: list           # Merkpunkt je Fläche (siehe features.face_point)
    segments: np.ndarray        # (m*2, 3)
    segment_edge: np.ndarray    # (m,) Index in edge_points
    edge_points: list           # Merkpunkt je Kante (siehe features.edge_point)


def display_deflection(shape: TopoDS_Shape, finest: float = 0.1) -> float:
    """Zulässige Abweichung der Anzeige-Zerlegung: höchstens 1/1000 der Körperdiagonale, mindestens `finest` mm.

    Eine feste Abweichung ließe riesige Körper (z. B. eine weit aufgezogene Kugel) in Millionen Dreiecke
    zerfallen – die Zerlegung dauerte dann Minuten. Normale Bauteile (bis 10 cm) behalten 0,1 mm.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, False)         # ohne Zerlegung: nur die Kurven und Flächen
    if box.IsVoid():
        return finest
    return max(finest, math.sqrt(box.SquareExtent()) / 1000.0)


# Zerlegungen je Formkern (Runde 85): Kopien aus Muster, Kreismuster, Einfügen teilen den Formkern und unterscheiden
# sich nur in der Lage. Ihre Zerlegung entsteht aus der gemerkten durch Verschieben/Drehen statt neu – beim
# Kreismuster mit vielen Kopien war das Neuzerlegen jeder Kopie ein großer Teil der Zeit.
# Schlüssel: Hash des Formkerns ohne Lage -> [(Form, Zerlegung)]
_PARTNER_MESHES: dict[int, list] = {}
_PARTNER_LIMIT = 400


def pick_mesh(shape: TopoDS_Shape, deflection: float | None = None) -> PickMesh:
    """Zerlegt einen Körper und merkt sich zu jedem Dreieck und Segment die Herkunft."""
    from ..core import features as ft

    reused = _from_partner(shape) if deflection is None else None
    if reused is not None:
        return reused
    own_deflection = deflection is None
    if deflection is None:
        deflection = display_deflection(shape)
    found = _pick_mesh(shape, deflection, ft)
    if own_deflection:
        _remember_partner(shape, found)
    return found


def _partner_key(shape: TopoDS_Shape) -> int:
    return hash(shape.Located(TopLoc_Location()))


def _from_partner(shape: TopoDS_Shape) -> PickMesh | None:
    """Zerlegung einer Form mit demselben Formkern und derselben Ausrichtung, in die Lage dieser Form gebracht."""
    for other, pick in _PARTNER_MESHES.get(_partner_key(shape), ()):
        if not shape.IsPartner(other) or shape.Orientation() != other.Orientation():
            continue
        if shape.IsEqual(other):
            return pick
        # Abbildung von der Lage der gemerkten Form in die Lage dieser Form
        move = shape.Location().Transformation().Multiplied(other.Location().Transformation().Inverted())
        return _moved(pick, move)
    return None


def _remember_partner(shape: TopoDS_Shape, pick: PickMesh) -> None:
    if sum(len(entries) for entries in _PARTNER_MESHES.values()) > _PARTNER_LIMIT:
        _PARTNER_MESHES.clear()             # einfach neu beginnen; die Liste füllt sich beim nächsten Zeichnen wieder
    _PARTNER_MESHES.setdefault(_partner_key(shape), []).append((shape, pick))


def _moved(pick: PickMesh, move) -> PickMesh:
    """Die Zerlegung mit einer starren Abbildung (gp_Trsf) bewegt: Punkte, Merkpunkte; die Zuordnung bleibt."""
    matrix = np.array([[move.Value(row, column) for column in range(1, 5)] for row in range(1, 4)])
    rotation, offset = matrix[:, :3], matrix[:, 3]
    apply = lambda points: (np.asarray(points, dtype=np.float64) @ rotation.T + offset).astype(np.float32)
    point_list = lambda points: [tuple(map(float, row)) for row in apply(points)] if len(points) else []
    return PickMesh(apply(pick.triangles), pick.triangle_face, point_list(pick.face_points), apply(pick.segments),
                    pick.segment_edge, point_list(pick.edge_points))


def pick_bounds(pick: PickMesh) -> np.ndarray:
    """Hüllquader der Zerlegung als zwei Punkte (2, 3), gemerkt an der Zerlegung (Runde 85: vorher wurde bei jedem
    Neuzeichnen über alle Dreiecke aller Körper das Minimum und Maximum gesucht)."""
    bounds = getattr(pick, "_bounds", None)
    if bounds is None:
        bounds = (np.array([pick.triangles.min(axis=0), pick.triangles.max(axis=0)], dtype=np.float32)
                  if len(pick.triangles) else np.zeros((0, 3), dtype=np.float32))
        pick._bounds = bounds
    return bounds


def _pick_mesh(shape: TopoDS_Shape, deflection: float, ft) -> PickMesh:
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.3, True)
    faces = ft.faces_of(shape)
    found = _triangles_via_stl(shape, faces)
    if found is not None:
        points_array, owners_array = found
    else:
        points_array, owners_array = _triangles_by_loop(faces)
    return _with_edges(shape, faces, points_array, owners_array, deflection)


# Binäres STL: 80 Byte Kopf, Anzahl, dann je Dreieck Normale, drei Eckpunkte und zwei Byte Zusatz
_STL_TRIANGLE = np.dtype([("normal", "<f4", 3), ("points", "<f4", (3, 3)), ("attribute", "<u2")])


def _triangles_via_stl(shape: TopoDS_Shape, faces: list):
    """Alle Dreiecke eines zerlegten Körpers auf einmal: der STL-Schreiber des Kerns sammelt sie in C++ (mit Lage
    und Flächenausrichtung), numpy liest sie in einem Schritt. Die Fläche je Dreieck folgt aus der Dreieckszahl je
    Fläche in derselben Reihenfolge. Rund 90-mal schneller als die Python-Schleife (312 Körper: 15 → 0,17 s).

    None, wenn etwas nicht zusammenpasst (dann rechnet `_triangles_by_loop` wie früher).
    """
    import os
    import tempfile
    from OCP.StlAPI import StlAPI_Writer
    counts = []
    for face in faces:
        triangulation = BRep_Tool.Triangulation_s(face, TopLoc_Location())
        counts.append(0 if triangulation is None else triangulation.NbTriangles())
    folder = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()       # im Arbeitsspeicher
    path = os.path.join(folder, f"wolfcad_zerlegung_{os.getpid()}.stl")
    try:
        writer = StlAPI_Writer()
        writer.ASCIIMode = False
        if not writer.Write(shape, path):
            return None
        data = np.fromfile(path, dtype=_STL_TRIANGLE, offset=84)
    except Exception:
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if len(data) != sum(counts):
        return None
    return (np.ascontiguousarray(data["points"].reshape(-1, 3), dtype=np.float32),
            np.repeat(np.arange(len(counts), dtype=np.int64), counts))


def _triangles_by_loop(faces: list):
    """Dreiecke Fläche für Fläche in Python (langsamer Rückfall für `_triangles_via_stl`)."""
    points: list[tuple[float, float, float]] = []
    owners: list[int] = []
    for index, face in enumerate(faces):
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, location)
        if mesh is None:
            continue
        transform = location.Transformation()
        nodes = [mesh.Node(i).Transformed(transform) for i in range(1, mesh.NbNodes() + 1)]
        coords = [(p.X(), p.Y(), p.Z()) for p in nodes]
        flip = face.Orientation() == TopAbs_REVERSED
        for i in range(1, mesh.NbTriangles() + 1):
            a, b, c = mesh.Triangle(i).Get()
            if flip:
                b, c = c, b
            points += (coords[a - 1], coords[b - 1], coords[c - 1])
            owners.append(index)
    return np.array(points, dtype=np.float32).reshape(-1, 3), np.array(owners, dtype=np.int64)


# Abgetastete Kanten je Kante gemerkt (Runde 79): Nach einer Änderung bleiben die meisten Kanten eines Körpers
# dieselben Objekte des Kerns – nur neue Kanten werden abgetastet (Live-Bearbeiten im Nutzerentwurf: ~7 ms je Körper
# gespart). Schlüssel: Hash der Kante; IsEqual prüft, ob es wirklich dieselbe Kante ist.
_EDGE_LINES: dict[int, list] = {}
_EDGE_LINES_LIMIT = 60000
_NO_SEGMENTS = np.zeros((0, 3), dtype=np.float32)


def _edge_line(edge, deflection: float) -> tuple[tuple, np.ndarray]:
    """(Merkpunkt, Strecken als Punktpaare) einer Kante, abgetastet mit der Sehnenabweichung `deflection`;
    entartete Kanten ohne Kurve: (NaN-Punkt, keine Strecken)."""
    from ..core import features as ft
    key = hash(edge)
    for known, step, found in _EDGE_LINES.get(key, ()):
        if step == deflection and known.IsEqual(edge):
            return found
    try:
        point = ft.edge_point(edge)
        sampler = GCPnts_TangentialDeflection(BRepAdaptor_Curve(edge), 0.2, deflection)
        polyline = np.array([sampler.Value(i).Coord() for i in range(1, sampler.NbPoints() + 1)], dtype=np.float32)
        # je zwei aufeinanderfolgende Punkte bilden eine Strecke: (p0, p1), (p1, p2), …
        pairs = np.stack([polyline[:-1], polyline[1:]], axis=1).reshape(-1, 3) if len(polyline) > 1 else _NO_SEGMENTS
        found = (point, pairs)
    except Exception:
        found = ((math.nan, math.nan, math.nan), _NO_SEGMENTS)
    if len(_EDGE_LINES) > _EDGE_LINES_LIMIT:
        _EDGE_LINES.clear()
    _EDGE_LINES.setdefault(key, []).append((edge, deflection, found))
    return found


def _with_edges(shape: TopoDS_Shape, faces: list, points: np.ndarray, owners: np.ndarray, deflection: float) -> "PickMesh":
    """Ergänzt die Dreiecke um die Kantenzüge und Merkpunkte zu einer PickMesh."""
    from ..core import features as ft
    lines = [_edge_line(edge, deflection / 2) for edge in ft.unique_edges(shape)]      # Kanten feiner als Flächen
    edge_points = [point for point, _pairs in lines]
    pieces = [pairs for _point, pairs in lines]
    segments = np.concatenate(pieces) if pieces else _NO_SEGMENTS
    # Eigentümer je Strecke (eine Strecke = zwei Punkte)
    segment_owners = np.repeat(np.arange(len(pieces), dtype=np.int64), [len(pairs) // 2 for pairs in pieces])
    return PickMesh(
        points, owners,
        [ft.face_point(face) for face in faces],
        np.ascontiguousarray(segments, dtype=np.float32).reshape(-1, 3), segment_owners,
        edge_points)
