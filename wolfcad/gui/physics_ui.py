# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Reiter PHYSIK (Runde 83): Lager und Lasten an Flächen setzen, Festigkeit, Luft- und Wasserströmung, Schwimmen.

Die Rechnungen stehen in core/physics.py. Hier: Werkzeuge der Leiste, Eingabe der Lasten (an vorher gewählten
Flächen), Rechnen im Hintergrund mit Fortschritt und Abbrechen, Anzeige des Ergebnisses in der 3D-Ansicht und eine
Legende oben links. Lager und Lasten liegen im Entwurf (view_state["physik"]) und werden mitgespeichert.

Anzeige: das Ergebnis ersetzt den berechneten Körper – farbige Dreiecke nach Spannung bzw. Druckbeiwert (eigene
Ebenen „physik#…“), Stromlinien, bei Schwimmen ein halbdurchsichtiger Wasserspiegel. „Ergebnis aus“ (oder eine
Änderung am Modell) zeigt den Körper wieder normal.
"""
from __future__ import annotations

import colorsys
import math
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QInputDialog,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressDialog)

from ..core import features as ft
from ..core import physics

PHYSICS_TAB = "PHYSIK"
PANELS = ("LASTEN", "BERECHNEN", "ERGEBNIS")
# Werkzeug -> (Bereich, als Symbol sichtbar, Symbol, Tooltip)
TOOLS = {
    "Lager": ("LASTEN", True, "phys_support", "Gewählte Flächen fest lagern (eingespannt)"),
    "Kraft": ("LASTEN", True, "phys_force", "Kraft in N auf die gewählten Flächen"),
    "Druck": ("LASTEN", True, "phys_pressure", "Druck in MPa senkrecht auf die gewählten Flächen"),
    "Lasten entfernen": ("LASTEN", False, "phys_clear", "Alle Lager und Lasten des Entwurfs entfernen"),
    "Festigkeit": ("BERECHNEN", True, "phys_stress", "Spannung, Verformung und Sicherheit unter den Lasten"),
    "Luftströmung": ("BERECHNEN", True, "phys_air", "Umströmung durch Luft: Widerstand, Druck, Stromlinien"),
    "Wasserströmung": ("BERECHNEN", True, "phys_water", "Umströmung durch Wasser: Widerstand, Druck, Stromlinien"),
    "Schwimmen": ("BERECHNEN", True, "phys_float", "Schwimmt der Körper? Eintauchtiefe und Zuladung"),
    "Ergebnis aus": ("ERGEBNIS", True, "phys_off", "Ergebnis ausblenden, Körper wieder normal zeigen"),
    # Runde 86: Schalter
    "Physikansicht": ("ERGEBNIS", True, "phys_view", "Physik-Ergebnis, Lager und Lasten zeigen oder ausblenden"),
    "Live neu rechnen": ("ERGEBNIS", True, "phys_live", "Ändert sich der Körper, wird das Ergebnis neu gerechnet"),
}
_SWITCHES = ("Physikansicht", "Live neu rechnen")       # ein-/ausschaltbare Werkzeuge (anfangs an)
_LIVE_DELAY = 700           # ms Ruhe nach der letzten Änderung, bevor live neu gerechnet wird
FORCE_DIRECTIONS = {"Senkrecht in die Fläche": (), "−Z (nach unten, Gewicht)": (0.0, 0.0, -1.0),
                    "+Z": (0.0, 0.0, 1.0), "+X": (1.0, 0.0, 0.0), "−X": (-1.0, 0.0, 0.0),
                    "+Y": (0.0, 1.0, 0.0), "−Y": (0.0, -1.0, 0.0)}
FLOW_DIRECTIONS = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
# Gitterfeinheit: Zellen über den Körper; dahinter grob die Rechenzeit eines mittelgroßen Teils
FLOW_QUALITY = {"Schnell (≈ ½ min)": 9, "Normal (≈ 2 min)": 12, "Genau (≈ 6 min)": 16}
_BINS = 12                      # Farbstufen der Ergebnisanzeige
_SUPPORT_COLOUR, _LOAD_COLOUR = (0.25, 0.80, 0.35), (1.00, 0.55, 0.15)


_ENTRY_BASE = {"lager": "Lager", physics.FORCE: "Kraft", physics.PRESSURE: "Druck"}


def _free_name(base: str, used: set) -> str:
    number = 1
    while f"{base}{number}" in used:
        number += 1
    return f"{base}{number}"


class _Cancelled(Exception):
    """Der Nutzer hat die Rechnung abgebrochen."""


def colour_scale(share: float) -> tuple[float, float, float]:
    """Farbe für einen Wert zwischen 0 (blau) und 1 (rot) über Cyan, Grün und Gelb – wie Fusions Ergebnisskala."""
    share = min(max(share, 0.0), 1.0)
    return colorsys.hsv_to_rgb((1.0 - share) * 0.66, 0.85, 0.95)


class PhysicsTools:
    """Werkzeuge und Ergebnisanzeige des Reiters PHYSIK für ein Hauptfenster."""

    def __init__(self, window):
        self.w = window
        self.result: dict | None = None         # {"art", "body", "groups", "lines", "water", "legend", "shape"}
        self._pool = ThreadPoolExecutor(max_workers=1)
        self.visible = True                     # Physikansicht ein (Schalter „Physikansicht“)
        self.live = True                        # bei Änderungen am Körper neu rechnen (Schalter „Live neu rechnen“)
        self._rerun = None                      # wie die nächste Rechnung wiederholt würde (siehe _remember_rerun)
        self._busy = False                      # eine Rechnung läuft gerade
        self._again = None                      # während der Rechnung geändert: danach diese Wiederholung
        self._live_timer = QTimer()
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._live_run)
        self._waiting = None                    # wartet auf das Ende der Ruhezeit: Wiederholung
        self._known: dict = {}                  # Körper -> zuletzt gesehene Form (Lager/Lasten folgen Änderungen)

    # --- Leiste ---------------------------------------------------------------------------------------------------
    def build(self, action, panels: dict) -> None:
        """Legt die Werkzeuge an: `action(text, symbol, handler, tip)` wie im Hauptfenster, `panels` je Bereich."""
        handlers = {"Lager": self.add_support, "Kraft": self.add_force, "Druck": self.add_pressure,
                    "Lasten entfernen": self.clear_loads, "Festigkeit": self.run_stress,
                    "Luftströmung": lambda: self.ask_flow("Luft"), "Wasserströmung": lambda: self.ask_flow("Wasser"),
                    "Schwimmen": self.run_buoyancy, "Ergebnis aus": self.clear_result,
                    "Physikansicht": self.set_visible, "Live neu rechnen": self.set_live}
        for name, (panel, featured, symbol, tip) in TOOLS.items():
            if name in _SWITCHES:
                item = action(name, symbol, lambda: None, tip)
                item.setCheckable(True)
                item.setChecked(True)
                item.toggled.connect(handlers[name])
            else:
                item = action(name, symbol, handlers[name], tip)
            panels[(PHYSICS_TAB, panel)].add(item, featured)

    def set_visible(self, on: bool) -> None:
        """Physikansicht ein/aus: Ergebnisfarben, Stromlinien, Wasser und Lastmarken – nichts wird gelöscht."""
        self.visible = bool(on)
        self.w._refresh()
        self.w.viewport.show_info(self.result["legend"] if on and self.result else "")
        self.w.statusBar().showMessage("Physikansicht " + ("an" if on else "aus"), 4000)

    def set_live(self, on: bool) -> None:
        self.live = bool(on)
        self.w.statusBar().showMessage("Live neu rechnen " + ("an" if on else "aus"), 4000)
        if on and self._waiting is not None:
            self._live_timer.start(_LIVE_DELAY)

    # --- Lager und Lasten -----------------------------------------------------------------------------------------
    def setup(self) -> dict:
        """Lager und Lasten des Entwurfs (wird mit dem Entwurf gespeichert). Jeder Eintrag hat einen Namen
        („Lager1“, „Kraft2“ …) und ist aktiv oder unterdrückt (Runde 86: Physik-Timeline); ältere Entwürfe ohne diese
        Angaben bekommen sie hier."""
        setup = self.w.timeline.view_state.setdefault("physik", {"lager": [], "lasten": []})
        used = {entry.get("name") for key in ("lager", "lasten") for entry in setup.get(key, [])}
        for key in ("lager", "lasten"):
            for entry in setup.setdefault(key, []):
                entry.setdefault("aktiv", True)
                if not entry.get("name"):
                    entry["name"] = _free_name(_ENTRY_BASE[entry.get("art", "lager") if key == "lasten" else "lager"],
                                               used)
                    used.add(entry["name"])
        return setup

    def _new_name(self, kind: str) -> str:
        used = {entry.get("name") for key in ("lager", "lasten") for entry in self.setup().get(key, [])}
        return _free_name(_ENTRY_BASE[kind], used)

    def entries(self) -> list[tuple[str, dict]]:
        """Alle Lager und Lasten in der Reihenfolge der Physik-Timeline: (Liste, Eintrag)."""
        setup = self.setup()
        return [("lager", entry) for entry in setup["lager"]] + [("lasten", entry) for entry in setup["lasten"]]

    def entry(self, name: str) -> tuple[str, dict] | None:
        return next(((key, entry) for key, entry in self.entries() if entry["name"] == name), None)

    def _chosen_faces(self, title: str) -> tuple[str, list] | None:
        """Gewählte Flächen eines einzigen Körpers: (Körper, Merkpunkte); sonst Hinweis und None."""
        faces = [(body, index) for kind, body, index in self.w._selection if kind == "face"]
        bodies = {body for body, _index in faces}
        if not faces or len(bodies) != 1 or next(iter(bodies)) not in self.w.timeline.state.bodies:
            QMessageBox.information(self.w, title, "Erst eine oder mehrere Flächen eines Körpers anklicken "
                                                   "(Strg+Klick für mehrere), dann das Werkzeug wählen.")
            return None
        body = next(iter(bodies))
        pick = self.w._picks[body]
        return body, [list(map(float, pick.face_points[index])) for _body, index in faces]

    def add_support(self, chosen: tuple | None = None) -> None:
        chosen = chosen or self._chosen_faces("Lager")
        if chosen is None:
            return
        body, points = chosen
        self.setup()["lager"].append({"name": self._new_name("lager"), "body": body, "points": points, "aktiv": True})
        self._after_setup_change(f"{len(points)} Fläche(n) an {body} fest gelagert")

    def add_force(self, chosen: tuple | None = None, value: float | None = None, direction: str | None = None) -> None:
        chosen = chosen or self._chosen_faces("Kraft")
        if chosen is None:
            return
        if value is None or direction is None:
            asked = self._ask_force()
            if asked is None:
                return
            value, direction = asked
        body, points = chosen
        self.setup()["lasten"].append({"name": self._new_name(physics.FORCE), "body": body, "points": points,
                                       "art": physics.FORCE, "wert": value, "aktiv": True,
                                       "richtung": list(FORCE_DIRECTIONS[direction]), "text": direction})
        self._after_setup_change(f"Kraft {value:g} N ({direction}) an {body}")

    def add_pressure(self, chosen: tuple | None = None, value: float | None = None) -> None:
        chosen = chosen or self._chosen_faces("Druck")
        if chosen is None:
            return
        if value is None:
            value, ok = QInputDialog.getDouble(self.w, "Druck", "Druck auf die Flächen (MPa = N/mm²; 1 bar = 0,1 MPa):",
                                               0.1, 0.0001, 10000.0, 4)
            if not ok:
                return
        body, points = chosen
        self.setup()["lasten"].append({"name": self._new_name(physics.PRESSURE), "body": body, "points": points,
                                       "art": physics.PRESSURE, "wert": value, "richtung": [], "aktiv": True})
        self._after_setup_change(f"Druck {value:g} MPa an {body}")

    def _ask_force(self, value: float = 100.0, current: str = "") -> tuple[float, str] | None:
        dialog = QDialog(self.w)
        dialog.setWindowTitle("Kraft")
        form = QFormLayout(dialog)
        amount = QDoubleSpinBox()
        amount.setRange(0.001, 1e7)
        amount.setDecimals(3)
        amount.setValue(value)
        amount.setSuffix(" N")
        form.addRow("Kraft (gesamt, auf die Flächen verteilt)", amount)
        direction = QComboBox()
        direction.addItems(list(FORCE_DIRECTIONS))
        if current in FORCE_DIRECTIONS:
            direction.setCurrentText(current)
        form.addRow("Richtung", direction)
        form.addRow("", _hint("1 kg Gewicht ≈ 9,81 N"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return amount.value(), direction.currentText()

    def clear_loads(self) -> None:
        self.w.timeline.view_state["physik"] = {"lager": [], "lasten": []}
        self._after_setup_change("Lager und Lasten entfernt")

    def _after_setup_change(self, message: str) -> None:
        self.show_setup()
        self.refresh_strip()
        self.w.statusBar().showMessage(message, 6000)

    # --- Einträge der Physik-Timeline bearbeiten (Runde 86) -------------------------------------------------------
    def edit_entry(self, name: str) -> None:
        """Wert und Richtung einer Last ändern (Lager haben keine Werte: dort nur umplatzieren)."""
        found = self.entry(name)
        if found is None:
            return
        key, entry = found
        if key == "lager":
            QMessageBox.information(self.w, name, "Ein Lager hat keine Werte. Zum Verlegen andere Flächen anklicken "
                                                  "und im Rechtsklick-Menü „Flächen umplatzieren“ wählen.")
            return
        if entry["art"] == physics.FORCE:
            asked = self._ask_force(entry["wert"], entry.get("text", ""))
            if asked is None:
                return
            entry["wert"], entry["text"] = asked
            entry["richtung"] = list(FORCE_DIRECTIONS[asked[1]])
        else:
            value, ok = QInputDialog.getDouble(self.w, name, "Druck auf die Flächen (MPa = N/mm²; 1 bar = 0,1 MPa):",
                                               entry["wert"], 0.0001, 10000.0, 4)
            if not ok:
                return
            entry["wert"] = value
        self._after_setup_change(f"{name} geändert")

    def move_entry(self, name: str, chosen: tuple | None = None) -> None:
        """Flächen umplatzieren: der Eintrag gilt ab jetzt für die gerade gewählten Flächen."""
        found = self.entry(name)
        if found is None:
            return
        chosen = chosen or self._chosen_faces(f"{name} umplatzieren")
        if chosen is None:
            return
        _key, entry = found
        entry["body"], entry["points"] = chosen
        self._after_setup_change(f"{name} liegt jetzt auf {len(chosen[1])} Fläche(n) von {chosen[0]}")

    def toggle_entry(self, name: str) -> None:
        """Unterdrücken bzw. wieder aufnehmen: unterdrückte Einträge zählen beim Rechnen nicht."""
        found = self.entry(name)
        if found is not None:
            found[1]["aktiv"] = not found[1].get("aktiv", True)
            self._after_setup_change(f"{name} " + ("aktiv" if found[1]["aktiv"] else "unterdrückt"))

    def remove_entry(self, name: str) -> None:
        found = self.entry(name)
        if found is not None:
            self.setup()[found[0]].remove(found[1])
            self._after_setup_change(f"{name} entfernt")

    def select_entry(self, name: str) -> None:
        """Klick in der Physik-Timeline: die Flächen des Eintrags im Modell markieren."""
        found = self.entry(name)
        if found is None:
            return
        entry = found[1]
        pick = self.w._picks.get(entry["body"])
        if pick is None:
            return
        indices = [index for index in (_nearest(pick.face_points, point) for point in entry["points"])
                   if index is not None]
        self.w._selection = [("face", entry["body"], index) for index in indices]
        self.w._show_selection()

    # --- Physik-Timeline -------------------------------------------------------------------------------------------
    def strip(self):
        """Die Leiste (wird beim ersten Gebrauch angelegt, siehe PhysicsTimeline)."""
        if getattr(self, "_strip", None) is None:
            self._strip = PhysicsTimeline(self)
        return self._strip

    def refresh_strip(self) -> None:
        if getattr(self, "_strip", None) is not None:
            self._strip.refresh()

    def tab_changed(self, name: str) -> None:
        """Wie in Fusions Simulation: im Reiter PHYSIK erscheint die Physik-Timeline unter der Timeline."""
        dock = getattr(self, "dock", None)
        if dock is not None:
            dock.setVisible(name == PHYSICS_TAB)
            if name == PHYSICS_TAB:
                self.refresh_strip()

    def show_setup(self) -> None:
        """Gelagerte Flächen grün, belastete orange; dazu je Last ein Pfeil (Linien)."""
        view = self.w.viewport
        state = self.w.timeline.state
        groups = {"physik_lager": [], "physik_last": []}
        arrows = []
        # bei ausgeschalteter Physikansicht bleiben die Ebenen leer (nichts wird gelöscht)
        for key, layer in ((("lager", "physik_lager"), ("lasten", "physik_last")) if self.visible else ()):
            for entry in self.setup().get(key, []):
                pick = self.w._picks.get(entry["body"])
                if pick is None or entry["body"] not in state.bodies or not entry.get("aktiv", True):
                    continue                                    # unterdrückte Einträge werden nicht gezeigt
                for point in entry["points"]:
                    index = _nearest(pick.face_points, point)
                    if index is None:
                        continue
                    groups[layer].append(pick.triangles.reshape(-1, 3, 3)[pick.triangle_face == index].reshape(-1, 3))
                    if key == "lasten":
                        arrows += _arrow_lines(state.bodies[entry["body"]], point, entry, view._distance)
        for name, colour in (("physik_lager", _SUPPORT_COLOUR), ("physik_last", _LOAD_COLOUR)):
            layer = _layer(view, name, colour, lines=False)
            layer.alpha = 0.75
            view.set_geometry(name, _stack(groups[name]))
        _layer(view, "physik_pfeile", _LOAD_COLOUR, lines=True)
        view.set_geometry("physik_pfeile", np.array(arrows, dtype=np.float32).reshape(-1, 3))

    # --- Rechnen im Hintergrund -----------------------------------------------------------------------------------
    def _run(self, title: str, work, finished, quiet: bool = False) -> None:
        """Führt `work(progress)` im Hintergrund aus; Fortschritt und Abbrechen über einen Dialog, danach
        `finished(Ergebnis)` im Oberflächen-Faden. Fehler kommen als Meldung.

        `quiet` (Live-Neurechnung): ohne Dialog, Fortschritt und Fehler in der Statuszeile. Läuft schon eine
        Rechnung, wird diese danach wiederholt (die letzte Änderung zählt)."""
        if self._busy:
            self._again = self._rerun
            return
        self._busy = True
        if quiet:
            return self._run_quiet(title, work, finished)
        dialog = QProgressDialog(f"{title} …", "Abbrechen", 0, 1000, self.w)
        dialog.setWindowTitle(title)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        status = {"share": 0.0, "text": "", "cancel": False}

        def progress(share: float, text: str) -> None:
            if status["cancel"]:
                raise _Cancelled()
            status["share"], status["text"] = share, text

        future = self._pool.submit(work, progress)
        timer = QTimer(self.w)

        def poll() -> None:
            if dialog.wasCanceled():
                status["cancel"] = True
            dialog.setValue(int(1000 * status["share"]))
            dialog.setLabelText(f"{title}: {status['text']}")
            if not future.done():
                return
            timer.stop()
            dialog.close()
            self._busy = False
            try:
                result = future.result()
            except _Cancelled:
                self.w.statusBar().showMessage(f"{title} abgebrochen", 5000)
                return
            except ft.FeatureError as error:
                QMessageBox.warning(self.w, title, str(error))
                return
            except Exception as error:          # Rechenkern: unerwartete Fehler als Meldung, nicht als Absturz
                QMessageBox.warning(self.w, title, f"Die Rechnung ist fehlgeschlagen:\n{error}")
                return
            finished(result)
            self._after_run()

        timer.timeout.connect(poll)
        timer.start(100)
        self._running = (future, timer, dialog)

    def _run_quiet(self, title: str, work, finished) -> None:
        """Live-Neurechnung: wie _run, aber ohne Dialog (Fortschritt in der Statuszeile)."""
        status = {"share": 0.0, "text": ""}

        def progress(share: float, text: str) -> None:
            status["share"], status["text"] = share, text

        future = self._pool.submit(work, progress)
        timer = QTimer(self.w)

        def poll() -> None:
            if not future.done():
                self.w.statusBar().showMessage(f"Live: {title} {status['share'] * 100:.0f} % – {status['text']}")
                return
            timer.stop()
            self._busy = False
            try:
                result = future.result()
            except Exception as error:          # z. B. eine gelagerte Fläche gibt es nach der Änderung nicht mehr
                self.w.statusBar().showMessage(f"Live: {title} nicht möglich – {error}", 10000)
                self._after_run()
                return
            self.w.statusBar().showMessage(f"Live: {title} neu gerechnet", 4000)
            finished(result)
            self._after_run()

        timer.timeout.connect(poll)
        timer.start(100)
        self._running = (future, timer, None)

    def _after_run(self) -> None:
        """Während der Rechnung wieder geändert? Dann gleich die nächste Live-Rechnung."""
        again, self._again = self._again, None
        if again is not None and self.live:
            self._waiting = again
            self._live_timer.start(_LIVE_DELAY)

    def _live_run(self) -> None:
        waiting, self._waiting = self._waiting, None
        if waiting is not None and self.live:
            waiting()

    def wait(self, timeout: float = 600.0) -> None:
        """Für Tests: wartet, bis die laufende Rechnung fertig und angezeigt ist."""
        from PySide6.QtWidgets import QApplication
        import time
        running = getattr(self, "_running", None)
        if running is None:
            return
        future, timer, _dialog = running
        end = time.monotonic() + timeout
        while (timer.isActive() or not future.done() or self._live_timer.isActive() or self._busy) \
                and time.monotonic() < end:
            QApplication.processEvents()
            threading.Event().wait(0.05)
        QApplication.processEvents()

    def _target_body(self, title: str) -> str | None:
        """Der zu rechnende Körper: der gewählte, sonst der mit Lagern/Lasten, sonst der einzige."""
        state = self.w.timeline.state
        chosen = [body for _kind, body, _index in self.w._selection if body in state.bodies]
        if chosen:
            return chosen[0]
        used = [entry["body"] for key in ("lager", "lasten") for entry in self.setup().get(key, [])
                if entry["body"] in state.bodies]
        if used:
            return used[0]
        visible = [name for name in state.bodies if name in self.w._picks and name not in state.helpers]
        if len(visible) == 1:
            return visible[0]
        QMessageBox.information(self.w, title, "Bitte den Körper anklicken, der berechnet werden soll.")
        return None

    def _material(self, body: str) -> physics.Material:
        return physics.material_named(self.w.timeline.view_state.get("material", {}).get(body))

    # --- Festigkeit -----------------------------------------------------------------------------------------------
    def run_stress(self, body: str | None = None, quiet: bool = False, name: str | None = None) -> None:
        setup = self.setup()
        body = body or self._target_body("Festigkeit")
        if body is None or body not in self.w.timeline.state.bodies:
            return
        supports = [p for entry in setup["lager"] if entry["body"] == body and entry.get("aktiv", True)
                    for p in entry["points"]]
        loads = [physics.Load(entry["art"], entry["points"], entry["wert"], tuple(entry.get("richtung") or ()))
                 for entry in setup["lasten"] if entry["body"] == body and entry.get("aktiv", True)]
        if not supports or not loads:
            text = (f"Für {body} fehlen noch Lager oder Lasten: Flächen anklicken, dann „Lager“ bzw. "
                    "„Kraft“/„Druck“.")
            self.w.statusBar().showMessage(text, 8000) if quiet else QMessageBox.information(self.w, "Festigkeit", text)
            return
        shape = self.w.timeline.state.bodies[body]
        material = self._material(body)
        self._remember_rerun(lambda result_name: self.run_stress(body, True, result_name), name)
        self._run("Festigkeit", lambda progress: physics.static_stress(shape, material, supports, loads,
                                                                         progress=progress),
                  lambda result: self._show_stress(body, shape, result), quiet)

    def _show_stress(self, body: str, shape, result: physics.StressResult) -> None:
        size = float(np.linalg.norm(result.nodes.max(axis=0) - result.nodes.min(axis=0)))
        scale = 0.05 * size / result.max_displacement if result.max_displacement > 1e-12 else 0.0
        points = result.nodes + result.displacement * scale
        triangles = points[result.surface]                                  # (m, 3, 3)
        values = result.von_mises[result.surface].mean(axis=1)
        top = max(result.max_stress, 1e-9)
        groups = _binned(triangles, values / top)
        number = lambda value, digits=2: f"{value:.{digits}f}".replace(".", ",")
        safety = "∞" if math.isinf(result.safety) else number(result.safety)
        verdict = ("hält" if result.safety >= 2 else "knapp – Sicherheit unter 2" if result.safety >= 1
                   else "versagt – Spannung über der Festigkeit")
        legend = (f"Festigkeit {body} ({result.material.name}, E = {result.material.young:g} MPa)\n"
                  f"größte Vergleichsspannung {number(result.max_stress, 1)} MPa (blau 0 … rot {number(top, 1)})\n"
                  f"größte Verschiebung {number(result.max_displacement, 4)} mm"
                  + (f" (Verformung {number(scale, 0)}-fach überhöht)" if scale > 1.5 else "") + "\n"
                  f"Sicherheitsfaktor {safety}: {verdict}\n"
                  f"Netz: {result.elements} Elemente; Lagerkraft {number(result.reaction, 1)} N "
                  f"= Last {number(result.load_total, 1)} N"
                  + (f"\nNetz automatisch gröber ({number(result.coarsened, 1)} mm) – feiner sprengte den Speicher"
                     if result.coarsened else ""))
        self._show({"art": "Festigkeit", "body": body, "shape": shape, "groups": groups, "lines": None,
                    "water": None, "legend": legend, "data": result})

    # --- Strömung -------------------------------------------------------------------------------------------------
    def ask_flow(self, fluid: str) -> None:
        body = self._target_body(f"{fluid}strömung")
        if body is None:
            return
        dialog = QDialog(self.w)
        dialog.setWindowTitle(f"{fluid}strömung")
        form = QFormLayout(dialog)
        direction = QComboBox()
        direction.addItems(list(FLOW_DIRECTIONS))
        form.addRow("Strömt in Richtung", direction)
        speed = QDoubleSpinBox()
        speed.setRange(0.01, 400.0)
        speed.setDecimals(2)
        speed.setValue(20.0 if fluid == "Luft" else 2.0)
        speed.setSuffix(" m/s")
        form.addRow("Geschwindigkeit", speed)
        quality = QComboBox()
        quality.addItems(list(FLOW_QUALITY))
        quality.setCurrentIndex(1)
        form.addRow("Genauigkeit", quality)
        form.addRow("", _hint("Näherung (grobes Gitter, etwa ±20 %): gut zum Vergleichen von Varianten,\n"
                              "kein Ersatz für eine Windkanal- oder Strömungsmessung."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.run_flow(fluid, direction.currentText(), speed.value(), FLOW_QUALITY[quality.currentText()], body)

    def run_flow(self, fluid: str, direction: str, speed: float, resolution: int, body: str | None = None,
                 quiet: bool = False, name: str | None = None) -> None:
        body = body or self._target_body(f"{fluid}strömung")
        if body is None or body not in self.w.timeline.state.bodies:
            return
        shape = self.w.timeline.state.bodies[body]
        self._remember_rerun(lambda result_name: self.run_flow(fluid, direction, speed, resolution, body, True,
                                                               result_name), name)
        self._run(f"{fluid}strömung",
                  lambda progress: physics.flow(shape, direction, speed, fluid, resolution, progress=progress),
                  lambda result: self._show_flow(body, shape, result), quiet)

    def _show_flow(self, body: str, shape, result: physics.FlowResult) -> None:
        from scipy.spatial import cKDTree
        pick = self.w._picks.get(body)
        triangles = pick.triangles.reshape(-1, 3, 3) if pick is not None else np.zeros((0, 3, 3))
        groups = []
        if len(triangles) and len(result.pressure_points):
            # Druckbeiwert je Dreieck: Wert der nächstgelegenen Fluidzelle am Körper
            _distance, nearest = cKDTree(result.pressure_points).query(triangles.mean(axis=1))
            cp = result.pressure_values[nearest]
            low, high = -1.5, 1.0                                     # Sog blau … Staudruck rot
            groups = _binned(triangles, (np.clip(cp, low, high) - low) / (high - low))
        segments = []
        for line in result.streamlines:
            segments += [point for pair in zip(line[:-1], line[1:]) for point in pair]
        number = lambda value, digits=2: f"{value:.{digits}f}".replace(".", ",")
        legend = (f"{result.fluid}strömung um {body}: {number(result.speed, 1)} m/s in Richtung {result.direction}\n"
                  f"Widerstandsbeiwert cw ≈ {number(result.drag_coefficient)}, Stirnfläche "
                  f"{number(result.frontal_area / 100, 1)} cm²\n"
                  f"Widerstandskraft ≈ {number(result.drag_force, 3)} N; Querkräfte {number(result.lift[0], 3)} / "
                  f"{number(result.lift[1], 3)} N\n"
                  f"Druck: blau Sog … rot Staudruck; Linien: Stromlinien\n"
                  f"Re {_thousands(result.reynolds)} (Gitter {_thousands(result.lattice_reynolds)}), "
                  f"{_thousands(result.cells)} Zellen – Näherung, etwa ±20 %")
        self._show({"art": f"{result.fluid}strömung", "body": body, "shape": shape, "groups": groups,
                    "lines": np.array(segments, dtype=np.float32).reshape(-1, 3), "water": None, "legend": legend,
                    "data": result})

    # --- Schwimmen ------------------------------------------------------------------------------------------------
    def run_buoyancy(self, body: str | None = None, quiet: bool = False, name: str | None = None) -> None:
        body = body or self._target_body("Schwimmen")
        if body is None or body not in self.w.timeline.state.bodies:
            return
        shape = self.w.timeline.state.bodies[body]
        material = self._material(body)
        self._remember_rerun(lambda result_name: self.run_buoyancy(body, True, result_name), name)
        self._run("Schwimmen", lambda progress: (progress(0.1, "Eintauchtiefe …"),
                                                 physics.buoyancy(shape, material.density))[1],
                  lambda result: self._show_buoyancy(body, shape, material, result), quiet)

    def _remember_rerun(self, rerun, name: str | None) -> None:
        """Merkt, wie die gerade gestartete Rechnung wiederholt würde, und unter welchem Namen ihr Ergebnis steht
        (bei einer Live-Neurechnung derselbe Name: der Eintrag der Physik-Timeline wird ersetzt)."""
        self._rerun = rerun
        self._pending_name = name

    def _show_buoyancy(self, body: str, shape, material: physics.Material, result: physics.Buoyancy) -> None:
        low = shape_bounds(shape)
        pad = 0.6 * max(low[3] - low[0], low[4] - low[1], 10.0)
        level = result.waterline if result.floats else low[5] + 0.02 * pad
        corners = [(low[0] - pad, low[1] - pad), (low[3] + pad, low[1] - pad), (low[3] + pad, low[4] + pad),
                   (low[0] - pad, low[4] + pad)]
        quad = [corners[i] for i in (0, 1, 2, 0, 2, 3)]
        water = np.array([(x, y, level) for x, y in quad], dtype=np.float32)
        number = lambda value, digits=1: f"{value:.{digits}f}".replace(".", ",")
        if result.floats:
            verdict = (f"schwimmt – taucht {number(result.draft)} mm tief ein ({number(result.submerged * 100, 0)} % "
                       f"des Volumens)\nZuladung bis zum Untergehen: {number(result.carry)} g")
        else:
            verdict = (f"geht unter – {number(result.weight, 2)} N Gewicht gegen höchstens "
                       f"{number(result.buoyancy_max, 2)} N Auftrieb")
        legend = (f"Schwimmen {body} ({material.name}, {number(material.density, 2)} g/cm³) in Süßwasser\n"
                  f"Masse {number(result.mass)} g, Volumen {number(result.volume / 1000)} cm³\n{verdict}\n"
                  "Hohlräume zählen nur, wenn sie im Körper eingeschlossen sind; Lage: aufrecht wie im Modell")
        self._show({"art": "Schwimmen", "body": body, "shape": shape, "groups": [], "lines": None, "water": water,
                    "legend": legend, "data": result})

    # --- Anzeige --------------------------------------------------------------------------------------------------
    def _show(self, result: dict, remember: bool = True) -> None:
        if remember:
            rerun = self._rerun
            known = getattr(self, "results", [])
            name = getattr(self, "_pending_name", None)
            self._pending_name = None
            if name and any(item.get("name") == name for item in known):
                # Live-Neurechnung: der Eintrag behält Platz und Namen
                result["name"] = name
                self.results = [result if item.get("name") == name else item for item in known]
            else:
                # jede Rechnung wird ein Eintrag der Physik-Timeline (nur für diese Sitzung – Ergebnisse sind groß)
                self.results = [*known, result][-12:]
                count = sum(1 for item in self.results if item["art"] == result["art"])
                result["name"] = f"{result['art']}{count}"
            # Wiederholung für Live: dieselbe Rechnung, Ergebnis unter demselben Namen
            result["rerun"] = (lambda name: (lambda: rerun(name)))(result["name"]) if rerun else None
        self.result = result
        self.w._refresh()
        self.refresh_strip()
        self.w.viewport.show_info(result["legend"])
        self.w.statusBar().showMessage(result["legend"].splitlines()[0], 8000)

    def show_result(self, name: str) -> None:
        """Ein früheres Ergebnis wieder zeigen (Klick auf den Rechnungs-Eintrag), solange der Körper gleich ist."""
        found = next((item for item in getattr(self, "results", []) if item.get("name") == name), None)
        if found is None:
            return
        if self.w.timeline.state.bodies.get(found["body"]) is not found["shape"]:
            self.w.statusBar().showMessage(f"{name}: der Körper hat sich seitdem geändert – bitte neu rechnen", 6000)
            return
        self._show(found, remember=False)

    def clear_result(self) -> None:
        if self.result is None:
            return
        self.result = None
        self.w._refresh()
        self.w.viewport.show_info("")

    def hidden_bodies(self) -> set:
        """Körper, deren normale Darstellung das Ergebnis ersetzt (nur bei farbigen Ergebnissen und an)."""
        if self.visible and self.result and self.result["groups"] and self._still_valid():
            return {self.result["body"]}
        return set()

    def _still_valid(self) -> bool:
        """Gilt das Ergebnis noch? Hat sich der Körper geändert (oder ist weg), wird es nicht mehr gezeigt – mit
        „Live neu rechnen“ startet kurz danach dieselbe Rechnung für den geänderten Körper (Runde 86)."""
        result = self.result
        if result is None:
            return False
        current = self.w.timeline.state.bodies.get(result["body"])
        if current is not result["shape"]:
            self.result = None
            self.w.viewport.show_info("")
            if current is not None and self.live and result.get("rerun") is not None:
                self._waiting = result["rerun"]
                if self._busy:
                    self._again = result["rerun"]       # nach der laufenden Rechnung
                else:
                    self._live_timer.start(_LIVE_DELAY)
                self.w.statusBar().showMessage(f"Live: {result['art']} wird für den geänderten Körper neu gerechnet …")
            return False
        return True

    def follow_changes(self) -> None:
        """Lager und Lasten folgen ihren Flächen, wenn sich ein Körper ändert (Runde 86, für „live“): wird z. B. ein
        Balken verlängert, wandert die Endfläche – ihr gemerkter Punkt läge sonst im Leeren und die Neurechnung
        fände die Fläche nicht mehr. Gesucht wird in der neuen Form die passende Fläche (siehe _followed_point)."""
        state = self.w.timeline.state
        for _key, entry in self.entries():
            body = entry["body"]
            current = state.bodies.get(body)
            before = self._known.get(body)
            if current is None or before is None or current is before:
                continue
            entry["points"] = [_followed_point(before, current, point) for point in entry["points"]]
        self._known = {entry["body"]: state.bodies.get(entry["body"]) for _key, entry in self.entries()
                       if entry["body"] in state.bodies}

    def decorate(self, groups: list) -> list:
        """Ergänzt die Farbgruppen der Ansicht um das Ergebnis und zeichnet Stromlinien, Wasser, Lasten."""
        self.follow_changes()
        view = self.w.viewport
        valid = self._still_valid() and self.visible
        lines = self.result["lines"] if valid and self.result["lines"] is not None else np.zeros((0, 3), np.float32)
        water = self.result["water"] if valid and self.result["water"] is not None else np.zeros((0, 3), np.float32)
        _layer(view, "physik_linien", (0.95, 0.95, 1.0), lines=True)
        view.set_geometry("physik_linien", lines)
        layer = _layer(view, "physik_wasser", (0.20, 0.55, 0.95), lines=False)
        layer.alpha = 0.35
        view.set_geometry("physik_wasser", water)
        self.show_setup()
        self.refresh_strip()                    # z. B. nach Rückgängig: Einträge können sich geändert haben
        return [*groups, *(self.result["groups"] if valid else [])]

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


# --- Hilfen ---------------------------------------------------------------------------------------------------------

def _followed_point(before, after, point: list) -> list:
    """Merkpunkt einer Fläche nach einer Änderung des Körpers: gibt es die Fläche an derselben Stelle noch, bleibt er.
    Sonst gilt die Fläche der neuen Form mit derselben Flächenart (bei Ebenen: dieselbe Außennormale), deren Mitte
    der alten am nächsten liegt – so wandert z. B. die Endfläche eines verlängerten Balkens mit."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from ..core import sketch as sk
    new_faces = ft.faces_of(after)
    points = [ft.face_point(face) for face in new_faces]
    if points and min(math.dist(known, point[:3]) for known in points) <= ft.SELECTION_TOLERANCE:
        return point
    old = ft.nearest_face(before, point)
    if old is None:
        return point
    kind = BRepAdaptor_Surface(old).GetType()
    old_frame = sk.face_frame(old)
    candidates = []
    for face, centre in zip(new_faces, points):
        if BRepAdaptor_Surface(face).GetType() != kind:
            continue
        if old_frame is not None:
            frame = sk.face_frame(face)
            if frame is None or sum(a * b for a, b in zip(frame[3:6], old_frame[3:6])) < 0.999:
                continue                    # andere Ausrichtung: eine andere Fläche
        candidates.append((math.dist(centre, point[:3]), centre))
    return list(min(candidates)[1]) if candidates else point


def _thousands(value: float) -> str:
    """Ganze Zahl mit Tausenderpunkten (deutsch): 26385 → „26.385“."""
    return f"{value:,.0f}".replace(",", ".")


def _hint(text: str):
    from PySide6.QtWidgets import QLabel
    label = QLabel(text)
    label.setStyleSheet("color: gray;")
    return label


def _layer(view, name: str, colour: tuple, lines: bool):
    """Eine eigene Zeichenebene der Ansicht (wird beim ersten Gebrauch angelegt)."""
    from .viewport import GL_LINES, GL_TRIANGLES, _Layer
    if name not in view._layers:
        view._layers[name] = (_Layer(GL_LINES, colour, False, line_width=1.6) if lines
                              else _Layer(GL_TRIANGLES, colour, not name.startswith("physik_l")))
    view._layers[name].color = colour
    return view._layers[name]


def _stack(parts: list) -> np.ndarray:
    return np.concatenate(parts).astype(np.float32) if parts else np.zeros((0, 3), dtype=np.float32)


def _nearest(points, point) -> int | None:
    if not len(points):
        return None
    distances = [math.dist(known, point[:3]) for known in points]
    index = min(range(len(distances)), key=distances.__getitem__)
    return index if distances[index] <= ft.SELECTION_TOLERANCE * 10 else None


def _binned(triangles: np.ndarray, shares: np.ndarray) -> list:
    """Dreiecke nach Wert (0 … 1) in Farbstufen: [(Farbe, Dreiecke (k·3, 3)), …]."""
    classes = np.clip((np.nan_to_num(shares) * _BINS).astype(int), 0, _BINS - 1)
    return [(colour_scale((klass + 0.5) / _BINS), triangles[classes == klass].reshape(-1, 3).astype(np.float32))
            for klass in range(_BINS) if np.any(classes == klass)]


def shape_bounds(shape) -> tuple:
    from ..core.solids import _bounds
    return _bounds(shape)


def _arrow_lines(shape, point, entry: dict, distance: float) -> list:
    """Ein Pfeil (Linien) für eine Last: auf die Fläche zu, Länge passend zur Kameraentfernung."""
    from ..core.extras import face_normal_at
    face = ft.nearest_face(shape, point)
    if face is None:
        return []
    marker = face_normal_at(face, point)
    base, normal = np.array(marker[:3]), np.array(marker[3:])
    direction = np.array(entry.get("richtung") or (-normal).tolist(), dtype=float)
    direction /= np.linalg.norm(direction) or 1.0
    length = 0.12 * distance
    start = base - direction * length
    side = np.cross(direction, [0.0, 0.0, 1.0] if abs(direction[2]) < 0.9 else [1.0, 0.0, 0.0])
    side /= np.linalg.norm(side) or 1.0
    head = 0.25 * length
    return [start, base, base, base - direction * head + side * head * 0.5,
            base, base - direction * head - side * head * 0.5]


class PhysicsTimeline(QListWidget):
    """Physik-Timeline (Runde 86): Lager, Lasten und Rechnungen als Einträge in einer Reihe, wie Fusions Simulations-
    browser. Klick markiert die Flächen bzw. zeigt ein Ergebnis wieder, Doppelklick bearbeitet, Rechtsklick:
    Bearbeiten, Flächen umplatzieren, Unterdrücken, Entfernen."""

    def __init__(self, tools: PhysicsTools):
        super().__init__()
        self.tools = tools
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListWidget.Movement.Static)
        self.setIconSize(QSize(28, 28))
        self.setGridSize(QSize(118, 74))       # Platz für Symbol und zwei Zeilen (Name, Wert)
        self.setWordWrap(True)
        self.setFixedHeight(96)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemClicked.connect(self._clicked)
        self.itemDoubleClicked.connect(self._double_clicked)
        self.setToolTip("Physik-Timeline: Klick markiert, Doppelklick bearbeitet, Rechtsklick für weitere Befehle")

    def refresh(self) -> None:
        from .icons import icon
        self.clear()
        for key, entry in self.tools.entries():
            kind = "lager" if key == "lager" else entry["art"]
            symbol = {"lager": "phys_support", physics.FORCE: "phys_force", physics.PRESSURE: "phys_pressure"}[kind]
            detail = ("" if key == "lager" else f"\n{entry['wert']:g} " + ("N" if kind == physics.FORCE else "MPa"))
            item = QListWidgetItem(icon(symbol), entry["name"] + detail)
            item.setData(Qt.ItemDataRole.UserRole, ("eintrag", entry["name"]))
            if not entry.get("aktiv", True):
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
                item.setForeground(QColor("gray"))
            item.setToolTip(f"{entry['name']} an {entry['body']}, {len(entry['points'])} Fläche(n)"
                            + ("" if entry.get("aktiv", True) else " – unterdrückt"))
            self.addItem(item)
        for result in getattr(self.tools, "results", []):
            symbol = {"Festigkeit": "phys_stress", "Luftströmung": "phys_air", "Wasserströmung": "phys_water",
                      "Schwimmen": "phys_float"}.get(result["art"], "phys_stress")
            item = QListWidgetItem(icon(symbol), result.get("name", result["art"]))
            item.setData(Qt.ItemDataRole.UserRole, ("ergebnis", result.get("name")))
            item.setToolTip(result["legend"].splitlines()[0] + "\nKlick zeigt dieses Ergebnis wieder")
            self.addItem(item)

    def _target(self, item) -> tuple[str, str] | None:
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _clicked(self, item) -> None:
        kind, name = self._target(item)
        (self.tools.select_entry if kind == "eintrag" else self.tools.show_result)(name)

    def _double_clicked(self, item) -> None:
        kind, name = self._target(item)
        if kind == "eintrag":
            self.tools.edit_entry(name)

    def _menu(self, position) -> None:
        target = self._target(self.itemAt(position))
        if target is None:
            return
        kind, name = target
        menu = QMenu(self)
        if kind == "eintrag":
            _key, entry = self.tools.entry(name)
            menu.addAction("Bearbeiten …", lambda: self.tools.edit_entry(name))
            menu.addAction("Flächen umplatzieren (gewählte Flächen übernehmen)", lambda: self.tools.move_entry(name))
            menu.addAction("Aufheben" if not entry.get("aktiv", True) else "Unterdrücken",
                           lambda: self.tools.toggle_entry(name))
            menu.addSeparator()
            menu.addAction("Entfernen", lambda: self.tools.remove_entry(name))
        else:
            menu.addAction("Ergebnis zeigen", lambda: self.tools.show_result(name))
        menu.exec(self.mapToGlobal(position))
