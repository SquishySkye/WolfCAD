# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Runde 77: Erklärkarten der Werkzeuge und Auswahlfelder beim Kombinieren (ohne Fenster auf dem Bildschirm)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtGui import QAction, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from wolfcad.gui import tool_hints  # noqa: E402
from wolfcad.gui.selection_field import SelectionField  # noqa: E402


def test_hint_uses_tooltip_without_own_text():
    # ohne eigenen Text dient die Beschreibung aus dem Tooltip („Name – Beschreibung“)
    what, steps = tool_hints.hint_for("Unbekannt", tool_hints.MODEL, "Unbekannt – macht etwas Nützliches")
    assert what == "macht etwas Nützliches" and steps == []
    assert tool_hints.hint_for("Unbekannt", tool_hints.MODEL, "Unbekannt") is None


def test_model_and_sketch_texts_are_separate():
    model = tool_hints.hint_for("Abrunden", tool_hints.MODEL, "Abrunden – Gewählte Kanten abrunden")
    sketch = tool_hints.hint_for("Abrunden", tool_hints.SKETCH)
    assert model[0].startswith("Gewählte Kanten") and "zwei Linien" in sketch[0]
    assert model[1] and sketch[1]


def test_every_hint_has_steps():
    for texts in (tool_hints.MODEL_HINTS, tool_hints.SKETCH_HINTS):
        for name, (_what, steps) in texts.items():
            assert steps and all(isinstance(step, str) and step for step in steps), name


def test_content_marks_sketch_actions():
    owner = QObject()           # Aktion mit Eigentümer, damit Qt sie beim Aufräumen sauber löscht
    action = tool_hints.mark(QAction(QIcon(), "Linie", owner), tool_hints.SKETCH)
    title, _icon, _key, what, steps = tool_hints.ToolHints.content(action)
    assert title == "Linie" and "Linien" in what and len(steps) >= 2
    owner.deleteLater()


def test_card_fills_and_animates_to_full_opacity():
    card = tool_hints.HintCard()
    card.fill("Extrusion", QIcon(), "E", "Zieht ein Profil hoch.", ["Profil anklicken", "Ziehen"])
    assert "SO GEHT" in card.how.text() and "Profil anklicken" in card.how.text()
    card._step(0.5)
    assert 0.4 < card._fade.opacity() < 0.6
    card._step(1.0)
    assert card._fade.opacity() == 1.0
    card.deleteLater()


def test_selection_field_counts_and_activates():
    field = SelectionField(single=False)
    assert field.button.text().endswith("Auswählen") and not field.clear.isEnabled()
    field.set_names(["Körper2", "Körper3"])
    assert field.button.text().endswith("2 ausgewählt") and field.names.text() == "Körper2, Körper3"
    seen = []
    field.activated.connect(lambda: seen.append(True))
    field.button.click()
    field.set_active(True)
    assert seen and field.active and field.button.isChecked()
    field.deleteLater()
    app.processEvents()
