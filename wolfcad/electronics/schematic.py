# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Schaltplan (Runde 98, Nutzerwunsch „Schaltplanfunktion wie in Fusion“): Bauteile, Leitungen, Netzlabels;
daraus Netze, elektrische Prüfung (ERC), Netzliste, Stückliste und SPICE-Netzliste.

Alle Lagen in ganzen Rastereinheiten (1 = 2,54 mm, siehe `symbols`), y nach unten. Verbunden ist, was sich an einem
Rasterpunkt trifft:
- Leitungsenden untereinander und mit Pins,
- ein Leitungsende mitten auf einer anderen Leitung (T-Stoß, bekommt einen Verbindungspunkt),
- Pins, die direkt aufeinander liegen,
- ein Netzlabel auf einer Leitung (Ende oder mitten darauf).
Kreuzen sich zwei Leitungen nur, sind sie NICHT verbunden (wie in Fusion/EAGLE), ebenso eine Leitung, die nur über
einen Pin hinwegläuft. Netze gleichen Namens (Label, Netzsymbol wie GND) sind überall verbunden. Netze ohne Namen
heißen N$1, N$2 … (geordnet nach Lage, damit die Namen beim Bearbeiten stabil bleiben).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field

from .symbols import LIBRARY, Pin, Symbol

ROTATIONS = (0, 90, 180, 270)
GROUND_NAMES = ("GND", "0", "MASSE", "AGND", "DGND")


@dataclass
class Part:
    """Ein platziertes Bauteil. `rotation` in Grad im Uhrzeigersinn (auf dem Bildschirm), `mirror` spiegelt vor
    dem Drehen an der senkrechten Achse."""
    ref: str
    symbol: str
    value: str = ""
    x: int = 0
    y: int = 0
    rotation: int = 0
    mirror: bool = False

    @property
    def definition(self) -> Symbol:
        return LIBRARY[self.symbol]

    def place(self, x: float, y: float) -> tuple[float, float]:
        """Symbolkoordinate → Schaltplankoordinate (spiegeln, drehen, verschieben)."""
        if self.mirror:
            x = -x
        for _turn in range(self.rotation // 90 % 4):
            x, y = -y, x            # 90° im Uhrzeigersinn (y zeigt nach unten)
        return x + self.x, y + self.y

    def pins(self) -> list[tuple[Pin, tuple[int, int]]]:
        """Die Anschlüsse mit ihrer Lage im Schaltplan."""
        result = []
        for pin in self.definition.pins:
            px, py = self.place(pin.x, pin.y)
            result.append((pin, (int(round(px)), int(round(py)))))
        return result

    def bounds(self) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = self.definition.bounds()
        corners = [self.place(x, y) for x, y in ((x1, y1), (x2, y1), (x1, y2), (x2, y2))]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class Wire:
    """Ein gerades Leitungsstück zwischen zwei Rasterpunkten."""
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def ends(self) -> tuple[tuple[int, int], tuple[int, int]]:
        return (self.x1, self.y1), (self.x2, self.y2)

    def contains(self, point, inside_only: bool = False) -> bool:
        """Liegt der Rasterpunkt auf dem Stück? (`inside_only`: ohne die beiden Enden)"""
        px, py = point
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        if (px - self.x1) * dy - (py - self.y1) * dx != 0:
            return False
        if not (min(self.x1, self.x2) <= px <= max(self.x1, self.x2)
                and min(self.y1, self.y2) <= py <= max(self.y1, self.y2)):
            return False
        return not (inside_only and point in self.ends)


@dataclass
class Label:
    """Netzlabel: zeigt den Namen des Netzes an der Stelle; gleichnamige Netze sind verbunden."""
    x: int
    y: int
    name: str
    rotation: int = 0


@dataclass
class Net:
    name: str
    pins: list = field(default_factory=list)       # [(Bauteil, Pin, Lage)] ohne Netzsymbole
    points: set = field(default_factory=set)       # Rasterpunkte des Netzes
    wires: list = field(default_factory=list)      # Nummern der Leitungen
    named: bool = False                            # Name aus Label/Netzsymbol (sonst N$…)
    sources: list = field(default_factory=list)    # Netzsymbole/Quellen-Pins (für die Prüfung)
    all_names: list = field(default_factory=list)  # alle Namen aus Labels/Netzsymbolen (mehrere: Warnung)

    def connections(self) -> int:
        """Zahl der Anschlüsse: Bauteil-Pins und Netzsymbole (ein Quellen-Pin zählt nur einmal)."""
        return len(self.pins) + sum(1 for part, _pin in self.sources if part.definition.power)


@dataclass
class Problem:
    level: str              # "Fehler" oder "Warnung"
    message: str
    at: tuple               # Rasterpunkt zum Hinspringen


def _natural(text: str):
    """Sortierschlüssel „R2“ vor „R10“."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


class _Union:
    """Vereinigungs-Suche über beliebige Schlüssel (Rasterpunkte, Pins)."""

    def __init__(self):
        self.parent: dict = {}

    def find(self, key):
        self.parent.setdefault(key, key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:
            self.parent[key], key = root, self.parent[key]
        return root

    def join(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


class Schematic:
    """Ein Schaltplanblatt mit Bauteilen, Leitungen und Labels."""

    def __init__(self, title: str = ""):
        self.title = title
        self.parts: list[Part] = []
        self.wires: list[Wire] = []
        self.labels: list[Label] = []

    # --- Datei ----------------------------------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {"titel": self.title, "bauteile": [asdict(p) for p in self.parts],
                "leitungen": [asdict(w) for w in self.wires], "labels": [asdict(l) for l in self.labels]}

    @classmethod
    def from_dict(cls, data: dict | None) -> "Schematic":
        sheet = cls((data or {}).get("titel", ""))
        for item in (data or {}).get("bauteile", []):
            if item.get("symbol") in LIBRARY:          # unbekannte Symbole (neuere Fassung) fallen weg
                sheet.parts.append(Part(**item))
        sheet.wires = [Wire(**item) for item in (data or {}).get("leitungen", [])]
        sheet.labels = [Label(**item) for item in (data or {}).get("labels", [])]
        return sheet

    def is_empty(self) -> bool:
        return not (self.parts or self.wires or self.labels)

    # --- Bearbeiten -----------------------------------------------------------------------------------------

    def next_ref(self, prefix: str) -> str:
        """Kleinste freie Bauteilnummer zum Kennbuchstaben (R1, R2 …); Netzsymbole bekommen #PWR…"""
        prefix = prefix or "#PWR"
        used = {p.ref for p in self.parts}
        number = 1
        while f"{prefix}{number}" in used:
            number += 1
        return f"{prefix}{number}"

    def add_part(self, symbol: str, x: int, y: int, rotation: int = 0, mirror: bool = False,
                 value: str | None = None) -> Part:
        definition = LIBRARY[symbol]
        part = Part(self.next_ref(definition.prefix), symbol, definition.value if value is None else value,
                    int(x), int(y), rotation % 360, mirror)
        self.parts.append(part)
        return part

    def add_wire(self, start, end) -> Wire | None:
        """Leitung von `start` nach `end` (Rasterpunkte); Nulllängen und doppelte Stücke entstehen nicht."""
        start, end = tuple(int(c) for c in start), tuple(int(c) for c in end)
        if start == end:
            return None
        for wire in self.wires:
            if {wire.ends[0], wire.ends[1]} == {start, end}:
                return None
        wire = Wire(*start, *end)
        self.wires.append(wire)
        return wire

    def attached_wire_ends(self, parts: list[Part]) -> list[tuple[int, int]]:
        """(Leitung, Ende 0/1), deren Ende an einem Pin der Bauteile liegt – die gehen beim Verschieben mit
        (Gummiband wie in Fusion)."""
        points = {position for part in parts for _pin, position in part.pins()}
        found = []
        for number, wire in enumerate(self.wires):
            for end, point in enumerate(wire.ends):
                if point in points:
                    found.append((number, end))
        return found

    def move_wire_end(self, number: int, end: int, point) -> None:
        wire = self.wires[number]
        if end == 0:
            wire.x1, wire.y1 = point
        else:
            wire.x2, wire.y2 = point

    def remove_zero_wires(self) -> None:
        self.wires = [w for w in self.wires if w.ends[0] != w.ends[1]]

    def annotate(self) -> dict[str, str]:
        """Bauteilnummern neu vergeben, je Kennbuchstabe von oben links nach unten rechts (Zeilen von 10 Rastern).
        Rückgabe: alt → neu."""
        renames = {}
        groups: dict[str, list[Part]] = {}
        for part in self.parts:
            groups.setdefault(part.definition.prefix or "#PWR", []).append(part)
        for prefix, parts in groups.items():
            parts.sort(key=lambda p: (p.y // 10, p.x, p.y))
            for number, part in enumerate(parts, start=1):
                new = f"{prefix}{number}"
                if part.ref != new:
                    renames[part.ref] = new
                part.ref = new
        return renames

    # --- Netze ----------------------------------------------------------------------------------------------

    def nets(self) -> list[Net]:
        """Alle Netze (siehe Moduldoku); unverbundene einzelne Pins bilden kein Netz."""
        union = _Union()
        wire_ends = {point for wire in self.wires for point in wire.ends}
        # Leitungen: beide Enden; ein Leitungsende oder Label mitten auf einer Leitung verbindet sich mit ihr
        attach = wire_ends | {(label.x, label.y) for label in self.labels}
        for number, wire in enumerate(self.wires):
            union.join(("w", number), wire.ends[0])
            union.join(("w", number), wire.ends[1])
            for point in attach:
                if wire.contains(point, inside_only=True):
                    union.join(("w", number), point)
        pin_points = []
        for part in self.parts:
            for pin, position in part.pins():
                union.join((part.ref, pin.number), position)
                pin_points.append((part, pin, position))
        # Namen je Gruppe aus Labels und Netzsymbolen
        names: dict = {}
        for label in self.labels:
            if label.name:
                names.setdefault(union.find((label.x, label.y)), []).append(label.name)
        for part, pin, position in pin_points:
            if part.definition.power and part.value:
                names.setdefault(union.find(position), []).append(part.value)
        # gleichnamige Gruppen verbinden (globale Netze)
        by_name: dict[str, object] = {}
        for root, found in list(names.items()):
            for name in found:
                if name in by_name:
                    union.join(by_name[name], root)
                else:
                    by_name[name] = root
        groups: dict = {}
        for key in list(union.parent):
            groups.setdefault(union.find(key), []).append(key)
        nets = []
        for root, keys in groups.items():
            net = Net("")
            for key in keys:
                if isinstance(key[0], int):
                    net.points.add(key)
                elif key[0] == "w":
                    net.wires.append(key[1])
            for part, pin, position in pin_points:
                if union.find(position) == root:
                    if part.definition.power:
                        net.sources.append((part, pin))
                    else:
                        net.pins.append((part, pin, position))
                        if pin.kind == "quelle":
                            net.sources.append((part, pin))
            own = sorted({name for r, found in names.items() if union.find(r) == root for name in found},
                         key=_natural)
            if own:
                net.name, net.named, net.all_names = own[0], True, own
            # ein einzelner Pin ohne Leitung, Label oder Partner ist kein Netz
            if not net.wires and net.connections() <= 1 and not own:
                continue
            nets.append(net)
        unnamed = sorted((net for net in nets if not net.named), key=lambda n: min(n.points) if n.points else (0, 0))
        for number, net in enumerate(unnamed, start=1):
            net.name = f"N${number}"
        nets.sort(key=lambda n: _natural(n.name))
        return nets

    def net_at(self, point) -> Net | None:
        """Das Netz, zu dem dieser Rasterpunkt gehört (auf einer Leitung oder an einem Pin)."""
        point = tuple(point)
        for net in self.nets():
            if point in net.points or any(self.wires[n].contains(point) for n in net.wires):
                return net
        return None

    def rename_net(self, point, name: str) -> bool:
        """Netz an der Stelle umbenennen: vorhandene Labels des Netzes bekommen den Namen, sonst entsteht ein
        unsichtbares Namensschild (Label ohne Anzeige: Drehung −1) am Punkt. False: kein Netz an der Stelle."""
        net = self.net_at(point)
        if net is None or not name:
            return False
        changed = False
        for label in self.labels:
            if (label.x, label.y) in net.points:        # Labelpunkte gehören zum Netz, an dem sie sitzen
                label.name = name
                changed = True
        if not changed:
            self.labels.append(Label(int(point[0]), int(point[1]), name, rotation=-1))
        return True

    def junctions(self) -> list[tuple[int, int]]:
        """Verbindungspunkte (gefüllte Punkte): wo drei oder mehr Leitungs-/Pin-Enden zusammenkommen oder ein
        Leitungsende mitten auf einer Leitung liegt."""
        count: dict = {}
        for wire in self.wires:
            for point in wire.ends:
                count[point] = count.get(point, 0) + 1
        for part in self.parts:
            for _pin, position in part.pins():
                if position in count:
                    count[position] += 1
        dots = {point for point, n in count.items() if n >= 3}
        for point in count:
            if any(wire.contains(point, inside_only=True) for wire in self.wires):
                dots.add(point)
        return sorted(dots)

    # --- Prüfung (ERC) --------------------------------------------------------------------------------------

    def check(self) -> list[Problem]:
        """Elektrische Prüfung wie Fusions ERC: doppelte Namen, offene Pins, Ausgänge gegeneinander, Versorgung
        fehlt, lose Leitungsenden, Netze mit nur einem Anschluss, fehlende Werte."""
        problems: list[Problem] = []
        seen: dict[str, Part] = {}
        for part in self.parts:
            if part.ref in seen and not part.definition.power:
                problems.append(Problem("Fehler", f"Bauteilnummer {part.ref} kommt mehrfach vor", (part.x, part.y)))
            seen[part.ref] = part
            if part.definition.value and not part.value and not part.definition.power:
                problems.append(Problem("Warnung", f"{part.ref} hat keinen Wert", (part.x, part.y)))
        nets = self.nets()
        connected = {(part.ref, pin.number) for net in nets for part, pin, _p in net.pins}
        connected |= {(part.ref, pin.number) for net in nets for part, pin in net.sources}
        for part in self.parts:
            for pin, position in part.pins():
                if (part.ref, pin.number) in connected:
                    continue
                if part.definition.power:
                    problems.append(Problem("Warnung", f"Netzsymbol {part.value} ist nicht angeschlossen", position))
                elif pin.kind == "versorgung":
                    problems.append(Problem("Fehler", f"Versorgungspin {part.ref}.{pin.name} ist offen", position))
                else:
                    problems.append(Problem("Warnung", f"Pin {part.ref}.{pin.name} ({pin.number}) ist offen",
                                            position))
        for net in nets:
            outputs = [(part, pin) for part, pin, _p in net.pins if pin.kind == "ausgang"]
            where = min(net.points) if net.points else (0, 0)
            if len(outputs) > 1:
                names = ", ".join(f"{p.ref}.{q.name}" for p, q in outputs)
                problems.append(Problem("Fehler", f"Netz {net.name}: Ausgänge gegeneinander ({names})", where))
            supplies = [pin for _part, pin, _p in net.pins if pin.kind == "versorgung"]
            if supplies and not net.sources and not net.named:
                problems.append(Problem("Fehler", f"Netz {net.name}: Versorgungspin ohne Quelle", where))
            if net.connections() == 1:
                problems.append(Problem("Warnung", f"Netz {net.name} hat nur einen Anschluss", where))
            if len(net.all_names) > 1:
                problems.append(Problem("Warnung", f"Netz hat mehrere Namen: {', '.join(net.all_names)}", where))
        # lose Leitungsenden: an nichts angeschlossen
        pin_points = {position for part in self.parts for _pin, position in part.pins()}
        label_points = {(label.x, label.y) for label in self.labels}
        for number, wire in enumerate(self.wires):
            for point in wire.ends:
                touching = sum(1 for other in self.wires if point in other.ends) - 1
                on_wire = any(other.contains(point, inside_only=True) for other in self.wires)
                if not touching and not on_wire and point not in pin_points and point not in label_points:
                    problems.append(Problem("Warnung", "Leitungsende ohne Verbindung", point))
        problems.sort(key=lambda p: (p.level != "Fehler", _natural(p.message)))
        return problems

    # --- Ausgaben -------------------------------------------------------------------------------------------

    def netlist_text(self) -> str:
        """Netzliste zum Lesen: je Netz die Anschlüsse als Bauteil-Pin."""
        lines = [f"Netzliste{(' – ' + self.title) if self.title else ''}", ""]
        for net in self.nets():
            pins = sorted((f"{part.ref}-{pin.number}" + (f" ({pin.name})" if pin.name != pin.number else "")
                           for part, pin, _p in net.pins), key=_natural)
            lines.append(f"{net.name}: {', '.join(pins) if pins else '–'}")
        return "\n".join(lines) + "\n"

    def bom_rows(self) -> list[dict]:
        """Stückliste: gleiche Bauteile (Symbol und Wert) zusammengefasst, Netzsymbole nicht."""
        groups: dict[tuple, list[Part]] = {}
        for part in self.parts:
            if not part.definition.power:
                groups.setdefault((part.symbol, part.value), []).append(part)
        rows = []
        for (symbol, value), parts in groups.items():
            refs = sorted((p.ref for p in parts), key=_natural)
            rows.append({"Anzahl": len(parts), "Bauteile": ", ".join(refs), "Wert": value, "Bauteil": symbol,
                         "Beschreibung": LIBRARY[symbol].description})
        rows.sort(key=lambda row: _natural(row["Bauteile"]))
        return rows

    def bom_csv(self) -> str:
        """Stückliste als CSV (Semikolon wie in deutschen Tabellenprogrammen)."""
        out = io.StringIO()
        writer = csv.DictWriter(out, ["Anzahl", "Bauteile", "Wert", "Bauteil", "Beschreibung"], delimiter=";")
        writer.writeheader()
        writer.writerows(self.bom_rows())
        return out.getvalue()

    def spice(self) -> str:
        """SPICE-Netzliste (z. B. für ngspice). Masse (GND) wird Knoten 0. Bauteile ohne SPICE-Entsprechung
        stehen als Kommentar darin; für Dioden und Transistoren ist der Wert der Modellname."""
        nets = self.nets()
        node: dict[tuple, str] = {}
        for net in nets:
            name = "0" if net.name.upper() in GROUND_NAMES else re.sub(r"[^\w+-]", "_", net.name)
            for part, pin, _p in net.pins:
                node[(part.ref, pin.number)] = name
        lines = [f"* {self.title or 'WolfCAD-Schaltplan'}", "* erzeugt von WolfCAD"]
        order = {"Q": ("C", "B", "E"), "M": ("D", "G", "S"), "D": ("A", "K"), "V": ("+", "−"), "C": None,
                 "R": None, "L": None}
        for part in sorted(self.parts, key=lambda p: _natural(p.ref)):
            letter = part.definition.spice
            if part.definition.power:
                continue
            if not letter:
                lines.append(f"* {part.ref} ({part.symbol}, {part.value or 'ohne Wert'}): kein SPICE-Modell")
                continue
            pins = part.definition.pins
            wanted = order.get(letter)
            if wanted:
                by_name = {pin.name: pin for pin in pins}
                pins = [by_name[name] for name in wanted if name in by_name]
            nodes = [node.get((part.ref, pin.number), f"NC_{part.ref}_{pin.number}") for pin in pins]
            if letter == "M":
                nodes.append(nodes[-1])         # Bulk an Source
            value = _spice_value(part.value)
            name = part.ref if part.ref.upper().startswith(letter) else f"{letter}{part.ref}"
            if letter == "V":
                value = f"DC {value}"
            lines.append(f"{name} {' '.join(nodes)} {value}".rstrip())
        lines.append(".end")
        return "\n".join(lines) + "\n"


def _spice_value(value: str) -> str:
    """„10µ“ → „10u“, „4,7k“ → „4.7k“, „5V“ → „5“, „8Ω“ → „8“; Modellnamen bleiben."""
    text = value.strip().replace(",", ".").replace("µ", "u").replace("Ω", "")
    match = re.fullmatch(r"([0-9.]+)\s*([pnumkKMG]?)(?:[VAFHvafh]|Hz)?", text)
    if match:
        number, unit = match.groups()
        unit = {"M": "Meg", "K": "k"}.get(unit, unit)
        return number + unit
    return text or "1"
