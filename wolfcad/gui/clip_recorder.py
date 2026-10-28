# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Werkzeug-Clips aufnehmen (Runde 80): kurze Endlos-Animationen für die Erklärkarten (tool_hints.py).

Jede Szene führt ein Werkzeug in einem echten WolfCAD-Fenster vor – Beispielteil anlegen, Werkzeug starten, Wert
über mehrere Bilder ändern, bestätigen. Aufgenommen wird nur die 3D-Ansicht (ohne Ansichtswürfel); dazu kommen ein
gezeichneter Mauszeiger (mit Klick-Welle) und unten eine Zeile mit dem aktuellen Schritt. Gespeichert wird als
animiertes WebP unter wolfcad/gui/clips/<art>_<name>.webp.

Eigene Aufnahmen aus WolfCAD – keine Grafik oder Videos von Autodesk.

Aufruf (unsichtbar, z. B. unter Xvfb): python clip_recorder.py <ausgabeordner> [Szene …]
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF, QVector3D

CLIP_SIZE = (612, 360)          # Pixel der Aufnahme (doppelt so groß wie in der Karte angezeigt: scharf auf HiDPI)
FPS = 14
# Aufgenommen wird mit doppelter Pixeldichte (QT_SCALE_FACTOR=2) ein fester Ausschnitt aus der Bildmitte: das
# Fenster lässt sich nicht beliebig verkleinern, und so bleiben Linien und Schrift scharf. Der kleinere Ausschnitt
# vergrößert; die Kamera rückt dafür weiter weg (CAMERA_FACTOR auf die in den Szenen angegebenen Abstände).
CAMERA_FACTOR = 1.6
CLIP_DIR = Path(__file__).resolve().parent / "clips"


def clip_name(kind: str, tool: str) -> str:
    """Dateiname eines Clips: Art (modell/skizze) und Werkzeugname in einfachen Zeichen."""
    plain = tool.lower()
    for umlaut, ascii_ in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        plain = plain.replace(umlaut, ascii_)
    plain = "".join(ch if ch.isalnum() else "_" for ch in plain).strip("_")
    return f"{kind}_{plain}.webp"


def clip_path(kind: str, tool: str) -> Path | None:
    """Pfad des Clips, wenn es einen gibt."""
    path = CLIP_DIR / clip_name(kind, tool)
    return path if path.exists() else None


class Recorder:
    """Sammelt Bilder der 3D-Ansicht eines Fensters und schreibt sie als animiertes WebP."""

    def __init__(self, window):
        self.window, self.view = window, window.viewport
        self.frames: list[QImage] = []
        self.caption = ""
        self.cursor: tuple | None = None        # Modellpunkt unter dem gezeichneten Mauszeiger
        self.view.cube_visible = False
        self.view.hud_scale = 1.6              # Maßfelder größer: der Clip wird in der Karte verkleinert gezeigt

    # --- Kamera und Hilfen ----------------------------------------------------------------------------------
    def look(self, target: tuple, distance: float, yaw: float = -35.0, pitch: float = 28.0) -> None:
        self.camera = (target, distance * CAMERA_FACTOR, yaw, pitch)
        self._apply_camera()

    def _apply_camera(self) -> None:
        """Kamera der Szene setzen – vor jedem Bild, denn WolfCAD passt die Ansicht z. B. beim ersten Körper eines
        Entwurfs von sich aus an."""
        if getattr(self, "camera", None) is None:
            return
        target, distance, yaw, pitch = self.camera
        self.view.set_view(yaw, pitch)
        self.view._target = QVector3D(*target)
        self.view._distance = distance
        self.view.update()

    def _crop(self) -> tuple[QImage, QRectF]:
        """Mittiger Ausschnitt der Ansicht im Seitenverhältnis des Clips."""
        image = self.view.grabFramebuffer()
        width, height = image.width(), image.height()
        # Ausschnitt: so groß wie der Clip in logischen Punkten (bei Pixeldichte 2 also doppelt so viele Pixel)
        ratio = self.view.devicePixelRatioF() or 1.0
        crop_w, crop_h = min(width, CLIP_SIZE[0] * ratio), min(height, CLIP_SIZE[1] * ratio)
        rect = QRectF((width - crop_w) / 2, (height - crop_h) / 2, crop_w, crop_h)
        return image, rect

    def shot(self, hold: int = 1, click: float = 0.0) -> None:
        """Ein Bild aufnehmen (`hold` = wie viele Bilder lang es steht); `click` > 0 zeichnet die Klick-Welle."""
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        self._apply_camera()
        image, rect = self._crop()
        frame = QImage(CLIP_SIZE[0], CLIP_SIZE[1], QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(frame)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(QRectF(0, 0, *CLIP_SIZE), image, rect)
        scale = CLIP_SIZE[0] / rect.width()
        if self.cursor is not None:
            at = self.view.project(self.cursor)         # logische Punkte → Pixel des Bildes → Clip
            ratio = self.view.devicePixelRatioF() or 1.0
            point = QPointF((at.x() * ratio - rect.x()) * scale, (at.y() * ratio - rect.y()) * scale)
            if click > 0:
                self._ripple(painter, point, click)
            self._pointer(painter, point)
        if self.caption:
            self._caption(painter, self.caption)
        painter.end()
        self.frames.extend([frame] * hold)

    @staticmethod
    def _pointer(painter: QPainter, tip: QPointF) -> None:
        """Mauszeiger (Pfeil) mit der Spitze auf `tip`."""
        shape = [(0, 0), (0, 25), (6.5, 19), (11, 29), (15, 27.5), (10.5, 18), (19, 18)]
        polygon = QPolygonF([QPointF(tip.x() + x, tip.y() + y) for x, y in shape])
        painter.setPen(QPen(QColor(15, 18, 24), 2.0))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawPolygon(polygon)

    @staticmethod
    def _ripple(painter: QPainter, centre: QPointF, phase: float) -> None:
        """Klick-Welle: ein Ring, der wächst und verblasst (phase 0 … 1)."""
        radius = 8 + 26 * phase
        painter.setPen(QPen(QColor(255, 210, 74, int(230 * (1 - phase))), 3.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, radius, radius)

    @staticmethod
    def _caption(painter: QPainter, text: str) -> None:
        """Schrittzeile unten: dunkles, abgerundetes Feld mit weißem Text."""
        font = QFont()
        font.setPixelSize(22)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = min(CLIP_SIZE[0] - 24, metrics.horizontalAdvance(text) + 32)
        box = QRectF((CLIP_SIZE[0] - width) / 2, CLIP_SIZE[1] - 52, width, 38)
        path = QPainterPath()
        path.addRoundedRect(box, 10, 10)
        painter.fillPath(path, QColor(18, 21, 28, 215))
        painter.setPen(QColor(240, 243, 248))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                                                                               int(width - 20)))

    # --- Abläufe --------------------------------------------------------------------------------------------
    def move_to(self, point: tuple, steps: int = 8) -> None:
        """Mauszeiger gleitet zu einem Modellpunkt."""
        start = self.cursor or (point[0] - 30.0, point[1] - 30.0, point[2])
        for number in range(1, steps + 1):
            t = _ease(number / steps)
            self.cursor = tuple(a + (b - a) * t for a, b in zip(start, point))
            self.shot()

    def click(self) -> None:
        for phase in (0.15, 0.4, 0.65, 0.9):
            self.shot(click=phase)

    def animate(self, steps: int, apply, cursor_of=None) -> None:
        """`steps` Bilder: `apply(t)` stellt den Zustand für t = 0 … 1 her, `cursor_of(t)` setzt den Zeiger."""
        for number in range(steps + 1):
            t = _ease(number / steps)
            apply(t)
            if cursor_of is not None:
                self.cursor = cursor_of(t)
            self.shot()

    def save(self, path: Path) -> None:
        from PIL import Image
        images = []
        for frame in self.frames:
            rgba = frame.convertToFormat(QImage.Format.Format_RGBA8888)
            images.append(Image.frombuffer("RGBA", (rgba.width(), rgba.height()), bytes(rgba.constBits()),
                                           "raw", "RGBA", rgba.bytesPerLine(), 1).convert("RGB"))
        path.parent.mkdir(parents=True, exist_ok=True)
        images[0].save(path, save_all=True, append_images=images[1:], duration=int(1000 / FPS), loop=0,
                       quality=72, method=4)


def _ease(t: float) -> float:
    """Weicher Verlauf (langsam an, langsam aus)."""
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))
