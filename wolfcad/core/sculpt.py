# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Formen (Sculpting) an Netzkörpern – angelehnt an den Sculpt-Modus von Blender (Runde 93, Nutzerwunsch).

Ein Netzkörper ist im Modell ein Feld lose liegender Dreiecke. Zum Formen wird daraus ein verbundenes Netz mit
gemeinsamen Ecken (`SculptMesh`): je Ecke Lage, Normale, Nachbarn und eine Maske (0 = frei, 1 = geschützt). Die
Pinsel verschieben die Ecken im Pinselkreis, gewichtet mit einer Abfallkurve (Mitte stark, Rand weich):

- Zeichnen: längs der mittleren Normale heraus (Strg: hinein)       - Ton / Tonstreifen: Material auftragen,
- Aufblasen: jede Ecke längs ihrer eigenen Normale                    zur Ebene der Stelle hin gedeckelt
- Greifen: Bereich beim Ziehen mitnehmen (wie Blender „Grab“)       - Schlangenhaken: Greifen, das mitwandert
- Glätten: zur Mitte der Nachbarn (Umschalt bei jedem Pinsel)       - Abflachen: zur Ebene der Stelle hin
- Kneifen: zur Pinselmitte zusammenziehen                           - Falte: Kneifen und hinein drücken
- Schicht: wie Zeichnen, höchstens bis zur Schichthöhe              - Maske: Ecken schützen (Strg: freigeben)

Symmetrie spiegelt jeden Pinseltupfer an den gewählten Hauptebenen durch den Ursprung. Für mehr Feinheit lässt
sich das Netz unterteilen (jedes Dreieck in vier). Das Ergebnis kommt als Schritt „Formen“ in die Timeline
(`SculptStep`), der den Netzkörper durch das geformte Netz ersetzt.

Alles ist mit numpy vektorisiert; die Suche der Ecken im Pinselkreis läuft über einen k-d-Baum (scipy), der nach
jedem Strich neu aufgebaut wird (während des Strichs mit Sicherheitszuschlag auf die alten Lagen).
"""
from __future__ import annotations

import base64
import zlib
from dataclasses import dataclass

import numpy as np

from .features import BuildState, Feature, FeatureError

BRUSHES = ("Zeichnen", "Ton", "Tonstreifen", "Aufblasen", "Greifen", "Schlangenhaken", "Glätten", "Abflachen",
           "Kneifen", "Falte", "Schicht", "Maske")
# Abfallkurven wie Blender: Gewicht abhängig vom Abstand zur Pinselmitte (t = 0 Mitte … 1 Rand)
FALLOFFS = ("Weich", "Kugel", "Wurzel", "Scharf", "Linear", "Konstant")
# Blender-Tasten der Pinsel (Umschalt+Taste wie in Blender als zweite Belegung)
BRUSH_KEYS = {"X": "Zeichnen", "Shift+C": "Ton", "C": "Tonstreifen", "I": "Aufblasen", "G": "Greifen",
              "K": "Schlangenhaken", "S": "Glätten", "Shift+T": "Abflachen", "P": "Kneifen", "Shift+X": "Falte",
              "L": "Schicht", "M": "Maske"}
MAX_TRIANGLES = 2_000_000       # darüber wird das Formen zu träge (Unterteilen verweigert)


def falloff(kind: str, t: np.ndarray) -> np.ndarray:
    """Gewicht 1 in der Mitte, 0 am Rand des Pinsels (t = Abstand / Radius)."""
    t = np.clip(t, 0.0, 1.0)
    if kind == "Kugel":
        return np.sqrt(np.maximum(1.0 - t * t, 0.0))
    if kind == "Wurzel":
        return 1.0 - np.sqrt(t)
    if kind == "Scharf":
        return (1.0 - t) ** 2
    if kind == "Linear":
        return 1.0 - t
    if kind == "Konstant":
        return np.ones_like(t)
    return 1.0 - t * t * (3.0 - 2.0 * t)        # „Weich“: glatte Hermite-Kurve (Blender: Smooth)


class SculptMesh:
    """Verbundenes Dreiecksnetz zum Formen: Ecken (V × 3), Dreiecke (F × 3 Eckennummern), Normalen, Nachbarn."""

    def __init__(self, vertices: np.ndarray, faces: np.ndarray, mask: np.ndarray | None = None):
        self.vertices = np.ascontiguousarray(vertices, dtype=np.float64)
        self.faces = np.ascontiguousarray(faces, dtype=np.int64)
        self.mask = np.zeros(len(self.vertices)) if mask is None else np.asarray(mask, dtype=np.float64)
        self._tree = None
        # Zähler der Topologie (neue Dreiecke, z. B. nach dem Unterteilen): die Grafik überträgt die Eckennummern
        # nur, wenn er sich geändert hat
        SculptMesh._versions = getattr(SculptMesh, "_versions", 0) + 1
        self.topology = SculptMesh._versions
        self._faces_cache = None    # (Schwerpunkte, Größen) der Dreiecke für das schnelle Treffen per Strahl
        self._moved = 0.0           # so weit ist eine Ecke höchstens gewandert, seit Baum und Cache gebaut wurden
        # float32-Kopie der Ecken für die Grafik: beim Formen nur die bewegten Zeilen nachgeführt (spart je Bild
        # das Umwandeln des ganzen Netzes)
        self._vertices32 = self.vertices.astype(np.float32)
        self._build_neighbours()
        self.update_normals()

    # --- Aufbau ---------------------------------------------------------------------------------------------

    @classmethod
    def from_triangles(cls, triangles: np.ndarray, tolerance: float = 1e-6) -> "SculptMesh":
        """Aus losen Dreiecken (n × 3 × 3): gleiche Ecken (bis `tolerance`) werden eine gemeinsame Ecke."""
        corners = np.asarray(triangles, dtype=np.float64).reshape(-1, 3)
        if len(corners) == 0:
            raise FeatureError("Der Netzkörper hat keine Dreiecke")
        keys = np.round(corners / tolerance).astype(np.int64)
        _unique, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        faces = inverse.reshape(-1, 3)
        # entartete Dreiecke (zwei gleiche Ecken) fallen weg
        keep = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
        return cls(corners[first], faces[keep])

    def _build_neighbours(self) -> None:
        """Dünnbesetzte Matrizen für schnelle Rechnungen über das ganze Netz (scipy.sparse):
        `_adjacency` (Ecke × Ecke, 1 je Nachbar) für das Glätten, `_incidence` (Ecke × Dreieck) zum Aufsummieren der
        Flächennormalen je Ecke – beides ein Matrixprodukt statt langsamer Schleifen bzw. np.add.at."""
        from scipy.sparse import csr_matrix
        count_v, count_f = len(self.vertices), len(self.faces)
        edges = np.concatenate([self.faces[:, [0, 1]], self.faces[:, [1, 2]], self.faces[:, [2, 0]]])
        edges = np.unique(np.concatenate([edges, edges[:, ::-1]]), axis=0)
        self._adjacency = csr_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(count_v, count_v))
        self._counts = np.asarray(self._adjacency.sum(axis=1)).ravel()
        rows = self.faces.ravel()
        cols = np.repeat(np.arange(count_f), 3)
        self._incidence = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(count_v, count_f))

    def triangles(self) -> np.ndarray:
        """Die Dreiecke als loses Feld (n × 3 × 3, float32) – so, wie Netzkörper im Modell liegen."""
        if len(self._vertices32) != len(self.vertices):
            self._vertices32 = self.vertices.astype(np.float32)
        return np.take(self._vertices32, self.faces, axis=0)

    def corner_normals(self) -> np.ndarray:
        """Eckennormalen je Dreiecksecke (zu `triangles`), für die glatte Schattierung beim Formen."""
        return np.take(self._normals32, self.faces, axis=0).reshape(-1, 3)

    def sync_vertices(self) -> None:
        """Nach Änderungen an `vertices` von außen (Rückgängig): die Grafik-Kopie ganz neu."""
        self._vertices32 = self.vertices.astype(np.float32)

    def update_normals(self, only: np.ndarray | None = None) -> None:
        """Flächen- und Eckennormalen (flächengewichtet) neu bestimmen – ganz oder nur um die Ecken `only`
        (Pinseltupfer: nur die berührten Dreiecke und deren Ecken, statt des ganzen Netzes)."""
        if only is None:
            corners = self.vertices[self.faces]
            self.face_normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
            normals = self._incidence @ self.face_normals          # Summe der Flächennormalen je Ecke
            length = np.linalg.norm(normals, axis=1, keepdims=True)
            self.normals = np.divide(normals, length, out=np.zeros_like(normals), where=length > 1e-12)
            self._normals32 = self.normals.astype(np.float32)
            return
        faces = np.unique(self._incidence[only].indices)
        if len(faces) == 0:
            return
        corners = self.vertices[self.faces[faces]]
        self.face_normals[faces] = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        touched = np.unique(self.faces[faces])
        normals = self._incidence[touched] @ self.face_normals
        length = np.linalg.norm(normals, axis=1, keepdims=True)
        self.normals[touched] = np.divide(normals, length, out=np.zeros_like(normals), where=length > 1e-12)
        self._normals32[touched] = self.normals[touched]

    def move_vertices(self, indices: np.ndarray, positions: np.ndarray) -> None:
        """Ecken an neue Lagen setzen: Normalen ringsum nachführen, Wanderweg für die Suche mitzählen."""
        if len(indices) == 0:
            return
        self.vertices[indices] = positions
        self._vertices32[indices] = positions
        if self._tree is not None:
            # genaue größte Abweichung seit dem Aufbau der Bäume – nur über die bewegten Ecken gemessen (eine Summe
            # über alle Tupfer wuchs bei langen Strichen auf Millimeter und machte die Strahlsuche träge)
            far = float(np.sqrt(np.max(((positions - self._tree_positions[indices]) ** 2).sum(axis=1))))
            self._moved = max(self._moved, far)
        self.update_normals(indices)

    def neighbour_mean(self, indices: np.ndarray) -> np.ndarray:
        """Mittelpunkt der Nachbarn jeder Ecke in `indices` (für Glätten)."""
        counts = self._counts[indices]
        sums = self._adjacency[indices] @ self.vertices
        means = self.vertices[indices].copy()
        inner = counts > 0
        means[inner] = sums[inner] / counts[inner, None]
        return means

    # --- Suche ----------------------------------------------------------------------------------------------

    def _kdtree(self):
        from scipy.spatial import cKDTree
        if self._tree is None:
            self._tree = cKDTree(self.vertices)
            self._tree_positions = self.vertices.copy()
            self._faces_cache = None
            self._moved = 0.0
        return self._tree

    def refresh_tree(self, force: bool = False) -> None:
        """Nach einem Strich: Suchbäume auf die neuen Lagen bringen – aber nur, wenn die Ecken seitdem merklich
        gewandert sind (mehr als zehn Dreiecksgrößen). Sonst genügt der mitgezählte Zuschlag; der Neuaufbau kostete
        bei 800 000 Dreiecken 0,6 s nach jedem Strich. `force` (nach Rückgängig, Ecken springen): immer neu."""
        largest = self._faces_cache[1] if self._faces_cache is not None else 0.0
        if not force and self._tree is not None and self._faces_cache is not None and self._moved < 10.0 * largest:
            return
        self._tree = None
        self._faces_cache = None
        self._moved = 0.0

    def near(self, centre, radius: float) -> tuple[np.ndarray, np.ndarray]:
        """(Eckennummern, Abstände) im Pinselkreis um `centre` – mit den aktuellen Lagen gemessen."""
        tree = self._kdtree()
        # die Ecken sind seit dem Aufbau des Baums höchstens `_moved` gewandert: so viel größer suchen, dann genau
        # messen (mitgezählt in move_vertices statt jedes Mal alle Ecken zu vergleichen)
        found = np.asarray(tree.query_ball_point(np.asarray(centre, float), radius + self._moved), dtype=np.int64)
        if len(found) == 0:
            return found, np.zeros(0)
        distance = np.linalg.norm(self.vertices[found] - np.asarray(centre, float), axis=1)
        inside = distance < radius
        return found[inside], distance[inside]

    def ray_hit(self, origin, direction) -> tuple[np.ndarray, int] | None:
        """Vorderster Treffpunkt des Strahls mit dem Netz und die Nummer des getroffenen Dreiecks (oder None).

        Erst werden die Dreiecke grob ausgesiebt (Abstand ihres Schwerpunkts zum Strahl höchstens ihre Größe),
        dann exakt nach Möller–Trumbore geschnitten – auch bei Hunderttausenden Dreiecken in Millisekunden."""
        origin = np.asarray(origin, float)
        direction = np.asarray(direction, float)
        direction = direction / (np.linalg.norm(direction) or 1.0)
        if self._faces_cache is None or self._tree is None:
            from scipy.spatial import cKDTree
            self._kdtree()
            corners = self.vertices[self.faces]
            centre = corners.mean(axis=1)
            size = np.sqrt(np.max(((corners - centre[:, None]) ** 2).sum(axis=2), axis=1))
            # Suchbaum über die Dreiecksmitten: der Strahl fragt nur nahe Dreiecke ab (statt alle durchzusehen –
            # bei 800 000 Dreiecken 42 ms je Mausschritt)
            self._faces_cache = (cKDTree(centre), float(size.max()), centre.min(axis=0), centre.max(axis=0))
        tree, largest, low, high = self._faces_cache
        reach = largest + 2.0 * self._moved + 1e-9     # Schwerpunkt und Ecken sind seitdem gewandert
        # Abschnitt des Strahls im (erweiterten) Hüllquader der Dreiecksmitten
        with np.errstate(divide="ignore", invalid="ignore"):
            t_low = (low - reach - origin) / direction
            t_high = (high + reach - origin) / direction
        t_near = np.nanmax(np.where(np.isfinite(np.minimum(t_low, t_high)), np.minimum(t_low, t_high), -np.inf))
        t_far = np.nanmin(np.where(np.isfinite(np.maximum(t_low, t_high)), np.maximum(t_low, t_high), np.inf))
        inside = (origin >= low - reach) & (origin <= high + reach)
        for axis in range(3):
            if direction[axis] == 0 and not inside[axis]:
                return None
        t_near, t_far = max(t_near, 0.0), t_far
        if not t_far > t_near:
            return None
        # Punkte längs des Strahls im Abstand höchstens der Dreiecksgröße; ihre Umgebung enthält jedes getroffene
        spacing = max(reach, (t_far - t_near) / 4000.0, 1e-6)
        samples = origin + direction * np.arange(t_near, t_far + spacing, spacing)[:, None]
        found = tree.query_ball_point(samples, r=spacing * 0.75 + reach, return_sorted=False)
        candidates = np.unique(np.concatenate([np.asarray(item, dtype=np.int64) for item in found]
                                              or [np.zeros(0, np.int64)]))
        if len(candidates) == 0:
            return None
        corners = self.vertices[self.faces[candidates]]
        a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]
        edge1, edge2 = b - a, c - a
        p = np.cross(direction, edge2)
        determinant = np.einsum("ij,ij->i", edge1, p)
        valid = np.abs(determinant) > 1e-12
        inverse = np.divide(1.0, determinant, out=np.zeros_like(determinant), where=valid)
        t_vec = origin - a
        u = np.einsum("ij,ij->i", t_vec, p) * inverse
        q = np.cross(t_vec, edge1)
        v = (q @ direction) * inverse
        t = np.einsum("ij,ij->i", edge2, q) * inverse
        hits = valid & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
        if not hits.any():
            return None
        best = np.argmin(np.where(hits, t, np.inf))
        return origin + direction * t[best], int(candidates[best])

    # --- Topologie ------------------------------------------------------------------------------------------

    def subdivide(self) -> None:
        """Jedes Dreieck in vier (Kantenmitten werden gemeinsame neue Ecken) – mehr Feinheit zum Formen."""
        if len(self.faces) * 4 > MAX_TRIANGLES:
            raise FeatureError(f"Mehr als {MAX_TRIANGLES:,} Dreiecke würden das Formen zu träge machen".replace(",", "."))
        f = self.faces
        edges = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
        ordered = np.sort(edges, axis=1)
        unique, inverse = np.unique(ordered, axis=0, return_inverse=True)
        middle = len(self.vertices) + inverse.reshape(3, -1).T           # Mitte der Kanten 01, 12, 20 je Dreieck
        points = (self.vertices[unique[:, 0]] + self.vertices[unique[:, 1]]) / 2
        masks = (self.mask[unique[:, 0]] + self.mask[unique[:, 1]]) / 2
        a, b, c = f[:, 0], f[:, 1], f[:, 2]
        ab, bc, ca = middle[:, 0], middle[:, 1], middle[:, 2]
        faces = np.concatenate([np.stack([a, ab, ca], 1), np.stack([ab, b, bc], 1), np.stack([ca, bc, c], 1),
                                np.stack([ab, bc, ca], 1)])
        self.__init__(np.concatenate([self.vertices, points]), faces, np.concatenate([self.mask, masks]))

    def refine(self, region: np.ndarray, detail: float, rounds: int = 12) -> int:
        """Dynamische Topologie (vereinfacht, Runde 96): im Bereich `region` (Ecken, bool) jede Kante, die länger als
        `detail` ist, halbieren. Jedes Dreieck wird nach der Zahl seiner geteilten Kanten zerlegt (eine: in zwei, zwei:
        in drei, drei: in vier – „Rot-Grün“-Teilung); so bleibt das Netz ohne Lücken oder T-Stöße. Wiederholt, bis
        im Bereich keine Kante mehr zu lang ist. Rückgabe: Zahl der neuen Ecken."""
        if detail <= 0:
            raise FeatureError("Die Detailgröße muss größer als 0 sein")
        vertices, faces, mask = self.vertices, self.faces, self.mask
        region = np.asarray(region, dtype=bool)
        added = 0
        for _round in range(rounds):
            count = len(vertices)
            nxt = np.roll(faces, -1, axis=1)                      # Kante k: Ecke k → Ecke k+1
            keys = np.minimum(faces, nxt) * count + np.maximum(faces, nxt)
            lengths = np.linalg.norm(vertices[nxt] - vertices[faces], axis=2)
            long = (lengths > detail) & (region[faces] | region[nxt])
            chosen = np.unique(keys[long])
            if len(chosen) == 0 or len(faces) + 3 * len(chosen) > MAX_TRIANGLES:
                break
            a, b = chosen // count, chosen % count
            vertices = np.concatenate([vertices, (vertices[a] + vertices[b]) / 2])
            mask = np.concatenate([mask, (mask[a] + mask[b]) / 2])
            region = np.concatenate([region, np.ones(len(chosen), dtype=bool)])
            added += len(chosen)
            is_split = np.isin(keys, chosen)
            middle = np.where(is_split, count + np.searchsorted(chosen, keys), -1)
            faces = _split_faces(faces, is_split, middle)
        if added:
            self.__init__(vertices, faces, mask)
        return added

    # --- Schleifenschnitt (Runde 98, wie Blenders „Loop Cut“) -----------------------------------------------

    def _edge_lookup(self):
        """Kante → angrenzende Dreiecke: sortierte Kantenschlüssel (klein·V + groß) und das Dreieck je Eintrag.
        Gemerkt je Topologie (beim Schleifenschnitt fragt jede Mausbewegung die Nachbarn einer Kante ab)."""
        cached = getattr(self, "_edges_cache", None)
        if cached is not None and cached[0] == self.topology:
            return cached[1], cached[2]
        count = len(self.vertices)
        nxt = np.roll(self.faces, -1, axis=1)
        keys = (np.minimum(self.faces, nxt) * count + np.maximum(self.faces, nxt)).ravel()
        order = np.argsort(keys, kind="stable")
        sorted_keys, owner = keys[order], order // 3
        self._edges_cache = (self.topology, sorted_keys, owner)
        return sorted_keys, owner

    def faces_at_edge(self, a: int, b: int) -> np.ndarray:
        """Die Dreiecke an der Kante a–b (eins am Rand, zwei im Inneren eines geschlossenen Netzes)."""
        keys, owner = self._edge_lookup()
        key = min(a, b) * len(self.vertices) + max(a, b)
        low, high = np.searchsorted(keys, key, "left"), np.searchsorted(keys, key, "right")
        return owner[low:high]

    def _is_diagonal(self, a: int, b: int) -> bool:
        """Liegt a–b als Diagonale in einem „Viereck“ aus zwei Dreiecken? (die längste Kante beider Dreiecke, die
        fast in einer Ebene liegen). Blender schneidet Vierecke quer; ein Schnitt längs der Diagonale liefe schräg
        durchs Netz – die Diagonale wird deshalb beim Zielen übersprungen (Netze aus Körpern sind Dreiecksnetze)."""
        around = self.faces_at_edge(a, b)
        if len(around) != 2:
            return False
        length = np.linalg.norm(self.vertices[a] - self.vertices[b])
        for face in around:
            corners = self.vertices[self.faces[face]]
            sides = np.linalg.norm(corners - np.roll(corners, -1, axis=0), axis=1)
            if length < sides.max() - 1e-9 * max(length, 1.0):
                return False
        n1, n2 = self.face_normals[around[0]], self.face_normals[around[1]]
        cosine = float(n1 @ n2) / ((np.linalg.norm(n1) * np.linalg.norm(n2)) or 1.0)
        return cosine > 0.98        # unter rund 11° Knick: ein ebenes Viereck

    def loop_edge(self, point, face: int) -> tuple[int, int] | None:
        """Die Kante, an der ein Schleifenschnitt bei `point` (auf dem Dreieck `face`) ansetzt: die nächste Kante des
        getroffenen Dreiecks, Viereck-Diagonalen ausgenommen. Rückgabe (a, b) als Eckennummern."""
        point = np.asarray(point, dtype=np.float64)
        corners = self.faces[face]
        candidates = []
        for k in range(3):
            a, b = int(corners[k]), int(corners[(k + 1) % 3])
            p, q = self.vertices[a], self.vertices[b]
            span = q - p
            t = float(np.clip((point - p) @ span / ((span @ span) or 1.0), 0.0, 1.0))
            candidates.append((float(np.linalg.norm(p + span * t - point)), a, b))
        candidates.sort()
        for _distance, a, b in candidates:
            if not self._is_diagonal(a, b):
                return a, b
        return candidates[0][1], candidates[0][2]

    @staticmethod
    def loop_positions(count: int, slide: float = 0.0) -> np.ndarray:
        """Wo `count` Schnitte die Ansatzkante teilen (0 … 1): gleichmäßig verteilt wie in Blender, mit `slide`
        (−1 … 1) gemeinsam zur einen oder anderen Ecke geschoben – höchstens bis knapp vor die Ecke."""
        count = max(1, int(count))
        base = np.arange(1, count + 1) / (count + 1)
        room = 0.95 / (count + 1)
        return base + float(np.clip(slide, -1.0, 1.0)) * room

    def _loop_cut_plane(self, point, normal):
        """Die Dreiecke, die die Schnittebene (durch `point`, Normale `normal`) als zusammenhängende Schleife um die
        Stelle `point` schneidet. Rückgabe (Abstände der Ecken zur Ebene, Vorzeichen, Dreiecke der Schleife,
        geschnittene Kanten je Dreieck) oder None, wenn die Ebene dort nichts schneidet.

        Ein Schnitt mit der Ebene statt Blenders Kantenring: Netze aus Körpern sind unregelmäßige Dreiecksnetze, in
        denen es keine Viereck-Ringe gibt. Damit nicht jede Stelle durchschnitten wird, die die Ebene sonst noch
        trifft (z. B. beide Arme eines U), gilt nur der zusammenhängende Ring, der an der Stelle beginnt."""
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        point = np.asarray(point, dtype=np.float64)
        normal = np.asarray(normal, dtype=np.float64)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        scale = float(np.linalg.norm(self.vertices.max(axis=0) - self.vertices.min(axis=0))) or 1.0
        distance = (self.vertices - point) @ normal
        side = np.where(np.abs(distance) <= 1e-7 * scale, 0, np.sign(distance)).astype(np.int8)
        corner_side = side[self.faces]
        straddling = np.nonzero((corner_side.max(axis=1) > 0) & (corner_side.min(axis=1) < 0))[0]
        if len(straddling) == 0:
            return None
        faces = self.faces[straddling]
        nxt = np.roll(faces, -1, axis=1)
        crossing = side[faces] * side[nxt] < 0                      # Kante k (Ecke k → k+1) geht durch die Ebene
        count = len(self.vertices)
        keys = np.minimum(faces, nxt) * count + np.maximum(faces, nxt)
        # Nachbarschaft der geschnittenen Dreiecke: gemeinsame geschnittene Kante oder gemeinsame Ecke auf der Ebene
        links = []
        local = np.repeat(np.arange(len(straddling)), 3).reshape(-1, 3)
        for owner_keys in (np.where(crossing, keys, -1), np.where(side[faces] == 0, faces, -1)):
            flat, who = owner_keys.ravel(), local.ravel()
            used = flat >= 0
            flat, who = flat[used], who[used]
            order = np.argsort(flat, kind="stable")
            flat, who = flat[order], who[order]
            same = flat[1:] == flat[:-1]
            links.append(np.stack([who[:-1][same], who[1:][same]], axis=1))
        pairs = np.concatenate(links) if links else np.zeros((0, 2), np.int64)
        graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(straddling),) * 2)
        _count, label = connected_components(graph, directed=False)
        # Anfang: das Dreieck mit dem Schnittpunkt, der der Stelle am nächsten liegt
        a, b = faces[crossing], nxt[crossing]
        t = distance[a] / (distance[a] - distance[b])
        hits = self.vertices[a] + (self.vertices[b] - self.vertices[a]) * t[:, None]
        owners = np.nonzero(crossing)[0]
        seed = owners[np.argmin(np.linalg.norm(hits - point, axis=1))]
        ring = np.nonzero(label == label[seed])[0]
        return distance, side, straddling[ring], crossing[ring]

    def loop_preview(self, point, normal) -> np.ndarray:
        """Linienstücke (2k × 3) der Schleife, die ein Schnitt an dieser Ebene erzeugen würde – die gelbe Vorschau."""
        found = self._loop_cut_plane(point, normal)
        if found is None:
            return np.zeros((0, 3), np.float32)
        distance, side, ring, crossing = found
        faces = self.faces[ring]
        nxt = np.roll(faces, -1, axis=1)
        a, b = faces, nxt
        t = np.where(crossing, distance[a] / np.where(crossing, distance[a] - distance[b], 1.0), 0.0)
        points = self.vertices[a] + (self.vertices[b] - self.vertices[a]) * t[..., None]   # F × 3 × 3
        # je Dreieck die beiden Endpunkte: geschnittene Kanten bzw. die Ecke, die auf der Ebene liegt
        on_plane = (side[faces] == 0)
        ends = np.where((crossing | on_plane)[..., None], points, np.nan)
        segments = []
        for row in ends:
            chosen = row[~np.isnan(row[:, 0])]
            if len(chosen) >= 2:
                segments.append(chosen[:2])
        return np.asarray(segments, dtype=np.float32).reshape(-1, 3)

    def loop_cut(self, point, normal) -> int:
        """Schneidet eine Schleife neuer Ecken längs der Ebene ins Netz (siehe `_loop_cut_plane`); jedes geschnittene
        Dreieck wird ohne Lücken oder T-Stöße zerlegt. Rückgabe: Zahl der neuen Ecken (0: an der Stelle nichts)."""
        found = self._loop_cut_plane(point, normal)
        if found is None:
            return 0
        distance, _side, ring, crossing_ring = found
        count = len(self.vertices)
        if len(self.faces) + 2 * len(ring) > MAX_TRIANGLES:
            raise FeatureError(f"Mehr als {MAX_TRIANGLES:,} Dreiecke würden das Formen zu träge machen".replace(",", "."))
        faces = self.faces
        nxt = np.roll(faces, -1, axis=1)
        keys = np.minimum(faces, nxt) * count + np.maximum(faces, nxt)
        is_split = np.zeros(faces.shape, dtype=bool)
        is_split[ring] = crossing_ring
        chosen = np.unique(keys[is_split])
        a, b = chosen // count, chosen % count
        t = (distance[a] / (distance[a] - distance[b]))[:, None]
        vertices = np.concatenate([self.vertices, self.vertices[a] + (self.vertices[b] - self.vertices[a]) * t])
        mask = np.concatenate([self.mask, self.mask[a] + (self.mask[b] - self.mask[a]) * t[:, 0]])
        middle = np.where(is_split, count + np.searchsorted(chosen, keys), -1)
        self.__init__(vertices, _split_faces(faces, is_split, middle), mask)
        return len(chosen)

    def loop_cuts(self, a: int, b: int, cuts: int, slide: float = 0.0) -> int:
        """`cuts` parallele Schleifen quer zur Kante a–b (wie Blender: gleichmäßig, gemeinsam verschiebbar).
        Die Ebenen stehen senkrecht auf der Kante; Kante und Ecken a, b bleiben beim Schneiden erhalten, deshalb
        treffen auch die späteren Schnitte (auf dem schon geteilten Netz) dieselbe Kante."""
        start, end = self.vertices[a].copy(), self.vertices[b].copy()
        direction = end - start
        if np.linalg.norm(direction) < 1e-12:
            return 0
        added = 0
        for position in self.loop_positions(cuts, slide):
            added += self.loop_cut(start + direction * position, direction)
        return added

    def loop_cuts_preview(self, a: int, b: int, cuts: int, slide: float = 0.0) -> np.ndarray:
        """Vorschau aller Schleifen von `loop_cuts` als Linienstücke."""
        start, end = self.vertices[a], self.vertices[b]
        direction = end - start
        parts = [self.loop_preview(start + direction * position, direction)
                 for position in self.loop_positions(cuts, slide)]
        return np.concatenate(parts) if parts else np.zeros((0, 3), np.float32)

    def wire_edges(self) -> np.ndarray:
        """Alle Kanten einmal (E × 2 Eckennummern) – für die Netzansicht (Drahtgitter) beim Formen. Gemerkt je
        Topologie: beim Formen bewegen sich nur die Ecken."""
        cached = getattr(self, "_wire_cache", None)
        if cached is not None and cached[0] == self.topology:
            return cached[1]
        f = self.faces
        edges = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
        edges = np.unique(edges, axis=0)
        self._wire_cache = (self.topology, edges)
        return edges

    # --- Speichern ------------------------------------------------------------------------------------------

    def pack(self) -> str:
        """Ecken (float32) und Dreiecke (int32) kompakt als Text für den Entwurf."""
        header = np.array([len(self.vertices), len(self.faces)], dtype=np.int64).tobytes()
        body = self.vertices.astype(np.float32).tobytes() + self.faces.astype(np.int32).tobytes()
        return base64.b64encode(zlib.compress(header + body, 6)).decode("ascii")

    @classmethod
    def unpack(cls, text: str) -> "SculptMesh":
        raw = zlib.decompress(base64.b64decode(text))
        count_v, count_f = np.frombuffer(raw[:16], dtype=np.int64)
        vertices = np.frombuffer(raw[16:16 + count_v * 12], dtype=np.float32).reshape(-1, 3)
        faces = np.frombuffer(raw[16 + count_v * 12:], dtype=np.int32).reshape(-1, 3)
        if len(faces) != count_f:
            raise FeatureError("Die gespeicherte Form ist beschädigt")
        return cls(vertices.astype(np.float64), faces.astype(np.int64))


def _split_faces(faces: np.ndarray, is_split: np.ndarray, middle: np.ndarray) -> np.ndarray:
    """Zerlegt Dreiecke nach der Zahl ihrer geteilten Kanten („Rot-Grün“-Teilung, ohne Lücken oder T-Stöße).
    `is_split` (F × 3): Kante k (Ecke k → k+1) wird geteilt; `middle` (F × 3): Nummer der neuen Ecke darauf.
    Eine geteilte Kante: in zwei, zwei: in drei, drei: in vier Dreiecke; die Umlaufrichtung bleibt erhalten."""
    splits = is_split.sum(axis=1)
    pieces = [faces[splits == 0]]
    # eine geteilte Kante k (p→q, Mitte m, gegenüber r): (p, m, r), (m, q, r)
    one = np.nonzero(splits == 1)[0]
    k = is_split[one].argmax(axis=1)
    p_, q_, r_ = faces[one, k], faces[one, (k + 1) % 3], faces[one, (k + 2) % 3]
    m_ = middle[one, k]
    pieces += [np.stack([p_, m_, r_], 1), np.stack([m_, q_, r_], 1)]
    # zwei geteilte Kanten: die ungeteilte u (a→b), dann b→c mit Mitte m1, c→a mit Mitte m2
    two = np.nonzero(splits == 2)[0]
    u = (~is_split[two]).argmax(axis=1)
    a2, b2, c2 = faces[two, u], faces[two, (u + 1) % 3], faces[two, (u + 2) % 3]
    m1, m2 = middle[two, (u + 1) % 3], middle[two, (u + 2) % 3]
    pieces += [np.stack([a2, b2, m1], 1), np.stack([a2, m1, m2], 1), np.stack([m2, m1, c2], 1)]
    # drei geteilte Kanten: vier Dreiecke wie beim Unterteilen
    three = np.nonzero(splits == 3)[0]
    a3, b3, c3 = faces[three, 0], faces[three, 1], faces[three, 2]
    ab, bc, ca = middle[three, 0], middle[three, 1], middle[three, 2]
    pieces += [np.stack([a3, ab, ca], 1), np.stack([ab, b3, bc], 1), np.stack([ca, bc, c3], 1),
               np.stack([ab, bc, ca], 1)]
    return np.concatenate(pieces)


def voxel_remesh(mesh: "SculptMesh", size: float) -> "SculptMesh":
    """Neu vernetzen wie Blenders Voxel-Remesh (Runde 96): gleichmäßiges, geschlossenes Netz mit Kantenlänge etwa
    `size`. Ablauf: Oberfläche dicht abtasten und in ein Voxelgitter eintragen, Inneres auffüllen (alles, was von
    außen nicht erreichbar ist), das Gitter leicht glätten und die Grenzfläche nach „Surface Nets“ ablesen – je
    Voxel an der Grenze ein Punkt (Mittel der Kantendurchgänge), je Gitterkante mit Vorzeichenwechsel ein Viereck."""
    from scipy import ndimage
    if size <= 0:
        raise FeatureError("Die Voxelgröße muss größer als 0 sein")
    low = mesh.vertices.min(axis=0) - 3 * size
    high = mesh.vertices.max(axis=0) + 3 * size
    shape = np.ceil((high - low) / size).astype(int) + 1
    if np.prod(shape) > 60_000_000:
        raise FeatureError("Die Voxelgröße ist für dieses Netz zu fein (mehr als 60 Mio. Voxel)")
    # 1. Oberfläche abtasten: je Dreieck so viele Punkte, dass keine Lücke größer als ein halbes Voxel bleibt
    corners = mesh.vertices[mesh.faces]
    longest = np.max(np.linalg.norm(corners - np.roll(corners, 1, axis=1), axis=2), axis=1)
    steps = np.clip(np.ceil(longest / (size * 0.5)).astype(int), 1, 64)
    surface = np.zeros(shape, dtype=bool)
    for count in np.unique(steps):
        chosen = corners[steps == count]
        u, v = np.meshgrid(np.arange(count + 1), np.arange(count + 1), indexing="ij")
        keep = u + v <= count
        a, b = (u[keep] / count)[:, None], (v[keep] / count)[:, None]
        points = chosen[:, 0, None, :] * (1 - a - b) + chosen[:, 1, None, :] * a + chosen[:, 2, None, :] * b
        index = np.round((points.reshape(-1, 3) - low) / size).astype(int)
        surface[index[:, 0], index[:, 1], index[:, 2]] = True
    # 2. Inneres: alles, was von außen (Rand des Gitters) nicht erreichbar ist
    # Die Hülle muss dicht sein (sonst „läuft“ das Füllen aus): die abgetastete Schicht leicht verbinden, falls nötig
    filled = ndimage.binary_fill_holes(surface)
    if filled.sum() <= surface.sum() * 1.05:
        filled = ndimage.binary_fill_holes(ndimage.binary_closing(surface))
    # Grenzfläche durch die Mitte der Oberflächenvoxel: innen 1, Oberfläche ½, außen 0 – dann leicht glätten
    occupancy = filled.astype(np.float32)
    occupancy[surface] = 0.5
    field = ndimage.gaussian_filter(occupancy, sigma=0.6) - 0.5
    # 3. Surface Nets
    signs = field > 0
    cell = signs[:-1, :-1, :-1].astype(np.int8)
    total = sum(signs[i:signs.shape[0] - 1 + i, j:signs.shape[1] - 1 + j, k:signs.shape[2] - 1 + k].astype(np.int8)
                for i in (0, 1) for j in (0, 1) for k in (0, 1))
    active = (total > 0) & (total < 8)
    cells = np.argwhere(active)
    number = -np.ones(active.shape, dtype=np.int64)
    number[active] = np.arange(len(cells))
    # Punkt je aktivem Voxel: Mittel der Nulldurchgänge auf seinen 12 Kanten (Gitterkoordinaten)
    offsets = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1]])
    edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
    values = np.stack([field[cells[:, 0] + o[0], cells[:, 1] + o[1], cells[:, 2] + o[2]] for o in offsets], axis=1)
    sums = np.zeros((len(cells), 3))
    counts = np.zeros(len(cells))
    for first, second in edges:
        f0, f1 = values[:, first], values[:, second]
        crossing = (f0 > 0) != (f1 > 0)
        t = np.divide(f0, f0 - f1, out=np.full_like(f0, 0.5), where=np.abs(f0 - f1) > 1e-12)
        position = offsets[first] + (offsets[second] - offsets[first]) * t[:, None]
        sums[crossing] += position[crossing]
        counts[crossing] += 1
    points = low + (cells + sums / np.maximum(counts, 1)[:, None]) * size
    # Vierecke: je Gitterkante mit Vorzeichenwechsel die vier Voxel um sie herum
    quads = []
    for axis in range(3):
        a1, a2 = [other for other in range(3) if other != axis]
        index = [slice(None)] * 3
        index[axis] = slice(0, -1)
        here = signs[tuple(index)]
        index[axis] = slice(1, None)
        there = signs[tuple(index)]
        changed = np.argwhere(here != there)
        # die vier Nachbarvoxel brauchen auf den beiden anderen Achsen einen Vorgänger
        changed = changed[(changed[:, a1] > 0) & (changed[:, a2] > 0)]
        flip = here[tuple(changed.T)]
        around = []
        for d1, d2 in ((0, 0), (1, 0), (1, 1), (0, 1)):
            spot = changed.copy()
            spot[:, a1] -= d1
            spot[:, a2] -= d2
            around.append(number[spot[:, 0], spot[:, 1], spot[:, 2]])
        quad = np.stack(around, axis=1)
        valid = (quad >= 0).all(axis=1)
        quad, flip = quad[valid], flip[valid]
        quad[flip] = quad[flip][:, ::-1]            # nach außen gerichtet
        if axis == 1:
            quad = quad[:, ::-1]                    # Umlaufsinn der y-Achse (zyklische Reihenfolge x → y → z)
        quads.append(quad)
    quads = np.concatenate(quads)
    faces = np.concatenate([quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]])
    result = SculptMesh(points, faces)
    # Ausrichtung prüfen: nach außen zeigende Normalen ergeben ein positives Volumen
    corners = result.vertices[result.faces]
    volume = np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum() / 6
    if volume < 0:
        result = SculptMesh(points, faces[:, ::-1])
    return result


@dataclass
class Brush:
    """Einstellungen des Pinsels (wie Blenders Werkzeugleiste)."""

    kind: str = "Zeichnen"
    radius: float = 5.0             # mm im Modell
    strength: float = 0.5           # 0 … 1
    falloff: str = "Weich"
    invert: bool = False            # Strg: umgekehrt (hinein statt heraus, Maske freigeben …)
    symmetry: tuple = (False, False, False)   # an der YZ-, XZ- bzw. XY-Ebene spiegeln (Achse X, Y, Z)
    layer_height: float = 1.0       # Schicht: so hoch höchstens


class Stroke:
    """Ein Pinselstrich vom Drücken bis zum Loslassen: Tupfer im Abstand eines Viertels des Radius.

    Greifen und Schlangenhaken merken sich beim Drücken die Ecken im Pinselkreis und verschieben sie mit der Maus.
    Schicht merkt sich die Lage zu Strichbeginn (die Höhe wächst nicht über `layer_height` hinaus)."""

    def __init__(self, mesh: SculptMesh, brush: Brush, start_point, view_direction):
        self.mesh, self.brush = mesh, brush
        self.view = np.asarray(view_direction, float) / (np.linalg.norm(view_direction) or 1.0)
        self.last = np.asarray(start_point, float)
        self.start_vertices = mesh.vertices.copy()          # für Rückgängig, Schicht und Greifen
        self.grabbed = []                                   # je Spiegelung: (Ecken, Gewichte, Anfangspunkt)
        self.travel = 0.0
        if brush.kind in ("Greifen", "Schlangenhaken"):
            for centre, flip in self._mirrors(self.last):
                found, distance = mesh.near(centre, brush.radius)
                weight = falloff(brush.falloff, distance / brush.radius) * (1.0 - mesh.mask[found])
                self.grabbed.append([found, weight, centre, flip])     # flip: Spiegelrichtung dieses Bilds
        else:
            self.dab(self.last)

    def _mirrors(self, point):
        """Der Punkt und seine Spiegelbilder nach den eingeschalteten Symmetrieachsen: (Punkt, Vorzeichen)."""
        points = [(np.asarray(point, float), np.ones(3))]
        for axis, on in enumerate(self.brush.symmetry):
            if on:
                flip = np.ones(3)
                flip[axis] = -1.0
                points += [(p * flip, s * flip) for p, s in points]
        return points

    def move_to(self, point) -> bool:
        """Maus beim Ziehen an `point` (auf dem Netz bzw. der Sichtebene): Tupfer entlang des Wegs. True, wenn sich
        etwas geändert hat."""
        point = np.asarray(point, float)
        if self.grabbed:
            return self._grab(point)
        step = max(self.brush.radius * 0.25, 1e-6)
        distance = float(np.linalg.norm(point - self.last))
        if distance < step:
            return False
        count = int(distance // step)
        for number in range(1, count + 1):
            self.dab(self.last + (point - self.last) * (number * step / distance))
        self.last = self.last + (point - self.last) * (count * step / distance)
        return True

    def _grab(self, point) -> bool:
        mesh, brush = self.mesh, self.brush
        delta = point - self.last
        if not np.any(delta):
            return False
        for entry in self.grabbed:
            found, weight, centre, flip = entry
            mesh.move_vertices(found, mesh.vertices[found] + (delta * flip)[None, :] * weight[:, None])
            entry[2] = centre + delta * flip            # das Spiegelbild zieht gespiegelt
        if brush.kind == "Schlangenhaken":
            # der Haken wandert mit: neue Ecken unter dem Pinsel kommen dazu
            for entry in self.grabbed:
                found, distance = mesh.near(entry[2], brush.radius)
                entry[0] = found
                entry[1] = falloff(brush.falloff, distance / brush.radius) * (1.0 - mesh.mask[found]) * 0.6
        self.last = point
        return True

    def dab(self, point) -> None:
        """Ein Tupfer an `point` (und seinen Spiegelbildern)."""
        mesh, brush = self.mesh, self.brush
        for centre, _flip in self._mirrors(point):
            found, distance = mesh.near(centre, brush.radius)
            if len(found) == 0:
                continue
            weight = falloff(brush.falloff, distance / brush.radius)
            if brush.kind == "Maske":
                target = 0.0 if brush.invert else 1.0
                mesh.mask[found] += (target - mesh.mask[found]) * weight * brush.strength
                continue
            weight = weight * (1.0 - mesh.mask[found]) * brush.strength
            sign = -1.0 if brush.invert else 1.0
            positions = mesh.vertices[found]
            normals = mesh.normals[found]
            area_normal = (normals * weight[:, None]).sum(axis=0)
            length = np.linalg.norm(area_normal)
            area_normal = area_normal / length if length > 1e-12 else -self.view
            area_centre = (positions * weight[:, None]).sum(axis=0) / max(weight.sum(), 1e-12)
            step = brush.radius * 0.12          # so weit wandert eine Ecke höchstens je Tupfer (Stärke 1)
            kind = brush.kind
            if kind in ("Zeichnen", "Schicht"):
                move = area_normal[None, :] * (sign * step * weight)[:, None]
                if kind == "Schicht":
                    # höchstens bis zur Schichthöhe über der Lage zu Strichbeginn
                    start = self.start_vertices[found]
                    height = ((positions + move - start) * area_normal).sum(axis=1)
                    limit = brush.layer_height
                    clipped = np.clip(height, -limit, limit) - ((positions - start) * area_normal).sum(axis=1)
                    move = area_normal[None, :] * clipped[:, None]
            elif kind == "Aufblasen":
                move = normals * (sign * step * weight)[:, None]
            elif kind in ("Ton", "Tonstreifen"):
                # Material auftragen bis zu einer Ebene knapp über der Stelle (Ton deckelt die Höhe)
                plane = area_centre + area_normal * sign * step
                offset = ((plane - positions) * area_normal).sum(axis=1)
                if kind == "Tonstreifen":
                    weight = weight * (distance / brush.radius < 0.85)      # flacher, kantiger Streifen
                move = area_normal[None, :] * (offset * weight)[:, None]
            elif kind == "Abflachen":
                offset = ((area_centre - positions) * area_normal).sum(axis=1)
                move = area_normal[None, :] * (offset * weight * sign)[:, None]
            elif kind in ("Kneifen", "Falte"):
                towards = centre[None, :] - positions
                towards -= area_normal[None, :] * (towards @ area_normal)[:, None]   # nur in der Fläche
                move = towards * (0.3 * weight * sign)[:, None]
                if kind == "Falte":
                    move -= area_normal[None, :] * (sign * step * 0.5 * weight)[:, None]
            elif kind == "Glätten":
                move = (mesh.neighbour_mean(found) - positions) * np.clip(weight * 2.0, 0, 1)[:, None]
            else:
                continue
            mesh.move_vertices(found, positions + move)

    def undo(self) -> None:
        """Den ganzen Strich zurücknehmen."""
        self.mesh.vertices[:] = self.start_vertices
        self.mesh.sync_vertices()
        self.mesh.update_normals()
        self.mesh.refresh_tree(force=True)


@dataclass
class SculptStep(Feature):
    """Formen (Sculpting): ersetzt einen Netzkörper durch das im Formen-Modus geformte Netz.

    Das geformte Netz wird im Entwurf gespeichert (wie ein eingefügtes Netz); Änderungen am Netzkörper davor
    übernimmt der Schritt deshalb nicht – wie bei Blender ist Formen eine direkte Bearbeitung des Netzes."""

    mesh: str = ""
    data: str = ""

    KIND = "Formen"

    def parameters(self) -> list:
        return [item for item in super().parameters() if item.name != "data"]

    def apply(self, state: BuildState) -> None:
        if not self.mesh:
            raise FeatureError("Bitte einen Netzkörper wählen (im Modell anklicken)")
        if self.mesh not in state.meshes:
            raise FeatureError(f"Netzkörper '{self.mesh}' ist nicht (mehr) vorhanden")
        if not self.data:
            return                          # noch nichts geformt
        state.meshes[self.mesh] = SculptMesh.unpack(self.data).triangles()


from . import features as _ft  # noqa: E402
_ft.FEATURE_CLASSES[SculptStep.KIND] = SculptStep
