# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Reiter „Schaltplan“ (Runde 98, Nutzerwunsch: Schaltplanfunktion wie in Fusion – eigene Umsetzung).

Aufbau wie Fusions Elektronik-Schaltplan: oben die Werkzeugleiste (Auswählen, Bauteil, Netz, Label, Name, Wert,
Drehen, Spiegeln, Löschen, Prüfen, Neu nummerieren, Ausgabe), links die Bibliothek mit Suche, in der Mitte das
Blatt mit Raster, rechts die Eigenschaften des Gewählten, unten die Ergebnisse der Prüfung (ERC).

Bedienung:
- Bauteil: in der Bibliothek doppelklicken (oder Taste A und suchen) – es hängt an der Maus; R dreht, M spiegelt,
  jeder Klick setzt eins (die Nummer zählt weiter), Esc/Rechtsklick beendet.
- Netz (Taste N): Klick beginnt, die Leitung folgt rechtwinklig (Leertaste: zuerst senkrecht), jeder Klick setzt
  einen Knick; ein Klick auf einen Pin oder eine Leitung schließt an und beendet, ebenso Doppelklick, Rechtsklick
  oder Esc. Endet eine Leitung mitten auf einer anderen, entsteht ein Verbindungspunkt.
- Auswählen: Klick wählt (Strg/Umschalt: hinzu), Ziehen auf dem Leeren zieht einen Rahmen, Ziehen am Gewählten
  verschiebt – angeschlossene Leitungen dehnen sich mit (Gummiband). Doppelklick auf ein Bauteil: Wert ändern,
  auf eine Leitung: Netz umbenennen. Entf löscht, R dreht, M spiegelt, Strg+D verdoppelt.
- Ansicht: Mausrad zoomt zur Maus, mittlere oder rechte Taste gezogen verschiebt, F/Pos1 zeigt alles.
- Strg+Z / Strg+Y: Rückgängig/Wiederholen (eigener Verlauf des Schaltplans).
Über einer Leitung leuchtet das ganze Netz auf; die Statuszeile nennt Netzname und Anschlüsse.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QImage, QKeySequence, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen, QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter, QToolBar, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..electronics import symbols as sy
from ..electronics.schematic import Label, Part, Schematic, Wire
from . import theme
from .icons import icon

TOOLS = ("auswahl", "bauteil", "netz", "label", "name", "wert", "loeschen")
_TOOL_TEXT = {"auswahl": "Auswählen", "bauteil": "Bauteil", "netz": "Netz", "label": "Label", "name": "Name",
              "wert": "Wert", "loeschen": "Löschen"}
_TOOL_ICON = {"auswahl": "select", "bauteil": "sch_part", "netz": "sch_wire", "label": "sch_label",
              "name": "sch_name", "wert": "sch_value", "loeschen": "delete"}
_TOOL_KEY = {"auswahl": "Esc", "bauteil": "A", "netz": "N", "label": "L", "name": "", "wert": "", "loeschen": ""}
_TOOL_TIP = {
    "auswahl": "Auswählen und verschieben: Klick wählt, Rahmen ziehen, Gewähltes ziehen (Leitungen dehnen sich mit)",
    "bauteil": "Bauteil aus der Bibliothek setzen (A): R dreht, M spiegelt, Klick setzt, Esc beendet",
    "netz": "Leitung zeichnen (N): rechtwinklig, Leertaste wechselt den Knick, Klick auf Pin/Leitung schließt an",
    "label": "Netzlabel auf eine Leitung setzen (L): zeigt den Netznamen, gleichnamige Netze sind verbunden",
    "name": "Name ändern: Bauteil anklicken (Bauteilnummer) oder Leitung anklicken (Netzname)",
    "wert": "Wert eines Bauteils ändern (z. B. 4k7, 100n, BC547)",
    "loeschen": "Bauteil, Leitung oder Label anklicken, um es zu löschen",
}
UNDO_LIMIT = 200
_UNIT_MM = 2.54


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t)


class Colours:
    """Farben des Blatts, abgeleitet vom Design (Hintergrund wie die 3D-Ansicht). `printing`: schwarz auf weiß
    für PDF/SVG/Bild."""

    def __init__(self, printing: bool = False):
        if printing:
            self.background = QColor("white")
            dark = False
        else:
            self.background = QColor.fromRgbF(*theme.current().background)
            dark = self.background.lightnessF() < 0.5
        ink = QColor("#e6e9ef") if dark else QColor("#1d2027")
        self.ink = ink
        self.symbol = QColor("#e9b44c") if dark else QColor("#8a1c1c")      # Schaltzeichen
        self.wire = QColor("#5fd17a") if dark else QColor("#1b7a33")
        self.pin = _blend(self.symbol, self.background, 0.15)
        self.text = _blend(ink, self.background, 0.1)
        self.muted = _blend(ink, self.background, 0.55)
        self.grid = _blend(ink, self.background, 0.82)
        self.grid_major = _blend(ink, self.background, 0.7)
        self.label = QColor("#4fc3f7") if dark else QColor("#0b5f8a")
        self.select = QColor.fromRgbF(*theme.current().handle[:3]) if not printing else QColor("#0060c0")
        self.highlight = QColor("#ffd23f") if dark else QColor("#e08a00")
        self.error = QColor("#ff5c5c")
        self.warning = QColor("#ffb020")
        self.open_pin = QColor("#ff5c5c")


class SchematicCanvas(QWidget):
    """Das Blatt: zeichnet den Schaltplan und setzt die Bedienung der Werkzeuge um (siehe Moduldoku)."""

    changed = Signal()              # der Schaltplan wurde geändert (nach jedem abgeschlossenen Schritt)
    selection_changed = Signal()
    tool_changed = Signal(str)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.sheet = Schematic()
        self.scale = 22.0                   # Bildpunkte je Rastereinheit
        self.offset = QPointF(400, 300)     # Bildschirmlage des Ursprungs
        self.tool = "auswahl"
        self.selection: set[tuple[str, int]] = set()
        self.colours = Colours()
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._mouse = QPointF()             # letzte Mauslage (Bildschirm)
        self._hover_grid = (0, 0)
        self._hover_net = None              # Netz unter der Maus (Name) für die Hervorhebung
        self._placing: Part | None = None   # Bauteil, das an der Maus hängt
        self._wire_start = None             # Rasterpunkt, an dem die Leitung gerade weitergeht
        self._wire_vertical_first = False
        self._wire_remembered = False       # für diese Leitung wurde schon ein Rückgängig-Schritt gemerkt
        self._drag = None                   # laufendes Ziehen: dict
        self._band = None                   # Auswahlrahmen (zwei Bildschirmpunkte)
        self._pan = None
        self._markers: list = []            # Prüfergebnisse zum Anzeigen [(Stufe, Rasterpunkt)]
        self._flash = None                  # Rasterpunkt, auf den gerade gesprungen wurde
        self._cache_version = -1
        self._version = 0
        self._nets = []
        self._junctions = []

    # --- Modell und Verlauf --------------------------------------------------------------------------------

    def load(self, data: dict | None) -> None:
        """Neuen Schaltplan anzeigen (Datei geöffnet, neuer Entwurf); der Verlauf beginnt neu."""
        self.sheet = Schematic.from_dict(data)
        self.selection.clear()
        self._undo.clear()
        self._redo.clear()
        self._placing = None
        self._wire_start = None
        self._touch()
        self.fit()

    def remember(self) -> None:
        """Stand vor einer Änderung merken (Rückgängig)."""
        self._undo.append(self.sheet.to_dict())
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    def _touch(self, emit: bool = False) -> None:
        self._version += 1
        self._markers = []          # Prüfergebnisse gelten nach einer Änderung nicht mehr (neu prüfen)
        if emit:
            self.changed.emit()
        self.update()

    def commit(self) -> None:
        """Ein Bearbeitungsschritt ist fertig: neu zeichnen und melden."""
        self.sheet.remove_zero_wires()
        self._touch(emit=True)
        self.selection_changed.emit()

    def undo(self) -> None:
        self._end_tool_action()
        if self._undo:
            self._redo.append(self.sheet.to_dict())
            self.sheet = Schematic.from_dict(self._undo.pop())
            self.selection.clear()
            self.commit()

    def redo(self) -> None:
        self._end_tool_action()
        if self._redo:
            self._undo.append(self.sheet.to_dict())
            self.sheet = Schematic.from_dict(self._redo.pop())
            self.selection.clear()
            self.commit()

    def nets(self):
        if self._cache_version != self._version:
            self._nets = self.sheet.nets()
            self._junctions = self.sheet.junctions()
            self._cache_version = self._version
        return self._nets

    def junctions(self):
        self.nets()
        return self._junctions

    # --- Koordinaten -----------------------------------------------------------------------------------------

    def to_screen(self, x: float, y: float) -> QPointF:
        return QPointF(self.offset.x() + x * self.scale, self.offset.y() + y * self.scale)

    def to_sheet(self, point: QPointF) -> tuple[float, float]:
        return ((point.x() - self.offset.x()) / self.scale, (point.y() - self.offset.y()) / self.scale)

    def snap(self, point: QPointF) -> tuple[int, int]:
        x, y = self.to_sheet(point)
        return int(round(x)), int(round(y))

    def fit(self) -> None:
        """Alles einpassen (F, Pos1); ein leeres Blatt zeigt den Ursprung in der Mitte."""
        boxes = [part.bounds() for part in self.sheet.parts]
        boxes += [(min(w.x1, w.x2), min(w.y1, w.y2), max(w.x1, w.x2), max(w.y1, w.y2)) for w in self.sheet.wires]
        boxes += [(l.x, l.y, l.x + 3, l.y) for l in self.sheet.labels]
        width, height = max(self.width(), 320), max(self.height(), 240)
        if not boxes:
            self.scale = 22.0
            self.offset = QPointF(width / 2, height / 2)
            self.update()
            return
        x1 = min(b[0] for b in boxes) - 4
        y1 = min(b[1] for b in boxes) - 4
        x2 = max(b[2] for b in boxes) + 4
        y2 = max(b[3] for b in boxes) + 4
        self.scale = max(4.0, min(60.0, min(width / (x2 - x1), height / (y2 - y1))))
        centre = ((x1 + x2) / 2, (y1 + y2) / 2)
        self.offset = QPointF(width / 2 - centre[0] * self.scale, height / 2 - centre[1] * self.scale)
        self.update()

    def centre_on(self, point) -> None:
        self.offset = QPointF(self.width() / 2 - point[0] * self.scale, self.height() / 2 - point[1] * self.scale)
        self._flash = tuple(point)
        self.update()

    # --- Werkzeuge -------------------------------------------------------------------------------------------

    def set_tool(self, tool: str) -> None:
        self._end_tool_action()
        self.tool = tool
        if tool != "bauteil":
            self._placing = None
        cursor = {"auswahl": Qt.CursorShape.ArrowCursor, "loeschen": Qt.CursorShape.ForbiddenCursor}.get(
            tool, Qt.CursorShape.CrossCursor)
        self.setCursor(cursor)
        self.tool_changed.emit(tool)
        self.status.emit(_TOOL_TIP.get(tool, ""))
        self.update()

    def start_placing(self, symbol: str) -> None:
        """Bauteil an die Maus hängen (Bibliothek)."""
        self.set_tool("bauteil")
        x, y = self._hover_grid
        definition = sy.LIBRARY[symbol]
        self._placing = Part(self.sheet.next_ref(definition.prefix), symbol, definition.value, x, y)
        self.status.emit(f"{symbol}: Klick setzt · R dreht · M spiegelt · Esc beendet")
        self.setFocus()
        self.update()

    def _end_tool_action(self) -> None:
        """Laufende Leitung bzw. Ziehen beenden (beim Werkzeugwechsel, Rückgängig)."""
        if self._wire_start is not None:
            self._finish_wire()
        self._drag = None
        self._band = None

    def escape(self) -> bool:
        """Esc: erst die laufende Aktion, dann das Werkzeug (zurück zu Auswählen), zuletzt die Auswahl."""
        if self._wire_start is not None:
            self._finish_wire()
            return True
        if self.tool != "auswahl":
            self.set_tool("auswahl")
            return True
        if self.selection:
            self.selection.clear()
            self.selection_changed.emit()
            self.update()
            return True
        return False

    # --- Treffer ---------------------------------------------------------------------------------------------

    def _tolerance(self) -> float:
        return max(0.3, 6.0 / self.scale)

    def item_at(self, point: QPointF):
        """Was unter dem Bildschirmpunkt liegt: ("label"|"wire"|"part", Nummer) oder None. Labels und Leitungen
        vor Bauteilen, weil sie schmal sind."""
        x, y = self.to_sheet(point)
        tol = self._tolerance()
        for number, label in enumerate(self.sheet.labels):
            if label.rotation < 0:
                continue
            box = self._label_box(label)
            if box.adjusted(-tol, -tol, tol, tol).contains(QPointF(x, y)):
                return ("label", number)
        best = None
        for number, wire in enumerate(self.sheet.wires):
            distance = _segment_distance((x, y), wire.ends[0], wire.ends[1])
            if distance <= tol and (best is None or distance < best[0]):
                best = (distance, number)
        if best is not None:
            return ("wire", best[1])
        for number in range(len(self.sheet.parts) - 1, -1, -1):
            x1, y1, x2, y2 = self.sheet.parts[number].bounds()
            if x1 - tol <= x <= x2 + tol and y1 - tol <= y <= y2 + tol:
                return ("part", number)
        return None

    def _label_box(self, label: Label) -> QRectF:
        """Umriss eines Labels in Rastereinheiten (für Treffer und Zeichnen)."""
        width = max(1.5, 0.62 * len(label.name) + 0.8)
        if label.rotation in (0, 180):
            x = label.x if label.rotation == 0 else label.x - width
            return QRectF(x, label.y - 1.0, width, 1.0)
        y = label.y - width if label.rotation == 90 else label.y
        return QRectF(label.x - 1.0, y, 1.0, width)

    def _pin_points(self) -> set:
        return {position for part in self.sheet.parts for _pin, position in part.pins()}

    def _connectable(self, point) -> bool:
        """Endet eine Leitung hier an etwas (Pin, Leitung)?"""
        if point in self._pin_points():
            return True
        return any(wire.contains(point) for wire in self.sheet.wires)

    # --- Maus ------------------------------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        self._mouse = event.position()
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            # mittlere/rechte Taste: Blatt verschieben; ein Rechtsklick ohne Ziehen wirkt beim Loslassen
            self._pan = (event.position(), QPointF(self.offset), False)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        grid = self.snap(event.position())
        if self.tool == "bauteil" and self._placing is not None:
            self.remember()
            part = copy.copy(self._placing)
            part.ref = self.sheet.next_ref(part.definition.prefix)
            part.x, part.y = grid
            self.sheet.parts.append(part)
            self.commit()
            self._placing = copy.copy(part)
            self._placing.ref = self.sheet.next_ref(part.definition.prefix)
            self.status.emit(f"{part.ref} gesetzt · weiter klicken setzt das nächste · Esc beendet")
            return
        if self.tool == "netz":
            self._wire_click(grid)
            return
        hit = self.item_at(event.position())
        if self.tool == "label":
            self._place_label(grid)
            return
        if self.tool == "loeschen":
            if hit is not None:
                self.remember()
                self._delete({hit})
                self.commit()
            return
        if self.tool in ("name", "wert"):
            self._edit_item(hit, grid, self.tool)
            return
        # Auswählen
        additive = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        if hit is None:
            if not additive:
                self.selection.clear()
                self.selection_changed.emit()
            self._band = (event.position(), event.position(), additive)
        else:
            if additive:
                self.selection ^= {hit}
            elif hit not in self.selection:
                self.selection = {hit}
            self.selection_changed.emit()
            if hit in self.selection:
                self._begin_drag(grid)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        self._mouse = event.position()
        grid = self.snap(event.position())
        if self._pan is not None:
            start, offset, _moved = self._pan
            delta = event.position() - start
            if delta.manhattanLength() > 3:
                self._pan = (start, offset, True)
                self.offset = offset + delta
                self.update()
            return
        moved = grid != self._hover_grid
        self._hover_grid = grid
        if self._drag is not None:
            if moved:
                self._drag_to(grid)
            return
        if self._band is not None:
            self._band = (self._band[0], event.position(), self._band[2])
            self.update()
            return
        if self._placing is not None:
            self._placing.x, self._placing.y = grid
        # Netz unter der Maus hervorheben
        hit = self.item_at(event.position())
        net = None
        if hit is not None and hit[0] == "wire":
            for found in self.nets():
                if hit[1] in found.wires:
                    net = found
                    break
            pins = ", ".join(f"{part.ref}.{pin.name}" for part, pin, _p in net.pins) if net else ""
            if net is not None:
                self.status.emit(f"Netz {net.name}" + (f" · {pins}" if pins else " · keine Anschlüsse"))
        elif hit is not None and hit[0] == "part":
            part = self.sheet.parts[hit[1]]
            self.status.emit(f"{part.ref} · {part.symbol} · {part.value}" if not part.definition.power
                             else f"Netzsymbol {part.value}")
        self._hover_net = net.name if net is not None else None
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._pan is not None:
            _start, _offset, moved = self._pan
            self._pan = None
            if not moved and event.button() == Qt.MouseButton.RightButton:
                self._right_click(event)
            return
        if self._drag is not None:
            drag, self._drag = self._drag, None
            if drag["moved"]:
                self.commit()
            return
        if self._band is not None:
            start, end, additive = self._band
            self._band = None
            if (end - start).manhattanLength() > 4:
                self._band_select(start, end, additive)
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        if self.tool == "netz":
            # Doppelklick beendet die Leitung; der erste Klick davor hat sie am selben Punkt schon beendet –
            # der zweite darf keine neue beginnen (bis Runde 98 hing danach eine neue Leitung an der Maus)
            if self._wire_start is not None:
                self._finish_wire()
            return
        if self.tool == "auswahl" and event.button() == Qt.MouseButton.LeftButton:
            hit = self.item_at(event.position())
            if hit is not None and hit[0] == "part":
                self._edit_item(hit, self.snap(event.position()), "wert")
            elif hit is not None:
                self._edit_item(hit, self.snap(event.position()), "name")
            return
        self.mousePressEvent(event)

    def wheelEvent(self, event) -> None:
        """Zoomen zur Maus: der Punkt unter dem Zeiger bleibt stehen."""
        factor = math.pow(1.0015, event.angleDelta().y())
        before = self.to_sheet(event.position())
        self.scale = max(3.0, min(120.0, self.scale * factor))
        self.offset = QPointF(event.position().x() - before[0] * self.scale,
                              event.position().y() - before[1] * self.scale)
        self.update()

    def _right_click(self, event) -> None:
        """Rechtsklick ohne Ziehen: laufende Aktion beenden; beim Auswählen ein Menü."""
        if self.tool == "netz":
            if self._wire_start is not None:
                self._finish_wire()
            else:
                self.set_tool("auswahl")
            return
        if self.tool == "bauteil":
            self.set_tool("auswahl")
            return
        if self.tool != "auswahl":
            self.set_tool("auswahl")
            return
        hit = self.item_at(event.position())
        if hit is not None and hit not in self.selection:
            self.selection = {hit}
            self.selection_changed.emit()
            self.update()
        menu = QMenu(self)
        if self.selection:
            menu.addAction(icon("sch_rotate"), "Drehen (R)", self.rotate_selection)
            menu.addAction(icon("mirror"), "Spiegeln (M)", self.mirror_selection)
            menu.addAction(icon("copy_bodies"), "Verdoppeln (Strg+D)", self.duplicate_selection)
            menu.addAction(icon("delete"), "Löschen (Entf)", self.delete_selection)
            if hit is not None and hit[0] == "part":
                menu.addSeparator()
                menu.addAction(icon("sch_value"), "Wert …", lambda: self._edit_item(hit, (0, 0), "wert"))
                menu.addAction(icon("sch_name"), "Name …", lambda: self._edit_item(hit, (0, 0), "name"))
            if hit is not None and hit[0] == "wire":
                menu.addSeparator()
                grid = self.snap(event.position())
                menu.addAction(icon("sch_name"), "Netz umbenennen …", lambda: self._edit_item(hit, grid, "name"))
        menu.addAction(icon("fit"), "Alles zeigen (F)", self.fit)
        menu.exec(event.globalPosition().toPoint())

    # --- Ziehen und Rahmen -----------------------------------------------------------------------------------

    def _begin_drag(self, grid) -> None:
        parts = [self.sheet.parts[n] for kind, n in self.selection if kind == "part"]
        wires = {n for kind, n in self.selection if kind == "wire"}
        # Gummiband: Enden fremder Leitungen an Pins der gewählten Bauteile bzw. an Enden gewählter Leitungen
        ends = [(n, e) for n, e in self.sheet.attached_wire_ends(parts) if n not in wires]
        moving_points = {p for n in wires for p in self.sheet.wires[n].ends}
        for number, wire in enumerate(self.sheet.wires):
            if number in wires:
                continue
            for end, point in enumerate(wire.ends):
                if point in moving_points and (number, end) not in ends:
                    ends.append((number, end))
        self._drag = {
            "start": grid, "moved": False, "snapshot": self.sheet.to_dict(),
            "parts": [(n, self.sheet.parts[n].x, self.sheet.parts[n].y) for kind, n in self.selection if kind == "part"],
            "wires": [(n, *self.sheet.wires[n].ends) for n in wires],
            "labels": [(n, self.sheet.labels[n].x, self.sheet.labels[n].y)
                       for kind, n in self.selection if kind == "label"],
            "ends": [(n, e, self.sheet.wires[n].ends[e]) for n, e in ends],
        }

    def _drag_to(self, grid) -> None:
        drag = self._drag
        dx, dy = grid[0] - drag["start"][0], grid[1] - drag["start"][1]
        if not drag["moved"]:
            if dx == 0 and dy == 0:
                return
            # erst beim ersten echten Schritt einen Rückgängig-Punkt setzen (ein Klick ändert nichts)
            self._undo.append(drag["snapshot"])
            del self._undo[:-UNDO_LIMIT]
            self._redo.clear()
            drag["moved"] = True
        for number, x, y in drag["parts"]:
            self.sheet.parts[number].x, self.sheet.parts[number].y = x + dx, y + dy
        for number, a, b in drag["wires"]:
            wire = self.sheet.wires[number]
            wire.x1, wire.y1, wire.x2, wire.y2 = a[0] + dx, a[1] + dy, b[0] + dx, b[1] + dy
        for number, x, y in drag["labels"]:
            self.sheet.labels[number].x, self.sheet.labels[number].y = x + dx, y + dy
        for number, end, point in drag["ends"]:
            self.sheet.move_wire_end(number, end, (point[0] + dx, point[1] + dy))
        self._touch()

    def _band_select(self, start: QPointF, end: QPointF, additive: bool) -> None:
        a, b = self.to_sheet(start), self.to_sheet(end)
        x1, x2 = sorted((a[0], b[0]))
        y1, y2 = sorted((a[1], b[1]))
        inside = lambda x, y: x1 <= x <= x2 and y1 <= y <= y2
        found = set()
        for number, part in enumerate(self.sheet.parts):
            bx1, by1, bx2, by2 = part.bounds()
            if inside(bx1, by1) and inside(bx2, by2):
                found.add(("part", number))
        for number, wire in enumerate(self.sheet.wires):
            if inside(*wire.ends[0]) and inside(*wire.ends[1]):
                found.add(("wire", number))
        for number, label in enumerate(self.sheet.labels):
            if label.rotation >= 0 and inside(label.x, label.y):
                found.add(("label", number))
        self.selection = (self.selection | found) if additive else found
        self.selection_changed.emit()

    # --- Leitungen -------------------------------------------------------------------------------------------

    def _wire_route(self, start, end) -> list:
        """Rechtwinkliger Weg von `start` nach `end`: Knickpunkt je nach Richtungswahl (Leertaste)."""
        if start[0] == end[0] or start[1] == end[1]:
            return [start, end]
        corner = (start[0], end[1]) if self._wire_vertical_first else (end[0], start[1])
        return [start, corner, end]

    def _wire_click(self, grid) -> None:
        if self._wire_start is None:
            self._wire_start = grid
            self._wire_remembered = False
            self.status.emit("Leitung: Klick setzt einen Knick · Klick auf Pin/Leitung schließt an · Leertaste: "
                             "Knick wechseln · Esc/Rechtsklick/Doppelklick beendet")
            self.update()
            return
        if grid == self._wire_start:
            self._finish_wire()
            return
        if not self._wire_remembered:
            self.remember()
            self._wire_remembered = True
        route = self._wire_route(self._wire_start, grid)
        # ein Ziel, an dem schon etwas hängt (vor dem Setzen geprüft), beendet die Leitung
        ends_here = self._connectable(grid)
        for a, b in zip(route, route[1:]):
            self.sheet.add_wire(a, b)
        self._wire_start = grid
        self.commit()
        if ends_here:
            self._finish_wire()

    def _finish_wire(self) -> None:
        self._wire_start = None
        self._wire_remembered = False
        self.status.emit(_TOOL_TIP["netz"])
        self.update()

    # --- Labels, Namen, Werte -------------------------------------------------------------------------------

    def _place_label(self, grid) -> None:
        net = self.sheet.net_at(grid)
        on_wire = any(wire.contains(grid) for wire in self.sheet.wires)
        if net is None or not on_wire:
            self.status.emit("Label: bitte auf eine Leitung klicken")
            return
        self.remember()
        self.sheet.labels.append(Label(grid[0], grid[1], net.name, 0))
        self.commit()
        self.status.emit(f"Label {net.name} gesetzt – Doppelklick darauf benennt das Netz um")

    def _edit_item(self, hit, grid, what: str) -> None:
        """Name/Wert ändern: Bauteil (Nummer bzw. Wert), Leitung oder Label (Netzname)."""
        if hit is None:
            return
        kind, number = hit
        if kind == "part":
            part = self.sheet.parts[number]
            if part.definition.power:
                what = "wert"           # Netzsymbol: der Wert ist der Netzname
            title = "Wert" if what == "wert" else "Name"
            current = part.value if what == "wert" else part.ref
            text, ok = QInputDialog.getText(self, title, f"{title} von {part.ref} ({part.symbol}):",
                                            QLineEdit.EchoMode.Normal, current)
            text = text.strip()
            if not ok or not text or text == current:
                return
            if what == "name" and any(p.ref == text for p in self.sheet.parts if p is not part):
                QMessageBox.information(self, "Name", f"{text} gibt es schon.")
                return
            self.remember()
            if what == "wert":
                part.value = text
            else:
                part.ref = text
            self.commit()
            return
        if kind == "label":
            label = self.sheet.labels[number]
            point = (label.x, label.y)
        else:
            wire = self.sheet.wires[number]
            point = grid if wire.contains(grid) else wire.ends[0]
        net = self.sheet.net_at(point)
        if net is None:
            return
        text, ok = QInputDialog.getText(self, "Netzname", f"Neuer Name für Netz {net.name}:",
                                        QLineEdit.EchoMode.Normal, net.name)
        text = text.strip()
        if not ok or not text or text == net.name:
            return
        self.remember()
        self.sheet.rename_net(point, text)
        self.commit()

    # --- Befehle auf der Auswahl ----------------------------------------------------------------------------

    def _delete(self, items: set) -> None:
        parts = sorted((n for kind, n in items if kind == "part"), reverse=True)
        wires = sorted((n for kind, n in items if kind == "wire"), reverse=True)
        labels = sorted((n for kind, n in items if kind == "label"), reverse=True)
        for number in parts:
            del self.sheet.parts[number]
        for number in wires:
            del self.sheet.wires[number]
        for number in labels:
            del self.sheet.labels[number]
        self.selection.clear()

    def delete_selection(self) -> None:
        if self.selection:
            self.remember()
            self._delete(set(self.selection))
            self.commit()

    def _transform_selection(self, action: str) -> None:
        """Drehen (90° im Uhrzeigersinn) bzw. Spiegeln um die Mitte der Auswahl; angeschlossene Leitungsenden
        gehen mit, soweit sie an gedrehten Pins hängen."""
        if self._placing is not None:
            if action == "drehen":
                self._placing.rotation = (self._placing.rotation + 90) % 360
            else:
                self._placing.mirror = not self._placing.mirror
            self.update()
            return
        if not self.selection:
            return
        self.remember()
        parts = [self.sheet.parts[n] for kind, n in self.selection if kind == "part"]
        wires = [self.sheet.wires[n] for kind, n in self.selection if kind == "wire"]
        labels = [self.sheet.labels[n] for kind, n in self.selection if kind == "label"]
        points = [(p.x, p.y) for p in parts] + [pt for w in wires for pt in w.ends] + [(l.x, l.y) for l in labels]
        cx = round(sum(p[0] for p in points) / len(points))
        cy = round(sum(p[1] for p in points) / len(points))

        def turn(point):
            x, y = point[0] - cx, point[1] - cy
            x, y = (-y, x) if action == "drehen" else (-x, y)
            return (x + cx, y + cy)

        before = {id(p): {pin.number: pos for pin, pos in p.pins()} for p in parts}
        attached = self.sheet.attached_wire_ends(parts)
        selected_wires = {id(w) for w in wires}
        for part in parts:
            part.x, part.y = turn((part.x, part.y))
            if action == "drehen":
                part.rotation = (part.rotation + 90) % 360
            else:
                part.mirror = not part.mirror
                part.rotation = (360 - part.rotation) % 360
        for wire in wires:
            (wire.x1, wire.y1), (wire.x2, wire.y2) = turn(wire.ends[0]), turn(wire.ends[1])
        for label in labels:
            label.x, label.y = turn((label.x, label.y))
        # Gummiband: Leitungsenden an Pins folgen dem Pin
        moved = {}
        for part in parts:
            after = {pin.number: pos for pin, pos in part.pins()}
            for number, pos in before[id(part)].items():
                moved[pos] = after[number]
        for number, end in attached:
            wire = self.sheet.wires[number]
            if id(wire) not in selected_wires and wire.ends[end] in moved:
                self.sheet.move_wire_end(number, end, moved[wire.ends[end]])
        self.commit()

    def rotate_selection(self) -> None:
        self._transform_selection("drehen")

    def mirror_selection(self) -> None:
        self._transform_selection("spiegeln")

    def duplicate_selection(self) -> None:
        """Gewähltes verdoppeln (um 4 Raster nach rechts unten versetzt, neue Bauteilnummern)."""
        if not self.selection:
            return
        self.remember()
        new = set()
        for kind, number in sorted(self.selection):
            if kind == "part":
                part = copy.copy(self.sheet.parts[number])
                part.ref = self.sheet.next_ref(part.definition.prefix)
                part.x += 4
                part.y += 4
                self.sheet.parts.append(part)
                new.add(("part", len(self.sheet.parts) - 1))
            elif kind == "wire":
                wire = self.sheet.wires[number]
                self.sheet.wires.append(Wire(wire.x1 + 4, wire.y1 + 4, wire.x2 + 4, wire.y2 + 4))
                new.add(("wire", len(self.sheet.wires) - 1))
            elif kind == "label":
                label = self.sheet.labels[number]
                self.sheet.labels.append(Label(label.x + 4, label.y + 4, label.name, label.rotation))
                new.add(("label", len(self.sheet.labels) - 1))
        self.selection = new
        self.commit()

    # --- Tasten ----------------------------------------------------------------------------------------------

    _KEYS = ("R", "M", "N", "A", "L", "F", "Home", "Space", "Del", "Backspace", "Esc", "Ctrl+Z", "Ctrl+Y",
             "Ctrl+Shift+Z", "Ctrl+D", "Ctrl+A")

    def event(self, event) -> bool:
        """Die Tasten des Blatts haben Vorrang vor den fensterweiten Kürzeln (Strg+Z des 3D-Entwurfs, Entf …)."""
        if event.type() == QEvent.Type.ShortcutOverride:
            name = QKeySequence(event.keyCombination()).toString()
            if name in self._KEYS:
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        name = QKeySequence(event.keyCombination()).toString()
        if name == "R":
            self.rotate_selection()
        elif name == "M":
            self.mirror_selection()
        elif name == "N":
            self.set_tool("netz")
        elif name == "A":
            self.parent_workspace().focus_search()
        elif name == "L":
            self.set_tool("label")
        elif name in ("F", "Home"):
            self.fit()
        elif name == "Space":
            self._wire_vertical_first = not self._wire_vertical_first
            self.update()
        elif name in ("Del", "Backspace"):
            self.delete_selection()
        elif name == "Esc":
            self.escape()
        elif name == "Ctrl+Z":
            self.undo()
        elif name in ("Ctrl+Y", "Ctrl+Shift+Z"):
            self.redo()
        elif name == "Ctrl+D":
            self.duplicate_selection()
        elif name == "Ctrl+A":
            self.selection = ({("part", n) for n in range(len(self.sheet.parts))}
                              | {("wire", n) for n in range(len(self.sheet.wires))}
                              | {("label", n) for n, l in enumerate(self.sheet.labels) if l.rotation >= 0})
            self.selection_changed.emit()
            self.update()
        else:
            super().keyPressEvent(event)

    def parent_workspace(self) -> "SchematicWorkspace":
        widget = self.parent()
        while widget is not None and not isinstance(widget, SchematicWorkspace):
            widget = widget.parent()
        return widget

    # --- Zeichnen --------------------------------------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.colours.background)
        self._paint_grid(painter)
        self.render_sheet(painter, self.colours, interactive=True)
        painter.end()

    def _paint_grid(self, painter: QPainter) -> None:
        """Rasterpunkte (jede Einheit, bei kleinem Zoom jede fünfte) und kräftigere alle 10 Einheiten."""
        step = 1 if self.scale >= 9 else 5 if self.scale >= 3 else 10
        x1, y1 = self.to_sheet(QPointF(0, 0))
        x2, y2 = self.to_sheet(QPointF(self.width(), self.height()))
        size = max(1.2, min(2.4, self.scale * 0.08))
        for major in (False, True):
            painter.setPen(QPen(self.colours.grid_major if major else self.colours.grid, size * (1.6 if major else 1),
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            every = 10 if major else step
            points = []
            for gx in range(int(math.floor(x1 / every)) * every, int(x2) + every, every):
                for gy in range(int(math.floor(y1 / every)) * every, int(y2) + every, every):
                    if major or (gx % 10 or gy % 10):
                        points.append(self.to_screen(gx, gy))
            if len(points) < 40000:
                painter.drawPoints(points)

    def render_sheet(self, painter: QPainter, colours: Colours, interactive: bool = False) -> None:
        """Zeichnet Leitungen, Bauteile, Labels und Verbindungspunkte – auf dem Bildschirm (mit Auswahl, Vorschau
        und Hervorhebung) und für die Ausgabe (PDF/SVG/Bild) mit `interactive=False`."""
        unit = self.scale
        nets = self.nets()
        highlight = set()
        if interactive and self._hover_net is not None:
            for net in nets:
                if net.name == self._hover_net:
                    highlight = set(net.wires)
        selected = self.selection if interactive else set()
        wire_pen = QPen(colours.wire, max(1.5, unit * 0.11), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        for number, wire in enumerate(self.sheet.wires):
            colour = colours.select if ("wire", number) in selected else \
                colours.highlight if number in highlight else colours.wire
            wire_pen.setColor(colour)
            wire_pen.setWidthF(max(1.5, unit * (0.16 if number in highlight else 0.11)))
            painter.setPen(wire_pen)
            painter.drawLine(self.to_screen(*wire.ends[0]), self.to_screen(*wire.ends[1]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colours.wire)
        for point in self.junctions():
            painter.drawEllipse(self.to_screen(*point), unit * 0.28, unit * 0.28)
        connected = {(part.ref, pin.number) for net in nets for part, pin, _p in net.pins}
        connected |= {(part.ref, pin.number) for net in nets for part, pin in net.sources}
        for number, part in enumerate(self.sheet.parts):
            colour = colours.select if ("part", number) in selected else colours.symbol
            self._paint_part(painter, part, colours, colour, connected if interactive else None)
        if interactive and self._placing is not None:
            ghost = QColor(colours.select)
            ghost.setAlphaF(0.75)
            self._paint_part(painter, self._placing, colours, ghost, None)
        for number, label in enumerate(self.sheet.labels):
            if label.rotation < 0:
                if interactive and self.tool == "name":
                    painter.setPen(QPen(colours.muted, 1, Qt.PenStyle.DashLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(self.to_screen(label.x, label.y), unit * 0.4, unit * 0.4)
                continue
            colour = colours.select if ("label", number) in selected else colours.label
            self._paint_label(painter, label, colour)
        if not interactive:
            return
        # Vorschau der Leitung
        if self.tool == "netz" and self._wire_start is not None:
            route = self._wire_route(self._wire_start, self._hover_grid)
            pen = QPen(colours.wire, max(1.5, unit * 0.11), Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            for a, b in zip(route, route[1:]):
                painter.drawLine(self.to_screen(*a), self.to_screen(*b))
        if self.tool in ("netz", "label", "bauteil"):
            # Fadenkreuz am Rasterpunkt unter der Maus
            centre = self.to_screen(*self._hover_grid)
            painter.setPen(QPen(colours.highlight if self._connectable(self._hover_grid) else colours.muted, 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, unit * 0.35, unit * 0.35)
        if self._band is not None:
            start, end, _additive = self._band
            band = QColor(colours.select)
            band.setAlphaF(0.15)
            painter.setBrush(band)
            painter.setPen(QPen(colours.select, 1, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(start, end).normalized())
        for level, point in self._markers:
            painter.setPen(QPen(colours.error if level == "Fehler" else colours.warning, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.to_screen(*point), unit * 0.7, unit * 0.7)
        if self._flash is not None:
            painter.setPen(QPen(colours.highlight, 3.0))
            painter.drawEllipse(self.to_screen(*self._flash), unit * 1.3, unit * 1.3)

    def _paint_part(self, painter: QPainter, part: Part, colours: Colours, colour: QColor, connected) -> None:
        unit = self.scale
        definition = part.definition
        pen = QPen(colour, max(1.2, unit * 0.09), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        at = lambda x, y: self.to_screen(*part.place(x, y))
        for item in definition.graphics:
            kind = item[0]
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if kind == "line":
                painter.drawLine(at(item[1], item[2]), at(item[3], item[4]))
            elif kind == "rect":
                corners = [at(item[1], item[2]), at(item[3], item[2]), at(item[3], item[4]), at(item[1], item[4])]
                if item[5]:
                    painter.setBrush(colour)
                painter.drawPolygon(QPolygonF(corners))
            elif kind == "circle":
                if item[4]:
                    painter.setBrush(colour)
                painter.drawEllipse(at(item[1], item[2]), item[3] * unit, item[3] * unit)
            elif kind == "arc":
                # Bogen als Polylinie (Drehen/Spiegeln wirkt so auf jeden Punkt)
                cx, cy, r, start, span = item[1:6]
                steps = max(6, int(abs(span) / 10))
                points = [at(cx + r * math.cos(math.radians(start + span * k / steps)),
                             cy - r * math.sin(math.radians(start + span * k / steps))) for k in range(steps + 1)]
                painter.drawPolyline(QPolygonF(points))
            elif kind == "poly":
                if item[2]:
                    painter.setBrush(colour)
                painter.drawPolygon(QPolygonF([at(x, y) for x, y in item[1]]))
            elif kind == "text":
                self._text(painter, at(item[1], item[2]), item[3], item[4] * unit, colour, Qt.AlignmentFlag.AlignCenter)
        # Pin-Namen (ICs, Operationsverstärker) und offene Pins
        for pin, position in part.pins():
            if pin.show_name:
                inward = _inward(pin)
                name_point = part.place(pin.x + inward[0] * 1.35, pin.y + inward[1] * 1.35)
                align = _align_for(part, inward)
                self._text(painter, self.to_screen(*name_point), pin.name, unit * 0.62, colours.text, align)
                number_point = part.place(pin.x + inward[0] * 0.5, pin.y + inward[1] * 0.5 - 0.35)
                self._text(painter, self.to_screen(*number_point), pin.number, unit * 0.45, colours.muted,
                           Qt.AlignmentFlag.AlignCenter)
            if connected is not None and (part.ref, pin.number) not in connected:
                painter.setPen(QPen(colours.open_pin, max(1.0, unit * 0.06)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(self.to_screen(*position), unit * 0.2, unit * 0.2)
        # Bauteilnummer und Wert (immer waagerecht lesbar)
        size = unit * 0.75
        if definition.power:
            self._text(painter, self.to_screen(*part.place(*definition.value_at)), part.value, size, colour,
                       Qt.AlignmentFlag.AlignCenter)
            return
        name_point = self.to_screen(*part.place(*definition.name_at))
        value_point = self.to_screen(*part.place(*definition.value_at))
        self._text(painter, name_point, part.ref, size, colours.text, Qt.AlignmentFlag.AlignCenter)
        if part.value:
            self._text(painter, value_point, part.value, size, colours.muted, Qt.AlignmentFlag.AlignCenter)

    def _paint_label(self, painter: QPainter, label: Label, colour: QColor) -> None:
        """Netzlabel als Fähnchen: Spitze am Ansatzpunkt, Name darin."""
        box = self._label_box(label)
        corners = [self.to_screen(box.left(), box.top()), self.to_screen(box.right(), box.top()),
                   self.to_screen(box.right(), box.bottom()), self.to_screen(box.left(), box.bottom())]
        painter.setPen(QPen(colour, max(1.0, self.scale * 0.07)))
        fill = QColor(colour)
        fill.setAlphaF(0.12)
        painter.setBrush(fill)
        painter.drawPolygon(QPolygonF(corners))
        centre = self.to_screen(box.center().x(), box.center().y())
        self._text(painter, centre, label.name, self.scale * 0.68, colour, Qt.AlignmentFlag.AlignCenter,
                   vertical=label.rotation in (90, 270))

    def _text(self, painter: QPainter, point: QPointF, text: str, size: float, colour: QColor, align,
              vertical: bool = False) -> None:
        if size < 4 or not text:
            return
        font = QFont(self.font())
        font.setPixelSize(max(1, int(size)))
        painter.setFont(font)
        painter.setPen(colour)
        metrics = QFontMetricsF(font)
        width, height = metrics.horizontalAdvance(text), metrics.height()
        painter.save()
        painter.translate(point)
        if vertical:
            painter.rotate(-90)
        if align & Qt.AlignmentFlag.AlignLeft:
            x = 0.0
        elif align & Qt.AlignmentFlag.AlignRight:
            x = -width
        else:
            x = -width / 2
        painter.drawText(QRectF(x, -height / 2, width + 2, height), Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    # --- Ausgabe als Bild/PDF/SVG ----------------------------------------------------------------------------

    def render_to(self, painter: QPainter, target: QRectF) -> None:
        """Den ganzen Schaltplan schwarz auf weiß in `target` zeichnen (Seitenränder 5 %)."""
        saved = (self.scale, QPointF(self.offset), self.selection, self._hover_net)
        boxes = [part.bounds() for part in self.sheet.parts]
        boxes += [(min(w.x1, w.x2), min(w.y1, w.y2), max(w.x1, w.x2), max(w.y1, w.y2)) for w in self.sheet.wires]
        boxes += [(l.x - 3, l.y - 1, l.x + 4, l.y + 1) for l in self.sheet.labels]
        if not boxes:
            boxes = [(0, 0, 10, 10)]
        x1 = min(b[0] for b in boxes) - 2
        y1 = min(b[1] for b in boxes) - 3
        x2 = max(b[2] for b in boxes) + 2
        y2 = max(b[3] for b in boxes) + 2
        margin = 0.05
        width, height = target.width() * (1 - 2 * margin), target.height() * (1 - 2 * margin)
        self.scale = min(width / (x2 - x1), height / (y2 - y1))
        self.offset = QPointF(target.left() + target.width() * margin - x1 * self.scale + (width - (x2 - x1) * self.scale) / 2,
                              target.top() + target.height() * margin - y1 * self.scale + (height - (y2 - y1) * self.scale) / 2)
        self.selection, self._hover_net = set(), None
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.fillRect(target, QColor("white"))
            self.render_sheet(painter, Colours(printing=True), interactive=False)
            if self.sheet.title:
                self._text(painter, QPointF(target.left() + target.width() * margin, target.top() + target.height() * 0.03),
                           self.sheet.title, target.height() * 0.022, QColor("black"), Qt.AlignmentFlag.AlignLeft)
        finally:
            self.scale, self.offset, self.selection, self._hover_net = saved


def _segment_distance(point, a, b) -> float:
    px, py = point
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _inward(pin: sy.Pin) -> tuple[float, float]:
    """Richtung vom Pin-Ende in den Körper (für Pin-Namen): zur Mitte des Symbols, auf die Hauptachse gelegt."""
    if abs(pin.x) >= abs(pin.y):
        return (-1.0 if pin.x > 0 else 1.0, 0.0)
    return (0.0, -1.0 if pin.y > 0 else 1.0)


def _align_for(part: Part, inward) -> Qt.AlignmentFlag:
    """Ausrichtung des Pin-Namens: zum Pin hin bündig (nach Drehen/Spiegeln auf dem Bildschirm)."""
    x0, y0 = part.place(0, 0)
    x1, y1 = part.place(inward[0], inward[1])
    dx, dy = x1 - x0, y1 - y0
    if abs(dx) >= abs(dy):
        return Qt.AlignmentFlag.AlignLeft if dx > 0 else Qt.AlignmentFlag.AlignRight
    return Qt.AlignmentFlag.AlignCenter


def symbol_pixmap(symbol: sy.Symbol, size: int = 48, colours: Colours | None = None) -> QPixmap:
    """Kleines Vorschaubild eines Symbols für die Bibliothek."""
    colours = colours or Colours()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    canvas = SchematicCanvas()
    x1, y1, x2, y2 = symbol.bounds()
    span = max(x2 - x1, y2 - y1, 1.0)
    canvas.scale = size * 0.82 / span
    canvas.offset = QPointF(size / 2 - (x1 + x2) / 2 * canvas.scale, size / 2 - (y1 + y2) / 2 * canvas.scale)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # ohne Nummer und Wert (leer): nur das Zeichen
    canvas._paint_part(painter, Part("", symbol.name, ""), colours, colours.symbol, None)
    painter.end()
    canvas.deleteLater()
    return pixmap


class SchematicWorkspace(QWidget):
    """Der Reiter „Schaltplan“ (siehe Moduldoku). `changed` meldet jede Änderung an das Hauptfenster, das den
    Schaltplan im Entwurf speichert."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.canvas = SchematicCanvas(self)
        self.toolbar = QToolBar()
        self.toolbar.setIconSize(QSize(26, 26))
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.tool_buttons: dict[str, QToolButton] = {}
        for tool in TOOLS:
            action = self.toolbar.addAction(icon(_TOOL_ICON[tool]), _TOOL_TEXT[tool])
            action.setCheckable(True)
            key = _TOOL_KEY.get(tool)
            action.setToolTip(_TOOL_TIP[tool] + (f" ({key})" if key else ""))
            action.triggered.connect(lambda _checked=False, chosen=tool: self._tool_clicked(chosen))
            self.tool_buttons[tool] = action
        self.toolbar.addSeparator()
        for text, name, slot, tip in (
                ("Drehen", "sch_rotate", self.canvas.rotate_selection, "Gewähltes um 90° drehen (R)"),
                ("Spiegeln", "mirror", self.canvas.mirror_selection, "Gewähltes spiegeln (M)"),
                ("Rückgängig", "undo", self.canvas.undo, "Rückgängig (Strg+Z)"),
                ("Wiederholen", "redo", self.canvas.redo, "Wiederholen (Strg+Y)")):
            action = self.toolbar.addAction(icon(name), text, slot)
            action.setToolTip(tip)
        self.toolbar.addSeparator()
        erc = self.toolbar.addAction(icon("sch_erc"), "Prüfen", self.run_check)
        erc.setToolTip("Elektrische Prüfung (ERC): offene Pins, Ausgänge gegeneinander, fehlende Versorgung, "
                       "lose Leitungsenden, doppelte Namen")
        annotate = self.toolbar.addAction(icon("sch_annotate"), "Nummerieren", self.annotate)
        annotate.setToolTip("Bauteilnummern der Reihe nach neu vergeben (von oben links nach unten rechts)")
        export = QToolButton()
        export.setIcon(icon("export"))
        export.setText("Ausgabe")
        export.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        export.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(export)
        menu.addAction("Netzliste (.txt) …", lambda: self.export("netzliste"))
        menu.addAction("Stückliste (.csv) …", lambda: self.export("stueckliste"))
        menu.addAction("SPICE-Netzliste (.cir) …", lambda: self.export("spice"))
        menu.addSeparator()
        menu.addAction("Schaltplan als PDF …", lambda: self.export("pdf"))
        menu.addAction("Schaltplan als SVG …", lambda: self.export("svg"))
        menu.addAction("Schaltplan als Bild (.png) …", lambda: self.export("png"))
        export.setMenu(menu)
        export.setToolTip("Netzliste, Stückliste, SPICE oder den Schaltplan als PDF/SVG/Bild speichern")
        self.toolbar.addWidget(export)
        fit = self.toolbar.addAction(icon("fit"), "Alles", self.canvas.fit)
        fit.setToolTip("Alles zeigen (F)")
        layout.addWidget(self.toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_library())
        middle = QSplitter(Qt.Orientation.Vertical)
        middle.addWidget(self.canvas)
        self.problems = QListWidget()
        self.problems.setVisible(False)
        self.problems.itemActivated.connect(self._jump_to_problem)
        self.problems.itemClicked.connect(self._jump_to_problem)
        middle.addWidget(self.problems)
        middle.setStretchFactor(0, 5)
        middle.setStretchFactor(1, 1)
        splitter.addWidget(middle)
        splitter.addWidget(self._build_inspector())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 900, 240])
        layout.addWidget(splitter, 1)
        self.status = QLabel()
        self.status.setObjectName("hint")
        self.status.setContentsMargins(8, 3, 8, 3)
        layout.addWidget(self.status)

        self.canvas.status.connect(self.status.setText)
        self.canvas.tool_changed.connect(self._show_tool)
        self.canvas.selection_changed.connect(self._show_selection)
        self.canvas.changed.connect(self.changed)
        self.canvas.changed.connect(self._show_selection)
        self._show_tool("auswahl")
        self._show_selection()

    # --- Seitenleisten ---------------------------------------------------------------------------------------

    def _build_library(self) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(6, 6, 6, 6)
        title = QLabel("Bibliothek")
        title.setObjectName("sectionTitle")
        column.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (z. B. LED, npn, 7805) …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill_library)
        self.search.returnPressed.connect(self._place_first_found)
        column.addWidget(self.search)
        self.library = QTreeWidget()
        self.library.setHeaderHidden(True)
        self.library.setIconSize(QSize(36, 36))
        self.library.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.library.itemDoubleClicked.connect(lambda item, _column: self._place_item(item))
        self.library.itemActivated.connect(lambda item, _column: self._place_item(item))
        column.addWidget(self.library, 1)
        place = QPushButton(icon("sch_part"), "Setzen")
        place.setToolTip("Gewähltes Bauteil an die Maus hängen (oder Doppelklick in der Liste)")
        place.clicked.connect(lambda: self._place_item(self.library.currentItem()))
        column.addWidget(place)
        self._fill_library("")
        return box

    def _fill_library(self, text: str) -> None:
        self.library.clear()
        found = sy.search(text) if text.strip() else list(sy.LIBRARY.values())
        groups: dict[str, QTreeWidgetItem] = {}
        colours = Colours()
        for symbol in found:
            group = groups.get(symbol.category)
            if group is None:
                group = QTreeWidgetItem([symbol.category])
                group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.library.addTopLevelItem(group)
                groups[symbol.category] = group
            item = QTreeWidgetItem([symbol.name])
            item.setData(0, Qt.ItemDataRole.UserRole, symbol.name)
            item.setToolTip(0, symbol.description + (f" · Vorgabe {symbol.value}" if symbol.value else ""))
            item.setIcon(0, symbol_pixmap(symbol, 40, colours))
            group.addChild(item)
        self.library.expandAll()
        first = next((groups[g].child(0) for g in groups if groups[g].childCount()), None)
        if first is not None and text.strip():
            self.library.setCurrentItem(first)

    def _place_item(self, item) -> None:
        if item is None:
            return
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if name:
            self.canvas.start_placing(name)

    def _place_first_found(self) -> None:
        item = self.library.currentItem()
        self._place_item(item)

    def focus_search(self) -> None:
        """Taste A (wie „Add“): in die Suche der Bibliothek springen."""
        self.search.setFocus()
        self.search.selectAll()

    def _build_inspector(self) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(6, 6, 6, 6)
        title = QLabel("Eigenschaften")
        title.setObjectName("sectionTitle")
        column.addWidget(title)
        form = QFormLayout()
        self.inspect_kind = QLabel("–")
        self.inspect_kind.setWordWrap(True)
        form.addRow("Auswahl", self.inspect_kind)
        self.inspect_name = QLineEdit()
        self.inspect_name.editingFinished.connect(lambda: self._inspector_edit("name"))
        form.addRow("Name", self.inspect_name)
        self.inspect_value = QLineEdit()
        self.inspect_value.editingFinished.connect(lambda: self._inspector_edit("wert"))
        form.addRow("Wert", self.inspect_value)
        self.inspect_title = QLineEdit()
        self.inspect_title.setPlaceholderText("Titel des Schaltplans")
        self.inspect_title.editingFinished.connect(self._title_edit)
        column.addLayout(form)
        row = QHBoxLayout()
        for text, name, slot in (("Drehen", "sch_rotate", self.canvas.rotate_selection),
                                 ("Spiegeln", "mirror", self.canvas.mirror_selection),
                                 ("Löschen", "delete", self.canvas.delete_selection)):
            button = QToolButton()
            button.setIcon(icon(name))
            button.setToolTip(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        column.addLayout(row)
        self.inspect_info = QLabel()
        self.inspect_info.setWordWrap(True)
        self.inspect_info.setObjectName("hint")
        column.addWidget(self.inspect_info)
        column.addStretch(1)
        sheet_title = QLabel("Blatt")
        sheet_title.setObjectName("sectionTitle")
        column.addWidget(sheet_title)
        column.addWidget(self.inspect_title)
        self.sheet_info = QLabel()
        self.sheet_info.setWordWrap(True)
        self.sheet_info.setObjectName("hint")
        column.addWidget(self.sheet_info)
        return box

    def _selected_single(self):
        selection = self.canvas.selection
        return next(iter(selection)) if len(selection) == 1 else None

    def _show_selection(self) -> None:
        canvas = self.canvas
        single = self._selected_single()
        name_edit, value_edit = self.inspect_name, self.inspect_value
        for widget in (name_edit, value_edit):
            widget.blockSignals(True)
        name_edit.setEnabled(False)
        value_edit.setEnabled(False)
        name_edit.clear()
        value_edit.clear()
        info = ""
        if single is None:
            self.inspect_kind.setText(f"{len(canvas.selection)} Elemente" if canvas.selection else "–")
        elif single[0] == "part":
            part = canvas.sheet.parts[single[1]]
            self.inspect_kind.setText(part.symbol)
            name_edit.setText(part.ref)
            value_edit.setText(part.value)
            name_edit.setEnabled(not part.definition.power)
            value_edit.setEnabled(True)
            pins = ", ".join(f"{pin.number} {pin.name}" if pin.name != pin.number else pin.number
                             for pin in part.definition.pins)
            info = f"{part.definition.description}\nPins: {pins}\nDrehung {part.rotation}°" + \
                (" · gespiegelt" if part.mirror else "")
        else:
            if single[0] == "wire":
                wire = canvas.sheet.wires[single[1]]
                point = wire.ends[0]
                length = math.hypot(wire.x2 - wire.x1, wire.y2 - wire.y1) * _UNIT_MM
                info = f"Länge {length:.2f} mm".replace(".", ",")
            else:
                label = canvas.sheet.labels[single[1]]
                point = (label.x, label.y)
            net = canvas.sheet.net_at(point)
            self.inspect_kind.setText("Leitung" if single[0] == "wire" else "Netzlabel")
            if net is not None:
                name_edit.setText(net.name)
                name_edit.setEnabled(True)
                pins = ", ".join(f"{part.ref}.{pin.name}" for part, pin, _p in net.pins)
                info = (info + "\n" if info else "") + f"Netz {net.name}: {pins or 'keine Anschlüsse'}"
        self.inspect_info.setText(info)
        for widget in (name_edit, value_edit):
            widget.blockSignals(False)
        sheet = canvas.sheet
        if not self.inspect_title.hasFocus():
            self.inspect_title.setText(sheet.title)
        parts = [p for p in sheet.parts if not p.definition.power]
        self.sheet_info.setText(f"{len(parts)} Bauteile · {len(canvas.nets())} Netze · {len(sheet.wires)} Leitungen")

    def _inspector_edit(self, what: str) -> None:
        single = self._selected_single()
        if single is None:
            return
        canvas = self.canvas
        text = (self.inspect_name if what == "name" else self.inspect_value).text().strip()
        if not text:
            return
        if single[0] == "part":
            part = canvas.sheet.parts[single[1]]
            if what == "name":
                if text == part.ref:
                    return
                if any(p.ref == text for p in canvas.sheet.parts if p is not part):
                    self.status.setText(f"{text} gibt es schon")
                    self._show_selection()
                    return
                canvas.remember()
                part.ref = text
            else:
                if text == part.value:
                    return
                canvas.remember()
                part.value = text
            canvas.commit()
            return
        if what == "name":
            point = canvas.sheet.wires[single[1]].ends[0] if single[0] == "wire" else \
                (canvas.sheet.labels[single[1]].x, canvas.sheet.labels[single[1]].y)
            net = canvas.sheet.net_at(point)
            if net is None or net.name == text:
                return
            canvas.remember()
            canvas.sheet.rename_net(point, text)
            canvas.commit()

    def _title_edit(self) -> None:
        text = self.inspect_title.text().strip()
        if text != self.canvas.sheet.title:
            self.canvas.remember()
            self.canvas.sheet.title = text
            self.canvas.commit()

    # --- Werkzeuge -------------------------------------------------------------------------------------------

    def _tool_clicked(self, tool: str) -> None:
        if tool == "bauteil":
            item = self.library.currentItem()
            if item is not None and item.data(0, Qt.ItemDataRole.UserRole):
                self._place_item(item)
            else:
                self.canvas.set_tool("bauteil")
                self.focus_search()
                self.status.setText("Bauteil in der Bibliothek wählen (Doppelklick) oder suchen")
            return
        self.canvas.set_tool(tool)
        self.canvas.setFocus()

    def _show_tool(self, tool: str) -> None:
        for name, action in self.tool_buttons.items():
            action.setChecked(name == tool)

    # --- Prüfen, Nummerieren, Ausgabe -----------------------------------------------------------------------

    def run_check(self) -> list:
        problems = self.canvas.sheet.check()
        self.problems.clear()
        if not problems:
            item = QListWidgetItem(icon("sch_erc"), "Keine Fehler oder Warnungen gefunden.")
            self.problems.addItem(item)
        for problem in problems:
            item = QListWidgetItem(f"{problem.level}: {problem.message}")
            item.setData(Qt.ItemDataRole.UserRole, problem.at)
            item.setForeground(self.canvas.colours.error if problem.level == "Fehler" else self.canvas.colours.warning)
            self.problems.addItem(item)
        self.problems.setVisible(True)
        self.canvas._markers = [(p.level, p.at) for p in problems]
        self.canvas.update()
        errors = sum(1 for p in problems if p.level == "Fehler")
        self.status.setText(f"Prüfung: {errors} Fehler, {len(problems) - errors} Warnungen")
        return problems

    def _jump_to_problem(self, item) -> None:
        point = item.data(Qt.ItemDataRole.UserRole)
        if point:
            self.canvas.centre_on(point)

    def annotate(self) -> None:
        self.canvas.remember()
        renames = self.canvas.sheet.annotate()
        self.canvas.commit()
        self.status.setText(f"{len(renames)} Bauteile neu nummeriert" if renames else "Nummern waren schon in Ordnung")

    def export(self, kind: str, path: str | None = None) -> str | None:
        """Ausgabe speichern; ohne `path` fragt ein Dateidialog. Rückgabe: der Pfad (None: abgebrochen)."""
        filters = {"netzliste": ("Netzliste (*.txt)", ".txt"), "stueckliste": ("Stückliste (*.csv)", ".csv"),
                   "spice": ("SPICE-Netzliste (*.cir)", ".cir"), "pdf": ("PDF (*.pdf)", ".pdf"),
                   "svg": ("SVG (*.svg)", ".svg"), "png": ("PNG-Bild (*.png)", ".png")}
        pattern, suffix = filters[kind]
        if path is None:
            base = (self.canvas.sheet.title or "Schaltplan").replace("/", "_")
            path, _chosen = QFileDialog.getSaveFileName(self, "Ausgabe speichern", base + suffix, pattern)
            if not path:
                return None
        if not path.lower().endswith(suffix):
            path += suffix
        sheet = self.canvas.sheet
        if kind == "netzliste":
            Path(path).write_text(sheet.netlist_text(), encoding="utf-8")
        elif kind == "stueckliste":
            Path(path).write_text(sheet.bom_csv(), encoding="utf-8-sig")       # mit BOM: Excel erkennt UTF-8
        elif kind == "spice":
            Path(path).write_text(sheet.spice(), encoding="utf-8")
        elif kind == "pdf":
            writer = QPdfWriter(path)
            writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            writer.setPageOrientation(QPageLayout.Orientation.Landscape)
            writer.setResolution(300)
            writer.setTitle(sheet.title or "Schaltplan")
            writer.setCreator("WolfCAD")
            painter = QPainter(writer)
            self.canvas.render_to(painter, QRectF(0, 0, writer.width(), writer.height()))
            painter.end()
        elif kind == "svg":
            from PySide6.QtSvg import QSvgGenerator
            generator = QSvgGenerator()
            generator.setFileName(path)
            generator.setSize(QSize(1600, 1131))
            generator.setViewBox(QRectF(0, 0, 1600, 1131))
            generator.setTitle(sheet.title or "Schaltplan")
            generator.setDescription("Erzeugt mit WolfCAD")
            painter = QPainter(generator)
            self.canvas.render_to(painter, QRectF(0, 0, 1600, 1131))
            painter.end()
        else:
            image = QImage(3200, 2262, QImage.Format.Format_ARGB32)
            painter = QPainter(image)
            self.canvas.render_to(painter, QRectF(0, 0, image.width(), image.height()))
            painter.end()
            image.save(path)
        self.status.setText(f"Gespeichert: {path}")
        return path

    # --- Hauptfenster ---------------------------------------------------------------------------------------

    def load(self, data: dict | None) -> None:
        self.canvas.load(data)
        self.problems.setVisible(False)
        self._show_selection()

    def data(self) -> dict:
        return self.canvas.sheet.to_dict()

    def escape(self) -> bool:
        return self.canvas.escape()

    def undo(self) -> None:
        self.canvas.undo()

    def redo(self) -> None:
        self.canvas.redo()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            self.canvas.fit()
