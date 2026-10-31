<p align="center"><img src="assets/wolfcad.svg" width="120" alt="WolfCAD-Logo"></p>

# WolfCAD

**Parametrisches 3D-CAD mit Timeline, 3D-Druck und Physik-Tests – freie Software unter GPL-3.0.**

*English summary: WolfCAD is a free (GPL-3.0-or-later) parametric 3D CAD program with a feature timeline, sketches
with constraints, solid/surface/sheet-metal/mesh tools, a built-in 3D-printing workspace (slicing with the bundled
Creality Print) and physics tests (static stress via FEM, air/water flow, buoyancy). The user interface is German.*

## Download

Fertige Pakete stehen unter [**Releases**](../../releases):

| System | Datei | Hinweise |
|---|---|---|
| Linux (x86_64) | `WolfCAD-<version>-x86_64.AppImage` | ausführbar machen und starten – keine Installation nötig |
| Windows 10/11 (64 Bit) | `WolfCAD-<version>-Windows-Setup.exe` | Installer; legt einen Startmenü-Eintrag an |

Beide Pakete bringen alles mit: eigene Python-Laufzeit, Geometriekern (OpenCASCADE) und den Slicer Creality Print.

```sh
chmod +x WolfCAD-*-x86_64.AppImage
./WolfCAD-*-x86_64.AppImage
```

Voraussetzungen: 64-Bit-System, Grafik mit OpenGL 3.3, unter Linux glibc ≥ 2.28 (praktisch jede aktuelle Distribution).

## Funktionen

- **Parametrische Timeline:** jeder Schritt bleibt änderbar, Werte auch als Ausdrücke und Parameter.
  Werkzeuge starten bei 0 und werden direkt im Modell am Pfeil aufgezogen.
- **Skizzen** mit Abhängigkeiten und Bemaßungen; DXF/SVG einfügen.
- **Volumenkörper:** Extrusion, Drehen, Sweep, Erhebung, Rippe, Steg, Bohrung, Gewinde, Abrunden, Fase, Schale,
  Schräge, Drücken/Ziehen, Teilen, Kombinieren, Muster, Spiegeln, Skalieren, Verschieben, Torus, Spule, Rohr und mehr.
- **Zahnräder:** Evolventenverzahnung aus Modul, Zähnezahl und Eingriffswinkel – Stirnrad, schräg- und
  pfeilverzahnt, Hohlrad und Zahnstange, mit Bohrung und Flankenspiel.
- **Formen (Sculpting):** Netze mit Pinseln wie in Blender formen (Zeichnen, Greifen, Glätten, Maske, Symmetrie,
  Neu vernetzen, dynamische Topologie).
- **Flächen, Blech und Netze:** Flächenwerkzeuge, Blech mit Abwicklung und Zuschnitt (DXF), Netze reparieren,
  glätten, schneiden und in Volumenkörper umwandeln.
- **Baugruppen:** Komponenten, Gelenke, starre Gruppen, Kollisionsprüfung.
- **Import/Export:** STEP und STL; Netze auch aus OBJ und 3MF, mehrere Dateien auf einmal. `.f3d`-Dateien lassen sich importieren; übernommen werden die gespeicherten Körper.
- **3D-Druck:** eigener Arbeitsbereich mit Druckbett, „Auf Fläche legen“, Profilen für Drucker, Filament und
  Prozess. Objekte am Pfeil verschieben und an Ringen drehen; mehrere Modelle (STL, OBJ, 3MF, STEP) auf einmal
  laden oder hereinziehen. Vorschau der Druckbahnen als Körper mit Ablauf-Regler. Geslict wird mit dem mitgelieferten Creality Print. Das Hochladen an Drucker mit Moonraker zeigt den
  Fortschritt an.
- **Physik-Tests:**
  - Festigkeit: statische FEM mit TET10-Elementen über gmsh und scipy.
  - Strömung: Luft und Wasser mit Lattice-Boltzmann (D3Q19).
  - Auftrieb.
  - Eigene Physik-Timeline, die auf Änderungen am Modell live neu rechnet.

> **Hinweis zur Physik:** Die Ergebnisse sind Näherungen am Modell und ersetzen keine Prüfung oder Freigabe des
> echten Bauteils.

## Aus dem Quelltext starten

Benötigt Python 3.14 (64 Bit).

```sh
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m wolfcad            # Windows: .venv\Scripts\python -m wolfcad
```

Den Slicer findet WolfCAD über die Umgebungsvariable `WOLFCAD_CREALITY`, die auf den Ordner einer Creality-Print-
Installation zeigt (bzw. über *Einstellungen → Druck*).

Tests:

```sh
.venv/bin/python -m pytest -q tests/
```

## Pakete bauen

- **Linux-AppImage:** `packaging/build_appimage.sh [ausgabeordner]` (Einzelheiten im Kopf des Skripts).
- **Windows-Installer:** wird von GitHub Actions gebaut (`.github/workflows/release.yml`, Skript
  `packaging/windows/build_windows.ps1`, Installer mit Inno Setup).

## Bekannte Einschränkungen

- Die Oberfläche ist deutsch.
- Unter Windows fehlt der Absturzschutz des Geometriekerns: Riskante Schritte werden unter Linux zuerst zur Probe in
  einem eigenen Prozess gerechnet, dafür braucht es `fork`. Ein Absturz des Kerns beendet unter Windows deshalb das
  Programm; der Entwurf lässt sich beim nächsten Start wiederherstellen.
- Das Kamerabild des Druckers braucht GStreamer aus dem System und ist bisher nur unter Linux nutzbar.

## Unterstützen

Wenn dir WolfCAD gefällt und du die Entwicklung unterstützen möchtest, freue ich mich über einen Kaffee auf
[**Ko-fi**](https://ko-fi.com/squishyskye) ☕ – danke! Fehlerberichte und Ideen sind genauso willkommen: einfach ein
[Issue](../../issues) anlegen.

## Lizenz

Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden.

WolfCAD ist freie Software unter der **GNU General Public License, Version 3 oder später** (GPL-3.0-or-later), siehe
[LICENSE](LICENSE). Es wird ohne jede Gewährleistung bereitgestellt.

Die Pakete enthalten Software Dritter unter deren eigenen Lizenzen (u. a. Creality Print unter AGPL-3.0, Qt/PySide6
unter LGPL-3.0, OpenCASCADE unter LGPL-2.1, gmsh unter GPL-2.0-or-later). Die Liste mit Quellen steht in
[packaging/LIZENZEN.txt](packaging/LIZENZEN.txt). Der Quelltext der mitgelieferten Programme unter GPL/AGPL liegt
jedem Release in genau der verwendeten Fassung bei.

## Marken

Autodesk und Fusion sind Marken der Autodesk, Inc. Creality und Creality Print sind Marken von Shenzhen Creality 3D
Technology Co., Ltd. WolfCAD ist ein unabhängiges Projekt, steht mit keinem dieser Unternehmen in Verbindung und wird
von ihnen weder unterstützt noch geprüft. Die Namen werden nur genannt, um anzugeben, mit welchen Dateiformaten und
Programmen WolfCAD zusammenarbeitet. WolfCAD enthält keine Software, Grafiken oder Daten von Autodesk. Alle Symbole und
Grafiken sind eigene Arbeiten.
