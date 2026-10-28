# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Zuordnung der Datenklassen in .f3d-Dateien (GUID -> Klassen- und Feldnamen), falls lokal vorhanden.

Die Segmentdaten einer .f3d-Datei nennen ihre Klassen nur über GUIDs. Liegt eine solche Zuordnung als JSON-Datei vor,
kann der Import zusätzlich den Konstruktionsverlauf lesen; ohne sie werden nur die gespeicherten Körper übernommen.
WolfCAD liefert diese Datei nicht mit und enthält kein Werkzeug, das sie aus fremder Software gewinnt.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_TARGET = Path.home() / ".local/share/wolfcad/fusion_schema.json"


def load(path: Path = DEFAULT_TARGET) -> dict[str, dict]:
    """Lädt eine vorhandene Zuordnung; ohne Datei ein leeres Verzeichnis (dann nur Körper-Import)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
