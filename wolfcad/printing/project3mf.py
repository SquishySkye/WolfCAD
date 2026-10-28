# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Druckbett als 3MF-Projekt im Format von Creality Print/OrcaSlicer.

Warum ein Projekt statt einzelner STL-Dateien: Beim Slicen von STL-Dateien ordnet Creality Print die Objekte
selbst neu an (2026-10-26 nachgemessen: zwei Würfel bei (50, 60) und (290, 280) landeten bei (175, 164) und
(175, 186)); einen Schalter dagegen gibt es nicht. Ein 3MF-Projekt trägt dagegen die Lage jedes Objekts, sein
Filament (CFS-Fach) und die vollständigen Einstellungen – so rechnet Creality Print genau das Bett aus WolfCAD, und
dasselbe Projekt lässt sich im Original öffnen.

Inhalt (Aufbau des 3MF-Formats der Bambu-/Orca-Familie):
  3D/3dmodel.model                 Netze der Objekte (Bettkoordinaten) und Bauliste
  Metadata/project_settings.config alle Einstellungen als JSON (Filamentwerte als Liste, je Filament ein Eintrag)
  Metadata/model_settings.config   je Objekt Name und Extruder (Filament), Zuordnung zur Platte 1
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np

from . import profiles

APPLICATION = "BambuStudio-01.09.00.70"     # mit diesem Kopf liest Creality Print das Projekt als eigenes Format und behält die Lage der Objekte (mit „CrealityPrint-…“ ordnete es sie neu an)
_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="text/plain"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""


# Filamentschlüssel, die im Projekt Einzelwerte bleiben (alle übrigen: eine Liste mit je einem Wert pro Filament,
# auch wenn das Profil nur einen Einzelwert nennt – sonst „filament_is_support's count 1 not equal to …“)
_FILAMENT_SCALARS = {"filament_id"}


def project_settings(machine: profiles.Profile, process: profiles.Profile,
                     filaments: list[profiles.Profile]) -> dict:
    """Alle Einstellungen in einem Wörterbuch: Drucker und Prozess wie sie sind, Filamentwerte je Filament zu einer
    Liste zusammengeführt (so stehen sie in Projekten des Originals)."""
    settings: dict = {}
    for profile in (machine, process):
        for key, value in profile.merged().items():
            settings[key] = value
    keys = []
    merged_filaments = [filament.merged() for filament in filaments]
    for data in merged_filaments:
        keys += [key for key in data if key not in keys]
    for key in keys:
        if key in ("name", "from", "inherits", "type", "instantiation", "compatible_printers",
                   "compatible_printers_condition", "setting_id", "filament_settings_id"):
            continue
        first = next(data[key] for data in merged_filaments if key in data)
        if key in _FILAMENT_SCALARS:
            # wenige Filamentschlüssel sind im Projekt Einzelwerte (am Slicer ermittelt) – als Liste bricht das
            # Einlesen ab („type must be string, but is array“)
            settings.setdefault(key, first[0] if isinstance(first, list) and first else first)
            continue
        values = []
        for data in merged_filaments:
            value = data.get(key, first)
            values.append(value[0] if isinstance(value, list) and value else value)
        settings[key] = values
    # Die Zahl der echten Filamente (CFS-Fächer) liest Creality Print aus der Farbliste; fehlt sie, hält es jedes
    # Filament ab dem zweiten für ein „gemischtes“ und bricht ab („Unable to resolve a mixed filament“)
    palette = ["#FFFFFF", "#000000", "#E53935", "#1E88E5", "#43A047", "#FDD835", "#8E24AA", "#FB8C00"]
    colours = []
    for number, data in enumerate(merged_filaments):
        colour = data.get("default_filament_colour") or data.get("filament_colour")
        colour = colour[0] if isinstance(colour, list) and colour else colour
        colours.append(colour if isinstance(colour, str) and colour.startswith("#") else palette[number % 8])
    settings["filament_colour"] = colours
    settings["print_settings_id"] = process.name
    settings["printer_settings_id"] = machine.name
    settings["filament_settings_id"] = [filament.name for filament in filaments]
    for key in ("name", "from", "inherits", "type", "instantiation"):
        settings.pop(key, None)
    settings.setdefault("curr_bed_type", "Textured PEI Plate")
    # Werte sind im Projekt immer Text bzw. Listen von Text
    return {key: ([str(item) for item in value] if isinstance(value, list) else str(value))
            for key, value in settings.items()}


def paint_code(state: int) -> str:
    """Bemalung eines ganzen Dreiecks im Format des Originals (TriangleSelector, als Hex-Text rückwärts):
    Zustand 1 → „4“, 2 → „8“, ab 3 → Kennung C plus (Zustand − 3) davor, z. B. 3 → „0C“."""
    if state <= 0:
        return ""
    if state < 3:
        return format(state << 2, "X")
    return format(state - 3, "X") + "C"


def _mesh_xml(triangles: np.ndarray, supports=None, colours=None) -> str:
    """Netz als 3MF-Ecken und -Dreiecke (gemeinsame Ecken zusammengefasst), mit Bemalung je Dreieck."""
    points = np.round(triangles.reshape(-1, 3), 5)
    unique, inverse = np.unique(points, axis=0, return_inverse=True)
    faces = inverse.reshape(-1, 3)
    keep = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    vertices = "\n".join(f'     <vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in unique)
    rows = []
    for index in np.flatnonzero(keep):
        a, b, c = faces[index]
        extra = ""
        if supports is not None and supports[index]:
            extra += f' paint_supports="{paint_code(int(supports[index]))}"'
        if colours is not None and colours[index]:
            extra += f' paint_color="{paint_code(int(colours[index]))}"'
        rows.append(f'     <triangle v1="{a}" v2="{b}" v3="{c}"{extra}/>')
    return f"   <mesh>\n    <vertices>\n{vertices}\n    </vertices>\n    <triangles>\n" + "\n".join(rows) + \
           "\n    </triangles>\n   </mesh>"


def write(path: Path, objects: list, settings: dict) -> Path:
    """Schreibt das Projekt. `objects`: (Name, Dreiecke in Bettkoordinaten, Filamentnummer ab 1) oder dazu noch
    ein Wörterbuch mit „settings“ (Einstellungen nur für dieses Objekt), „supports“ und „colours“ (Bemalung je
    Dreieck, siehe plate.PlateObject)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    model_parts, build, config = [], [], []
    for number, entry in enumerate(objects, 1):
        name, triangles, filament = entry[:3]
        extra = entry[3] if len(entry) > 3 else {}
        mesh = _mesh_xml(np.asarray(triangles, float), extra.get("supports"), extra.get("colours"))
        model_parts.append(f'  <object id="{number}" type="model">\n{mesh}\n  </object>')
        # Einstellungen nur für dieses Objekt stehen wie im Original als Metadaten am Objekt
        own = "".join(f'    <metadata key={quoteattr(key)} value={quoteattr(str(value))}/>\n'
                      for key, value in extra.get("settings", {}).items())
        build.append(f'  <item objectid="{number}" transform="1 0 0 0 1 0 0 0 1 0 0 0" printable="1"/>')
        config.append(f'  <object id="{number}">\n'
                      f'    <metadata key="name" value={quoteattr(name)}/>\n'
                      f'    <metadata key="extruder" value="{filament}"/>\n' + own +
                      f'    <part id="1" subtype="normal_part">\n'
                      f'      <metadata key="name" value={quoteattr(name)}/>\n'
                      '      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                      f'      <metadata key="extruder" value="{filament}"/>\n'
                      "    </part>\n  </object>")
    instances = "\n".join(f'    <model_instance>\n      <metadata key="object_id" value="{number}"/>\n'
                          '      <metadata key="instance_id" value="0"/>\n'
                          f'      <metadata key="identify_id" value="{number}"/>\n    </model_instance>'
                          for number in range(1, len(objects) + 1))
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">\n'
             f' <metadata name="Application">{APPLICATION}</metadata>\n'
             ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
             ' <metadata name="Title">WolfCAD</metadata>\n'
             ' <resources>\n' + "\n".join(model_parts) + '\n </resources>\n'
             ' <build>\n' + "\n".join(build) + '\n </build>\n</model>\n')
    model_settings = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n' + "\n".join(config) +
                      '\n  <plate>\n    <metadata key="plater_id" value="1"/>\n'
                      '    <metadata key="plater_name" value=""/>\n    <metadata key="locked" value="false"/>\n'
                      f'{instances}\n  </plate>\n</config>\n')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("3D/3dmodel.model", model)
        archive.writestr("Metadata/project_settings.config", json.dumps(settings, indent=1, ensure_ascii=False))
        archive.writestr("Metadata/model_settings.config", model_settings)
    return path
