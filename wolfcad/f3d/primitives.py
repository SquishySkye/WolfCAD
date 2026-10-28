# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Liest die Grundkörper (Quader, Zylinder, Kugel) eines Fusion-Entwurfs.

Aufbau des Abschnitts der jeweiligen Klasse, an echten Daten ermittelt:
  - Verweise auf die Parameter und auf eine Bezugsmatrix (DRMatrix)
  - eine eingebettete 4×4-Matrix (16 Doubles, zeilenweise, letzte Zeile 0 0 0 1)
  - die Operation als 4-Byte-Zahl in festem Abstand vor dieser Matrix
Die Lage im Raum ist "Bezugsmatrix × eingebettete Matrix" (Längen in cm).

Im Koordinatensystem dieser Lage gilt:
  Quader:   von der Ecke (VersatzX, VersatzY, 0) aus DistanceX × DistanceY × DistanceZ
  Zylinder: Achse z ab dem Ursprung, Höhe "Height" (Vorzeichen = Richtung), "Diameter"
  Kugel:    Mittelpunkt im Ursprung, "Diameter"
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from . import segment
from .segment import Directory

# Klassenname -> (Art, Abstand der Operation vor der eingebetteten Matrix)
_CLASSES = {
    "DcBoxPrimitive": ("box", 83),
    "DcCylinderPrimitive": ("cylinder", 50),
    "DcSpherePrimitive": ("sphere", 39),
}
_MATRIX_WINDOW = 140    # Suchbereich für Matrix und Bezugsverweis ab Abschnittsbeginn
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


@dataclass
class FusionPrimitive:
    feature_id: int
    kind: str                       # "box", "cylinder" oder "sphere"
    operation: int                  # wie bei Extrusionen: 1 Vereinigen … 4 Neuer Körper
    matrix: tuple[float, ...]       # 16 Werte, Lage im Raum (cm)
    values: dict[str, float]        # Parameter nach Rolle, in cm


def _find_matrix(bulk: bytes, start: int) -> tuple[int, tuple[float, ...]] | None:
    """Sucht ab `start` eine starre 4×4-Matrix; gibt Abstand und Werte zurück."""
    for offset in range(_MATRIX_WINDOW):
        if start + offset + 128 > len(bulk):
            return None
        matrix = struct.unpack_from("<16d", bulk, start + offset)
        if matrix[12:16] != (0.0, 0.0, 0.0, 1.0):
            continue
        if not all(math.isfinite(value) and abs(value) < 1e6 for value in matrix):
            continue
        if all(abs(sum(matrix[row * 4 + column] ** 2 for column in range(3)) - 1) < 1e-6
               for row in range(3)):
            return offset, matrix
    return None


def _multiply(first, second) -> tuple[float, ...]:
    return tuple(sum(first[row * 4 + k] * second[k * 4 + column] for k in range(4))
                 for row in range(4) for column in range(4))


def read_primitives(bulk: bytes, directory: Directory, schema: dict[str, dict],
                    parameters: dict[int, list]) -> dict[int, FusionPrimitive]:
    """Alle Grundkörper des Entwurfs: Feature-ID -> Grundkörper."""
    def sections(name: str) -> dict[int, int]:
        guid = segment.class_guid(directory, schema, name)
        return segment.find_sections(bulk, directory, guid) if guid else {}

    reference_at = sections("DRMatrix")

    def reference_matrix(start: int) -> tuple[float, ...]:
        """Matrix des ersten DRMatrix-Verweises im Abschnitt (ohne Verweis: Einheitsmatrix)."""
        for position in range(start, min(start + _MATRIX_WINDOW, len(bulk) - 10)):
            if bulk[position] == 1 and bulk[position + 9:position + 11] == b"\x00\x00":
                (target,) = struct.unpack_from("<Q", bulk, position + 1)
                if target in reference_at:
                    found = _find_matrix(bulk, reference_at[target])
                    return found[1] if found else _IDENTITY
        return _IDENTITY

    primitives: dict[int, FusionPrimitive] = {}
    for class_name, (kind, operation_before) in _CLASSES.items():
        for feature_id, start in sections(class_name).items():
            found = _find_matrix(bulk, start)
            if found is None or found[0] < operation_before:
                continue
            offset, local = found
            (operation,) = struct.unpack_from("<I", bulk, start + offset - operation_before)
            values = {parameter.role: parameter.value for parameter in parameters.get(feature_id, [])}
            if operation in (1, 2, 3, 4) and values:
                primitives[feature_id] = FusionPrimitive(
                    feature_id, kind, operation, _multiply(reference_matrix(start), local), values)
    return primitives


def solid(kind: str, matrix: tuple[float, ...], sizes: dict[str, float]):
    """Volumen des Grundkörpers in Weltkoordinaten; `sizes` in Millimetern.

    Schlüssel je Art: box: x, y, z, offset_x, offset_y; cylinder: diameter, height;
    sphere: diameter. Gibt None zurück, wenn die Maße kein Volumen ergeben.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeSphere
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf

    if kind == "box":
        x, y, z = sizes["x"], sizes["y"], sizes["z"]
        if min(abs(x), abs(y), abs(z)) < 1e-9:
            return None
        corner = gp_Pnt(sizes.get("offset_x", 0.0), sizes.get("offset_y", 0.0), 0.0)
        opposite = gp_Pnt(corner.X() + x, corner.Y() + y, z)
        shape = BRepPrimAPI_MakeBox(corner, opposite).Shape()
    elif kind == "cylinder":
        radius, height = sizes["diameter"] / 2, sizes["height"]
        if radius <= 0 or abs(height) < 1e-9:
            return None
        # Negative Höhe: der Zylinder wächst gegen die z-Achse
        axis = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1 if height > 0 else -1))
        shape = BRepPrimAPI_MakeCylinder(axis, radius, abs(height)).Shape()
    elif kind == "sphere":
        if sizes["diameter"] <= 0:
            return None
        shape = BRepPrimAPI_MakeSphere(sizes["diameter"] / 2).Shape()
    else:
        return None

    # Lage: Drehung unverändert, Verschiebung von cm in mm
    placement = gp_Trsf()
    placement.SetValues(matrix[0], matrix[1], matrix[2], matrix[3] * 10,
                        matrix[4], matrix[5], matrix[6], matrix[7] * 10,
                        matrix[8], matrix[9], matrix[10], matrix[11] * 10)
    return BRepBuilderAPI_Transform(shape, placement, True).Shape()
