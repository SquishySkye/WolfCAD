# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Drucker-, Filament- und Prozessprofile aus einer Creality-Print-Installation lesen.

Creality Print (Fork von OrcaSlicer, AGPL-3.0) bringt seine Profile als JSON-Dateien mit, z. B.
  resources/profiles/Creality/machine/Creality K2 Plus 0.4 nozzle.json
  resources/profiles/Creality/process/0.20mm Standard @Creality K2 Plus 0.4 nozzle.json
  resources/profiles/Creality/filament/Hyper PLA @Creality K2 Plus 0.4 nozzle.json
Jedes Profil erbt über „inherits“ von allgemeineren (… → fdm_creality_common → fdm_machine_common); erst die ganze
Kette ergibt alle Werte. Der Druck-Reiter zeigt die aufgelösten Werte an, eigene Änderungen werden als Überschreibungen
gemerkt und beim Slicen als eigene, vollständige Profildateien übergeben (so verlangt es die Kommandozeile).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Vorgabe-Ort der entpackten Creality-Print-Installation (vom Nutzer am 2026-10-26 freigegebener Download, v7.3.0)
DEFAULT_INSTALL = Path.home() / "Coding" / "Dateien" / "wolfcad" / "crealityprint" / "squashfs-root"
VENDOR = "Creality"
KINDS = ("machine", "process", "filament")


def install_dir() -> Path:
    """Die Creality-Print-Installation: Einstellung `druck/creality_pfad`, Umgebungsvariable oder Vorgabe."""
    configured = os.environ.get("WOLFCAD_CREALITY")
    if not configured:
        try:
            from PySide6.QtCore import QSettings
            configured = QSettings("WolfCAD", "WolfCAD").value("druck/creality_pfad", "") or ""
        except Exception:
            configured = ""
    return Path(configured) if configured else DEFAULT_INSTALL


def profile_root(install: Path | None = None) -> Path:
    return (install or install_dir()) / "resources" / "profiles" / VENDOR


@dataclass
class Profile:
    """Ein aufgelöstes Profil: alle Werte der Vererbungskette, eigene Änderungen getrennt."""
    kind: str                                   # machine, process oder filament
    name: str
    values: dict = field(default_factory=dict)  # aufgelöst (Basis bis eigenes Profil)
    changes: dict = field(default_factory=dict) # Änderungen des Nutzers in WolfCAD

    def get(self, key: str, default=None):
        return self.changes.get(key, self.values.get(key, default))

    def merged(self) -> dict:
        """Vollständiges Profil mit den Änderungen, fertig zum Übergeben an den Slicer."""
        data = dict(self.values)
        data.update(self.changes)
        data.pop("inherits", None)              # alles ist aufgelöst: keine Vererbung mehr nötig
        data["name"] = self.name + (" (WolfCAD)" if self.changes else "")
        data["from"] = "User"
        return data


class Library:
    """Alle Profile einer Installation; Vererbung wird beim Laden aufgelöst und gemerkt."""

    def __init__(self, install: Path | None = None):
        self.root = profile_root(install)
        self._raw: dict[tuple[str, str], dict] = {}
        self._resolved: dict[tuple[str, str], dict] = {}
        if not self.root.is_dir():
            return
        for kind in KINDS:
            for path in (self.root / kind).glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                self._raw[(kind, data.get("name", path.stem))] = data

    @property
    def available(self) -> bool:
        return bool(self._raw)

    def resolved(self, kind: str, name: str) -> dict:
        """Alle Werte eines Profils samt geerbter (das eigene Profil gewinnt)."""
        key = (kind, name)
        if key not in self._resolved:
            chain, seen = [], set()
            current = self._raw.get(key)
            while current is not None and current.get("name") not in seen:
                seen.add(current.get("name"))
                chain.append(current)
                parent = current.get("inherits")
                current = self._raw.get((kind, parent)) if parent else None
            values: dict = {}
            for data in reversed(chain):
                values.update(data)
            self._resolved[key] = values
        return self._resolved[key]

    def profile(self, kind: str, name: str) -> Profile:
        return Profile(kind, name, dict(self.resolved(kind, name)))

    def names(self, kind: str) -> list[str]:
        """Sichtbare Profile einer Art (keine reinen Basisprofile wie „fdm_machine_common“)."""
        return sorted(name for (k, name), data in self._raw.items()
                      if k == kind and data.get("instantiation", "true") == "true")

    def printers(self) -> list[str]:
        return self.names("machine")

    def compatible(self, kind: str, printer: str) -> list[str]:
        """Prozess- bzw. Filamentprofile, die für den Drucker freigegeben sind."""
        result = []
        for name in self.names(kind):
            allowed = self.resolved(kind, name).get("compatible_printers", [])
            if not allowed or printer in allowed:
                result.append(name)
        return result

    def default_process(self, printer: str) -> str:
        wanted = self.resolved("machine", printer).get("default_print_profile", "")
        options = self.compatible("process", printer)
        return wanted if wanted in options else (options[0] if options else "")

    def default_filament(self, printer: str) -> str:
        wanted = self.resolved("machine", printer).get("default_filament_profile", [""])
        wanted = wanted[0] if isinstance(wanted, list) and wanted else wanted
        options = self.compatible("filament", printer)
        return wanted if wanted in options else (options[0] if options else "")

    def bed(self, printer: str) -> tuple[list[tuple[float, float]], float]:
        """Druckbett als Umriss (mm) und Bauhöhe des Druckers."""
        values = self.resolved("machine", printer)
        area = values.get("printable_area", "0x0,220x0,220x220,0x220")
        if isinstance(area, list):
            area = ",".join(area)
        outline = [tuple(float(c) for c in corner.split("x")) for corner in area.split(",") if "x" in corner]
        return outline, float(values.get("printable_height", 250))


def find_printer(library: Library, model_hint: str) -> str:
    """Ein Profil zum Druckermodell aus dem Netz (z. B. „K2Plus-1A2B“ → „Creality K2 Plus 0.4 nozzle“)."""
    hint = model_hint.lower().replace("-", " ").split()[0] if model_hint else ""
    hint = hint.replace("k2plus", "k2 plus").replace("k1max", "k1 max")
    for name in library.printers():
        if hint and hint in name.lower() and "0.4" in name:
            return name
    return next((name for name in library.printers() if "0.4" in name), library.printers()[0]
                if library.printers() else "")
