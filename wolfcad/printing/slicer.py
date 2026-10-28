# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Slicen mit dem Kern von Creality Print (Kommandozeile im Rechenmodus `--cli`, ohne Fenster und Bildschirm).

WolfCAD schreibt das Druckbett als 3MF-Projekt (printing/project3mf.py: Lage, Filament je Objekt, alle
Einstellungen) und lässt Creality Print daraus den G-Code rechnen:

    CrealityPrint --cli --slice 0 --need-gcode-file --outputdir <ordner> projekt.3mf

Am 2026-10-26 an Creality Print 7.3.0 ermittelt: Ohne `--cli` startet trotz der Schalter die Oberfläche; STL-Dateien
werden immer neu angeordnet (deshalb das Projekt); die Ausgabe ist plate_1.gcode im Ausgabeordner, das Ergebnis
steht zusätzlich in result.json (error_string, return_code). Die Rechnung läuft als eigener Prozess (QProcess im
Druck-Reiter), damit die Oberfläche bedienbar bleibt und ein Absturz des Slicers WolfCAD nicht trifft.
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import profiles


class SlicerError(Exception):
    """Slicen nicht möglich (Programm fehlt, Profilfehler, Modell außerhalb des Bauraums …)."""


def executable(install: Path | None = None) -> Path:
    """Das Programm des Slicers: unter Linux bin/CrealityPrint (entpacktes AppImage), unter Windows
    CrealityPrint.exe direkt im Installationsordner (so legen es Installer und Velopack-Paket ab)."""
    root = install or profiles.install_dir()
    if os.name == "nt":
        for candidate in (root / "CrealityPrint.exe", root / "bin" / "CrealityPrint.exe"):
            if candidate.exists():
                return candidate
        return root / "CrealityPrint.exe"
    return root / "bin" / "CrealityPrint"


def environment(install: Path | None = None) -> dict:
    """Umgebung wie im AppRun-Skript des AppImages (Bibliotheken der Installation, feste Sprache)."""
    root = install or profiles.install_dir()
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = f"{root / 'bin'}:{root / 'usr' / 'lib'}" + (
        ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    env["LC_ALL"] = "C"                 # ohne feste Sprache stürzt Creality Print auf manchen Systemen ab
    return env


def cli_environment(install: Path | None = None) -> dict:
    """Umgebung für den Rechenmodus: ohne Bildschirm – so kann auf keinen Fall ein Fenster aufgehen."""
    env = environment(install)
    for key in ("DISPLAY", "WAYLAND_DISPLAY"):
        env.pop(key, None)
    return env


def missing_libraries(install: Path | None = None) -> list[str]:
    """Systembibliotheken, die Creality Print braucht, aber fehlen (z. B. libwebkit2gtk-4.1)."""
    import subprocess
    binary = executable(install)
    if not binary.exists() or not shutil.which("ldd"):
        return []
    try:
        output = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, timeout=20,
                                env=environment(install)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({line.split("=>")[0].strip() for line in output.splitlines() if "not found" in line})


@dataclass
class Job:
    """Ein Slice-Auftrag: Arbeitsordner, Projektdatei und der fertige Befehl."""
    folder: Path
    arguments: list[str] = field(default_factory=list)

    @property
    def gcode(self) -> Path:
        return self.folder / "plate_1.gcode"

    @property
    def project(self) -> Path:
        return self.folder / "projekt.3mf"

    def result(self) -> dict:
        """Rückmeldung des Slicers (result.json); leer, wenn es keine gibt."""
        try:
            return json.loads((self.folder / "result.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}


def prepare(folder: Path, objects: list[tuple[str, object, int]], machine: profiles.Profile,
            process: profiles.Profile, filaments: list[profiles.Profile], install: Path | None = None) -> Job:
    """Schreibt das Projekt und baut den Aufruf. `objects`: (Name, Dreiecke in Bettkoordinaten, Filament ab 1)."""
    from . import project3mf
    binary = executable(install)
    if not binary.exists():
        raise SlicerError(f"Creality Print wurde nicht gefunden ({binary}). In den Einstellungen unter "
                          "„Druck“ den Ordner der entpackten Installation angeben.")
    missing = missing_libraries(install)
    if missing:
        raise SlicerError("Creality Print braucht noch Systembibliotheken: " + ", ".join(missing)
                          + " – unter Arch: sudo pacman -S --needed webkit2gtk-4.1")
    folder.mkdir(parents=True, exist_ok=True)
    for old in (*folder.glob("plate_*.gcode"), folder / "result.json"):
        if old.exists():
            old.unlink()
    job = Job(folder)
    project3mf.write(job.project, objects, project3mf.project_settings(machine, process, filaments))
    job.arguments = [str(binary), "--cli", "--slice", "0", "--need-gcode-file", "--allow-newer-file",
                     "--outputdir", str(folder), str(job.project)]
    return job


def gcode_from_project(project: Path, target: Path) -> Path | None:
    """Holt den G-Code aus einem geslicten 3MF (Metadata/plate_1.gcode), falls er nicht einzeln geschrieben wurde."""
    try:
        with zipfile.ZipFile(project) as archive:
            names = [name for name in archive.namelist() if name.startswith("Metadata/plate_")
                     and name.endswith(".gcode")]
            if not names:
                return None
            target.write_bytes(archive.read(sorted(names)[0]))
            return target
    except (OSError, zipfile.BadZipFile):
        return None


def open_in_creality_print(files: list[Path], install: Path | None = None) -> None:
    """Übergabe an das Original: Creality Print mit den Modellen/dem Projekt öffnen (eigenes Fenster)."""
    import subprocess
    binary = executable(install)
    if not binary.exists():
        raise SlicerError(f"Creality Print wurde nicht gefunden ({binary})")
    env = environment(install)                  # mit Fenster
    # Absturz beim Start in der echten Sitzung (2026-10-27: SIGSEGV in cairo über die mitgelieferte libpango 1.52):
    # Pango aus dem System nehmen (usr/lib-system-pango: alles außer libpango*) und GTK über X11 laufen lassen
    root = install or profiles.install_dir()
    without_pango = root / "usr" / "lib-system-pango"
    if without_pango.is_dir():
        env["LD_LIBRARY_PATH"] = f"{root / 'bin'}:{without_pango}"
    env["GDK_BACKEND"] = "x11"
    # eigentliche Absturzursache: die in Creality Print einkompilierte FreeType-Kopie (tt_apply_mvar) verträgt keine
    # variablen Schriften wie „Adwaita Sans“ – eigene Schriftkonfiguration ohne variable Schriften
    fonts = root.parent / "fonts-ohne-variable.conf"
    if fonts.exists():
        env["FONTCONFIG_FILE"] = str(fonts)
    subprocess.Popen([str(binary), *[str(path) for path in files]], env=env, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
