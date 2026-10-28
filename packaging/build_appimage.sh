#!/usr/bin/env bash
# WolfCAD als AppImage bauen: eine Datei, die auf üblichen Linux-Systemen (x86_64, glibc ≥ 2.28) ohne Installation
# läuft. Enthalten sind eine eigene Python-Laufzeit (python-build-standalone), nur die Pakete, die WolfCAD wirklich
# braucht (ohne vtk, matplotlib, Qt-WebEngine …), das Programm samt Videos und der Slicer Creality Print 7.3.
#
# Aufruf:  packaging/build_appimage.sh [ausgabeordner]
# Braucht: die heruntergeladenen Werkzeuge in $WERKZEUGE (Python-Archiv, appimagetool), die entpackte
#          Creality-Print-Installation in $CREALITY und Internet (oder einen pip-Zwischenspeicher) für die Pakete.
set -euo pipefail

PROJEKT="$(cd "$(dirname "$0")/.." && pwd)"
AUSGABE="${1:-$HOME/Coding/Dateien/wolfcad/paket}"
WERKZEUGE="${WERKZEUGE:-$HOME/Coding/Dateien/wolfcad/paket/werkzeuge}"
CREALITY="${CREALITY:-$HOME/Coding/Dateien/wolfcad/crealityprint}"
PYTHON_ARCHIV="$(ls "$WERKZEUGE"/cpython-3.14*-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz | head -1)"
APPDIR="$AUSGABE/WolfCAD.AppDir"
# Fassung aus dem Programm (wolfcad/__init__.py), nicht aus dem Datum – sonst passt der Dateiname nach Mitternacht
# nicht mehr zum Release
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PROJEKT/wolfcad/__init__.py")"

# Laufzeit-Pakete mit festen Fassungen (wie in der Entwicklungsumgebung geprüft). Qt nur die Grundmodule
# (PySide6-Essentials). vtk braucht OCP für einige seiner Bibliotheken – davon bleiben unten nur diese übrig.
PAKETE=(
  "cadquery-ocp==8.0.1.1.0" "cadquery-ocp-proxy==8.0.1.1.0" "vtk==9.6.2"
  "PySide6-Essentials==6.12.0" "shiboken6==6.12.0"
  "numpy==2.5.4" "scipy==1.18.1" "gmsh==4.15.2" "pillow==12.3.0"
)

echo "== AppDir in $APPDIR"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr" "$APPDIR/usr/app"

echo "== Python-Laufzeit"
tar -xzf "$PYTHON_ARCHIV" -C "$APPDIR/usr"          # ergibt $APPDIR/usr/python
PY="$APPDIR/usr/python/bin/python3"

echo "== Pakete (ohne Abhängigkeiten, Liste oben)"
"$PY" -m pip install --quiet --no-deps --no-compile --disable-pip-version-check "${PAKETE[@]}"
# Bytecode einmal erzeugen: schnellerer erster Start (die AppImage ist schreibgeschützt)
"$PY" -m compileall -q -j 0 "$APPDIR/usr/python/lib/python3.14/site-packages" >/dev/null || true

echo "== Verschlanken"
SITE="$APPDIR/usr/python/lib/python3.14/site-packages"
# vtk: nur die Bibliotheken, die OCP (direkt oder über andere) lädt – von 639 MB bleiben gut 300 MB. Die
# Python-Schnittstelle von vtk benutzt WolfCAD nicht.
NOETIG="$(mktemp)"
ldd "$SITE"/OCP/OCP*.so | grep -o "vtkmodules/[^ ]*\.so[^ ]*" | xargs -n1 basename | sort -u > "$NOETIG"
if ldd "$SITE"/OCP/OCP*.so | grep -q "not found"; then echo "OCP findet Bibliotheken nicht" >&2; exit 1; fi
( cd "$SITE/vtkmodules" && for datei in *; do grep -qxF "$datei" "$NOETIG" || rm -rf "$datei"; done )
rm -f "$SITE/vtk.py" "$NOETIG"
# Qt: Werkzeuge, Übersetzungen anderer Sprachen, QML/Quick und Module, die WolfCAD nicht lädt
rm -rf "$SITE/PySide6/Qt/qml" "$SITE/PySide6/Qt/libexec" "$SITE/PySide6/include" \
       "$SITE/PySide6/"{designer,assistant,linguist,lupdate,lrelease,uic,rcc,qmllint,qmlformat,qmlls,balsam} \
       "$SITE/PySide6/examples" "$SITE/PySide6/glue" "$SITE/PySide6/typesystems"
find "$SITE/PySide6/Qt/translations" -type f ! -name "qt*_de.qm" ! -name "qt*_en.qm" -delete 2>/dev/null || true
for modul in QtQml QtQuick QtQuickControls2 QtQuickWidgets QtDesigner QtHelp QtSql QtNetwork QtConcurrent \
             QtDBus QtXml QtPrintSupport QtUiTools QtStateMachine QtOpenGL3D; do
  rm -f "$SITE/PySide6/$modul".*.so "$SITE/PySide6/$modul".pyi
done
find "$SITE/PySide6/Qt/lib" -name "libQt6Quick*" -o -name "libQt6Qml*" -o -name "libQt6Designer*" \
     -o -name "libQt6Sql*" -o -name "libQt6Help*" | xargs -r rm -f
# Tests und Kopfdateien der Rechenpakete, Python-Teile, die niemand braucht
rm -rf "$SITE"/scipy/*/tests "$SITE"/numpy/*/tests "$SITE"/numpy/_core/include "$SITE"/gmsh-*.data/data/include
rm -rf "$APPDIR/usr/python/lib/python3.14/"{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3} \
       "$APPDIR/usr/python/lib/"{tcl*,tk*,itcl*,thread*} "$APPDIR/usr/python/share" "$APPDIR/usr/python/include"
find "$APPDIR/usr" -name "__pycache__" -path "*/tests/*" -prune -exec rm -rf {} + 2>/dev/null || true

echo "== WolfCAD"
cp -r "$PROJEKT/wolfcad" "$APPDIR/usr/app/"
# Nur in der lokalen Fassung: das Werkzeug, das die .f3d-Klassenzuordnung aus einer Fusion-Installation ausliest,
# gehört nicht in veröffentlichte Pakete (siehe wolfcad/f3d/schema.py)
rm -f "$APPDIR/usr/app/wolfcad/f3d/schema_extract.py"
find "$APPDIR/usr/app" -name "__pycache__" -prune -exec rm -rf {} +
"$PY" -m compileall -q -j 0 "$APPDIR/usr/app/wolfcad" >/dev/null
cp "$PROJEKT/README.md" "$APPDIR/usr/app/"            # das öffentliche README (Entwicklungstagebuch bleibt lokal)

echo "== Slicer (Creality Print 7.3, AGPL-3.0)"
mkdir -p "$APPDIR/usr/crealityprint"
cp -a "$CREALITY/squashfs-root/." "$APPDIR/usr/crealityprint/"
cp "$CREALITY/fonts-ohne-variable.conf" "$APPDIR/usr/crealityprint/" 2>/dev/null || true
rm -f "$APPDIR/usr/crealityprint/AppRun" "$APPDIR/usr/crealityprint/CrealityPrint.desktop"
cp "$PROJEKT/packaging/LIZENZEN.txt" "$APPDIR/usr/app/LIZENZEN.txt"
cp -r "$PROJEKT/packaging/lizenzen" "$APPDIR/usr/app/lizenzen"        # volle Lizenztexte der Fremdprogramme
cp "$PROJEKT/LICENSE" "$APPDIR/usr/app/LICENSE"                  # GPL-3.0 – auch im „Über WolfCAD“-Dialog
cp "$PROJEKT/LICENSE" "$APPDIR/LICENSE"

echo "== Start, Symbol, Desktop-Eintrag"
cp "$PROJEKT/packaging/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp "$PROJEKT/assets/wolfcad.svg" "$APPDIR/de.skye.WolfCAD.svg"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps" "$APPDIR/usr/share/applications"
cp "$PROJEKT/assets/wolfcad.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/de.skye.WolfCAD.svg"
cp "$PROJEKT/packaging/de.skye.WolfCAD.desktop" "$APPDIR/de.skye.WolfCAD.desktop"
cp "$PROJEKT/packaging/de.skye.WolfCAD.desktop" "$APPDIR/usr/share/applications/"

echo "== Größe des AppDir: $(du -sh "$APPDIR" | cut -f1)"
echo "== AppImage packen"
ARCH=x86_64 VERSION="$VERSION" "$WERKZEUGE/appimagetool-x86_64.AppImage" --appimage-extract-and-run --no-appstream \
  --comp zstd "$APPDIR" "$AUSGABE/WolfCAD-$VERSION-x86_64.AppImage"
echo "== fertig: $AUSGABE/WolfCAD-$VERSION-x86_64.AppImage ($(du -h "$AUSGABE/WolfCAD-$VERSION-x86_64.AppImage" | cut -f1))"
