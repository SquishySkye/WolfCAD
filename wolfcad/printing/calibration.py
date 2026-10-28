# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Kalibrierdrucke wie im Menü „Kalibrierung“ von Creality Print: Temperaturturm, Druckvorlauf (Turm), Fluss
(Blöcke), Rückzug (Turm) und größter Volumenstrom.

Jede Kalibrierung liefert die Modelle (selbst erzeugt), Prozessänderungen für den Druck und eine Nachbearbeitung
des G-Codes, die je Höhe den zu prüfenden Wert setzt (Klipper-Befehle; der K2 Plus läuft mit Klipper). Nach dem
Druck liest man ab, welche Höhe bzw. welcher Block am besten aussieht – der Hinweistext nennt die Zuordnung.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..core import features as ft
from ..core import meshes, solids


@dataclass
class Calibration:
    title: str
    objects: list                                   # (Name, Dreiecke, Filament ab 1, Extras) wie für project3mf
    process: dict = field(default_factory=dict)     # Prozessänderungen nur für diesen Druck
    post: Callable[[str], str] | None = None        # Nachbearbeitung des fertigen G-Codes
    hint: str = ""                                  # wie das Ergebnis abzulesen ist


def _box(x: float, y: float, length: float, width: float, height: float, z: float = 0.0,
         hollow: float = 0.0) -> np.ndarray:
    """Dreiecke eines Quaders (Mitte x/y, Unterseite z); `hollow` > 0: rundum offener Rahmen dieser Wandstärke."""
    state = ft.BuildState()
    solids.Box(center_x=x, center_y=y, length=length, width=width, height=height, offset=z).apply(state)
    if hollow > 0:
        solids.Box(center_x=x, center_y=y, length=length - 2 * hollow, width=width - 2 * hollow, height=height + 2,
                   offset=z - 1, operation="Ausschneiden").apply(state)
    return meshes.tessellate(next(iter(state.bodies.values())), 0.02)


def _per_height(text: str, value_at: Callable[[float], str | None]) -> str:
    """Fügt nach jedem Schichtwechsel den Befehl für die neue Höhe ein (nur, wenn er sich ändert)."""
    lines, last, height = [], None, None
    for line in text.splitlines(keepends=True):
        lines.append(line)
        stripped = line.strip()
        if stripped.startswith((";Z:", ";:")):
            try:
                height = float(stripped.split(":", 1)[1])
            except ValueError:
                continue
            command = value_at(height)
            if command and command != last:
                lines.append(command + "\n")
                last = command
    return "".join(lines)


def temperature_tower(bed_centre=(175.0, 175.0), start: int = 240, end: int = 200, step: int = 5,
                      block: float = 10.0) -> Calibration:
    """Ein Block je Temperatur (unten heiß, oben kalt); je Block ein Überhang-Absatz, an dem man Fäden und Kanten
    vergleicht."""
    temps = list(range(start, end - 1, -step)) if start >= end else list(range(start, end + 1, step))
    parts = []
    for number, _temp in enumerate(temps):
        z = number * block
        parts.append(_box(bed_centre[0], bed_centre[1], 30, 12, block * 0.8, z))
        parts.append(_box(bed_centre[0] + 5, bed_centre[1], 40, 12, block * 0.2, z + block * 0.8))
    tower = np.concatenate(parts)

    def post(text: str) -> str:
        return _per_height(text, lambda h: f"M104 S{temps[min(int(h // block), len(temps) - 1)]}"
                           if h > 0 else None)

    hint = " · ".join(f"{i * block:.0f}–{(i + 1) * block:.0f} mm: {t} °C" for i, t in enumerate(temps))
    return Calibration("Temperaturturm", [("Temperaturturm", tower, 1, {})], {"enable_support": "0"}, post,
                       "Block mit den saubersten Kanten und wenigsten Fäden wählen und die Temperatur im Filament "
                       "übernehmen. " + hint)


def pressure_advance_tower(bed_centre=(175.0, 175.0), start: float = 0.0, end: float = 0.1, height: float = 50.0) \
        -> Calibration:
    """Hohler Turm mit scharfen Ecken; der Druckvorlauf steigt gleichmäßig mit der Höhe (wie Orcas „PA-Turm“)."""
    tower = _box(bed_centre[0], bed_centre[1], 60, 60, height, hollow=0.8)
    per_mm = (end - start) / height

    def post(text: str) -> str:
        return _per_height(text, lambda h: f"SET_PRESSURE_ADVANCE ADVANCE={start + per_mm * h:.4f}")

    return Calibration("Druckvorlauf", [("Druckvorlauf-Turm", tower, 1, {})],
                       {"wall_loops": "1", "top_shell_layers": "0", "bottom_shell_layers": "1",
                        "sparse_infill_density": "0%", "enable_support": "0"}, post,
                       f"Höhe mit den schärfsten Ecken messen: Druckvorlauf = {start} + {per_mm:.5f} × Höhe (mm). "
                       "Den Wert im Filament unter „Druckvorlauf“ eintragen.")


def flow_blocks(bed_centre=(175.0, 175.0), ratios=(0.90, 0.93, 0.96, 0.99, 1.02, 1.05)) -> Calibration:
    """Ein Block je Flussverhältnis (Fluss nur für diesen Block, wie im Original); man wählt die glatteste
    Oberseite."""
    objects = []
    columns = 3
    for number, ratio in enumerate(ratios):
        x = bed_centre[0] + (number % columns - (columns - 1) / 2) * 32
        y = bed_centre[1] + (number // columns - 0.5) * 32
        objects.append((f"Fluss {ratio:.2f}", _box(x, y, 25, 25, 3), 1,
                        {"settings": {"print_flow_ratio": f"{ratio:.3f}", "top_shell_layers": "5"}}))
    return Calibration("Fluss", objects, {"enable_support": "0"}, None,
                       "Block mit der glattesten, lückenlosen Oberseite wählen und sein Flussverhältnis × bisherigem "
                       "Filament-Fluss übernehmen.")


def retraction_tower(bed_centre=(175.0, 175.0), start: float = 0.0, end: float = 2.0, height: float = 40.0) \
        -> Calibration:
    """Zwei Säulen; die Rückzugslänge steigt je Millimeter Höhe – zwischen den Säulen sieht man die Fäden."""
    pillars = np.concatenate([_box(bed_centre[0] - 20, bed_centre[1], 8, 8, height),
                              _box(bed_centre[0] + 20, bed_centre[1], 8, 8, height)])
    per_mm = (end - start) / height
    retract = re.compile(r"^(G1 E)(-?\d*\.?\d+)( F\d+.*)?$")

    def post(text: str) -> str:
        out, height_now = [], 0.0
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if stripped.startswith((";Z:", ";:")):
                try:
                    height_now = float(stripped.split(":", 1)[1])
                except ValueError:
                    pass
            match = retract.match(stripped)
            # nur echte Rückzüge/Rückstellungen (kleine Längen) – Spülmengen beim Filamentwechsel bleiben
            if match and height_now > 0.5 and abs(float(match.group(2))) <= 3.0:
                length = start + per_mm * height_now
                sign = "-" if match.group(2).startswith("-") else ""
                line = f"{match.group(1)}{sign}{length:.3f}{match.group(3) or ''}\n"
            out.append(line)
        return "".join(out)

    return Calibration("Rückzug", [("Rückzug-Turm", pillars, 1, {})],
                       {"wipe": "0", "enable_support": "0"}, post,
                       f"Höhe ohne Fäden zwischen den Säulen messen: Rückzug = {start} + {per_mm:.3f} × Höhe (mm).")


def max_volumetric_speed(bed_centre=(175.0, 175.0), start: float = 5.0, end: float = 30.0, height: float = 50.0) \
        -> Calibration:
    """Vasenmodus-Zylinder; die Geschwindigkeit steigt mit der Höhe (M220), bis das Filament nicht mehr nachkommt."""
    state = ft.BuildState()
    solids.Cylinder(center_x=bed_centre[0], center_y=bed_centre[1], diameter=50, height=height).apply(state)
    tower = meshes.tessellate(next(iter(state.bodies.values())), 0.02)
    # Bahnquerschnitt im Vasenmodus ≈ Linienbreite × Schichthöhe (0,42 × 0,2 mm²); 100 % entsprechen der
    # Außenwand-Geschwindigkeit von 20 mm/s → 1,7 mm³/s; die Faktoren bilden start … end mm³/s ab
    base = 0.42 * 0.2 * 20.0
    per_mm = (end - start) / height

    def post(text: str) -> str:
        return _per_height(text, lambda h: f"M220 S{round((start + per_mm * h) / base * 100)}")

    return Calibration("Max. Volumenstrom", [("Volumenstrom-Turm", tower, 1, {})],
                       {"spiral_mode": "1", "outer_wall_speed": "20", "wall_loops": "1", "top_shell_layers": "0",
                        "sparse_infill_density": "0%", "enable_support": "0"}, post,
                       f"Höhe messen, ab der die Wand rau oder lückenhaft wird: Volumenstrom = {start} + "
                       f"{per_mm:.2f} × Höhe (mm³/s) – etwas darunter im Filament eintragen.")


CALIBRATIONS = {
    "Temperaturturm …": temperature_tower,
    "Druckvorlauf (Turm) …": pressure_advance_tower,
    "Fluss (Blöcke) …": flow_blocks,
    "Rückzug (Turm) …": retraction_tower,
    "Max. Volumenstrom …": max_volumetric_speed,
}
