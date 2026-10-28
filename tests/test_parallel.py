# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Verteilte Kernrechnungen: viele unabhängige Berührungsprüfungen bzw. Schnitte laufen in mehreren abgespaltenen
Prozessen – mit genau demselben Ergebnis wie der Reihe nach."""
import os
import signal

import pytest

from wolfcad.core import features as ft
from wolfcad.core import guard
from wolfcad.core import solids
from wolfcad.core.timeline import Timeline

pytestmark = pytest.mark.skipif(not guard.available(), reason="ohne fork kein verteiltes Rechnen")


def test_run_parallel_keeps_the_order():
    assert guard.run_parallel(lambda value: value * value, list(range(37))) == [v * v for v in range(37)]


def test_run_parallel_gives_up_on_a_crash_or_error():
    def crash(value):
        if value == 5:
            os.kill(os.getpid(), signal.SIGSEGV)
        return value
    assert guard.run_parallel(crash, list(range(12))) is None
    assert guard.run_parallel(lambda value: 1 // (value - 3), list(range(12))) is None


def row_of_boxes(count: int) -> Timeline:
    timeline = Timeline()
    timeline.crash_guard = False
    for number in range(count):
        timeline.add(solids.Box(name=f"Q{number}", center_x=number * 15.0, length=10, width=10, height=10,
                                operation="Neuer Körper"))
    return timeline


@pytest.mark.parametrize("operation", ["Ausschneiden", "Vereinigen", "Automatisch", "Schnittmenge"])
def test_many_bodies_same_result_as_one_after_another(operation, monkeypatch):
    results = []
    for minimum in (10 ** 9, 2):                     # erst der Reihe nach, dann verteilt
        monkeypatch.setattr(ft, "PARALLEL_TOUCH_MIN", minimum)
        monkeypatch.setattr(ft, "PARALLEL_BOOLEAN_MIN", minimum)
        timeline = row_of_boxes(10)
        # ein langer Balken quer durch alle zehn Würfel
        timeline.add(solids.Box(name="B", center_x=67.5, length=150, width=4, height=4, offset=3, operation=operation))
        assert not timeline.errors
        results.append(sorted(round(solids._volume(shape), 4) for shape in timeline.state.bodies.values()))
    assert results[0] == results[1]


def test_collisions_same_result_as_one_after_another(monkeypatch):
    timeline = row_of_boxes(10)
    # versetzte Würfel, die jeweils zwei Nachbarn der Reihe überlappen
    for number in range(9):
        timeline.add(solids.Box(name=f"V{number}", center_x=number * 15.0 + 7.5, length=10, width=6, height=6,
                                operation="Neuer Körper"))
    results = []
    for minimum in (10 ** 9, 2):
        monkeypatch.setattr(ft, "PARALLEL_BOOLEAN_MIN", minimum)
        results.append([(a, b, round(volume, 4)) for a, b, volume in solids.interferences(timeline.state)])
    assert results[0] == results[1] and len(results[0]) == 18
