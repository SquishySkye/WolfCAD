# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Design „System“ folgt dem Betriebssystem auch im laufenden Programm (hell/dunkel, Akzentfarbe, Systemereignis).

Der Wechsel des Systems wird über QStyleHints.setColorScheme nachgestellt (so meldet Qt auch einen echten Wechsel).
Die Noctalia-Farbdatei wird abgeschaltet, damit der Test auf jedem Rechner die reinen Systemfarben prüft. Läuft
ohne Fenster auf dem Bildschirm, unter Linux wie unter Windows (GitHub Actions).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from wolfcad.gui import theme  # noqa: E402


@pytest.fixture
def plain_system(monkeypatch):
    """Ohne Noctalia (wie unter Windows oder auf einem anderen Linux-Desktop); danach Ausgangszustand wieder her."""
    monkeypatch.setattr(theme, "_NOCTALIA_FILES", ())
    before = app.styleHints().colorScheme()
    yield
    app.styleHints().setColorScheme(before)
    app.styleHints().unsetColorScheme()
    theme.apply(app, theme.SYSTEM, remember=False)


def _set_scheme(scheme) -> None:
    """Stellt einen Wechsel hell/dunkel des Systems nach; kann die Qt-Plattform das nicht (z. B. „offscreen“ unter
    Windows), wird der Test übersprungen – in GitHub Actions läuft er unter Windows deshalb mit der echten Plattform."""
    app.styleHints().setColorScheme(scheme)
    app.processEvents()
    if app.styleHints().colorScheme() != scheme:
        pytest.skip("diese Qt-Plattform kann einen Wechsel hell/dunkel nicht nachstellen")


def _window_lightness() -> int:
    return QColor(theme.current().extra["colours"]["window"]).lightness()


def test_system_design_follows_light_and_dark(plain_system):
    _set_scheme(Qt.ColorScheme.Dark)
    theme.apply(app, theme.SYSTEM, remember=False)
    assert _window_lightness() < 128
    dark_view = theme.current().background
    _set_scheme(Qt.ColorScheme.Light)
    theme.apply(app, theme.SYSTEM, remember=False)
    assert _window_lightness() > 128
    # auch die Anwendung selbst und die 3D-Ansicht sind hell geworden
    assert app.palette().color(QPalette.ColorRole.Window).lightness() > 128
    assert sum(theme.current().background) > sum(dark_view)
    # helle Oberfläche: Symbole werden in der Textfarbe gezeichnet
    assert theme.current().extra["icon"] is not None


def test_palette_is_read_fresh_each_time(plain_system):
    # vorher galt immer die Palette vom Programmstart – jetzt die gerade aktuelle des Systems
    _set_scheme(Qt.ColorScheme.Light)
    theme.apply(app, theme.SYSTEM, remember=False)
    first = theme.current().extra["colours"]["window"]
    _set_scheme(Qt.ColorScheme.Dark)
    theme.apply(app, theme.SYSTEM, remember=False)
    assert theme.current().extra["colours"]["window"] != first


def test_watcher_reports_scheme_change_and_theme_event(plain_system):
    watcher = theme.SystemWatcher(app)
    seen = []
    watcher.changed.connect(lambda: seen.append(1))
    QCoreApplication.sendEvent(app, QEvent(QEvent.Type.ThemeChange))     # neue Systemfarben (z. B. Akzent)
    QTest.qWait(500)
    assert len(seen) == 1
    # das eigene Umfärben löst keine Meldung aus (sonst bauten sich Design und Meldung endlos gegenseitig neu)
    theme.apply(app, theme.SYSTEM, remember=False)
    QTest.qWait(500)
    assert len(seen) == 1
    # Wechsel hell/dunkel: gebündelt eine Meldung
    _set_scheme(Qt.ColorScheme.Light if app.styleHints().colorScheme() != Qt.ColorScheme.Light
                else Qt.ColorScheme.Dark)
    QTest.qWait(500)
    watcher.deleteLater()
    assert len(seen) == 2


def test_accent_comes_from_system(plain_system):
    accent = theme._desktop_colours(app.palette())["accent"]
    assert QColor(accent).isValid()
    if os.name == "nt":
        found = theme._windows_accent()
        assert found is None or (found.startswith("#") and len(found) == 7)
        if found:
            assert accent == found
    else:
        assert theme._windows_accent() is None and theme._windows_signature() is None
