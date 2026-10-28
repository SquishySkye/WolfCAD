# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Oberfläche von WolfCAD.

Beim Beenden räumt dieses Paket die Fenster selbst ab. Ohne das zerstört PySide die noch lebenden
Qt-Objekte am Ende des Programms in beliebiger Reihenfolge – dabei kam es gelegentlich zum Absturz
(Speicherzugriffsfehler in `QObject::~QObject`), vor allem nachdem mehrere Dialoge offen gewesen waren.
"""

from __future__ import annotations

import atexit
import gc


def shut_down() -> None:
    """Schließt und löscht alle Fenster in geordneter Reihenfolge, solange die Anwendung noch lebt."""
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()            # löscht das Fenster mit allen Kindern, Eltern vor Kindern
    # die vorgemerkten Löschungen jetzt ausführen, danach die verwaisten Python-Hüllen freigeben
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


atexit.register(shut_down)
