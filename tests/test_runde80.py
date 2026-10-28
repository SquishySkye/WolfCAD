# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Runde 80: Werkzeug-Clips – Dateinamen, Vollständigkeit, Aufklappen der Erklärkarte (ohne Fenster auf dem
Bildschirm)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from wolfcad.gui import tool_hints  # noqa: E402
from wolfcad.gui.clip_recorder import CLIP_SIZE, clip_name, clip_path  # noqa: E402
from wolfcad.gui.clip_scenes import SCENES  # noqa: E402


def test_clip_names_are_plain():
    assert clip_name("modell", "Drücken/Ziehen") == "modell_druecken_ziehen.webp"
    assert clip_name("skizze", "Kreis") == "skizze_kreis.webp"


def test_every_scene_has_an_animated_clip_with_a_hint_text():
    for (kind, tool) in SCENES:
        path = clip_path(kind, tool)
        assert path is not None, tool
        with Image.open(path) as clip:
            # gleiche Folgebilder fasst WebP zusammen: gezählt werden nur verschiedene Bilder
            assert clip.size == CLIP_SIZE and getattr(clip, "n_frames", 1) > 5, tool
        texts = tool_hints.SKETCH_HINTS if kind == "skizze" else tool_hints.MODEL_HINTS
        assert tool in texts, tool          # zum Video gehört immer auch die Erklärung


def test_card_unfolds_video_and_resets_when_hidden():
    card = tool_hints.HintCard()
    clip = clip_path("modell", "Extrusion")
    card.fill("Extrusion", QIcon(), "E", "Zieht ein Profil hoch.", ["Profil anklicken"], clip=clip)
    card.show()
    assert card.more.isVisibleTo(card.card) and not card.video.isVisibleTo(card.card)
    card.expand()
    card._unfold.setCurrentTime(tool_hints.UNFOLD)          # Aufklappen ans Ende spulen
    assert card.expanded and card.video.height() == tool_hints.VIDEO_SIZE[1] and card._movie is not None
    # Nachbar mit Clip: sofort mit Video; ohne Clip: ohne
    card.fill("Drehen", QIcon(), "", "Dreht ein Profil.", ["Achse wählen"], clip=clip_path("modell", "Drehen"))
    assert card.video.isVisibleTo(card.card) and card._movie is not None
    card.fill("Rippe", QIcon(), "", "Wand.", ["Linie zeichnen"], clip=None)
    assert not card.video.isVisibleTo(card.card) and card._movie is None
    # ausgeblendet: wieder zugeklappt
    card._target = 0.0
    card._finished()
    assert not card.isVisible() and card._movie is None and card.expanded     # Wechsel-Frist: bleibt offen
    card.collapse()
    assert not card.expanded
    card.deleteLater()
    app.processEvents()
