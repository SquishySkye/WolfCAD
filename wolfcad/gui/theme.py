# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Designs (Themes) der Oberfläche.

„System“ übernimmt Farben und Schrift des Betriebssystems: zuerst die Farben der Noctalia-Shell
(`~/.config/noctalia/colors.json`), sonst die Farben, die Qt vom Desktop bekommt. „Klassisch“ ist das
ursprüngliche WolfCAD-Aussehen. Die Wahl wird gemerkt und lässt sich im laufenden Programm wechseln.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette

SYSTEM, CLASSIC = "System", "Klassisch"
NAMES = (SYSTEM, CLASSIC)
# Farbschema der Noctalia-Shell: neuere Fassungen schreiben es nach ~/.local/state/noctalia/shared-colors.json
# (Farben als #AARRGGBB), ältere nach ~/.config/noctalia/colors.json. Die zuletzt geänderte vorhandene Datei gilt –
# bis Runde 82 las WolfCAD nur die alte Datei, die seit dem Wechsel nicht mehr geändert wird (Nutzermeldung).
_NOCTALIA_FILES = (Path.home() / ".local" / "state" / "noctalia" / "shared-colors.json",
                   Path.home() / ".config" / "noctalia" / "colors.json")


def _noctalia_file() -> Path | None:
    """Die aktuelle Farbdatei der Shell (die zuletzt geänderte), None ohne Noctalia."""
    found = [path for path in _NOCTALIA_FILES if path.exists()]
    return max(found, key=lambda path: path.stat().st_mtime) if found else None


def _plain_colour(value: str) -> str:
    """#AARRGGBB (Noctalia) → #RRGGBB; #RRGGBB bleibt."""
    value = value.strip()
    return "#" + value[-6:] if value.startswith("#") and len(value) == 9 else value
# Blau der Ziehpfeile, angelehnt an die Griffe in Fusion (selbst gewählt, keine übernommene Grafik)
HANDLE_BLUE = (0.05, 0.58, 0.92)

Colour = tuple[float, float, float]


@dataclass(frozen=True)
class Theme:
    """Alles, was sich zwischen den Designs unterscheidet."""

    name: str
    stylesheet: str                     # Aussehen der Bedienelemente (für die ganze Anwendung)
    palette: dict[str, str] | None      # Grundfarben für Qt (None = Farben vom Programmstart)
    chips: dict[str, str]               # Einträge der Timeline je Zustand
    marker: str                         # Farbe der Rücksetzmarke
    background: Colour                  # Hintergrund der 3D-Ansicht
    overlay: str                        # Hinweisfelder in der Ansicht (ohne Rahmenfarbe)
    value_box: str                      # Wertfeld am Ziehpfeil
    preview: str                        # Hintergrund hinter dem Zeichnungsblatt
    handle: Colour = HANDLE_BLUE        # Farbe der Ziehpfeile
    extra: dict = field(default_factory=dict)


def _rgb(colour: str) -> Colour:
    c = QColor(colour)
    return c.redF(), c.greenF(), c.blueF()


def _mix(first: str, second: str, share: float) -> str:
    """Mischfarbe: `share` (0 bis 1) Anteile der zweiten Farbe in der ersten."""
    a, b = QColor(first), QColor(second)
    return QColor(round(a.red() + (b.red() - a.red()) * share), round(a.green() + (b.green() - a.green()) * share),
                  round(a.blue() + (b.blue() - a.blue()) * share)).name()


# Nur die Farben der Beschriftungen – alles Übrige bleibt, wie Qt es von sich aus zeichnet
_CLASSIC_STYLESHEET = """
QLabel#hint { color: #8a93a5; }
QLabel#toolGroup { color: #7b8496; font-size: 10px; padding: 0 6px; }
QToolButton#ribbonTitle { color: #9aa3b5; font-size: 10px; border: none; padding: 0 4px; }
QToolButton#ribbonWorkspace { font-weight: bold; padding: 2px 8px; }
QTabBar#ribbonTabs::tab { padding: 3px 10px; font-size: 11px; border: none; }
QTabBar#ribbonTabs::tab:selected { color: #2d9bf0; border-bottom: 2px solid #2d9bf0; }
QFrame#browserOverlay { background: #20242c; border: 1px solid #3a4150; border-radius: 8px; }
QTreeWidget#browserTree { background: transparent; }
QLabel#browserTitle { font-size: 10px; font-weight: bold; color: #9aa3b5; }
QWidget#operationBar { background: #1b1e24; border: 1px solid #2d9bf0; border-radius: 6px; }
"""

_CLASSIC = Theme(
    name=CLASSIC,
    stylesheet=_CLASSIC_STYLESHEET,
    palette=None,
    chips={"active": "background:#3d6fb5; color:white;",
           "rolled_back": "background:#3a3d44; color:#8a8f99;",
           "suppressed": "background:#55585f; color:#b0b4bb; text-decoration:line-through;",
           "error": "background:#b5483d; color:white;"},
    marker="#f0c040",
    background=(0.16, 0.17, 0.20),
    overlay="background:#1b1e24; color:#e6e9ef; padding:6px 9px;",
    value_box="background:#1b1e24; color:white; border:2px solid #2d9bf0; padding:4px; font-weight:bold;",
    preview="#3a3d44",
)


def _desktop_colours(start: QPalette) -> dict[str, str]:
    """Die Farben des Desktops: Noctalia, falls vorhanden, sonst aus den Farben, die Qt beim Start bekam."""
    try:
        source = _noctalia_file()
        if source is None:
            raise OSError("keine Noctalia-Farben")
        noctalia = {key: _plain_colour(value) for key, value in
                    json.loads(source.read_text(encoding="utf-8")).items() if isinstance(value, str)}
        return {"window": noctalia["mSurface"], "surface": noctalia["mSurfaceVariant"],
                "text": noctalia["mOnSurface"], "muted": noctalia["mOnSurfaceVariant"],
                "accent": noctalia["mPrimary"], "on_accent": noctalia["mOnPrimary"],
                "second": noctalia["mSecondary"], "on_second": noctalia["mOnSecondary"],
                "outline": noctalia["mOutline"], "error": noctalia["mError"], "on_error": noctalia["mOnError"]}
    except (OSError, ValueError, KeyError):
        pass
    role = QPalette.ColorRole
    window, text = start.color(role.Window).name(), start.color(role.WindowText).name()
    accent = start.color(role.Highlight).name()
    return {"window": window, "surface": start.color(role.Base).name(), "text": text,
            "muted": _mix(text, window, 0.45), "accent": accent, "on_accent": start.color(role.HighlightedText).name(),
            "second": accent, "on_second": start.color(role.HighlightedText).name(),
            "outline": _mix(window, text, 0.22), "error": "#e05555", "on_error": "#ffffff"}


def _system(start: QPalette) -> Theme:
    c = _desktop_colours(start)
    hover = _mix(c["surface"], c["accent"], 0.22)
    raised = _mix(c["surface"], c["text"], 0.07)
    # eingeschaltete Knöpfe: getönt mit Rahmen in der Akzentfarbe – die hellen Symbole bleiben darauf lesbar
    checked = _mix(c["surface"], c["accent"], 0.30)
    light = QColor(c["window"]).lightness() > 140
    stylesheet = f"""
QMainWindow, QDialog, QDockWidget, QStatusBar {{ background: {c['window']}; color: {c['text']}; }}
QToolBar {{ background: {c['window']}; border: none; border-bottom: 1px solid {c['outline']}; padding: 4px; spacing: 2px; }}
QToolBar QToolButton {{ border: none; border-radius: 10px; padding: 4px 3px; color: {c['text']}; }}
QToolBar QToolButton:hover {{ background: {hover}; }}
QToolBar QToolButton:pressed {{ background: {c['accent']}; color: {c['on_accent']}; }}
QToolBar QToolButton:checked {{ background: {checked}; border: 1px solid {c['accent']}; }}
QToolBar::separator {{ background: {c['outline']}; width: 1px; margin: 8px 3px; }}
QDockWidget::title {{ background: {c['window']}; padding: 7px 8px; }}
QTreeView, QListView, QTableView, QTextEdit, QPlainTextEdit {{ background: {c['surface']}; color: {c['text']};
    border: 1px solid {c['outline']}; border-radius: 10px; padding: 3px; }}
QTreeView::item, QListView::item {{ padding: 3px 2px; border-radius: 6px; }}
QTreeView::item:hover, QListView::item:hover {{ background: {hover}; }}
QTreeView::item:selected, QListView::item:selected {{ background: {c['second']}; color: {c['on_second']}; }}
QHeaderView::section {{ background: {raised}; color: {c['text']}; border: none; padding: 4px 6px; }}
QLineEdit, QAbstractSpinBox, QComboBox {{ background: {c['surface']}; color: {c['text']};
    border: 1px solid {c['outline']}; border-radius: 8px; padding: 3px 7px; }}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['outline']};
    selection-background-color: {c['second']}; selection-color: {c['on_second']}; }}
QPushButton {{ background: {raised}; color: {c['text']}; border: 1px solid {c['outline']}; border-radius: 10px;
    padding: 5px 14px; }}
QPushButton:hover {{ border: 1px solid {c['accent']}; }}
QPushButton:pressed, QPushButton:checked, QPushButton:default {{ background: {c['accent']}; color: {c['on_accent']};
    border: 1px solid {c['accent']}; }}
QPushButton:disabled {{ color: {c['muted']}; }}
QDockWidget QToolButton, QDialog QToolButton {{ border-radius: 8px; padding: 4px 6px; color: {c['text']}; }}
QDockWidget QToolButton:hover, QDialog QToolButton:hover {{ background: {hover}; }}
QDockWidget QToolButton:checked, QDialog QToolButton:checked {{ background: {checked}; border: 1px solid {c['accent']}; }}
QMenu {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['outline']}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 5px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['second']}; color: {c['on_second']}; }}
QMenu::separator {{ height: 1px; background: {c['outline']}; margin: 4px 8px; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['outline']}; border-radius: 6px; padding: 4px 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {c['outline']}; border-radius: 4px; min-width: 24px; min-height: 24px; }}
QScrollBar::handle:hover {{ background: {c['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QSplitter::handle, QMainWindow::separator {{ background: {c['window']}; width: 5px; height: 5px; }}
QLabel#hint {{ color: {c['muted']}; }}
QLabel#toolGroup {{ color: {c['muted']}; font-size: 10px; padding: 0 6px; }}
QToolButton#ribbonTitle {{ color: {c['muted']}; font-size: 10px; border: none; padding: 0 4px; }}
QToolButton#ribbonTitle:hover {{ color: {c['text']}; background: {hover}; }}
QToolButton#ribbonWorkspace {{ font-weight: bold; padding: 2px 8px; border: 1px solid {c['outline']}; border-radius: 6px; }}
QTabBar#ribbonTabs::tab {{ padding: 3px 10px; font-size: 11px; border: none; background: transparent; color: {c['muted']}; }}
QTabBar#ribbonTabs::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
QFrame#ribbonSeparator {{ color: {c['outline']}; }}
QFrame#browserOverlay {{ background: {c['surface']}; border: 1px solid {c['outline']}; border-radius: 10px; }}
QTreeWidget#browserTree {{ background: transparent; }}
QLabel#browserTitle {{ font-size: 10px; font-weight: bold; color: {c['muted']}; }}
QWidget#operationBar {{ background: {c['surface']}; border: 1px solid {c['outline']}; border-radius: 14px; }}
"""
    palette = {"Window": c["window"], "WindowText": c["text"], "Base": c["surface"], "AlternateBase": raised,
               "Text": c["text"], "Button": raised, "ButtonText": c["text"], "Highlight": c["second"],
               "HighlightedText": c["on_second"], "ToolTipBase": c["surface"], "ToolTipText": c["text"],
               "PlaceholderText": c["muted"], "Link": c["accent"]}
    return Theme(
        name=SYSTEM,
        stylesheet=stylesheet,
        palette=palette,
        chips={"active": f"background:{c['second']}; color:{c['on_second']};",
               "rolled_back": f"background:{c['surface']}; color:{c['muted']};",
               "suppressed": f"background:{c['outline']}; color:{c['muted']}; text-decoration:line-through;",
               "error": f"background:{c['error']}; color:{c['on_error']};"},
        marker=c["accent"],
        # etwas heller als das Fenster, damit sich die Ansicht abhebt und dunkle Körper sichtbar bleiben
        background=_rgb(_mix(c["surface"], c["text"], 0.06)),
        overlay=f"background:{c['surface']}; color:{c['text']}; padding:6px 9px; border-radius:8px;",
        value_box=("background:white; color:#10131a; border:2px solid #2d9bf0; border-radius:6px; "
                   "padding:4px; font-weight:bold;"),
        preview=c["window"],
        # auf hellem Grund werden die hell gezeichneten Symbole in der Textfarbe gezeichnet
        extra={"colours": c, "icon": c["text"] if light else None},
    )


_start_palette: QPalette | None = None
_current: Theme = _CLASSIC


def watched_files() -> list[Path]:
    """Dateien, deren Änderung das Design „System“ betrifft (soweit vorhanden)."""
    return [path for path in _NOCTALIA_FILES if path.exists()]


def watched_folders() -> list[Path]:
    """Ordner der Farbdateien: ersetzt die Shell eine Datei (neu schreiben und umbenennen) oder legt sie erst an,
    meldet das nur die Beobachtung des Ordners."""
    return [path.parent for path in _NOCTALIA_FILES if path.parent.exists()]


def current() -> Theme:
    """Das gerade geltende Design."""
    return _current


def remembered() -> str:
    """Das zuletzt gewählte Design; ohne Wahl gilt „System“."""
    name = QSettings("WolfCAD", "WolfCAD").value("design", SYSTEM)
    return name if name in NAMES else SYSTEM


def apply(app, name: str | None = None, remember: bool = True) -> Theme:
    """Stellt das Design für die ganze Anwendung ein und merkt es sich auf Wunsch."""
    global _start_palette, _current
    if _start_palette is None:
        _start_palette = QPalette(app.palette())        # so sah Qt beim Start aus (Grundlage für „Klassisch“)
    name = name if name in NAMES else remembered()
    _current = _system(_start_palette) if name == SYSTEM else _CLASSIC
    palette = QPalette(_start_palette)
    for role, colour in (_current.palette or {}).items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(colour))
    if _current.palette:
        muted = QColor(_current.palette["PlaceholderText"])
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
            palette.setColor(QPalette.ColorGroup.Disabled, role, muted)
    app.setPalette(palette)
    app.setStyleSheet(_current.stylesheet)
    if remember:
        QSettings("WolfCAD", "WolfCAD").setValue("design", name)
    return _current
