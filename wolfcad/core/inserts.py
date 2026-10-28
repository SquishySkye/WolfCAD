# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""DXF und SVG in eine Skizze einfügen (Fusion: Einfügen → DXF einfügen / SVG einfügen).

Beide Leser kommen ohne Zusatzbibliotheken aus und liefern Skizzenelemente (siehe sketch.py) in mm:
- DXF: LINE, CIRCLE, ARC, LWPOLYLINE und POLYLINE (auch mit Bogensegmenten über „bulge“), SPLINE, ELLIPSE.
- SVG: line, rect, circle, ellipse, polyline, polygon und path (M L H V C S Q T A Z, groß und klein) samt
  transform-Angaben; Kurven werden als glatte Splines durch Stützpunkte übernommen. Ein SVG-Pixel sind 25,4/96 mm,
  die y-Achse zeigt nach oben (SVG zählt nach unten).
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ElementTree

from . import sketch as sk
from .features import FeatureError

PX_TO_MM = 25.4 / 96.0


# --------------------------------------------------------------------------------------- DXF ---

def _bulge_arc(start: tuple, end: tuple, bulge: float) -> list:
    """Kreisbogen zwischen zwei Polylinienpunkten mit DXF-„bulge“ (tan eines Viertels des Öffnungswinkels)."""
    angle = 4 * math.atan(bulge)
    chord = math.dist(start, end)
    if abs(bulge) < 1e-12 or chord < 1e-12:
        return sk.line(*start, *end)
    radius = chord / (2 * math.sin(abs(angle) / 2))
    middle = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    # Mitte liegt senkrecht zur Sehne, auf der Seite, zu der sich der Bogen wölbt
    distance = math.sqrt(max(radius ** 2 - (chord / 2) ** 2, 0.0))
    nx, ny = -(end[1] - start[1]) / chord, (end[0] - start[0]) / chord
    side = 1.0 if (bulge > 0) == (abs(angle) < math.pi) else -1.0
    centre = (middle[0] + nx * distance * side, middle[1] + ny * distance * side)
    a = math.degrees(math.atan2(start[1] - centre[1], start[0] - centre[0]))
    b = math.degrees(math.atan2(end[1] - centre[1], end[0] - centre[0]))
    # Bögen laufen in der Skizze gegen den Uhrzeigersinn: bei negativem bulge Anfang und Ende tauschen
    return sk.arc(*centre, radius, a % 360, b % 360) if bulge > 0 else sk.arc(*centre, radius, b % 360, a % 360)


def _dxf_records(path: str) -> dict:
    """Die Einträge der DXF-Abschnitte: {"ENTITIES": [(Art, [(Code, Wert), …]), …], "BLOCKS": {Name: (Basis,
    Einträge)}}."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = [line.strip() for line in handle]
    except OSError as error:
        raise FeatureError(f"DXF-Datei nicht lesbar: {error}") from error
    pairs = [(lines[i], lines[i + 1]) for i in range(0, len(lines) - 1, 2)]
    entities: list = []
    blocks: dict = {}
    section, current, block = None, None, None
    expect_name = False

    def close() -> None:
        nonlocal current
        if current is not None:
            (block[2] if block is not None else entities).append(current)
        current = None

    for code, value in pairs:
        if code == "0" and value == "SECTION":
            close()
            expect_name = True
            continue
        if expect_name and code == "2":
            section, expect_name = value, False
            continue
        if code == "0" and value == "ENDSEC":
            close()
            section = None
            continue
        if section == "BLOCKS":
            if code == "0" and value == "BLOCK":
                close()
                block = ["", [0.0, 0.0], []]
                current = None
                continue
            if code == "0" and value == "ENDBLK":
                close()
                if block is not None and block[0]:
                    blocks[block[0]] = (tuple(block[1]), block[2])
                block = None
                continue
            if block is not None and current is None and code != "0":
                # Kopf des Blocks: Name und Basispunkt
                if code == "2":
                    block[0] = value
                elif code in ("10", "20"):
                    block[1][0 if code == "10" else 1] = float(value)
                continue
        if section in ("ENTITIES", "BLOCKS") and code == "0":
            close()
            current = (value, [])
        elif current is not None:
            current[1].append((code, value))
    return {"ENTITIES": entities, "BLOCKS": blocks}


def _place(matrix: tuple, x: float, y: float) -> tuple:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _transformed(entity: list, matrix: tuple) -> list:
    """Ein Skizzenelement mit einer ebenen Abbildung (a, b, c, d, e, f) umgerechnet (Blöcke in DXF).

    Kreise und Bögen werden mit dem mittleren Maßstab übernommen (ungleiche Maßstäbe verzerren sie in DXF zu
    Ellipsen; hier bleiben sie Kreise). Gespiegelte Bögen laufen in die andere Richtung und werden getauscht.
    """
    a, b, c, d, _e, _f = matrix
    scale = math.sqrt(abs(a * d - b * c))
    mirrored = a * d - b * c < 0
    kind = entity[0]
    if kind == sk.line(0, 0, 1, 1)[0]:
        return sk.line(*_place(matrix, *entity[1:3]), *_place(matrix, *entity[3:5]))
    if kind == sk.circle(0, 0, 1)[0]:
        return sk.circle(*_place(matrix, *entity[1:3]), entity[3] * scale)
    if kind == sk.arc(0, 0, 1, 0, 1)[0]:
        x, y, radius, start, end = entity[1:6]
        centre = _place(matrix, x, y)
        ends = []
        for angle in (start, end):
            point = _place(matrix, x + radius * math.cos(math.radians(angle)), y + radius * math.sin(math.radians(angle)))
            ends.append(math.degrees(math.atan2(point[1] - centre[1], point[0] - centre[0])) % 360)
        if mirrored:
            ends.reverse()
        return sk.arc(*centre, radius * scale, *ends)
    if kind == sk.ellipse(0, 0, 1, 1)[0]:
        x, y, first, second, rotation = entity[1:6]
        centre = _place(matrix, x, y)
        tip = _place(matrix, x + first * math.cos(math.radians(rotation)), y + first * math.sin(math.radians(rotation)))
        side = _place(matrix, x - second * math.sin(math.radians(rotation)), y + second * math.cos(math.radians(rotation)))
        return sk.ellipse(*centre, math.dist(centre, tip), math.dist(centre, side),
                          math.degrees(math.atan2(tip[1] - centre[1], tip[0] - centre[0])))
    if kind == sk.spline((0, 0), (1, 1))[0]:
        values = entity[1:]
        return sk.spline(*[_place(matrix, values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)])
    if kind == sk.CP_SPLINE:
        values = entity[1:-1]
        return sk.control_spline(*[_place(matrix, values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)],
                                 degree=int(entity[-1]))
    return entity


def _dxf_entities(records: list, blocks: dict, depth: int = 0) -> list:
    """Skizzenelemente aus DXF-Einträgen; INSERT setzt die Elemente eines Blocks ein (auch verschachtelt)."""
    entities, polyline = [], None
    for kind, data in records:
        values: dict = {}
        for code, value in data:
            values.setdefault(code, []).append(value)

        def number(code: str, default: float = 0.0) -> float:
            found = values.get(code)
            try:
                return float(found[0]) if found else default
            except ValueError:
                return default

        if kind == "LINE":
            entities.append(sk.line(number("10"), number("20"), number("11"), number("21")))
        elif kind == "CIRCLE":
            entities.append(sk.circle(number("10"), number("20"), number("40")))
        elif kind == "ARC":
            entities.append(sk.arc(number("10"), number("20"), number("40"), number("50") % 360, number("51") % 360))
        elif kind == "LWPOLYLINE":
            # Punkte (10/20) mit ihrem bulge (42 folgt dem Punkt, zu dem er gehört)
            points, bulges = [], []
            for code, value in data:
                if code == "10":
                    points.append([float(value), 0.0])
                    bulges.append(0.0)
                elif code == "20" and points:
                    points[-1][1] = float(value)
                elif code == "42" and bulges:
                    bulges[-1] = float(value)
            entities += _polyline(points, bulges, int(number("70")) & 1)
        elif kind == "POLYLINE":
            polyline = {"points": [], "bulges": [], "closed": int(number("70")) & 1}
        elif kind == "VERTEX" and polyline is not None:
            polyline["points"].append([number("10"), number("20")])
            polyline["bulges"].append(number("42"))
        elif kind == "SEQEND" and polyline is not None:
            entities += _polyline(polyline["points"], polyline["bulges"], polyline["closed"])
            polyline = None
        elif kind == "SPLINE":
            fit = list(zip(map(float, values.get("11", [])), map(float, values.get("21", []))))
            control = list(zip(map(float, values.get("10", [])), map(float, values.get("20", []))))
            if len(fit) >= 2:
                entities.append(sk.spline(*fit))
            elif len(control) >= 2:
                entities.append(sk.control_spline(*control, degree=3 if int(number("71", 3.0)) <= 3 else 5))
        elif kind == "ELLIPSE":
            cx, cy, ax, ay = number("10"), number("20"), number("11"), number("21")
            major = math.hypot(ax, ay)
            if major > 0:
                entities.append(sk.ellipse(cx, cy, major, major * number("40", 1.0), math.degrees(math.atan2(ay, ax))))
        elif kind == "INSERT" and depth < 8:          # Tiefe begrenzt: Blöcke, die sich selbst einsetzen
            name = (values.get("2") or [""])[0]
            if name not in blocks:
                continue
            (base_x, base_y), inner = blocks[name]
            sx, sy = number("41", 1.0), number("42", 1.0)
            angle = math.radians(number("50"))
            cos, sin = math.cos(angle), math.sin(angle)
            # Basispunkt abziehen, maßstäblich, drehen, an den Einfügepunkt schieben
            a, b, c, d = cos * sx, sin * sx, -sin * sy, cos * sy
            e = number("10") - (a * base_x + c * base_y)
            f = number("20") - (b * base_x + d * base_y)
            entities += [_transformed(entity, (a, b, c, d, e, f))
                         for entity in _dxf_entities(inner, blocks, depth + 1)]
    return entities


def read_dxf(path: str) -> list:
    """Skizzenelemente einer DXF-Datei (Einheit: mm), einschließlich eingesetzter Blöcke (INSERT)."""
    records = _dxf_records(path)
    entities = _dxf_entities(records["ENTITIES"], records["BLOCKS"])
    if not entities:
        raise FeatureError("In der DXF-Datei wurden keine Linien, Kreise, Bögen oder Kurven gefunden")
    return [entity for entity in entities if sk.check_entity(entity) is None]


def _polyline(points: list, bulges: list, closed: bool) -> list:
    """Linien und Bögen eines Polylinienzugs."""
    found = []
    count = len(points)
    for index in range(count if closed else count - 1):
        start, end = tuple(points[index]), tuple(points[(index + 1) % count])
        if math.dist(start, end) > 1e-9:
            found.append(_bulge_arc(start, end, bulges[index]) if bulges[index] else sk.line(*start, *end))
    return found


# --------------------------------------------------------------------------------------- SVG ---

def _matrix(text: str) -> tuple:
    """Die transform-Angabe eines SVG-Elements als Matrix (a, b, c, d, e, f)."""
    result = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, arguments in re.findall(r"(\w+)\s*\(([^)]*)\)", text or ""):
        values = [float(v) for v in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", arguments)]
        if name == "matrix" and len(values) == 6:
            step = tuple(values)
        elif name == "translate":
            step = (1, 0, 0, 1, values[0], values[1] if len(values) > 1 else 0.0)
        elif name == "scale":
            step = (values[0], 0, 0, values[1] if len(values) > 1 else values[0], 0, 0)
        elif name == "rotate":
            angle = math.radians(values[0])
            cos, sin = math.cos(angle), math.sin(angle)
            cx, cy = (values[1], values[2]) if len(values) == 3 else (0.0, 0.0)
            step = (cos, sin, -sin, cos, cx - cos * cx + sin * cy, cy - sin * cx - cos * cy)
        else:
            continue
        result = _multiply(result, step)
    return result


def _multiply(m: tuple, n: tuple) -> tuple:
    a, b, c, d, e, f = m
    g, h, i, j, k, l = n
    return (a * g + c * h, b * g + d * h, a * i + c * j, b * i + d * j, a * k + c * l + e, b * k + d * l + f)


def _numbers(text: str) -> list[float]:
    return [float(v) for v in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", text or "")]


def _path_pieces(d: str) -> list[list[tuple]]:
    """Ein SVG-Pfad als Liste von Zügen; jeder Zug ist eine Liste von ("L", Punkt) bzw. ("C", Stützpunkte)."""
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d or "")
    pieces, current, position, start = [], [], (0.0, 0.0), (0.0, 0.0)
    last_control, command, i = None, None, 0

    def take(count: int) -> list[float]:
        nonlocal i
        values = [float(tokens[i + k]) for k in range(count)]
        i += count
        return values

    while i < len(tokens):
        if re.match(r"[A-Za-z]", tokens[i]):
            command = tokens[i]
            i += 1
            if command in "Zz":
                if current:
                    current.append(("L", start))
                    pieces.append(current)
                current, position = [], start
                continue
        relative = command.islower()
        base = position if relative else (0.0, 0.0)
        kind = command.upper()
        if kind == "M":
            x, y = take(2)
            if current:
                pieces.append(current)
            position = start = (base[0] + x, base[1] + y)
            current = [("M", position)]
            command = "l" if relative else "L"         # weitere Paare nach M sind Linien
        elif kind == "L":
            x, y = take(2)
            position = (base[0] + x, base[1] + y)
            current.append(("L", position))
        elif kind == "H":
            (x,) = take(1)
            position = (position[0] + x if relative else x, position[1])
            current.append(("L", position))
        elif kind == "V":
            (y,) = take(1)
            position = (position[0], position[1] + y if relative else y)
            current.append(("L", position))
        elif kind in "CS":
            if kind == "C":
                x1, y1, x2, y2, x, y = take(6)
                first = (base[0] + x1, base[1] + y1)
            else:
                x2, y2, x, y = take(4)
                first = (2 * position[0] - last_control[0], 2 * position[1] - last_control[1]) if last_control else position
            second, end = (base[0] + x2, base[1] + y2), (base[0] + x, base[1] + y)
            current.append(("C", (position, first, second, end)))
            last_control, position = second, end
            continue
        elif kind in "QT":
            if kind == "Q":
                x1, y1, x, y = take(4)
                control = (base[0] + x1, base[1] + y1)
            else:
                x, y = take(2)
                control = (2 * position[0] - last_control[0], 2 * position[1] - last_control[1]) if last_control else position
            end = (base[0] + x, base[1] + y)
            # quadratische Kurve als kubische
            first = (position[0] + 2 / 3 * (control[0] - position[0]), position[1] + 2 / 3 * (control[1] - position[1]))
            second = (end[0] + 2 / 3 * (control[0] - end[0]), end[1] + 2 / 3 * (control[1] - end[1]))
            current.append(("C", (position, first, second, end)))
            last_control, position = control, end
            continue
        elif kind == "A":
            rx, ry, rotation, large, sweep, x, y = take(7)
            end = (base[0] + x, base[1] + y)
            current += [("L", point) for point in _arc_points(position, end, rx, ry, rotation, large, sweep)]
            position = end
        last_control = None
    if current:
        pieces.append(current)
    return pieces


def _arc_points(start, end, rx, ry, rotation, large, sweep, steps: int = 24) -> list:
    """Elliptischer SVG-Bogen als Punkte (Umrechnung von Endpunkt- auf Mittelpunktdarstellung, SVG-Anhang F.6)."""
    if rx == 0 or ry == 0 or math.dist(start, end) < 1e-12:
        return [end]
    phi = math.radians(rotation)
    cos, sin = math.cos(phi), math.sin(phi)
    dx, dy = (start[0] - end[0]) / 2, (start[1] - end[1]) / 2
    x1, y1 = cos * dx + sin * dy, -sin * dx + cos * dy
    rx, ry = abs(rx), abs(ry)
    scale = (x1 / rx) ** 2 + (y1 / ry) ** 2
    if scale > 1:
        rx, ry = rx * math.sqrt(scale), ry * math.sqrt(scale)
    factor = math.sqrt(max(0.0, (rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1)
                           / (rx * rx * y1 * y1 + ry * ry * x1 * x1)))
    if large == sweep:
        factor = -factor
    cx1, cy1 = factor * rx * y1 / ry, -factor * ry * x1 / rx
    cx, cy = cos * cx1 - sin * cy1 + (start[0] + end[0]) / 2, sin * cx1 + cos * cy1 + (start[1] + end[1]) / 2
    angle = lambda ux, uy: math.atan2(uy, ux)
    begin = angle((x1 - cx1) / rx, (y1 - cy1) / ry)
    delta = angle((-x1 - cx1) / rx, (-y1 - cy1) / ry) - begin
    if sweep and delta < 0:
        delta += 2 * math.pi
    elif not sweep and delta > 0:
        delta -= 2 * math.pi
    points = []
    for k in range(1, steps + 1):
        t = begin + delta * k / steps
        px, py = rx * math.cos(t), ry * math.sin(t)
        points.append((cos * px - sin * py + cx, sin * px + cos * py + cy))
    return points


def read_svg(path: str) -> list:
    """Skizzenelemente aus einer SVG-Datei (Pixel → mm, y nach oben)."""
    entities, texts = read_svg_full(path)
    if not entities:
        raise FeatureError("In der SVG-Datei wurden keine Linien oder Kurven gefunden")
    return entities


def read_svg_full(path: str) -> tuple[list, list]:
    """Skizzenelemente und Texte einer SVG-Datei. Texte im Format der Skizze: [Text, x, y, Höhe in mm, Drehung]."""
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as error:
        raise FeatureError(f"SVG-Datei nicht lesbar: {error}") from error
    entities, texts = [], []

    def place(matrix: tuple, point: tuple) -> tuple:
        a, b, c, d, e, f = matrix
        x, y = a * point[0] + c * point[1] + e, b * point[0] + d * point[1] + f
        return x * PX_TO_MM, -y * PX_TO_MM

    def bezier(matrix, points) -> list:
        p0, p1, p2, p3 = points
        samples = []
        for k in range(0, 9):
            t = k / 8
            samples.append(tuple((1 - t) ** 3 * p0[i] + 3 * (1 - t) ** 2 * t * p1[i] + 3 * (1 - t) * t * t * p2[i]
                                 + t ** 3 * p3[i] for i in (0, 1)))
        return [sk.spline(*[place(matrix, point) for point in samples])]

    def chain(matrix, points, closed=False) -> list:
        placed = [place(matrix, point) for point in points]
        if closed and placed and math.dist(placed[0], placed[-1]) > 1e-9:
            placed.append(placed[0])
        return [sk.line(*a, *b) for a, b in zip(placed, placed[1:]) if math.dist(a, b) > 1e-9]

    def walk(element, matrix):
        matrix = _multiply(matrix, _matrix(element.get("transform")))
        tag = element.tag.rsplit("}", 1)[-1]
        number = lambda name: float((_numbers(element.get(name, "0")) or [0.0])[0])
        if tag == "line":
            entities.extend(chain(matrix, [(number("x1"), number("y1")), (number("x2"), number("y2"))]))
        elif tag == "rect":
            x, y, w, h = number("x"), number("y"), number("width"), number("height")
            entities.extend(chain(matrix, [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], closed=True))
        elif tag in ("circle", "ellipse"):
            cx, cy = number("cx"), number("cy")
            rx = number("r") if tag == "circle" else number("rx")
            ry = number("r") if tag == "circle" else number("ry")
            a, b, c, d, _e, _f = matrix
            uniform = abs(a - d) < 1e-9 and abs(b + c) < 1e-9 and abs(rx - ry) < 1e-12
            centre = place(matrix, (cx, cy))
            if uniform:
                entities.append(sk.circle(*centre, rx * math.hypot(a, b) * PX_TO_MM))
            else:
                axis = place(matrix, (cx + rx, cy))
                minor = place(matrix, (cx, cy + ry))
                major = math.dist(centre, axis)
                entities.append(sk.ellipse(*centre, major, math.dist(centre, minor),
                                           math.degrees(math.atan2(axis[1] - centre[1], axis[0] - centre[0]))))
        elif tag in ("polyline", "polygon"):
            values = _numbers(element.get("points"))
            points = list(zip(values[0::2], values[1::2]))
            entities.extend(chain(matrix, points, closed=tag == "polygon"))
        elif tag == "path":
            for piece in _path_pieces(element.get("d")):
                position = None
                for kind, data in piece:
                    if kind == "M":
                        position = data
                    elif kind == "L":
                        if position is not None:
                            entities.extend(chain(matrix, [position, data]))
                        position = data
                    else:
                        entities.extend(bezier(matrix, data))
                        position = data[3]
        elif tag == "text":
            # Text samt <tspan>-Teilen; Schriftgröße aus font-size bzw. style (Vorgabe 16 px)
            content = " ".join(part.strip() for part in element.itertext() if part.strip())
            size_text = element.get("font-size") or (re.search(r"font-size\s*:\s*([^;]+)", element.get("style", ""))
                                                    or [None, "16"])[1]
            size = float((_numbers(size_text) or [16.0])[0])
            a, b, c, d, _e, _f = matrix
            if content:
                x, y = place(matrix, (number("x"), number("y")))
                height = size * math.sqrt(abs(a * d - b * c)) * PX_TO_MM
                # SVG dreht im Uhrzeigersinn (y nach unten): in der Skizze entgegengesetzt
                texts.append([content, x, y, height, -math.degrees(math.atan2(b, a))])
            return                      # die Kinder (tspan) sind schon im Text enthalten
        for child in element:
            walk(child, matrix)

    walk(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
    entities = [entity for entity in entities if sk.check_entity(entity) is None]
    if not entities and not texts:
        raise FeatureError("In der SVG-Datei wurden keine Linien, Kurven oder Texte gefunden")
    return entities, texts


def read_drawing_full(path: str) -> tuple[list, list]:
    """DXF oder SVG nach der Dateiendung: (Skizzenelemente, Texte)."""
    if path.lower().endswith(".svg"):
        return read_svg_full(path)
    return read_drawing(path), []


def read_drawing(path: str) -> list:
    """DXF oder SVG nach der Dateiendung."""
    if path.lower().endswith(".dxf"):
        return read_dxf(path)
    if path.lower().endswith(".svg"):
        return read_svg(path)
    raise FeatureError("Nur DXF- und SVG-Dateien lassen sich einfügen")
