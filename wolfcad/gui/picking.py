# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Treffersuche für Mausklicks in der 3D-Ansicht (reine Geometrie, ohne Oberfläche).

Ein Klick wird als Strahl vom Auge durch den Mauspunkt aufgefasst. Gesucht wird
das vorderste getroffene Dreieck bzw. das vorderste Kantenstück, das der Strahl
innerhalb eines kleinen Winkels passiert.
"""

from __future__ import annotations

import numpy as np


def ray_triangles(origin: np.ndarray, direction: np.ndarray, triangles: np.ndarray) -> tuple[int, float] | None:
    """Vorderster Treffer des Strahls auf Dreiecken (n, 3, 3): (Index, Entfernung) oder None."""
    if len(triangles) == 0:
        return None
    triangles = triangles.astype(np.float64)
    edge1 = triangles[:, 1] - triangles[:, 0]
    edge2 = triangles[:, 2] - triangles[:, 0]
    # Verfahren nach Möller und Trumbore, für alle Dreiecke gleichzeitig
    p = np.cross(direction, edge2)
    determinant = np.einsum("ij,ij->i", edge1, p)
    valid = np.abs(determinant) > 1e-12
    inverse = np.zeros_like(determinant)
    inverse[valid] = 1.0 / determinant[valid]
    offset = origin - triangles[:, 0]
    u = np.einsum("ij,ij->i", offset, p) * inverse
    q = np.cross(offset, edge1)
    v = (q @ direction) * inverse
    distance = np.einsum("ij,ij->i", edge2, q) * inverse
    hit = valid & (u >= 0) & (v >= 0) & (u + v <= 1) & (distance > 1e-9)
    if not hit.any():
        return None
    index = int(np.argmin(np.where(hit, distance, np.inf)))
    return index, float(distance[index])


def ray_segments(origin: np.ndarray, direction: np.ndarray, segments: np.ndarray,
                 angle: float) -> tuple[int, float] | None:
    """Vorderstes Segment (m, 2, 3), das der Strahl näher als `angle` (Bogenmaß) passiert.

    Die zulässige Entfernung wächst mit dem Abstand vom Auge, damit sie auf dem
    Bildschirm immer gleich vielen Pixeln entspricht. Rückgabe: (Index, Entfernung).
    """
    if len(segments) == 0:
        return None
    segments = segments.astype(np.float64)
    start = segments[:, 0]
    along = segments[:, 1] - start
    w = start - origin
    # Nächste Punkte zwischen Strahl (Parameter t) und Segment (Parameter s in 0…1)
    a = float(direction @ direction)
    b = along @ direction
    c = np.einsum("ij,ij->i", along, along)
    d = w @ direction
    e = np.einsum("ij,ij->i", along, w)
    denominator = a * c - b * b
    safe = np.abs(denominator) > 1e-12
    s = np.where(safe, (b * d - a * e) / np.where(safe, denominator, 1.0), 0.0)
    s = np.clip(s, 0.0, 1.0)
    t = (d + s * b) / a
    nearest_on_segment = start + along * s[:, None]
    nearest_on_ray = origin + np.outer(t, direction)
    gap = np.linalg.norm(nearest_on_segment - nearest_on_ray, axis=1)
    hit = (t > 1e-9) & (gap <= np.tan(angle) * t * np.sqrt(a))
    if not hit.any():
        return None
    index = int(np.argmin(np.where(hit, t, np.inf)))
    return index, float(t[index])
