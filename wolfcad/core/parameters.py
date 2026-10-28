# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Benutzerparameter: benannte Werte mit Ausdrücken wie in Fusion („Parameter ändern“).

Ein Ausdruck besteht aus Zahlen (Punkt oder Komma als Dezimalzeichen), den Rechenzeichen
+ - * / ^, Klammern, Namen anderer Parameter und den Funktionen sqrt, abs, sin, cos, tan
(Winkel in Grad) sowie der Zahl pi. Eine angehängte Einheit „mm“ wird überlesen.
Ausgewertet wird über den Syntaxbaum – es wird kein Programmcode ausgeführt.
"""

from __future__ import annotations

import ast
import math
import re

FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs,
    "sin": lambda degrees: math.sin(math.radians(degrees)),
    "cos": lambda degrees: math.cos(math.radians(degrees)),
    "tan": lambda degrees: math.tan(math.radians(degrees)),
}
CONSTANTS = {"pi": math.pi}
_OPERATORS = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b,
}
_NAME = re.compile(r"[A-Za-zÄÖÜäöüß_][A-Za-zÄÖÜäöüß_0-9]*\Z")


class ParameterError(ValueError):
    """Ein Ausdruck oder Parametername ist unbrauchbar."""


def is_number(text: str) -> bool:
    """Ist der Text eine einfache Zahl (kein Ausdruck)?"""
    try:
        float(text.strip().replace(",", "."))
        return True
    except ValueError:
        return False


def check_name(name: str) -> None:
    if not _NAME.match(name):
        raise ParameterError(f"'{name}' ist kein gültiger Name (Buchstaben, Ziffern, _; nicht mit Ziffer beginnen)")
    if name in FUNCTIONS or name in CONSTANTS or name == "mm":
        raise ParameterError(f"'{name}' ist bereits vergeben (Funktion, Konstante oder Einheit)")


def evaluate(expression: str, values: dict[str, float] | None = None) -> float:
    """Rechnet einen Ausdruck aus; `values` sind die schon bekannten Parameter."""
    values = values or {}
    text = expression.strip()
    if not text:
        raise ParameterError("Der Ausdruck ist leer")
    # Schreibweisen angleichen: Dezimalkomma, Potenzzeichen, angehängte Einheit
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"(?<=[\d)\s])\s*mm\b", "", text).replace("^", "**")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as error:
        raise ParameterError(f"'{expression}' ist kein gültiger Ausdruck") from error

    def value_of(node) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in values:
                return float(values[node.id])
            if node.id in CONSTANTS:
                return CONSTANTS[node.id]
            raise ParameterError(f"Unbekannter Parameter '{node.id}'")
        if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](value_of(node.left), value_of(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            operand = value_of(node.operand)
            return -operand if isinstance(node.op, ast.USub) else operand
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS
                and len(node.args) == 1 and not node.keywords):
            return float(FUNCTIONS[node.func.id](value_of(node.args[0])))
        raise ParameterError(f"'{expression}' enthält etwas, das nicht berechnet werden kann")

    try:
        result = value_of(tree.body)
    except ParameterError:
        raise       # eigene Meldung (z. B. unbekannter Parameter) unverändert weitergeben
    except ZeroDivisionError as error:
        raise ParameterError(f"'{expression}': Division durch 0") from error
    except (ValueError, OverflowError) as error:
        raise ParameterError(f"'{expression}' lässt sich nicht berechnen") from error
    if isinstance(result, complex) or not math.isfinite(result):
        raise ParameterError(f"'{expression}' ergibt keine brauchbare Zahl")
    return result


def rename(expression: str, old: str, new: str) -> str:
    """Ersetzt in einem Ausdruck den Parameternamen `old` durch `new` (nur als ganzes Wort)."""
    return re.sub(rf"(?<![\wÄÖÜäöüß]){re.escape(old)}(?![\wÄÖÜäöüß(])", new, expression)


def resolve(parameters: dict[str, str]) -> tuple[dict[str, float], dict[str, str]]:
    """Rechnet alle Parameter aus, auch wenn sie aufeinander verweisen.

    Zurück kommen die Werte und – je nicht berechenbarem Parameter – eine Fehlermeldung
    (z. B. bei unbekannten Namen oder wenn sich Parameter im Kreis aufeinander beziehen).
    """
    values: dict[str, float] = {}
    errors: dict[str, str] = {}
    open_names = list(parameters)
    for name in list(open_names):
        try:
            check_name(name)
        except ParameterError as error:
            errors[name] = str(error)
            open_names.remove(name)
    # So oft durchgehen, bis nichts Neues mehr berechnet werden kann
    progress = True
    while open_names and progress:
        progress = False
        for name in list(open_names):
            try:
                values[name] = evaluate(parameters[name], values)
            except ParameterError:
                continue
            open_names.remove(name)
            progress = True
    for name in open_names:
        try:
            evaluate(parameters[name], values)
            errors[name] = "Der Parameter bezieht sich im Kreis auf sich selbst"
        except ParameterError as error:
            message = str(error)
            unknown = re.search(r"Unbekannter Parameter '(.+)'", message)
            if unknown and unknown.group(1) in parameters:
                message = "Die Parameter beziehen sich im Kreis aufeinander"
            errors[name] = message
    return values, errors
