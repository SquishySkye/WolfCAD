# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Eigene Werkzeugsymbole (SVG) und der Zugriff darauf.

Die Symbole sind mit heller Linienfarbe für dunkle Oberflächen gezeichnet. In einem hellen Design werden
diese Linien beim Zeichnen in der Textfarbe des Designs ausgegeben; die farbigen Akzente bleiben.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_FOLDER = Path(__file__).parent
_LINE_COLOURS = ("#d6dbe4", "#c8ccd4")     # helle Linienfarben, in denen die Symbole gezeichnet sind


class _ThemedEngine(QIconEngine):
    """Zeichnet ein SVG-Symbol in den Farben des gerade geltenden Designs."""

    def __init__(self, source: str):
        super().__init__()
        self._source = source
        self._renderers: dict[str | None, QSvgRenderer] = {}

    def _renderer(self) -> QSvgRenderer:
        from .. import theme        # hier eingebunden: das Design hängt nicht von den Symbolen ab

        colour = theme.current().extra.get("icon")
        if colour not in self._renderers:
            text = self._source
            for light in _LINE_COLOURS if colour is not None else ():
                text = text.replace(light, colour)
            self._renderers[colour] = QSvgRenderer(QByteArray(text.encode("utf-8")))
        return self._renderers[colour]

    def paint(self, painter: QPainter, rect, mode, state) -> None:
        if mode == QIcon.Mode.Disabled:
            painter.setOpacity(0.35)        # ausgegraute Werkzeuge
        self._renderer().render(painter, QRectF(rect))

    def pixmap(self, size: QSize, mode, state) -> QPixmap:
        image = QPixmap(size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        self.paint(painter, image.rect(), mode, state)
        painter.end()
        return image

    def clone(self) -> QIconEngine:
        return _ThemedEngine(self._source)


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    """Lädt das Symbol `name` (Dateiname ohne Endung); fehlt es, gibt es ein leeres Symbol."""
    path = _FOLDER / f"{name}.svg"
    return QIcon(_ThemedEngine(path.read_text(encoding="utf-8"))) if path.exists() else QIcon()


def logo_pixmap(height: int, strong: str, soft: str) -> QPixmap:
    """Das Wolf-Logo des Nutzers in Blautönen und der gewünschten Höhe (für die Ecke oben links).

    Aus dem farbigen Bild wird eine Zeichnung in Abstufungen einer Farbe: die dunklen Stellen des Originals
    (Umrisse, dunkles Fell) bekommen den kräftigen Ton `strong`, die mittleren den zarten Ton `soft`; je
    heller eine Stelle, desto durchsichtiger – der weiße Rand verschwindet.
    """
    import numpy as np
    from PySide6.QtGui import QColor, QImage

    path = _FOLDER / "logo.png"
    if not path.exists():
        return QPixmap()
    source = QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(source.constBits(), dtype=np.uint8).reshape(source.height(), source.bytesPerLine())
    pixels = pixels[:, :source.width() * 4].reshape(source.height(), source.width(), 4).astype(np.float32)
    brightness = (0.299 * pixels[..., 0] + 0.587 * pixels[..., 1] + 0.114 * pixels[..., 2]) / 255.0
    # Deckkraft aus der Dunkelheit, etwas angehoben, damit auch die mittleren Töne sichtbar bleiben
    strength = np.clip((1.0 - brightness) * 1.35, 0.0, 1.0) * (pixels[..., 3] / 255.0)
    dark, light = QColor(strong), QColor(soft)
    # Farbton je Bildpunkt: von kräftig (dunkle Stelle) nach zart (helle Stelle)
    share = np.clip(brightness * 1.25, 0.0, 1.0)
    result = np.zeros((source.height(), source.width(), 4), dtype=np.uint8)
    for channel, (low, high) in enumerate(((dark.red(), light.red()), (dark.green(), light.green()),
                                           (dark.blue(), light.blue()))):
        result[..., channel] = (low + (high - low) * share).astype(np.uint8)
    result[..., 3] = (strength * 255).astype(np.uint8)
    image = QImage(result.tobytes(), source.width(), source.height(), source.width() * 4,
                   QImage.Format.Format_RGBA8888).copy()        # copy: die Bilddaten gehören danach dem Bild selbst
    return QPixmap.fromImage(image.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation))
