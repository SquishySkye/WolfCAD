# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
#
# WolfCAD für Windows (x64) zusammenstellen – Gegenstück zu packaging/build_appimage.sh.
# Ergebnis: ein fertiger Programmordner (python\, app\, crealityprint\, WolfCAD.pyw), aus dem wolfcad.iss den
# Installer baut. Läuft auf GitHub Actions (windows-latest), geht aber auch auf jedem Windows-Rechner mit PowerShell 5+.
#
# Aufruf:  powershell -File packaging\windows\build_windows.ps1 [-Ausgabe build\windows]
# Alle Downloads werden über ihre SHA-256-Prüfsumme geprüft; stimmt eine nicht, bricht der Bau ab.

param(
    [string]$Ausgabe = "build\windows"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"          # Fortschrittsbalken bremsen Invoke-WebRequest stark

$Projekt = (Resolve-Path "$PSScriptRoot\..\..").Path
$Ziel = Join-Path $Ausgabe "WolfCAD"
$Laden = Join-Path $Ausgabe "downloads"

# Eigene Python-Laufzeit (dieselbe Fassung wie im AppImage)
$PythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/" +
             "cpython-3.14.10%2B20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
$PythonSha = "c1876a9795d3a94ee49a1499a69cd91e26ca3e2829593f93fa6e4db90d05402b"
# Creality Print 7.3.0 für Windows als Velopack-/Squirrel-Paket (ein Zip; einfacher zu entpacken als der Installer)
$CrealityUrl = "https://github.com/CrealityOfficial/CrealityPrint/releases/download/v7.3.0/" +
               "crealityprint-7.3.0-release-full.nupkg"
$CrealitySha = "c34a37fc38145b83cd1ca8443bb0e0be53f3163a20eb2f08473f5ef3856e0f77"

function Hole-Datei([string]$Url, [string]$Sha, [string]$Datei) {
    # Lädt eine Datei (falls noch nicht da) und prüft ihre Prüfsumme
    if (-not (Test-Path $Datei)) {
        Write-Host "   laden: $Url"
        Invoke-WebRequest -Uri $Url -OutFile $Datei
    }
    $Ist = (Get-FileHash -Algorithm SHA256 $Datei).Hash.ToLower()
    if ($Ist -ne $Sha) { throw "Prüfsumme falsch für $Datei (ist $Ist, soll $Sha)" }
}

Write-Host "== Programmordner $Ziel"
if (Test-Path $Ziel) { Remove-Item -Recurse -Force $Ziel }
New-Item -ItemType Directory -Force -Path $Ziel, $Laden, "$Ziel\app" | Out-Null

Write-Host "== Python-Laufzeit"
$PythonArchiv = Join-Path $Laden "python.tar.gz"
Hole-Datei $PythonUrl $PythonSha $PythonArchiv
tar -xzf $PythonArchiv -C $Ziel                    # ergibt $Ziel\python
if ($LASTEXITCODE -ne 0) { throw "Entpacken der Python-Laufzeit fehlgeschlagen" }
$Py = Join-Path $Ziel "python\python.exe"

Write-Host "== Pakete (feste Fassungen aus requirements.txt, ohne Testpakete)"
$Pakete = Get-Content "$Projekt\requirements.txt" | Where-Object { $_ -match "==" }
& $Py -m pip install --quiet --no-deps --no-compile --disable-pip-version-check @Pakete
if ($LASTEXITCODE -ne 0) { throw "pip install fehlgeschlagen" }

Write-Host "== Verschlanken"
$Site = Join-Path $Ziel "python\Lib\site-packages"
Get-ChildItem -Path "$Site\scipy", "$Site\numpy" -Directory -Recurse -Filter "tests" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
foreach ($Weg in "Lib\test", "Lib\idlelib", "Lib\tkinter", "Lib\turtledemo", "tcl", "include") {
    $Pfad = Join-Path $Ziel "python\$Weg"
    if (Test-Path $Pfad) { Remove-Item -Recurse -Force $Pfad }
}

Write-Host "== WolfCAD"
Copy-Item -Recurse "$Projekt\wolfcad" "$Ziel\app\wolfcad"
Get-ChildItem -Path "$Ziel\app" -Directory -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force
# Nur in der lokalen Fassung: Auslesewerkzeug für die .f3d-Klassenzuordnung (siehe wolfcad\f3d\schema.py)
Remove-Item -Force -ErrorAction SilentlyContinue "$Ziel\app\wolfcad\f3d\schema_extract.py"
Copy-Item "$Projekt\README.md", "$Projekt\LICENSE", "$Projekt\packaging\LIZENZEN.txt" "$Ziel\app\"
Copy-Item "$Projekt\LICENSE" "$Ziel\LICENSE.txt"
Copy-Item -Recurse "$Projekt\packaging\lizenzen" "$Ziel\app\lizenzen"     # volle Lizenztexte der Fremdprogramme
Copy-Item "$Projekt\packaging\windows\WolfCAD.pyw", "$Projekt\packaging\windows\wolfcad.ico" "$Ziel\"
# Bytecode einmal erzeugen: schnellerer erster Start
& $Py -m compileall -q -j 0 "$Ziel\app\wolfcad" "$Site" | Out-Null

Write-Host "== Slicer (Creality Print 7.3.0, AGPL-3.0, unverändert)"
$CrealityPaket = Join-Path $Laden "crealityprint.nupkg"
Hole-Datei $CrealityUrl $CrealitySha $CrealityPaket
$Entpackt = Join-Path $Laden "crealityprint"
if (Test-Path $Entpackt) { Remove-Item -Recurse -Force $Entpackt }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($CrealityPaket, $Entpackt)
Move-Item "$Entpackt\lib\net45" "$Ziel\crealityprint"
# Nicht gebraucht: Selbst-Aktualisierung (würde die mitgelieferte Fassung austauschen), Startstummel von Squirrel,
# VC-Laufzeit-Installer (die Laufzeit-DLLs liegen schon daneben) und die Explorer-Erweiterung für Vorschaubilder
foreach ($Muster in "updater*.exe", "*_ExecutionStub.exe", "vcredist_x64.exe", "STLThumbWinShellExtension.dll") {
    Get-ChildItem -Path "$Ziel\crealityprint" -Filter $Muster | Remove-Item -Force
}
if (-not (Test-Path "$Ziel\crealityprint\CrealityPrint.exe")) { throw "CrealityPrint.exe fehlt im Paket" }

$Groesse = [math]::Round(((Get-ChildItem -Recurse -File $Ziel | Measure-Object Length -Sum).Sum / 1MB))
Write-Host "== fertig: $Ziel ($Groesse MB)"
