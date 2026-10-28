# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Verbindung zum 3D-Drucker über Moonraker (Klipper) – so wie Creality Print einen K1/K2 im Netz anspricht.

Ein Creality K2 Plus (z. B. K2Plus-1A2B, im Netz per mDNS als _Creality-…._udp angekündigt) beantwortet die
Moonraker-Schnittstelle auf Port 7125 ohne Anmeldung (2026-10-26 lesend geprüft: /printer/info → state „ready“).
Hier steht nur, was WolfCAD braucht: Zustand und Temperaturen lesen, G-Code hochladen und auf Wunsch gleich drucken,
Druck anhalten/fortsetzen/abbrechen. Nur Standardbibliothek (urllib), damit keine weiteren Pakete nötig sind.
"""
from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 7125
TIMEOUT = 6.0                       # Sekunden für Abfragen; Hochladen bekommt mehr Zeit
UPLOAD_TIMEOUT = 300.0


class PrinterError(Exception):
    """Der Drucker ist nicht erreichbar oder hat eine Anfrage abgelehnt (Meldung für den Nutzer)."""


@dataclass
class PrinterStatus:
    """Momentaufnahme des Druckers für die Anzeige im Druck-Reiter."""
    state: str = "unbekannt"                    # standby, printing, paused, complete, error, …
    message: str = ""
    file: str = ""
    progress: float = 0.0                       # 0 … 1
    print_seconds: float = 0.0
    temperatures: dict[str, tuple[float, float]] = field(default_factory=dict)    # Name -> (ist, soll)


class Moonraker:
    """Ein Drucker unter `host` (Name oder IP-Adresse)."""

    def __init__(self, host: str, port: int = DEFAULT_PORT):
        self.host, self.port = host.strip(), port

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    # --- Grundlagen -------------------------------------------------------------------------------------------

    def _request(self, method: str, path: str, body=None, headers: dict | None = None,
                 timeout: float = TIMEOUT) -> dict:
        request = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            raise PrinterError(_explain(error.code, error.read().decode("utf-8", "replace"))) from error
        except (urllib.error.URLError, socket.timeout, OSError) as error:
            raise PrinterError(f"Drucker {self.host} ist nicht erreichbar: {error}") from error
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError as error:
            raise PrinterError("Unerwartete Antwort des Druckers") from error

    def get(self, path: str, **query) -> dict:
        if query:
            path += "?" + urllib.parse.urlencode(query, safe="&,")
        return self._request("GET", path).get("result", {})

    def post(self, path: str, **query) -> dict:
        if query:
            path += "?" + urllib.parse.urlencode(query)
        return self._request("POST", path, b"").get("result", {})

    # --- Lesen ------------------------------------------------------------------------------------------------

    def info(self) -> dict:
        """Grunddaten (Name, Zustand, Klipper-Version)."""
        return self.get("/printer/info")

    def status(self) -> PrinterStatus:
        """Zustand, Fortschritt und Temperaturen in einer Abfrage."""
        objects = "print_stats,display_status,virtual_sdcard,extruder,heater_bed,heater_generic chamber_heater"
        result = self.get("/printer/objects/query?" + "&".join(objects.split(",")).replace(" ", "%20"))
        data = result.get("status", {})
        stats = data.get("print_stats", {})
        status = PrinterStatus(state=stats.get("state", "unbekannt"), message=stats.get("message", ""),
                               file=stats.get("filename", ""), print_seconds=stats.get("print_duration", 0.0))
        status.progress = float(data.get("display_status", {}).get("progress")
                                or data.get("virtual_sdcard", {}).get("progress") or 0.0)
        for key, label in (("extruder", "Düse"), ("heater_bed", "Druckbett"),
                           ("heater_generic chamber_heater", "Kammer")):
            heater = data.get(key)
            if heater:
                status.temperatures[label] = (float(heater.get("temperature", 0.0)), float(heater.get("target", 0.0)))
        return status

    def build_volume(self) -> tuple[float, float, float] | None:
        """Bauraum aus der Klipper-Konfiguration (position_max der Achsen); None, wenn nicht lesbar."""
        try:
            config = self.get("/printer/objects/query", configfile="settings")
        except PrinterError:
            return None
        settings = config.get("status", {}).get("configfile", {}).get("settings", {})
        try:
            return tuple(float(settings[f"stepper_{axis}"]["position_max"]) for axis in "xyz")
        except (KeyError, TypeError, ValueError):
            return None

    # --- Schreiben --------------------------------------------------------------------------------------------

    def upload(self, gcode: Path, start: bool = False, name: str | None = None, progress=None) -> str:
        """Lädt eine G-Code-Datei in den Druckspeicher; mit `start` beginnt der Druck sofort. Gibt den Dateinamen
        auf dem Drucker zurück.

        `progress(gesendet, gesamt)` (Bytes) wird während des Sendens aufgerufen – aus dem sendenden Faden.

        Moonraker verweigert (403) das Überschreiben der Datei, die gerade gedruckt wird. Läuft ein Druck mit
        demselben Namen, bekommt die neue Datei deshalb einen freien Namen („Körper1 (2).gcode“). Starten lässt
        sich während eines Drucks nichts – das wird vorher klar gemeldet statt mit einer Fehlerseite."""
        gcode = Path(gcode)
        name = name or gcode.name
        running = self._running_file()
        if running is not None:
            if start:
                raise PrinterError(f"Der Drucker druckt gerade „{running}“. Erst diesen Druck beenden – oder "
                                   "„Nur hochladen“ und später starten.")
            name = _free_name(name, running)
        boundary = uuid.uuid4().hex
        parts = []
        for key, value in (("root", "gcodes"), ("print", "true" if start else "false")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                      "Content-Type: application/octet-stream\r\n\r\n").encode())
        body = b"".join(parts) + gcode.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))}
        # Als Datenstrom gesendet, damit sich der Fortschritt verfolgen lässt (http.client liest ihn blockweise)
        stream = _ProgressStream(body, progress) if progress is not None else body
        result = self._request("POST", "/server/files/upload", stream, headers, UPLOAD_TIMEOUT)
        return result.get("item", {}).get("path", name)

    def _running_file(self) -> str | None:
        """Name der Datei, die gerade gedruckt wird bzw. pausiert ist; None ohne laufenden Druck."""
        try:
            stats = self.get("/printer/objects/query", print_stats="").get("status", {}).get("print_stats", {})
        except PrinterError:
            return None             # Zustand nicht lesbar: dann entscheidet der Drucker selbst beim Hochladen
        if stats.get("state") in ("printing", "paused") and stats.get("filename"):
            return str(stats["filename"])
        return None

    def pause(self) -> None:
        self.post("/printer/print/pause")

    def resume(self) -> None:
        self.post("/printer/print/resume")

    def cancel(self) -> None:
        self.post("/printer/print/cancel")


class _ProgressStream(io.RawIOBase):
    """Lesbarer Datenstrom über den fertigen Inhalt, der nach jedem gelesenen Block den Fortschritt meldet."""

    def __init__(self, data: bytes, progress):
        super().__init__()
        self._data, self._progress, self._position = data, progress, 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        end = len(self._data) if size is None or size < 0 else min(len(self._data), self._position + size)
        block = self._data[self._position:end]
        self._position = end
        self._progress(self._position, len(self._data))
        return block

    def __len__(self) -> int:
        return len(self._data)


def _free_name(name: str, taken: str) -> str:
    """Ein Dateiname, der nicht `taken` ist: „Teil.gcode“ → „Teil (2).gcode“, „Teil (3).gcode“ …"""
    if name != taken:
        return name
    stem, dot, suffix = name.rpartition(".")
    stem, suffix = (stem, dot + suffix) if dot else (name, "")
    number = 2
    while f"{stem} ({number}){suffix}" == taken:
        number += 1
    return f"{stem} ({number}){suffix}"


def _explain(code: int, body: str) -> str:
    """Fehlerantwort des Druckers als verständlicher Satz (statt der rohen Moonraker-Antwort mit Traceback)."""
    message = ""
    try:
        message = str(json.loads(body).get("error", {}).get("message", ""))
    except (json.JSONDecodeError, AttributeError):
        message = body.strip().splitlines()[0][:200] if body.strip() else ""
    known = {403: "Der Drucker erlaubt das gerade nicht (z. B. weil er druckt oder die Datei benutzt wird)",
             404: "Der Drucker kennt diese Anfrage bzw. Datei nicht",
             401: "Der Drucker verlangt eine Anmeldung"}
    text = known.get(code, f"Der Drucker lehnt die Anfrage ab ({code})")
    return f"{text}." + (f" Meldung des Druckers: {message}" if message and message != "Forbidden" else "")


def discover(seconds: float = 6.0) -> list[tuple[str, str]]:
    """Creality-Drucker im lokalen Netz: [(Name, Adresse)]. Nutzt avahi-browse, wenn vorhanden (ohne: leere Liste –
    dann trägt der Nutzer die Adresse von Hand ein)."""
    import shutil
    import subprocess
    if not shutil.which("avahi-browse"):
        return []
    try:
        output = subprocess.run(["avahi-browse", "-aprt"], capture_output=True, text=True,
                                timeout=seconds).stdout
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or b"").decode("utf-8", "replace") if isinstance(error.stdout, bytes) \
            else (error.stdout or "")
    except (subprocess.SubprocessError, OSError):
        return []
    found = {}
    for line in output.splitlines():
        fields = line.split(";")
        # aufgelöste Einträge: =;Schnittstelle;Protokoll;Name;Typ;Domäne;Host;Adresse;Port;… – der K2 Plus meldet
        # seine IPv4-Adresse auch im IPv6-Eintrag; genommen wird jede Adresse mit Punkten
        if len(fields) > 8 and fields[0] == "=" and "." in fields[7] and "creality" in fields[4].lower():
            found[fields[3]] = fields[7]
    return sorted(found.items())
