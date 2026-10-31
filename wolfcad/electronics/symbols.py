# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Schaltzeichen für den Schaltplan (Runde 98) – nach DIN EN 60617 (Widerstand als Rechteck, Spule als gefülltes
Rechteck), eigene Zeichnungen, keine fremden Bibliotheken.

Koordinaten in Rastereinheiten (1 Einheit = 2,54 mm = 0,1 Zoll, das übliche Raster für Schaltpläne); y wächst nach
unten wie auf dem Bildschirm. Jedes Symbol besteht aus Zeichenelementen und Anschlüssen (Pins). Die Anschlüsse
liegen immer auf ganzen Rasterpunkten – nur dort verbinden sich Leitungen. Die Pin-Linie (vom Anschluss zum
Körper) gehört zur Zeichnung, der Anschluss selbst ist ihr äußeres Ende.

Zeichenelemente: ("line", x1, y1, x2, y2) · ("rect", x1, y1, x2, y2, gefüllt) · ("circle", x, y, r, gefüllt) ·
("arc", x, y, r, Startwinkel, Winkel) in Grad gegen den Uhrzeigersinn wie Qt · ("poly", [(x, y) …], gefüllt) ·
("text", x, y, Text, Größe).

Pin-Arten (für die elektrische Prüfung): "passiv", "eingang", "ausgang", "ein/aus", "versorgung" (Versorgungs-
eingang eines Bauteils, z. B. V+ am Operationsverstärker), "quelle" (liefert Versorgung: Netzsymbole wie GND und
+5V, Spannungsregler-Ausgang, Batterie).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pin:
    number: str             # Anschlussnummer (wie im Bauteil-Datenblatt)
    name: str               # Anschlussname (A, K, B, C, E, +, − …)
    x: int
    y: int
    kind: str = "passiv"
    show_name: bool = False     # Namen am Pin anzeigen (bei ICs und Operationsverstärkern)


@dataclass(frozen=True)
class Symbol:
    name: str               # Name in der Bibliothek (deutsch), zugleich Schlüssel in der Datei
    prefix: str             # Kennbuchstabe der Bauteilnummer (R1, C3, U2 …)
    category: str           # Gruppe in der Bibliothek
    graphics: tuple
    pins: tuple
    value: str = ""         # Vorgabewert (z. B. „10k“)
    description: str = ""
    power: bool = False     # Netzsymbol (GND, +5V …): der Wert benennt das Netz, keine Bauteilnummer
    spice: str = ""         # SPICE-Kennbuchstabe (R, C, L, D, Q, M, V) – leer: nicht in der SPICE-Netzliste
    keywords: str = ""      # zusätzliche Suchbegriffe (englisch u. a.)
    name_at: tuple = (0.0, -1.6)        # Lage der Bauteilnummer bzw. des Werts (vor dem Drehen)
    value_at: tuple = (0.0, 1.6)

    def bounds(self) -> tuple[float, float, float, float]:
        """Umriss (x1, y1, x2, y2) aus Zeichnung und Anschlüssen – für die Auswahl per Klick."""
        xs, ys = [], []
        for item in self.graphics:
            kind = item[0]
            if kind in ("line", "rect"):
                xs += [item[1], item[3]]
                ys += [item[2], item[4]]
            elif kind in ("circle", "arc"):
                xs += [item[1] - item[3], item[1] + item[3]]
                ys += [item[2] - item[3], item[2] + item[3]]
            elif kind == "poly":
                xs += [p[0] for p in item[1]]
                ys += [p[1] for p in item[1]]
            elif kind == "text":
                xs.append(item[1])
                ys.append(item[2])
        xs += [p.x for p in self.pins]
        ys += [p.y for p in self.pins]
        return min(xs), min(ys), max(xs), max(ys)


# --- Bausteine für wiederkehrende Formen --------------------------------------------------------------------------

def _two_pin(body: list, name: str, prefix: str, category: str, value: str = "", description: str = "",
             pins=("1", "2"), names=("1", "2"), spice: str = "", keywords: str = "", kinds=("passiv", "passiv"),
             span: int = 2, name_at: tuple = (0.0, -1.6)) -> Symbol:
    """Zweipol waagerecht: Anschlüsse bei (−span, 0) und (span, 0), Körper dazwischen."""
    return Symbol(name, prefix, category, tuple(body), (
        Pin(pins[0], names[0], -span, 0, kinds[0]), Pin(pins[1], names[1], span, 0, kinds[1])),
        value=value, description=description, spice=spice, keywords=keywords, name_at=name_at)


def _arrows(x: float, y: float, outward: bool) -> list:
    """Zwei kleine Pfeile schräg nach oben (LED: heraus, Fotodiode: hinein)."""
    items = []
    for dx in (0.0, 0.55):
        if outward:
            start, end = (x + dx, y), (x + dx + 0.7, y - 0.7)
        else:
            start, end = (x + dx + 0.7, y - 0.7), (x + dx, y)
        items.append(("line", *start, *end))
        ex, ey = end
        sx, sy = start
        # Pfeilspitze als kleines gefülltes Dreieck am Ende
        ux, uy = (ex - sx) / 0.99, (ey - sy) / 0.99
        items.append(("poly", [(ex, ey), (ex - ux * 0.3 - uy * 0.15, ey - uy * 0.3 + ux * 0.15),
                               (ex - ux * 0.3 + uy * 0.15, ey - uy * 0.3 - ux * 0.15)], True))
    return items


def _diode_body(extra: list | None = None) -> list:
    """Diode A → K: Dreieck mit Strich (Kathode rechts)."""
    return [("line", -2, 0, -0.6, 0), ("line", 0.6, 0, 2, 0),
            ("poly", [(-0.6, -0.7), (-0.6, 0.7), (0.6, 0)], True), ("line", 0.6, -0.7, 0.6, 0.7)] + (extra or [])


def _transistor(pnp: bool) -> Symbol:
    """Bipolartransistor: Basis links (−2, 0), Kollektor oben (1, −2), Emitter unten (1, 2); Pfeil am Emitter."""
    body = [("circle", 0.3, 0, 1.45, False), ("line", -2, 0, -0.4, 0), ("line", -0.4, -0.9, -0.4, 0.9),
            ("line", -0.4, -0.4, 1, -1.2), ("line", 1, -1.2, 1, -2), ("line", -0.4, 0.4, 1, 1.2),
            ("line", 1, 1.2, 1, 2)]
    if pnp:
        # Pfeil zur Basis hin (in den Transistor hinein)
        body.append(("poly", [(-0.25, 0.48), (0.35, 0.62), (0.05, 1.0)], True))
    else:
        body.append(("poly", [(1, 1.2), (0.35, 0.65), (0.6, 1.25)], True))
    pins = (Pin("1", "B", -2, 0), Pin("2", "C", 1, -2), Pin("3", "E", 1, 2))
    name = "PNP-Transistor" if pnp else "NPN-Transistor"
    return Symbol(name, "Q", "Halbleiter", tuple(body), pins, value="BC557" if pnp else "BC547",
                  description=f"Bipolartransistor ({'PNP' if pnp else 'NPN'})", spice="Q",
                  keywords="transistor bjt " + ("pnp" if pnp else "npn"), name_at=(2.6, -0.6), value_at=(2.6, 0.6))


def _mosfet(p_channel: bool) -> Symbol:
    """MOSFET (Anreicherung): Gate links (−2, 0), Drain oben (1, −2), Source unten (1, 2)."""
    body = [("circle", 0.3, 0, 1.45, False), ("line", -2, 0, -0.7, 0), ("line", -0.7, -0.8, -0.7, 0.8)]
    for y in (-0.7, 0.0, 0.7):
        body.append(("line", -0.35, y - 0.3, -0.35, y + 0.3))       # unterbrochener Kanal (Anreicherung)
    body += [("line", -0.35, -0.7, 1, -0.7), ("line", 1, -0.7, 1, -2), ("line", -0.35, 0.7, 1, 0.7),
             ("line", 1, 0.7, 1, 2), ("line", -0.35, 0, 1, 0), ("line", 1, 0, 1, 0.7)]
    if p_channel:
        body.append(("poly", [(0.35, 0), (-0.1, -0.25), (-0.1, 0.25)], True))
    else:
        body.append(("poly", [(-0.35, 0), (0.1, -0.25), (0.1, 0.25)], True))
    pins = (Pin("1", "G", -2, 0, "eingang"), Pin("2", "D", 1, -2), Pin("3", "S", 1, 2))
    name = "P-MOSFET" if p_channel else "N-MOSFET"
    return Symbol(name, "Q", "Halbleiter", tuple(body), pins, value="IRLML6402" if p_channel else "IRLML2502",
                  description=f"MOSFET, {'P' if p_channel else 'N'}-Kanal, Anreicherung", spice="M",
                  keywords="mosfet fet transistor", name_at=(2.6, -0.6), value_at=(2.6, 0.6))


def _header(count: int) -> Symbol:
    """Stiftleiste mit `count` Kontakten untereinander, Anschlüsse links."""
    body = [("rect", 0, -0.8, 1.6, (count - 1) * 2 + 0.8, False)]
    pins = []
    for k in range(count):
        y = k * 2
        body += [("line", -1, y, 0.5, y), ("circle", 0.8, y, 0.3, False)]
        pins.append(Pin(str(k + 1), str(k + 1), -1, y))
    return Symbol(f"Stiftleiste {count}-polig", "J", "Steckverbinder", tuple(body), tuple(pins),
                  description=f"Stiftleiste / Steckverbinder mit {count} Kontakten",
                  keywords="header connector stecker buchse pinheader",
                  name_at=(0.8, -1.6), value_at=(0.8, (count - 1) * 2 + 1.6))


def _ic(count: int) -> Symbol:
    """Allgemeines IC im DIL-Gehäuse: Pins 1 … n/2 links von oben nach unten, die übrigen rechts von unten nach oben
    (Zählweise wie am Gehäuse)."""
    half = count // 2
    height = (half - 1) * 2
    body = [("rect", -2, -1, 2, height + 1, False)]
    pins = []
    for k in range(half):
        y = k * 2
        body.append(("line", -3, y, -2, y))
        pins.append(Pin(str(k + 1), str(k + 1), -3, y, "ein/aus", True))
    for k in range(half):
        y = height - k * 2
        body.append(("line", 2, y, 3, y))
        pins.append(Pin(str(half + k + 1), str(half + k + 1), 3, y, "ein/aus", True))
    body.append(("arc", 0, -1, 0.5, 180, 180))                     # Gehäusekerbe oben
    return Symbol(f"IC {count}-polig", "U", "ICs", tuple(body), tuple(pins), value="IC",
                  description=f"Allgemeiner Baustein mit {count} Anschlüssen (DIL)", keywords="ic chip dip dil",
                  name_at=(0, -2), value_at=(0, height + 2))


def _power(name: str, value: str, ground: bool) -> Symbol:
    """Netzsymbol: ein Anschluss bei (0, 0); der Wert benennt das Netz (alle gleichnamigen sind verbunden)."""
    if ground:
        body = [("line", 0, 0, 0, 1), ("line", -1, 1, 1, 1), ("line", -0.6, 1.35, 0.6, 1.35),
                ("line", -0.25, 1.7, 0.25, 1.7)]
        value_at = (0, 2.6)
    else:
        body = [("line", 0, 0, 0, -1), ("line", -0.8, -1, 0.8, -1)]
        value_at = (0, -1.8)
    return Symbol(name, "", "Versorgung", tuple(body), (Pin("1", value, 0, 0, "quelle"),), value=value,
                  description=f"Netzsymbol {value}: verbindet alle Stellen mit diesem Netznamen", power=True,
                  keywords="power supply versorgung netz " + value.lower(), name_at=(0, 0), value_at=value_at)


# --- Die Bibliothek ----------------------------------------------------------------------------------------------

def _build() -> dict[str, Symbol]:
    symbols: list[Symbol] = []
    add = symbols.append
    # Passive Bauteile
    add(_two_pin([("line", -2, 0, -1.2, 0), ("rect", -1.2, -0.45, 1.2, 0.45, False), ("line", 1.2, 0, 2, 0)],
                 "Widerstand", "R", "Passiv", "10k", "Widerstand (DIN-Rechteck)", spice="R",
                 keywords="resistor widerstand ohm"))
    add(Symbol("Potentiometer", "R", "Passiv",
               (("line", -2, 0, -1.2, 0), ("rect", -1.2, -0.45, 1.2, 0.45, False), ("line", 1.2, 0, 2, 0),
                ("line", 0, -2, 0, -0.75), ("poly", [(0, -0.45), (-0.3, -0.95), (0.3, -0.95)], True)),
               (Pin("1", "1", -2, 0), Pin("2", "S", 0, -2), Pin("3", "3", 2, 0)), value="10k",
               description="Einstellbarer Widerstand mit Schleifer", keywords="potentiometer poti trimmer",
               name_at=(0, 1.6), value_at=(0, 2.8)))
    add(_two_pin([("line", -2, 0, -0.3, 0), ("line", -0.3, -0.9, -0.3, 0.9), ("line", 0.3, -0.9, 0.3, 0.9),
                  ("line", 0.3, 0, 2, 0)],
                 "Kondensator", "C", "Passiv", "100n", "Kondensator (ungepolt)", spice="C",
                 keywords="capacitor kondensator keramik"))
    add(_two_pin([("line", -2, 0, -0.3, 0), ("line", -0.3, -0.9, -0.3, 0.9), ("rect", 0.3, -0.9, 0.6, 0.9, True),
                  ("line", 0.6, 0, 2, 0), ("line", -1.1, -0.9, -0.5, -0.9), ("line", -0.8, -1.2, -0.8, -0.6)],
                 "Elko", "C", "Passiv", "10µ", "Elektrolytkondensator (gepolt, + links)", pins=("1", "2"),
                 names=("+", "−"), spice="C", keywords="electrolytic capacitor elko gepolt polarized"))
    add(_two_pin([("line", -2, 0, -1.2, 0), ("rect", -1.2, -0.4, 1.2, 0.4, True), ("line", 1.2, 0, 2, 0)],
                 "Spule", "L", "Passiv", "10µ", "Spule / Induktivität (DIN)", spice="L",
                 keywords="inductor spule drossel coil"))
    add(_two_pin([("line", -2, 0, -1.2, 0), ("rect", -1.2, -0.4, 1.2, 0.4, False), ("line", -1.2, 0, 1.2, 0),
                  ("line", 1.2, 0, 2, 0)],
                 "Sicherung", "F", "Passiv", "1A", "Schmelzsicherung", keywords="fuse sicherung"))
    add(_two_pin([("line", -2, 0, -0.9, 0), ("line", -0.9, -0.6, -0.9, 0.6), ("rect", -0.5, -0.8, 0.5, 0.8, False),
                  ("line", 0.9, -0.6, 0.9, 0.6), ("line", 0.9, 0, 2, 0)],
                 "Quarz", "Y", "Passiv", "16MHz", "Schwingquarz", keywords="crystal quarz oszillator xtal"))
    # Halbleiter
    add(_two_pin(_diode_body(), "Diode", "D", "Halbleiter", "1N4148", "Diode (Anode links, Kathode rechts)",
                 names=("A", "K"), spice="D", keywords="diode gleichrichter"))
    add(_two_pin(_diode_body([("line", 0.6, -0.7, 0.9, -0.9), ("line", 0.6, 0.7, 0.3, 0.9)]), "Z-Diode", "D",
                 "Halbleiter", "5V1", "Zenerdiode", names=("A", "K"), spice="D", keywords="zener z-diode"))
    add(_two_pin(_diode_body(_arrows(0.0, -1.0, True)), "LED", "D", "Halbleiter", "rot", "Leuchtdiode",
                 names=("A", "K"), spice="D", keywords="led leuchtdiode light", name_at=(-1.2, -1.6)))
    add(_two_pin(_diode_body(_arrows(-0.9, -1.2, False)), "Fotodiode", "D", "Halbleiter", "BPW34",
                 "Fotodiode (Licht fällt ein)", names=("A", "K"), spice="D", keywords="photodiode fotodiode",
                 name_at=(1.2, -1.6)))
    add(_transistor(False))
    add(_transistor(True))
    add(_mosfet(False))
    add(_mosfet(True))
    # ICs
    add(Symbol("Operationsverstärker", "U", "ICs",
               (("poly", [(-2, -2.2), (-2, 2.2), (2.2, 0)], False), ("line", -3, -1, -2, -1), ("line", -3, 1, -2, 1),
                ("line", 2.2, 0, 3, 0), ("line", 0, -2, 0, -1.05), ("line", 0, 2, 0, 1.05),
                ("text", -1.5, -1, "−", 0.9), ("text", -1.5, 1, "+", 0.9)),
               (Pin("2", "−", -3, -1, "eingang"), Pin("3", "+", -3, 1, "eingang"), Pin("1", "OUT", 3, 0, "ausgang"),
                Pin("8", "V+", 0, -2, "versorgung"), Pin("4", "V−", 0, 2, "versorgung")),
               value="LM358", description="Operationsverstärker mit Versorgungsanschlüssen",
               keywords="opamp op-amp operationsverstaerker comparator", name_at=(1.6, -2.4), value_at=(1.6, 2.4)))
    add(Symbol("Spannungsregler", "U", "ICs",
               (("rect", -2, -1, 2, 1, False), ("line", -3, 0, -2, 0), ("line", 2, 0, 3, 0), ("line", 0, 1, 0, 2),
                ("text", -1.3, 0, "IN", 0.6), ("text", 1.2, 0, "OUT", 0.6), ("text", 0, 0.6, "GND", 0.5)),
               (Pin("1", "IN", -3, 0, "versorgung"), Pin("2", "GND", 0, 2, "versorgung"),
                Pin("3", "OUT", 3, 0, "quelle")),
               value="7805", description="Linearer Festspannungsregler (IN, GND, OUT)",
               keywords="regulator spannungsregler ldo 7805 ams1117", name_at=(0, -1.8), value_at=(0, 2.8)))
    for count in (8, 14, 16):
        add(_ic(count))
    # Schalter und Elektromechanik
    add(_two_pin([("line", -2, 0, -0.8, 0), ("circle", -0.8, 0, 0.12, True), ("line", -0.8, 0, 0.9, -0.8),
                  ("circle", 0.9, 0, 0.12, True), ("line", 0.9, 0, 2, 0)],
                 "Schalter", "S", "Schalter", "", "Schalter (Schließer)", keywords="switch schalter"))
    add(_two_pin([("line", -2, 0, -0.9, 0), ("line", 0.9, 0, 2, 0), ("line", -0.9, -0.5, 0.9, -0.5),
                  ("line", 0, -0.5, 0, -1.3), ("line", -0.5, -1.3, 0.5, -1.3), ("circle", -0.9, 0, 0.12, True),
                  ("circle", 0.9, 0, 0.12, True)],
                 "Taster", "S", "Schalter", "", "Taster (Schließer)", keywords="button taster push"))
    add(Symbol("Relais", "K", "Schalter",
               (("rect", -2.8, -0.8, -1.2, 0.8, False), ("line", -2, -2, -2, -0.8), ("line", -2, 0.8, -2, 2),
                ("line", -1.2, 0, 0.2, 0), ("line", 1, 2, 1, 0.6), ("line", 1, 0.6, 1.9, -1.2),
                ("line", 0, -2, 0, -1.2), ("line", 2, -2, 2, -1.2), ("circle", 0, -1.2, 0.1, True),
                ("circle", 2, -1.2, 0.1, True)),
               (Pin("1", "A1", -2, -2), Pin("2", "A2", -2, 2), Pin("3", "COM", 1, 2),
                Pin("4", "NC", 0, -2), Pin("5", "NO", 2, -2)),
               value="5V", description="Relais mit Wechsler (Spule A1/A2, Kontakte COM, NC, NO)",
               keywords="relay relais", name_at=(-4.0, -0.6), value_at=(-4.0, 0.6)))
    # Quellen und Verbraucher
    add(_two_pin([("line", -2, 0, -0.3, 0), ("line", -0.3, -1.1, -0.3, 1.1), ("line", 0.3, -0.5, 0.3, 0.5),
                  ("line", 0.3, 0, 2, 0), ("line", -1.2, -0.9, -0.6, -0.9), ("line", -0.9, -1.2, -0.9, -0.6)],
                 "Batterie", "B", "Quellen", "9V", "Batterie / Akku (+ links)", names=("+", "−"), spice="V",
                 kinds=("quelle", "quelle"), keywords="battery batterie akku cell"))
    add(_two_pin([("line", -2, 0, -1, 0), ("circle", 0, 0, 1, False), ("line", 1, 0, 2, 0),
                  ("line", -1, 0, 1, 0), ("line", -0.6, -0.5, -0.6, -0.1), ("line", -0.8, -0.3, -0.4, -0.3)],
                 "Spannungsquelle", "V", "Quellen", "5V", "Gleichspannungsquelle (+ links)", names=("+", "−"),
                 spice="V", kinds=("quelle", "quelle"), keywords="voltage source spannungsquelle dc"))
    add(_two_pin([("line", -2, 0, -1, 0), ("circle", 0, 0, 1, False), ("line", 1, 0, 2, 0),
                  ("text", 0, 0, "M", 1.0)],
                 "Motor", "M", "Verbraucher", "", "Gleichstrommotor", keywords="motor dc"))
    add(_two_pin([("line", -2, 0, -1, 0), ("circle", 0, 0, 1, False), ("line", 1, 0, 2, 0),
                  ("line", -0.7, -0.7, 0.7, 0.7), ("line", -0.7, 0.7, 0.7, -0.7)],
                 "Lampe", "H", "Verbraucher", "", "Glühlampe / Leuchte", keywords="lamp lampe light"))
    add(_two_pin([("line", -2, 0, -0.8, 0), ("rect", -0.8, -0.5, -0.2, 0.5, False),
                  ("poly", [(-0.2, -0.5), (0.8, -1.2), (0.8, 1.2), (-0.2, 0.5)], False), ("line", 0.8, 0, 2, 0)],
                 "Lautsprecher", "LS", "Verbraucher", "8Ω", "Lautsprecher", keywords="speaker lautsprecher buzzer"))
    # Steckverbinder
    for count in (2, 3, 4, 6, 8):
        add(_header(count))
    # Netzsymbole
    add(_power("Masse", "GND", True))
    for value in ("VCC", "+5V", "+3V3", "+12V"):
        add(_power(value, value, False))
    return {symbol.name: symbol for symbol in symbols}


LIBRARY: dict[str, Symbol] = _build()
CATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(symbol.category for symbol in LIBRARY.values()))


def search(text: str) -> list[Symbol]:
    """Symbole, deren Name, Beschreibung oder Suchbegriffe alle Wörter von `text` enthalten (ohne Groß/klein)."""
    words = text.lower().split()
    found = []
    for symbol in LIBRARY.values():
        haystack = " ".join((symbol.name, symbol.description, symbol.keywords, symbol.prefix, symbol.value)).lower()
        if all(word in haystack for word in words):
            found.append(symbol)
    return found
