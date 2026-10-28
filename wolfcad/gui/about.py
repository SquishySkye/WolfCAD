# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""„Über WolfCAD“: Version, Urheber, Lizenzhinweis und die mitgelieferten Fremdprogramme.

Die GPL-3.0 verlangt, dass eine Programmoberfläche die „Appropriate Legal Notices“ zeigt (Abschnitt 0 und 5d):
Urheberrechtsvermerk, Ausschluss der Gewährleistung und wo die Lizenz zu finden ist."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTabWidget, QTextBrowser, QVBoxLayout

import wolfcad

_ROOT = Path(wolfcad.__file__).resolve().parent.parent       # Projektordner bzw. usr/app im AppImage

NOTICE = (f"<h2>WolfCAD {wolfcad.__version__}</h2>"
          "<p>Parametrisches 3D-CAD mit Timeline, .f3d-Import, 3D-Druck und Physik-Tests.</p>"
          f"<p>{wolfcad.__copyright__}</p>"
          "<p>WolfCAD ist freie Software: Sie dürfen es unter den Bedingungen der <b>GNU General Public License</b> "
          "Version 3 oder (nach Ihrer Wahl) jeder späteren Version weitergeben und verändern.</p>"
          "<p>WolfCAD wird in der Hoffnung verbreitet, dass es nützlich ist, aber <b>ohne jede Gewährleistung</b> – "
          "sogar ohne die implizite Gewährleistung der Marktreife oder der Eignung für einen bestimmten Zweck. "
          "Physik-Ergebnisse sind Berechnungen am Modell und ersetzen keine Prüfung des echten Teils.</p>"
          "<p>Lizenztext: <a href='https://www.gnu.org/licenses/gpl-3.0.html'>gnu.org/licenses/gpl-3.0</a> "
          "(liegt auch als Datei LICENSE bei).</p>"
          "<p>Autodesk und Fusion sind Marken der Autodesk, Inc.; Creality und Creality Print sind Marken von "
          "Shenzhen Creality 3D Technology Co., Ltd. WolfCAD ist ein unabhängiges Projekt, steht mit keinem dieser "
          "Unternehmen in Verbindung und wird von ihnen weder unterstützt noch geprüft. Es liest .f3d-Dateien mit "
          "eigenem Code und enthält keine Software, Grafik oder Daten von Autodesk. Die Namen werden nur genannt, "
          "um anzugeben, mit welchen Dateien und Programmen WolfCAD zusammenarbeitet.</p>")


def _read(name: str) -> str:
    for folder in (_ROOT, _ROOT / "packaging"):
        path = folder / name
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    return f"({name} nicht gefunden)"


class AboutDialog(QDialog):
    """Drei Reiter: Hinweis, GPL-Lizenztext, mitgelieferte Programme und Bibliotheken."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Über WolfCAD")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        notice = QLabel(NOTICE)
        notice.setWordWrap(True)
        notice.setOpenExternalLinks(True)
        notice.setAlignment(Qt.AlignmentFlag.AlignTop)
        tabs.addTab(notice, "WolfCAD")
        for title, name in (("Lizenz (GPL-3.0)", "LICENSE"), ("Mitgelieferte Programme", "LIZENZEN.txt")):
            text = QTextBrowser()
            text.setPlainText(_read(name))
            tabs.addTab(text, title)
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Schließen")   # ohne Qt-Übersetzung
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
