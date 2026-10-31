# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Die Timeline: geordnete Liste aller Features mit Rücksetzmarke.

Das Modell wird nie direkt verändert, sondern immer aus der Feature-Liste neu
aufgebaut. Dadurch wirken Parameteränderungen, Umsortieren, Unterdrücken und
das Zurückrollen auf einen früheren Stand automatisch auf alle Folgeschritte.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import guard
from .features import LEGACY_XZ, BuildState, Feature, FeatureError, feature_from_dict
from .parameters import ParameterError, evaluate, resolve

FILE_VERSION = 2
# Version 1: die XZ-Ebene hatte ihre y-Achse nach -Z; solche Entwürfe behalten beim Laden ihre alte Lage
_READABLE_VERSIONS = (1, 2)
# Geometriestufe eines Entwurfs (Datei-Schlüssel „geometrie“; fehlt er, gilt Stufe 1):
#   1 – bisheriges Verhalten: Skizzenstege bleiben als Schlitz im Profil, Körper werden nach einem Schritt weder
#       repariert noch zusammengefasst. Ältere Entwürfe behalten so ihre Form – ihre Schritte (z. B. „Fläche
#       löschen“ auf damaligen Nahtflächen) beziehen sich auf genau diese Flächen.
#   2 – seit 2026-10-26: Stege werden ignoriert, Körper nach jedem Schritt repariert und Flächen zusammengefasst,
#       ein Schritt mit ungültigem Ergebnis meldet einen Fehler und ändert nichts (wie Fusion).
#   3 – seit 2026-10-27: fast deckungsgleiche Skizzenkurven (unter 1 µm, z. B. ein gezeichneter Kreis auf einem
#       projizierten) werden zu einer Kurve zusammengelegt, statt haarfeine, ungültige Spaltflächen zu bilden.
GEOMETRY_LEVEL = 3
# Schritte, die den Geometriekern nicht in Gefahr bringen: sie brauchen keinen Probelauf (siehe guard.py)
_HARMLESS_KINDS = {"Skizze", "Ebene", "Rechteck-Skizze", "Kreis-Skizze", "Verschieben", "Farbe", "Komponente",
                   "Gelenk", "Import", "STEP-Import", "Fusion-Skizzen", "Fusion-Endstand", "Fusion-Verschieben",
                   "Fusion-Körper entfernen", "Körper einfügen"}
# Schritte, die Flächen absichtlich teilen: ihre neuen Kanten dürfen nicht wieder verschmolzen werden
_KEEPS_FACES_SPLIT = {"Fläche teilen", "Prägen"}
# Schritte, die nur etwas zum Anschauen in den Stand legen (eine Leinwand: Bild mit vier Ecken) und an den Körpern
# nichts ändern. Ändert man sie, muss nichts Späteres neu gerechnet werden (Runde 90: das Verschieben einer
# Leinwand rechnete bei jedem Mausschritt alle späteren Rundungen, Schalen … neu, mit Probelauf – es ruckelte stark)
_DISPLAY_ONLY = {"Leinwand": "canvases"}       # Art -> das Verzeichnis im Stand, in das der Schritt schreibt
_HARMLESS_KINDS |= set(_DISPLAY_ONLY)
_PRIMITIVE_KINDS = {"Quader", "Zylinder", "Kugel"}      # harmlos, solange sie nicht mit Körpern verrechnet werden
# Schritte, die nur Prismen, Zylinder oder Kopien bilden und boolesch verrechnen: der Geometriekern meldet hier
# Fehler, statt abzustürzen (in allen Zufallstests nie abgestürzt). Ohne Probelauf bleibt das Ziehen flüssig –
# der Probelauf kostet je Schritt einen abgespaltenen Prozess und eine doppelte Rechnung.
ROBUST_KINDS = {"Extrusion", "Bohrung", "Rechteckiges Muster", "Kreisförmiges Muster", "Muster entlang Pfad",
                "Spiegeln", "Kombinieren", "Teilen", "Achse", "Fusion-Extrusion", "Fusion-Quader", "Fusion-Zylinder",
                "Fusion-Kugel", "Fusion-Kombinieren"}


def _pack_trial(before: BuildState, after: BuildState, feature: Feature) -> dict | None:
    """Ergebnis eines Probelaufs zum Übertragen in den Elternprozess (Runde 93): die Körper in ihrer Reihenfolge,
    neu entstandene bzw. veränderte Formen im Binärformat des Kerns, dazu die Felder des Schritts (er führt z. B.
    Merkpunkte nach). None, wenn der Schritt noch anderes am Stand geändert hat – dann rechnet der Elternprozess
    selbst (wie bisher)."""
    import os
    import tempfile
    from dataclasses import fields as dataclass_fields
    from OCP.BinTools import BinTools
    for item in dataclass_fields(before):
        if item.name == "bodies":
            continue
        old, new = getattr(before, item.name), getattr(after, item.name)
        if isinstance(old, dict):
            if old.keys() != new.keys() or any(old[key] is not new[key] for key in old):
                return None
        elif old != new:
            return None
    shapes = {}
    for name, shape in after.bodies.items():
        if before.bodies.get(name) is shape:
            continue
        handle, path = tempfile.mkstemp(suffix=".brep", dir="/dev/shm" if os.path.isdir("/dev/shm") else None)
        os.close(handle)
        try:
            BinTools.Write_s(shape, path)
            with open(path, "rb") as source:
                shapes[name] = source.read()
        finally:
            os.remove(path)
    return {"order": list(after.bodies), "shapes": shapes, "feature": feature.to_dict()}


def _unpack_trial(packed: dict | None, feature: Feature, state: BuildState) -> bool:
    """Übernimmt das Ergebnis eines Probelaufs in `state`; False, wenn es keins gibt (dann selbst rechnen).
    Ist der Schritt im Probelauf gescheitert, kommt derselbe Fehler hier (ohne zweite Rechnung)."""
    if not packed:
        return False
    if "error" in packed:
        if packed["feature_error"]:
            raise FeatureError(packed["error"])
        raise RuntimeError(packed["error"])
    import os
    import tempfile
    from OCP.BinTools import BinTools
    from OCP.TopoDS import TopoDS_Shape
    bodies = {}
    for name in packed["order"]:
        if name in packed["shapes"]:
            handle, path = tempfile.mkstemp(suffix=".brep", dir="/dev/shm" if os.path.isdir("/dev/shm") else None)
            try:
                os.write(handle, packed["shapes"][name])
                os.close(handle)
                shape = TopoDS_Shape()
                BinTools.Read_s(shape, path)
            finally:
                os.remove(path)
            bodies[name] = shape
        elif name in state.bodies:
            bodies[name] = state.bodies[name]
        else:
            return False                # passt nicht zusammen: lieber selbst rechnen
    state.bodies = bodies
    # nur, was der Probelauf am Schritt verändert hat (z. B. nachgeführte Merkpunkte der Auswahl); beide Seiten
    # über to_dict verglichen, damit gespeicherte Vereinfachungen (Fillet: ungenutzter Endradius) nichts ändern
    current = feature.to_dict()
    for key, value in packed["feature"].items():
        if key != "kind" and current.get(key) != value:
            setattr(feature, key, value)
    return True


_TRIAL_SURVIVED: set[str] = set()
_TRIAL_FAILED: dict[str, str] = {}


def _names_in(value):
    """Alle Zeichenketten in einem Feature-Wert (auch in verschachtelten Listen) – mögliche Verweise auf Schritte."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _names_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _names_in(item)


class Timeline:
    # Absturzschutz (siehe guard.py): riskante Schritte laufen erst zur Probe in einem Kindprozess.
    # Gilt für alle Entwürfe zugleich; das echte Programm schaltet ihn beim Start ein, Tests lassen ihn aus.
    crash_guard = False

    def __init__(self):
        self.features: list[Feature] = []
        # Anzahl der aktiven Features: alles ab diesem Index ist "zurückgerollt"
        self.marker: int = 0
        # Fehlermeldungen der letzten Neuberechnung, nach Feature-Index
        self.errors: dict[int, str] = {}
        self.state = BuildState()
        # Benutzerparameter: Name -> Ausdruck (siehe parameters.py); ihre Werte und Fehler
        self.parameters: dict[str, str] = {}
        self.parameter_values: dict[str, float] = {}
        self.parameter_errors: dict[str, str] = {}
        # Zustand der Ansicht, der mit dem Entwurf gespeichert wird (z. B. ausgeblendete Körper)
        self.view_state: dict = {}
        # Schaltplan des Entwurfs (Runde 98, Reiter „Schaltplan“): Daten von electronics.schematic.Schematic.to_dict;
        # gehört zur Datei, aber nicht zum Bauverlauf (Rückgängig im 3D-Entwurf lässt ihn stehen)
        self.schematic: dict = {}
        self.geometry_level = GEOMETRY_LEVEL     # neue Entwürfe rechnen nach dem aktuellen Verfahren
        # Modellstand vor jedem aktiven Feature (Index wie in `features`), für schnelles Weiterrechnen
        self._before: list[BuildState] = []
        self._computed_until = -1       # nach wie vielen Schritten `state` berechnet ist (-1: noch nie)
        self._end_state = None          # Stand nach dem letzten Feature, solange er gilt (siehe set_marker)
        # Absturzschutz: Kombinationen aus Schritt und Vorgeschichte, die den Probelauf schon bestanden haben bzw.
        # abgestürzt oder hängen geblieben sind. Der Schlüssel beschreibt die Rechnung vollständig – das Gedächtnis
        # gilt deshalb für alle Zeitachsen (Rückgängig/Wiederholen baut eine neue auf und soll nichts neu prüfen)
        self._survived: set[str] = _TRIAL_SURVIVED
        self._failed: dict[str, str] = _TRIAL_FAILED

    # --- Bearbeiten -------------------------------------------------------

    def unique_name(self, base: str) -> str:
        """Vergibt fortlaufende Namen wie 'Extrusion1', 'Extrusion2'."""
        existing = {f.name for f in self.features}
        number = 1
        while f"{base}{number}" in existing:
            number += 1
        return f"{base}{number}"

    def _new_step(self, feature: Feature) -> None:
        """Ein neuer Schritt rechnet immer nach dem aktuellen Verfahren – auch in einem älteren Entwurf. Die alten
        Schritte behalten ihre Stufe (ihre Bezüge hängen an den damals entstandenen Flächen); der neue merkt sich
        seine eigene (`_geometrie`, in der Datei je Schritt als „geometrie“)."""
        if self.geometry_level < GEOMETRY_LEVEL and getattr(feature, "_geometrie", None) is None:
            feature._geometrie = GEOMETRY_LEVEL

    def set_step_level(self, index: int, current: bool) -> None:
        """Einen Schritt eines älteren Entwurfs nach dem aktuellen Verfahren rechnen lassen (oder wieder nach dem
        des Entwurfs) – vom Nutzer gezielt gewählt, z. B. für einen Schritt mit ungültigem Ergebnis."""
        from .sketch import Sketch
        feature = self.features[index]
        # die Skizzen, die der Schritt benutzt, gehören dazu: ihr Profil (z. B. ohne Steg) bestimmt den Körper
        used = set(_names_in(feature.to_dict()))
        steps = [index] + [number for number, other in enumerate(self.features[:index])
                           if isinstance(other, Sketch) and other.name in used]
        for number in steps:
            step = self.features[number]
            if current and self.geometry_level < GEOMETRY_LEVEL:
                step._geometrie = GEOMETRY_LEVEL
            elif hasattr(step, "_geometrie"):
                del step._geometrie
        self.recompute(start=min(steps))

    def _invalid_hint(self, state: BuildState) -> str:
        """Zusatz zur Fehlermeldung in älteren Entwürfen: liegt ein ungültiger Körper vor, welcher Schritt ihn
        erzeugt hat und wie er sich nach dem aktuellen Verfahren rechnen lässt. Geprüft wird nur im Fehlerfall."""
        if self.geometry_level >= GEOMETRY_LEVEL:
            return ""
        from . import features as _features
        for name, shape in state.bodies.items():
            if _features.is_valid(shape):
                continue
            # der erste Schritt, nach dem der Körper ungültig war (vorher fehlte er oder war gültig)
            origin = next((step for step, (before, after, _feature) in state.history.items()
                           if name in after and not _features.is_valid(after[name])
                           and (name not in before or _features.is_valid(before[name]))), None)
            where = f"„{origin}“" if origin else "der Schritt, der ihn erzeugt"
            return (f" – {name} ist ungültig (älterer Entwurf). Abhilfe: Rechtsklick auf {where} → „Nach aktuellem "
                    "Verfahren rechnen“")
        return ""

    def level_of(self, feature: Feature) -> int:
        """Geometriestufe, nach der dieser Schritt rechnet."""
        return getattr(feature, "_geometrie", None) or self.geometry_level

    def add(self, feature: Feature) -> Feature:
        """Fügt ein Feature an der Rücksetzmarke ein (wie in Fusion)."""
        if not feature.name:
            feature.name = self.unique_name(feature.KIND.replace("-", ""))
        self._new_step(feature)
        self.features.insert(self.marker, feature)
        self.marker += 1
        # Alles vor dem neuen Schritt bleibt gleich: erst ab ihm rechnen (früher: von vorn – bei schweren Modellen
        # kostete so jeder neue Schritt die Zeit des ganzen Modells)
        self.recompute(start=self.marker - 1)
        return feature

    def add_many(self, features: list[Feature]) -> None:
        """Fügt mehrere Features an der Rücksetzmarke ein und berechnet nur einmal neu (ab dem ersten neuen)."""
        first = self.marker
        for feature in features:
            if not feature.name:
                feature.name = self.unique_name(feature.KIND.replace("-", ""))
            self._new_step(feature)
            self.features.insert(self.marker, feature)
            self.marker += 1
        self.recompute(start=first)

    def remove(self, index: int) -> None:
        del self.features[index]
        if index < self.marker:
            self.marker -= 1
        self.recompute(start=index)         # die Schritte davor sind unberührt

    def move(self, source: int, target: int) -> None:
        """Verschiebt ein Feature an eine andere Position der Timeline."""
        target = max(0, min(target, len(self.features) - 1))
        self.features.insert(target, self.features.pop(source))
        self.recompute(start=min(source, target))

    def set_marker(self, position: int) -> None:
        """Rollt das Modell auf den Stand nach `position` Features zurück/vor.

        Der Stand vor jedem Feature ist gemerkt (`_before`) und bleibt gültig, solange sich davor nichts ändert
        (jede Änderung rechnet ab dem geänderten Feature neu und verwirft die Stände dahinter). Liegt der Stand an
        der neuen Stelle vor, wird er übernommen statt alles neu zu rechnen – bei einer übernommenen Fusion-Timeline
        mit Tausenden Schritten dauerte ein Klick in die Timeline sonst über 20 s. Sonst wird ab dem letzten
        gemerkten Stand weitergerechnet."""
        position = max(0, min(position, len(self.features)))
        remembered = (self._before[position] if position < len(self._before)
                      else self._end_state if position == len(self.features) else None)
        if self._computed_until >= 0 and remembered is not None:
            self.marker = position
            self.state = remembered.copy()
            self._computed_until = position
            return
        self.marker = position
        # weiter vorn: ab dem letzten gemerkten Stand weiterrechnen (der Schritt dort wird noch einmal gerechnet)
        self.recompute(max(0, len(self._before) - 1) if self._computed_until >= 0 else 0)

    def set_suppressed(self, index: int, suppressed: bool) -> None:
        self.features[index].suppressed = suppressed
        self.recompute(start=index)

    def update(self, index: int, **values) -> None:
        """Ändert Parameter eines Features und berechnet alles Folgende neu."""
        feature = self.features[index]
        for key, value in values.items():
            if not hasattr(feature, key):
                raise AttributeError(f"{feature.KIND} hat keinen Parameter '{key}'")
            setattr(feature, key, value)
        if self._update_display_only(index):
            return
        # Alles vor diesem Feature bleibt unverändert und muss nicht neu berechnet werden
        self.recompute(start=index)

    def _update_display_only(self, index: int) -> bool:
        """Rechnet einen reinen Anzeige-Schritt (siehe _DISPLAY_ONLY) für sich allein neu; True, wenn das ging.

        Sein Eintrag wird in den Stand nach ihm und in alle gemerkten Zwischenstände danach übernommen. Die Körper
        und alle späteren Schritte bleiben, wie sie sind – sie hängen nicht von ihm ab. Geht es nicht sicher
        (Zwischenstände fehlen, der Schritt ist unterdrückt oder zurückgerollt, oder er schlägt fehl), True nicht:
        dann rechnet update wie gewohnt ab ihm."""
        feature = self.features[index]
        key = _DISPLAY_ONLY.get(feature.KIND)
        if (key is None or feature.suppressed or not index < self.marker <= len(self._before)
                or self._computed_until != self.marker or self.errors.get(index)):
            return False
        trial = self._before[index].copy()
        try:
            self.parameter_values, self.parameter_errors = resolve(self.parameters)
            self._apply_expressions(feature)
            feature.apply(trial)
        except Exception:           # Fehler meldet die gewöhnliche Neuberechnung (mit Text in self.errors)
            return False
        entry = getattr(trial, key).get(feature.name)
        if entry is None:
            return False
        for state in [*self._before[index + 1:], self.state]:
            getattr(state, key)[feature.name] = entry       # jeder Stand hat sein eigenes Verzeichnis (copy)
        if self._end_state is not None:
            getattr(self._end_state, key)[feature.name] = entry
        return True

    def rename(self, index: int, name: str) -> None:
        """Benennt einen Schritt um (z. B. eine Skizze) und führt alle Verweise späterer Schritte mit.

        Spätere Schritte nennen frühere beim Namen (Skizze einer Extrusion, Pfad eines Sweeps, Basisebene,
        Komponente, Quell-Feature eines Musters …): jedes Text- und Listenfeld mit genau dem alten Namen wird ersetzt.
        """
        from dataclasses import fields as dataclass_fields
        name = name.strip()
        old = self.features[index].name
        if not name or name == old:
            return
        if any(feature.name == name for feature in self.features):
            raise ValueError(f"Den Namen „{name}“ gibt es schon")
        for feature in self.features[index + 1:]:
            for item in dataclass_fields(feature):
                if item.name in ("name", "expressions"):
                    continue
                value = getattr(feature, item.name)
                if isinstance(value, str) and value == old:
                    setattr(feature, item.name, name)
                elif isinstance(value, list) and old in value:
                    setattr(feature, item.name, [name if entry == old else entry for entry in value])
        self.features[index].name = name
        self.recompute(start=index)

    def set_parameters(self, parameters: dict[str, str]) -> None:
        """Ersetzt die Benutzerparameter und berechnet das ganze Modell neu."""
        self.parameters = dict(parameters)
        self.recompute()

    def set_expression(self, index: int, field_name: str, expression: str | None) -> None:
        """Bindet ein Zahlenfeld eines Features an einen Ausdruck (None oder leer löst die Bindung)."""
        feature = self.features[index]
        if expression and expression.strip():
            feature.expressions[field_name] = expression.strip()
        else:
            feature.expressions.pop(field_name, None)
        self.recompute(start=index)

    def _apply_expressions(self, feature: Feature) -> None:
        """Rechnet die Ausdrücke eines Features aus und trägt die Werte in seine Felder ein."""
        for field_name, expression in feature.expressions.items():
            try:
                value = evaluate(expression, self.parameter_values)
            except ParameterError as error:
                raise FeatureError(f"{field_name}: {error}") from error
            setattr(feature, field_name, value)
        # Features mit eigenen Ausdrücken (z. B. Skizzenmaße) rechnen diese selbst aus
        try:
            feature.resolve_parameters(lambda text: evaluate(text, self.parameter_values))
        except ValueError as error:         # schließt ParameterError ein
            raise FeatureError(str(error)) from error

    # --- Berechnen --------------------------------------------------------

    def recompute(self, start: int = 0) -> BuildState:
        """Baut das Modell aus den aktiven Features neu auf, ab dem Feature mit dem Index `start`.

        Vor jedem Feature wird der Zwischenstand gemerkt. Ändert sich nur ein Feature (etwa
        beim Ziehen eines Maßes), genügt es deshalb, ab dort weiterzurechnen. Schlägt ein
        Feature fehl, wird es übersprungen und der Fehler vermerkt; der Rest wird trotzdem
        berechnet.
        """
        # Ohne passenden Zwischenstand (z. B. nach Einfügen oder Löschen) von vorn beginnen
        self.parameter_values, self.parameter_errors = resolve(self.parameters)
        start = max(0, min(start, self.marker, len(self._before)))
        if start == 0:
            state = BuildState()
        elif start < len(self._before):
            state = self._before[start].copy()
        elif self._computed_until == start:
            # Neuer Schritt hinter dem bisher berechneten Ende: der fertige Stand ist der Stand davor
            state = self.state.copy()
        else:
            start, state = 0, BuildState()      # kein passender Zwischenstand: von vorn
            self._before.clear()
        previous = list(self._before)       # der Stand der vorigen Berechnung (zum Nachführen der Bezüge)
        from . import features as _features
        _features.set_geometry_level(self.geometry_level)
        del self._before[start:]
        self.errors = {index: message for index, message in self.errors.items() if index < start}
        for index in range(start, self.marker):
            feature = self.features[index]
            self._before.append(state.copy())
            if feature.suppressed:
                continue
            # Bezüge (Flächen/Kanten) findet ein Schritt über den vorigen Stand vor ihm wieder (features._follow)
            from . import features as _features
            _features.set_previous(previous[index] if index < len(previous) else None)
            level = self.level_of(feature)
            _features.set_geometry_level(level)         # je Schritt: alte Schritte alt, neue nach aktuellem Verfahren
            try:
                self._apply_expressions(feature)
                from . import start_values
                if start_values.is_neutral(feature):
                    # gerade gestartetes Werkzeug, noch bei 0 (bzw. neutral): ändert nichts, meldet keinen Fehler
                    continue
                self._trial_result = None
                problem = self._trial(index, feature, state, level)
                if problem:
                    self.errors[index] = problem        # der Kern wäre hier abgestürzt oder hängen geblieben
                    continue
                bodies_before = dict(state.bodies)
                if level >= 2:
                    _features.set_keep_edges(bodies_before.values())
                try:
                    # Runde 93: hat der Probelauf sein Ergebnis mitgeschickt, wird es übernommen statt den
                    # (riskanten, meist teuren) Schritt ein zweites Mal zu rechnen
                    if not _unpack_trial(self._trial_result, feature, state):
                        feature.apply(state)
                    if level >= 2:
                        self._heal_changed(feature, state, bodies_before)
                finally:
                    _features.set_keep_edges(None)
                if state.bodies != bodies_before:
                    # gemerkt wird nur, welche Formen vorher und nachher da waren (keine Kopien der Geometrie)
                    state.history[feature.name] = (bodies_before, dict(state.bodies), feature)
            except FeatureError as error:
                self.errors[index] = str(error) + self._invalid_hint(self._before[index])
                if level >= 2:
                    state = self._before[index].copy()  # ein fehlgeschlagener Schritt ändert nichts (wie Fusion)
            except Exception as error:  # unerwarteter Fehler des Geometriekerns
                self.errors[index] = f"Geometriefehler: {error}" + self._invalid_hint(self._before[index])
                if level >= 2:
                    state = self._before[index].copy()
        from . import features as _features
        _features.set_previous(None)
        _features.set_geometry_level(self.geometry_level)
        self.state = state
        self._computed_until = self.marker      # self.state ist der Stand nach so vielen Schritten
        # Stand nach dem letzten Feature merken (für das Vorrollen ans Ende ohne Neurechnen, siehe set_marker);
        # endet die Rechnung vorher, kann sich davor etwas geändert haben: dann gilt kein gemerkter Endstand
        self._end_state = state.copy() if self.marker == len(self.features) else None
        return state

    @staticmethod
    def _heal_changed(feature: Feature, state: BuildState, bodies_before: dict) -> None:
        """Nach einem Schritt: Flächen geänderter Körper zusammenfassen (features.healed).

        Wie in Fusion gilt: Ein Schritt klappt ganz oder gar nicht. Macht er aus einem gültigen Körper einen
        ungültigen (z. B. zwei Rundungen, die sich in einer Rille überlappen), meldet der Schritt einen Fehler,
        statt eine kaputte Form weiterzureichen."""
        from . import features as _features
        broken = []
        for name, shape in list(state.bodies.items()):
            before = bodies_before.get(name)
            if before is shape:
                continue                    # unverändert (dieselbe Form): nichts zu tun
            # Gilt der Körper vorher als heil? Neue Körper ja; vorher kaputte (z. B. aus einem Import) werden nicht
            # diesem Schritt angelastet, sondern nur repariert, soweit es geht
            if feature.KIND in _KEEPS_FACES_SPLIT:
                # gewollte Teilung: nur prüfen, nicht zusammenfassen (sonst wäre „Fläche teilen“ wirkungslos)
                if before is not None and _features.is_valid(before) and not _features.is_valid(shape):
                    broken.append(name)
                continue
            result = _features.healed(shape, source_valid=before is None or _features.is_valid(before))
            if result is None:
                broken.append(name)
            else:
                state.bodies[name] = result
        if broken:
            # recompute setzt den ganzen Stand auf den vor dem Schritt zurück
            raise FeatureError(f"{feature.KIND} ergibt keinen gültigen Körper ({', '.join(broken)}) – "
                               "der Schritt wurde nicht ausgeführt; bitte andere Werte oder Auswahl versuchen")

    def _trial(self, index: int, feature: Feature, state: BuildState, level: int = 0) -> str:
        """Probelauf eines riskanten Schritts im Kindprozess; eine Meldung, wenn er abstürzt oder hängt, sonst "".

        Harmlose Schritte (Skizzen, Ebenen, Verschieben, Farben …) und bereits bestandene Kombinationen aus
        Schritt und Vorgeschichte werden nicht erneut geprüft.
        """
        if not self.crash_guard or not guard.available() or feature.KIND in _HARMLESS_KINDS | ROBUST_KINDS:
            return ""
        if feature.KIND in _PRIMITIVE_KINDS:
            # Grundkörper (auch mit Verrechnung): Quader/Zylinder/Kugel plus dieselbe Boolesche Verrechnung wie
            # die Extrusion, die schon ohne Probelauf läuft (ROBUST_KINDS). Der Probelauf rechnete im Kindprozess
            # seriell und lief bei schweren Modellen in die 45-s-Grenze (Zufallstest 21: Zylinder über 18 Körper)
            return ""
        key = json.dumps([step.to_dict() for step in self.features[:index + 1] if not step.suppressed],
                         sort_keys=True, default=str) + json.dumps(self.parameter_values, sort_keys=True)
        if key in self._survived:
            return ""
        if key in self._failed:
            # Derselbe Schritt mit derselben Vorgeschichte ist schon einmal abgestürzt oder hängen geblieben:
            # nicht bei jeder Neuberechnung erneut bis zur Zeitgrenze warten
            return self._failed[key]
        def work():
            # genau wie die echte Rechnung (recompute): Kanten behalten ab Stufe 2, dann anwenden
            from . import features as _features
            trial = state.copy()
            if level >= 2:
                _features.set_keep_edges(trial.bodies.values())
            try:
                feature.apply(trial)
            except FeatureError as error:
                # Fehler des Schritts mitschicken: der Elternprozess meldet ihn, ohne den Schritt nachzurechnen
                return {"error": str(error), "feature_error": True}
            except Exception as error:      # unerwarteter Fehler des Geometriekerns
                return {"error": str(error), "feature_error": False}
            return _pack_trial(state, trial, feature)

        status, value = guard.run(work, want_result=True)
        if status == guard.OK:
            self._trial_result = value          # Ergebnis zum Übernehmen (None: nicht übertragbar)
        if status == guard.CRASH:
            message = ("Der Geometriekern ist bei diesem Schritt abgestürzt (abgefangen) – "
                       "bitte andere Werte oder eine andere Auswahl versuchen")
        elif status == guard.TIMEOUT:
            message = (f"Die Berechnung dauert länger als {guard.DEFAULT_SECONDS:.0f} s und wurde abgebrochen – "
                       "bitte den Schritt vereinfachen")
        else:
            message = ""
        if message:
            if len(self._failed) > 4000:
                self._failed.clear()
            self._failed[key] = message
            return message
        if len(self._survived) > 4000:
            self._survived.clear()
        self._survived.add(key)
        return ""

    def state_before(self, index: int) -> BuildState:
        """Der Modellstand unmittelbar vor dem Feature `index` (leer, wenn es keinen gibt)."""
        return self._before[index] if 0 <= index < len(self._before) else BuildState()

    # --- Speichern und Laden ----------------------------------------------

    def to_document(self) -> dict:
        """Der Entwurf als einfache Datenstruktur (für Dateien und Rückgängig-Schritte)."""
        return {
            "version": FILE_VERSION,
            "marker": self.marker,
            "parameters": dict(self.parameters),
            "ansicht": dict(self.view_state),
            "geometrie": self.geometry_level,
            "features": [self._step_dict(f) for f in self.features],
            # nur mit Inhalt: Entwürfe ohne Schaltplan bleiben Wort für Wort wie bisher
            **({"schaltplan": self.schematic} if self.schematic else {}),
        }

    @staticmethod
    def _step_dict(feature: Feature) -> dict:
        """Ein Schritt für die Datei; eine eigene Geometriestufe (neuer Schritt in älterem Entwurf) kommt mit."""
        data = feature.to_dict()
        if getattr(feature, "_geometrie", None):
            data["geometrie"] = feature._geometrie
        return data

    @classmethod
    def from_document(cls, document: dict) -> "Timeline":
        if document.get("version") not in _READABLE_VERSIONS:
            raise ValueError(f"Nicht unterstützte Dateiversion: {document.get('version')}")
        features = [dict(d) for d in document["features"]]
        if document.get("version") == 1:
            for data in features:
                if data.get("plane") == "XZ":
                    data["plane"] = LEGACY_XZ       # Geometrie bleibt, wo sie war
        timeline = cls()
        levels = [data.pop("geometrie", None) for data in features]     # eigene Stufe einzelner Schritte
        timeline.features = [feature_from_dict(d) for d in features]
        for feature, level in zip(timeline.features, levels):
            if level:
                feature._geometrie = int(level)
        timeline.parameters = dict(document.get("parameters", {}))     # ältere Dateien haben keine
        timeline.view_state = dict(document.get("ansicht", {}))
        timeline.schematic = dict(document.get("schaltplan", {}))      # ältere Dateien: kein Schaltplan
        timeline.geometry_level = int(document.get("geometrie", 1))    # ältere Dateien: bisheriges Verfahren
        timeline.marker = max(0, min(document.get("marker", len(timeline.features)),
                                     len(timeline.features)))
        timeline.recompute()
        return timeline

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_document(), indent=2, ensure_ascii=False),
                              encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Timeline":
        return cls.from_document(json.loads(Path(path).read_text(encoding="utf-8")))
