# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Skye und die WolfCAD-Mitwirkenden
"""Werkzeug-Erklärungen beim Überfahren (Runde 77).

Bleibt die Maus kurz auf einem Werkzeug der Leiste (oder auf einem Eintrag einer ▾-Liste), gleitet eine kleine Karte
weich ein: Symbol, Name und Tastenkürzel, darunter „Was es tut“ und „So geht's“ in wenigen Schritten. Wandert die Maus
zum nächsten Werkzeug, wechselt die Karte sofort mit; verlässt sie die Leiste, blendet die Karte aus.

Aufbau:
  - `MODEL_HINTS` / `SKETCH_HINTS`: Texte je Werkzeug (Modell und Reiter SKIZZE getrennt, weil es gleichnamige
    Werkzeuge gibt, z. B. „Abrunden“). Fehlt ein Text, dient der vorhandene Tooltip als „Was es tut“.
  - `HintCard`: das schwebende Fenster mit der Animation (Deckkraft + kurzes Hineingleiten). Die Deckkraft wird über
    einen Grafikeffekt im Fenster gemacht, nicht über die Fensterdeckkraft – die kennt Wayland nicht.
  - `ToolHints`: beobachtet Knöpfe und Menüs, wartet die Verzögerung ab und setzt die Karte an die richtige Stelle.
  - Video (Runde 80): Gibt es zum Werkzeug einen Clip (clip_recorder.py / clip_scenes.py, wolfcad/gui/clips), klappt
    die Karte nach einer weiteren Sekunde Ruhe oben ein kurzes Endlos-Video auf (wie Fusions erweiterte Tooltips).
    Wandert die Maus weiter, bleibt die Karte aufgeklappt und zeigt gleich das Video des nächsten Werkzeugs.

Eigene Texte und eigene Grafik; nur das Verhalten (erweiterte Tooltips) ist an Fusion 360 angelehnt.
"""
from __future__ import annotations

import html

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QRect, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QAction, QColor, QGuiApplication, QMovie, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu,
    QToolButton, QVBoxLayout, QWidget,
)

MODEL, SKETCH = "modell", "skizze"      # Art des Werkzeugs (Eigenschaft „werkzeughilfe“ an der Aktion)
_KIND = "werkzeughilfe"

SHOW_DELAY = 550        # ms Ruhe auf einem Werkzeug, bis die Karte erscheint
SWITCH_GRACE = 400      # ms nach dem Ausblenden, in denen ein neues Werkzeug ohne Wartezeit erklärt wird
FADE_IN, FADE_OUT = 190, 120    # ms der Ein- und Ausblendung
SLIDE = 10              # px, um die die Karte beim Erscheinen hineingleitet
CARD_WIDTH = 330
MARGIN = 14             # Rand um die Karte für den Schatten
DETAIL_DELAY = 1100     # ms weitere Ruhe, bis das Video aufklappt
VIDEO_SIZE = (306, 180)  # Größe des Videos in der Karte (Clips sind doppelt so groß aufgenommen: scharf auf HiDPI)
UNFOLD = 230            # ms des Aufklappens

# --- Texte: Name -> (Was es tut, [So geht's …]); leeres „Was“ = aus dem Tooltip übernehmen -----------------------
_LIVE = "Am blauen Pfeil ziehen oder den Wert eintippen – Eingabe bestätigt, Esc bricht ab"

MODEL_HINTS: dict[str, tuple[str, list[str]]] = {
    "Neu": ("Beginnt einen leeren Entwurf.", ["Klicken (Strg+N) – ungespeicherte Änderungen werden vorher erfragt"]),
    "Öffnen": ("Öffnet einen gespeicherten WolfCAD-Entwurf (.wcad).", ["Klicken (Strg+O) und die Datei wählen"]),
    "Speichern": ("Speichert den Entwurf samt Timeline.", ["Klicken (Strg+S); beim ersten Mal Dateinamen wählen"]),
    "Import": ("", ["Klicken und eine .f3d- oder .step-Datei wählen",
                    "Fusion-Dateien bringen ihren Verlauf mit – „Abspielen“ holt die Schritte in die Timeline"]),
    "Zeichnung": ("", ["Gewünschte Körper sichtbar schalten", "Klicken: Ansichten werden auf dem Blatt angelegt",
                       "Blatt als PDF oder SVG speichern"]),
    "Export": ("", ["Nur die Körper sichtbar lassen, die hinein sollen", "Klicken, STEP oder STL als Format wählen"]),
    "3D-Druck": ("", ["Körper wählen (ohne Auswahl: alle sichtbaren)",
                      "Klicken – der Reiter „Druck“ öffnet sich mit dem Druckbett",
                      "Dort anordnen, slicen und an den Drucker senden"]),
    "Schaltplan": ("Schaltplan des Entwurfs zeichnen und prüfen (eigener Reiter, wird mit dem Entwurf gespeichert).",
                   ["Klicken – der Reiter „Schaltplan“ öffnet sich", "Bauteil in der Bibliothek doppelklicken, "
                    "mit R drehen, klicken setzt", "N: Leitungen von Pin zu Pin ziehen",
                    "„Prüfen“ zeigt offene Pins und Fehler; „Ausgabe“: Netzliste, Stückliste, SPICE, PDF"]),
    "Rückgängig": ("Nimmt den letzten Schritt zurück.", ["Klicken oder Strg+Z"]),
    "Wiederholen": ("Stellt einen zurückgenommenen Schritt wieder her.", ["Klicken oder Strg+Umschalt+Z"]),
    "Ebene": ("", ["Bezug anklicken: Fläche, zwei Flächen, drei Punkte oder zwei Kanten",
                   "Werkzeug starten", "Abstand bzw. Winkel am Pfeil ziehen oder eintippen"]),
    "Achse": ("", ["Kante, Bohrung, Kreiskante oder zwei Eckpunkte anklicken", "Werkzeug starten"]),
    "Punkt": ("", ["Eckpunkt, Kante oder Fläche anklicken", "Werkzeug starten"]),
    "Ebene entlang Pfad": ("", ["Zuerst den Pfad als Skizze zeichnen",
                                "Werkzeug starten und die Stelle (0 = Anfang, 1 = Ende) einstellen"]),
    "Achse senkrecht zur Fläche": ("", ["Werkzeug starten", "Auf die gewünschte Stelle einer Fläche klicken"]),
    "Skizze": ("", ["Ebene Fläche anklicken – oder ohne Auswahl starten und eine Hauptebene wählen",
                    "Im Reiter SKIZZE zeichnen und bemaßen", "Mit „Skizze fertig stellen“ (Eingabe) abschließen"]),
    "Extrusion": ("", ["Geschlossenes Profil einer Skizze anklicken", "Werkzeug starten", _LIVE,
                       "Unten wählen: Vereinigen, Ausschneiden, Schnittmenge oder Neuer Körper"]),
    "Drehen": ("", ["Profil anklicken und Werkzeug starten", "Drehachse wählen (Skizzenlinie, Kante oder Hauptachse)",
                    "Winkel am Pfeil ziehen oder eintippen (360° = voll)"]),
    "Sweep": ("", ["Zwei Skizzen zeichnen: Querschnitt und Pfad", "Werkzeug starten",
                   "Profil und Pfad zuordnen, mit Eingabe bestätigen"]),
    "Erhebung": ("", ["Querschnitte auf verschiedenen Ebenen skizzieren",
                      "Werkzeug starten – die Profile werden der Reihe nach verbunden"]),
    "Rippe": ("", ["Linie auf einer Ebene durch den Körper skizzieren", "Werkzeug starten, Dicke einstellen"]),
    "Quader": ("", ["Werkzeug starten", "Erste Ecke auf dem Raster anklicken, zur zweiten ziehen",
                    "Höhe am Pfeil ziehen oder eintippen"]),
    "Zylinder": ("", ["Werkzeug starten", "Mittelpunkt anklicken, Rand aufziehen", "Höhe am Pfeil einstellen"]),
    "Kugel": ("", ["Werkzeug starten", "Mittelpunkt anklicken und den Rand aufziehen"]),
    "Torus": ("", ["Werkzeug starten", "Ring- und Querschnittsdurchmesser eintragen"]),
    "Spule": ("", ["Werkzeug starten", "Durchmesser, Steigung, Windungen und Drahtstärke eintragen"]),
    "Zahnrad": ("", ["Werkzeug starten, Art wählen (Stirnrad, schräg, Pfeil, Hohlrad, Zahnstange)",
                     "Zahnbreite am Pfeil ziehen, Zähnezahl am Pfeil nach außen",
                     "Modul, Eingriffswinkel, Bohrung und Flankenspiel eintragen"]),
    "Rohr": ("", ["Pfad als Skizze zeichnen", "Werkzeug starten", "Außendurchmesser und ggf. Wandstärke einstellen"]),
    "Prägen": ("", ["Text oder Form skizzieren", "Zielfläche anklicken, Werkzeug starten",
                    "Tiefe einstellen – positiv erhaben, negativ vertieft"]),
    "Steg": ("", ["Offene Linien skizzieren", "Werkzeug starten, Dicke und Tiefe einstellen"]),
    "Bohrung": ("", ["Ebene Fläche anklicken", "Werkzeug starten", "Lage anklicken, Durchmesser und Tiefe "
                     "einstellen – auch Senkung und Gewinde"]),
    "Spiegeln": ("Spiegelt Körper an einer Ebene; die Kopie wird ein neuer Körper oder mit dem Original vereinigt.",
                 ["Körper wählen und Werkzeug starten",
                  "Spiegelebene wählen: Hauptebene, ebene Körperfläche oder Konstruktionsebene",
                  "Vereinigen oder Neuer Körper wählen"]),
    "Muster": ("", ["Körper wählen, Werkzeug starten", "Anzahl und Abstand je Richtung einstellen"]),
    "Kreismuster": ("", ["Körper wählen, Werkzeug starten", "Achse wählen, Anzahl und Winkel einstellen"]),
    "Pfadmuster": ("", ["Pfad skizzieren", "Körper wählen, Werkzeug starten", "Anzahl und Abstand einstellen"]),
    "Abrunden": ("", ["Kanten anklicken (Strg für mehrere)", "Werkzeug starten", "Radius am Pfeil ziehen oder "
                      "eintippen"]),
    "Fase": ("", ["Kanten anklicken", "Werkzeug starten", "Abstand am Pfeil ziehen oder eintippen"]),
    "Schale": ("", ["Flächen anklicken, die offen sein sollen", "Werkzeug starten", "Wandstärke einstellen"]),
    "Fläche ersetzen": ("", ["Zu ersetzende Fläche anklicken", "Werkzeug starten", "Neue Fläche oder Ebene anklicken"]),
    "Fläche löschen": ("", ["Flächen anklicken (z. B. eine Verrundung)", "Werkzeug starten – die Lücke schließt sich"]),
    "Fläche verschieben": ("", ["Flächen anklicken", "Werkzeug starten", "An den Pfeilen verschieben oder drehen"]),
    "Drücken/Ziehen": ("", ["Werkzeug starten", "Eine oder mehrere Flächen anklicken",
                            "Am Pfeil herausziehen oder hineindrücken – auch gerundete Flächen"]),
    "Schräge": ("", ["Seitenflächen anklicken (ohne Auswahl: alle)", "Werkzeug starten",
                     "Zugrichtung und Winkel einstellen"]),
    "Teilen": ("", ["Werkzeug starten", "Trennfläche anklicken: Ebene, Körperfläche (auch gewölbt) oder Flächenkörper",
                    "Die Fläche wird verlängert – die Stücke werden eigene Körper"]),
    "Skalieren": ("", ["Körper wählen, Werkzeug starten", "Faktor eintragen (2 = doppelt so groß)"]),
    "Gewinde": ("", ["Zylinderfläche anklicken (Welle oder Bohrung)", "Werkzeug starten",
                     "Größe und Länge wählen; „modelliert“ schneidet echte Gänge"]),
    "Fläche teilen": ("", ["Flächen anklicken", "Werkzeug starten", "Teilende Ebene wählen"]),
    "Ausrichten": ("", ["Werkzeug starten", "Fläche am zu bewegenden Körper anklicken", "Zielfläche anklicken"]),
    "Physisches Material": ("", ["Körper wählen (ohne Auswahl: alle sichtbaren)", "Werkstoff aus der Liste wählen"]),
    "Alle berechnen": ("", ["Klicken oder Strg+R – hilft, wenn etwas veraltet aussieht"]),
    "Kombinieren": ("", ["Werkzeug starten", "Zuerst den Zielkörper anklicken, dann die Werkzeugkörper",
                         "Vereinigen, Ausschneiden oder Schnittmenge wählen"]),
    "Verschieben": ("", ["Körper wählen, Werkzeug starten",
                         "Pfeile verschieben (X rot, Y grün, Z blau), die Ringstücke drehen um jede Achse",
                         "Der helle Schrägpfeil skaliert (Faktor) – gedreht und skaliert wird um die Körpermitte",
                         "„Kopie“ lässt das Original stehen"]),
    "Farbe": ("", ["Körper wählen (ohne Auswahl: alle)", "Werkzeug starten und Farbe wählen"]),
    # Reiter PHYSIK (Runde 83)
    "Lager": ("Hält gewählte Flächen fest – dort ist das Teil eingespannt oder verschraubt.",
              ["Flächen anklicken (Strg+Klick für mehrere)", "Werkzeug wählen – die Flächen werden grün"]),
    "Kraft": ("Setzt eine Kraft auf gewählte Flächen, verteilt über die ganze Fläche.",
              ["Flächen anklicken", "Werkzeug wählen", "Kraft in N und Richtung eintragen (1 kg ≈ 9,81 N)"]),
    "Druck": ("Setzt einen Druck senkrecht auf gewählte Flächen (z. B. Wasser- oder Luftdruck).",
              ["Flächen anklicken", "Werkzeug wählen", "Druck in MPa eintragen (1 bar = 0,1 MPa)"]),
    "Lasten entfernen": ("Entfernt alle Lager und Lasten des Entwurfs.", ["Werkzeug wählen"]),
    "Festigkeit": ("Rechnet Spannung und Verformung unter den Lasten (Finite Elemente) und zeigt, ob das Teil hält.",
                   ["Lager und Lasten setzen", "Werkstoff unter „Physisches Material“ wählen",
                    "Werkzeug wählen – blau entspannt, rot hoch belastet"]),
    "Luftströmung": ("Lässt Luft um den Körper strömen: Widerstand (cw), Druck auf der Oberfläche, Stromlinien.",
                     ["Körper wählen", "Richtung und Geschwindigkeit eintragen",
                      "Näherung zum Vergleichen von Formen"]),
    "Wasserströmung": ("Wie Luftströmung, aber mit Wasser – z. B. für Rümpfe, Paddel oder Düsen.",
                       ["Körper wählen", "Richtung und Geschwindigkeit eintragen"]),
    "Schwimmen": ("Prüft, ob der Körper schwimmt, wie tief er eintaucht und wie viel er tragen kann.",
                  ["Werkstoff wählen", "Körper wählen, Werkzeug starten – der Wasserspiegel erscheint"]),
    "Ergebnis aus": ("Blendet das Ergebnis aus und zeigt den Körper wieder normal.", ["Werkzeug wählen"]),
    "Physikansicht": ("Schaltet die Physikansicht dazu oder weg: Ergebnisfarben, Stromlinien, Wasserspiegel, Lager und "
                      "Lasten – gelöscht wird nichts.", ["Anklicken schaltet um"]),
    "Live neu rechnen": ("Ändert sich der berechnete Körper, wird das Ergebnis kurz danach im Hintergrund mit denselben "
                         "Einstellungen neu gerechnet.", ["Eingeschaltet lassen", "Körper ändern – das Ergebnis folgt"]),
    "Parameter": ("", ["Klicken – Tabelle der Parameter öffnet sich",
                       "Name und Wert eintragen; in Feldern dann z. B. „breite / 2“ schreiben"]),
    "Fläche extrudieren": ("", ["Kurven skizzieren (offen geht auch)", "Werkzeug starten, Höhe einstellen"]),
    "Fläche drehen": ("", ["Kurven skizzieren", "Werkzeug starten, Achse und Winkel wählen"]),
    "Flicken": ("", ["Geschlossene Kurve skizzieren", "Werkzeug starten"]),
    "Fläche versetzen": ("", ["Werkzeug starten", "Flächenkörper anklicken, Abstand einstellen"]),
    "Fläche sweepen": ("", ["Profil und danach den Pfad skizzieren", "Werkzeug starten"]),
    "Fläche erheben": ("", ["Zwei Kurvenzüge skizzieren", "Werkzeug starten"]),
    "Begrenzungsfüllung": ("", ["Werkzeug starten", "Alle begrenzenden Flächen und Körper anklicken",
                                "Zellen wählen, die gefüllt werden"]),
    "Fläche stutzen": ("", ["Werkzeug starten", "Zu stutzende Fläche, dann das Schnittwerkzeug anklicken",
                            "Stücke anklicken, die wegfallen"]),
    "Fläche verlängern": ("", ["Werkzeug starten", "Flächenkörper anklicken, Länge einstellen"]),
    "Trennen": ("", ["Werkzeug starten", "Körper anklicken – jede Fläche wird ein eigener Körper"]),
    "Regelfläche": ("", ["Zwei Kurvenzüge skizzieren", "Werkzeug starten"]),
    "Normale umkehren": ("", ["Werkzeug starten", "Flächenkörper anklicken"]),
    "Aufdicken": ("", ["Werkzeug starten", "Flächenkörper anklicken, Dicke einstellen"]),
    "Vernähen": ("", ["Werkzeug starten", "Alle Flächenkörper anklicken", "Mit Eingabe bestätigen"]),
    "Netz einfügen": ("", ["Klicken und eine STL-Datei wählen"]),
    "Körper in Netz umwandeln": ("", ["Werkzeug starten", "Körper anklicken"]),
    "Netz in Körper umwandeln": ("", ["Werkzeug starten", "Geschlossenes Netz anklicken (vorher ggf. reparieren)"]),
    "Netz verkleinern": ("", ["Werkzeug starten", "Netz anklicken, Rasterweite einstellen"]),
    "Netz reparieren": ("", ["Werkzeug starten", "Netz anklicken"]),
    "Netz glätten": ("", ["Werkzeug starten", "Netz anklicken, Stärke einstellen"]),
    "Formen": ("Netz modellieren wie in Blenders Sculpt-Modus",
               ["Netz- oder Volumenkörper anklicken, „Formen“ starten",
                "Mit gedrückter linker Taste über die Oberfläche streichen – Strg drückt hinein, Umschalt glättet",
                "Pinsel per Taste wie in Blender (X Zeichnen, G Greifen, S Glätten, M Maske …), [ ] Radius",
                "Eingabe/OK übernimmt den Schritt „Formen“"]),
    "Schleifenschnitt": ("Neue Kantenschleife ins Netz setzen wie in Blender",
                         ["Netz- oder Volumenkörper anklicken, Werkzeug starten (im Formen-Modus: Strg+R)",
                          "Über eine Kante fahren: die Schleife erscheint gelb quer dazu",
                          "Mausrad oder +/− ändert die Zahl der Schnitte, Klick legt sie fest",
                          "Längs der Kante schieben, Klick bestätigt (Rechtsklick: mittig)"]),
    "Netz schneiden": ("", ["Werkzeug starten", "Netz anklicken, Ebene wählen", "„Schließen“ füllt die Schnittfläche"]),
    "Netz trennen": ("", ["Werkzeug starten", "Netz anklicken"]),
    "Netze kombinieren": ("", ["Werkzeug starten", "Alle Netze anklicken"]),
    "Netznormalen umkehren": ("", ["Werkzeug starten", "Netz anklicken"]),
    "Netz als STL speichern": ("", ["Netz wählen", "Klicken und Dateinamen wählen"]),
    "Flansch": ("", ["Profil skizzieren", "Werkzeug starten", "Dicke, Biegeradius und K-Faktor einstellen"]),
    "Flansch an Kante": ("", ["Gerade Kante des Blechteils anklicken", "Werkzeug starten", "Länge und Winkel einstellen"]),
    "Konturflansch": ("", ["Offenen Linienzug skizzieren", "Werkzeug starten, Breite einstellen"]),
    "Aufreißen": ("", ["Äußere Ecke (Kante) des Blechteils anklicken", "Werkzeug starten, Spalt einstellen"]),
    "Saum": ("", ["Kante des Blechteils anklicken", "Werkzeug starten, Spalt und Länge einstellen"]),
    "Abwicklung": ("", ["Werkzeug starten", "Blechteil anklicken – der Zuschnitt erscheint daneben"]),
    "In Blech umwandeln": ("", ["Werkzeug starten", "Plattenförmigen Körper anklicken"]),
    "Biegen": ("", ["Biegelinie auf dem Blech skizzieren", "Werkzeug starten, Winkel einstellen"]),
    "Abwickeln": ("", ["Werkzeug starten", "Blechteil anklicken", "Danach z. B. Löcher setzen, dann „Zurückbiegen“"]),
    "Zurückbiegen": ("", ["Werkzeug starten", "Abgewickeltes Blechteil anklicken"]),
    "Zuschnitt (DXF)": ("", ["Blechteil wählen", "Klicken und Dateinamen wählen"]),
    "Komponente": ("", ["Körper wählen", "Klicken – sie werden zu einem Bauteil zusammengefasst"]),
    "Gelenk": ("", ["Werkzeug starten", "Fläche oder Kante am beweglichen Teil anklicken",
                    "Dann die am festen Teil – Art wählen: starr, drehbar, verschiebbar"]),
    "Gelenkursprung": ("", ["Werkzeug starten", "Stelle am Körper anklicken"]),
    "Starre Gruppe": ("", ["Körper wählen", "Klicken – sie bewegen sich ab jetzt gemeinsam"]),
    "Bewegen": ("", ["Gelenke anlegen", "Klicken – erneut klicken hält an"]),
    "Kollision": ("", ["Klicken – Durchdringungen werden rot gezeigt"]),
    "Messen": ("", ["Werkzeug starten", "Ein Element anklicken – oder zwei für Abstand und Winkel"]),
    "Schwerpunkt": ("", ["Körper wählen (ohne Auswahl: alle sichtbaren)", "Klicken"]),
    "Leinwand": ("", ["Ebene Körperfläche wählen (oder danach anklicken), dann Bild wählen",
                      "Pfeile verschieben, Bogen dreht, heller Pfeil skaliert",
                      "Danach darauf skizzieren und nachzeichnen"]),
    "Komponentenfarben": ("", ["Klicken schaltet ein und wieder aus"]),
    "Körper kopieren": ("", ["Körper wählen", "Klicken oder Strg+C"]),
    "Körper einfügen": ("", ["Klicken oder Strg+V", "An den Pfeilen an die richtige Stelle schieben"]),
    "DXF/SVG einfügen": ("", ["Klicken, Datei wählen", "Ebene und Maßstab wählen"]),
    "Eigenschaften": ("", ["Klicken – Werte erscheinen in einem Fenster"]),
    "Schnitt": ("", ["Klicken – das Modell wird aufgeschnitten", "Ebene am Pfeil verschieben",
                     "Erneut klicken: nächste Achse (X → Y → Z → aus)"]),
    "Einpassen": ("", ["Klicken – das ganze Modell passt ins Bild"]),
    "Raster": ("", ["Klicken schaltet das Bodenraster ein oder aus"]),
    "Einstellungen": ("", ["Klicken – Mausbelegung, Tastenkürzel, Makros und diese Erklärungen einstellen"]),
    "Abspielen": ("", ["Fusion-Datei importieren", "Klicken – Schritt für Schritt in die Timeline holen"]),
    "Hilfskörper": ("", ["Klicken schaltet sie ein oder aus"]),
    "Analyse": ("", ["Knopf anklicken und Entformungsschräge oder Wandstärke wählen",
                     "Die Farben zeigen kritische Stellen; erneut wählen schaltet aus"]),
    "Ansichten": ("Fertige Blickrichtungen (oben, vorne, iso …).", ["Knopf anklicken, Ansicht wählen"]),
}

_DRAW = "Maße lassen sich beim Zeichnen eintippen (Tab wechselt das Feld)"
_PICK2 = "Erst das eine, dann das andere Element anklicken"
SKETCH_HINTS: dict[str, tuple[str, list[str]]] = {
    "Auswählen": ("Elemente der Skizze anklicken, verschieben und bearbeiten.",
                  ["Anklicken oder Rahmen aufziehen", "Punkte und Linien ziehen", "Entf löscht die Auswahl"]),
    "Punkt": ("Einzelner Skizzenpunkt, z. B. als Bohrungsmitte.", ["Auf die gewünschte Stelle klicken"]),
    "Linie": ("Gerade Linien, auch als zusammenhängender Linienzug.",
              ["Anfang anklicken, weitere Punkte anklicken", "Länge/Winkel eintippen möglich",
               "Esc oder Doppelklick beendet den Zug"]),
    "Rechteck": ("Rechteck über zwei gegenüberliegende Ecken.", ["Erste Ecke anklicken", "Zweite Ecke anklicken",
                                                                  "Breite und Höhe auch eintippen"]),
    "Mittelpunkt-Rechteck": ("Rechteck von der Mitte aus.", ["Mitte anklicken", "Eine Ecke anklicken"]),
    "3-Punkt-Rechteck": ("Gedrehtes Rechteck: erst eine Kante, dann die Tiefe.",
                         ["Anfang und Ende der ersten Kante anklicken", "Tiefe mit dem dritten Klick"]),
    "Kreis": ("Kreis über Mittelpunkt und Durchmesser.", ["Mittelpunkt anklicken", "Rand anklicken oder Durchmesser "
                                                          "eintippen"]),
    "Kreis 3 Punkte": ("Kreis durch drei Punkte.", ["Drei Punkte auf dem Umfang anklicken"]),
    "Kreis 2 Punkte": ("Kreis über seinen Durchmesser.", ["Beide Enden des Durchmessers anklicken"]),
    "Kreis 2 Tangenten": ("Kreis, der zwei Linien berührt.", ["Zwei Linien anklicken", "Radius einstellen"]),
    "Kreis 3 Tangenten": ("Kreis, der drei Linien berührt.", ["Drei Linien anklicken"]),
    "Bogen": ("Kreisbogen um einen Mittelpunkt.", ["Mittelpunkt anklicken", "Anfang anklicken", "Ende anklicken"]),
    "3-Punkt-Bogen": ("Bogen durch Anfang, Ende und einen Punkt dazwischen.",
                      ["Anfang und Ende anklicken", "Dritter Klick legt die Wölbung fest"]),
    "Tangentialbogen": ("Bogen, der glatt an eine Linie anschließt.",
                        ["Am Ende einer Linie oder eines Bogens beginnen", "Endpunkt anklicken"]),
    "Polygon": ("Regelmäßiges Vieleck.", ["Mitte anklicken", "Eckenzahl einstellen", "Ecke anklicken"]),
    "Langloch": ("Langloch mit runden Enden.", ["Beide Mittelpunkte anklicken", "Breite anklicken oder eintippen"]),
    "Spline": ("Weiche Kurve durch die angeklickten Punkte.",
               ["Punkte nacheinander anklicken", "Doppelklick oder Eingabe beendet",
                "Griffe an den Punkten verändern die Richtung"]),
    "Steuerpunkt-Spline": ("Weiche Kurve, die von Steuerpunkten angezogen wird.",
                           ["Steuerpunkte anklicken", "Doppelklick oder Eingabe beendet"]),
    "Ellipse": ("Ellipse über Mitte und zwei Halbachsen.", ["Mitte anklicken", "Erste Achse anklicken",
                                                            "Zweite Achse anklicken"]),
    "Kegelschnitt": ("Kurve zwischen Ellipse, Parabel und Hyperbel (Wert Rho).",
                     ["Anfang und Ende anklicken", "Spitze anklicken", "Rho in der Leiste einstellen"]),
    "Text": ("Schrift in der Skizze – z. B. zum Prägen.", ["Stelle anklicken", "Text, Schrift und Höhe eintragen"]),
    "Abstand": ("Bemaßt den Abstand zweier Punkte oder Linien.", [_PICK2, "Wert eintippen"]),
    "Winkel": ("Bemaßt den Winkel zwischen zwei Linien.", [_PICK2, "Wert eintippen"]),
    "Bemaßung": ("Ein Werkzeug für alle Maße: Länge, Abstand, Winkel, Radius.",
                 ["Element(e) anklicken", "Maß an seinen Platz ziehen und klicken", "Wert eintippen (auch Ausdrücke)"]),
    "Abrunden": ("Rundet die Ecke zwischen zwei Linien ab.", ["Ecke oder beide Linien anklicken", "Radius eintippen"]),
    "Fase": ("Schneidet die Ecke zwischen zwei Linien schräg ab.", ["Ecke anklicken", "Abstand eintippen"]),
    "Stutzen": ("Entfernt ein Stück einer Kurve bis zum nächsten Schnittpunkt.", ["Auf das überflüssige Stück "
                                                                                 "klicken"]),
    "Verlängern": ("Verlängert eine Kurve bis zur nächsten.", ["Nahe dem Ende anklicken, das wachsen soll"]),
    "Brechen": ("Teilt eine Kurve an ihren Schnittpunkten.", ["Kurve anklicken"]),
    "Konstruktion": ("Macht Elemente zu Hilfslinien (gestrichelt, kein Profil).", ["Elemente anklicken – erneut "
                                                                                    "schaltet zurück"]),
    "Spiegeln": ("Spiegelt Skizzenelemente an einer Linie.", ["Elemente wählen", "Spiegellinie anklicken"]),
    "Versatz": ("Zieht eine Parallele im Abstand – auch um ganze Konturen.", ["Kurve anklicken", "Abstand ziehen "
                                                                               "oder eintippen"]),
    "Projizieren": ("Holt Kanten eines Körpers in die Skizze.", ["Kanten oder Flächen des Körpers anklicken"]),
    "Schnittkurve": ("Holt die Kurven in die Skizze, an denen die Skizzenebene einen Körper schneidet.",
                     ["Körper anklicken, der durch die Skizzenebene geht"]),
    "Muster": ("Wiederholt Skizzenelemente im Raster.", ["Elemente wählen", "Anzahl und Abstand einstellen"]),
    "Kreismuster": ("Wiederholt Skizzenelemente um einen Mittelpunkt.", ["Elemente wählen", "Mittelpunkt anklicken",
                                                                         "Anzahl einstellen"]),
    "Skizze skalieren": ("Vergrößert oder verkleinert Skizzenelemente.", ["Elemente wählen", "Bezugspunkt anklicken",
                                                                          "Faktor eintragen"]),
    "Übergangskurve": ("Glatte Verbindungskurve zwischen zwei Enden.", ["Beide Kurvenenden anklicken"]),
    "Verschieben/Kopieren": ("Verschiebt oder dreht Skizzenelemente – auf Wunsch als Kopie.",
                             ["Elemente wählen", "An Pfeil und Ring ziehen oder Werte eintragen"]),
    "Horizontal": ("Richtet eine Linie (oder zwei Punkte) waagerecht aus.", ["Linie oder zwei Punkte anklicken"]),
    "Vertikal": ("Richtet eine Linie (oder zwei Punkte) senkrecht aus.", ["Linie oder zwei Punkte anklicken"]),
    "Parallel": ("Zwei Linien laufen parallel.", [_PICK2]),
    "Rechtwinklig": ("Zwei Linien stehen im rechten Winkel.", [_PICK2]),
    "Gleich": ("Gleiche Länge bzw. gleicher Radius.", [_PICK2]),
    "Tangential": ("Linie und Bogen (oder zwei Bögen) gehen glatt ineinander über.", [_PICK2]),
    "Punkt auf Kurve": ("Ein Punkt liegt auf einer Linie oder einem Bogen.", ["Punkt anklicken, dann die Kurve"]),
    "Deckungsgleich": ("Zwei Punkte fallen zusammen.", [_PICK2]),
    "Mittelpunkt": ("Ein Punkt liegt in der Mitte einer Linie.", ["Punkt anklicken, dann die Linie"]),
    "Kollinear": ("Zwei Linien liegen auf einer Geraden.", [_PICK2]),
    "Konzentrisch": ("Kreise oder Bögen teilen den Mittelpunkt.", [_PICK2]),
    "Symmetrisch": ("Zwei Elemente liegen symmetrisch zu einer Linie.", ["Beide Elemente anklicken",
                                                                          "Dann die Symmetrielinie"]),
    "Krümmung": ("Zwei Kurven gehen krümmungsstetig (G2) ineinander über.", ["Spline und Anschlusskurve anklicken"]),
    "Fixieren": ("Hält Punkte oder Linien an ihrer Stelle fest.", ["Element anklicken – erneut löst es"]),
    "Löschen": ("Entfernt die gewählten Skizzenelemente.", ["Elemente wählen", "Klicken oder Entf"]),
    "SKIZZE FERTIG STELLEN": ("Beendet die Skizze; ihre geschlossenen Flächen stehen als Profile bereit.",
                              ["Klicken oder Eingabe", "Danach z. B. ein Profil anklicken und extrudieren"]),
}
# Bei den Grundformen kommt der allgemeine Hinweis zum Eintippen der Maße dazu
for _tool in ("Rechteck", "Mittelpunkt-Rechteck", "Polygon", "Langloch"):
    SKETCH_HINTS[_tool][1].append(_DRAW)


def enabled() -> bool:
    """Gewählt in den Einstellungen (Vorgabe: an)."""
    from . import settings
    return settings.tool_hints()


def mark(action: QAction, kind: str) -> QAction:
    """Merkt an der Aktion, zu welcher Textsammlung sie gehört (Modell oder Skizze)."""
    action.setProperty(_KIND, kind)
    return action


# Menüeinträge ohne eigenes Video, die das Video ihres Werkzeugs zeigen (Runde 82): die Arten der Analyse
CLIP_ALIASES = {"Entformung": "Analyse", "Zebra": "Analyse", "Wandstärke": "Analyse", "Krümmung": "Analyse",
                "Isokurven": "Analyse", "Analyse aus": "Analyse"}


def hint_for(name: str, kind: str, tooltip: str = "") -> tuple[str, list[str]] | None:
    """(Was es tut, Schritte) für ein Werkzeug; ohne eigenen Text dient der Tooltip als Beschreibung."""
    texts = SKETCH_HINTS if kind == SKETCH else MODEL_HINTS
    what, steps = texts.get(name, ("", []))
    if not what and tooltip:
        # Tooltips der Leiste lauten „Name – Beschreibung“: nur die Beschreibung übernehmen
        what = tooltip.split(" – ", 1)[1] if tooltip.startswith(name + " – ") else tooltip
        if what == name:
            what = ""
    if not what and not steps:
        return None
    return what, list(steps)


def _mix(first: QColor, second: QColor, share: float) -> QColor:
    return QColor(round(first.red() + (second.red() - first.red()) * share),
                  round(first.green() + (second.green() - first.green()) * share),
                  round(first.blue() + (second.blue() - first.blue()) * share))


class HintCard(QWidget):
    """Schwebende Erklärkarte. Ein-/Ausblenden über einen Grafikeffekt plus kurzes Hineingleiten."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # Hülle (wird ein-/ausgeblendet und gleitet) -> Karte (mit Schatten)
        self._shell = QWidget(self)
        self._fade = QGraphicsOpacityEffect(self._shell)
        self._fade.setOpacity(0.0)
        self._shell.setGraphicsEffect(self._fade)
        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        # Schatten als halbdurchsichtige Fläche hinter der Karte: ein zweiter Grafikeffekt (Schlagschatten) in der
        # ausgeblendeten Hülle führt bei Qt zu „Painter not active“ – verschachtelte Effekte vertragen sich nicht
        self._shadows = [QFrame(self._shell) for _ in range(3)]
        for depth, shadow in enumerate(self._shadows):
            shadow.setStyleSheet(f"background: rgba(0, 0, 0, {34 - depth * 10}); border-radius: {11 + depth * 2}px;")
        self.card = QFrame()
        self.card.setObjectName("toolHintCard")
        shell_layout.addWidget(self.card)
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(12, 10, 12, 11)
        layout.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(9)
        self.symbol = QLabel()
        self.symbol.setFixedSize(30, 30)
        head.addWidget(self.symbol)
        self.title = QLabel()
        self.title.setObjectName("toolHintTitle")
        head.addWidget(self.title, 1)
        self.shortcut = QLabel()
        self.shortcut.setObjectName("toolHintKey")
        head.addWidget(self.shortcut, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)
        # Video (erst nach dem Aufklappen sichtbar); die Höhe wächst beim Aufklappen von 0 auf VIDEO_SIZE
        self.video = QLabel()
        self.video.setFixedSize(VIDEO_SIZE[0], 0)
        self.video.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.video.hide()
        layout.addWidget(self.video)
        self._movie: QMovie | None = None
        self._clip = None
        self.expanded = False
        self.what = QLabel()
        self.what.setWordWrap(True)
        layout.addWidget(self.what)
        self.how = QLabel()
        self.how.setWordWrap(True)
        self.how.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.how)
        self.more = QLabel("▶  Maus kurz halten für das Video")
        self.more.setObjectName("toolHintMore")
        layout.addWidget(self.more)
        self.card.setFixedWidth(CARD_WIDTH)
        self._unfold = QVariantAnimation(self)
        self._unfold.setDuration(UNFOLD)
        self._unfold.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._unfold.valueChanged.connect(self._unfold_step)
        self._progress = 0.0
        self._target = 1.0
        self._animation = QVariantAnimation(self)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self._finished)
        self._style()

    def _style(self) -> None:
        """Farben aus der aktuellen Palette – passt so zu jedem Design (System/Noctalia oder Klassisch)."""
        palette = QApplication.palette()
        window, text = palette.color(QPalette.ColorRole.Window), palette.color(QPalette.ColorRole.WindowText)
        accent = palette.color(QPalette.ColorRole.Highlight)
        background = _mix(window, text, 0.05)
        self._accent = accent.name()
        self._muted = _mix(text, window, 0.35).name()
        self.card.setStyleSheet(
            f"QFrame#toolHintCard {{ background: {background.name()}; border: 1px solid "
            f"{_mix(window, accent, 0.45).name()}; border-radius: 10px; }}"
            f"QLabel {{ color: {text.name()}; background: transparent; border: none; }}"
            f"QLabel#toolHintTitle {{ font-weight: bold; font-size: 13px; }}"
            f"QLabel#toolHintKey {{ color: {self._muted}; border: 1px solid {self._muted}; border-radius: 4px; "
            f"padding: 0 5px; font-size: 10px; }}"
            f"QLabel#toolHintMore {{ color: {self._muted}; font-size: 10px; }}")

    def fill(self, title: str, icon, shortcut: str, what: str, steps: list[str], clip=None) -> None:
        """Inhalt setzen; `clip` = Pfad des Videos (oder None). Ist die Karte schon aufgeklappt, läuft es sofort."""
        self._style()
        self._clip = clip
        self._stop_movie()
        if clip is not None and self.expanded:
            self._start_movie()
            self.video.setFixedHeight(VIDEO_SIZE[1])
            self.video.show()
        else:
            self.video.hide()
        self.more.setVisible(clip is not None and not self.expanded)
        self.symbol.setPixmap(icon.pixmap(30, 30) if icon is not None and not icon.isNull() else QPixmap())
        self.symbol.setVisible(icon is not None and not icon.isNull())
        self.title.setText(title)
        self.shortcut.setText(shortcut)
        self.shortcut.setVisible(bool(shortcut))
        self.what.setText(what)
        self.what.setVisible(bool(what))
        if steps:
            items = "".join(f"<tr><td style='color:{self._accent}; padding-right:6px'><b>{number}</b></td>"
                            f"<td>{html.escape(step)}</td></tr>" for number, step in enumerate(steps, 1))
            self.how.setText(f"<div style='color:{self._accent}; font-size:10px; font-weight:bold; "
                             f"letter-spacing:1px'>SO GEHT'S</div><table cellspacing='0' cellpadding='1'>{items}"
                             "</table>")
        self.how.setVisible(bool(steps))
        self._relayout()

    def _relayout(self) -> None:
        """Größe von Karte, Schatten und Fenster nach dem Inhalt richten."""
        self.card.adjustSize()
        self._shell.adjustSize()
        self._shell.layout().activate()
        for depth, shadow in enumerate(self._shadows):     # weicher Rand: drei immer größere, blassere Flächen
            grow = 1 + depth * 2
            shadow.setGeometry(self.card.geometry().adjusted(-grow, -grow + 2, grow, grow + 3))
            shadow.lower()
        self.resize(self._shell.size().width(), self._shell.size().height() + SLIDE)

    # --- Video ----------------------------------------------------------------------------------------------
    def expand(self) -> None:
        """Video aufklappen (weich von Höhe 0 auf VIDEO_SIZE), wenn es eines gibt."""
        if self._clip is None or self.expanded or not self.isVisible():
            return
        self.expanded = True
        self.more.hide()
        self._start_movie()
        self.video.setFixedHeight(0)
        self.video.show()
        self._unfold.stop()
        self._unfold.setStartValue(0)
        self._unfold.setEndValue(VIDEO_SIZE[1])
        self._unfold.start()

    def _unfold_step(self, value) -> None:
        self.video.setFixedHeight(int(value))
        self._relayout()
        # wächst die Karte über den Bildschirmrand, rückt sie nach oben
        screen = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        overflow = self.geometry().bottom() - screen.bottom()
        if overflow > 0:
            self.move(self.x(), max(screen.top(), self.y() - overflow))

    def _start_movie(self) -> None:
        self._movie = QMovie(str(self._clip), parent=self)
        self._movie.setCacheMode(QMovie.CacheMode.CacheAll)
        self._movie.frameChanged.connect(self._show_frame)
        self._movie.start()

    def _stop_movie(self) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None

    def _show_frame(self, _number: int) -> None:
        """Bild des Videos in der Karte: doppelt so groß aufgenommen – mit Pixeldichte 2 bleibt es scharf."""
        if self._movie is None:
            return
        frame = self._movie.currentPixmap()
        if frame.isNull():
            return
        frame.setDevicePixelRatio(frame.width() / VIDEO_SIZE[0])
        self.video.setPixmap(frame)

    # --- Animation ------------------------------------------------------------------------------------------
    def appear(self, position: QPoint, from_above: bool = True) -> None:
        """Karte an `position` (linke obere Ecke der Karte selbst, ohne Schattenrand) einblenden."""
        self._from_above = from_above
        self.move(position - QPoint(MARGIN, MARGIN))
        if not self.isVisible():
            self._progress = 0.0
            self._step(0.0)
            self.show()
        self._run(1.0, FADE_IN, QEasingCurve.Type.OutCubic)

    def vanish(self) -> None:
        if self.isVisible() and self._target != 0.0:
            self._run(0.0, FADE_OUT, QEasingCurve.Type.InCubic)

    def _run(self, target: float, duration: int, curve) -> None:
        self._target = target
        self._animation.stop()
        self._animation.setStartValue(self._progress)
        self._animation.setEndValue(target)
        self._animation.setDuration(max(1, int(duration * abs(target - self._progress))))
        self._animation.setEasingCurve(curve)
        self._animation.start()

    def _step(self, value) -> None:
        """Ein Bild der Animation: Deckkraft und Lage der Hülle (gleitet von oben bzw. unten hinein)."""
        self._progress = float(value)
        self._fade.setOpacity(self._progress)
        offset = round((1.0 - self._progress) * SLIDE)
        self._shell.move(0, SLIDE - offset if getattr(self, "_from_above", True) else offset)

    def _finished(self) -> None:
        if self._target == 0.0:
            self.hide()
            self._stop_movie()          # aufgeklappt bleibt sie bis `collapse` (Wechsel zum Nachbarwerkzeug)

    def collapse(self) -> None:
        """Zuklappen: die nächste Karte beginnt wieder ohne Video."""
        self._stop_movie()
        self._unfold.stop()
        self.expanded = False
        self.video.hide()

    @property
    def shown(self) -> bool:
        return self.isVisible() and self._target == 1.0


class ToolHints(QObject):
    """Beobachtet Werkzeugknöpfe und ▾-Listen und zeigt nach kurzer Ruhe die Erklärkarte."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.card: HintCard | None = None
        self._pending: tuple | None = None          # (Quelle, Aktion/Knopf) der wartenden Karte
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.timeout.connect(self._show_pending)
        self._recent = QTimer(self)                 # kurz nach dem Ausblenden: neue Karte ohne Wartezeit
        self._recent.setSingleShot(True)
        self._recent.setInterval(SWITCH_GRACE)
        # erst wenn die Wechsel-Frist ohne neues Werkzeug verstrichen ist, klappt die Karte zu – sonst hinge es von der
        # Rechenlast ab, ob beim schnellen Wechsel zum Nachbarn das Video offen bleibt
        self._recent.timeout.connect(self._collapse_if_idle)
        self._detail = QTimer(self)                 # weitere Ruhe auf demselben Werkzeug: Video aufklappen
        self._detail.setSingleShot(True)
        self._detail.setInterval(DETAIL_DELAY)
        self._detail.timeout.connect(lambda: self.card is not None and self.card.expand())

    # --- Anmelden ---------------------------------------------------------------------------------------------
    def watch_button(self, button: QToolButton) -> None:
        button.installEventFilter(self)

    def watch_menu(self, menu: QMenu) -> None:
        menu.hovered.connect(lambda action, source=menu: self._menu_hovered(source, action))
        menu.aboutToHide.connect(self.hide)

    # --- Inhalt ermitteln -------------------------------------------------------------------------------------
    @staticmethod
    def content(widget_or_action) -> tuple | None:
        """(Titel, Symbol, Kürzel, Was, Schritte) für einen Knopf oder eine Aktion; None ohne Erklärung."""
        action = widget_or_action if isinstance(widget_or_action, QAction) else widget_or_action.defaultAction()
        if action is not None:
            name, kind = action.text().replace("&", ""), action.property(_KIND) or MODEL
            icon, tip = action.icon(), action.toolTip()
            shortcut = action.shortcut().toString(action.shortcut().SequenceFormat.NativeText)
        else:
            button = widget_or_action
            name, icon, tip, shortcut = button.text().replace("&", ""), button.icon(), button.toolTip(), ""
            kind = button.property(_KIND) or MODEL
        if not shortcut:
            # Werkzeugkürzel (E, F, L …) vergibt WolfCAD selbst über die Einstellungen, nicht an der Aktion
            from . import settings
            # (mit dem Namen gefragt, damit auch selbst vergebene Tasten anderer Werkzeuge gefunden werden)
            keys = settings.sketch_keys([name]) if kind == SKETCH else settings.model_keys([name])
            shortcut = keys.get(name, "")
        found = hint_for(name, kind, tip)
        if found is None:
            return None
        return (name, icon, shortcut, *found)

    @staticmethod
    def clip_of(widget_or_action):
        """Pfad des Videos zum Werkzeug (None, wenn es keines gibt)."""
        from .clip_recorder import clip_path
        action = widget_or_action if isinstance(widget_or_action, QAction) else widget_or_action.defaultAction()
        source = action if action is not None else widget_or_action
        # Menüeinträge, die einen Dialog öffnen, enden auf „ …“ (z. B. „Zebra …“)
        name = source.text().replace("&", "").removesuffix("…").strip()
        kind = source.property(_KIND) or MODEL
        found = clip_path(kind, name)
        if found is None and kind == MODEL and name in CLIP_ALIASES:
            found = clip_path(kind, CLIP_ALIASES[name])
        return found

    # --- Ereignisse ---------------------------------------------------------------------------------------------
    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.Enter:
            self._request(watched, watched)
        elif kind in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Hide):
            self.hide()
        elif kind == QEvent.Type.ToolTip and enabled() and self.content(watched) is not None:
            return True             # die Karte ersetzt den einfachen Tooltip
        return False

    def _menu_hovered(self, menu: QMenu, action: QAction) -> None:
        # Qt meldet das Überfahren eines Leistenknopfs über dessen Aktion auch an jede Liste, in der sie steht –
        # nur eine wirklich offene Liste ist gemeint
        if not menu.isVisible():
            return
        if action.menu() is not None or action.isSeparator():
            self.hide()
            return
        self._request(menu, action)

    def _request(self, anchor, source) -> None:
        if not enabled() or self.content(source) is None:
            self.hide()
            return
        self._pending = (anchor, source)
        # läuft schon eine Karte (oder ist eben erst verschwunden), wechselt sie gleich mit
        if (self.card is not None and self.card.shown) or self._recent.isActive():
            self._delay.start(30)
        else:
            self._delay.start(SHOW_DELAY)

    def _show_pending(self) -> None:
        if self._pending is None:
            return
        anchor, source = self._pending
        content = self.content(source)
        if content is None or not anchor.isVisible():
            return
        if self.card is None:
            self.card = HintCard()
        self.card.fill(*content, clip=self.clip_of(source))
        if not self.card.expanded:
            self._detail.start()
        size = self.card.card.sizeHint()
        position, from_above = self._place(anchor, source, size)
        self.card.appear(position, from_above)

    def _place(self, anchor: QWidget, source, size) -> tuple[QPoint, bool]:
        """Linke obere Ecke der Karte: unter dem Knopf bzw. rechts neben dem Menüeintrag, im Bildschirm gehalten."""
        if isinstance(anchor, QMenu):
            item = anchor.actionGeometry(source)
            top_left = anchor.mapToGlobal(QPoint(anchor.width() + 6, item.top()))
            room = QRect(top_left, size)
            screen = (anchor.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            if room.right() > screen.right():
                top_left.setX(anchor.mapToGlobal(QPoint(0, 0)).x() - size.width() - 6)
            from_above = True
        else:
            top_left = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
            screen = (anchor.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            from_above = True
            if top_left.y() + size.height() > screen.bottom():
                top_left.setY(anchor.mapToGlobal(QPoint(0, 0)).y() - size.height() - 4)
                from_above = False
        top_left.setX(max(screen.left() + 4, min(top_left.x(), screen.right() - size.width() - 4)))
        top_left.setY(max(screen.top() + 4, min(top_left.y(), screen.bottom() - size.height() - 4)))
        return top_left, from_above

    def _collapse_if_idle(self) -> None:
        if self.card is not None and not self.card.shown and self._pending is None:
            self.card.collapse()

    def hide(self) -> None:
        self._delay.stop()
        self._detail.stop()
        self._pending = None
        if self.card is not None and self.card.isVisible():
            self.card.vanish()
            self._recent.start()


_instance: ToolHints | None = None


def instance() -> ToolHints:
    """Gemeinsame Steuerung für alle Leisten des Programms."""
    global _instance
    if _instance is None:
        _instance = ToolHints(QApplication.instance())
    return _instance
