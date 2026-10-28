# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Projektmappe wie Fusions Datenpanel: Schnellauswahl der Projekte und ihrer Entwürfe am linken Fensterrand.

Ein Projekt ist ein Ordner im Projektordner (Vorgabe: Dokumente/WolfCAD, in den Einstellungen unter
„projektordner“ änderbar). Darin liegen Entwürfe (*.wcad) und Unterordner; zu jedem Entwurf merkt sich WolfCAD beim
Speichern ein Vorschaubild (im versteckten Unterordner „.vorschau“). Doppelklick öffnet einen Entwurf bzw. Ordner.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from .icons import icon

DESIGN_SUFFIX = ".wcad"
PREVIEW_FOLDER = ".vorschau"
_UP = ".."              # Eintrag „eine Ebene höher“


def projects_root() -> Path:
    """Der Ordner, in dem die Projekte liegen (Einstellung „projektordner“, sonst Dokumente/WolfCAD)."""
    chosen = QSettings("WolfCAD", "WolfCAD").value("projektordner", "")
    if chosen:
        return Path(chosen)
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(documents or Path.home()) / "WolfCAD"


def preview_path(design: Path) -> Path:
    """Wo das Vorschaubild eines Entwurfs liegt."""
    return design.parent / PREVIEW_FOLDER / (design.name + ".png")


class DataPanel(QWidget):
    """Projekte (Auswahlliste oben) und der Inhalt des gewählten Projekts als Kacheln mit Vorschaubild."""

    open_requested = Signal(str)        # Pfad eines Entwurfs, der geöffnet werden soll

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        head = QHBoxLayout()
        self.projects = QComboBox()
        self.projects.setToolTip("Projekt wählen")
        self.projects.currentTextChanged.connect(lambda _text: self._enter_project())
        head.addWidget(self.projects, 1)
        new_project = QToolButton()
        new_project.setText("＋")
        new_project.setToolTip("Neues Projekt")
        new_project.clicked.connect(self.new_project)
        head.addWidget(new_project)
        layout.addLayout(head)
        buttons = QHBoxLayout()
        for text, tip, handler in (("Neuer Ordner", "Ordner im aktuellen Projekt anlegen", self.new_folder),
                                   ("Hochladen", "Datei (Entwurf, STEP, Fusion) in das Projekt kopieren", self.upload),
                                   ("⟳", "Inhalt neu einlesen", self.refresh)):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.where = QLabel()
        self.where.setObjectName("hint")
        layout.addWidget(self.where)
        self.files = QListWidget()
        self.files.setViewMode(QListWidget.ViewMode.IconMode)
        self.files.setIconSize(QSize(112, 84))
        self.files.setGridSize(QSize(128, 116))
        self.files.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.files.setMovement(QListWidget.Movement.Static)
        self.files.setWordWrap(True)
        self.files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self._file_menu)
        self.files.itemDoubleClicked.connect(self._activate)
        layout.addWidget(self.files, 1)
        self.folder: Path | None = None     # gerade gezeigter Ordner (Projekt oder Unterordner)
        self.refresh()

    # --- Projekte ---------------------------------------------------------------------------

    def refresh(self) -> None:
        """Projektliste und Inhalt neu einlesen (z. B. nach dem Speichern)."""
        root = projects_root()
        names = sorted(path.name for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")) \
            if root.is_dir() else []
        current = self.projects.currentText()
        self.projects.blockSignals(True)
        self.projects.clear()
        self.projects.addItems(names)
        if current in names:
            self.projects.setCurrentText(current)
        self.projects.blockSignals(False)
        if self.folder is None or not self.folder.is_dir():
            self._enter_project()
        else:
            self._fill()

    def new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "Neues Projekt", "Name des Projekts:")
        name = name.strip()
        if not ok or not name:
            return
        (projects_root() / name).mkdir(parents=True, exist_ok=True)
        self.refresh()
        self.projects.setCurrentText(name)

    def project_folder(self) -> Path | None:
        """Ordner des gewählten Projekts (None, wenn es noch keines gibt)."""
        name = self.projects.currentText()
        return projects_root() / name if name else None

    def _enter_project(self) -> None:
        self.folder = self.project_folder()
        self._fill()

    # --- Inhalt ---------------------------------------------------------------------------

    def _fill(self) -> None:
        self.files.clear()
        if self.folder is None:
            self.where.setText(f"Noch kein Projekt – mit ＋ anlegen (Ordner: {projects_root()})")
            return
        project = self.project_folder()
        relative = self.folder.relative_to(project) if project and self.folder.is_relative_to(project) else Path()
        self.where.setText(f"{self.projects.currentText()}{'' if relative == Path() else ' / ' + str(relative)}")
        if project is not None and self.folder != project:
            up = QListWidgetItem(icon("open"), "⬑ zurück")
            up.setData(Qt.ItemDataRole.UserRole, _UP)
            self.files.addItem(up)
        if not self.folder.is_dir():
            return
        entries = sorted(self.folder.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower()))
        for path in entries:
            if path.name.startswith("."):
                continue
            if path.is_dir():
                item = QListWidgetItem(icon("open"), path.name)
            elif path.suffix.lower() == DESIGN_SUFFIX:
                preview = preview_path(path)
                picture = QIcon(QPixmap(str(preview))) if preview.exists() else icon("sketch")
                item = QListWidgetItem(picture, path.stem)
            else:
                continue            # andere Dateien zeigt die Mappe nicht (Hochladen kopiert sie trotzdem)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.files.addItem(item)

    def _activate(self, item: QListWidgetItem) -> None:
        target = item.data(Qt.ItemDataRole.UserRole)
        if target == _UP:
            self.folder = self.folder.parent
            self._fill()
            return
        path = Path(target)
        if path.is_dir():
            self.folder = path
            self._fill()
        else:
            self.open_requested.emit(str(path))

    def new_folder(self) -> None:
        if self.folder is None:
            return
        name, ok = QInputDialog.getText(self, "Neuer Ordner", "Name des Ordners:")
        if ok and name.strip():
            (self.folder / name.strip()).mkdir(exist_ok=True)
            self._fill()

    def upload(self) -> None:
        """Eine Datei in den aktuellen Ordner kopieren (Fusion: Hochladen)."""
        if self.folder is None:
            QMessageBox.information(self, "Hochladen", "Bitte zuerst ein Projekt anlegen (＋).")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Datei in das Projekt hochladen", "",
                                              "Entwürfe und CAD-Dateien (*.wcad *.f3d *.f3z *.step *.stp)")
        if path:
            shutil.copy2(path, self.folder / Path(path).name)
            self._fill()

    def _file_menu(self, position) -> None:
        item = self.files.itemAt(position)
        if item is None or item.data(Qt.ItemDataRole.UserRole) == _UP:
            menu = QMenu(self)
            menu.addAction("Neuer Ordner …", self.new_folder)
            menu.addAction("Hochladen …", self.upload)
            if self.folder is not None:
                menu.addAction("Im Dateimanager zeigen",
                               lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))))
            menu.exec(self.files.mapToGlobal(position))
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        menu.addAction("Öffnen", lambda: self._activate(item))
        menu.addAction("Umbenennen …", lambda: self._rename(path))
        menu.addAction("In den Papierkorb", lambda: self._trash(path))
        menu.addAction("Im Dateimanager zeigen",
                       lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))))
        menu.exec(self.files.mapToGlobal(position))

    def _rename(self, path: Path) -> None:
        name, ok = QInputDialog.getText(self, "Umbenennen", "Neuer Name:", text=path.stem if path.is_file() else path.name)
        name = name.strip()
        if not ok or not name:
            return
        target = path.with_name(name + (path.suffix if path.is_file() else ""))
        if target.exists():
            QMessageBox.warning(self, "Umbenennen", f"„{target.name}“ gibt es schon.")
            return
        path.rename(target)
        if path.is_file() or not path.exists():
            old_preview = preview_path(path)
            if old_preview.exists():
                old_preview.rename(preview_path(target))
        self._fill()

    def _trash(self, path: Path) -> None:
        """In den Papierkorb (nicht endgültig löschen): lässt sich dort wiederherstellen."""
        if QMessageBox.question(self, "In den Papierkorb", f"„{path.name}“ in den Papierkorb legen?") \
                != QMessageBox.StandardButton.Yes:
            return
        from PySide6.QtCore import QFile
        QFile.moveToTrash(str(path))
        self._fill()
