# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Absturzschutz: ein Schritt, der den Prozess töten oder endlos rechnen würde, wird abgefangen."""

import os
import signal
import time
from dataclasses import dataclass

import pytest

from wolfcad.core import guard
from wolfcad.core.features import Feature
from wolfcad.core.solids import Box
from wolfcad.core.timeline import Timeline

pytestmark = pytest.mark.skipif(not guard.available(), reason="kein fork auf diesem System")


def test_run_reports_results_errors_crashes_and_hangs():
    assert guard.run(lambda: 6 * 7, want_result=True) == (guard.OK, 42)
    assert guard.run(lambda: 1 / 0)[0] == guard.ERROR
    assert guard.run(lambda: os.kill(os.getpid(), signal.SIGSEGV)) == (guard.CRASH, signal.SIGSEGV)
    started = time.monotonic()
    assert guard.run(lambda: time.sleep(30), seconds=0.5) == (guard.TIMEOUT, None)
    assert time.monotonic() - started < 5


@dataclass
class _Deadly(Feature):
    """Stellt einen Schritt nach, bei dem der Geometriekern abstürzt."""

    KIND = "Probe-Absturz"

    def apply(self, state) -> None:
        os.kill(os.getpid(), signal.SIGSEGV)


def test_timeline_survives_a_crashing_step(monkeypatch):
    monkeypatch.setattr(Timeline, "crash_guard", True)
    timeline = Timeline()
    timeline.add(Box(length=40, width=20, height=10))
    timeline.add(_Deadly(name="Absturz1"))
    timeline.add(Box(length=10, width=10, height=30, operation="Vereinigen"))
    # der tödliche Schritt ist als Fehler vermerkt, davor und danach wurde normal gerechnet
    assert list(timeline.errors) == [1] and "abgestürzt" in timeline.errors[1]
    assert len(timeline.state.bodies) == 1
    # ein bestandener Schritt wird beim nächsten Neuberechnen nicht noch einmal zur Probe gerechnet
    calls = []
    monkeypatch.setattr(guard, "run", lambda *args, **kwargs: calls.append(1) or (guard.OK, None))
    timeline.recompute(2)
    assert not calls
    # … und ein abgestürzter (oder hängen gebliebener) Schritt auch nicht: sonst kostete jede Neuberechnung
    # erneut einen Probelauf bis zur Zeitgrenze (im Zufallstest jedes Mal 45 s)
    timeline.recompute(0)
    assert not calls and "abgestürzt" in timeline.errors[1]
