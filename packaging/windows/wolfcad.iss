; SPDX-License-Identifier: GPL-3.0-or-later
; Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
;
; Inno-Setup-Skript für den Windows-Installer von WolfCAD.
; Erwartet den von build_windows.ps1 erzeugten Programmordner und die Fassung:
;   iscc /DVersion=2026.10.28 /DQuelle=build\windows\WolfCAD /DAusgabe=build\windows wolfcad.iss

#ifndef Version
  #define Version "0.0.0"
#endif
#ifndef Quelle
  #define Quelle "..\..\build\windows\WolfCAD"
#endif
#ifndef Ausgabe
  #define Ausgabe "..\..\build\windows"
#endif

[Setup]
; Feste Kennung: damit erkennt Windows spätere Fassungen als Aktualisierung derselben Anwendung
AppId={{6F0C2B5E-8A71-4D3C-9E2A-57B1D0A4C9F3}
AppName=WolfCAD
AppVersion={#Version}
AppPublisher=Skye und die WolfCAD-Mitwirkenden
AppPublisherURL=https://github.com/SquishySkye/WolfCAD
AppSupportURL=https://github.com/SquishySkye/WolfCAD/issues
DefaultDirName={autopf}\WolfCAD
DefaultGroupName=WolfCAD
DisableProgramGroupPage=yes
LicenseFile={#Quelle}\LICENSE.txt
OutputDir={#Ausgabe}
OutputBaseFilename=WolfCAD-{#Version}-Windows-Setup
SetupIconFile={#Quelle}\wolfcad.ico
UninstallDisplayIcon={app}\wolfcad.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Installation auch ohne Administratorrechte (dann im Benutzerordner)
PrivilegesRequiredOverridesAllowed=dialog
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#Quelle}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start ohne Konsolenfenster: pythonw.exe mit dem Startskript (setzt Suchpfad und Slicer-Ort)
Name: "{autoprograms}\WolfCAD"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\WolfCAD.pyw"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\wolfcad.ico"
Name: "{autodesktop}\WolfCAD"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\WolfCAD.pyw"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\wolfcad.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\WolfCAD.pyw"""; Description: "{cm:LaunchProgram,WolfCAD}"; \
  Flags: nowait postinstall skipifsilent
