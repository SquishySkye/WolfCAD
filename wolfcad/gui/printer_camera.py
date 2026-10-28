# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Kamerabild des Druckers im Druck-Reiter (wie die Geräteseite von Creality Print).

Der K2 Plus sendet sein Bild nur per WebRTC als H.264. Eingebettete Qt-Browser spielen kein H.264 ab, und aiortc
kam mit der WebRTC-Gegenstelle des Druckers nicht zurecht (Verbindungsprüfung klappte, Verschlüsselung nicht –
2026-10-27 untersucht). Deshalb empfängt ein kleines Hilfsprogramm mit GStreamer (printing/camera_helper.py, System-
Python) das Bild und gibt JPEG-Einzelbilder aus; hier werden sie angezeigt. Es läuft als eigener Prozess: Bricht es
ab, startet es nach kurzer Pause neu, und WolfCAD selbst bleibt unberührt.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

# Wartezeit vor einem neuen Versuch: anfangs 5 s, nach jedem Fehlschlag ohne Bild doppelt so lang, höchstens 60 s –
# sonst startet bei einem Drucker ohne erreichbare Kamera alle 5 s ein neues Hilfsprogramm (Python + GStreamer)
RETRY_FIRST, RETRY_LONGEST = 5000, 60000
HELPER = Path(__file__).resolve().parent.parent / "printing" / "camera_helper.py"
SYSTEM_PYTHON = shutil.which("python3", path="/usr/bin:/bin") or "python3"
# GStreamer-Elemente, die das Hilfsprogramm braucht, und das Arch-Paket, das sie mitbringt
NEEDED = {"webrtcbin": "gst-plugins-bad", "rtpbin": "gst-plugins-good", "rtph264depay": "gst-plugins-good",
          "avdec_h264": "gst-libav", "jpegenc": "gst-plugins-good", "nicesrc": "libnice"}


def missing_packages() -> list[str]:
    """Pakete, die für das Kamerabild noch fehlen (leer, wenn alles da ist)."""
    if not shutil.which("gst-inspect-1.0"):
        return ["gstreamer"]
    missing = set()
    for element, package in NEEDED.items():
        try:
            found = subprocess.run(["gst-inspect-1.0", "--exists", element], capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return ["gstreamer"]
        if found.returncode != 0:
            missing.add(package)
    return sorted(missing)


class PrinterCamera(QWidget):
    """Zeigt das Kamerabild; gestartet wird erst, wenn ein Drucker gesetzt ist und das Feld sichtbar ist."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.picture = QLabel("Kein Drucker gewählt.")
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setMinimumHeight(160)
        self.picture.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.picture.setStyleSheet("background: #111; color: #9aa3b5;")
        layout.addWidget(self.picture)
        self._host = ""
        self._process: QProcess | None = None
        self._buffer = bytearray()
        self._frames = 0
        self._restart = QTimer(self)
        self._restart.setSingleShot(True)
        self._restart.setInterval(RETRY_FIRST)
        self._failures = 0          # Versuche ohne ein einziges Bild in Folge (für die wachsende Wartezeit)
        self._restart.timeout.connect(self._start)
        self._missing: list[str] | None = None

    def show_printer(self, host: str) -> None:
        if host == self._host and self._process is not None:
            return
        self.stop()
        self._failures = 0
        self._host = host
        if host:
            self._start()

    def _start(self) -> None:
        if not self._host or self._process is not None:
            return
        if self._missing is None:
            self._missing = missing_packages()
        if self._missing:
            self.picture.setText("Für das Kamerabild fehlen noch Pakete:\n"
                                 f"sudo pacman -S --needed {' '.join(self._missing)}")
            return
        pin = self._pinned_fingerprint()
        if pin is None:
            self.picture.setText("Kamera: Zertifikat des Druckers nicht bestätigt.")
            return
        self.picture.setText("Verbinde mit der Kamera …")
        self._buffer.clear()
        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.finished.connect(self._ended)
        self._process.start(SYSTEM_PYTHON, [str(HELPER), self._host, "10", "https", pin])

    def _pinned_fingerprint(self) -> str | None:
        """Bestätigter Fingerabdruck des Drucker-Zertifikats (HTTPS mit Crealitys eigener Zertifizierungsstelle).
        Beim ersten Mal wird er angezeigt und muss bestätigt werden; danach gilt nur genau dieses Zertifikat."""
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QMessageBox
        settings = QSettings("WolfCAD", "WolfCAD")
        key = f"druck/kamera_zertifikat/{self._host}"
        stored = settings.value(key, "") or ""
        if stored:
            return stored
        try:
            found = subprocess.run([SYSTEM_PYTHON, str(HELPER), self._host, "fingerabdruck"], capture_output=True,
                                   text=True, timeout=20).stdout.strip().splitlines()[-1]
        except (OSError, subprocess.SubprocessError, IndexError):
            return None
        pretty = ":".join(found[i:i + 2].upper() for i in range(0, len(found), 2))
        answer = QMessageBox.question(self, "Kamera – Zertifikat",
                                      f"Der Drucker {self._host} nutzt ein eigenes Zertifikat von Creality.\n\n"
                                      f"SHA-256: {pretty}\n\nDiesem Zertifikat für die Kamera vertrauen? "
                                      "Ändert es sich später, wird erneut gefragt.")
        if answer != QMessageBox.StandardButton.Yes:
            return None
        settings.setValue(key, found)
        return found

    def _read(self) -> None:
        self._buffer += bytes(self._process.readAllStandardOutput())
        newest = None
        while len(self._buffer) >= 4:
            (length,) = struct.unpack(">I", self._buffer[:4])
            if len(self._buffer) < 4 + length:
                break
            newest = bytes(self._buffer[4:4 + length])
            del self._buffer[:4 + length]
        if newest is not None:
            image = QImage.fromData(newest, "JPEG")
            if not image.isNull():
                self._frames += 1
                self._failures = 0                      # Bild kam: beim nächsten Abbruch wieder rasch versuchen
                self.picture.setPixmap(QPixmap.fromImage(image).scaled(
                    self.picture.size(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))

    def _ended(self, *_args) -> None:
        error = bytes(self._process.readAllStandardError()).decode("utf-8", "replace") if self._process else ""
        self._process = None
        lines = [line for line in error.splitlines() if line.startswith("Fehler")]
        if self._host:
            self._failures += 1
            wait = min(RETRY_LONGEST, RETRY_FIRST * 2 ** (self._failures - 1))
            self._restart.setInterval(wait)
            self.picture.setText((lines[-1] if lines else "Kamera getrennt")
                                 + f" – neuer Versuch in {wait // 1000} s …")
            self._restart.start()

    @property
    def frames(self) -> int:
        return self._frames

    def stop(self) -> None:
        self._restart.stop()
        self._host = ""
        if self._process is not None:
            process, self._process = self._process, None
            process.finished.disconnect()
            process.kill()
            process.waitForFinished(2000)
