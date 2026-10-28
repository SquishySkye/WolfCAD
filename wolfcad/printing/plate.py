# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Das Druckbett im Druck-Reiter: Objekte als Dreiecksnetze mit Lage, Drehung und Maßstab – wie die Platte in
Creality Print (Objekte hinzufügen, verschieben, drehen, skalieren, auf eine Fläche legen, automatisch anordnen,
vervielfältigen). Reine Rechnung ohne Oberfläche, damit sie sich ohne Fenster prüfen lässt.

Jedes Objekt merkt sich sein Netz in eigenen Koordinaten (Mitte im Ursprung, Unterseite bei z = 0) und eine Drehung
als 3×3-Matrix; die Lage auf dem Bett ist (x, y) der Mitte. Nach jeder Drehung liegt das Objekt wieder auf dem Bett.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

GAP = 6.0           # Abstand zwischen Objekten beim automatischen Anordnen (mm), wie die Vorgabe des Originals


@dataclass
class PlateObject:
    name: str
    mesh: np.ndarray                                    # (n, 3, 3) Dreiecke in eigenen Koordinaten
    x: float = 0.0
    y: float = 0.0
    rotation: np.ndarray = field(default_factory=lambda: np.eye(3))
    scale: float = 1.0
    filament: int = 0                                   # Nummer des Filaments (CFS-Fach) für dieses Objekt
    settings: dict = field(default_factory=dict)        # Einstellungen nur für dieses Objekt (z. B. Fülldichte)
    # gemalte Bereiche je Dreieck (wie „Stützen malen“/„Farbe malen“ im Original): 0 = nichts,
    # Stützen 1 = erzwingen, 2 = blockieren; Farbe = Filamentnummer ab 1
    support_paint: np.ndarray | None = None
    color_paint: np.ndarray | None = None

    def placed(self) -> np.ndarray:
        """Die Dreiecke an ihrer Lage auf dem Bett (Unterseite bei z = 0)."""
        transform = self.rotation.T * self.scale
        points = self.mesh.reshape(-1, 3) @ transform
        points[:, 2] -= points[:, 2].min()
        centre = (points[:, :2].min(0) + points[:, :2].max(0)) / 2
        points[:, 0] += self.x - centre[0]
        points[:, 1] += self.y - centre[1]
        placed = points.reshape(-1, 3, 3)
        if np.linalg.det(self.rotation) < 0:
            placed = placed[:, [0, 2, 1]]               # gespiegelt: Umlaufsinn umkehren, sonst zeigt innen nach außen
        return placed

    def mirror(self, axis: str) -> None:
        """Spiegelt an der Ebene durch die Objektmitte senkrecht zur Bettachse."""
        flip = np.eye(3)
        flip["XYZ".index(axis.upper()), "XYZ".index(axis.upper())] = -1.0
        self.rotation = flip @ self.rotation

    def set_mesh(self, triangles: np.ndarray) -> None:
        """Neues Netz in Bettkoordinaten übernehmen (nach Schneiden, Vereinfachen …): Lage bleibt, Drehung und
        Maßstab sind darin schon enthalten, Bemalung entfällt."""
        triangles = np.asarray(triangles, float).reshape(-1, 3, 3)
        points = triangles.reshape(-1, 3)
        low, high = points.min(0), points.max(0)
        self.x, self.y = float((low[0] + high[0]) / 2), float((low[1] + high[1]) / 2)
        mesh = triangles.copy()
        mesh.reshape(-1, 3)[:, :2] -= (low[:2] + high[:2]) / 2
        mesh.reshape(-1, 3)[:, 2] -= low[2]
        self.mesh, self.rotation, self.scale = mesh, np.eye(3), 1.0
        self.support_paint = self.color_paint = None

    def subdivide(self, max_edge: float, limit: int = 300_000) -> None:
        """Teilt große Dreiecke (längste Kante halbieren), bis keine Kante länger als `max_edge` ist – damit sich
        fein malen lässt. Bemalung geht auf die Teile über."""
        mesh = self.mesh
        paints = [self.support_paint, self.color_paint]
        while len(mesh) < limit:
            edges = np.stack([np.linalg.norm(mesh[:, (i + 1) % 3] - mesh[:, i], axis=1) for i in range(3)], 1)
            longest = edges.argmax(1)
            split = edges.max(1) > max_edge / max(self.scale, 1e-9)
            if not split.any() or len(mesh) + split.sum() > limit:
                break
            keep, cut = mesh[~split], mesh[split]
            order = longest[split]
            a = cut[np.arange(len(cut)), order]
            b = cut[np.arange(len(cut)), (order + 1) % 3]
            c = cut[np.arange(len(cut)), (order + 2) % 3]
            middle = (a + b) / 2
            mesh = np.concatenate([keep, np.stack([a, middle, c], 1), np.stack([middle, b, c], 1)])
            paints = [None if paint is None else np.concatenate([paint[~split], paint[split], paint[split]])
                      for paint in paints]
        self.mesh = mesh
        self.support_paint, self.color_paint = paints

    def paint(self, which: str, centre, radius: float, value: int) -> int:
        """Malt alle Dreiecke, deren Mitte (auf dem Bett) im Pinselkreis um `centre` liegt; gibt ihre Zahl zurück."""
        middles = self.placed().mean(1)
        hit = np.linalg.norm(middles - np.asarray(centre, float), axis=1) <= radius
        name = "support_paint" if which == "support" else "color_paint"
        current = getattr(self, name)
        if current is None:
            current = np.zeros(len(self.mesh), np.int8)
        current[hit] = value
        setattr(self, name, current if current.any() else None)
        return int(hit.sum())

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        points = self.placed().reshape(-1, 3)
        return points.min(0), points.max(0)

    def size(self) -> np.ndarray:
        low, high = self.bounds()
        return high - low

    def rotate(self, axis: str, degrees: float) -> None:
        """Dreht um eine Bettachse durch die Objektmitte; danach liegt es wieder auf dem Bett."""
        angle = math.radians(degrees)
        c, s = math.cos(angle), math.sin(angle)
        matrix = {"X": [[1, 0, 0], [0, c, -s], [0, s, c]],
                  "Y": [[c, 0, s], [0, 1, 0], [-s, 0, c]],
                  "Z": [[c, -s, 0], [s, c, 0], [0, 0, 1]]}[axis.upper()]
        self.rotation = np.asarray(matrix, float) @ self.rotation

    def lay_on(self, normal) -> None:
        """Legt das Objekt auf die Fläche mit der Außennormalen `normal` (in Bettkoordinaten): sie zeigt danach
        nach unten (Creality Print: „Auf Fläche legen“)."""
        n = np.asarray(normal, float)
        n /= np.linalg.norm(n)
        target = np.array([0.0, 0.0, -1.0])
        axis = np.cross(n, target)
        sin, cos = np.linalg.norm(axis), float(n @ target)
        if sin < 1e-9:
            turn = np.eye(3) if cos > 0 else np.diag([1.0, -1.0, -1.0])
        else:
            k = axis / sin
            K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
            turn = np.eye(3) + sin * K + (1 - cos) * K @ K
        self.rotation = turn @ self.rotation

    def support_faces(self, min_share: float = 0.002) -> list[tuple[np.ndarray, np.ndarray, float]]:
        """Flächen, auf die sich das Objekt legen lässt (wie „Auf Fläche legen“ im Original): die ebenen Flächen
        seiner konvexen Hülle – nur auf ihnen kann es stabil aufliegen; eine Fläche in einer Vertiefung kann es nicht.
        Je Fläche (Außennormale, Dreiecke (k, 3, 3) in Bettkoordinaten, Flächeninhalt); winzige Facetten (unter
        `min_share` der Hüllfläche, z. B. an Rundungen) fallen weg."""
        from scipy.spatial import ConvexHull
        points = np.unique(self.placed().reshape(-1, 3).round(6), axis=0)
        if len(points) < 4:
            return []
        try:
            hull = ConvexHull(points)
        except Exception:                   # flach oder entartet: keine Hülle
            return []
        size = float(np.linalg.norm(points.max(0) - points.min(0))) or 1.0
        groups: dict[tuple, list] = {}
        for simplex, equation in zip(hull.simplices, hull.equations):
            normal, offset = equation[:3], equation[3]
            # gleiche Ebene: Normale und Abstand gerundet (Abstand relativ zur Objektgröße)
            key = (*np.round(normal, 3), round(float(offset) / size, 4))
            groups.setdefault(key, [normal, []])[1].append(points[simplex])
        found = []
        for normal, triangles in groups.values():
            triangles = np.array(triangles)
            area = float(np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                                 triangles[:, 2] - triangles[:, 0]), axis=1).sum() / 2)
            found.append((np.asarray(normal, float), triangles, area))
        total = sum(area for _normal, _triangles, area in found) or 1.0
        return sorted([entry for entry in found if entry[2] >= min_share * total], key=lambda entry: -entry[2])

    def flat_faces(self, limit: int = 8) -> list[tuple[np.ndarray, float]]:
        """Die größten ebenen Bereiche (Normale, Fläche) – Vorschläge für „Auf Fläche legen“."""
        triangles = self.placed()
        cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        areas = np.linalg.norm(cross, axis=1) / 2
        keep = areas > 1e-9
        normals = cross[keep] / (2 * areas[keep, None])
        groups: dict[tuple, list] = {}
        for normal, area in zip(np.round(normals, 3), areas[keep]):
            groups.setdefault(tuple(normal), [np.zeros(3), 0.0])
            groups[tuple(normal)][0] += normal * area
            groups[tuple(normal)][1] += area
        ranked = sorted(((vector / np.linalg.norm(vector), area) for vector, area in groups.values()),
                        key=lambda item: -item[1])
        return ranked[:limit]


@dataclass
class Plate:
    width: float = 350.0
    depth: float = 350.0
    height: float = 350.0
    objects: list[PlateObject] = field(default_factory=list)

    def add(self, name: str, triangles: np.ndarray) -> PlateObject:
        """Nimmt ein Netz (Weltkoordinaten aus dem Entwurf) auf; es kommt mittig aufs Bett bzw. neben die
        vorhandenen Objekte (danach anordnen)."""
        mesh = np.asarray(triangles, float).reshape(-1, 3, 3).copy()
        points = mesh.reshape(-1, 3)
        low, high = points.min(0), points.max(0)
        points[:, :2] -= (low[:2] + high[:2]) / 2
        points[:, 2] -= low[2]
        item = PlateObject(self._unique(name), mesh, self.width / 2, self.depth / 2)
        self.objects.append(item)
        if len(self.objects) > 1:
            self.arrange()
        return item

    def _unique(self, name: str) -> str:
        taken = {item.name for item in self.objects}
        if name not in taken:
            return name
        number = 2
        while f"{name} ({number})" in taken:
            number += 1
        return f"{name} ({number})"

    def duplicate(self, item: PlateObject, count: int = 1) -> list[PlateObject]:
        copies = []
        for _ in range(count):
            copy = PlateObject(self._unique(item.name), item.mesh, item.x, item.y, item.rotation.copy(), item.scale,
                               item.filament, dict(item.settings),
                               None if item.support_paint is None else item.support_paint.copy(),
                               None if item.color_paint is None else item.color_paint.copy())
            self.objects.append(copy)
            copies.append(copy)
        self.arrange()
        return copies

    def remove(self, item: PlateObject) -> None:
        self.objects.remove(item)

    def arrange(self) -> bool:
        """Ordnet alle Objekte in Reihen auf dem Bett an (größte zuerst, von der Mitte aus verteilt).
        False, wenn nicht alle auf das Bett passen."""
        order = sorted(self.objects, key=lambda item: -float(np.prod(item.size()[:2])))
        rows: list[list[PlateObject]] = [[]]
        row_width, fits = 0.0, True
        for item in order:
            width = float(item.size()[0])
            if rows[-1] and row_width + GAP + width > self.width:
                rows.append([])
                row_width = 0.0
            row_width += (GAP if rows[-1] else 0.0) + width
            rows[-1].append(item)
        total_depth = sum(max(float(item.size()[1]) for item in row) for row in rows) + GAP * (len(rows) - 1)
        y = (self.depth + total_depth) / 2
        for row in rows:
            depth = max(float(item.size()[1]) for item in row)
            width = sum(float(item.size()[0]) for item in row) + GAP * (len(row) - 1)
            x = (self.width - width) / 2
            for item in row:
                size = item.size()
                item.x, item.y = x + size[0] / 2, y - depth / 2
                x += size[0] + GAP
            y -= depth + GAP
        for item in self.objects:
            fits = fits and self.inside(item)
        return fits

    def inside(self, item: PlateObject) -> bool:
        low, high = item.bounds()
        return bool(low[0] >= -1e-6 and low[1] >= -1e-6 and high[0] <= self.width + 1e-6
                    and high[1] <= self.depth + 1e-6 and high[2] <= self.height + 1e-6)

    def problems(self) -> list[str]:
        """Hinweise wie im Original vor dem Slicen (Objekt außerhalb des Bauraums …)."""
        return [f"„{item.name}“ liegt nicht vollständig im Bauraum" for item in self.objects if not self.inside(item)]

    def split_parts(self, item: PlateObject) -> list[PlateObject]:
        """„In Objekte zerlegen“: getrennte Teile eines Netzes werden eigene Objekte (an ihrer Stelle)."""
        parts = connected_parts(item.placed())
        if len(parts) < 2:
            return [item]
        index = self.objects.index(item)
        self.objects.remove(item)
        created = []
        for number, part in enumerate(parts, 1):
            piece = PlateObject(self._unique(f"{item.name} {number}"), part, filament=item.filament,
                                settings=dict(item.settings))
            piece.set_mesh(part)
            self.objects.insert(index + number - 1, piece)
            created.append(piece)
        return created

    def cut(self, item: PlateObject, height: float, keep: str = "beide", flip_upper: bool = True) -> list[PlateObject]:
        """„Schneiden“ wie im Original: waagrecht in `height` über dem Bett; `keep` = "beide", "oben" oder "unten".
        Der obere Teil wird auf Wunsch umgedreht, damit er mit der Schnittfläche auf dem Bett liegt."""
        lower, upper = cut_mesh(item.placed(), height)
        index = self.objects.index(item)
        self.objects.remove(item)
        created = []
        for label, part in (("unten", lower), ("oben", upper)):
            if part is None or keep not in ("beide", label):
                continue
            piece = PlateObject(self._unique(f"{item.name} ({label})"), part, filament=item.filament,
                                settings=dict(item.settings))
            piece.set_mesh(part)
            if label == "oben" and flip_upper:
                piece.rotate("X", 180)
            self.objects.insert(index + len(created), piece)
            created.append(piece)
        if not created:
            raise ValueError("Die Schnitthöhe liegt außerhalb des Objekts")
        self.arrange()
        return created

    def write_models(self, folder: Path) -> list[Path]:
        """Schreibt je Objekt eine STL an seiner Lage auf dem Bett (für den Slicer)."""
        from ..core.meshes import write_stl
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for number, item in enumerate(self.objects, 1):
            path = folder / f"objekt_{number}.stl"
            write_stl(item.placed(), str(path))
            paths.append(path)
        return paths


def connected_parts(triangles: np.ndarray) -> list[np.ndarray]:
    """Zusammenhängende Teile eines Netzes (über gemeinsame Ecken verbunden), größtes zuerst."""
    keys = np.round(triangles.reshape(-1, 3) * 1e4).astype(np.int64)
    _unique, vertex = np.unique(keys, axis=0, return_inverse=True)
    vertex = vertex.reshape(-1, 3)
    parent = np.arange(vertex.max() + 1)

    def find(i):
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:
            parent[i], i = root, parent[i]
        return root

    for a, b, c in vertex:
        ra, rb, rc = find(a), find(b), find(c)
        parent[rb] = ra
        parent[find(rc)] = ra
    roots = np.array([find(v) for v in vertex[:, 0]])
    parts = [triangles[roots == root] for root in np.unique(roots)]
    return sorted(parts, key=len, reverse=True)


def cut_mesh(triangles: np.ndarray, height: float) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Schneidet ein geschlossenes Netz waagrecht; beide Teile geschlossen (Schnittfläche gedeckelt). Gerechnet über
    den Geometriekern: Netz → Körper, mit Halbräumen schneiden, wieder in Dreiecke zerlegen."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from ..core import meshes
    solid = meshes.to_solid(triangles)
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(0, 0, height), gp_Dir(0, 0, 1))).Face()
    parts = []
    for side in (-1.0, 1.0):
        half = BRepPrimAPI_MakeHalfSpace(face, gp_Pnt(0, 0, height + side)).Solid()
        common = BRepAlgoAPI_Common(solid, half)
        common.Build()
        shape = common.Shape() if common.IsDone() else None
        if shape is None or not TopExp_Explorer(shape, TopAbs_SOLID).More():
            parts.append(None)
            continue
        parts.append(meshes.tessellate(shape, 0.02))
    return parts[0], parts[1]
