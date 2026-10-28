# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Programmstart: `python -m wolfcad [datei.f3d]`."""

import datetime
import faulthandler
import sys
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication, QMessageBox

_last_report = [0.0]        # Zeitpunkt der letzten Fehlermeldung am Bildschirm


def _install_error_handling(folder) -> None:
    """Fehler beenden das Programm nicht still: sie landen im Protokoll und werden gemeldet.

    Programmfehler (Python) werden abgefangen, das Programm läuft weiter. Stürzt der Geometriekern ab,
    schreibt `faulthandler` wenigstens die Absturzstelle ins Protokoll; der Entwurf lässt sich dann beim
    nächsten Start wiederherstellen.
    """
    try:
        folder.mkdir(parents=True, exist_ok=True)
        log = open(folder / "fehler.log", "a", encoding="utf-8")       # bleibt für faulthandler offen
    except OSError:
        return
    faulthandler.enable(file=log)

    def report(kind, value, trace) -> None:
        text = "".join(traceback.format_exception(kind, value, trace))
        log.write(f"\n--- {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ---\n{text}")
        log.flush()
        sys.stderr.write(text)
        now = datetime.datetime.now().timestamp()
        if now - _last_report[0] > 15:      # nicht bei jeder Mausbewegung ein neues Fenster
            _last_report[0] = now
            message = f"Es ist ein Fehler aufgetreten, das Programm läuft weiter.\n\n{kind.__name__}: {value}\n\n" \
                      f"Einzelheiten stehen in {folder / 'fehler.log'}"
            # erst nach dem laufenden Ereignis melden (der Fehler kann mitten im Zeichnen aufgetreten sein)
            QTimer.singleShot(0, lambda: QMessageBox.warning(QApplication.activeWindow(), "WolfCAD", message))

    sys.excepthook = report


def main() -> int:
    # OpenGL 3.3 Core muss vor dem Anlegen der Anwendung angefordert werden
    surface = QSurfaceFormat()
    surface.setVersion(3, 3)
    surface.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    surface.setDepthBufferSize(24)
    surface.setSamples(4)
    QSurfaceFormat.setDefaultFormat(surface)

    app = QApplication(sys.argv)
    # Muss dem Namen der .desktop-Datei entsprechen, damit Fenster und Symbol zusammenfinden
    app.setDesktopFileName("de.skye.WolfCAD")
    app.setApplicationName("WolfCAD")
    # Import erst nach QApplication, damit Qt vollständig eingerichtet ist
    from .gui import shut_down, theme
    from .core.timeline import Timeline
    from .gui.main_window import STATE_FOLDER, MainWindow

    Timeline.crash_guard = True     # riskante Schritte des Geometriekerns erst zur Probe rechnen (core/guard.py)

    _install_error_handling(STATE_FOLDER)
    theme.apply(app)        # zuletzt gewähltes Design, ohne Wahl das des Betriebssystems

    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        window.import_file(sys.argv[1])
    else:
        window.offer_recovery()     # nach einem Absturz den gesicherten Stand anbieten
    window.enable_safeguards()      # Nachfrage bei ungespeicherten Änderungen, laufende Sicherung
    code = app.exec()
    shut_down()                     # Fenster geordnet abräumen (sonst gelegentlich Absturz beim Beenden)
    return code


if __name__ == "__main__":
    sys.exit(main())
