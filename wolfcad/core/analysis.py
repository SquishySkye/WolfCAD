# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Prüfwerkzeuge wie in Fusion: Entformungs- und Wandstärkenanalyse auf den Dreiecken der Anzeige.

Beide arbeiten auf der Zerlegung, die die Ansicht ohnehin hat (Dreiecke mit nach außen zeigender Reihenfolge der
Ecken) und liefern je Dreieck eine Klasse, nach der die Ansicht einfärbt.
"""

from __future__ import annotations

import numpy as np
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

# Klassen der Entformungsanalyse und ihre Farben (Rot/Grün/Blau 0 … 1)
DRAFT_OK, DRAFT_FLAT, DRAFT_UNDERCUT = 0, 1, 2
DRAFT_COLOURS = {DRAFT_OK: (0.25, 0.72, 0.32), DRAFT_FLAT: (0.95, 0.80, 0.20), DRAFT_UNDERCUT: (0.90, 0.25, 0.22)}
# Klassen der Wandstärkenanalyse: zu dünn, knapp (bis doppelte Mindeststärke), ausreichend
THIN, TIGHT, THICK = 0, 1, 2
THICKNESS_COLOURS = {THIN: (0.90, 0.25, 0.22), TIGHT: (0.95, 0.70, 0.20), THICK: (0.30, 0.55, 0.90)}


def triangle_normals(triangles: np.ndarray) -> np.ndarray:
    """Einheitsnormalen je Dreieck (aus der Reihenfolge der Ecken, also nach außen)."""
    corners = triangles.reshape(-1, 3, 3).astype(float)
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths < 1e-15] = 1.0
    return normals / lengths[:, None]


def draft_classes(triangles: np.ndarray, direction=(0.0, 0.0, 1.0), minimum: float = 1.0) -> np.ndarray:
    """Entformung in Zugrichtung `direction`: je Dreieck OK (Schräge ≥ minimum Grad), FLACH (zu wenig Schräge,
    z. B. senkrechte Wände) oder HINTERSCHNITT (Fläche zeigt gegen die Zugrichtung)."""
    pull = np.array(direction, dtype=float)
    pull /= np.linalg.norm(pull)
    # Schrägwinkel einer Fläche: Winkel zwischen ihr und der Zugrichtung = 90° − Winkel(Normale, Zugrichtung)
    angles = np.degrees(np.arcsin(np.clip(triangle_normals(triangles) @ pull, -1.0, 1.0)))
    result = np.full(len(angles), DRAFT_FLAT, dtype=int)
    result[angles >= minimum] = DRAFT_OK
    result[angles <= -minimum] = DRAFT_UNDERCUT
    return result


def wall_thickness(shape, triangles: np.ndarray) -> np.ndarray:
    """Wandstärke je Dreieck: Abstand von seiner Mitte nach innen (gegen die Normale) bis zur nächsten Fläche.

    Ohne Gegenfläche (sollte bei geschlossenen Körpern nicht vorkommen) steht dort unendlich.
    """
    corners = triangles.reshape(-1, 3, 3).astype(float)
    centres = corners.mean(axis=1)
    normals = triangle_normals(triangles)
    finder = IntCurvesFace_ShapeIntersector()
    finder.Load(shape, 1e-6)
    result = np.full(len(centres), np.inf)
    for number, (centre, normal) in enumerate(zip(centres, normals)):
        if not np.any(normal):
            continue
        start = centre - normal * 1e-4            # knapp innen beginnen, damit die eigene Fläche nicht zählt
        finder.Perform(gp_Lin(gp_Pnt(*start), gp_Dir(*(-normal))), 0.0, 1e9)
        if finder.IsDone() and finder.NbPnt() > 0:
            result[number] = min(finder.WParameter(i) for i in range(1, finder.NbPnt() + 1)) + 1e-4
    return result


def thickness_classes(thickness: np.ndarray, minimum: float) -> np.ndarray:
    result = np.full(len(thickness), THICK, dtype=int)
    result[thickness < 2 * minimum] = TIGHT
    result[thickness < minimum] = THIN
    return result


# Klassen der Krümmungsanalyse: eben, sanft gekrümmt, eng gekrümmt (Radius unter dem Grenzwert)
FLAT, GENTLE, TIGHT_CURVE = 0, 1, 2
CURVATURE_COLOURS = {FLAT: (0.55, 0.62, 0.72), GENTLE: (0.30, 0.65, 0.85), TIGHT_CURVE: (0.90, 0.35, 0.25)}


def curvature_radii(faces: list, triangles: np.ndarray, triangle_face: np.ndarray) -> np.ndarray:
    """Kleinster Krümmungsradius je Dreieck (aus der Fläche an der Dreiecksmitte); eben = unendlich."""
    from OCP.BRep import BRep_Tool
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.GeomLProp import GeomLProp_SLProps
    from OCP.TopoDS import TopoDS
    centres = triangles.reshape(-1, 3, 3).astype(float).mean(axis=1)
    radii = np.full(len(centres), np.inf)
    surfaces = {}
    for number, (centre, face_index) in enumerate(zip(centres, triangle_face)):
        if face_index not in surfaces:
            surfaces[face_index] = BRep_Tool.Surface_s(TopoDS.Face(faces[face_index]))
        surface = surfaces[face_index]
        projector = GeomAPI_ProjectPointOnSurf(gp_Pnt(*centre), surface)
        if projector.NbPoints() < 1:
            continue
        u, v = projector.LowerDistanceParameters()
        properties = GeomLProp_SLProps(surface, u, v, 2, 1e-6)
        if not properties.IsCurvatureDefined():
            continue
        largest = max(abs(properties.MaxCurvature()), abs(properties.MinCurvature()))
        if largest > 1e-9:
            radii[number] = 1.0 / largest
    return radii


def curvature_classes(radii: np.ndarray, minimum: float) -> np.ndarray:
    result = np.full(len(radii), GENTLE, dtype=int)
    result[~np.isfinite(radii)] = FLAT
    result[radii < minimum] = TIGHT_CURVE
    return result


# Zebra-Analyse wie in Fusion: abwechselnd helle und dunkle Streifen; laufen sie über eine Kante glatt weiter,
# gehen die Flächen tangential ineinander über, knicken sie oder springen sie, ist dort ein Knick bzw. Sprung.
ZEBRA_DARK, ZEBRA_LIGHT = 0, 1
ZEBRA_COLOURS = {ZEBRA_DARK: (0.12, 0.13, 0.16), ZEBRA_LIGHT: (0.93, 0.94, 0.96)}


def zebra_classes(triangles: np.ndarray, axis=(0.0, 0.0, 1.0), width: float = 10.0, view=None) -> np.ndarray:
    """Streifen je Dreieck in Bändern von `width` Grad.

    Mit Blickrichtung `view` wie in Fusion: der Blickstrahl wird an der Fläche gespiegelt und trifft auf
    eine Umgebung aus waagerechten Leuchtstreifen (quer zu `axis`) – die Streifen wandern beim Drehen mit.
    Ohne `view` zählt nur die Neigung der Normale gegen `axis` (unabhängig von der Kamera).
    """
    if width <= 0:
        raise ValueError("Die Streifenbreite muss größer als 0 sein")
    direction = np.array(axis, dtype=float)
    direction /= np.linalg.norm(direction)
    normals = triangle_normals(triangles)
    if view is not None:
        eye = np.array(view, dtype=float)
        eye /= np.linalg.norm(eye)
        # gespiegelter Blickstrahl: r = v − 2 (v·n) n
        normals = eye[None, :] - 2.0 * (normals @ eye)[:, None] * normals
    angles = np.degrees(np.arcsin(np.clip(normals @ direction, -1.0, 1.0))) + 90.0
    return (np.floor(angles / width).astype(int) % 2).astype(int)


def face_normal(face) -> tuple[float, float, float]:
    """Nach außen zeigende Normale einer Körperfläche in der Mitte ihres Parameterbereichs (auch gekrümmt).

    Der Merkpunkt (Schwerpunkt) taugt dafür nicht: bei einer Zylinderwand liegt er auf der Achse.
    """
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Vec
    from OCP.TopoDS import TopoDS
    face = TopoDS.Face(face)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    where, normal = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal((u0 + u1) / 2, (v0 + v1) / 2, where, normal)     # beachtet die Ausrichtung
    if normal.Magnitude() < 1e-12:
        raise ValueError("An dieser Stelle hat die Fläche keine eindeutige Normale")
    normal.Normalize()
    return normal.X(), normal.Y(), normal.Z()


def smooth_normals(triangles: np.ndarray, triangle_face: np.ndarray) -> np.ndarray:
    """Normale je Dreiecksecke (n, 3, 3): innerhalb einer Fläche gemittelt über alle Dreiecke an derselben Ecke.

    So verläuft die Normale über eine gekrümmte Fläche glatt; an Flächengrenzen bleibt sie getrennt (dort zeigt
    die Zebra-Analyse, ob der Übergang tangential ist).
    """
    corners = triangles.reshape(-1, 3, 3).astype(float)
    flat = triangle_normals(triangles)
    # gleiche Ecke = gleiche Fläche und (gerundet) gleiche Lage
    keys = np.concatenate([np.repeat(triangle_face, 3)[:, None].astype(float),
                           np.round(corners.reshape(-1, 3), 5)], axis=1)
    _unique, groups = np.unique(keys, axis=0, return_inverse=True)
    groups = groups.reshape(-1)
    sums = np.zeros((groups.max() + 1, 3))
    np.add.at(sums, groups, np.repeat(flat, 3, axis=0))
    lengths = np.linalg.norm(sums, axis=1)
    lengths[lengths < 1e-15] = 1.0
    return (sums / lengths[:, None])[groups].reshape(-1, 3, 3)


def zebra_mesh(triangles: np.ndarray, triangle_face: np.ndarray, axis=(0.0, 0.0, 1.0), width: float = 10.0,
               view=None, levels: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Feine Zebra-Streifen: jedes Dreieck wird 4^levels-fach unterteilt, die Normale über die Ecken interpoliert.

    Zurück kommen die Teildreiecke (m·3, 3) und ihre Streifenklasse (m,). Die Form bleibt dieselbe; nur die
    Färbung folgt der glatten Normale – so sind die Streifen auch auf grob zerlegten Rundungen glatt.
    """
    if width <= 0:
        raise ValueError("Die Streifenbreite muss größer als 0 sein")
    corners = triangles.reshape(-1, 3, 3).astype(float)
    normals = smooth_normals(triangles, triangle_face)
    # baryzentrische Teilung: Gitterpunkte (i, j) mit i + j ≤ n, daraus die Teildreiecke
    n = 2 ** levels
    small = []
    for i in range(n):
        for j in range(n - i):
            small.append(((i, j), (i + 1, j), (i, j + 1)))
            if i + j < n - 1:
                small.append(((i + 1, j), (i + 1, j + 1), (i, j + 1)))
    weights = np.array([[(n - i - j) / n, i / n, j / n] for triangle in small for i, j in triangle])  # (k·3, 3)
    positions = np.einsum("kc,tcd->tkd", weights, corners).reshape(-1, 3)
    centre_weights = weights.reshape(-1, 3, 3).mean(axis=1)                                       # (k, 3)
    centre_normals = np.einsum("kc,tcd->tkd", centre_weights, normals).reshape(-1, 3)
    lengths = np.linalg.norm(centre_normals, axis=1)
    lengths[lengths < 1e-15] = 1.0
    centre_normals /= lengths[:, None]
    direction = np.array(axis, dtype=float)
    direction /= np.linalg.norm(direction)
    if view is not None:
        eye = np.array(view, dtype=float)
        eye /= np.linalg.norm(eye)
        centre_normals = eye[None, :] - 2.0 * (centre_normals @ eye)[:, None] * centre_normals
    angles = np.degrees(np.arcsin(np.clip(centre_normals @ direction, -1.0, 1.0))) + 90.0
    return positions.astype(np.float32), (np.floor(angles / width).astype(int) % 2)


def zebra_surface(faces: list, triangles: np.ndarray, triangle_face: np.ndarray,
                  levels: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Teildreiecke (wie `zebra_mesh`) mit der echten Flächennormale an ihrer Mitte: (Punkte m·3×3, Normalen m×3).

    Die Normale kommt aus der Fläche selbst (nicht aus der Zerlegung) – so laufen die Streifen exakt glatt.
    Ebene Flächen haben überall dieselbe Normale und werden nur einmal ausgewertet. Das Ergebnis hängt nicht
    von der Blickrichtung ab und lässt sich deshalb beim Drehen weiterverwenden (`zebra_from_normals`).
    """
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Vec
    positions, _classes = zebra_mesh(triangles, triangle_face, levels=levels)
    per_triangle = 4 ** levels
    centres = positions.reshape(-1, 3, 3).astype(float).mean(axis=1)
    owners = np.repeat(triangle_face, per_triangle)
    normals = np.zeros((len(centres), 3))
    flat = np.repeat(triangle_normals(triangles), per_triangle, axis=0)
    for number in np.unique(owners):
        chosen = np.nonzero(owners == number)[0]
        face = TopoDS.Face(faces[number])
        if BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane:
            normals[chosen] = flat[chosen[0]]           # eben: eine Normale für alle
            continue
        projector, properties = ShapeAnalysis_Surface(BRep_Tool.Surface_s(face)), BRepGProp_Face(face)
        where, normal = gp_Pnt(), gp_Vec()
        for index in chosen:
            uv = projector.ValueOfUV(gp_Pnt(*centres[index]), 1e-4)
            properties.Normal(uv.X(), uv.Y(), where, normal)    # beachtet die Ausrichtung der Fläche
            normals[index] = (normal.X(), normal.Y(), normal.Z())
    lengths = np.linalg.norm(normals, axis=1)
    bad = lengths < 1e-12
    normals[bad], lengths[bad] = flat[bad], 1.0         # entartete Stelle (z. B. Kegelspitze): Facettennormale
    return positions, normals / lengths[:, None]


def zebra_from_normals(normals: np.ndarray, axis=(0.0, 0.0, 1.0), width: float = 10.0, view=None) -> np.ndarray:
    """Streifenklasse je Normale (gespiegelter Blickstrahl gegen waagerechte Leuchtstreifen, siehe zebra_classes)."""
    if width <= 0:
        raise ValueError("Die Streifenbreite muss größer als 0 sein")
    direction = np.array(axis, dtype=float)
    direction /= np.linalg.norm(direction)
    if view is not None:
        eye = np.array(view, dtype=float)
        eye /= np.linalg.norm(eye)
        normals = eye[None, :] - 2.0 * (normals @ eye)[:, None] * normals
    angles = np.degrees(np.arcsin(np.clip(normals @ direction, -1.0, 1.0))) + 90.0
    return np.floor(angles / width).astype(int) % 2


def iso_lines(faces, count: int = 5, steps: int = 32) -> np.ndarray:
    """Isokurven (Fusion: Isokurvenanalyse): je Fläche `count` Linien konstanter u- und v-Parameter, als
    Liniensegmente (Paare von Punkten, n × 3). Nur die Stücke innerhalb der Flächenbegrenzung werden gezeigt."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.IntTools import IntTools_FClass2d
    from OCP.TopAbs import TopAbs_OUT
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt2d
    segments = []
    for face in faces:
        face = TopoDS.Face(face)
        surface = BRepAdaptor_Surface(face)
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
        inside = IntTools_FClass2d(face, 1e-7)
        for direction in (0, 1):
            for k in range(1, count + 1):
                fixed = (u0 + (u1 - u0) * k / (count + 1)) if direction == 0 else (v0 + (v1 - v0) * k / (count + 1))
                previous = None
                for i in range(steps + 1):
                    running = (v0 + (v1 - v0) * i / steps) if direction == 0 else (u0 + (u1 - u0) * i / steps)
                    u, v = (fixed, running) if direction == 0 else (running, fixed)
                    if inside.Perform(gp_Pnt2d(u, v)) == TopAbs_OUT:
                        previous = None
                        continue
                    point = surface.Value(u, v)
                    current = (point.X(), point.Y(), point.Z())
                    if previous is not None:
                        segments += [previous, current]
                    previous = current
    return np.array(segments, dtype=np.float32).reshape(-1, 3)
