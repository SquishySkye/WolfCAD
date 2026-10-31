# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""G-Code lesen für die Vorschau im Druck-Reiter: Bahnen je Schicht und Art (Außenwand, Füllung …), Druckzeit und
Materialverbrauch aus den Kommentaren, die Creality Print/OrcaSlicer schreiben.

Gelesen wird nur, was die Vorschau braucht: G0/G1 (auch G2/G3 als Sehnenzug), absolute oder relative Extrusion
(M82/M83), Schichtwechsel (;LAYER_CHANGE / ;Z:) und Bahnart (;TYPE:…). Fahrwege ohne Extrusion werden getrennt
gesammelt (in der Vorschau abschaltbar wie im Original).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Bahnarten mit deutscher Bezeichnung und Farbe (an die Farben des Originals angelehnt)
FEATURES = {
    "Outer wall": ("Außenwand", (1.00, 0.55, 0.10)),
    "Inner wall": ("Innenwand", (1.00, 0.85, 0.25)),
    "Overhang wall": ("Überhangwand", (0.30, 0.30, 1.00)),
    "Sparse infill": ("Füllung", (0.70, 0.18, 0.18)),
    "Internal solid infill": ("Massive Füllung", (0.62, 0.25, 0.55)),
    "Top surface": ("Oberfläche", (0.95, 0.30, 0.30)),
    "Bottom surface": ("Unterseite", (0.40, 0.70, 0.90)),
    "Bridge": ("Brücke", (0.30, 0.50, 0.75)),
    "Gap infill": ("Lückenfüllung", (1.00, 1.00, 1.00)),
    "Skirt": ("Saum", (0.00, 0.53, 0.50)),
    "Brim": ("Rand", (0.00, 0.53, 0.50)),
    "Support": ("Stütze", (0.10, 0.75, 0.25)),
    "Support interface": ("Stützenschnittstelle", (0.20, 0.90, 0.45)),
    "Support transition": ("Stützenübergang", (0.20, 0.60, 0.30)),
    "Prime tower": ("Reinigungsturm", (0.75, 0.75, 0.75)),
    "Ironing": ("Bügeln", (1.00, 0.55, 0.55)),
    "Custom": ("Sonstiges", (0.55, 0.55, 0.55)),
}
OTHER = "Custom"


@dataclass
class Toolpath:
    """Die gelesenen Bahnen: je Art ein Feld von Strecken (Anfang, Ende) mit Schichtnummer."""
    segments: dict[str, np.ndarray] = field(default_factory=dict)     # Art -> (n, 2, 3) float32
    layers: dict[str, np.ndarray] = field(default_factory=dict)       # Art -> (n,) Schichtnummer
    travel: np.ndarray = field(default_factory=lambda: np.zeros((0, 2, 3), np.float32))
    travel_layers: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))
    layer_heights: list[float] = field(default_factory=list)          # Z je Schicht
    info: dict[str, str] = field(default_factory=dict)                # Druckzeit, Filament …
    # Runde 92 (Ablauf wie im Original): laufende Nummer jeder Strecke in Druckreihenfolge (über alle Arten
    # hinweg, Fahrwege eingeschlossen) und Bahnbreite aus „;WIDTH:“ (für die Darstellung als Körper)
    orders: dict[str, np.ndarray] = field(default_factory=dict)       # Art -> (n,) int64, aufsteigend
    widths: dict[str, np.ndarray] = field(default_factory=dict)       # Art -> (n,) float32 in mm
    travel_orders: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    layer_first: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))    # erste Nummer je Schicht
    layer_last: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))     # letzte Nummer je Schicht

    @property
    def layer_count(self) -> int:
        return len(self.layer_heights)

    def thickness(self, layer: int) -> float:
        """Schichtdicke: Abstand zur Schicht darunter (die erste Schicht liegt auf dem Bett)."""
        if not self.layer_heights:
            return 0.2
        layer = min(max(layer, 0), len(self.layer_heights) - 1)
        below = self.layer_heights[layer - 1] if layer > 0 else 0.0
        return float(min(max(self.layer_heights[layer] - below, 0.04), 1.0))


_NUMBER = re.compile(r"([XYZEFIJR])(-?\d*\.?\d+)")
_INFO_KEYS = {
    "estimated printing time (normal mode)": "Druckzeit",
    "total estimated time": "Druckzeit",
    "filament used [mm]": "Filament (mm)",
    "filament used [g]": "Filament (g)",
    "total filament used [g]": "Filament (g)",
    "filament cost": "Kosten",
    "total layers count": "Schichten",
}


def read(path: str | Path, max_segments: int = 6_000_000) -> Toolpath:
    """Liest eine G-Code-Datei; sehr große Dateien werden bis `max_segments` Strecken gelesen."""
    collected: dict[str, list] = {}
    layer_of: dict[str, list] = {}
    order_of: dict[str, list] = {}
    width_of: dict[str, list] = {}
    travel, travel_layer, travel_order = [], [], []
    width = 0.45                        # Bahnbreite, bis der G-Code eine nennt (;WIDTH:)
    heights: list[float] = []
    info: dict[str, str] = {}
    x = y = z = e = 0.0
    relative_e = False
    relative_xyz = False
    kind = OTHER
    layer = -1
    count = 0
    implicit_first = marked = False     # erste Schicht nur aus dem Startcode erschlossen / Schichtmarken gesehen
    with open(path, encoding="utf-8", errors="replace") as source:
        for raw in source:
            line = raw.strip()
            if not line:
                continue
            if line[0] == ";":
                text = line[1:].strip()
                if text.startswith("WIDTH:"):
                    try:
                        width = float(text[6:])
                    except ValueError:
                        pass
                elif text.startswith("TYPE:"):
                    kind = text[5:].strip()
                    if kind not in FEATURES:
                        kind = OTHER
                elif text.startswith("LAYER_CHANGE"):
                    marked = True
                    layer += 1
                    heights.append(z)
                elif (text.startswith("Z:") or text.startswith(":")) and heights:
                    # OrcaSlicer schreibt „;Z:0.2“, Creality Print 7 „;:0.2“
                    try:
                        heights[-1] = float(text.split(":", 1)[1])
                    except ValueError:
                        pass
                elif "=" in text:
                    key, _, value = text.partition("=")
                    label = _INFO_KEYS.get(key.strip().lower())
                    if label:
                        info[label] = value.strip()
                continue
            code, _, _comment = line.partition(";")
            parts = code.split(None, 1)
            if not parts:
                continue
            command = parts[0].upper()
            if command == "M83":
                relative_e = True
            elif command == "M82":
                relative_e = False
            elif command == "G91":
                relative_xyz = True
            elif command == "G90":
                relative_xyz = False
            elif command == "G92":
                values = dict((k, float(v)) for k, v in _NUMBER.findall(parts[1] if len(parts) > 1 else ""))
                e = values.get("E", e)
            elif command in ("G0", "G1", "G2", "G3") and count < max_segments:
                values = dict((k, float(v)) for k, v in _NUMBER.findall(parts[1] if len(parts) > 1 else ""))
                nx = x + values["X"] if relative_xyz and "X" in values else values.get("X", x)
                ny = y + values["Y"] if relative_xyz and "Y" in values else values.get("Y", y)
                nz = z + values["Z"] if relative_xyz and "Z" in values else values.get("Z", z)
                extruding = False
                if "E" in values:
                    amount = values["E"] if relative_e else values["E"] - e
                    e = e + values["E"] if relative_e else values["E"]
                    extruding = amount > 1e-6
                if layer < 0 and extruding:
                    layer = 0               # Datei ohne ;LAYER_CHANGE: die erste gedruckte Höhe ist Schicht 1
                    heights.append(nz)
                    implicit_first = True
                points = [(nx, ny, nz)]
                if command in ("G2", "G3") and ("I" in values or "J" in values):
                    points = _arc(x, y, nx, ny, nz, values.get("I", 0.0), values.get("J", 0.0), command == "G2")
                start = (x, y, z)
                for point in points:
                    if (point[0], point[1]) != (start[0], start[1]) or point[2] != start[2]:
                        if extruding:
                            collected.setdefault(kind, []).append((start, point))
                            layer_of.setdefault(kind, []).append(max(layer, 0))
                            order_of.setdefault(kind, []).append(count)
                            width_of.setdefault(kind, []).append(width)
                        elif (point[0], point[1]) != (start[0], start[1]):
                            travel.append((start, point))
                            travel_layer.append(max(layer, 0))
                            travel_order.append(count)
                        count += 1
                    start = point
                x, y, z = nx, ny, nz
    shift = 0
    if implicit_first and marked and len(heights) > 1:
        # Extrusion vor der ersten Schichtmarke (Reinigungslinie im Startcode): zählt zur ersten echten Schicht,
        # damit die Schichtzahl der des Originals entspricht
        heights, shift = heights[1:], 1
    path_data = Toolpath(layer_heights=heights, info=info)
    for name, items in collected.items():
        path_data.segments[name] = np.asarray(items, dtype=np.float32).reshape(-1, 2, 3)
        path_data.layers[name] = np.maximum(np.asarray(layer_of[name], dtype=np.int32) - shift, 0)
        path_data.orders[name] = np.asarray(order_of[name], dtype=np.int64)
        path_data.widths[name] = np.asarray(width_of[name], dtype=np.float32)
    if travel:
        path_data.travel = np.asarray(travel, dtype=np.float32).reshape(-1, 2, 3)
        path_data.travel_layers = np.maximum(np.asarray(travel_layer, dtype=np.int32) - shift, 0)
        path_data.travel_orders = np.asarray(travel_order, dtype=np.int64)
    _layer_bounds(path_data)
    return path_data


def _layer_bounds(path: Toolpath) -> None:
    """Erste und letzte laufende Nummer der gedruckten Strecken je Schicht (für den Ablauf-Regler)."""
    count = max(path.layer_count, 1)
    first = np.full(count, np.iinfo(np.int64).max, dtype=np.int64)
    last = np.full(count, -1, dtype=np.int64)
    for kind, layers in path.layers.items():
        if len(layers):
            clipped = np.minimum(layers, count - 1)
            np.minimum.at(first, clipped, path.orders[kind])
            np.maximum.at(last, clipped, path.orders[kind])
    # Schichten ohne gedruckte Strecke: an die Nachbarn anschließen, damit der Regler nicht springt
    for layer in range(count):
        if last[layer] < 0:
            last[layer] = last[layer - 1] if layer else 0
            first[layer] = last[layer]
    path.layer_first, path.layer_last = first, last


def solid_triangles(segments: np.ndarray, widths: np.ndarray, thickness: np.ndarray) -> np.ndarray:
    """Strecken als flache Stränge wie im Original (Runde 92): je Strecke ein Quader aus Oberseite und zwei
    Flanken (die Unterseite verdeckt die Schicht darunter), Breite = Bahnbreite, Höhe = Schichtdicke, an den
    Enden um eine halbe Breite verlängert, damit Ecken geschlossen wirken. Ergebnis: (n, 18, 3) float32."""
    start, end = segments[:, 0].astype(np.float32), segments[:, 1].astype(np.float32)
    along = end - start
    along[:, 2] = 0.0
    length = np.linalg.norm(along, axis=1, keepdims=True)
    flat = length[:, 0] < 1e-6
    along = np.where(flat[:, None], np.array([1.0, 0.0, 0.0], np.float32), along / np.maximum(length, 1e-6))
    half = (widths.astype(np.float32) / 2)[:, None]
    side = np.stack([-along[:, 1], along[:, 0], np.zeros(len(along), np.float32)], axis=1) * half
    start, end = start - along * half * 0.8, end + along * half * 0.8
    down = np.zeros_like(start)
    down[:, 2] = -thickness.astype(np.float32)
    a_l, a_r, b_l, b_r = start + side, start - side, end + side, end - side
    corners = [a_l, b_l, b_r, a_l, b_r, a_r,                            # Oberseite
               a_l, a_l + down, b_l + down, a_l, b_l + down, b_l,      # linke Flanke
               a_r, b_r, b_r + down, a_r, b_r + down, a_r + down]      # rechte Flanke
    return np.stack(corners, axis=1).astype(np.float32)


def _arc(x0, y0, x1, y1, z1, i, j, clockwise, step=1.0):
    """G2/G3-Bogen als Sehnenzug (Punkte ohne den Anfang)."""
    cx, cy = x0 + i, y0 + j
    radius = math.hypot(i, j)
    start = math.atan2(y0 - cy, x0 - cx)
    end = math.atan2(y1 - cy, x1 - cx)
    sweep = end - start
    if clockwise and sweep >= 0:
        sweep -= 2 * math.pi
    elif not clockwise and sweep <= 0:
        sweep += 2 * math.pi
    pieces = max(2, int(abs(sweep) * radius / step))
    return [(cx + radius * math.cos(start + sweep * k / pieces), cy + radius * math.sin(start + sweep * k / pieces),
             z1) for k in range(1, pieces + 1)]


def header_thumbnail(path: str | Path) -> bytes | None:
    """Das eingebettete Vorschaubild (PNG) aus dem Kopf der Datei, falls vorhanden."""
    import base64
    lines, inside = [], False
    with open(path, encoding="utf-8", errors="replace") as source:
        for number, raw in enumerate(source):
            text = raw.strip()
            if text.startswith("; thumbnail begin"):
                inside, lines = True, []
            elif text.startswith("; thumbnail end"):
                try:
                    return base64.b64decode("".join(lines))
                except ValueError:
                    return None
            elif inside:
                lines.append(text.lstrip("; ").strip())
            if number > 20000 and not inside:
                return None
    return None
