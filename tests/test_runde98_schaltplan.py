"""Runde 98: Schaltplan – Symbole, Netze (T-Stoß, Kreuzung, Labels, Netzsymbole), Prüfung (ERC), Ausgaben."""
from wolfcad.core.timeline import Timeline
from wolfcad.electronics import symbols
from wolfcad.electronics.schematic import Label, Schematic, _spice_value


def _led_circuit() -> Schematic:
    """Batterie (senkrecht) → R1 → LED → GND, Batterie − an GND."""
    s = Schematic("LED")
    s.add_part("Widerstand", 0, 0)
    s.add_part("LED", 6, 0)
    s.add_part("Masse", 14, 4)
    s.add_part("Batterie", -6, 6, rotation=90)
    for a, b in (((2, 0), (4, 0)), ((8, 0), (14, 0)), ((14, 0), (14, 4)), ((-2, 0), (-6, 0)), ((-6, 0), (-6, 4)),
                 ((-6, 8), (-6, 10)), ((-6, 10), (14, 10)), ((14, 10), (14, 4))):
        s.add_wire(a, b)
    return s


def _pins(net):
    return sorted(f"{part.ref}.{pin.name}" for part, pin, _p in net.pins)


def test_library_pins_on_grid_and_unique():
    assert len(symbols.LIBRARY) >= 35
    for symbol in symbols.LIBRARY.values():
        numbers = [pin.number for pin in symbol.pins]
        assert len(numbers) == len(set(numbers)), symbol.name
        assert all(float(pin.x).is_integer() and float(pin.y).is_integer() for pin in symbol.pins), symbol.name
    assert [s.name for s in symbols.search("npn")] == ["NPN-Transistor"]


def test_led_circuit_nets_and_clean_check():
    s = _led_circuit()
    nets = {net.name: _pins(net) for net in s.nets()}
    assert nets["GND"] == ["B1.−", "D1.K"]
    assert sorted(nets.values()) == sorted([["B1.−", "D1.K"], ["B1.+", "R1.1"], ["D1.A", "R1.2"]])
    assert s.check() == []
    assert (14, 4) in s.junctions()          # drei Enden an der Masse


def test_rotation_and_mirror_move_pins():
    s = Schematic()
    part = s.add_part("Widerstand", 10, 10, rotation=90)
    assert sorted(pos for _pin, pos in part.pins()) == [(10, 8), (10, 12)]
    part.mirror, part.rotation = True, 0
    assert [pos for _pin, pos in part.pins()] == [(12, 10), (8, 10)]


def test_t_junction_connects_but_crossing_does_not():
    s = Schematic()
    s.add_wire((0, 0), (10, 0))
    s.add_wire((5, 0), (5, 5))           # T-Stoß
    s.add_wire((8, -3), (8, 3))          # kreuzt nur
    nets = s.nets()
    together = [net for net in nets if 0 in net.wires][0]
    assert 1 in together.wires and 2 not in together.wires
    assert (5, 0) in s.junctions() and (8, 0) not in s.junctions()


def test_labels_and_power_symbols_join_nets_by_name():
    s = Schematic()
    r1 = s.add_part("Widerstand", 0, 0)
    r2 = s.add_part("Widerstand", 20, 0)
    s.add_wire((2, 0), (4, 0))
    s.add_wire((22, 0), (24, 0))
    s.labels += [Label(4, 0, "SIG"), Label(24, 0, "SIG")]
    s.add_part("+5V", -2, 0)
    s.add_part("+5V", 18, 0)
    nets = {net.name: _pins(net) for net in s.nets()}
    assert nets["SIG"] == ["R1.2", "R2.2"]
    assert nets["+5V"] == ["R1.1", "R2.1"]
    assert r1.ref == "R1" and r2.ref == "R2"


def test_rename_net_updates_labels_or_adds_hidden_name():
    s = _led_circuit()
    assert s.rename_net((3, 0), "LED_A")
    assert any(l.name == "LED_A" and l.rotation == -1 for l in s.labels)
    s.labels.append(Label(4, 0, "LED_A"))
    assert s.rename_net((4, 0), "ANODE")
    assert {l.name for l in s.labels} == {"ANODE"}


def test_check_finds_typical_mistakes():
    s = Schematic()
    s.add_part("Operationsverstärker", 0, 0)
    a = s.add_part("Widerstand", 10, 0)
    s.add_part("Widerstand", 20, 0).ref = a.ref
    s.add_wire((30, 0), (35, 0))
    messages = [(p.level, p.message) for p in s.check()]
    assert ("Fehler", "Versorgungspin U1.V+ ist offen") in messages
    assert ("Fehler", "Bauteilnummer R1 kommt mehrfach vor") in messages
    assert any(m == "Leitungsende ohne Verbindung" for _l, m in messages)
    assert any("U1.OUT" in m for _l, m in messages)


def test_two_outputs_on_one_net_is_an_error():
    s = Schematic()
    s.add_part("Operationsverstärker", 0, 0)
    s.add_part("Operationsverstärker", 0, 10)
    s.add_wire((3, 0), (3, 10))
    assert any("Ausgänge gegeneinander" in p.message for p in s.check())


def test_exports_netlist_bom_spice():
    s = _led_circuit()
    s.add_part("Widerstand", 0, 20, value="4,7k")
    assert "GND: B1-2 (−), D1-2 (K)" in s.netlist_text()
    bom = s.bom_csv()
    assert "Masse" not in bom and "Anzahl;Bauteile;Wert" in bom
    spice = s.spice()
    assert "R1 " in spice and "D1 " in spice and " 0 " in spice and "4.7k" in spice and spice.endswith(".end\n")
    assert _spice_value("10µ") == "10u" and _spice_value("1M") == "1Meg" and _spice_value("5V") == "5"


def test_annotate_numbers_left_to_right():
    s = Schematic()
    s.add_part("Widerstand", 30, 0)
    s.add_part("Widerstand", 0, 0)
    renames = s.annotate()
    assert sorted(renames.items()) == [("R1", "R2"), ("R2", "R1")]
    assert [(p.ref, p.x) for p in sorted(s.parts, key=lambda p: p.ref)] == [("R1", 0), ("R2", 30)]


def test_schematic_round_trip_in_document():
    timeline = Timeline()
    assert "schaltplan" not in timeline.to_document()        # ohne Schaltplan: Datei wie bisher
    timeline.schematic = _led_circuit().to_dict()
    again = Timeline.from_document(timeline.to_document())
    sheet = Schematic.from_dict(again.schematic)
    assert len(sheet.parts) == 4 and len(sheet.wires) == 8 and sheet.title == "LED"
    assert sheet.check() == []
