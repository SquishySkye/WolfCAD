# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Dialog „Zeichnung“: Blatt mit Ansichten der Körper, Vorschau und Ausgabe als PDF oder SVG."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QMarginsF, QRectF, QSizeF, Qt, Signal
from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout,
)

from ..core import drawing
from . import theme


_SNAP_MM = 3.0      # so nah (auf dem Blatt) muss ein Klick an einem Punkt liegen
_PICK_MM = 12.0     # so nah muss ein Klick an der Maßzahl liegen, um das Maß zu löschen
_SIDE = "Seite"     # Ankreuzfeld für die Seitenansicht (links oder rechts, je nach Anordnung)


def write_pdf(svg: str, path: str, sheet: tuple[float, float]) -> None:
    """Schreibt das Blatt maßstäblich in eine PDF-Datei (Blattgröße in mm)."""
    writer = QPdfWriter(path)
    writer.setPageLayout(QPageLayout(QPageSize(QSizeF(*sheet), QPageSize.Unit.Millimeter),
                                     QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0)))
    writer.setResolution(600)
    painter = QPainter(writer)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter, QRectF(0, 0, writer.width(), writer.height()))
    painter.end()


class _Preview(QSvgWidget):
    """Vorschau des Blatts; meldet Klicks, Ziehen und Loslassen in Blattkoordinaten (mm)."""

    clicked = Signal(float, float)
    dragged = Signal(float, float)
    hovered = Signal(float, float)
    released = Signal(float, float)
    sheet_size = (297.0, 210.0)

    def _sheet_point(self, event) -> tuple[float, float]:
        # Das Blatt liegt mittig und im richtigen Seitenverhältnis in der Vorschau
        scale = min(self.width() / self.sheet_size[0], self.height() / self.sheet_size[1])
        left = (self.width() - self.sheet_size[0] * scale) / 2
        top = (self.height() - self.sheet_size[1] * scale) / 2
        return (event.position().x() - left) / scale, (event.position().y() - top) / scale

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(*self._sheet_point(event))

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.dragged.emit(*self._sheet_point(event))
        else:
            self.hovered.emit(*self._sheet_point(event))

    def mouseReleaseEvent(self, event) -> None:
        self.released.emit(*self._sheet_point(event))


class DrawingDialog(QDialog):
    """Stellt das Zeichenblatt zusammen; jede Änderung der Einstellungen erneuert die Vorschau.

    `memory` ist ein Verzeichnis des Entwurfs, in dem die selbst gesetzten Maße stehen bleiben.
    """

    def __init__(self, shapes: list, title: str, parent=None, memory: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Zeichnung")
        self.resize(1100, 760)
        self._shapes = shapes
        self._svg = ""
        self._layout = None                 # das zuletzt angeordnete Blatt (für Klicks und Maße)
        self._memory = memory if memory is not None else {}
        # Eigene Maße: je Eintrag [Ansicht, [x1, y1], [x2, y2], Art] in den Maßen der Ansicht
        self._own = [list(entry) for entry in self._memory.get("masse", [])]
        self._lost: set[int] = set()        # Nummern der Maße, deren Punkte es nicht mehr gibt
        self._first = None                  # erster angeklickter Punkt eines neuen Maßes
        self._second = None                 # Winkelmaß: der erste Schenkelpunkt (nach dem Scheitel)
        self._pending = None                # Maß mit beiden Punkten, dessen Maßlinie noch der Maus folgt
        # Von Hand verschobene Ansichten: {Ansicht: [dx, dy]} in mm auf dem Blatt
        self._offsets = {name: list(shift) for name, shift in self._memory.get("versatz", {}).items()}
        self._grip = None                   # (Ansicht, x, y) solange eine Ansicht gezogen wird
        self._title = QLineEdit(title)
        self._title.editingFinished.connect(self.refresh)
        self._sheet = QComboBox()
        self._sheet.addItems(list(drawing.SHEETS))
        self._sheet.currentTextChanged.connect(lambda _text: self.refresh())
        # Anordnung der Ansichten: Methode 1 (Europa) oder Methode 3 (Amerika)
        self._method = QComboBox()
        self._method.addItems(list(drawing.METHODS))
        self._method.currentTextChanged.connect(lambda _text: self.refresh())
        self._views = {}
        options = QHBoxLayout()
        options.addWidget(QLabel("Titel"))
        options.addWidget(self._title, 1)
        options.addWidget(QLabel("Blatt"))
        options.addWidget(self._sheet)
        options.addWidget(QLabel("Anordnung"))
        options.addWidget(self._method)
        toggles = QHBoxLayout()
        for name in ("Vorne", "Oben", _SIDE, "Isometrisch", drawing.SECTION, drawing.CROSS_SECTION):
            box = QCheckBox(name)
            # die Schnittansichten kommen auf Wunsch dazu
            box.setChecked(name not in (drawing.SECTION, drawing.CROSS_SECTION))
            box.toggled.connect(lambda _on: self.refresh())
            self._views[name] = box
            toggles.addWidget(box)
        # Lage des Schnitts A–A: mittig, um diesen Wert nach hinten (+) oder vorn (−) verschoben
        self._shift = QDoubleSpinBox()
        self._shift.setRange(-10000, 10000)
        self._shift.setDecimals(1)
        self._shift.setSuffix(" mm")
        self._shift.setToolTip("Lage des Schnitts A–A, von der Mitte des Modells aus gemessen")
        self._shift.valueChanged.connect(lambda _value: self.refresh())
        toggles.addWidget(QLabel("Lage A–A"))
        toggles.addWidget(self._shift)
        # Lage des Querschnitts B–B: mittig, um diesen Wert nach rechts (+) oder links (−) verschoben
        self._cross_shift = QDoubleSpinBox()
        self._cross_shift.setRange(-10000, 10000)
        self._cross_shift.setDecimals(1)
        self._cross_shift.setSuffix(" mm")
        self._cross_shift.setToolTip("Lage des Querschnitts B–B, von der Mitte des Modells aus gemessen")
        self._cross_shift.valueChanged.connect(lambda _value: self.refresh())
        toggles.addWidget(QLabel("Lage B–B"))
        toggles.addWidget(self._cross_shift)
        # Richtung des Schnitts A–A: um die Hochachse gedreht (0° = parallel zur Vorderansicht)
        self._section_angle = QDoubleSpinBox()
        self._section_angle.setRange(-90.0, 90.0)
        self._section_angle.setDecimals(1)
        self._section_angle.setSingleStep(5.0)
        self._section_angle.setSuffix(" °")
        self._section_angle.setValue(float(self._memory.get("schnittwinkel", 0.0)))
        self._section_angle.setToolTip("Richtung des Schnitts A–A: um die Hochachse gedreht, 0° liegt parallel zur Vorderansicht")
        self._section_angle.valueChanged.connect(self._section_angle_changed)
        toggles.addWidget(QLabel("Winkel A–A"))
        toggles.addWidget(self._section_angle)
        self._hidden = QCheckBox("Verdeckte Kanten")
        self._hidden.setChecked(True)
        self._dimensions = QCheckBox("Gesamtmaße")
        self._dimensions.setChecked(True)
        for box in (self._hidden, self._dimensions):
            box.toggled.connect(lambda _on: self.refresh())
            toggles.addWidget(box)
        toggles.addStretch(1)
        self._preview = _Preview()
        self._preview.setStyleSheet(f"background:{theme.current().preview};")
        self._preview.clicked.connect(self._preview_clicked)
        self._preview.dragged.connect(self._preview_dragged)
        self._preview.hovered.connect(self._preview_hovered)
        self._preview.setMouseTracking(True)        # die Maßlinie folgt der Maus auch ohne gedrückte Taste
        self._preview.released.connect(self._preview_released)
        self._status = QLabel()
        # Eigene Maße: einschalten, dann zwei Punkte einer Ansicht anklicken
        self._measure = QPushButton("Maß setzen")
        self._measure.setCheckable(True)
        self._measure.setToolTip("Zwei Punkte derselben Ansicht anklicken (Ecken, Kantenenden, Bohrungsmitten), "
                                 "dann die Maßlinie mit der Maus an ihren Platz ziehen und mit einem Klick absetzen")
        self._measure.toggled.connect(self._measure_toggled)
        # Maßart: waagrecht/senkrecht (das größere von beiden) oder der wahre, schräge Abstand
        self._kind = QComboBox()
        self._kind.addItems(["Waagrecht/Senkrecht", "Schräg (wahrer Abstand)", "Winkel (Scheitel, dann zwei Punkte)",
                             "Radius (Rundung oder Kreis anklicken)"])
        self._kind.setToolTip("Art des nächsten gesetzten Maßes")
        self._delete = QPushButton("Maß löschen")
        self._delete.setCheckable(True)
        self._delete.setToolTip("Einschalten und die Maßzahl des Maßes anklicken, das weg soll")
        self._delete.toggled.connect(self._delete_toggled)
        undo = QPushButton("Letztes Maß entfernen")
        undo.clicked.connect(self._remove_last)
        reset = QPushButton("Ansichten zurücksetzen")
        reset.setToolTip("Stellt verschobene Ansichten wieder an ihren üblichen Platz "
                         "(Ansichten lassen sich in der Vorschau mit der Maus verschieben)")
        reset.clicked.connect(self._reset_offsets)
        measures = QHBoxLayout()
        # Detailansicht: Mitte und Rand des Ausschnitts in einer Ansicht anklicken, er erscheint vergrößert
        self._detail = QPushButton("Detail setzen")
        self._detail.setCheckable(True)
        self._detail.setToolTip("In einer Ansicht erst die Mitte, dann den Rand des Ausschnitts anklicken")
        self._detail.toggled.connect(self._detail_toggled)
        self._detail_factor = QDoubleSpinBox()
        self._detail_factor.setRange(1.5, 20.0)
        self._detail_factor.setSingleStep(0.5)
        self._detail_factor.setPrefix("× ")
        # ältere Entwürfe kennen nur eine Detailansicht unter „detail“: in die Liste übernehmen
        if self._memory.get("detail") and not self._memory.get("details"):
            self._memory["details"] = [self._memory.pop("detail")]
        self._detail_factor.setValue(float((self._memory.get("details") or [{}])[-1].get("faktor", 2.0)))
        self._detail_factor.setToolTip("Vergrößerung der Detailansicht gegenüber dem Blattmaßstab")
        self._detail_factor.valueChanged.connect(self._detail_factor_changed)
        self._detail_centre = None          # (Ansicht, Punkt) nach dem ersten Klick
        no_detail = QPushButton("Detail entfernen")
        no_detail.clicked.connect(self._remove_detail)
        for widget in (self._measure, self._kind, self._delete, undo, reset, self._detail, self._detail_factor, no_detail):
            measures.addWidget(widget)
        measures.addStretch(1)
        save_pdf = QPushButton("Als PDF speichern …")
        save_pdf.clicked.connect(lambda: self._save("PDF (*.pdf)", ".pdf"))
        save_svg = QPushButton("Als SVG speichern …")
        save_svg.clicked.connect(lambda: self._save("SVG (*.svg)", ".svg"))
        close = QPushButton("Schließen")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self._status, 1)
        buttons.addWidget(save_pdf)
        buttons.addWidget(save_svg)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addLayout(options)
        layout.addLayout(toggles)
        layout.addLayout(measures)
        layout.addWidget(self._preview, 1)
        layout.addLayout(buttons)
        self.refresh()

    def sheet_size(self) -> tuple[float, float]:
        return drawing.SHEETS[self._sheet.currentText()]

    def refresh(self) -> None:
        """Berechnet das Blatt mit den aktuellen Einstellungen neu und zeigt es."""
        # Die Seitenansicht ist je nach Anordnung die von links oder die von rechts
        side = next(name for name in drawing.METHODS[self._method.currentText()] if name in ("Links", "Rechts"))
        chosen = [side if name == _SIDE else name for name, box in self._views.items() if box.isChecked()]
        try:
            self._layout = drawing.arrange(self._shapes, self._sheet.currentText(), chosen,
                                           self._method.currentText(), self._shift.value(), self._offsets,
                                           self._cross_shift.value(), self._memory.get("details"),
                                           self._section_angle.value())
        except ValueError as error:
            self._svg, self._layout = "", None
            self._status.setText(str(error))
            return
        # Hat sich das Modell geändert, wandern die Maße an die nächsten aktuellen Punkte; was keinen
        # Punkt mehr findet, bleibt stehen und wird rot gezeichnet
        self._own, self._lost = drawing.resnap(self._layout, self._own, self._memory.get("ansichtsgroesse"))
        sizes = dict(self._memory.get("ansichtsgroesse", {}))
        sizes.update({placed.view.name: [placed.view.width, placed.view.height] for placed in self._layout.placed})
        self._memory["ansichtsgroesse"] = sizes
        if self._own or "masse" in self._memory:
            self._memory["masse"] = [list(entry) for entry in self._own]
        self._draw()
        if self._lost and not (self._measure.isChecked() or self._delete.isChecked()):
            self._status.setText(f"{len(self._lost)} Maß(e) haben ihre Punkte verloren (rot) – löschen oder neu setzen")

    def _draw(self) -> None:
        """Zeichnet das angeordnete Blatt neu (z. B. nach einem neuen Maß; die Ansichten bleiben berechnet)."""
        if self._layout is None:
            return
        marks = [point[2] for point in (self._first, self._second) if point is not None]
        # ein Maß, das gerade platziert wird, erscheint schon mit (es steht noch nicht in der Liste)
        own = self._own + ([self._pending] if self._pending is not None else [])
        self._svg = drawing.render(self._layout, self._title.text().strip() or "Zeichnung", self._hidden.isChecked(),
                                   self._dimensions.isChecked(), own, marks, self._lost)
        self._preview.sheet_size = self._layout.size
        self._preview.load(QByteArray(self._svg.encode("utf-8")))
        # Die Vorschau behält das Seitenverhältnis des Blatts
        self._preview.renderer().setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        if not (self._measure.isChecked() or self._delete.isChecked()):
            self._status.setText("")

    # --- Eigene Maße ----------------------------------------------------------

    def _measure_toggled(self, on: bool) -> None:
        self._first = self._second = None
        self._pending = None                    # ein halb gesetztes Maß verfällt
        if on:
            self._delete.setChecked(False)      # immer nur eine der beiden Arbeitsweisen
        self._status.setText("Maß setzen: ersten Punkt anklicken" if on else "")
        self._draw()

    def _section_angle_changed(self, value: float) -> None:
        self._memory["schnittwinkel"] = value
        self.refresh()

    def _detail_toggled(self, on: bool) -> None:
        self._detail_centre = None
        if on:
            self._measure.setChecked(False)
            self._delete.setChecked(False)
        self._status.setText("Detail: Mitte des Ausschnitts in einer Ansicht anklicken" if on else "")

    def _detail_clicked(self, x: float, y: float) -> None:
        """Erster Klick: Mitte des Ausschnitts; zweiter Klick: sein Rand – dann erscheint die Detailansicht."""
        if self._detail_centre is None:
            name = self._layout.view_at(x, y)
            if name is None or name == "Isometrisch" or drawing.is_detail(name):
                self._status.setText("Detail: bitte in eine der geraden Ansichten klicken")
                return
            self._detail_centre = (name, self._layout.from_sheet(name, x, y))
            self._status.setText("Detail: Rand des Ausschnitts anklicken")
            return
        name, centre = self._detail_centre
        edge = self._layout.from_sheet(name, x, y)
        radius = ((edge[0] - centre[0]) ** 2 + (edge[1] - centre[1]) ** 2) ** 0.5
        if radius < 1e-6:
            return
        details = self._memory.setdefault("details", [])
        if len(details) >= len(drawing.DETAIL_LETTERS):
            self._status.setText(f"Mehr als {len(drawing.DETAIL_LETTERS)} Detailansichten gibt es nicht")
            return
        details.append({"ansicht": name, "mitte": list(centre), "radius": radius, "faktor": self._detail_factor.value()})
        self._detail.setChecked(False)
        self.refresh()
        self._status.setText("Detailansicht gesetzt – sie lässt sich wie die anderen Ansichten verschieben; "
                             "„Detail setzen“ fügt eine weitere hinzu")

    def _detail_factor_changed(self, value: float) -> None:
        """Die Vergrößerung gilt für die zuletzt gesetzte Detailansicht (und für die nächste neue)."""
        if self._memory.get("details"):
            self._memory["details"][-1]["faktor"] = value
            self.refresh()

    def _remove_detail(self) -> None:
        """Entfernt die zuletzt gesetzte Detailansicht."""
        details = self._memory.get("details") or []
        if details:
            details.pop()
            self._offsets.pop(drawing.detail_name(len(details)), None)
        self.refresh()

    def _delete_toggled(self, on: bool) -> None:
        if on:
            self._measure.setChecked(False)
        self._status.setText("Maß löschen: die Maßzahl anklicken" if on else "")
        self._draw()

    def _store_own(self) -> None:
        self._memory["masse"] = [list(entry) for entry in self._own]

    def _delete_at(self, x: float, y: float) -> None:
        """Löscht das Maß, dessen Maßzahl dem Klick am nächsten liegt."""
        anchors = drawing.dimension_anchors(self._layout, self._own)
        near = min((index for index, anchor in enumerate(anchors) if anchor is not None), default=None,
                   key=lambda index: (anchors[index][0] - x) ** 2 + (anchors[index][1] - y) ** 2)
        if near is None or (anchors[near][0] - x) ** 2 + (anchors[near][1] - y) ** 2 > _PICK_MM ** 2:
            self._status.setText("Dort steht kein eigenes Maß – bitte die Maßzahl anklicken")
            return
        del self._own[near]
        # die Nummern der verlorenen Maße hinter dem gelöschten rücken auf
        self._lost = {index - (index > near) for index in self._lost if index != near}
        self._store_own()
        self._status.setText("Maß gelöscht – weitere Maßzahl anklicken oder „Maß löschen“ ausschalten")
        self._draw()

    def _preview_dragged(self, x: float, y: float) -> None:
        """Zieht die gegriffene Ansicht mit der Maus über das Blatt."""
        if self._grip is None or self._layout is None:
            return
        name, start_x, start_y = self._grip
        dx, dy = self._layout.move(name, x - start_x, y - start_y)     # am Blattrand gekappt
        shift = self._offsets.setdefault(name, [0.0, 0.0])
        shift[0], shift[1] = shift[0] + dx, shift[1] + dy
        self._grip = (name, start_x + dx, start_y + dy)
        self._draw()

    def _spot(self, x: float, y: float) -> list[float]:
        """Blattstelle als Platz der Maßlinie: in Modell-mm ab der linken unteren Ecke der Ansicht des Maßes."""
        left, bottom = self._layout.to_sheet(self._pending[0], 0.0, 0.0)
        return [(x - left) / self._layout.factor, (y - bottom) / self._layout.factor]

    def _preview_hovered(self, x: float, y: float) -> None:
        """Solange ein Maß platziert wird, läuft seine Maßlinie durch die Mausposition."""
        if self._pending is not None and self._layout is not None:
            self._pending[4] = self._spot(x, y)
            self._draw()

    def _preview_released(self, _x: float, _y: float) -> None:
        if self._grip is not None:
            self._grip = None
            self._memory["versatz"] = {name: list(shift) for name, shift in self._offsets.items()}

    def _reset_offsets(self) -> None:
        self._offsets = {}
        self._memory["versatz"] = {}
        self.refresh()

    def _preview_clicked(self, x: float, y: float) -> None:
        """Klick auf das Blatt (mm): setzt oder löscht ein Maß, sonst greift er eine Ansicht zum Verschieben."""
        if self._layout is None:
            return
        if self._delete.isChecked():
            self._delete_at(x, y)
            return
        if self._detail.isChecked():
            self._detail_clicked(x, y)
            return
        if not self._measure.isChecked():
            name = self._layout.view_at(x, y)
            self._grip = (name, x, y) if name else None
            return
        if self._pending is not None:
            # dritter Klick: die Maßlinie bleibt hier stehen
            self._pending[4] = self._spot(x, y)
            self._own.append(self._pending)
            self._pending = None
            self._store_own()
            self._status.setText("Maß gesetzt – für ein weiteres den ersten Punkt anklicken")
            self._draw()
            return
        if self._kind.currentIndex() == 3:
            self._radius_clicked(x, y)
            return
        points = self._layout.snap_points()
        if self._first is not None:
            points = [point for point in points if point[0] == self._first[0]]     # nur in derselben Ansicht
        nearest = min(points, default=None, key=lambda point: (point[2][0] - x) ** 2 + (point[2][1] - y) ** 2)
        if nearest is None or (nearest[2][0] - x) ** 2 + (nearest[2][1] - y) ** 2 > _SNAP_MM ** 2:
            self._status.setText("Dort ist kein Punkt – bitte eine Ecke, ein Kantenende oder eine Bohrungsmitte anklicken")
            return
        if self._first is None:
            self._first = nearest
            self._status.setText(f"Maß setzen: zweiten Punkt in der Ansicht „{nearest[0]}“ anklicken")
        elif nearest[1] != self._first[1] and self._kind.currentIndex() == 2:
            # Winkelmaß: nach dem Scheitel folgen zwei Punkte, je einer auf jedem Schenkel
            if self._second is None:
                self._second = nearest
                self._status.setText("Winkel: Punkt auf dem zweiten Schenkel anklicken")
            elif nearest[1] != self._second[1]:
                # wie bei den anderen Maßen folgt jetzt der Bogen der Maus, ein Klick setzt ihn ab
                self._pending = [nearest[0], list(self._first[1]), list(self._second[1]), drawing.ANGLE, None,
                                 list(nearest[1])]
                self._first = self._second = None
                self._status.setText("Winkelbogen mit der Maus an seinen Platz ziehen und klicken")
        elif nearest[1] != self._first[1]:
            kind = drawing.ALIGNED if self._kind.currentIndex() == 1 else ""
            self._pending = [nearest[0], list(self._first[1]), list(nearest[1]), kind, None]
            self._first = None
            self._status.setText("Maßlinie mit der Maus an ihren Platz ziehen und klicken")
        self._draw()

    def _radius_clicked(self, x: float, y: float) -> None:
        """Radiusmaß: der Klick trifft eine Rundung oder einen Kreis; danach wird die Maßzahl mit der Maus platziert."""
        best = None
        for placed in self._layout.placed:
            view = placed.view
            if view.name == "Isometrisch" or drawing.is_detail(view.name):
                continue
            here = self._layout.from_sheet(view.name, x, y)
            curves = [(cx, cy, radius) for cx, cy, radius, _px, _py in view.arcs]
            curves += [(cx, cy, diameter / 2) for cx, cy, diameter in view.rounds]
            for cx, cy, radius in curves:
                away = ((here[0] - cx) ** 2 + (here[1] - cy) ** 2) ** 0.5
                miss = abs(away - radius) * self._layout.factor            # Abstand zur Kurve in Blatt-mm
                if away > 1e-9 and miss <= _SNAP_MM and (best is None or miss < best[0]):
                    # Punkt auf der Kurve in Richtung des Klicks
                    on_curve = [cx + (here[0] - cx) / away * radius, cy + (here[1] - cy) / away * radius]
                    best = (miss, [view.name, [cx, cy], on_curve, drawing.RADIUS, None])
        if best is None:
            self._status.setText("Dort ist keine Rundung – bitte auf einen Bogen oder Kreis klicken")
            return
        self._pending = best[1]
        self._status.setText("Maßzahl mit der Maus an ihren Platz ziehen und klicken")
        self._draw()

    def _remove_last(self) -> None:
        if self._own:
            self._own.pop()
            self._lost.discard(len(self._own))
            self._store_own()
        self._first = None
        self._draw()

    def save(self, path: str) -> None:
        """Schreibt das Blatt; die Endung entscheidet über PDF oder SVG."""
        if not self._svg:
            raise ValueError("Es gibt nichts zu speichern")
        if Path(path).suffix.lower() == ".svg":
            Path(path).write_text(self._svg, encoding="utf-8")
        else:
            write_pdf(self._svg, path, self.sheet_size())

    def _save(self, file_filter: str, suffix: str) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Zeichnung speichern", "zeichnung" + suffix, file_filter)
        if not path:
            return
        if Path(path).suffix.lower() != suffix:
            path += suffix
        try:
            self.save(path)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Zeichnung", f"Speichern fehlgeschlagen:\n{error}")
            return
        self._status.setText(f"Gespeichert: {path}")
