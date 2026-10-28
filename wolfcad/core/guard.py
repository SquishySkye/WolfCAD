# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Schutz vor Abstürzen und Hängern des Geometriekerns.

Der Geometriekern (OpenCASCADE) stürzt bei manchen unmöglichen Eingaben hart ab oder rechnet endlos –
beides lässt sich im selben Prozess nicht abfangen. Deshalb läuft eine riskante Berechnung zuerst zur Probe
in einem abgespaltenen Kindprozess: Überlebt er, wird wirklich gerechnet; stürzt er ab oder braucht er zu
lange, bleibt das Programm unversehrt und meldet den Schritt als fehlerhaft.
"""

from __future__ import annotations

import os
import pickle
import select
import signal
import time

OK, CRASH, TIMEOUT, ERROR = "ok", "crash", "timeout", "error"
DEFAULT_SECONDS = 45.0      # so lange darf eine Berechnung im Probelauf höchstens dauern
_IN_CHILD = False           # wahr im Kindprozess eines Probelaufs (dort nichts parallel rechnen, siehe in_child)


def in_child() -> bool:
    """Läuft der Code gerade im Kindprozess eines Probelaufs?

    Dort darf der Geometriekern nicht parallel rechnen: die Arbeits-Threads des Elternprozesses gibt es nach dem
    Abspalten nicht mehr, parallele Rechnungen können dann verklemmen (in Runde 56 an der Zerlegung nachgewiesen).
    """
    return _IN_CHILD


def available() -> bool:
    """Gibt es auf diesem System das Abspalten von Prozessen (Linux, macOS)?"""
    return hasattr(os, "fork")


def run(work, seconds: float = DEFAULT_SECONDS, want_result: bool = False) -> tuple[str, object]:
    """Führt `work()` in einem Kindprozess aus.

    Zurück kommt (Status, Wert): OK mit dem Ergebnis (nur mit `want_result`, es muss sich mit pickle
    übertragen lassen), ERROR mit der Fehlermeldung eines gewöhnlichen Programmfehlers, CRASH mit der
    Nummer des tödlichen Signals oder TIMEOUT.
    """
    read_end, write_end = os.pipe()
    child = os.fork()
    if child == 0:
        # Kindprozess: nur rechnen, Ergebnis melden und sofort enden – nichts von der Oberfläche anfassen
        global _IN_CHILD
        _IN_CHILD = True
        code = 0
        try:
            os.close(read_end)
            try:
                answer = (OK, work() if want_result else (work(), None)[1])
            except BaseException as error:      # gewöhnlicher Fehler: der Elternprozess meldet ihn selbst
                answer = (ERROR, str(error))
            with os.fdopen(write_end, "wb") as out:
                pickle.dump(answer, out)
        except BaseException:
            code = 3
        finally:
            os._exit(code)
    os.close(write_end)
    deadline = time.monotonic() + seconds
    data = b""
    timed_out = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            ready, _w, _x = select.select([read_end], [], [], min(remaining, 0.5))
            if ready:
                chunk = os.read(read_end, 1 << 16)
                if not chunk:
                    break               # das Kind hat geschlossen: fertig oder abgestürzt
                data += chunk
    finally:
        os.close(read_end)
    if timed_out:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass
    _pid, status = os.waitpid(child, 0)
    if timed_out:
        return TIMEOUT, None
    if os.WIFSIGNALED(status):
        return CRASH, os.WTERMSIG(status)
    try:
        return pickle.loads(data)
    except Exception:
        return CRASH, 0                 # ohne Antwort beendet: wie ein Absturz behandeln


_MIN_PER_CHILD = 3          # so viele Aufgaben bekommt ein Kind mindestens (siehe run_parallel)
_FORK_SECONDS = 0.02        # Richtwert: Abspalten eines Kindes bei großem Programm (gemessen 10–20 ms)
_COST: dict = {}            # Rechenart (Code der Funktion) -> gemessene Sekunden je Aufgabe


def run_parallel(work, items: list, seconds: float = DEFAULT_SECONDS, workers: int | None = None) -> list | None:
    """Rechnet `work(item)` für alle `items` verteilt auf mehrere abgespaltene Kindprozesse.

    Gedacht für viele gleichartige, voneinander unabhängige Kernrechnungen (z. B. Berührungsprüfungen eines neuen
    Volumens mit über hundert Körpern), deren Ergebnis sich mit pickle übertragen lässt. Jedes Kind bekommt einen
    zusammenhängenden Teil der Liste und rechnet seriell (siehe in_child). Zurück kommt die Liste der Ergebnisse in
    der Reihenfolge von `items` – oder None, wenn ein Kind abstürzt, zu lange braucht oder einen Fehler meldet;
    dann rechnet der Aufrufer wie bisher selbst."""
    if not available() or not items:
        return None
    # Jedes Kind kostet einen fork des ganzen Programms (bei großem Speicher ~10 ms). Je Kind deshalb mindestens
    # _MIN_PER_CHILD Aufgaben; lohnt sich das Verteilen nicht (weniger als zwei Kinder), rechnet der Aufrufer selbst
    # (Runde 85: ein Kreismuster startete so 1440 Kinder für kleine Listen, 13,7 s nur fürs Abspalten)
    count = min(workers or os.cpu_count() or 1, 16, len(items) // _MIN_PER_CHILD)
    if count < 2:
        return None
    # Lohnt sich das Verteilen? Aus früheren Aufrufen derselben Rechenart (gleiche Funktion) ist bekannt, wie lange
    # eine Aufgabe dauert. Sind alle zusammen schneller fertig als das Abspalten der Kinder, rechnet der Aufrufer
    # selbst (Runde 85: viele Listen sind lang, aber jede Aufgabe dauert nur Millisekunden – 960 Kinder kosteten mehr
    # als die ganze Rechnung). Gerechnet wird nie im eigenen Prozess: ein Absturz soll nur ein Kind treffen.
    kind = getattr(work, "__code__", None)
    each = _COST.get(kind)
    if each is not None and each * len(items) < _FORK_SECONDS * count * 3:
        return None
    size = -(-len(items) // count)                       # aufgerundet: Anzahl Einträge je Kind
    chunks = [items[start:start + size] for start in range(0, len(items), size)]
    children = []                                        # (Prozessnummer, Leseende, Daten)
    for chunk in chunks:
        read_end, write_end = os.pipe()
        child = os.fork()
        if child == 0:
            global _IN_CHILD
            _IN_CHILD = True
            code = 0
            try:
                os.close(read_end)
                for _pid, other, _data in children:      # Leseenden der Geschwister im Kind schließen
                    os.close(other)
                try:
                    begun = time.monotonic()
                    answer = (OK, [work(item) for item in chunk], (time.monotonic() - begun) / len(chunk))
                except BaseException as error:
                    answer = (ERROR, str(error))
                with os.fdopen(write_end, "wb") as out:
                    pickle.dump(answer, out)
            except BaseException:
                code = 3
            finally:
                os._exit(code)
        os.close(write_end)
        children.append((child, read_end, bytearray()))
    deadline = time.monotonic() + seconds
    open_ends = {read_end: data for _pid, read_end, data in children}
    try:
        while open_ends:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _w, _x = select.select(list(open_ends), [], [], min(remaining, 0.5))
            for end in ready:
                chunk = os.read(end, 1 << 16)
                if chunk:
                    open_ends[end] += chunk
                else:                                    # dieses Kind ist fertig (oder abgestürzt)
                    os.close(end)
                    del open_ends[end]
    finally:
        for end in open_ends:
            os.close(end)
    failed = bool(open_ends)                             # noch offene Enden: Zeit abgelaufen
    results = []
    for child, _end, data in children:
        if failed:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _pid, status = os.waitpid(child, 0)
        if failed or os.WIFSIGNALED(status):
            failed = True
            continue
        try:
            state, value, *timing = pickle.loads(bytes(data))
        except Exception:
            failed = True
            continue
        if state != OK:
            failed = True
            continue
        results.extend(value)
        if timing and kind is not None:
            # gleitender Mittelwert der Dauer je Aufgabe (für die Entscheidung beim nächsten Aufruf)
            _COST[kind] = timing[0] if kind not in _COST else 0.5 * (_COST[kind] + timing[0])
    return None if failed or len(results) != len(items) else results
